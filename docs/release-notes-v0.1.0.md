# AgentForce Simulator v0.1.0

Initial public release of AgentForce Simulator: a Python asyncio multi-agent workflow engine for support, analytics, and escalation demos with signed export verification, auditability, and interactive operations tooling.

## Highlights

- Async multi-agent orchestration with support, analysis, and escalation handoffs.
- Streamlit test console with chat, trace dashboard, telemetry views, and security panel.
- FastAPI endpoints for conversations, telemetry, traces, signed exports, and verification.
- Security controls: API roles, key rotation support, replay controls, verify rate limiting, and persisted audit events.
- Audit explorer filters and retention guardrails.
- Deterministic security smoke script for operational verification.
- CI workflow for automated tests and smoke checks.

## Included Artifacts

- Changelog: CHANGELOG.md
- CI workflow: .github/workflows/ci.yml
- Smoke script: scripts/security_smoke.py
- Demo runbook and release checklist: README.md

## Validation Snapshot

- Test suite: 19 passed.
- Security smoke check: ok=true against local API.

## Upgrade / Migration Notes

- No prior public release baseline; this is the initial tagged release.

## Recommended Post-Release Actions

1. Enable branch protection requiring CI pass on pull requests.
2. Add repository-level secrets for production deployment workflows.
3. Define v0.1.1 scope for incremental hardening and UX polish.

## Suggested GitHub Release Body

```markdown
## AgentForce Simulator v0.1.0

Initial release of the multi-agent workflow demo with production-style security controls and observability.

### What is included
- Async support, analysis, and escalation agents with orchestrated handoffs
- Streamlit console with traces, telemetry, and security operations panel
- FastAPI for conversation, telemetry, trace, and signed export flows
- HMAC signing with key IDs, rotation support, replay and nonce protections
- Persisted security audit events with filters and retention controls
- CI pipeline and deterministic security smoke verification

### Validation
- Test suite passing
- Security smoke checks passing
```
