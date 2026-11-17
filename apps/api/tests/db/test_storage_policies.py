"""Storage buckets and `storage.objects` policies (schema created by the real Supabase Storage service)."""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import errors

from .rls import DB_MARKS, as_anon, as_user

pytestmark = DB_MARKS

USER_BUCKETS = ("user-uploads", "vault", "generated-media")
# 0034: the vault is written by the API only (signed upload token / service role), never by a client policy.
CLIENT_WRITE_BUCKETS = ("user-uploads", "generated-media")
MB = 1024 * 1024


def _put(conn: psycopg.Connection, bucket: str, name: str, owner: uuid.UUID | None = None) -> None:
    conn.execute(
        "insert into storage.objects (bucket_id, name, owner_id) values (%s, %s, %s)",
        (bucket, name, str(owner) if owner else None),
    )


def _visible(conn: psycopg.Connection, bucket: str, name: str) -> bool:
    row = conn.execute(
        "select count(*) from storage.objects where bucket_id = %s and name = %s", (bucket, name)
    ).fetchone()
    return row == (1,)


def test_bucket_configuration(pg: psycopg.Connection) -> None:
    rows = pg.execute(
        "select id, public, file_size_limit, allowed_mime_types from storage.buckets where id = any(%s) order by id",
        (["content-public", "content-private", *USER_BUCKETS],),
    ).fetchall()
    cfg = {r[0]: r[1:] for r in rows}
    assert set(cfg) == {"content-public", "content-private", *USER_BUCKETS}
    assert cfg["content-public"][0] is True
    for b in ("content-private", *USER_BUCKETS):
        assert cfg[b][0] is False, b
    for b, (_, limit, mimes) in cfg.items():
        assert limit is not None and 0 < limit <= 50 * MB, b
        assert mimes, b
    assert "application/pdf" in cfg["vault"][2]
    assert "image/svg+xml" in cfg["content-public"][2]
    assert "image/svg+xml" not in cfg["user-uploads"][2]  # user SVGs could carry script
    for b, (_, _, mimes) in cfg.items():
        # a catch-all type would make the allowlist meaningless
        assert not {"application/octet-stream", "*/*", "application/*"} & set(mimes), b
    assert {"application/zip", "application/x-zip-compressed", "application/vnd.anki"} <= set(cfg["user-uploads"][2])


@pytest.mark.parametrize("bucket", CLIENT_WRITE_BUCKETS)
def test_user_folder_isolation(pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID, bucket: str) -> None:
    a_name = f"{user_a}/doc.pdf"
    # A's object, written as the backend would (the outer fixture transaction rolls it back at the end)
    with pg.transaction():
        pg.execute("set local role service_role")
        _put(pg, bucket, a_name, user_a)

    with as_user(pg, user_a) as conn:
        assert _visible(conn, bucket, a_name)
        _put(conn, bucket, f"{user_a}/second.pdf", user_a)
        assert (
            conn.execute(
                "update storage.objects set metadata = '{}' where bucket_id = %s and name = %s", (bucket, a_name)
            ).rowcount
            == 1
        )
        # cannot write outside the own folder, nor at the bucket root
        for name in (f"{user_b}/x.pdf", "root.pdf", f"x/{user_a}/y.pdf"):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                _put(conn, bucket, name, user_a)

    with as_user(pg, user_b) as conn:
        assert not _visible(conn, bucket, a_name), "B can read A's object"
        assert (
            conn.execute(
                "update storage.objects set metadata = '{}' where bucket_id = %s and name = %s", (bucket, a_name)
            ).rowcount
            == 0
        )
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _put(conn, bucket, f"{user_a}/planted.pdf", user_b)
        # moving one's own object into A's folder is refused by the update check
        _put(conn, bucket, f"{user_b}/mine.pdf", user_b)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(
                "update storage.objects set name = %s where bucket_id = %s and name = %s",
                (f"{user_a}/moved.pdf", bucket, f"{user_b}/mine.pdf"),
            )

    with as_anon(pg) as conn:
        assert not _visible(conn, bucket, a_name)
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            _put(conn, bucket, f"{user_a}/anon.pdf")


def test_user_folder_deletes(pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID) -> None:
    # Storage blocks direct SQL deletes unless this flag is set; the storage service sets it when it deletes on
    # a user's behalf, and RLS still decides which rows that user may delete.
    name = f"{user_a}/del.pdf"
    with pg.transaction():
        pg.execute("set local role service_role")
        _put(pg, "user-uploads", name, user_a)
    for uid, expected in ((user_b, 0), (user_a, 1)):
        with as_user(pg, uid) as conn:
            conn.execute("select set_config('storage.allow_delete_query', 'true', true)")
            cur = conn.execute("delete from storage.objects where bucket_id = 'user-uploads' and name = %s", (name,))
            assert cur.rowcount == expected, uid


def test_content_buckets(pg: psycopg.Connection, user_a: uuid.UUID) -> None:
    pub, priv = f"figures/{uuid.uuid4()}.svg", f"pdfs/{uuid.uuid4()}.pdf"
    with pg.transaction():
        pg.execute("set local role service_role")
        _put(pg, "content-public", pub)
        _put(pg, "content-private", priv)
    for ctx in (as_anon(pg), as_user(pg, user_a)):
        with ctx as conn:
            assert _visible(conn, "content-public", pub)
            assert not _visible(conn, "content-private", priv)
            for bucket in ("content-public", "content-private"):
                with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                    _put(conn, bucket, f"{user_a}/x.png")


def test_the_vault_bucket_is_read_only_for_clients(
    pg: psycopg.Connection, user_a: uuid.UUID, user_b: uuid.UUID
) -> None:
    """A student can read their own vault folder but cannot insert, overwrite or delete a vault object: every write
    goes through the API (signed upload token, byte check, document cap, service role)."""
    name = f"{user_a}/doc"
    with pg.transaction():
        pg.execute("set local role service_role")
        _put(pg, "vault", name, user_a)
    with as_user(pg, user_a) as conn:
        assert _visible(conn, "vault", name)
        for stmt_name in (f"{user_a}/untracked", f"{user_a}/doc2"):
            with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
                _put(conn, "vault", stmt_name, user_a)
        # an upsert overwrites through UPDATE: no row is updatable
        assert (
            conn.execute(
                "update storage.objects set metadata = '{}' where bucket_id = 'vault' and name = %s", (name,)
            ).rowcount
            == 0
        )
        conn.execute("select set_config('storage.allow_delete_query', 'true', true)")
        assert (
            conn.execute("delete from storage.objects where bucket_id = 'vault' and name = %s", (name,)).rowcount == 0
        )
    with as_user(pg, user_b) as conn:
        assert not _visible(conn, "vault", name)
    # the API's role still writes (signed uploads and the ingest hand-off use it)
    with pg.transaction():
        pg.execute("set local role service_role")
        _put(pg, "vault", f"{user_a}/by-api", user_a)
