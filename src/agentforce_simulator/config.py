from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(slots=True)
class AppConfig:
    llm_provider: str = "heuristic"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4.1-mini"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    storage_backend: str = "postgres"
    postgres_dsn: str = "sqlite+aiosqlite:///./agentforce.db"
    dynamodb_table: str = "agentforce-simulator"
    aws_region: str = "us-east-1"
    context_window_chars: int = 6000
    support_experiment_rollout: float = 0.5
    api_auth_enabled: bool = False
    api_key: str | None = None
    api_admin_key: str | None = None
    api_readonly_key: str | None = None
    export_signing_key_id: str = "current"
    export_signing_secret: str | None = None
    export_signing_previous_keys: str | None = None
    export_signing_previous_key_expiry: str | None = None
    export_verify_max_age_seconds: int = 300
    export_verify_max_clock_skew_seconds: int = 30
    export_verify_rate_limit_count: int = 30
    export_verify_rate_limit_window_seconds: int = 60
    security_audit_retention_max_events: int = 5000

    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "heuristic").strip().lower(),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "llama3.1"),
            storage_backend=os.getenv("STORAGE_BACKEND", "postgres").strip().lower(),
            postgres_dsn=os.getenv("POSTGRES_DSN", "sqlite+aiosqlite:///./agentforce.db"),
            dynamodb_table=os.getenv("DYNAMODB_TABLE", "agentforce-simulator"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            context_window_chars=int(os.getenv("CONTEXT_WINDOW_CHARS", "6000")),
            support_experiment_rollout=float(os.getenv("SUPPORT_EXPERIMENT_ROLLOUT", "0.5")),
            api_auth_enabled=(os.getenv("API_AUTH_ENABLED", "false").strip().lower() == "true"),
            api_key=os.getenv("API_KEY") or None,
            api_admin_key=os.getenv("API_ADMIN_KEY") or None,
            api_readonly_key=os.getenv("API_READONLY_KEY") or None,
            export_signing_key_id=os.getenv("EXPORT_SIGNING_KEY_ID", "current"),
            export_signing_secret=os.getenv("EXPORT_SIGNING_SECRET") or None,
            export_signing_previous_keys=os.getenv("EXPORT_SIGNING_PREVIOUS_KEYS") or None,
            export_signing_previous_key_expiry=os.getenv("EXPORT_SIGNING_PREVIOUS_KEY_EXPIRY") or None,
            export_verify_max_age_seconds=int(os.getenv("EXPORT_VERIFY_MAX_AGE_SECONDS", "300")),
            export_verify_max_clock_skew_seconds=int(os.getenv("EXPORT_VERIFY_MAX_CLOCK_SKEW_SECONDS", "30")),
            export_verify_rate_limit_count=int(os.getenv("EXPORT_VERIFY_RATE_LIMIT_COUNT", "30")),
            export_verify_rate_limit_window_seconds=int(os.getenv("EXPORT_VERIFY_RATE_LIMIT_WINDOW_SECONDS", "60")),
            security_audit_retention_max_events=int(os.getenv("SECURITY_AUDIT_RETENTION_MAX_EVENTS", "5000")),
        )
