from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]
AgentName = Literal["support", "analysis", "escalation"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ConversationTurn:
    role: Role
    content: str
    agent_name: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(slots=True)
class ToolInvocation:
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AgentContext:
    conversation_id: str
    conversation_turns: list[ConversationTurn]
    experiment_bucket: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentResult:
    agent_name: AgentName
    response: str
    confidence: float
    success: bool = True
    fallback_used: bool = False
    handoff_to: AgentName | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    visualization_data: list[dict[str, Any]] = field(default_factory=list)
    visualization_kind: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TraceStep:
    step: str
    detail: str
    agent_name: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(slots=True)
class WorkflowResponse:
    conversation_id: str
    active_agent: AgentName
    agent_version: str
    experiment_bucket: str
    response: str
    confidence: float
    visualization_data: list[dict[str, Any]] = field(default_factory=list)
    visualization_kind: str | None = None
    tool_invocations: list[ToolInvocation] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "active_agent": self.active_agent,
            "agent_version": self.agent_version,
            "experiment_bucket": self.experiment_bucket,
            "response": self.response,
            "confidence": self.confidence,
            "visualization_data": self.visualization_data,
            "visualization_kind": self.visualization_kind,
            "tool_invocations": [tool.to_dict() for tool in self.tool_invocations],
            "trace": [step.to_dict() for step in self.trace],
            "telemetry": self.telemetry,
        }


@dataclass(slots=True)
class TelemetryEvent:
    conversation_id: str
    agent_name: AgentName
    agent_version: str
    latency_ms: float
    success: bool
    fallback_used: bool
    experiment_bucket: str
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload


@dataclass(slots=True)
class SecurityAuditEvent:
    event_type: str
    outcome: str
    request_id: str
    key_id: str | None = None
    reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return payload
