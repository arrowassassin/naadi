"""`.apkg` reader: the three container formats, note types, cloze, templates, media manifests and hostile input."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
import zstandard

from naadi_api.anki.apkg import AnkiLimits, ApkgError, Package, cloze_text
from naadi_api.anki.html import doc_text, html_to_doc

from .builders import BASIC, BASIC_REV, CLOZE, NoteSpec, build_apkg, png, write_apkg

KINDS = ["anki2", "anki21", "anki21b"]


def notes() -> list[NoteSpec]:
    return [
        NoteSpec(BASIC.mid, ["What is 2+2?", "Four"], "g-basic", "math easy", deck=2),
        NoteSpec(BASIC_REV.mid, ["cat", "gato"], "g-rev", "", deck=2, cards=[0, 1]),
        NoteSpec(
            CLOZE.mid,
            ["{{c1::Paris}} is the capital of {{c2::France::country}}", "Europe"],
            "g-cloze",
            deck=1,
            cards=[0, 1],
        ),
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_basic_reversed_and_cloze_cards_in_every_container_format(kind: str, tmp_path: Path) -> None:
    with Package(write_apkg(tmp_path / "d.apkg", kind, notes())) as pkg:
        parsed = pkg.parse()
    by = {c.guid: c for c in parsed.cards}
    assert parsed.notes == 3 and len(parsed.cards) == 5
    basic = by["g-basic"]
    assert (basic.deck, basic.kind, basic.tags) == ("Biology::Cells", "basic", ["math", "easy"])
    assert doc_text(html_to_doc(basic.front_html)) == "What is 2+2?"
    assert doc_text(html_to_doc(basic.back_html)) == "Four"  # the front is not repeated on the back
    rev = by["g-rev:1"]
    assert doc_text(html_to_doc(rev.front_html)) == "gato" and doc_text(html_to_doc(rev.back_html)) == "cat"
    c1, c2 = by["g-cloze:0"], by["g-cloze:1"]
    assert c1.kind == "cloze"
    assert doc_text(html_to_doc(c1.front_html)) == "[...] is the capital of France"
    assert doc_text(html_to_doc(c1.back_html)) == "Paris is the capital of France\nEurope"
    assert doc_text(html_to_doc(c2.front_html)) == "Paris is the capital of [country]"


def test_cloze_text_hides_only_the_asked_ordinal() -> None:
    t = "{{c1::a}} and {{c2::b::hint}}"
    assert cloze_text(t, 2, "q") == "a and <b>[hint]</b>"
    assert cloze_text(t, 2, "a") == "a and <b>b</b>"


def test_template_conditionals_and_extra_fields() -> None:
    from naadi_api.anki.apkg import Ctx, render

    ctx = Ctx({"Front": "q", "Back": "a", "Extra": ""}, "D::E", "T", ["x"], None)
    assert render("{{#Extra}}[{{Extra}}]{{/Extra}}{{^Extra}}none{{/Extra}}{{Front}}", ctx, "q", "") == "noneq"
    assert render("{{FrontSide}}<hr id=answer>{{Back}}{{Tags}}", ctx, "a", "q") == "ax"


@pytest.mark.parametrize("kind", KINDS)
def test_media_manifest_formats_resolve_pictures(kind: str, tmp_path: Path) -> None:
    img = png()
    n = [NoteSpec(BASIC.mid, ['<img src="cell.png">?', "ok"], "g1")]
    with Package(write_apkg(tmp_path / "d.apkg", kind, n, media={"cell.png": img})) as pkg:
        parsed = pkg.parse()
        assert parsed.media_state == "ok"
        assert pkg.read_media("cell.png") == img and pkg.read_media("CELL.PNG") == img
        assert pkg.read_media("other.png") is None


@pytest.mark.parametrize("kind", KINDS)
def test_unreadable_manifest_is_reported_not_fatal(kind: str, tmp_path: Path) -> None:
    n = [NoteSpec(BASIC.mid, ['<img src="cell.png">?', "ok"], "g1")]
    with Package(write_apkg(tmp_path / "d.apkg", kind, n, media={"cell.png": png()}, manifest="broken")) as pkg:
        parsed = pkg.parse()
        assert parsed.media_state == "unreadable" and len(parsed.cards) == 1
        assert pkg.read_media("cell.png") is None


def test_filtered_deck_cards_keep_their_home_deck(tmp_path: Path) -> None:
    import sqlite3

    raw = build_apkg("anki2", [NoteSpec(BASIC.mid, ["a", "b"], "g", deck=1)])
    p = tmp_path / "d.apkg"
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        db = z.read("collection.anki2")
    dbp = tmp_path / "c.db"
    dbp.write_bytes(db)
    c = sqlite3.connect(dbp)
    c.execute("update cards set did = 1, odid = 2")
    c.commit()
    c.close()
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("collection.anki2", dbp.read_bytes())
        z.writestr("media", "{}")
    with Package(p) as pkg:
        assert pkg.parse().cards[0].deck == "Biology::Cells"


@pytest.mark.parametrize(
    ("make", "code"),
    [
        (lambda: b"not a zip", "anki_not_a_package"),
        (lambda: _zip({"readme": b"x"}), "anki_no_collection"),
        (lambda: _zip({"collection.anki2": b"garbage" * 10}), "anki_corrupt"),
        (lambda: _zip({"../evil": b"x"}), "anki_unsafe_path"),
    ],
)
def test_bad_packages_have_stable_codes(make, code: str, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    p = tmp_path / "x.apkg"
    p.write_bytes(make())
    with pytest.raises(ApkgError) as e, Package(p) as pkg:
        pkg.parse()
    assert e.value.code == code


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def test_a_zip_bomb_stops_at_the_cap(tmp_path: Path) -> None:
    bomb = zstandard.ZstdCompressor().compress(b"\0" * (3 * 1024 * 1024))
    p = tmp_path / "x.apkg"
    p.write_bytes(_zip({"collection.anki21b": bomb}))
    with pytest.raises(ApkgError) as e, Package(p, AnkiLimits(max_collection_bytes=1024 * 1024)) as pkg:
        pkg.parse()
    assert e.value.code == "anki_too_large"


def test_a_hostile_sqlite_cannot_write(tmp_path: Path) -> None:
    import sqlite3

    raw = build_apkg("anki2", [NoteSpec(BASIC.mid, ["a", "b"], "g")])
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        db = bytearray(z.read("collection.anki2"))
    dbp = tmp_path / "c.db"
    dbp.write_bytes(bytes(db))
    c = sqlite3.connect(dbp)
    c.execute("create trigger t after insert on notes begin delete from cards; end")
    c.commit()
    c.close()
    p = tmp_path / "x.apkg"
    p.write_bytes(_zip({"collection.anki2": dbp.read_bytes(), "media": b"{}"}))
    with Package(p) as pkg:
        assert len(pkg.parse().cards) == 1  # reading never fires a trigger


def test_card_cap_counts_the_rest_as_skipped(tmp_path: Path) -> None:
    many = [NoteSpec(BASIC.mid, [f"q{i}", "a"], f"g{i}") for i in range(5)]
    with Package(write_apkg(tmp_path / "d.apkg", "anki2", many), AnkiLimits(max_cards=3)) as pkg:
        parsed = pkg.parse()
    assert len(parsed.cards) == 3 and parsed.skipped_cards == 2
