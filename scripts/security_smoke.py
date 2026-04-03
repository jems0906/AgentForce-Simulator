from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

import httpx


def _build_headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return {}
    return {"x-api-key": api_key}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AgentForce API security smoke checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001", help="API base URL")
    parser.add_argument("--api-key", default=None, help="Readonly API key")
    parser.add_argument("--rate-limit-probe", type=int, default=40, help="Max probe calls for rate-limit check")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    headers = _build_headers(args.api_key)

    summary: dict[str, object] = {
        "health": None,
        "first_verify_valid": None,
        "replay_verify_valid": None,
        "replay_reason": None,
        "rate_limit_triggered_at": None,
        "latest_audit_events": [],
    }

    try:
        with httpx.Client(timeout=15.0) as client:
            health = client.get(f"{args.base_url}/api/health")
            health.raise_for_status()
            summary["health"] = health.json().get("status")

            export_resp = client.get(f"{args.base_url}/api/telemetry/export", headers=headers)
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

            verify_first = client.post(f"{args.base_url}/api/exports/verify", headers=headers, json=verify_body)
            verify_first.raise_for_status()
            first_json = verify_first.json()
            summary["first_verify_valid"] = first_json.get("valid")

            verify_replay = client.post(f"{args.base_url}/api/exports/verify", headers=headers, json=verify_body)
            verify_replay.raise_for_status()
            replay_json = verify_replay.json()
            summary["replay_verify_valid"] = replay_json.get("valid")
            summary["replay_reason"] = replay_json.get("reason")

            for i in range(1, max(args.rate_limit_probe, 0) + 1):
                probe_body = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "signature": "bad-signature",
                    "signature_algorithm": "BAD",
                    "key_id": verify_body.get("key_id"),
                    "nonce": f"smoke-rate-{i}",
                    "data": {"probe": i},
                }
                probe = client.post(f"{args.base_url}/api/exports/verify", headers=headers, json=probe_body)
                probe.raise_for_status()
                probe_json = probe.json()
                if probe_json.get("reason") == "Verify rate limit exceeded.":
                    summary["rate_limit_triggered_at"] = i
                    break

            audit = client.get(f"{args.base_url}/api/security/audit", headers=headers, params={"limit": 5})
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
    except httpx.HTTPError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "summary": summary}, indent=2))
        return 1

    ok = (
        summary["health"] == "ok"
        and summary["first_verify_valid"] is True
        and summary["replay_verify_valid"] is False
        and summary["replay_reason"] == "Nonce already used."
    )
    print(json.dumps({"ok": ok, "summary": summary}, indent=2))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
