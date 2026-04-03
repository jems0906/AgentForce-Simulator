from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Iterable

from agentforce_simulator.llm import LLMClient
from agentforce_simulator.schemas import AgentContext, AgentResult, ToolInvocation
from agentforce_simulator.tools import ToolCatalog

ESCALATION_KEYWORDS = {
    "human",
    "manager",
    "escalate",
    "urgent",
    "outage",
    "legal",
    "angry",
    "complaint",
    "frustrated",
}
ANALYSIS_KEYWORDS = {
    "sql",
    "query",
    "chart",
    "plot",
    "analyze",
    "analysis",
    "dashboard",
    "report",
    "trend",
    "volume",
}
FAQ_LIBRARY = {
    "refund": "Refunds can be requested within 30 days of purchase. Approved refunds typically settle within 5 to 7 business days.",
    "shipping": "Standard shipping is 3 to 5 business days. Expedited shipping is available at checkout for eligible regions.",
    "password": "Customers can reset passwords from the sign-in page using the Forgot Password flow.",
    "subscription": "Subscriptions can be upgraded, downgraded, or canceled from the billing portal at the end of the current billing cycle.",
}


@dataclass(slots=True)
class SupportAgent:
    llm: LLMClient
    tools: ToolCatalog
    version: str = "v1"
    name: str = "support"

    async def handle(self, message: str, context: AgentContext) -> AgentResult:
        lowered = message.lower()
        if self._needs_escalation(lowered):
            return AgentResult(
                agent_name="support",
                response="This looks high-risk or time-sensitive. I am routing this conversation to a human escalation queue now.",
                confidence=0.42,
                handoff_to="escalation",
                metadata={"reason": "escalation-keyword"},
            )
        if self._needs_analysis(lowered):
            return AgentResult(
                agent_name="support",
                response="This request is better handled by the data analysis agent. Handing off now.",
                confidence=0.51,
                handoff_to="analysis",
                metadata={"reason": "analysis-keyword"},
            )

        tool_invocations: list[ToolInvocation] = []
        fallback_used = False
        sections: list[str] = []

        faq_response = self._lookup_faq(lowered)
        if faq_response:
            sections.append(faq_response)

        city = self.tools.extract_city(message)
        if city:
            try:
                weather = await self.tools.weather.run(city)
                tool_invocations.append(ToolInvocation("weather", {"city": city}, weather))
                sections.append(
                    f"Current weather in {weather['city']}, {weather['country']}: {weather['temperature_c']} C with wind speed {weather['wind_speed_kph']} kph."
                )
            except Exception as exc:
                fallback_used = True
                sections.append(f"I could not fetch live weather data right now: {exc}")

        symbol = self.tools.extract_stock_symbol(message)
        if symbol:
            try:
                stock = await self.tools.stocks.run(symbol)
                tool_invocations.append(ToolInvocation("stocks", {"symbol": symbol}, stock))
                sections.append(
                    f"{stock['symbol']} is trading at {stock['regular_market_price']} {stock['currency']} versus previous close {stock['previous_close']} {stock['currency']}."
                )
            except Exception as exc:
                fallback_used = True
                sections.append(f"I could not fetch the live market quote right now: {exc}")

        case_id = self.tools.extract_case_id(message)
        if case_id is not None:
            try:
                case_payload = await self.tools.support_case.run(case_id)
                tool_invocations.append(ToolInvocation("support_case", {"case_id": case_id}, case_payload))
                case = case_payload["case"]
                sections.append(
                    f"Case {case['id']} is currently {case['status']} with {case['priority']} priority in the {case['category']} category for region {case['region']}. Satisfaction is {case['satisfaction']}."
                )
            except Exception as exc:
                fallback_used = True
                sections.append(f"I could not retrieve the requested case right now: {exc}")

        if self.tools.needs_operations_summary(message):
            try:
                summary_payload = await self.tools.operations_summary.run()
                tool_invocations.append(ToolInvocation("operations_summary", {}, summary_payload))
                summary = summary_payload["summary"]
                sections.append(
                    "Operations summary: "
                    f"{summary.get('total_cases', 0)} total cases, "
                    f"{summary.get('open_cases', 0)} open, "
                    f"{summary.get('high_priority_cases', 0)} high priority, "
                    f"average satisfaction {summary.get('avg_satisfaction', 0)}."
                )
            except Exception as exc:
                fallback_used = True
                sections.append(f"I could not generate the operations summary right now: {exc}")

        prompt = self._build_prompt(message, context.conversation_turns, sections)
        llm_response = await self.llm.generate(
            system_prompt=self._system_prompt(context.experiment_bucket),
            user_prompt=prompt,
            history=context.conversation_turns,
        )
        sections.append(llm_response)
        response = "\n\n".join(section for section in sections if section)
        confidence = 0.88 if sections and not fallback_used else 0.68
        return AgentResult(
            agent_name="support",
            response=response,
            confidence=confidence,
            fallback_used=fallback_used,
            tool_invocations=tool_invocations,
        )

    def _lookup_faq(self, message: str) -> str | None:
        for keyword, response in FAQ_LIBRARY.items():
            if keyword in message:
                return response
        return None

    def _build_prompt(self, message: str, turns: Iterable, evidence: list[str]) -> str:
        evidence_text = " ".join(evidence)
        if self.version == "v2":
            prior_turns = " | ".join(turn.content for turn in list(turns)[-3:])
            return (
                f"Customer message: {message}. Use the evidence to give a concise, empathetic answer with a clear next action. "
                f"Evidence: {evidence_text}. Recent context: {prior_turns}"
            )
        return f"Customer message: {message}. Use the known evidence to answer directly and accurately. Evidence: {evidence_text}"

    def _system_prompt(self, experiment_bucket: str) -> str:
        if self.version == "v2":
            return (
                "You are Support Agent v2. You prefer context-aware answers, stronger summaries, and explicit next steps. "
                f"Experiment bucket: {experiment_bucket}."
            )
        return (
            "You are Support Agent v1. You prefer short FAQ-style responses, then add tool-based details when available. "
            f"Experiment bucket: {experiment_bucket}."
        )

    @staticmethod
    def _needs_escalation(message: str) -> bool:
        return any(keyword in message for keyword in ESCALATION_KEYWORDS)

    @staticmethod
    def _needs_analysis(message: str) -> bool:
        return any(keyword in message for keyword in ANALYSIS_KEYWORDS)


@dataclass(slots=True)
class AnalysisAgent:
    llm: LLMClient
    tools: ToolCatalog
    version: str = "v1"
    name: str = "analysis"

    async def handle(self, message: str, context: AgentContext) -> AgentResult:
        query = self._build_query(message)
        try:
            result = await self.tools.database.run(query)
            rows = result["rows"]
            visualization_kind = "bar"
            summary_prompt = (
                f"Summarize this analytics result for a business user. Original request: {message}. SQL: {query}. Rows: {rows}"
            )
            summary = await self.llm.generate(
                system_prompt="You are a data analysis agent. Explain metrics, trends, and operational implications in plain language.",
                user_prompt=summary_prompt,
                history=context.conversation_turns,
            )
            return AgentResult(
                agent_name="analysis",
                response=summary,
                confidence=0.93,
                tool_invocations=[ToolInvocation("database", {"query": query}, result)],
                visualization_data=rows,
                visualization_kind=visualization_kind,
                metadata={"query": query},
            )
        except Exception as exc:
            return AgentResult(
                agent_name="analysis",
                response=(
                    f"The data analysis agent could not complete the SQL request: {exc}. "
                    "Try a simpler analytics question or switch to the SQL storage backend."
                ),
                confidence=0.38,
                success=False,
                fallback_used=True,
            )

    @staticmethod
    def _build_query(message: str) -> str:
        lowered = message.lower()
        if "status" in lowered or "volume" in lowered:
            return "select status, count(*) as case_count from support_cases group by status order by case_count desc"
        if "priority" in lowered:
            return "select priority, count(*) as case_count from support_cases group by priority order by case_count desc"
        if "region" in lowered:
            return "select region, count(*) as case_count from support_cases group by region order by case_count desc"
        if "open" in lowered and "priority" in lowered:
            return (
                "select priority, count(*) as open_case_count from support_cases "
                "where status = 'open' group by priority order by open_case_count desc"
            )
        if "satisfaction" in lowered or "category" in lowered:
            return (
                "select category, round(cast(avg(satisfaction) as numeric), 2) as avg_satisfaction, count(*) as case_count "
                "from support_cases group by category order by case_count desc"
            )
        return (
            "select category, status, priority, satisfaction, region "
            "from support_cases order by id desc limit 10"
        )


@dataclass(slots=True)
class EscalationAgent:
    llm: LLMClient
    tools: ToolCatalog
    version: str = "v1"
    name: str = "escalation"

    async def handle(self, message: str, context: AgentContext) -> AgentResult:
        queue = "human-priority" if any(token in message.lower() for token in {"outage", "legal", "urgent"}) else "human-standard"
        response = await self.llm.generate(
            system_prompt="You are an escalation agent. Confirm handoff, summarize urgency, and set expectations clearly.",
            user_prompt=(
                f"Prepare a human handoff note for this customer request: {message}. "
                f"Route it to {queue} and mention that a specialist will continue the conversation."
            ),
            history=context.conversation_turns,
        )
        return AgentResult(
            agent_name="escalation",
            response=response,
            confidence=0.97,
            metadata={"queue": queue},
        )
