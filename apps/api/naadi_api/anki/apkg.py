"""Read an Anki `.apkg` into plain cards. Standard library `zipfile` + `sqlite3` + `zstandard`; no Anki code.

Formats (the package's own `meta` / member names say which):
- `collection.anki2` (legacy), `collection.anki21` (legacy, schema 11): SQLite, note types and decks as JSON in `col`.
- `collection.anki21b` (current): the same SQLite compressed with zstd, note types / fields / templates / decks as
  tables with protobuf `config` blobs. Its `media` member is a zstd protobuf manifest and the media files are zstd
  too; the legacy `media` member is JSON (`{"0": "name.png"}`) and its files are raw.

Safety: the package is untrusted. Member count and sizes are capped before and while reading (a zip bomb stops at
the cap), the SQLite file is written to a private temp directory and opened read-only with an authorizer that
allows reads only, and rendered HTML is cleaned by `html.py` afterwards (nothing here emits markup that is kept).
A card renders its note type's own templates (fields, `{{FrontSide}}`, conditionals, cloze) so Basic, reversed,
cloze and custom note types all come out as front and back HTML.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

import zstandard

from . import proto

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
SEP = "\x1f"
MediaState = Literal["ok", "unreadable", "none"]


class ApkgError(ValueError):
    """The file is not a readable Anki package. `code` is a stable machine code for the API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class AnkiLimits:
    max_collection_bytes: int = 400 * 1024 * 1024
    max_members: int = 200_000
    max_media_file_bytes: int = 10 * 1024 * 1024
    max_media_read_bytes: int = 400 * 1024 * 1024
    max_manifest_bytes: int = 64 * 1024 * 1024
    max_cards: int = 50_000
    max_face_chars: int = 20_000
    max_field_chars: int = 200_000  # one note field, before any rendering
    max_template_chars: int = 20_000
    max_cloze_depth: int = 32
    parse_seconds: float = 20.0  # wall-clock budget for everything SQLite does for one package


@dataclass(slots=True)
class RawCard:
    guid: str  # stable id of this card: the note's guid (card 0) or `guid:ord`
    note_guid: str
    ord: int
    deck: str  # Anki name; `::` separates levels
    kind: Literal["basic", "cloze", "other"]
    note_type: str
    front_html: str
    back_html: str
    tags: list[str]


@dataclass(slots=True)
class NoteType:
    name: str
    cloze: bool
    fields: list[str]
    templates: list[tuple[str, str]]  # (qfmt, afmt) by ordinal


@dataclass(slots=True)
class ParsedPackage:
    cards: list[RawCard] = field(default_factory=list)
    notes: int = 0
    skipped_cards: int = 0
    decks: dict[str, int] = field(default_factory=dict)
    media_state: MediaState = "none"
    media_names: dict[str, str] = field(default_factory=dict)  # lowercase name -> zip member


def _capped_read(stream: IO[bytes], cap: int, code: str) -> bytes:
    data = stream.read(cap + 1)
    if len(data) > cap:
        raise ApkgError(code)
    return data


def _unzstd(data: bytes, cap: int, code: str) -> bytes:
    try:
        reader = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data))
        out = reader.read(cap + 1)
    except zstandard.ZstdError as exc:
        raise ApkgError(code) from exc
    if len(out) > cap:
        raise ApkgError(code)
    return out


class Package:
    """An open `.apkg`: `parse()` the cards, `read_media(name)` a picture. Use as a context manager."""

    def __init__(self, path: Path, limits: AnkiLimits | None = None) -> None:
        self.limits = limits or AnkiLimits()
        try:
            self.zip = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as exc:
            raise ApkgError("anki_not_a_package") from exc
        infos = self.zip.infolist()
        if len(infos) > self.limits.max_members:
            self.zip.close()
            raise ApkgError("anki_too_many_files")
        self._members = {i.filename: i for i in infos if not i.is_dir()}
        for name in self._members:
            parts = name.replace("\\", "/").split("/")
            if name.startswith(("/", "\\")) or ".." in parts:
                self.zip.close()
                raise ApkgError("anki_unsafe_path")  # nothing is ever extracted by name, but refuse it anyway
        self.latest = False
        self._media: dict[str, str] = {}
        self.media_state: MediaState = "none"
        self._media_read = 0

    def __enter__(self) -> Package:
        return self

    def __exit__(self, *exc: object) -> None:
        self.zip.close()

    # ── collection ──
    def _member(self, name: str, cap: int, code: str) -> bytes | None:
        info = self._members.get(name)
        if info is None:
            return None
        if info.file_size > cap:
            raise ApkgError(code)
        with self.zip.open(info) as f:
            return _capped_read(f, cap, code)

    def _collection(self) -> bytes:
        cap = self.limits.max_collection_bytes
        for name, packed in (("collection.anki21b", True), ("collection.anki21", False), ("collection.anki2", False)):
            raw = self._member(name, cap, "anki_too_large")
            if raw is None:
                continue
            if packed or raw[:4] == ZSTD_MAGIC:
                self.latest = packed
                raw = _unzstd(raw, cap, "anki_too_large")
            if raw[:16] != b"SQLite format 3\x00":
                raise ApkgError("anki_corrupt")
            return raw
        raise ApkgError("anki_no_collection")

    def parse(self) -> ParsedPackage:
        raw = self._collection()
        out = ParsedPackage()
        with tempfile.TemporaryDirectory(prefix="naadi-anki-") as tmp:
            db_path = Path(tmp) / "collection.sqlite"
            db_path.write_bytes(raw)
            del raw
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                _lock_down(conn, self.limits.parse_seconds)
                try:
                    _require_plain_tables(conn)
                    self._read_cards(conn, out)
                except (sqlite3.DatabaseError, ValueError, KeyError, TypeError) as exc:
                    if isinstance(exc, ApkgError):
                        raise
                    raise ApkgError("anki_corrupt") from exc
            finally:
                conn.close()
        self._read_media_map(out)
        return out

    # ── cards ──
    def _read_cards(self, conn: sqlite3.Connection, out: ParsedPackage) -> None:
        types, decks = _note_types_and_decks(conn)
        rows = conn.execute(
            "select c.ord, case when c.odid != 0 then c.odid else c.did end, n.guid, n.mid, n.tags, n.flds"
            " from cards c join notes n on n.id = c.nid order by c.id"
        )
        seen_notes: set[str] = set()
        for ord_, did, guid, mid, tags, flds in rows:
            if len(out.cards) >= self.limits.max_cards:
                out.skipped_cards += 1
                continue
            nt = types.get(mid)
            if nt is None:
                out.skipped_cards += 1
                continue
            fields = str(flds or "").split(SEP)
            if any(len(f) > self.limits.max_field_chars for f in fields):
                out.skipped_cards += 1
                continue
            values = {name: (fields[i] if i < len(fields) else "") for i, name in enumerate(nt.fields)}
            ti = 0 if nt.cloze else int(ord_)
            if ti >= len(nt.templates):
                out.skipped_cards += 1
                continue
            deck = decks.get(did, "Default")
            qfmt, afmt = nt.templates[ti]
            if len(qfmt) > self.limits.max_template_chars or len(afmt) > self.limits.max_template_chars:
                out.skipped_cards += 1
                continue
            cl = int(ord_) + 1 if nt.cloze else None
            tag_list = [t for t in str(tags or "").split() if t]
            ctx = Ctx(values, deck, nt.name, tag_list, cl)
            front = render(qfmt, ctx, "q", "")
            back = render(afmt, ctx, "a", front)
            note_guid = str(guid or "") or hashlib.sha1(str(flds).encode()).hexdigest()[:16]  # noqa: S324
            if len(note_guid) > 180:
                note_guid = hashlib.sha1(note_guid.encode()).hexdigest()  # noqa: S324
            card_guid = note_guid if (not nt.cloze and int(ord_) == 0) else f"{note_guid}:{int(ord_)}"
            if len(front) > self.limits.max_face_chars or len(back) > self.limits.max_face_chars:
                out.skipped_cards += 1
                continue
            seen_notes.add(note_guid)
            out.cards.append(
                RawCard(
                    guid=card_guid,
                    note_guid=note_guid,
                    ord=int(ord_),
                    deck=deck,
                    kind="cloze" if nt.cloze else ("basic" if len(nt.templates) <= 2 else "other"),
                    note_type=nt.name,
                    front_html=front,
                    back_html=back,
                    tags=tag_list,
                )
            )
        out.notes = len(seen_notes)

    # ── media ──
    def _read_media_map(self, out: ParsedPackage) -> None:
        raw = self._member("media", self.limits.max_manifest_bytes, "anki_media_manifest")
        if raw is None:
            self.media_state = out.media_state = "none"
            return
        try:
            names = _parse_manifest(raw, self.limits.max_manifest_bytes)
        except (ApkgError, proto.ProtoError, ValueError):
            self.media_state = out.media_state = "unreadable"
            return
        self._media = {n.lower(): member for n, member in names.items()}
        out.media_names = dict(self._media)
        self.media_state = out.media_state = "ok"

    def read_media(self, name: str) -> bytes | None:
        """The bytes of a media file by its name in the cards, or None (unknown, too large, budget spent)."""
        member = self._media.get(name.lower())
        if member is None:
            return None
        info = self._members.get(member)
        cap = self.limits.max_media_file_bytes
        if (
            info is None
            or info.file_size > cap * 2
            or self._media_read + info.file_size > self.limits.max_media_read_bytes
        ):
            return None
        self._media_read += info.file_size
        try:
            with self.zip.open(info) as f:
                data = _capped_read(f, cap * 2, "anki_media_large")
            if data[:4] == ZSTD_MAGIC:
                data = _unzstd(data, cap, "anki_media_large")
        except (ApkgError, zipfile.BadZipFile, OSError, RuntimeError):
            return None
        return data if len(data) <= cap else None


def _parse_manifest(raw: bytes, cap: int) -> dict[str, str]:
    """name -> zip member, from the legacy JSON map or the zstd protobuf manifest."""
    if raw[:4] == ZSTD_MAGIC:
        body = _unzstd(raw, cap, "anki_media_manifest")
        names: dict[str, str] = {}
        index = 0
        for number, wire, value in proto.fields(body):
            if number == 1 and wire == 2 and isinstance(value, bytes):
                name = proto.first_string(value, 1)
                if name:
                    names[name] = str(index)
                index += 1  # an entry without a name still takes its slot: members are numbered by position
        return names
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ApkgError("anki_media_manifest")
    return {str(v): str(k) for k, v in data.items() if isinstance(v, str)}


def _unicase(a: str, b: str) -> int:
    a, b = a.casefold(), b.casefold()
    return (a > b) - (a < b)


def _lock_down(conn: sqlite3.Connection, deadline_s: float) -> None:
    """Reads only, no recursion and a wall-clock deadline: a crafted collection cannot hang a worker."""
    # Anki's own tables index names with its `unicase` collation; SQLite cannot plan a query without it.
    conn.create_collation("unicase", _unicase)

    def authorizer(action: int, *_: object) -> int:
        # No SQLITE_RECURSIVE: a view or query with a never-ending recursive CTE is refused at prepare time.
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    stop_at = time.monotonic() + deadline_s

    def progress() -> int:
        return 1 if time.monotonic() > stop_at else 0  # non-zero aborts the running statement

    conn.execute("pragma query_only = on")
    conn.execute("pragma trusted_schema = off")
    conn.set_authorizer(authorizer)
    conn.set_progress_handler(progress, 1000)


def _require_plain_tables(conn: sqlite3.Connection) -> None:
    """A real Anki collection has tables and indexes only. A view or a trigger can hide arbitrary queries behind a
    table name, so a package that has one is refused before any card is read."""
    if conn.execute("select count(*) from sqlite_master where type in ('view', 'trigger')").fetchone()[0]:
        raise ApkgError("anki_corrupt")
    have = {r[0] for r in conn.execute("select name from sqlite_master where type = 'table'")}
    if not {"notes", "cards"} <= have:
        raise ApkgError("anki_corrupt")


def _deck_name(raw: str) -> str:
    return raw.replace(SEP, "::")


def _note_types_and_decks(conn: sqlite3.Connection) -> tuple[dict[int, NoteType], dict[int, str]]:
    tables = {r[0] for r in conn.execute("select name from sqlite_master where type = 'table'")}
    types: dict[int, NoteType] = {}
    decks: dict[int, str] = {}
    if "notetypes" in tables and "templates" in tables:
        fields_by: dict[int, list[tuple[int, str]]] = {}
        for ntid, ord_, name in conn.execute("select ntid, ord, name from fields"):
            fields_by.setdefault(ntid, []).append((ord_, name))
        tmpl_by: dict[int, list[tuple[int, str, str]]] = {}
        for ntid, ord_, config in conn.execute("select ntid, ord, config from templates"):
            blob = bytes(config or b"")
            tmpl_by.setdefault(ntid, []).append((ord_, proto.first_string(blob, 1), proto.first_string(blob, 2)))
        for ntid, name, config in conn.execute("select id, name, config from notetypes"):
            kind = proto.first_varint(bytes(config or b""), 1, 0)
            types[ntid] = NoteType(
                name=name,
                cloze=kind == 1,
                fields=[n for _, n in sorted(fields_by.get(ntid, []))],
                templates=[(q, a) for _, q, a in sorted(tmpl_by.get(ntid, []))],
            )
        for did, name in conn.execute("select id, name from decks"):
            decks[did] = _deck_name(name)
        return types, decks
    row = conn.execute("select models, decks from col").fetchone()
    if row is None:
        raise ApkgError("anki_corrupt")
    for mid, m in json.loads(row[0] or "{}").items():
        types[int(mid)] = NoteType(
            name=str(m.get("name", "")),
            cloze=int(m.get("type", 0)) == 1,
            fields=[f["name"] for f in sorted(m.get("flds", []), key=lambda f: f.get("ord", 0))],
            templates=[
                (t.get("qfmt", ""), t.get("afmt", ""))
                for t in sorted(m.get("tmpls", []), key=lambda t: t.get("ord", 0))
            ],
        )
    for did, d in json.loads(row[1] or "{}").items():
        decks[int(did)] = _deck_name(str(d.get("name", "Default")))
    return types, decks


# ── template rendering ──


@dataclass(slots=True)
class Ctx:
    values: dict[str, str]
    deck: str
    note_type: str
    tags: list[str]
    cloze: int | None


_SECTION = re.compile(r"\{\{([#^])\s*([^{}]+?)\s*\}\}((?:(?!\{\{[#^]).)*?)\{\{/\s*\2\s*\}\}", re.DOTALL)
_TOKEN = re.compile(r"\{\{\s*([^{}#^/][^{}]*?)\s*\}\}")
_TAGS = re.compile(r"<[^>]*>")


_CLOZE_TOKEN = re.compile(r"\{\{c(\d{1,4})::|\}\}|::")


def cloze_text(text: str, ordinal: int | None, mode: str, max_depth: int = 32) -> str:
    """`{{c1::answer::hint}}` for card `ordinal`: the question hides it, the answer shows it in bold.

    One left-to-right pass over a fixed token set with an explicit stack (nesting deeper than `max_depth` and
    unclosed openers stay literal text): linear in the text, no backtracking, whatever a deck's author wrote.
    """
    # Frame: [cloze number, answer pieces, hint pieces or None]; frames[0] is the plain text around the clozes.
    frames: list[list[Any]] = [[0, [], None]]
    last = 0

    def put(chunk: str) -> None:
        if chunk:
            top = frames[-1]
            (top[2] if top[2] is not None else top[1]).append(chunk)

    for m in _CLOZE_TOKEN.finditer(text):
        tok = m.group(0)
        if tok == "::":
            if len(frames) > 1 and frames[-1][2] is None:  # the first `::` inside a cloze starts its hint
                put(text[last : m.start()])
                frames[-1][2] = []
                last = m.end()
        elif tok == "}}":
            if len(frames) > 1:
                put(text[last : m.start()])
                num, answer, hint = frames.pop()
                body = "".join(answer)
                if num != ordinal:
                    piece = body
                elif mode == "q":
                    hint_text = "".join(hint) if hint is not None else ""
                    piece = f"<b>[{hint_text}]</b>" if hint_text else "<b>[...]</b>"
                else:
                    piece = f"<b>{body}</b>"
                put(piece)
                last = m.end()
        elif len(frames) <= max_depth:
            put(text[last : m.start()])
            frames.append([int(m.group(1)), [], None])
            last = m.end()
    put(text[last:])
    while len(frames) > 1:  # never closed: its text is literal
        num, answer, hint = frames.pop()
        raw = f"{{{{c{num}::" + "".join(answer) + ("::" + "".join(hint) if hint is not None else "")
        put(raw)
    return "".join(frames[0][1])


def render(template: str, ctx: Ctx, mode: str, front_side: str) -> str:
    text = template
    for _ in range(8):  # sections can nest; each pass resolves the innermost
        new = _SECTION.sub(lambda m: _section(m, ctx, mode, front_side), text)
        if new == text:
            break
        text = new
    out = _TOKEN.sub(lambda m: _token(m.group(1), ctx, mode, front_side), text)
    return _prune_front(out) if mode == "a" else out


def _present(ctx: Ctx, name: str) -> bool:
    if name == "FrontSide":
        return True
    return bool(_TAGS.sub("", ctx.values.get(name, "")).replace("&nbsp;", " ").strip())


def _section(m: re.Match[str], ctx: Ctx, mode: str, front_side: str) -> str:
    kind, name, body = m.group(1), m.group(2).split(":")[-1], m.group(3)
    has = _present(ctx, name)
    return body if (kind == "#") == has else ""


def _token(raw: str, ctx: Ctx, mode: str, front_side: str) -> str:
    parts = [p.strip() for p in raw.split(":")]
    name, filters = parts[-1], parts[:-1]
    if name == "FrontSide":
        return "\x00FRONT\x00"
    if name == "Tags":
        return " ".join(ctx.tags)
    if name == "Deck":
        return ctx.deck
    if name == "Subdeck":
        return ctx.deck.rsplit("::", 1)[-1]
    if name == "Type":
        return ctx.note_type
    value = ctx.values.get(name, "")
    if "cloze" in filters:
        return cloze_text(value, ctx.cloze, mode)
    if "type" in filters or "hint" in filters:
        return "" if mode == "q" else value
    if "text" in filters:
        return _TAGS.sub("", value)
    return value


_FRONT_HR = re.compile(r"^\s*\x00FRONT\x00\s*(?:<hr[^>]*>)?", re.IGNORECASE)


def _prune_front(back: str) -> str:
    """The answer side repeats the question through `{{FrontSide}}`; keep only what the answer adds."""
    stripped = _FRONT_HR.sub("", back, count=1)
    return stripped.replace("\x00FRONT\x00", "").strip()
