"""Import preparation (no database): HTML to TipTap, pictures to WebP, audio dropped, duplicates, tags, decks."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest
from PIL import Image

from naadi_api.anki import images, proto
from naadi_api.anki.apkg import AnkiLimits
from naadi_api.anki.html import doc_text, doc_to_html, html_to_doc, referenced_images
from naadi_api.anki.importer import _prepare, clean_tags, deck_name

from .builders import BASIC, CLOZE, NoteSpec, png, write_apkg

UID = uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_html_is_reduced_to_the_strict_node_set() -> None:
    doc = html_to_doc(
        '<div class="x" onclick="evil()">Hello <b>bold</b> <i>it</i> H<sub>2</sub>O<script>alert(1)</script>'
        '<style>p{}</style><iframe src="https://evil"></iframe></div><ul><li>one</li><li>two<br>lines</li></ul>'
        "<h1>Head</h1><table><tr><th>a</th><td colspan=2>b</td></tr></table>"
    )
    kinds = [n["type"] for n in doc["content"]]
    assert kinds == ["paragraph", "bulletList", "heading", "table"]
    assert doc["content"][0]["content"][0] == {"type": "text", "text": "Hello "}
    assert {"type": "bold"} in doc["content"][0]["content"][1]["marks"]
    text = doc_text(doc)
    assert "alert" not in text and "evil" not in text and "H2O" in text
    assert doc["content"][2] == {
        "type": "heading",
        "attrs": {"level": 2},
        "content": [{"type": "text", "text": "Head"}],
    }
    row = doc["content"][3]["content"][0]["content"]
    assert [c["type"] for c in row] == ["tableHeader", "tableCell"] and row[1]["attrs"]["colspan"] == 2


def test_remote_and_inline_images_are_never_kept() -> None:
    from naadi_api.anki.html import Stats

    st = Stats()
    doc = html_to_doc(
        '<img src="https://t.example/p.gif"><img src="data:image/png;base64,AAAA"><img src="a.png">', lambda s: None, st
    )
    assert doc["content"] == [] and st.images_remote == 2 and st.images_missing == 1


def test_audio_and_sound_tags_are_dropped_and_counted() -> None:
    from naadi_api.anki.html import Stats

    st = Stats()
    doc = html_to_doc("Say it [sound:x.mp3]<audio src=y.mp3>fallback</audio> now", None, st)
    assert doc_text(doc) == "Say it now" and st.audio == 2


def test_doc_to_html_round_trips_text_and_marks() -> None:
    doc = html_to_doc("A <b>b</b> &amp; <i>i</i><br>next")
    again = html_to_doc(doc_to_html(doc))
    assert again == doc


def test_figure_nodes_use_the_resolver_and_leave_the_paragraph() -> None:
    doc = html_to_doc("before <img src='p.png'> after", lambda s: "m-1" if s == "p.png" else None)
    assert [n["type"] for n in doc["content"]] == ["paragraph", "figure", "paragraph"]
    assert doc["content"][1]["attrs"] == {"mediaId": "m-1"}
    assert doc_to_html(doc, lambda m: f"{m}.webp").count('<img src="m-1.webp">') == 1
    assert referenced_images("<img src='a%20b.png'><img src='https://x/y.png'>") == ["a%20b.png"]


def test_to_webp_bounds_size_and_refuses_junk() -> None:
    big = io.BytesIO()
    Image.new("RGB", (3000, 2000), (1, 2, 3)).save(big, format="PNG")
    w = images.to_webp(big.getvalue())
    assert w is not None and max(w.width, w.height) == images.MAX_SIDE and w.data[:4] == b"RIFF"
    assert images.to_webp(b"not an image") is None
    assert images.to_webp(b"<svg xmlns='http://www.w3.org/2000/svg'/>") is None


def test_deck_names_tags_and_ids() -> None:
    assert deck_name("Biology::Cells::Membrane", None) == "Biology / Cells / Membrane"
    assert deck_name("", "School") == "School / Imported"
    assert deck_name("A::B", "School") == "School / A / B"
    assert len(deck_name("x" * 200, None)) <= 60
    assert clean_tags(["a b", "a", "a", "x" * 99])[:2] == ["ab", "a"]
    assert images.media_id(str(UID), "ab" * 32).startswith("u111111111111-")


def test_prepare_builds_cards_pictures_and_counts(tmp_path: Path) -> None:
    img = png()
    notes = [
        NoteSpec(BASIC.mid, ['<img src="a.png"> what?', "yes [sound:s.mp3]"], "g1", "t1"),
        NoteSpec(BASIC.mid, ['<img src="a.png"> again', "same picture"], "g2"),
        NoteSpec(BASIC.mid, ['<img src="gone.png"> x', "y"], "g3"),
        NoteSpec(BASIC.mid, ["", "empty front"], "g4"),
        NoteSpec(BASIC.mid, ["dup", "a"], "g5"),
        NoteSpec(BASIC.mid, ["dup2", "a"], "g5"),
        NoteSpec(CLOZE.mid, ["{{c1::x}} y", ""], "g6"),
    ]
    p = write_apkg(tmp_path / "d.apkg", "anki21b", notes, media={"a.png": img})
    prep = _prepare(p, UID, None, AnkiLimits())
    guids = [c.guid for c in prep.cards]
    assert guids == ["g1", "g2", "g3", "g5", "g6:0"]  # empty front dropped, the duplicate guid dropped
    assert prep.skipped_cards == 2 and prep.notes == 6
    assert len(prep.pictures) == 1  # the same picture twice is stored once
    assert prep.images_skipped == 1 and prep.audio_skipped == 1 and prep.manifest == "ok"
    first = prep.cards[0]
    assert first.tags == ["t1"] and first.deck == "Default"
    assert first.front_doc["content"][0] == {"type": "figure", "attrs": {"mediaId": next(iter(prep.pictures))}}
    assert "sound" not in first.back_html


def test_unreadable_manifest_imports_cards_without_pictures(tmp_path: Path) -> None:
    p = write_apkg(
        tmp_path / "d.apkg",
        "anki2",
        [NoteSpec(BASIC.mid, ['<img src="a.png"> q', "a"], "g1")],
        media={"a.png": png()},
        manifest="broken",
    )
    prep = _prepare(p, UID, None, AnkiLimits())
    assert prep.manifest == "unreadable" and len(prep.cards) == 1 and prep.pictures == {} and prep.images_skipped == 1


def test_proto_reader_is_bounded_and_strict() -> None:
    blob = proto.encode_field(1, "hi") + proto.encode_field(2, 300)
    assert proto.first_string(blob, 1) == "hi" and proto.first_varint(blob, 2) == 300
    for bad in (b"\x0a\x05ab", b"\x08", b"\x00\x01"):
        with pytest.raises(proto.ProtoError):
            list(proto.fields(bad))
