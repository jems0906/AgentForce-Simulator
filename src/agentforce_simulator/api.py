from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel

from agentforce_simulator import AppConfig, WorkflowEngine
from agentforce_simulator.schemas import SecurityAuditEvent


class MessageRequest(BaseModel):
    message: str


class VerifyExportRequest(BaseModel):
    generated_at: str | None = None
    signature: str | None = None
    signature_algorithm: str | None = None
    key_id: str | None = None
    nonce: str | None = None
    data: dict


LOGGER = logging.getLogger("agentforce.api.security")


def _resolve_role(config: AppConfig, supplied_key: str) -> str | None:
    if config.api_admin_key and supplied_key == config.api_admin_key:
        return "admin"
    if config.api_readonly_key and supplied_key == config.api_readonly_key:
        return "readonly"
    if config.api_key and supplied_key == config.api_key:
        return "admin"
    return None


def _auth_dependency(config: AppConfig, required_role: str):
    async def _verify_api_key(x_api_key: str | None = Header(default=None)) -> None:
        if not config.api_auth_enabled:
            return
        if not any([config.api_key, config.api_admin_key, config.api_readonly_key]):
            raise HTTPException(status_code=500, detail="API auth is enabled but no API keys are configured.")
        if not x_api_key:
            raise HTTPException(status_code=401, detail="Missing API key.")
        caller_role = _resolve_role(config, x_api_key)
        if caller_role is None:
            raise HTTPException(status_code=401, detail="Invalid API key.")
        if required_role == "admin" and caller_role != "admin":
            raise HTTPException(status_code=403, detail="Admin role required for this endpoint.")

    return _verify_api_key


def _build_signing_keys(config: AppConfig) -> dict[str, str]:
    keys: dict[str, str] = {}
    if config.export_signing_secret:
        keys[config.export_signing_key_id] = config.export_signing_secret
    raw_previous = (config.export_signing_previous_keys or "").strip()
    if not raw_previous:
        return keys
    for token in raw_previous.split(","):
        item = token.strip()
        if not item:
            continue
        if ":" not in item:
            raise RuntimeError(
                "Invalid EXPORT_SIGNING_PREVIOUS_KEYS format. Expected comma-separated key_id:secret pairs."
            )
        key_id, secret = item.split(":", 1)
        key_id = key_id.strip()
        secret = secret.strip()
        if not key_id or not secret:
            raise RuntimeError(
                "Invalid EXPORT_SIGNING_PREVIOUS_KEYS entry. Both key_id and secret are required."
            )
        keys[key_id] = secret
    return keys


def _build_previous_key_expiry(config: AppConfig) -> dict[str, datetime]:
    expiries: dict[str, datetime] = {}
    raw = (config.export_signing_previous_key_expiry or "").strip()
    if not raw:
        return expiries
    for token in raw.split(","):
        item = token.strip()
        if not item:
            continue
        if ":" not in item:
            raise RuntimeError(
                "Invalid EXPORT_SIGNING_PREVIOUS_KEY_EXPIRY format. Expected comma-separated key_id:iso8601 pairs."
            )
        key_id, ts = item.split(":", 1)
        key_id = key_id.strip()
        ts = ts.strip()
        if not key_id or not ts:
            raise RuntimeError(
                "Invalid EXPORT_SIGNING_PREVIOUS_KEY_EXPIRY entry. Both key_id and timestamp are required."
            )
        try:
            expiries[key_id] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError(
                f"Invalid expiry timestamp '{ts}' for key_id '{key_id}'."
            ) from exc
    return expiries


def _canonical_signing_input(generated_at: str, key_id: str | None, nonce: str | None, payload: dict) -> str:
    envelope = {
        "generated_at": generated_at,
        "key_id": key_id,
        "nonce": nonce,
        "data": payload,
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str)


def _signed_payload(config: AppConfig, payload: dict, signing_keys: dict[str, str]) -> dict:
    generated_at = datetime.now(timezone.utc).isoformat()
    nonce = str(uuid4())
    if not config.export_signing_secret:
        return {
            "generated_at": generated_at,
            "signature": None,
            "signature_algorithm": None,
            "key_id": None,
            "nonce": nonce,
            "warning": "EXPORT_SIGNING_SECRET is not configured; payload is unsigned.",
            "data": payload,
        }
    secret = signing_keys[config.export_signing_key_id]
    signature = _signature_for_payload(secret, generated_at, config.export_signing_key_id, nonce, payload)
    return {
        "generated_at": generated_at,
        "signature": signature,
        "signature_algorithm": "HMAC-SHA256",
        "key_id": config.export_signing_key_id,
        "nonce": nonce,
        "data": payload,
    }


def _signature_for_payload(secret: str, generated_at: str, key_id: str | None, nonce: str | None, payload: dict) -> str:
    body = _canonical_signing_input(generated_at, key_id, nonce, payload)
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _set_signature_headers(response: Response, payload: dict) -> None:
    signature = payload.get("signature")
    algorithm = payload.get("signature_algorithm")
    key_id = payload.get("key_id")
    if signature:
        response.headers["x-agentforce-signature"] = str(signature)
    if algorithm:
        response.headers["x-agentforce-signature-algorithm"] = str(algorithm)
    if key_id:
        response.headers["x-agentforce-key-id"] = str(key_id)


def _validate_startup_config(config: AppConfig) -> None:
    if config.api_auth_enabled and not any([config.api_key, config.api_admin_key, config.api_readonly_key]):
        raise RuntimeError("API_AUTH_ENABLED=true but no API keys are configured.")
    if config.export_signing_secret and not config.export_signing_key_id:
        raise RuntimeError("EXPORT_SIGNING_SECRET is configured but EXPORT_SIGNING_KEY_ID is empty.")
    if config.export_verify_max_age_seconds < 0:
        raise RuntimeError("EXPORT_VERIFY_MAX_AGE_SECONDS cannot be negative.")
    if config.export_verify_max_clock_skew_seconds < 0:
        raise RuntimeError("EXPORT_VERIFY_MAX_CLOCK_SKEW_SECONDS cannot be negative.")
    if config.export_verify_rate_limit_count < 0:
        raise RuntimeError("EXPORT_VERIFY_RATE_LIMIT_COUNT cannot be negative.")
    if config.export_verify_rate_limit_window_seconds < 0:
        raise RuntimeError("EXPORT_VERIFY_RATE_LIMIT_WINDOW_SECONDS cannot be negative.")
    if config.security_audit_retention_max_events < 0:
        raise RuntimeError("SECURITY_AUDIT_RETENTION_MAX_EVENTS cannot be negative.")
    _build_previous_key_expiry(config)


def create_app(engine: WorkflowEngine | None = None) -> FastAPI:
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    config = engine.config if engine else AppConfig.from_env()
    workflow_engine = engine or WorkflowEngine(config)
    signing_keys = _build_signing_keys(config)
    previous_key_expiry = _build_previous_key_expiry(config)
    verify_admin = _auth_dependency(config, required_role="admin")
    verify_read = _auth_dependency(config, required_role="readonly")
    verify_rate_limit_buckets: dict[str, deque[float]] = defaultdict(deque)
    used_nonces: dict[str, datetime] = {}
    startup_lock = asyncio.Lock()

    async def ensure_engine_started() -> None:
        if workflow_engine._started:
            return
        async with startup_lock:
            await workflow_engine.startup()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _validate_startup_config(config)
        await ensure_engine_started()
        yield

    app = FastAPI(title="AgentForce Simulator API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def add_request_correlation_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.get("/.well-known/agentforce-signing-keys")
    async def get_signing_keys_metadata() -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "generated_at": now,
            "current_key_id": config.export_signing_key_id if config.export_signing_secret else None,
            "previous_key_ids": [
                key_id for key_id in signing_keys.keys() if key_id != config.export_signing_key_id
            ],
            "signature_algorithm": "HMAC-SHA256",
        }

    @app.get("/api/security/audit")
    async def get_security_audit(
        limit: int = Query(default=100, ge=1, le=1000),
        event_type: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        key_id: str | None = Query(default=None),
        request_id: str | None = Query(default=None),
        since: str | None = Query(default=None, description="Optional ISO timestamp lower bound."),
        until: str | None = Query(default=None, description="Optional ISO timestamp upper bound."),
        _: None = Depends(verify_read),
    ) -> dict:
        since_dt = None
        until_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {exc}") from exc
        if until:
            try:
                until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid until timestamp: {exc}") from exc
        if since_dt and until_dt and since_dt > until_dt:
            raise HTTPException(status_code=400, detail="since cannot be greater than until.")

        await ensure_engine_started()
        return {
            "items": await workflow_engine.storage.get_security_audit_events(
                limit=limit,
                event_type=event_type,
                outcome=outcome,
                key_id=key_id,
                request_id=request_id,
                since=since_dt,
                until=until_dt,
            )
        }

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        await workflow_engine.startup()
        return {"status": "ok"}

    @app.post("/api/conversations/{conversation_id}/messages")
    async def post_message(
        conversation_id: str,
        request: MessageRequest,
        _: None = Depends(verify_admin),
    ) -> dict:
        response = await workflow_engine.process_user_message(conversation_id, request.message)
        return response.to_dict()

    @app.get("/api/conversations/{conversation_id}/history")
    async def get_history(
        conversation_id: str,
        _: None = Depends(verify_read),
    ) -> list[dict]:
        history = await workflow_engine.get_conversation_history(conversation_id)
        return [turn.to_dict() for turn in history]

    @app.get("/api/conversations/{conversation_id}/latest-trace")
    async def get_latest_trace(
        conversation_id: str,
        _: None = Depends(verify_read),
    ) -> list[dict]:
        history = await workflow_engine.get_conversation_history(conversation_id)
        assistant_turns = [turn for turn in history if turn.role == "assistant"]
        if not assistant_turns:
            raise HTTPException(status_code=404, detail="No assistant trace found for this conversation.")
        return assistant_turns[-1].metadata.get("trace", [])

    @app.get("/api/traces")
    async def search_traces(
        conversation_id: str = Query(..., description="Conversation ID to search trace events in."),
        agent: str | None = Query(default=None, description="Optional trace agent filter, e.g. support or analysis."),
        step: str | None = Query(default=None, description="Optional trace step filter, e.g. primary-agent-selected."),
        since: str | None = Query(default=None, description="Optional ISO timestamp lower bound."),
        limit: int = Query(default=100, ge=1, le=1000),
        _: None = Depends(verify_read),
    ) -> dict:
        since_dt = None
        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=f"Invalid since timestamp: {exc}") from exc
        history = await workflow_engine.get_conversation_history(conversation_id)
        assistant_turns = [turn for turn in history if turn.role == "assistant"]
        matches: list[dict] = []
        for turn in assistant_turns:
            trace = turn.metadata.get("trace", []) if isinstance(turn.metadata, dict) else []
            for item in trace:
                created_at_raw = item.get("created_at")
                created_at_dt = None
                if created_at_raw:
                    try:
                        created_at_dt = datetime.fromisoformat(str(created_at_raw).replace("Z", "+00:00"))
                    except ValueError:
                        created_at_dt = None
                if since_dt and created_at_dt and created_at_dt < since_dt:
                    continue
                if agent and str(item.get("agent_name") or "").lower() != agent.lower():
                    continue
                if step and str(item.get("step") or "").lower() != step.lower():
                    continue
                matches.append(item)
                if len(matches) >= limit:
                    return {"conversation_id": conversation_id, "count": len(matches), "items": matches}
        return {"conversation_id": conversation_id, "count": len(matches), "items": matches}

    @app.get("/api/conversations/{conversation_id}/export")
    async def export_conversation(
        conversation_id: str,
        response: Response,
        _: None = Depends(verify_read),
    ) -> dict:
        history = await workflow_engine.get_conversation_history(conversation_id)
        transcript = [turn.to_dict() for turn in history]
        payload = {
            "conversation_id": conversation_id,
            "turn_count": len(transcript),
            "transcript": transcript,
        }
        signed = _signed_payload(config, payload, signing_keys)
        _set_signature_headers(response, signed)
        return signed

    @app.get("/api/telemetry")
    async def get_telemetry(_: None = Depends(verify_read)) -> dict:
        return await workflow_engine.get_dashboard_snapshot()

    @app.get("/api/telemetry/export")
    async def export_telemetry(response: Response, _: None = Depends(verify_read)) -> dict:
        snapshot = await workflow_engine.get_dashboard_snapshot()
        signed = _signed_payload(config, snapshot, signing_keys)
        _set_signature_headers(response, signed)
        return signed

    @app.post("/api/exports/verify")
    async def verify_export_signature(
        payload: VerifyExportRequest,
        request: Request,
        x_api_key: str | None = Header(default=None),
        _: None = Depends(verify_read),
    ) -> dict:
        request_id = getattr(request.state, "request_id", "unknown")
        caller_token = x_api_key or (request.client.host if request.client else "anonymous")

        async def audit(outcome: str, reason: str | None, key_id: str | None) -> None:
            await ensure_engine_started()
            await workflow_engine.storage.record_security_audit_event(
                SecurityAuditEvent(
                    event_type="export_verify",
                    outcome=outcome,
                    request_id=request_id,
                    key_id=key_id,
                    reason=reason,
                    metadata={
                        "path": str(request.url.path),
                        "client": request.client.host if request.client else None,
                    },
                )
            )

        now = datetime.now(timezone.utc)
        window_seconds = config.export_verify_rate_limit_window_seconds
        if config.export_verify_rate_limit_count > 0 and window_seconds > 0:
            bucket = verify_rate_limit_buckets[caller_token]
            now_ts = now.timestamp()
            while bucket and now_ts - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= config.export_verify_rate_limit_count:
                LOGGER.info("signature_verify_rejected: rate_limit_exceeded request_id=%s", request_id)
                await audit("rejected", "Rate limit exceeded.", payload.key_id)
                return {"valid": False, "reason": "Verify rate limit exceeded."}
            bucket.append(now_ts)

        if not signing_keys:
            raise HTTPException(status_code=400, detail="EXPORT_SIGNING_SECRET is not configured on this server.")
        if payload.signature_algorithm != "HMAC-SHA256":
            LOGGER.info("signature_verify_rejected: unsupported_algorithm request_id=%s", request_id)
            await audit("failed", "Unsupported or missing signature algorithm.", payload.key_id)
            return {"valid": False, "reason": "Unsupported or missing signature algorithm."}
        if not payload.signature:
            LOGGER.info("signature_verify_rejected: missing_signature request_id=%s", request_id)
            await audit("failed", "Missing signature.", payload.key_id)
            return {"valid": False, "reason": "Missing signature."}

        if config.export_verify_max_age_seconds > 0:
            if not payload.generated_at:
                LOGGER.info("signature_verify_rejected: missing_generated_at request_id=%s", request_id)
                await audit("failed", "Missing generated_at for replay protection.", payload.key_id)
                return {"valid": False, "reason": "Missing generated_at for replay protection."}
            try:
                generated_at = datetime.fromisoformat(payload.generated_at.replace("Z", "+00:00"))
            except ValueError:
                LOGGER.info("signature_verify_rejected: invalid_generated_at request_id=%s", request_id)
                await audit("failed", "Invalid generated_at timestamp.", payload.key_id)
                return {"valid": False, "reason": "Invalid generated_at timestamp."}
            age_seconds = (now - generated_at).total_seconds()
            if age_seconds < -config.export_verify_max_clock_skew_seconds:
                LOGGER.info("signature_verify_rejected: future_generated_at request_id=%s", request_id)
                await audit("failed", "generated_at is too far in the future.", payload.key_id)
                return {"valid": False, "reason": "generated_at is too far in the future."}
            if age_seconds > config.export_verify_max_age_seconds:
                LOGGER.info("signature_verify_rejected: replay_window_expired request_id=%s", request_id)
                await audit("failed", "Signature replay window expired.", payload.key_id)
                return {"valid": False, "reason": "Signature replay window expired."}

        if payload.nonce:
            expired_nonces = [nonce for nonce, expires_at in used_nonces.items() if expires_at <= now]
            for nonce in expired_nonces:
                used_nonces.pop(nonce, None)
            if payload.nonce in used_nonces:
                LOGGER.info("signature_verify_rejected: nonce_reused request_id=%s", request_id)
                await audit("failed", "Nonce already used.", payload.key_id)
                return {"valid": False, "reason": "Nonce already used.", "key_id": payload.key_id}

        key_id = payload.key_id or config.export_signing_key_id
        secret = signing_keys.get(key_id)
        if not secret:
            LOGGER.info("signature_verify_rejected: unknown_key_id=%s request_id=%s", key_id, request_id)
            await audit("failed", f"Unknown key_id: {key_id}", key_id)
            return {"valid": False, "reason": f"Unknown key_id: {key_id}"}
        if key_id != config.export_signing_key_id:
            expiry = previous_key_expiry.get(key_id)
            if expiry and datetime.now(timezone.utc) > expiry:
                LOGGER.info("signature_verify_rejected: expired_previous_key=%s request_id=%s", key_id, request_id)
                await audit("failed", f"Expired key_id: {key_id}", key_id)
                return {"valid": False, "reason": f"Expired key_id: {key_id}", "key_id": key_id}
        expected = _signature_for_payload(secret, payload.generated_at or "", key_id, payload.nonce, payload.data)
        valid = hmac.compare_digest(payload.signature, expected)
        if valid:
            LOGGER.info("signature_verify_succeeded: key_id=%s request_id=%s", key_id, request_id)
            if payload.nonce:
                ttl_seconds = config.export_verify_max_age_seconds or config.export_verify_rate_limit_window_seconds or 300
                used_nonces[payload.nonce] = now + timedelta(seconds=ttl_seconds)
            await audit("succeeded", None, key_id)
        else:
            LOGGER.info("signature_verify_rejected: signature_mismatch key_id=%s request_id=%s", key_id, request_id)
            await audit("failed", "Signature mismatch.", key_id)
        return {"valid": valid, "key_id": key_id}

    return app