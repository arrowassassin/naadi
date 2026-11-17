"""Export the student's own cards as an Anki `.apkg` (genanki, MIT).

Only cards the student owns leave: their own (`user`), the assistant's (`assistant`) and ones they imported
(`anki_import`). Shared seed cards are never exported. Note GUIDs are stable (the card's `anki_guid`, else derived
from its id), so exporting twice and importing into the same Anki collection updates rather than duplicates, and
importing the file back into Naadi is a no-op. Scheduling is not exported (the file carries new cards), and no
audio exists to export. Pictures are bundled from the student's private storage.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from pathlib import Path
from typing import Any

import genanki

from ..db import Database
from . import images
from .db import AnkiError, service_tx
from .html import doc_to_html

# genanki needs numeric ids; both are fixed so Anki recognises our note type across exports.
MODEL_ID = 1_702_020_117
MODEL_NAME = "Naadi Basic"
EXPORT_ORIGINS = ("user", "assistant", "anki_import")
MAX_EXPORT_CARDS = 50_000

CSS = (
    ".card { font-family: Inter, Arial, sans-serif; font-size: 20px; text-align: left; color: #111; background: #fff; }"
    " img { max-width: 100%; height: auto; }"
)
MODEL = genanki.Model(
    MODEL_ID,
    MODEL_NAME,
    fields=[{"name": "Front"}, {"name": "Back"}],
    templates=[
        {
            "name": "Card 1",
            "qfmt": "{{Front}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Back}}",
        }
    ],
    css=CSS,
)


def deck_id(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:6], "big") + 1_000_000_000


def note_guid(card_id: uuid.UUID, anki_guid: str | None) -> str:
    return anki_guid or "n" + hashlib.sha256(str(card_id).encode()).hexdigest()[:20]


def anki_deck_name(name: str) -> str:
    """` / ` is Naadi's nesting, `::` is Anki's."""
    return "::".join(p.strip() for p in name.split(" / ") if p.strip()) or name


def anki_tag(tag: str) -> str:
    return "".join("_" if ch.isspace() else ch for ch in tag)


def slug(name: str) -> str:
    out = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
    while "--" in out:
        out = out.replace("--", "-")
    return out[:40] or "deck"


async def fetch_cards(db: Database, user_id: uuid.UUID, deck: str | None) -> list[dict[str, Any]]:
    async with service_tx(db) as conn:
        cur = await conn.execute(
            "select id, deck, front_html, back_html, front_doc, back_doc, tags, anki_guid from public.flashcards"
            " where owner_user_id = %(uid)s and origin = any(%(origins)s)"
            " and (%(deck)s::text is null or deck = %(deck)s or deck like %(deck)s || ' / %%')"
            " order by deck, created_at, id limit %(limit)s",
            {"uid": user_id, "origins": list(EXPORT_ORIGINS), "deck": deck, "limit": MAX_EXPORT_CARDS + 1},
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if len(rows) > MAX_EXPORT_CARDS:
            raise AnkiError(413, "anki_export_too_large")
        ids = sorted({n["attrs"]["mediaId"] for r in rows for k in ("front_doc", "back_doc") for n in _figs(r[k])})
        media: dict[str, str] = {}
        if ids:
            cur = await conn.execute(
                "select id, url from public.media_assets where owner_user_id = %s and id = any(%s)", (user_id, ids)
            )
            media = {r["id"]: r["url"] for r in await cur.fetchall() if r["url"]}
    for r in rows:
        r["_media"] = media
    return rows


def _figs(doc: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(n: object) -> None:
        if isinstance(n, dict):
            if n.get("type") == "figure":
                out.append(n)
            for c in n.get("content", []) or []:
                walk(c)

    walk(doc)
    return out


async def build_package(db: Database, store: images.ImageStore, user_id: uuid.UUID, deck: str | None) -> bytes:
    rows = await fetch_cards(db, user_id, deck)
    if not rows:
        raise AnkiError(404, "anki_no_cards_to_export")
    media_urls: dict[str, str] = rows[0]["_media"]
    # Pictures: fetched once per media id, written under a content-derived file name.
    files: dict[str, tuple[str, bytes]] = {}
    sem = asyncio.Semaphore(4)

    async def fetch(mid: str, url: str) -> None:
        bucket_prefix = "user-uploads/"
        if not url.startswith(bucket_prefix):
            return
        async with sem:
            try:
                data = await store.get(url[len(bucket_prefix) :])
            except Exception:  # noqa: BLE001 - a missing picture leaves its card without it
                return
        if data:
            files[mid] = (f"naadi-{hashlib.sha256(data).hexdigest()[:24]}.webp", data)

    await asyncio.gather(*(fetch(m, u) for m, u in media_urls.items()))

    def src(mid: str) -> str | None:
        f = files.get(mid)
        return f[0] if f else None

    decks: dict[str, genanki.Deck] = {}
    for r in rows:
        name = anki_deck_name(r["deck"])
        d = decks.setdefault(name, genanki.Deck(deck_id(name), name))
        front = doc_to_html(r["front_doc"], src) if r["front_doc"] else r["front_html"]
        back = doc_to_html(r["back_doc"], src) if r["back_doc"] else r["back_html"]
        d.add_note(
            genanki.Note(
                model=MODEL,
                fields=[front, back],
                tags=[anki_tag(t) for t in (r["tags"] or [])],
                guid=note_guid(r["id"], r["anki_guid"]),
            )
        )
    used = {n["attrs"]["mediaId"] for r in rows for k in ("front_doc", "back_doc") for n in _figs(r[k])}

    def write() -> bytes:
        with tempfile.TemporaryDirectory(prefix="naadi-export-") as tmp:
            paths: list[str] = []
            for mid in sorted(used):
                if mid in files:
                    name, data = files[mid]
                    p = Path(tmp) / name
                    p.write_bytes(data)
                    paths.append(str(p))
            pkg = genanki.Package(list(decks.values()))
            pkg.media_files = paths
            out = Path(tmp) / "out.apkg"
            pkg.write_to_file(str(out))
            return out.read_bytes()

    return await asyncio.to_thread(write)
