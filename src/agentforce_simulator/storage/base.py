from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agentforce_simulator.schemas import ConversationTurn, SecurityAuditEvent, TelemetryEvent


class StorageBackend(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def seed_demo_data(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_conversation_turns(self, conversation_id: str, limit: int = 30) -> list[ConversationTurn]:
        raise NotImplementedError

    @abstractmethod
    async def append_turn(self, conversation_id: str, turn: ConversationTurn) -> None:
        raise NotImplementedError

    @abstractmethod
    async def record_telemetry(self, event: TelemetryEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_telemetry_summary(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def record_security_audit_event(self, event: SecurityAuditEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    async def get_security_audit_events(
        self,
        limit: int = 100,
        event_type: str | None = None,
        outcome: str | None = None,
        key_id: str | None = None,
        request_id: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def run_sql(self, query: str) -> list[dict[str, Any]]:
        raise NotImplementedError
