from __future__ import annotations

from agentforce_simulator.config import AppConfig
from agentforce_simulator.storage.base import StorageBackend
from agentforce_simulator.storage.dynamodb import DynamoDBStorageBackend
from agentforce_simulator.storage.sql import SQLStorageBackend


def build_storage_backend(config: AppConfig) -> StorageBackend:
    if config.storage_backend == "dynamodb":
        return DynamoDBStorageBackend(
            config.dynamodb_table,
            config.aws_region,
            security_audit_retention_max_events=config.security_audit_retention_max_events,
        )
    return SQLStorageBackend(
        config.postgres_dsn,
        security_audit_retention_max_events=config.security_audit_retention_max_events,
    )
