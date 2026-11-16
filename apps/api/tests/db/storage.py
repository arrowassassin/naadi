"""Local Storage service (stack.sh `NAADI_STORAGE_PORT`) for the seed importer's db tests.

stack.sh runs the real supabase/storage-api with a fixed, local-only JWT secret; a `service_role` key for it
is minted here (it is a throwaway for a local container, never a real secret). The tests talk to storage-api
directly with the official storage client (`storage3`, part of the `supabase` SDK), exactly as the importer
does with `NAADI_STORAGE_URL`.
"""

from __future__ import annotations

import os
import time

import httpx
import jwt
import pytest

# Same value as TEST_JWT_SECRET in stack.sh (local container only).
LOCAL_STORAGE_JWT_SECRET = "naadi-local-test-only-jwt-secret-0000"  # noqa: S105 - local test container secret


def local_service_key(secret: str = LOCAL_STORAGE_JWT_SECRET) -> str:
    now = int(time.time())
    claims = {"role": "service_role", "iss": "naadi-tests", "iat": now, "exp": now + 3600}
    return jwt.encode(claims, secret, algorithm="HS256")


def local_storage_url() -> str | None:
    """`NAADI_STORAGE_URL` if storage-api is published (stack.sh with NAADI_STORAGE_PORT), else None."""
    url = os.environ.get("NAADI_STORAGE_URL", "").strip().rstrip("/")
    if not url:
        port = os.environ.get("NAADI_STORAGE_PORT", "").strip()
        url = f"http://localhost:{port}" if port else ""
    return url or None


def require_local_storage() -> str:
    url = local_storage_url()
    if url is None:
        pytest.fail(
            "storage-api is not published: run tests/db/stack.sh with NAADI_STORAGE_PORT set (CI: stack.sh storage"
            " publishes it on the host network) and export NAADI_STORAGE_URL"
        )
    try:
        httpx.get(f"{url}/status", timeout=5).raise_for_status()
    except httpx.HTTPError as exc:
        pytest.fail(f"storage-api at {url} is not reachable: {type(exc).__name__}")
    return url
