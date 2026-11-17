"""Local Storage service (stack.sh `NAADI_STORAGE_PORT`) for the seed importer's db tests.

stack.sh runs the real supabase/storage-api with a fixed, local-only JWT secret; a `service_role` key for it
is minted here (it is a throwaway for a local container, never a real secret). The tests talk to storage-api
directly with the official storage client (`storage3`, part of the `supabase` SDK), exactly as the importer
does with `NAADI_STORAGE_URL`.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from typing import Any

import httpx
import jwt
import pytest

# Same value as TEST_JWT_SECRET in stack.sh (local container only).
LOCAL_STORAGE_JWT_SECRET = "naadi-local-test-only-jwt-secret-0000"


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


def storage_client(url: str) -> Any:
    """The official storage client for the local storage-api, with a minted local service key."""
    from storage3 import SyncStorageClient

    key = local_service_key()
    return SyncStorageClient(url.rstrip("/") + "/", {"apikey": key, "Authorization": f"Bearer {key}"})


def remove_prefix(bucket: Any, prefix: str = "") -> int:
    """Remove every object under `prefix` (recursively) through the Storage API. Returns how many."""
    paths: list[str] = []
    pending = [prefix.strip("/")]
    while pending:
        folder = pending.pop()
        offset = 0
        while True:
            page = bucket.list(folder or None, {"limit": 1000, "offset": offset})
            for entry in page:
                path = f"{folder}/{entry['name']}" if folder else entry["name"]
                # storage-api lists a folder as an entry without an id
                (paths if entry.get("id") else pending).append(path)
            if len(page) < 1000:
                break
            offset += 1000
    for i in range(0, len(paths), 500):
        bucket.remove(paths[i : i + 500])
    return len(paths)


def empty_buckets(url: str, bucket_ids: Iterable[str]) -> int:
    """Empty the given buckets through the Storage API (deleting storage rows in SQL would orphan the files).

    Buckets that don't exist yet are skipped. Returns how many objects were removed."""
    client = storage_client(url)
    existing = {b.id for b in client.list_buckets()}
    return sum(remove_prefix(client.from_(b)) for b in bucket_ids if b in existing)
