from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
import httpx
import pandas as pd
import streamlit as st

from agentforce_simulator import AppConfig, WorkflowEngine

load_dotenv(dotenv_path=Path(__file__).with_name('.env'), override=False)

st.set_page_config(page_title="AgentForce Simulator", layout="wide")
st.title("AgentForce Simulator")
st.caption("Async multi-agent orchestration demo for support, analytics, and escalation workflows")


def render_trace(trace_steps: list[dict]) -> None:
    if not trace_steps:
        st.info("No trace captured for this turn.")
        return
    trace_frame = pd.DataFrame(trace_steps)
    if "metadata" in trace_frame.columns:
        trace_frame["metadata"] = trace_frame["metadata"].apply(lambda value: str(value))
    st.dataframe(trace_frame, width="stretch")


def collect_trace_rows(messages: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for message in messages:
        metadata = message.get("metadata") if isinstance(message, dict) else {}
        trace = metadata.get("trace") if isinstance(metadata, dict) else []
        for item in trace:
            rows.append(
                {
                    "role": message.get("role"),
                    "agent": metadata.get("agent"),
                    "step": item.get("step"),
                    "detail": item.get("detail"),
                    "trace_agent": item.get("agent_name"),
                    "created_at": item.get("created_at"),
                    "metadata": item.get("metadata"),
                }
            )
    return rows


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _api_headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"x-api-key": api_key}


def run_security_smoke(base_url: str, api_key: str | None, rate_limit_probe: int = 35) -> dict:
    headers = _api_headers(api_key)
    summary: dict[str, object] = {
        "health": None,
        "first_verify_valid": None,
        "replay_verify_valid": None,
        "replay_reason": None,
        "rate_limit_triggered_at": None,
        "latest_audit_events": [],
    }
    with httpx.Client(timeout=15.0) as client:
        health = client.get(f"{base_url}/api/health")
        health.raise_for_status()
        summary["health"] = health.json().get("status")

        export_resp = client.get(f"{base_url}/api/telemetry/export", headers=headers)
        export_resp.raise_for_status()
        export_payload = export_resp.json()

        verify_body = {
            "generated_at": export_payload.get("generated_at"),
            "signature": export_payload.get("signature"),
            "signature_algorithm": export_payload.get("signature_algorithm"),
            "key_id": export_payload.get("key_id"),
            "nonce": export_payload.get("nonce"),
            "data": export_payload.get("data"),
        }

        verify_first = client.post(f"{base_url}/api/exports/verify", headers=headers, json=verify_body)
        verify_first.raise_for_status()
        first_json = verify_first.json()
        summary["first_verify_valid"] = first_json.get("valid")

        verify_replay = client.post(f"{base_url}/api/exports/verify", headers=headers, json=verify_body)
        verify_replay.raise_for_status()
        replay_json = verify_replay.json()
        summary["replay_verify_valid"] = replay_json.get("valid")
        summary["replay_reason"] = replay_json.get("reason")

        for i in range(1, max(rate_limit_probe, 0) + 1):
            probe_body = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "signature": "bad-signature",
                "signature_algorithm": "BAD",
                "key_id": verify_body.get("key_id"),
                "nonce": f"ui-rate-{i}",
                "data": {"probe": i},
            }
            probe = client.post(f"{base_url}/api/exports/verify", headers=headers, json=probe_body)
            probe.raise_for_status()
            probe_json = probe.json()
            if probe_json.get("reason") == "Verify rate limit exceeded.":
                summary["rate_limit_triggered_at"] = i
                break

        audit = client.get(f"{base_url}/api/security/audit", headers=headers, params={"limit": 5})
        audit.raise_for_status()
        items = audit.json().get("items", [])
        summary["latest_audit_events"] = [
            {
                "event_type": item.get("event_type"),
                "outcome": item.get("outcome"),
                "reason": item.get("reason"),
            }
            for item in items
        ]

    ok = (
        summary["health"] == "ok"
        and summary["first_verify_valid"] is True
        and summary["replay_verify_valid"] is False
        and summary["replay_reason"] == "Nonce already used."
    )
    return {"ok": ok, "summary": summary}


def fetch_security_audit(
    base_url: str,
    api_key: str | None,
    limit: int,
    event_type: str,
    outcome: str,
    key_id: str,
    request_id: str,
) -> list[dict]:
    headers = _api_headers(api_key)
    params: dict[str, object] = {"limit": limit}
    if event_type:
        params["event_type"] = event_type
    if outcome:
        params["outcome"] = outcome
    if key_id:
        params["key_id"] = key_id
    if request_id:
        params["request_id"] = request_id

    with httpx.Client(timeout=15.0) as client:
        response = client.get(f"{base_url}/api/security/audit", headers=headers, params=params)
        response.raise_for_status()
        return response.json().get("items", [])


def apply_audit_preset(preset: str) -> None:
    if preset == "Failed-only":
        st.session_state["security_audit_event_type"] = "export_verify"
        st.session_state["security_audit_outcome"] = "failed"
        st.session_state["security_audit_key_id"] = ""
        st.session_state["security_audit_request_id"] = ""
        return
    if preset == "Rate-limited":
        st.session_state["security_audit_event_type"] = "export_verify"
        st.session_state["security_audit_outcome"] = "rejected"
        st.session_state["security_audit_key_id"] = ""
        st.session_state["security_audit_request_id"] = ""
        return
    if preset == "Nonce-reuse":
        st.session_state["security_audit_event_type"] = "export_verify"
        st.session_state["security_audit_outcome"] = "failed"
        st.session_state["security_audit_key_id"] = ""
        st.session_state["security_audit_request_id"] = ""
        return


def render_smoke_badges(smoke_result: dict) -> None:
    summary = smoke_result.get("summary", {}) if isinstance(smoke_result, dict) else {}
    checks = [
        ("Health", summary.get("health") == "ok"),
        ("Primary Verify", summary.get("first_verify_valid") is True),
        ("Replay Block", summary.get("replay_verify_valid") is False and summary.get("replay_reason") == "Nonce already used."),
        ("Rate Limit", summary.get("rate_limit_triggered_at") is not None),
    ]
    cols = st.columns(len(checks))
    for idx, (label, passed) in enumerate(checks):
        with cols[idx]:
            status_text = "PASS" if passed else "FAIL"
            status_color = "green" if passed else "red"
            st.markdown(f"**{label}**  ")
            st.markdown(f":{status_color}[{status_text}]")


@st.cache_resource
def get_engine() -> WorkflowEngine:
    engine = WorkflowEngine(AppConfig.from_env())
    run_async(engine.startup())
    return engine


engine = get_engine()

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = f"conv-{uuid4()}"
if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.subheader("Session")
    st.write(f"Conversation: {st.session_state.conversation_id}")
    if st.button("New Conversation", width="stretch"):
        st.session_state.conversation_id = f"conv-{uuid4()}"
        st.session_state.messages = []
        st.rerun()
    config = AppConfig.from_env()
    st.subheader("Runtime")
    st.write(
        {
            "llm_provider": config.llm_provider,
            "storage_backend": config.storage_backend,
            "context_window_chars": config.context_window_chars,
            "support_rollout": config.support_experiment_rollout,
        }
    )

history = run_async(engine.get_conversation_history(st.session_state.conversation_id))
if history and not st.session_state.messages:
    st.session_state.messages = [turn.to_dict() for turn in history]

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("metadata"):
            with st.expander("Metadata", expanded=False):
                st.json(message["metadata"])
            trace_steps = message["metadata"].get("trace") if isinstance(message["metadata"], dict) else None
            if trace_steps:
                with st.expander("Trace", expanded=False):
                    render_trace(trace_steps)

prompt = st.chat_input("Ask the simulator to resolve support, analytics, or escalation scenarios")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt, "metadata": {}})
    with st.chat_message("user"):
        st.markdown(prompt)

    result = run_async(engine.process_user_message(st.session_state.conversation_id, prompt))
    assistant_message = {
        "role": "assistant",
        "content": result.response,
        "metadata": {
            "agent": result.active_agent,
            "version": result.agent_version,
            "confidence": result.confidence,
            "experiment_bucket": result.experiment_bucket,
            "telemetry": result.telemetry,
            "tools": [tool.tool_name for tool in result.tool_invocations],
            "trace": [step.to_dict() for step in result.trace],
        },
    }
    st.session_state.messages.append(assistant_message)
    with st.chat_message("assistant"):
        st.markdown(result.response)
        st.caption(
            f"Agent: {result.active_agent} | Version: {result.agent_version} | Bucket: {result.experiment_bucket} | Latency: {result.telemetry['latency_ms']} ms"
        )
        if result.visualization_data:
            frame = pd.DataFrame(result.visualization_data)
            st.dataframe(frame, width="stretch")
            numeric_columns = [column for column in frame.columns if pd.api.types.is_numeric_dtype(frame[column])]
            if numeric_columns:
                chart_value = numeric_columns[0]
                label_column = next((column for column in frame.columns if column != chart_value), None)
                if label_column:
                    chart_frame = frame.set_index(label_column)[[chart_value]]
                    st.bar_chart(chart_frame)
        with st.expander("Metadata", expanded=False):
            st.json(assistant_message["metadata"])
        with st.expander("Trace", expanded=False):
            render_trace(assistant_message["metadata"].get("trace", []))

st.divider()
telemetry = run_async(engine.get_dashboard_snapshot())
left, right = st.columns(2)
with left:
    st.subheader("Agent Metrics")
    metrics_frame = pd.DataFrame(telemetry.get("agent_metrics", []))
    if not metrics_frame.empty:
        st.dataframe(metrics_frame, width="stretch")
    else:
        st.info("No telemetry recorded yet.")
with right:
    st.subheader("Experiment Metrics")
    experiment_frame = pd.DataFrame(telemetry.get("experiment_metrics", []))
    if not experiment_frame.empty:
        st.dataframe(experiment_frame, width="stretch")
        if "experiment_bucket" in experiment_frame.columns and "total_runs" in experiment_frame.columns:
            st.bar_chart(experiment_frame.set_index("experiment_bucket")[["total_runs"]])
    else:
        st.info("No experiment data recorded yet.")

st.divider()
st.subheader("Trace Dashboard")
trace_rows = collect_trace_rows(st.session_state.messages)
if trace_rows:
    trace_frame = pd.DataFrame(trace_rows)
    step_options = sorted(trace_frame["step"].dropna().unique().tolist())
    agent_options = sorted(trace_frame["trace_agent"].dropna().unique().tolist())
    selected_steps = st.multiselect("Filter by trace step", step_options, default=step_options)
    selected_agents = st.multiselect("Filter by trace agent", agent_options, default=agent_options)

    filtered = trace_frame.copy()
    if selected_steps:
        filtered = filtered[filtered["step"].isin(selected_steps)]
    if selected_agents:
        filtered = filtered[filtered["trace_agent"].isin(selected_agents)]
    if "metadata" in filtered.columns:
        filtered["metadata"] = filtered["metadata"].apply(lambda value: str(value))
    st.dataframe(filtered, width="stretch")

    export_payload = {
        "conversation_id": st.session_state.conversation_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "trace_rows": filtered.to_dict(orient="records"),
    }
    st.download_button(
        "Download Trace JSON",
        data=json.dumps(export_payload, indent=2, default=str),
        file_name=f"trace-{st.session_state.conversation_id}.json",
        mime="application/json",
    )
else:
    st.info("No trace events available yet. Send a message to populate trace data.")

st.divider()
st.subheader("Security")
default_api_key = config.api_readonly_key or config.api_admin_key or config.api_key or ""
default_api_base_url = os.getenv("AGENTFORCE_API_BASE_URL", "http://127.0.0.1:8001")
security_base_url = st.text_input("API base URL", value=default_api_base_url, key="security_base_url")
security_api_key = st.text_input("API key", value=default_api_key, type="password", key="security_api_key")

smoke_col, _ = st.columns([1, 2])
with smoke_col:
    if st.button("Run Security Smoke Check", width="stretch"):
        try:
            st.session_state["security_smoke_result"] = run_security_smoke(
                security_base_url,
                security_api_key or None,
            )
        except httpx.HTTPError as exc:
            st.session_state["security_smoke_result"] = {"ok": False, "error": str(exc)}

smoke_result = st.session_state.get("security_smoke_result")
if smoke_result:
    render_smoke_badges(smoke_result)
    if smoke_result.get("ok"):
        st.success("Security smoke checks passed.")
    else:
        st.warning("Security smoke checks did not fully pass.")
    st.json(smoke_result)
    st.download_button(
        "Download Smoke Report JSON",
        data=json.dumps(smoke_result, indent=2, default=str),
        file_name=f"security-smoke-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
        mime="application/json",
    )

st.markdown("#### Audit Explorer")
preset_col, _ = st.columns([1, 3])
with preset_col:
    preset = st.selectbox("Preset", ["Custom", "Failed-only", "Rate-limited", "Nonce-reuse"], index=0)
    if st.button("Apply Preset", width="stretch"):
        apply_audit_preset(preset)

a_col1, a_col2, a_col3, a_col4 = st.columns(4)
with a_col1:
    audit_limit = st.number_input("Limit", min_value=1, max_value=1000, value=50, step=1)
with a_col2:
    audit_event_type = st.text_input("event_type", value="", key="security_audit_event_type")
with a_col3:
    audit_outcome = st.text_input("outcome", value="", key="security_audit_outcome")
with a_col4:
    audit_key_id = st.text_input("key_id", value="", key="security_audit_key_id")
audit_request_id = st.text_input("request_id", value="", key="security_audit_request_id")

if st.button("Load Audit Events", width="stretch"):
    try:
        audit_items = fetch_security_audit(
            base_url=security_base_url,
            api_key=security_api_key or None,
            limit=int(audit_limit),
            event_type=audit_event_type.strip(),
            outcome=audit_outcome.strip(),
            key_id=audit_key_id.strip(),
            request_id=audit_request_id.strip(),
        )
        st.session_state["security_audit_items"] = audit_items
    except httpx.HTTPError as exc:
        st.error(f"Failed to load audit events: {exc}")

audit_items = st.session_state.get("security_audit_items", [])
if audit_items:
    audit_frame = pd.DataFrame(audit_items)
    if "metadata" in audit_frame.columns:
        audit_frame["metadata"] = audit_frame["metadata"].apply(lambda value: str(value))
    st.dataframe(audit_frame, width="stretch")
    st.download_button(
        "Download Audit JSON",
        data=json.dumps(audit_items, indent=2, default=str),
        file_name=f"security-audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
        mime="application/json",
    )
else:
    st.info("No security audit events loaded yet.")
