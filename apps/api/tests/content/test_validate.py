"""Content validators (naadi_api/content/validate.py): the sample pack passes; each defect the S23 brief names is
caught (why-not, key consistency, lint and lab ranges, licence gate, originality in both modes)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from naadi_api.content.validate import FINGERPRINT_FILE, fingerprint, review_sample, shingles, validate
from naadi_api.seed.data import load_bundle
from naadi_api.settings import REPO_ROOT

from ..seed.content_fixture import FIXTURE_PACK, data_dir_with_sample_pack

CONFIG = REPO_ROOT / "config"


def _bundle(tmp: Path, edit: Callable[[dict[str, Any]], None] | None = None):  # type: ignore[no-untyped-def]
    data = data_dir_with_sample_pack(tmp)
    if edit is not None:
        pack = data / "content" / FIXTURE_PACK.name
        pack.unlink()
        shutil.copytree(FIXTURE_PACK, pack)
        doc = json.loads((pack / "questions.json").read_text(encoding="utf-8"))
        edit(doc)
        (pack / "questions.json").write_text(json.dumps(doc), encoding="utf-8")
    return load_bundle(data, CONFIG)


def _checks(report: Any, check: str) -> list[str]:
    return [f"{f.ref}: {f.detail}" for f in report.errors if f.check == check]


def test_sample_pack_passes_in_fallback_mode(tmp_path: Path) -> None:
    report = validate(_bundle(tmp_path), CONFIG)
    assert report.ok, [f.as_dict() for f in report.errors]
    assert report.originality_mode == "fallback"  # config/originality_fingerprints.txt ships without digests


def test_why_not_too_short_or_repeated(tmp_path: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        d["items"][0]["whyNot"]["A"] = "Wrong."
        d["items"][0]["whyNot"]["C"] = d["items"][0]["whyNot"]["D"]

    found = _checks(validate(_bundle(tmp_path, edit), CONFIG), "why_not")
    assert any("why-not A is under 4 words" in f for f in found)
    assert any("share the same why-not text" in f for f in found)


def test_key_consistency(tmp_path: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        d["items"][0]["explanationHtml"] = "<p>The correct answer is D because flow rises.</p>"
        d["items"][1]["options"][4]["html"] = d["items"][1]["options"][3]["html"]

    found = _checks(validate(_bundle(tmp_path, edit), CONFIG), "key")
    assert any("explanation names D but the key is B" in f for f in found)
    assert any("options share a text" in f for f in found)


def test_lint_catches_banned_options_and_implausible_labs(tmp_path: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        d["items"][0]["options"][4]["html"] = "All of the above"
        d["items"][3]["stemHtml"] = d["items"][3]["stemHtml"].replace(
            "Pulse is 92/min", "Pulse is 92/min. Serum sodium is 14000 mEq/L"
        )

    found = _checks(validate(_bundle(tmp_path, edit), CONFIG), "lint")
    assert any(f.startswith("item smp-q01") for f in found), found
    assert any(f.startswith("item smp-q04") and "L13" in f for f in found), found


def test_licence_gate_refuses_link_only_and_nd_sources_for_stored_text(tmp_path: Path) -> None:
    def edit(d: dict[str, Any]) -> None:
        d["items"][0]["source"] = "statpearls"
        d["items"][1]["source"] = "usmle-official-samples"

    found = _checks(validate(_bundle(tmp_path, edit), CONFIG), "licence")
    assert any("smp-q01" in f and "no_derivatives" in f for f in found), found
    assert any("smp-q02" in f and "link_only" in f for f in found), found


def test_originality_fallback_flags_a_copied_stem(tmp_path: Path) -> None:
    legacy = json.loads((REPO_ROOT / "data" / "questions.json").read_text(encoding="utf-8"))[0]["stemHtml"]

    def edit(d: dict[str, Any]) -> None:
        d["items"][0]["stemHtml"] = legacy

    found = _checks(validate(_bundle(tmp_path, edit), CONFIG), "originality")
    assert any("item smp-q01 stem" in f and "ch01-q01 stem" in f for f in found), found


def test_originality_fingerprints_mode(tmp_path: Path) -> None:
    (tmp_path / "b").mkdir()
    b = _bundle(tmp_path / "b")
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    stem = next(q for q in b.questions if q.id == "smp-q04").stem_html
    salt = "00ff"
    digests = sorted(fingerprint(s, salt) for s in shingles(stem))
    (config / FINGERPRINT_FILE).write_text(f"# salt: {salt}\n" + "\n".join(digests) + "\n", encoding="utf-8")
    report = validate(b, config)
    assert report.originality_mode == "fingerprints"
    assert any(f.ref == "item smp-q04 stem" and "forbidden" in f.detail for f in report.errors)


def test_review_sample_is_stratified_and_deterministic(tmp_path: Path) -> None:
    b = _bundle(tmp_path)
    first = review_sample(b)
    assert first == review_sample(b)
    assert set(first) <= {q.id for q in b.questions if q.pack_id}
    assert "smp-q01" in first  # the only step1 stratum member is always sampled
