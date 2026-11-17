"""Test-only: a stored upload, a vault document and a note-free student for the Export everything e2e (local only).

    uv run python -m tests.auth.e2e_export_files create --email naadi-e2e-<role>-<run>@example.com --db URL
    uv run python -m tests.auth.e2e_export_files delete --email ... --db URL

Needs NAADI_STORAGE_URL (the local storage-api) and uses the throwaway local service key (tests/db/storage.py).
Creates `user-uploads/{user}/e2e-upload.pdf`, `vault/{user}/e2e-vault.pdf` and one `vault_documents` row pointing at the
vault object. Only naadi-e2e-*@example.com users and loopback databases and storage hosts are accepted. Prints one
JSON line: {"upload": "...", "vault": "..."} (object names, no secrets).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlparse

import psycopg
from storage3 import SyncStorageClient

from tests.db.storage import local_service_key

TEST_EMAIL = re.compile(r"^naadi-e2e-[a-z]+-[a-z0-9-]{4,40}@example\.com$")
LOCAL = {"localhost", "127.0.0.1", "::1"}
UPLOAD_NAME = "e2e-upload.pdf"
VAULT_NAME = "e2e-vault.pdf"
TITLE = "E2E vault document"


def _client() -> Any:
    url = os.environ.get("NAADI_STORAGE_URL", "").rstrip("/")
    if not url or urlparse(url).hostname not in LOCAL:
        raise SystemExit("NAADI_STORAGE_URL must be the local storage-api")
    key = local_service_key()
    return SyncStorageClient(url + "/", {"apikey": key, "Authorization": f"Bearer {key}"})


def _user_id(conn: psycopg.Connection[Any], email: str) -> str:
    row = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
    if row is None:
        raise SystemExit("no such user")
    return str(row[0])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["create", "delete"])
    ap.add_argument("--email", required=True)
    ap.add_argument("--db", required=True)
    a = ap.parse_args(argv)
    if not TEST_EMAIL.match(a.email.strip().lower()):
        raise SystemExit("only naadi-e2e-<role>-<run>@example.com test addresses are allowed")
    if urlparse(a.db).hostname not in LOCAL:
        raise SystemExit("only a local database is accepted")
    storage = _client()
    with psycopg.connect(a.db, autocommit=True) as conn:
        uid = _user_id(conn, a.email.strip().lower())
        upload, vault = f"{uid}/{UPLOAD_NAME}", f"{uid}/{VAULT_NAME}"
        conn.execute("delete from public.vault_documents where user_id = %s and title = %s", (uid, TITLE))
        if a.action == "delete":
            for bucket, path in (("user-uploads", upload), ("vault", vault)):
                try:
                    storage.from_(bucket).remove([path])
                except Exception as exc:  # noqa: BLE001 - already gone is fine
                    print(type(exc).__name__, file=sys.stderr)
            return
        opts = {"content-type": "application/pdf", "upsert": "true"}
        storage.from_("user-uploads").upload(upload, b"%PDF-1.4 e2e uploaded file", opts)
        storage.from_("vault").upload(vault, b"%PDF-1.4 e2e vault document", opts)
        conn.execute(
            "insert into public.vault_documents (user_id, doc_type, title, storage_path) values (%s, 'other', %s, %s)",
            (uid, TITLE, vault),
        )
    print(json.dumps({"upload": UPLOAD_NAME, "vault": VAULT_NAME}))


if __name__ == "__main__":
    main()
