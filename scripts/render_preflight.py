from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _is_truthy(value: str) -> bool:
    return value.strip().lower() == "true"


def _validate_dsn(errors: list[str], warnings: list[str]) -> None:
    dsn = _env("POSTGRES_DSN")
    if not dsn:
        errors.append("POSTGRES_DSN is required.")
        return

    if dsn.startswith("sqlite"):
        errors.append("POSTGRES_DSN cannot be sqlite for Render deployment.")
        return

    if dsn.startswith("postgresql+asyncpg://"):
        return

    if dsn.startswith("postgres://") or dsn.startswith("postgresql://"):
        warnings.append(
            "POSTGRES_DSN uses postgres:// or postgresql:// and will be normalized to postgresql+asyncpg:// at runtime."
        )
        return

    errors.append("POSTGRES_DSN must start with postgres://, postgresql://, or postgresql+asyncpg://.")


def _validate_api_base_url(errors: list[str]) -> None:
    base_url = _env("AGENTFORCE_API_BASE_URL")
    if not base_url:
        errors.append("AGENTFORCE_API_BASE_URL is required for the app service.")
        return

    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append("AGENTFORCE_API_BASE_URL must be a valid absolute http(s) URL.")


def _validate_api_keys(errors: list[str], role: str) -> None:
    auth_enabled = _is_truthy(_env("API_AUTH_ENABLED"))
    api_key = _env("API_KEY")
    api_admin_key = _env("API_ADMIN_KEY")
    api_readonly_key = _env("API_READONLY_KEY")

    if not auth_enabled:
        errors.append("API_AUTH_ENABLED must be true for Render deployment.")
        return

    if role == "api":
        if not (api_key or (api_admin_key and api_readonly_key)):
            errors.append("Set API_KEY, or both API_ADMIN_KEY and API_READONLY_KEY for the API service.")
        if not _env("EXPORT_SIGNING_SECRET"):
            errors.append("EXPORT_SIGNING_SECRET is required for signed exports in the API service.")
        return

    if role == "app" and not api_readonly_key:
        errors.append("API_READONLY_KEY is required in the app service when API auth is enabled.")


def _validate_shared(errors: list[str], warnings: list[str]) -> None:
    storage_backend = _env("STORAGE_BACKEND").lower()
    if storage_backend != "postgres":
        errors.append("STORAGE_BACKEND must be postgres for Render deployment.")
    _validate_dsn(errors, warnings)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate required Render environment variables before app startup.")
    parser.add_argument("--service", choices=["api", "app"], required=True, help="Service profile to validate")
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    _validate_shared(errors, warnings)
    _validate_api_keys(errors, args.service)

    if args.service == "app":
        _validate_api_base_url(errors)

    if warnings:
        for message in warnings:
            print(f"[preflight:warn] {message}")

    if errors:
        for message in errors:
            print(f"[preflight:error] {message}", file=sys.stderr)
        print(f"[preflight] failed with {len(errors)} error(s).", file=sys.stderr)
        return 1

    print(f"[preflight] {args.service} configuration looks valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
