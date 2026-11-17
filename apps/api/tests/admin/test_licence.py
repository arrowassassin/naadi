"""The publishing gate matrix (naadi_api.admin.licence.decide): pure, every reason."""

from __future__ import annotations

import datetime as dt

import pytest

from naadi_api.admin.licence import AssetFacts, PageFacts, SourceTerms, decide
from naadi_api.config_api.schemas import SourcesPolicy

NOW = dt.datetime(2026, 11, 17, tzinfo=dt.UTC)
POLICY = SourcesPolicy()


def src(license: str = "proprietary-naadi", **kw: bool) -> SourceTerms:
    flags = {"allow_copy": True, "allow_modify": True, "allow_commercial": True, "allow_ai_ingest": True, **kw}
    return SourceTerms(id="s", license=license, **flags)


YT = src("youtube-embed-only", allow_copy=False, allow_modify=False, allow_commercial=False, allow_ai_ingest=False)
FRESH = AssetFacts("video_youtube", True, NOW - dt.timedelta(days=1))


@pytest.mark.parametrize(
    ("source", "asset", "use", "page", "expected"),
    [
        (None, AssetFacts("svg"), "host", None, "no_source"),
        (src(""), AssetFacts("svg"), "host", None, "no_license"),
        (src("per-file"), AssetFacts("image"), "page", None, "no_license"),
        (src("unknown"), AssetFacts("pdf"), "host", None, "no_license"),
        (src("link-only", allow_copy=False), AssetFacts("pdf"), "host", None, "copy_not_allowed"),
        (src("link-only", allow_copy=False), AssetFacts("svg"), "page", None, "copy_not_allowed"),
        (src(), AssetFacts("svg"), "host", None, None),
        (src(), AssetFacts("svg"), "page", None, None),
        (src("CC-BY-NC-ND-4.0", allow_commercial=False, allow_modify=False), AssetFacts("image"), "page", PageFacts(paid=True), "noncommercial_on_paid"),
        (src("CC-BY-ND-4.0", allow_modify=False), AssetFacts("image"), "page", PageFacts(paid=True), "noncommercial_on_paid"),
        (src("CC-BY-NC-4.0", allow_commercial=False), AssetFacts("image"), "page", PageFacts(paid=False, proprietary=False), None),
        (src("CC-BY-SA-4.0"), AssetFacts("image"), "page", None, "share_alike_in_proprietary"),
        (src("CC-BY-NC-SA-3.0", allow_commercial=False), AssetFacts("image"), "page", None, "share_alike_in_proprietary"),
        (src("CC-BY-SA-4.0"), AssetFacts("image"), "page", PageFacts(proprietary=False), None),
        (src("CC-BY-SA-4.0"), AssetFacts("image"), "host", None, None),
        (src(allow_ai_ingest=False), AssetFacts("svg"), "rag", None, "no_ai_ingest"),
        (src(), AssetFacts("svg"), "rag", None, None),
        (YT, FRESH, "embed", None, None),
        (YT, FRESH, "page", None, None),
        (src(), FRESH, "embed", None, "no_license"),
        (YT, AssetFacts("video_youtube", False, NOW), "embed", None, "not_embeddable"),
        (YT, AssetFacts("video_youtube", None, NOW), "embed", None, "not_embeddable"),
        (YT, AssetFacts("video_youtube", True, NOW - dt.timedelta(days=31)), "page", None, "metadata_stale"),
        (YT, AssetFacts("video_youtube", True, None), "embed", None, "metadata_stale"),
    ],
)
def test_gate_matrix(source: SourceTerms | None, asset: AssetFacts, use: str, page: PageFacts | None, expected: str | None) -> None:
    assert decide(source, asset, use, POLICY, page=page, now=NOW) == expected  # type: ignore[arg-type]


def test_policy_switches_turn_soft_gates_off() -> None:
    off = SourcesPolicy(block_noncommercial_on_paid=False, block_share_alike_in_proprietary=False)
    nc = src("CC-BY-NC-SA-4.0", allow_commercial=False)
    assert decide(nc, AssetFacts("image"), "page", off, page=PageFacts(paid=True), now=NOW) is None
    # the licence and RAG gates are pinned on in the model itself
    with pytest.raises(ValueError):
        SourcesPolicy(block_without_license=False)  # type: ignore[arg-type]


def test_share_alike_detection() -> None:
    assert src("CC-BY-SA-4.0").share_alike and src("cc by-sa").share_alike
    assert not src("CC-BY-4.0").share_alike and not src("SAMPLE-licence").share_alike
