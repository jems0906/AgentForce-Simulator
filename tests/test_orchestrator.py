from __future__ import annotations

from fastapi.testclient import TestClient
import hashlib
import hmac
import json
import pytest

from agentforce_simulator.config import AppConfig
from agentforce_simulator.api import create_app
from agentforce_simulator.orchestrator import WorkflowEngine


async def test_analysis_agent_handles_analytics_request(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    await engine.startup()

    result = await engine.process_user_message("conv-analysis", "Show me case volume by status")

    assert result.active_agent == "analysis"
    assert result.visualization_data
    assert result.visualization_kind == "bar"
    assert any(step.step == "primary-agent-selected" for step in result.trace)


async def test_escalation_agent_handles_urgent_request(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    await engine.startup()

    result = await engine.process_user_message("conv-escalation", "I need a human right now, this outage is urgent")

    assert result.active_agent == "escalation"
    assert result.confidence > 0.9


async def test_support_agent_uses_tool_flow(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    await engine.startup()

    result = await engine.process_user_message("conv-support", "What is your refund policy?")

    assert result.active_agent == "support"
    assert "Refunds can be requested" in result.response


async def test_support_agent_can_lookup_case(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    await engine.startup()

    result = await engine.process_user_message("conv-case", "Show me case 1")

    assert result.active_agent == "support"
    assert "Case 1 is currently" in result.response
    assert any(tool.tool_name == "support_case" for tool in result.tool_invocations)


async def test_support_agent_can_generate_operations_summary(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    await engine.startup()

    result = await engine.process_user_message("conv-summary", "Give me an operations summary")

    assert result.active_agent == "support"
    assert "Operations summary:" in result.response
    assert any(tool.tool_name == "operations_summary" for tool in result.tool_invocations)


async def test_api_message_endpoint_returns_trace(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    response = client.post("/api/conversations/api-conv/messages", json={"message": "Show me case volume by status"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["active_agent"] == "analysis"
    assert payload["trace"]


async def test_api_export_endpoints(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    create_response = client.post(
        "/api/conversations/export-conv/messages",
        json={"message": "What is your refund policy?"},
    )
    assert create_response.status_code == 200

    conversation_export = client.get("/api/conversations/export-conv/export")
    telemetry_export = client.get("/api/telemetry/export")

    assert conversation_export.status_code == 200
    assert telemetry_export.status_code == 200
    assert conversation_export.json()["data"]["turn_count"] >= 2
    assert "generated_at" in telemetry_export.json()
    assert "data" in telemetry_export.json()


async def test_api_key_auth_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.setenv("API_ADMIN_KEY", "secret-admin-key")
    monkeypatch.setenv("API_READONLY_KEY", "secret-read-key")
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        api_auth_enabled=True,
        api_admin_key="secret-admin-key",
        api_readonly_key="secret-read-key",
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    unauthorized = client.post(
        "/api/conversations/secure/messages",
        json={"message": "hello"},
    )
    forbidden_readonly = client.post(
        "/api/conversations/secure/messages",
        headers={"x-api-key": "secret-read-key"},
        json={"message": "hello"},
    )
    authorized = client.post(
        "/api/conversations/secure/messages",
        headers={"x-api-key": "secret-admin-key"},
        json={"message": "hello"},
    )
    readonly_telemetry = client.get(
        "/api/telemetry",
        headers={"x-api-key": "secret-read-key"},
    )

    assert unauthorized.status_code == 401
    assert forbidden_readonly.status_code == 403
    assert authorized.status_code == 200
    assert readonly_telemetry.status_code == 200


async def test_trace_search_endpoint(tmp_path):
    config = AppConfig(postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    create_response = client.post(
        "/api/conversations/trace-search/messages",
        json={"message": "Show me case volume by status"},
    )
    assert create_response.status_code == 200

    search_response = client.get(
        "/api/traces",
        params={"conversation_id": "trace-search", "step": "primary-agent-selected", "limit": 10},
    )

    assert search_response.status_code == 200
    payload = search_response.json()
    assert payload["count"] >= 1
    assert any(item.get("step") == "primary-agent-selected" for item in payload["items"])


async def test_export_verify_endpoint(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="test-signing-secret",
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    client.post(
        "/api/conversations/verify-conv/messages",
        json={"message": "hello"},
    )
    export_response = client.get("/api/telemetry/export")
    assert export_response.status_code == 200

    verification_response = client.post(
        "/api/exports/verify",
        json=export_response.json(),
    )
    assert verification_response.status_code == 200
    assert verification_response.json()["valid"] is True
    assert export_response.json()["key_id"] == "k-current"
    assert export_response.headers.get("x-agentforce-key-id") == "k-current"
    assert export_response.headers.get("x-agentforce-signature")


async def test_export_verify_supports_previous_rotation_key(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_signing_previous_keys="k-old:old-secret",
        export_verify_max_age_seconds=0,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2026-01-01T00:00:00Z"
    nonce = "nonce-rotation-1"
    payload_data = {"demo": "value", "count": 1}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-old", "nonce": nonce, "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    old_sig = hmac.new("old-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    verification_response = client.post(
        "/api/exports/verify",
        json={
            "generated_at": generated_at,
            "signature": old_sig,
            "signature_algorithm": "HMAC-SHA256",
            "key_id": "k-old",
            "nonce": nonce,
            "data": payload_data,
        },
    )

    assert verification_response.status_code == 200
    assert verification_response.json()["valid"] is True
    assert verification_response.json()["key_id"] == "k-old"


async def test_export_verify_rejects_expired_previous_key(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_signing_previous_keys="k-old:old-secret",
        export_signing_previous_key_expiry="k-old:2000-01-01T00:00:00Z",
        export_verify_max_age_seconds=0,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2026-04-03T00:00:00Z"
    nonce = "nonce-expired-1"
    payload_data = {"demo": "expired-key", "v": 2}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-old", "nonce": nonce, "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    old_sig = hmac.new("old-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    verification_response = client.post(
        "/api/exports/verify",
        json={
            "generated_at": generated_at,
            "signature": old_sig,
            "signature_algorithm": "HMAC-SHA256",
            "key_id": "k-old",
            "nonce": nonce,
            "data": payload_data,
        },
    )

    assert verification_response.status_code == 200
    assert verification_response.json()["valid"] is False
    assert "Expired key_id" in verification_response.json().get("reason", "")


async def test_export_verify_replay_window_expired(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_verify_max_age_seconds=1,
        export_verify_max_clock_skew_seconds=0,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2000-01-01T00:00:00Z"
    nonce = "nonce-replay-old"
    payload_data = {"demo": "replay-window", "v": 4}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-current", "nonce": nonce, "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    current_sig = hmac.new("current-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()

    verification_response = client.post(
        "/api/exports/verify",
        json={
            "generated_at": generated_at,
            "signature": current_sig,
            "signature_algorithm": "HMAC-SHA256",
            "key_id": "k-current",
            "nonce": nonce,
            "data": payload_data,
        },
    )

    assert verification_response.status_code == 200
    assert verification_response.json()["valid"] is False
    assert "replay window expired" in verification_response.json().get("reason", "").lower()


async def test_signing_key_metadata_endpoint(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_signing_previous_keys="k-old:old-secret",
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    response = client.get("/.well-known/agentforce-signing-keys")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_key_id"] == "k-current"
    assert "k-old" in payload["previous_key_ids"]


async def test_export_verify_rejects_reused_nonce_and_records_audit(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_verify_max_age_seconds=0,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2026-04-03T00:00:00Z"
    nonce = "nonce-once-only"
    payload_data = {"demo": "nonce-reuse", "v": 6}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-current", "nonce": nonce, "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    sig = hmac.new("current-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    body = {
        "generated_at": generated_at,
        "signature": sig,
        "signature_algorithm": "HMAC-SHA256",
        "key_id": "k-current",
        "nonce": nonce,
        "data": payload_data,
    }

    first = client.post("/api/exports/verify", json=body)
    second = client.post("/api/exports/verify", json=body)
    audit = client.get("/api/security/audit")

    assert first.status_code == 200
    assert first.json()["valid"] is True
    assert second.status_code == 200
    assert second.json()["valid"] is False
    assert "Nonce already used" in second.json()["reason"]
    assert audit.status_code == 200
    assert any(item["event_type"] == "export_verify" for item in audit.json()["items"])


async def test_export_verify_rate_limit(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_verify_max_age_seconds=0,
        export_verify_rate_limit_count=1,
        export_verify_rate_limit_window_seconds=60,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2026-04-03T00:00:00Z"
    payload_data = {"demo": "rate-limit", "v": 7}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-current", "nonce": "nonce-rate-1", "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    sig = hmac.new("current-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    body = {
        "generated_at": generated_at,
        "signature": sig,
        "signature_algorithm": "HMAC-SHA256",
        "key_id": "k-current",
        "nonce": "nonce-rate-1",
        "data": payload_data,
    }

    first = client.post("/api/exports/verify", json=body, headers={"x-api-key": ""})
    second = client.post("/api/exports/verify", json=body, headers={"x-api-key": ""})

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["valid"] is False
    assert "rate limit" in second.json()["reason"].lower()


async def test_security_audit_query_filters(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_verify_max_age_seconds=0,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    generated_at = "2026-04-03T00:00:00Z"
    payload_data = {"demo": "filter", "v": 9}
    canonical = json.dumps(
        {"generated_at": generated_at, "key_id": "k-current", "nonce": "nonce-filter-1", "data": payload_data},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    sig = hmac.new("current-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    body = {
        "generated_at": generated_at,
        "signature": sig,
        "signature_algorithm": "HMAC-SHA256",
        "key_id": "k-current",
        "nonce": "nonce-filter-1",
        "data": payload_data,
    }

    first = client.post("/api/exports/verify", json=body)
    second = client.post("/api/exports/verify", json=body)
    succeeded = client.get("/api/security/audit", params={"outcome": "succeeded"})
    failed = client.get("/api/security/audit", params={"outcome": "failed"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert succeeded.status_code == 200
    assert failed.status_code == 200
    assert succeeded.json()["items"]
    assert failed.json()["items"]
    assert all(item["outcome"] == "succeeded" for item in succeeded.json()["items"])
    assert all(item["outcome"] == "failed" for item in failed.json()["items"])


async def test_security_audit_retention_limit_applied(tmp_path):
    config = AppConfig(
        postgres_dsn=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        export_signing_key_id="k-current",
        export_signing_secret="current-secret",
        export_verify_max_age_seconds=0,
        security_audit_retention_max_events=2,
    )
    engine = WorkflowEngine(config)
    app = create_app(engine)
    client = TestClient(app)

    for idx in range(3):
        nonce = f"nonce-retention-{idx}"
        generated_at = "2026-04-03T00:00:00Z"
        payload_data = {"demo": "retention", "i": idx}
        canonical = json.dumps(
            {"generated_at": generated_at, "key_id": "k-current", "nonce": nonce, "data": payload_data},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        sig = hmac.new("current-secret".encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        body = {
            "generated_at": generated_at,
            "signature": sig,
            "signature_algorithm": "HMAC-SHA256",
            "key_id": "k-current",
            "nonce": nonce,
            "data": payload_data,
        }
        result = client.post("/api/exports/verify", json=body)
        assert result.status_code == 200

    audit = client.get("/api/security/audit", params={"limit": 10})
    assert audit.status_code == 200
    assert len(audit.json()["items"]) == 2


def test_startup_validation_fails_without_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("API_AUTH_ENABLED", "true")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("API_ADMIN_KEY", raising=False)
    monkeypatch.delenv("API_READONLY_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

    app = create_app()
    with pytest.raises(RuntimeError):
        with TestClient(app):
            pass
