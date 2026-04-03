from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx


def run_command(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=str(cwd), text=True, capture_output=True, check=False)


def wait_for_health(base_url: str, timeout_seconds: int) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    health_url = f"{base_url.rstrip('/')}/api/health"
    last_error = ""
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                response = client.get(health_url)
                if response.status_code == 200:
                    payload = response.json()
                    if payload.get("status") == "ok":
                        return True, "ok"
                    last_error = f"Unexpected health payload: {payload}"
                else:
                    last_error = f"HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
            time.sleep(1)
    return False, last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bring up the stack, verify health, run smoke checks, and print summary."
    )
    parser.add_argument("--workspace", default=".", help="Workspace root path")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001", help="API base URL")
    parser.add_argument("--api-key", default=None, help="Readonly API key for smoke checks")
    parser.add_argument("--health-timeout-seconds", type=int, default=60, help="Health wait timeout")
    parser.add_argument("--no-build", action="store_true", help="Skip docker image rebuild")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip running security smoke checks")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace = Path(args.workspace).resolve()

    docker_check = run_command(["docker", "info"], workspace)
    if docker_check.returncode != 0:
        print(json.dumps(
            {
                "ok": False,
                "stage": "docker_check",
                "stdout": docker_check.stdout,
                "stderr": docker_check.stderr,
            },
            indent=2,
        ))
        return docker_check.returncode

    compose_cmd = ["docker", "compose", "up", "-d"]
    if not args.no_build:
        compose_cmd.append("--build")

    compose_result = run_command(compose_cmd, workspace)
    if compose_result.returncode != 0:
        # One retry for transient Docker engine issues.
        time.sleep(2)
        compose_result = run_command(compose_cmd, workspace)
    if compose_result.returncode != 0:
        print(json.dumps(
            {
                "ok": False,
                "stage": "compose_up",
                "stdout": compose_result.stdout,
                "stderr": compose_result.stderr,
            },
            indent=2,
        ))
        return compose_result.returncode

    healthy, health_detail = wait_for_health(args.base_url, args.health_timeout_seconds)
    if not healthy:
        print(json.dumps(
            {
                "ok": False,
                "stage": "health_check",
                "detail": health_detail,
            },
            indent=2,
        ))
        return 2

    smoke_result_data: dict[str, object] | None = None
    smoke_exit_code = 0
    if not args.skip_smoke:
        smoke_cmd = [
            sys.executable,
            "scripts/security_smoke.py",
            "--base-url",
            args.base_url,
        ]
        if args.api_key:
            smoke_cmd.extend(["--api-key", args.api_key])
        smoke_result = run_command(smoke_cmd, workspace)
        smoke_exit_code = smoke_result.returncode
        try:
            smoke_result_data = json.loads(smoke_result.stdout)
        except json.JSONDecodeError:
            smoke_result_data = {
                "ok": False,
                "error": "Could not parse smoke output as JSON.",
                "stdout": smoke_result.stdout,
                "stderr": smoke_result.stderr,
            }

    summary = {
        "ok": healthy and (args.skip_smoke or smoke_exit_code == 0),
        "compose_up": "ok",
        "health_check": "ok",
        "smoke_check": "skipped" if args.skip_smoke else ("ok" if smoke_exit_code == 0 else "failed"),
        "smoke_result": smoke_result_data,
    }
    print(json.dumps(summary, indent=2, default=str))
    return 0 if summary["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
