"""Tiny `.apkg` builders for the tests: nothing here is a real deck, every card is made up in code."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import zstandard
from PIL import Image

from naadi_api.anki import proto

SEP = "\x1f"
BASIC_Q, BASIC_A = "{{Front}}", "{{FrontSide}}<hr id=answer>{{Back}}"
REV_Q, REV_A = "{{Back}}", "{{FrontSide}}<hr id=answer>{{Front}}"
CLOZE_Q, CLOZE_A = "{{cloze:Text}}", "{{cloze:Text}}<br>{{Back Extra}}"


@dataclass
class NoteSpec:
    mid: int
    fields: list[str]
    guid: str
    tags: str = ""
    deck: int = 1
    cards: list[int] = field(default_factory=lambda: [0])


@dataclass
class Model:
    mid: int
    name: str
    fields: list[str]
    templates: list[tuple[str, str]]
    cloze: bool = False


BASIC = Model(1001, "Basic", ["Front", "Back"], [(BASIC_Q, BASIC_A)])
BASIC_REV = Model(1002, "Basic (and reversed card)", ["Front", "Back"], [(BASIC_Q, BASIC_A), (REV_Q, REV_A)])
CLOZE = Model(1003, "Cloze", ["Text", "Back Extra"], [(CLOZE_Q, CLOZE_A)], cloze=True)


def png(color: tuple[int, int, int] = (200, 30, 30), size: tuple[int, int] = (8, 6)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _legacy_db(path: Path, models: list[Model], decks: dict[int, str], notes: list[NoteSpec]) -> None:
    c = sqlite3.connect(path)
    c.executescript(
        """
        create table col (id integer primary key, crt integer, mod integer, scm integer, ver integer, dty integer,
          usn integer, ls integer, conf text, models text, decks text, dconf text, tags text);
        create table notes (id integer primary key, guid text, mid integer, mod integer, usn integer, tags text,
          flds text, sfld text, csum integer, flags integer, data text);
        create table cards (id integer primary key, nid integer, did integer, ord integer, mod integer, usn integer,
          type integer, queue integer, due integer, ivl integer, factor integer, reps integer, lapses integer,
          left integer, odue integer, odid integer, flags integer, data text);
        """
    )
    mj = {
        str(m.mid): {
            "name": m.name,
            "type": 1 if m.cloze else 0,
            "flds": [{"name": n, "ord": i} for i, n in enumerate(m.fields)],
            "tmpls": [
                {"name": f"Card {i + 1}", "ord": i, "qfmt": q, "afmt": a} for i, (q, a) in enumerate(m.templates)
            ],
        }
        for m in models
    }
    dj = {str(k): {"name": v} for k, v in decks.items()}
    c.execute("insert into col values (1,0,0,0,11,0,0,0,'{}',?,?,'{}','{}')", (json.dumps(mj), json.dumps(dj)))
    _rows(c, notes)
    c.commit()
    c.close()


def _rows(c: sqlite3.Connection, notes: list[NoteSpec]) -> None:
    cid = 1
    for nid, n in enumerate(notes, start=1):
        c.execute(
            "insert into notes values (?,?,?,0,0,?,?,?,0,0,'')",
            (nid, n.guid, n.mid, f" {n.tags} " if n.tags else "", SEP.join(n.fields), n.fields[0]),
        )
        for ord_ in n.cards:
            c.execute("insert into cards values (?,?,?,?,0,0,2,2,100,30,2500,5,1,0,0,0,0,'')", (cid, nid, n.deck, ord_))
            cid += 1


def _v18_db(path: Path, models: list[Model], decks: dict[int, str], notes: list[NoteSpec]) -> None:
    c = sqlite3.connect(path)
    c.executescript(
        """
        create table col (id integer primary key, crt integer, mod integer, scm integer, ver integer, dty integer,
          usn integer, ls integer, conf text, models text, decks text, dconf text, tags text);
        create table notetypes (id integer primary key, name text, mtime_secs integer, usn integer, config blob);
        create table fields (ntid integer, ord integer, name text, config blob, primary key (ntid, ord));
        create table templates (ntid integer, ord integer, name text, mtime_secs integer, usn integer, config blob,
          primary key (ntid, ord));
        create table decks (id integer primary key, name text, mtime_secs integer, usn integer, common blob, kind blob);
        create table notes (id integer primary key, guid text, mid integer, mod integer, usn integer, tags text,
          flds text, sfld text, csum integer, flags integer, data text);
        create table cards (id integer primary key, nid integer, did integer, ord integer, mod integer, usn integer,
          type integer, queue integer, due integer, ivl integer, factor integer, reps integer, lapses integer,
          left integer, odue integer, odid integer, flags integer, data text);
        """
    )
    c.execute("insert into col values (1,0,0,0,18,0,0,0,'','','','','')")
    for m in models:
        cfg = proto.encode_field(1, 1) if m.cloze else b""
        c.execute("insert into notetypes values (?,?,0,0,?)", (m.mid, m.name, cfg))
        for i, n in enumerate(m.fields):
            c.execute("insert into fields values (?,?,?,?)", (m.mid, i, n, b""))
        for i, (q, a) in enumerate(m.templates):
            c.execute(
                "insert into templates values (?,?,?,0,0,?)",
                (m.mid, i, f"Card {i + 1}", proto.encode_field(1, q) + proto.encode_field(2, a)),
            )
    for k, v in decks.items():
        c.execute("insert into decks values (?,?,0,0,x'',x'')", (k, v.replace("::", SEP)))
    _rows(c, notes)
    c.commit()
    c.close()


def build_apkg(
    kind: str,
    notes: list[NoteSpec],
    *,
    models: list[Model] | None = None,
    decks: dict[int, str] | None = None,
    media: dict[str, bytes] | None = None,
    manifest: str = "auto",  # auto | broken | missing
) -> bytes:
    """`kind`: anki2 | anki21 | anki21b."""
    models = models or [BASIC, BASIC_REV, CLOZE]
    decks = decks or {1: "Default", 2: "Biology::Cells"}
    media = media or {}
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "c.sqlite"
        (_v18_db if kind == "anki21b" else _legacy_db)(db, models, decks, notes)
        raw = db.read_bytes()
    zc = zstandard.ZstdCompressor()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if kind == "anki21b":
            z.writestr("collection.anki21b", zc.compress(raw))
            z.writestr("collection.anki2", b"dummy: please update")
            z.writestr("meta", proto.encode_field(1, 3))
            names = list(media)
            entries = b"".join(
                proto.encode_field(1, proto.encode_field(1, n) + proto.encode_field(2, len(media[n]))) for n in names
            )
            if manifest == "auto":
                z.writestr("media", zc.compress(entries))
            elif manifest == "broken":
                z.writestr("media", zc.compress(b"\xff\xff\xff\xff\xff"))
            for i, n in enumerate(names):
                z.writestr(str(i), zc.compress(media[n]))
        else:
            z.writestr("collection.anki21" if kind == "anki21" else "collection.anki2", raw)
            names = list(media)
            if manifest == "auto":
                z.writestr("media", json.dumps({str(i): n for i, n in enumerate(names)}))
            elif manifest == "broken":
                z.writestr("media", "{not json")
            for i, n in enumerate(names):
                z.writestr(str(i), media[n])
    return buf.getvalue()


def write_apkg(path: Path, *args: object, **kwargs: object) -> Path:
    path.write_bytes(build_apkg(*args, **kwargs))  # type: ignore[arg-type]
    return path
