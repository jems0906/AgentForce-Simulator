from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import aioboto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from agentforce_simulator.schemas import ConversationTurn, SecurityAuditEvent, TelemetryEvent
from agentforce_simulator.storage.base import StorageBackend


class DynamoDBStorageBackend(StorageBackend):
    def __init__(self, table_name: str, region_name: str, security_audit_retention_max_events: int = 5000) -> None:
        self._table_name = table_name
        self._region_name = region_name
        self._session = aioboto3.Session()
        self._security_audit_retention_max_events = max(security_audit_retention_max_events, 0)

    async def initialize(self) -> None:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            try:
                await dynamodb.Table(self._table_name).load()
                return
            except ClientError as exc:
                error_code = exc.response.get("Error", {}).get("Code")
                if error_code != "ResourceNotFoundException":
                    raise
            table = await dynamodb.create_table(
                TableName=self._table_name,
                KeySchema=[
                    {"AttributeName": "pk", "KeyType": "HASH"},
                    {"AttributeName": "sk", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "pk", "AttributeType": "S"},
                    {"AttributeName": "sk", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
            await table.wait_until_exists()

    async def seed_demo_data(self) -> None:
        return

    async def get_conversation_turns(self, conversation_id: str, limit: int = 30) -> list[ConversationTurn]:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            response = await table.query(
                KeyConditionExpression=Key("pk").eq(f"CONV#{conversation_id}") & Key("sk").begins_with("TURN#"),
                Limit=limit,
                ScanIndexForward=False,
            )
        items = list(reversed(response.get("Items", [])))
        return [
            ConversationTurn(
                role=item["role"],
                content=item["content"],
                agent_name=item.get("agent_name") or None,
                created_at=datetime.fromisoformat(item["created_at"]) if item.get("created_at") else datetime.now(timezone.utc),
                metadata=item.get("metadata", {}),
            )
            for item in items
        ]

    async def append_turn(self, conversation_id: str, turn: ConversationTurn) -> None:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            await table.put_item(
                Item={
                    "pk": f"CONV#{conversation_id}",
                    "sk": f"TURN#{turn.created_at.isoformat()}#{uuid4()}",
                    "role": turn.role,
                    "content": turn.content,
                    "agent_name": turn.agent_name or "",
                    "created_at": turn.created_at.isoformat(),
                    "metadata": turn.metadata,
                }
            )

    async def record_telemetry(self, event: TelemetryEvent) -> None:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            await table.put_item(
                Item={
                    "pk": "TELEMETRY",
                    "sk": f"EVENT#{event.created_at.isoformat()}#{uuid4()}",
                    "conversation_id": event.conversation_id,
                    "agent_name": event.agent_name,
                    "agent_version": event.agent_version,
                    "latency_ms": Decimal(str(round(event.latency_ms, 3))),
                    "success": event.success,
                    "fallback_used": event.fallback_used,
                    "experiment_bucket": event.experiment_bucket,
                    "metadata": event.metadata,
                }
            )

    async def get_telemetry_summary(self) -> dict[str, Any]:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            response = await table.scan(FilterExpression=Attr("pk").eq("TELEMETRY"))
        events = response.get("Items", [])
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        experiments: dict[str, int] = {}
        for event in events:
            key = (event["agent_name"], event["agent_version"])
            bucket = grouped.setdefault(
                key,
                {
                    "agent_name": event["agent_name"],
                    "agent_version": event["agent_version"],
                    "total_runs": 0,
                    "avg_latency_ms": 0.0,
                    "success_rate": 0.0,
                    "fallback_rate": 0.0,
                },
            )
            bucket["total_runs"] += 1
            bucket["avg_latency_ms"] += float(event["latency_ms"])
            bucket["success_rate"] += 1.0 if event["success"] else 0.0
            bucket["fallback_rate"] += 1.0 if event["fallback_used"] else 0.0
            experiments[event["experiment_bucket"]] = experiments.get(event["experiment_bucket"], 0) + 1
        for bucket in grouped.values():
            total = max(bucket["total_runs"], 1)
            bucket["avg_latency_ms"] /= total
            bucket["success_rate"] /= total
            bucket["fallback_rate"] /= total
        return {
            "agent_metrics": list(grouped.values()),
            "experiment_metrics": [
                {"experiment_bucket": bucket, "total_runs": count}
                for bucket, count in sorted(experiments.items())
            ],
        }

    async def record_security_audit_event(self, event: SecurityAuditEvent) -> None:
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            sk = f"EVENT#{event.created_at.isoformat()}#{uuid4()}"
            await table.put_item(
                Item={
                    "pk": "SECURITY_AUDIT",
                    "sk": sk,
                    "event_type": event.event_type,
                    "outcome": event.outcome,
                    "request_id": event.request_id,
                    "key_id": event.key_id or "",
                    "reason": event.reason or "",
                    "created_at": event.created_at.isoformat(),
                    "metadata": event.metadata,
                }
            )
            if self._security_audit_retention_max_events > 0:
                response = await table.scan(FilterExpression=Attr("pk").eq("SECURITY_AUDIT"))
                items = sorted(response.get("Items", []), key=lambda item: item.get("created_at", ""), reverse=True)
                stale = items[self._security_audit_retention_max_events :]
                for item in stale:
                    await table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})

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
        async with self._session.resource("dynamodb", region_name=self._region_name) as dynamodb:
            table = await dynamodb.Table(self._table_name)
            response = await table.scan(FilterExpression=Attr("pk").eq("SECURITY_AUDIT"))
        items = sorted(response.get("Items", []), key=lambda item: item.get("created_at", ""), reverse=True)

        def matches(item: dict[str, Any]) -> bool:
            if event_type and item.get("event_type") != event_type:
                return False
            if outcome and item.get("outcome") != outcome:
                return False
            if key_id and (item.get("key_id") or None) != key_id:
                return False
            if request_id and item.get("request_id") != request_id:
                return False
            created_at_raw = item.get("created_at")
            created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else None
            if since and created_at and created_at < since:
                return False
            if until and created_at and created_at > until:
                return False
            return True

        items = [item for item in items if matches(item)][:limit]
        return [
            {
                "event_type": item.get("event_type"),
                "outcome": item.get("outcome"),
                "request_id": item.get("request_id"),
                "key_id": item.get("key_id") or None,
                "reason": item.get("reason") or None,
                "created_at": item.get("created_at"),
                "metadata": item.get("metadata", {}),
            }
            for item in items
        ]

    async def run_sql(self, query: str) -> list[dict[str, Any]]:
        raise ValueError("SQL queries are not supported when using DynamoDB storage.")
