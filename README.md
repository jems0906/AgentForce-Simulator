# AgentForce Simulator

AgentForce Simulator is a Python asyncio workflow engine that demonstrates Salesforce Agentforce-style multi-agent orchestration. It includes a support agent, a data analysis agent, and an escalation agent, with pluggable reasoning backends, conversation memory, telemetry, A/B testing, and a Streamlit test console.

## Badges

[![CI](https://github.com/jems0906/AgentForce-Simulator/actions/workflows/ci.yml/badge.svg)](https://github.com/jems0906/AgentForce-Simulator/actions/workflows/ci.yml)

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).
Next iteration planning is tracked in [docs/v0.1.1-backlog.md](docs/v0.1.1-backlog.md).

## Features

- Async workflow engine coordinating multiple AI agents
- Customer support, data analysis, and escalation agents with handoffs
- Tool calling for weather, stock prices, case lookup, KPI summaries, and SQL-backed analysis
- Conversation memory with context-window trimming
- Telemetry for latency, success rate, and fallback rate tracking
- A/B routing between support agent versions
- REST API for programmatic conversations and telemetry access
- Structured trace views for routing, handoffs, tool calls, and persistence
- PostgreSQL-compatible SQL storage and DynamoDB storage support
- Streamlit UI for interactive testing

## Quick Start

1. Create and activate a Python 3.11+ environment.
2. Install dependencies:

```bash
pip install -e .[dev]
```

3. Copy `.env.example` to `.env` and adjust values if needed.
4. Run the Streamlit app:

```bash
streamlit run streamlit_app.py
```

## 5-Minute Demo Runbook

1. Start the full stack:

```bash
docker compose up -d --build
```

2. Verify API health:

```bash
curl http://127.0.0.1:8001/api/health
```

3. Run the security smoke check (uses readonly key from `.env`):

```bash
python scripts/security_smoke.py --base-url http://127.0.0.1:8001 --api-key <readonly-key>
```

4. Open the Streamlit console:

- `http://127.0.0.1:8501`
- Send one support prompt and one analytics prompt.
- Open the `Security` section and run `Run Security Smoke Check`.

5. Export evidence artifacts from the UI:

- `Download Smoke Report JSON`
- `Download Audit JSON`

6. Stop the stack when done:

```bash
docker compose down
```

## Release Checklist

### Pre-Demo

1. Start services with `docker compose up -d --build`.
2. Confirm API health at `http://127.0.0.1:8001/api/health`.
3. Run `python scripts/security_smoke.py --base-url http://127.0.0.1:8001 --api-key <readonly-key>` and verify `"ok": true`.
4. Open Streamlit (`http://127.0.0.1:8501`) and validate one support flow, one analytics flow, and one escalation flow.
5. In Streamlit `Security`, run smoke check and export both JSON artifacts.

### Pre-Merge

1. Run `python -m pytest` locally and confirm all tests pass.
2. Ensure CI workflow in [.github/workflows/ci.yml](.github/workflows/ci.yml) is green.
3. Verify no placeholder values remain in production `.env` (API keys, signing keys, DSN).
4. Review audit retention and verify-rate settings for target environment.
5. Update README endpoints/config docs if any API contract changed.

### Tagging v0.1.0

If this workspace is not yet a git repository, initialize and connect it first:

```bash
git init
git add .
git commit -m "release: prepare v0.1.0"
```

Then create the release tag:

```bash
git tag -a v0.1.0 -m "AgentForce Simulator v0.1.0"
git push origin main --tags
```

Use [docs/release-notes-v0.1.0.md](docs/release-notes-v0.1.0.md) as the GitHub Release description template.

## Docker And PostgreSQL

Run the full app with PostgreSQL using Docker Compose:

```bash
docker compose up --build
```

This starts:

- `postgres`: PostgreSQL 16 with an `agentforce` database
- `app`: Streamlit on `http://localhost:8501`
- `api`: FastAPI on `http://localhost:8001`

If your environment intermittently times out on `localhost`, use `127.0.0.1` (for example, `http://127.0.0.1:8001`) for API requests.

The compose file already wires `POSTGRES_DSN=postgresql+asyncpg://agentforce:agentforce@postgres:5432/agentforce` for the app container.
PostgreSQL stays on the internal Docker network by default, which avoids conflicts with any local database already using port `5432`.
For API security settings, Compose reads values from the local `.env` file using variable substitution so keys are not hardcoded in `docker-compose.yml`.

## REST API

Run the API locally:

```bash
uvicorn api_app:app --reload --port 8000
```

Available endpoints:

- `GET /api/health`
- `POST /api/conversations/{conversation_id}/messages`
- `GET /api/conversations/{conversation_id}/history`
- `GET /api/conversations/{conversation_id}/latest-trace`
- `GET /api/conversations/{conversation_id}/export`
- `GET /api/traces?conversation_id=...&agent=...&step=...&since=...&limit=...`
- `GET /api/telemetry`
- `GET /api/telemetry/export`
- `POST /api/exports/verify`
- `GET /api/security/audit`
- `GET /.well-known/agentforce-signing-keys`

### Optional API Key Auth And Roles

Set these environment variables to protect API endpoints:

- `API_AUTH_ENABLED=true`
- `API_ADMIN_KEY=<admin-secret-key>`
- `API_READONLY_KEY=<readonly-secret-key>`

Compatibility mode is also supported with a single key:

- `API_KEY=<single-secret-key>`

Role behavior:

- `admin`: can post new conversation messages and access all read/export endpoints
- `readonly`: can access history, trace search, and telemetry/export endpoints but cannot post messages

The API performs startup validation and will fail fast when `API_AUTH_ENABLED=true` but no API key variables are configured.

When enabled, pass the key in the `x-api-key` header.

### Signed Export Payloads

Set `EXPORT_SIGNING_SECRET=<secret>` to sign export payloads using `HMAC-SHA256`.
Optionally set `EXPORT_SIGNING_KEY_ID=<key-id>` (defaults to `current`) so exports include which key signed them.
For rotation windows, set `EXPORT_SIGNING_PREVIOUS_KEYS=<key-id:secret,key-id:secret>` so verification accepts previous keys.
To enforce rotation cutovers, set `EXPORT_SIGNING_PREVIOUS_KEY_EXPIRY=<key-id:iso8601,key-id:iso8601>` so expired previous keys are rejected.
To reduce replay risk, set `EXPORT_VERIFY_MAX_AGE_SECONDS` (default `300`) and `EXPORT_VERIFY_MAX_CLOCK_SKEW_SECONDS` (default `30`) for verification time-window checks.
To limit brute-force or abuse on `POST /api/exports/verify`, set `EXPORT_VERIFY_RATE_LIMIT_COUNT` (default `30`) and `EXPORT_VERIFY_RATE_LIMIT_WINDOW_SECONDS` (default `60`).
Set `SECURITY_AUDIT_RETENTION_MAX_EVENTS` (default `5000`) to cap persisted verification audit rows.

- `GET /api/conversations/{conversation_id}/export`
- `GET /api/telemetry/export`

Both endpoints return:

- `generated_at`
- `signature`
- `signature_algorithm`
- `key_id`
- `nonce`
- `data`

Both export endpoints also include response headers:

- `x-agentforce-signature`
- `x-agentforce-signature-algorithm`
- `x-agentforce-key-id`

If `EXPORT_SIGNING_SECRET` is not set, `signature` fields are null and a warning is included.

You can verify export signatures by posting the export payload to `POST /api/exports/verify`.
The verifier will use `key_id` to pick the correct active or previous signing key.
Each export also includes a one-time `nonce`; once a signed payload with that nonce is successfully verified, the same nonce is rejected on reuse within the configured retention window.

`GET /.well-known/agentforce-signing-keys` exposes non-secret signing metadata (`current_key_id`, `previous_key_ids`, algorithm) for verifier discovery.

`GET /api/security/audit` exposes recent persisted verification audit events for investigation and monitoring.
It supports filters: `event_type`, `outcome`, `key_id`, `request_id`, `since`, and `until`.

## Security Smoke Check

Run a deterministic end-to-end security check against a running API:

```bash
python scripts/security_smoke.py --base-url http://127.0.0.1:8001 --api-key <readonly-key>
```

The script verifies:

- signed export verification success
- nonce replay rejection
- recent audit event retrieval
- rate-limit trigger probing (best-effort)

## Storage Options

- `STORAGE_BACKEND=postgres`: Uses the SQLAlchemy async backend. For local no-dependency demos, set `POSTGRES_DSN=sqlite+aiosqlite:///./agentforce.db`. For PostgreSQL, use `postgresql+asyncpg://agentforce:agentforce@localhost:5432/agentforce`.
- `STORAGE_BACKEND=dynamodb`: Uses DynamoDB for conversation and telemetry persistence.

## Suggested Demo Prompts

- `What is your refund policy?`
- `What is the weather in Seattle?`
- `Show me case volume by status.`
- `Plot average satisfaction by category.`
- `Show me case 7.`
- `Give me an operations summary.`
- `I need a human, this outage is costing us money.`
- `What is the latest price for MSFT?`

## Architecture

- `WorkflowEngine`: Async orchestration, routing, handoff, persistence, telemetry, and experiment assignment
- `SupportAgent`: FAQ lookup, live tool calling, case lookup, KPI summary, and LLM-backed response generation
- `DataAnalysisAgent`: SQL generation and visualization payloads for Streamlit
- `EscalationAgent`: Human handoff routing for high-risk or low-confidence conversations
- `FastAPI layer`: JSON endpoints for chat simulation, history inspection, and telemetry consumption
- `StorageBackends`: SQL and DynamoDB persistence adapters

## Notes

- OpenAI support is available by setting `LLM_PROVIDER=openai` and `OPENAI_API_KEY`.
- Local Llama support is available through an Ollama-compatible endpoint with `LLM_PROVIDER=ollama`.
- The bundled SQL demo seeds a `support_cases` table for analytics examples.
- The SQL backend is production-ready enough for demos with `asyncpg`, connection liveness checks, and Dockerized PostgreSQL.
