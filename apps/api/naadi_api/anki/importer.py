"""Import an Anki `.apkg` into the student's own flashcards (private, `origin 'anki_import'`).

Cards start as new in FSRS: Anki's own scheduling is never read, so no `card_reviews` row is written. A re-import is
idempotent by `(owner, anki_guid)`: cards already in the library are left as they are (reviews kept). Decks keep
their names (`::` becomes ` / `), tags stay, audio is dropped, pictures become private WebP. No network is used and
no model is called; the package's text goes nowhere but this student's rows.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from psycopg.types.json import Jsonb

from ..db import Database
from ..logs import get_logger
from . import images
from .apkg import AnkiLimits, ApkgError, Package, RawCard
from .db import MAX_CARDS_PER_USER, AnkiError, card_total, existing_guids, service_tx
from .html import Stats, doc_to_html, html_to_doc, referenced_images
from .models import ImportedDeck, ImportReport

log = get_logger("naadi.anki")

DECK_MAX = 60
MAX_IMAGES = 5000
MAX_IMAGE_BYTES_TOTAL = 150 * 1024 * 1024
MAX_TAGS = 20
MAX_TAG_CHARS = 40
UPLOAD_CONCURRENCY = 4
DEFAULT_DECK = "Imported"


def deck_name(raw: str, prefix: str | None) -> str:
    parts = [" ".join(p.split()) for p in raw.split("::")]
    name = " / ".join(p for p in parts if p) or DEFAULT_DECK
    if prefix:
        name = f"{prefix} / {name}"
    return name[:DECK_MAX].rstrip(" /") or DEFAULT_DECK


def clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = "".join(ch for ch in t if ch.isprintable() and not ch.isspace())[:MAX_TAG_CHARS]
        if t and t not in out:
            out.append(t)
        if len(out) >= MAX_TAGS:
            break
    return out


@dataclass(slots=True)
class _Prepared:
    guid: str
    deck: str
    note_type: str
    tags: list[str]
    front_html: str
    back_html: str
    front_doc: dict[str, object]
    back_doc: dict[str, object]


@dataclass(slots=True)
class _Picture:
    media_id: str
    path: str
    image: images.WebpImage


@dataclass(slots=True)
class Prepared:
    cards: list[_Prepared]
    pictures: dict[str, _Picture]
    notes: int
    skipped_cards: int
    images_skipped: int
    audio_skipped: int
    manifest: str


def _prepare(path: Path, user_id: uuid.UUID, deck_prefix: str | None, limits: AnkiLimits) -> Prepared:
    """CPU work (unzip, SQLite, Pillow, HTML cleaning): runs in a worker thread."""
    uid = str(user_id)
    with Package(path, limits) as pkg:
        parsed = pkg.parse()
        # Pictures first: every distinct name used by a card, read once, re-encoded, de-duplicated by content.
        wanted: list[str] = []
        seen: set[str] = set()
        for c in parsed.cards:
            for name in (*referenced_images(c.front_html), *referenced_images(c.back_html)):
                if name.lower() not in seen:
                    seen.add(name.lower())
                    wanted.append(name)
        by_name: dict[str, str | None] = {}
        pictures: dict[str, _Picture] = {}
        total = 0
        skipped = 0
        for name in wanted:
            if len(pictures) >= MAX_IMAGES or total > MAX_IMAGE_BYTES_TOTAL:
                by_name[name.lower()] = None
                skipped += 1
                continue
            raw = pkg.read_media(name)
            webp = images.to_webp(raw) if raw is not None else None
            if webp is None:
                by_name[name.lower()] = None
                skipped += 1
                continue
            mid = images.media_id(uid, webp.sha256)
            if mid not in pictures:
                total += len(webp.data)
                pictures[mid] = _Picture(mid, images.storage_path(uid, webp.sha256), webp)
            by_name[name.lower()] = mid

        stats = Stats()

        def resolve(src: str) -> str | None:
            return by_name.get(src.lower())

        cards: list[_Prepared] = []
        dropped = parsed.skipped_cards
        seen_guids: set[str] = set()
        for c in parsed.cards:
            if c.guid in seen_guids:
                dropped += 1
                continue
            seen_guids.add(c.guid)
            front = html_to_doc(c.front_html, resolve, stats)
            back = html_to_doc(c.back_html, resolve, stats)
            if not front["content"] or not back["content"]:
                if not front["content"]:
                    dropped += 1
                    continue
                back = {"type": "doc", "content": [{"type": "paragraph"}]}
            cards.append(_card(c, front, back, deck_prefix))
        # An image that no card kept (card dropped) is not stored.
        used = {n["attrs"]["mediaId"] for c in cards for d in (c.front_doc, c.back_doc) for n in _figures(d)}
        pictures = {k: v for k, v in pictures.items() if k in used}
        return Prepared(
            cards=cards,
            pictures=pictures,
            notes=parsed.notes,
            skipped_cards=dropped,
            images_skipped=skipped + stats.images_missing,
            audio_skipped=stats.audio,
            manifest=parsed.media_state,
        )


def _figures(doc: object) -> list[dict[str, dict[str, str]]]:
    out: list[dict[str, dict[str, str]]] = []

    def walk(n: object) -> None:
        if isinstance(n, dict):
            if n.get("type") == "figure":
                out.append(n)
            for c in n.get("content", []) or []:
                walk(c)

    walk(doc)
    return out


def _card(c: RawCard, front: dict[str, object], back: dict[str, object], prefix: str | None) -> _Prepared:
    return _Prepared(
        guid=c.guid,
        deck=deck_name(c.deck, prefix),
        note_type=c.note_type[:200],
        tags=clean_tags(c.tags),
        front_html=doc_to_html(front),
        back_html=doc_to_html(back),
        front_doc=front,
        back_doc=back,
    )


async def import_package(
    db: Database,
    store: images.ImageStore,
    user_id: uuid.UUID,
    path: Path,
    *,
    deck_prefix: str | None = None,
    limits: AnkiLimits | None = None,
) -> ImportReport:
    lim = limits or AnkiLimits()
    try:
        prep = await asyncio.to_thread(_prepare, path, user_id, deck_prefix, lim)
    except ApkgError as exc:
        raise AnkiError(422, exc.code) from exc
    except (MemoryError, RecursionError) as exc:
        raise AnkiError(422, "anki_too_large") from exc
    if not prep.cards:
        raise AnkiError(422, "anki_no_cards")

    # Pictures to Storage first (a card row must never point at a picture that is not there); each is content
    # addressed, so a failed import that is retried uploads the same objects again.
    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)

    async def put(p: _Picture) -> bool:
        async with sem:
            try:
                await store.put(p.path, p.image.data)
            except Exception as exc:  # noqa: BLE001 - one lost picture must not fail the deck
                log.warning("anki_image_upload_failed", error=type(exc).__name__)
                return False
            return True

    async with service_tx(db) as conn:
        cur = await conn.execute(
            "select id from public.media_assets where owner_user_id = %s and id = any(%s)",
            (user_id, list(prep.pictures)),
        )
        have = {r["id"] for r in await cur.fetchall()}
    todo = [p for p in prep.pictures.values() if p.media_id not in have]
    results = await asyncio.gather(*(put(p) for p in todo))
    failed = {p.media_id for p, ok in zip(todo, results, strict=True) if not ok}
    stored = {k for k in prep.pictures if k not in failed}

    async with service_tx(db) as conn:
        guids = [c.guid for c in prep.cards]
        existing = await existing_guids(conn, user_id, guids)
        fresh = [c for c in prep.cards if c.guid not in existing]
        if fresh and await card_total(conn, user_id) + len(fresh) > MAX_CARDS_PER_USER:
            raise AnkiError(413, "anki_card_cap")
        new_media = [prep.pictures[m] for m in stored if m not in have]
        for p in new_media:
            await conn.execute(
                "insert into public.media_assets (id, kind, source_id, url, owner_user_id, review_status,"
                " width, height, content_sha256) values (%s, 'image', 'user-upload', %s, %s, 'approved', %s, %s, %s)"
                " on conflict (id) do nothing",
                (
                    p.media_id,
                    f"user-uploads/{p.path}",
                    user_id,
                    p.image.width,
                    p.image.height,
                    p.image.sha256,
                ),
            )
        rows = []
        for c in fresh:
            front, back = c.front_doc, c.back_doc
            if failed:  # a picture that did not upload: its figure is removed, the card stays
                front, back = _strip_figures(front, failed), _strip_figures(back, failed)
            rows.append(
                (
                    user_id,
                    c.deck,
                    doc_to_html(front),
                    doc_to_html(back),
                    Jsonb(front),
                    Jsonb(back),
                    c.tags,
                    c.guid,
                    c.note_type,
                )
            )
        if rows:
            async with conn.cursor() as cur2:
                await cur2.executemany(
                    "insert into public.flashcards (owner_user_id, deck, front_html, back_html, front_doc, back_doc,"
                    " origin, tags, anki_guid, anki_note_type)"
                    " values (%s, %s, %s, %s, %s, %s, 'anki_import', %s, %s, %s)"
                    " on conflict (owner_user_id, anki_guid) where anki_guid is not null do nothing",
                    rows,
                )

    decks: dict[str, list[int]] = {}
    for c in prep.cards:
        d = decks.setdefault(c.deck, [0, 0])
        d[1 if c.guid in existing else 0] += 1
    return ImportReport(
        notes=prep.notes,
        cards_added=len(fresh),
        cards_existing=len(prep.cards) - len(fresh),
        cards_skipped=prep.skipped_cards,
        decks=[ImportedDeck(deck=k, added=v[0], existing=v[1]) for k, v in sorted(decks.items())],
        images_added=len(new_media),
        images_skipped=prep.images_skipped + len(failed),
        audio_skipped=prep.audio_skipped,
        media_manifest=prep.manifest,
    )


def _strip_figures(doc: dict[str, object], gone: set[str]) -> dict[str, object]:
    def keep(n: object) -> bool:
        return not (isinstance(n, dict) and n.get("type") == "figure" and n["attrs"]["mediaId"] in gone)

    def walk(n: dict[str, object]) -> dict[str, object]:
        kids = n.get("content")
        if isinstance(kids, list):
            n = {**n, "content": [walk(k) for k in kids if keep(k)]}
        return n

    out = walk(doc)
    if not out.get("content"):
        out["content"] = [{"type": "paragraph"}]
    return out
