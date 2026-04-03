from __future__ import annotations

import asyncio
from hashlib import md5
from time import perf_counter
from typing import Any

from agentforce_simulator.agents.runtime import AnalysisAgent, EscalationAgent, SupportAgent
from agentforce_simulator.config import AppConfig
from agentforce_simulator.llm import build_llm_client
from agentforce_simulator.schemas import AgentContext, ConversationTurn, TelemetryEvent, TraceStep, WorkflowResponse
from agentforce_simulator.storage.base import StorageBackend
from agentforce_simulator.storage.factory import build_storage_backend
from agentforce_simulator.tools import ToolCatalog


class WorkflowEngine:
    def __init__(self, config: AppConfig, storage: StorageBackend | None = None) -> None:
        self.config = config
        self.storage = storage or build_storage_backend(config)
        self.llm = build_llm_client(config)
        self.tools = ToolCatalog(self.storage)
        self.support_v1 = SupportAgent(self.llm, self.tools, version="v1")
        self.support_v2 = SupportAgent(self.llm, self.tools, version="v2")
        self.analysis = AnalysisAgent(self.llm, self.tools)
        self.escalation = EscalationAgent(self.llm, self.tools)
        self._started = False

    async def startup(self) -> None:
        if self._started:
            return
        await self.storage.initialize()
        await self.storage.seed_demo_data()
        self._started = True

    async def process_user_message(self, conversation_id: str, message: str) -> WorkflowResponse:
        await self.startup()
        started_at = perf_counter()
        trace: list[TraceStep] = []
        experiment_bucket = self._assign_bucket(conversation_id)
        trace.append(
            TraceStep(
                step="bucket-assigned",
                detail=f"Conversation assigned to {experiment_bucket}.",
                metadata={"experiment_bucket": experiment_bucket},
            )
        )
        history = await self.storage.get_conversation_turns(conversation_id)
        user_turn = ConversationTurn(role="user", content=message)
        context_turns = self._trim_context([*history, user_turn])
        trace.append(
            TraceStep(
                step="context-trimmed",
                detail=f"Loaded {len(history)} persisted turns and kept {len(context_turns)} turns in the active context window.",
                metadata={"persisted_turns": len(history), "context_turns": len(context_turns)},
            )
        )
        context = AgentContext(
            conversation_id=conversation_id,
            conversation_turns=context_turns,
            experiment_bucket=experiment_bucket,
        )

        primary_agent = self._select_primary_agent(message, experiment_bucket)
        trace.append(
            TraceStep(
                step="primary-agent-selected",
                detail=f"Selected {primary_agent.name} as the primary agent.",
                agent_name=primary_agent.name,
            )
        )
        result = await primary_agent.handle(message, context)
        active_version = primary_agent.version
        trace.append(
            TraceStep(
                step="primary-agent-completed",
                detail=(
                    f"{primary_agent.name} finished with confidence {result.confidence:.2f}"
                    f" and {len(result.tool_invocations)} tool calls."
                ),
                agent_name=primary_agent.name,
                metadata={
                    "confidence": result.confidence,
                    "fallback_used": result.fallback_used,
                    "tool_names": [tool.tool_name for tool in result.tool_invocations],
                },
            )
        )

        if result.handoff_to:
            handoff_agent = self._agent_by_name(result.handoff_to, experiment_bucket)
            trace.append(
                TraceStep(
                    step="handoff-issued",
                    detail=f"Primary agent requested handoff to {result.handoff_to}.",
                    agent_name=primary_agent.name,
                    metadata={"handoff_to": result.handoff_to},
                )
            )
            result = await handoff_agent.handle(message, context)
            active_version = handoff_agent.version
            trace.append(
                TraceStep(
                    step="handoff-completed",
                    detail=(
                        f"{handoff_agent.name} handled the conversation with confidence {result.confidence:.2f}"
                        f" and {len(result.tool_invocations)} tool calls."
                    ),
                    agent_name=handoff_agent.name,
                    metadata={
                        "confidence": result.confidence,
                        "fallback_used": result.fallback_used,
                        "tool_names": [tool.tool_name for tool in result.tool_invocations],
                    },
                )
            )

        latency_ms = (perf_counter() - started_at) * 1000
        trace.append(
            TraceStep(
                step="persistence-scheduled",
                detail="Persisting conversation turns and telemetry.",
                agent_name=result.agent_name,
                metadata={"latency_ms": round(latency_ms, 2)},
            )
        )
        assistant_turn = ConversationTurn(
            role="assistant",
            content=result.response,
            agent_name=result.agent_name,
            metadata={
                "confidence": result.confidence,
                "tool_invocations": [tool.tool_name for tool in result.tool_invocations],
                "visualization_kind": result.visualization_kind,
                "trace": [step.to_dict() for step in trace],
                **result.metadata,
            },
        )
        telemetry = TelemetryEvent(
            conversation_id=conversation_id,
            agent_name=result.agent_name,
            agent_version=active_version,
            latency_ms=latency_ms,
            success=result.success,
            fallback_used=result.fallback_used,
            experiment_bucket=experiment_bucket,
            metadata={"tool_count": len(result.tool_invocations), **result.metadata},
        )
        await asyncio.gather(
            self.storage.append_turn(conversation_id, user_turn),
            self.storage.append_turn(conversation_id, assistant_turn),
            self.storage.record_telemetry(telemetry),
        )
        return WorkflowResponse(
            conversation_id=conversation_id,
            active_agent=result.agent_name,
            agent_version=active_version,
            experiment_bucket=experiment_bucket,
            response=result.response,
            confidence=result.confidence,
            visualization_data=result.visualization_data,
            visualization_kind=result.visualization_kind,
            tool_invocations=result.tool_invocations,
            trace=trace,
            telemetry={
                "latency_ms": round(latency_ms, 2),
                "success": result.success,
                "fallback_used": result.fallback_used,
            },
        )

    async def get_conversation_history(self, conversation_id: str) -> list[ConversationTurn]:
        await self.startup()
        return await self.storage.get_conversation_turns(conversation_id, limit=100)

    async def get_dashboard_snapshot(self) -> dict[str, Any]:
        await self.startup()
        return await self.storage.get_telemetry_summary()

    def _select_primary_agent(self, message: str, experiment_bucket: str):
        lowered = message.lower()
        if any(token in lowered for token in {"human", "manager", "escalate", "urgent", "outage", "legal"}):
            return self.escalation
        if any(token in lowered for token in {"sql", "query", "chart", "plot", "analysis", "analyze", "dashboard", "report", "trend", "volume"}):
            return self.analysis
        return self.support_v2 if experiment_bucket == "support-v2" else self.support_v1

    def _agent_by_name(self, name: str, experiment_bucket: str):
        if name == "analysis":
            return self.analysis
        if name == "escalation":
            return self.escalation
        return self.support_v2 if experiment_bucket == "support-v2" else self.support_v1

    def _assign_bucket(self, conversation_id: str) -> str:
        digest = md5(conversation_id.encode("utf-8"), usedforsecurity=False).hexdigest()
        ratio = int(digest[:8], 16) / 0xFFFFFFFF
        return "support-v2" if ratio < self.config.support_experiment_rollout else "support-v1"

    def _trim_context(self, turns: list[ConversationTurn]) -> list[ConversationTurn]:
        total = 0
        trimmed: list[ConversationTurn] = []
        for turn in reversed(turns):
            turn_size = len(turn.content)
            if trimmed and total + turn_size > self.config.context_window_chars:
                break
            trimmed.append(turn)
            total += turn_size
        return list(reversed(trimmed))
