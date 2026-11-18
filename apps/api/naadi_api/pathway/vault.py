"""The document vault: private files (passport, diploma, DS-2019 ...) kept in the `vault` bucket under
`{user_id}/{id}` and opened only through a short-lived signed URL.

PRIVACY (docs/AGENTS.md principle 4): this module and `vault_routes.py` never import `naadi_api.llm`, the search
module or the embeddings package, and nothing here logs a title, a path, a hash or a byte. The vault never reaches
a model, a search provider, an index, a log or the offline cache. `tests/pathway/test_vault_isolation.py` walks the
import graph to keep it that way.

Upload is two steps because the browser sends the bytes straight to Storage: the API creates the row and a signed
upload URL, then (`complete`) reads the stored object back once, checks from its first bytes that it is a PDF, JPEG,
PNG or HEIC, and records its size and SHA-256. A row without a hash is a pending upload: never listed, removed
after `PENDING_TTL` together with its object.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from typing import Any, Protocol

import psycopg

from ..config_api.schemas import PathwayRules
from . import rules
from .db import PathwayError
from .store import Conn, Row, fetch, fetch_one

BUCKET = "vault"
PENDING_TTL = dt.timedelta(hours=2)
HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
HEIF_BRANDS = frozenset({b"mif1", b"msf1"})

_DOC = (
    "id, title, coalesce(doc_type, 'other') as doc_type, mime, bytes, expires_on, created_at"
    " from public.vault_documents"
)


class VaultStorage(Protocol):
    """What the vault needs from Supabase Storage. The real one wraps the official `supabase` client."""

    async def create_signed_upload(self, path: str) -> tuple[str, str]: ...
    async def download(self, bucket: str, path: str) -> bytes | None: ...
    async def upload(self, path: str, data: bytes, content_type: str) -> None: ...
    async def signed_url(self, path: str, seconds: int) -> str: ...
    async def remove(self, bucket: str, path: str) -> None: ...


def sniff_mime(head: bytes) -> str | None:
    """The type of a file from its first bytes (never from its name or the declared type)."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(head) >= 12 and head[4:8] == b"ftyp" and head[8:12] in HEIC_BRANDS:
        return "image/heif" if head[8:12] in HEIF_BRANDS else "image/heic"
    return None


def object_path(user_id: uuid.UUID, doc_id: uuid.UUID) -> str:
    return f"{user_id}/{doc_id}"


def shape(row: Row, *, today: dt.date, soon_days: int) -> dict[str, Any]:
    days, state = rules.expiry_state(row["expires_on"], today, soon_days)
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "doc_type": row["doc_type"],
        "mime": row["mime"],
        "bytes": row["bytes"],
        "expires_on": row["expires_on"],
        "expires_in_days": days,
        "expiry": state,
        "created_at": row["created_at"],
    }


def check_type(doc_type: str, cfg: PathwayRules) -> None:
    if doc_type not in cfg.vault_types:
        raise PathwayError(422, "vault_type_unknown")


async def list_docs(conn: Conn, user_id: uuid.UUID) -> list[Row]:
    return await fetch(
        conn,
        f"select {_DOC} where user_id = %s and sha256 is not null order by created_at desc, id",
        (user_id,),
    )


async def get_doc(conn: Conn, user_id: uuid.UUID, doc_id: uuid.UUID) -> Row | None:
    return await fetch_one(
        conn, f"select {_DOC} where user_id = %s and id = %s and sha256 is not null", (user_id, doc_id)
    )


async def purge_pending(conn: Conn, storage: VaultStorage, user_id: uuid.UUID) -> None:
    """Remove this student's abandoned uploads (a row that never completed, and its object if one arrived)."""
    stale = await fetch(
        conn,
        "select id from public.vault_documents where user_id = %s and sha256 is null and created_at < %s",
        (user_id, dt.datetime.now(dt.UTC) - PENDING_TTL),
    )
    for r in stale:
        await storage.remove(BUCKET, object_path(user_id, r["id"]))
        await conn.execute("delete from public.vault_documents where user_id = %s and id = %s", (user_id, r["id"]))


async def begin_upload(
    conn: Conn,
    storage: VaultStorage,
    user_id: uuid.UUID,
    *,
    doc_id: uuid.UUID,
    title: str,
    doc_type: str,
    mime: str,
    size: int,
    expires_on: dt.date | None,
    cfg: PathwayRules,
) -> tuple[str, str]:
    """Create the pending row and the signed upload URL: `(signed_url, token)`."""
    check_type(doc_type, cfg)
    if size > cfg.vault_max_bytes:
        raise PathwayError(413, "vault_file_too_large")
    await purge_pending(conn, storage, user_id)
    n = await fetch_one(conn, "select count(*)::int as n from public.vault_documents where user_id = %s", (user_id,))
    if n and n["n"] >= cfg.vault_max_documents:
        raise PathwayError(409, "vault_limit")
    owner = await fetch_one(conn, "select user_id, sha256 from public.vault_documents where id = %s", (doc_id,))
    if owner is not None:
        # A retry of the same upload is fine until it completes; anything else is a clash.
        if owner["user_id"] != user_id or owner["sha256"] is not None:
            raise PathwayError(409, "vault_id_conflict")
        await conn.execute(
            "update public.vault_documents set title = %s, doc_type = %s, expires_on = %s where id = %s",
            (title, doc_type, expires_on, doc_id),
        )
    else:
        await conn.execute(
            "insert into public.vault_documents (id, user_id, doc_type, title, storage_path, expires_on)"
            " values (%s, %s, %s, %s, %s, %s)",
            (doc_id, user_id, doc_type, title, object_path(user_id, doc_id), expires_on),
        )
    return await storage.create_signed_upload(object_path(user_id, doc_id))


async def complete_upload(
    conn: Conn, storage: VaultStorage, user_id: uuid.UUID, doc_id: uuid.UUID, cfg: PathwayRules
) -> Row:
    """Read the uploaded object back, check it, and record `bytes`, `mime` and `sha256`. A file that is not a PDF,
    JPEG, PNG or HEIC, or is too large, raises (the route then deletes the file and its row)."""
    row = await fetch_one(
        conn, "select id, sha256 from public.vault_documents where user_id = %s and id = %s", (user_id, doc_id)
    )
    if row is None:
        raise PathwayError(404, "vault_not_found")
    if row["sha256"] is not None:
        done = await get_doc(conn, user_id, doc_id)
        assert done is not None
        return done
    data = await storage.download(BUCKET, object_path(user_id, doc_id))
    if data is None:
        raise PathwayError(409, "vault_upload_missing")
    mime = sniff_mime(data[:16])
    if mime is None or len(data) == 0 or len(data) > cfg.vault_max_bytes:
        # The caller deletes the object and the row (outside this transaction, which the error rolls back).
        raise PathwayError(415 if mime is None else 413, "vault_file_type" if mime is None else "vault_file_too_large")
    await conn.execute(
        "update public.vault_documents set bytes = %s, mime = %s, sha256 = %s where user_id = %s and id = %s",
        (len(data), mime, hashlib.sha256(data).hexdigest(), user_id, doc_id),
    )
    done = await get_doc(conn, user_id, doc_id)
    assert done is not None
    return done


async def adopt_object(
    conn: Conn,
    storage: VaultStorage,
    cfg: PathwayRules,
    user_id: uuid.UUID,
    *,
    bucket: str,
    path: str,
    title: str,
    doc_type: str = "other",
    expires_on: dt.date | None = None,
) -> Row:
    """Hand-off from ingest's sensitivity gate: move an object the student already uploaded (e.g. under
    `user-uploads/{user_id}/...`) into the vault. The bytes pass through this process only: no model, no OCR, no
    index. The source object is deleted after the copy is recorded."""
    check_type(doc_type, cfg)
    if not path.startswith(f"{user_id}/"):
        raise PathwayError(403, "vault_source_not_yours")
    data = await storage.download(bucket, path)
    if data is None:
        raise PathwayError(404, "vault_not_found")
    mime = sniff_mime(data[:16])
    if mime is None:
        raise PathwayError(415, "vault_file_type")
    if len(data) > cfg.vault_max_bytes:
        raise PathwayError(413, "vault_file_too_large")
    n = await fetch_one(conn, "select count(*)::int as n from public.vault_documents where user_id = %s", (user_id,))
    if n and n["n"] >= cfg.vault_max_documents:
        raise PathwayError(409, "vault_limit")
    doc_id = uuid.uuid4()
    await storage.upload(object_path(user_id, doc_id), data, mime)
    try:
        await conn.execute(
            "insert into public.vault_documents (id, user_id, doc_type, title, storage_path, expires_on, bytes, mime,"
            " sha256) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                doc_id,
                user_id,
                doc_type,
                title,
                object_path(user_id, doc_id),
                expires_on,
                len(data),
                mime,
                hashlib.sha256(data).hexdigest(),
            ),
        )
    except psycopg.Error:
        await storage.remove(BUCKET, object_path(user_id, doc_id))
        raise
    await storage.remove(bucket, path)
    done = await get_doc(conn, user_id, doc_id)
    assert done is not None
    return done


async def delete_doc(conn: Conn, storage: VaultStorage, user_id: uuid.UUID, doc_id: uuid.UUID) -> bool:
    """Object first, then the row: a failed object removal keeps the row so the student can try again."""
    row = await fetch_one(
        conn, "select id from public.vault_documents where user_id = %s and id = %s", (user_id, doc_id)
    )
    if row is None:
        return False
    await storage.remove(BUCKET, object_path(user_id, doc_id))
    await conn.execute("delete from public.vault_documents where user_id = %s and id = %s", (user_id, doc_id))
    return True
