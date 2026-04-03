from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, MetaData, String, Table, Column, and_, delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from agentforce_simulator.schemas import ConversationTurn, SecurityAuditEvent, TelemetryEvent
from agentforce_simulator.storage.base import StorageBackend

metadata = MetaData()

conversation_turns_table = Table(
    "conversation_turns",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", String(255), index=True, nullable=False),
    Column("role", String(32), nullable=False),
    Column("content", String, nullable=False),
    Column("agent_name", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
)

telemetry_events_table = Table(
    "telemetry_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", String(255), index=True, nullable=False),
    Column("agent_name", String(64), nullable=False),
    Column("agent_version", String(64), nullable=False),
    Column("latency_ms", Float, nullable=False),
    Column("success", Boolean, nullable=False),
    Column("fallback_used", Boolean, nullable=False),
    Column("experiment_bucket", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
)

security_audit_events_table = Table(
    "security_audit_events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_type", String(64), nullable=False),
    Column("outcome", String(32), nullable=False),
    Column("request_id", String(128), index=True, nullable=False),
    Column("key_id", String(128), nullable=True),
    Column("reason", String, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("metadata", JSON, nullable=False, default=dict),
)

support_cases_table = Table(
    "support_cases",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("category", String(64), nullable=False),
    Column("status", String(32), nullable=False),
    Column("priority", String(32), nullable=False),
    Column("satisfaction", Float, nullable=False),
    Column("region", String(32), nullable=False),
)


class SQLStorageBackend(StorageBackend):
    def __init__(self, dsn: str, security_audit_retention_max_events: int = 5000) -> None:
        self._engine: AsyncEngine = create_async_engine(dsn, future=True, pool_pre_ping=True)
        self._security_audit_retention_max_events = max(security_audit_retention_max_events, 0)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(metadata.create_all)

    async def seed_demo_data(self) -> None:
        async with self._engine.begin() as connection:
            existing = await connection.scalar(select(func.count()).select_from(support_cases_table))
            if existing:
                return
            sample_rows = [
                {"category": "billing", "status": "open", "priority": "high", "satisfaction": 3.2, "region": "NA"},
                {"category": "billing", "status": "closed", "priority": "medium", "satisfaction": 4.4, "region": "EMEA"},
                {"category": "shipping", "status": "open", "priority": "high", "satisfaction": 2.9, "region": "NA"},
                {"category": "shipping", "status": "pending", "priority": "medium", "satisfaction": 3.5, "region": "APAC"},
                {"category": "returns", "status": "closed", "priority": "low", "satisfaction": 4.7, "region": "NA"},
                {"category": "returns", "status": "open", "priority": "medium", "satisfaction": 3.8, "region": "EMEA"},
                {"category": "technical", "status": "open", "priority": "high", "satisfaction": 2.7, "region": "NA"},
                {"category": "technical", "status": "pending", "priority": "high", "satisfaction": 3.1, "region": "LATAM"},
                {"category": "technical", "status": "closed", "priority": "medium", "satisfaction": 4.1, "region": "EMEA"},
                {"category": "account", "status": "closed", "priority": "low", "satisfaction": 4.9, "region": "APAC"},
                {"category": "account", "status": "open", "priority": "medium", "satisfaction": 3.6, "region": "NA"},
                {"category": "billing", "status": "pending", "priority": "high", "satisfaction": 3.0, "region": "LATAM"},
            ]
            await connection.execute(insert(support_cases_table), sample_rows)

    async def get_conversation_turns(self, conversation_id: str, limit: int = 30) -> list[ConversationTurn]:
        async with self._engine.begin() as connection:
            result = await connection.execute(
                select(conversation_turns_table)
                .where(conversation_turns_table.c.conversation_id == conversation_id)
                .order_by(conversation_turns_table.c.created_at.desc())
                .limit(limit)
            )
            rows = list(reversed(result.mappings().all()))
        return [
            ConversationTurn(
                role=row["role"],
                content=row["content"],
                agent_name=row["agent_name"],
                created_at=row["created_at"],
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]

    async def append_turn(self, conversation_id: str, turn: ConversationTurn) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(conversation_turns_table).values(
                    conversation_id=conversation_id,
                    role=turn.role,
                    content=turn.content,
                    agent_name=turn.agent_name,
                    created_at=turn.created_at,
                    metadata=turn.metadata,
                )
            )

    async def record_telemetry(self, event: TelemetryEvent) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(telemetry_events_table).values(
                    conversation_id=event.conversation_id,
                    agent_name=event.agent_name,
                    agent_version=event.agent_version,
                    latency_ms=event.latency_ms,
                    success=event.success,
                    fallback_used=event.fallback_used,
                    experiment_bucket=event.experiment_bucket,
                    created_at=event.created_at,
                    metadata=event.metadata,
                )
            )

    async def get_telemetry_summary(self) -> dict[str, Any]:
        async with self._engine.begin() as connection:
            metrics_result = await connection.execute(
                text(
                    """
                    select
                        agent_name,
                        agent_version,
                        count(*) as total_runs,
                        avg(latency_ms) as avg_latency_ms,
                        avg(case when success then 1.0 else 0.0 end) as success_rate,
                        avg(case when fallback_used then 1.0 else 0.0 end) as fallback_rate
                    from telemetry_events
                    group by agent_name, agent_version
                    order by agent_name, agent_version
                    """
                )
            )
            experiment_result = await connection.execute(
                text(
                    """
                    select experiment_bucket, count(*) as total_runs
                    from telemetry_events
                    group by experiment_bucket
                    order by experiment_bucket
                    """
                )
            )
        return {
            "agent_metrics": [dict(row) for row in metrics_result.mappings().all()],
            "experiment_metrics": [dict(row) for row in experiment_result.mappings().all()],
        }

    async def record_security_audit_event(self, event: SecurityAuditEvent) -> None:
        async with self._engine.begin() as connection:
            await connection.execute(
                insert(security_audit_events_table).values(
                    event_type=event.event_type,
                    outcome=event.outcome,
                    request_id=event.request_id,
                    key_id=event.key_id,
                    reason=event.reason,
                    created_at=event.created_at,
                    metadata=event.metadata,
                )
            )
            if self._security_audit_retention_max_events > 0:
                prune_subquery = (
                    select(security_audit_events_table.c.id)
                    .order_by(security_audit_events_table.c.created_at.desc(), security_audit_events_table.c.id.desc())
                    .offset(self._security_audit_retention_max_events)
                )
                await connection.execute(delete(security_audit_events_table).where(security_audit_events_table.c.id.in_(prune_subquery)))

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
        filters = []
        if event_type:
            filters.append(security_audit_events_table.c.event_type == event_type)
        if outcome:
            filters.append(security_audit_events_table.c.outcome == outcome)
        if key_id:
            filters.append(security_audit_events_table.c.key_id == key_id)
        if request_id:
            filters.append(security_audit_events_table.c.request_id == request_id)
        if since:
            filters.append(security_audit_events_table.c.created_at >= since)
        if until:
            filters.append(security_audit_events_table.c.created_at <= until)

        query = select(security_audit_events_table)
        if filters:
            query = query.where(and_(*filters))
        query = query.order_by(security_audit_events_table.c.created_at.desc()).limit(limit)

        async with self._engine.begin() as connection:
            result = await connection.execute(query)
            rows = result.mappings().all()
        return [
            {
                "event_type": row["event_type"],
                "outcome": row["outcome"],
                "request_id": row["request_id"],
                "key_id": row["key_id"],
                "reason": row["reason"],
                "created_at": row["created_at"].isoformat(),
                "metadata": row["metadata"] or {},
            }
            for row in rows
        ]

    async def run_sql(self, query: str) -> list[dict[str, Any]]:
        normalized = query.strip().lower()
        if not normalized.startswith("select"):
            raise ValueError("Only SELECT queries are allowed.")
        async with self._engine.begin() as connection:
            result = await connection.execute(text(query))
            return [dict(row) for row in result.mappings().all()]
