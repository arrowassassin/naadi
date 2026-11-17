"""review19 defects 2-3: every row of config/sources.yaml against the publishing gates (table-driven).

Only storable licences publish stored content (files, question and chapter text): public domain, CC BY / BY-SA
(SA only in its own partition), Naadi originals. Link-only, embed-only, private, ND and (until the owner confirms
non-commercial use) NC sources never do, whatever their allow_copy flag says.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from naadi_api.admin.licence import AssetFacts, SourceTerms, decide, usable_for
from naadi_api.config_api.schemas import SourcesPolicy

ROOT = Path(__file__).resolve().parents[4]
SOURCES: list[dict[str, Any]] = yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8"))["sources"]
NOW = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)
POLICY = SourcesPolicy()

# id -> expected first refusal (None = allowed) for: host an SVG, put an SVG on a chapter page, write question or
# chapter text. A new source in sources.yaml must be added here deliberately.
EXPECTED: dict[str, tuple[str | None, str | None, str | None]] = {
    "naadi-original-v1": (None, None, None),
    "naadi-ai-generated": (None, None, None),
    "user-provided": ("private_source", "private_source", "private_source"),
    "youtube": ("embed_only", "embed_only", "embed_only"),
    "statpearls": ("no_derivatives", "no_derivatives", "no_derivatives"),
    "openstax": ("link_only", "link_only", "link_only"),  # its generative-AI clause (LINK_ONLY_SOURCES)
    "radiopaedia": ("noncommercial", "noncommercial", "noncommercial"),
    "medlineplus-federal": (None, None, None),
    "medlineplus-licensed": ("link_only", "link_only", "link_only"),
    "cdc-phil": ("no_license", "no_license", "no_license"),
    "uspstf": ("copy_not_allowed", "copy_not_allowed", "copy_not_allowed"),
    "cdc-guidelines": ("copy_not_allowed", "copy_not_allowed", "copy_not_allowed"),
    "ahrq": ("copy_not_allowed", "copy_not_allowed", "copy_not_allowed"),
    "wikimedia-commons": ("no_license", "no_license", "no_license"),
    "usmle-official-samples": ("link_only", "link_only", "link_only"),
    "nbme-lab-values": ("link_only", "link_only", "link_only"),
    "pathway-official-pages": ("link_only", "link_only", "link_only"),
    "pathway-third-party-pages": ("link_only", "link_only", "link_only"),
    "mesh": (None, None, "modify_not_allowed"),
    "rxnorm": (None, None, None),
}


def terms(row: dict[str, Any]) -> SourceTerms:
    return SourceTerms.of(row)


def test_every_source_in_sources_yaml_has_an_expectation() -> None:
    assert sorted(EXPECTED) == sorted(s["id"] for s in SOURCES)


@pytest.mark.parametrize("row", SOURCES, ids=lambda r: r["id"])
def test_gate_per_source(row: dict[str, Any]) -> None:
    host, page, text = EXPECTED[row["id"]]
    t = terms(row)
    assert decide(t, AssetFacts("svg"), "host", POLICY, now=NOW) == host
    assert decide(t, AssetFacts("pdf"), "host", POLICY, now=NOW) == host
    assert decide(t, AssetFacts("svg"), "page", POLICY, now=NOW) == page
    assert decide(t, AssetFacts("text"), "text", POLICY, now=NOW) == text
    # Raster uploads are re-encoded (a modified copy): never allowed where text is refused for modification.
    if text == "modify_not_allowed":
        assert decide(t, AssetFacts("image"), "host", POLICY, now=NOW) == "modify_not_allowed"
    embeddable = decide(t, AssetFacts("video_youtube", True, NOW), "embed", POLICY, now=NOW) is None
    assert embeddable == (row["id"] == "youtube")
    assert set(usable_for(t, POLICY)) == {
        u
        for u, ok in (("host", host is None), ("page", page is None), ("text", text is None), ("embed", embeddable))
        if ok
    }


@pytest.mark.parametrize("sid", ["statpearls", "usmle-official-samples", "nbme-lab-values", "medlineplus-licensed"])
def test_never_storable_whatever_the_flags_say(sid: str) -> None:
    """Link-only and NC-ND stay refused even if a row claims allow_copy/modify/commercial."""
    row = {
        **next(s for s in SOURCES if s["id"] == sid),
        "allow_copy": True,
        "allow_modify": True,
        "allow_commercial": True,
    }
    confirmed = SourcesPolicy(owner_confirms_noncommercial=True)
    for use, kind in (("host", "svg"), ("host", "pdf"), ("page", "image"), ("text", "text")):
        assert decide(terms(row), AssetFacts(kind), use, confirmed, now=NOW) is not None, (sid, use, kind)  # type: ignore[arg-type]


# The same table with `owner_confirms_noncommercial: true` (review19 R1): NC opens only where nothing else refuses.
# Share-alike never reaches question or chapter text (both proprietary), and OpenStax stays link-only (its
# generative-AI clause). Only host differs from the default for radiopaedia (SA files live in their own partition).
EXPECTED_NC_CONFIRMED: dict[str, tuple[str | None, str | None, str | None]] = {
    **EXPECTED,
    "radiopaedia": (None, "share_alike_in_proprietary", "share_alike_in_proprietary"),
}


@pytest.mark.parametrize("row", SOURCES, ids=lambda r: r["id"])
def test_gate_per_source_with_owner_confirmed_noncommercial(row: dict[str, Any]) -> None:
    confirmed = SourcesPolicy(owner_confirms_noncommercial=True)
    host, page, text = EXPECTED_NC_CONFIRMED[row["id"]]
    t = terms(row)
    assert decide(t, AssetFacts("svg"), "host", confirmed, now=NOW) == host
    assert decide(t, AssetFacts("svg"), "page", confirmed, now=NOW) == page
    assert decide(t, AssetFacts("text"), "text", confirmed, now=NOW) == text
    assert "text" not in usable_for(t, confirmed) or not t.share_alike


def test_owner_confirmed_noncommercial_never_opens_nd_sa_text_or_openstax() -> None:
    confirmed = SourcesPolicy(owner_confirms_noncommercial=True)
    by_id = {s["id"]: terms(s) for s in SOURCES}
    for use, kind in (("host", "svg"), ("page", "svg"), ("text", "text")):
        assert decide(by_id["openstax"], AssetFacts(kind), use, confirmed, now=NOW) == "link_only"  # type: ignore[arg-type]
    assert usable_for(by_id["openstax"], confirmed) == []
    assert usable_for(by_id["radiopaedia"], confirmed) == ["host"]
    sa = SourceTerms("x", "CC-BY-SA-4.0", True, True, True, False)
    assert decide(sa, AssetFacts("text"), "text", SourcesPolicy(), now=NOW) == "share_alike_in_proprietary"
    assert decide(by_id["statpearls"], AssetFacts("pdf"), "host", confirmed, now=NOW) == "no_derivatives"
