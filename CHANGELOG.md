# Changelog

All notable changes to this project are documented in this file.

## [0.1.0] - 2026-04-03

### Added
- Async multi-agent workflow engine with support, analysis, and escalation agents.
- Streamlit chat interface with trace, telemetry, and security panels.
- FastAPI surface for conversations, traces, telemetry, signed exports, and verification.
- SQL and DynamoDB storage backends with seeded analytics demo data.
- Security audit persistence endpoint with filtering support.
- Deterministic smoke script at scripts/security_smoke.py.
- CI workflow at .github/workflows/ci.yml.

### Changed
- Export verification now signs and verifies envelope fields including nonce and key metadata.
- Startup path now guarantees storage initialization before direct audit writes.
- Security panel now supports JSON evidence exports from the UI.

### Security
- API key auth with admin and readonly roles.
- HMAC export signatures with key ID, previous-key rotation support, and expiry cutovers.
- Replay controls via generated_at window and one-time nonce protection.
- Verify endpoint rate limiting and persisted audit events.
- Audit retention controls through SECURITY_AUDIT_RETENTION_MAX_EVENTS.

### Documentation
- 5-minute demo runbook.
- Release checklist for pre-demo and pre-merge gates.
- CI badge setup instructions.

## Unreleased
- No entries yet.
