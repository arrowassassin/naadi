"""Unit tests (no database): figure URL resolution, content scan, system membership, resume logic."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from pydantic import SecretStr, ValidationError

from naadi_api.chapters_content import scan_body
from naadi_api.chapters_media import SIGNED_URL_TTL_S, MediaUrlResolver, configured_buckets, parse_storage_path
from naadi_api.config_api.schemas import StorageBuckets
from naadi_api.courses import ReadingPositionIn, ReportIssueIn, chapter_systems, next_section, owned_attachments
from tests.conftest import make_settings

SUPA = "https://ref.supabase.co"
OWNER = uuid.UUID("11111111-1111-4111-8111-111111111111")


@pytest.mark.parametrize(
    ("raw", "ok"),
    [
        ("content-public/figures/a.svg", ("content-public", "figures/a.svg")),
        ("content-private/pdfs/x y.pdf", ("content-private", "pdfs/x y.pdf")),
        ("content-public", None),
        ("content-public/", None),
        ("/content-public/a.svg", None),
        ("content-public/../vault/a", None),
        ("content-public//a", None),
        ("https://example.org/a.png", None),
        ("content-public\\a.svg", None),
        ("", None),
    ],
)
def test_parse_storage_path(raw: str, ok: tuple[str, str] | None) -> None:
    sp = parse_storage_path(raw)
    assert (None if sp is None else (sp.bucket, sp.path)) == ok


class FakeSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], int]] = []
        self.fail = False

    async def __call__(self, bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
        self.calls.append((bucket, paths, expires_in))
        if self.fail:
            raise RuntimeError("storage down")
        return {p: f"{SUPA}/storage/v1/object/sign/{bucket}/{p}?token=t{len(self.calls)}" for p in paths}


def resolver(signer: FakeSigner, clock: list[float] | None = None, supabase_url: str | None = SUPA) -> MediaUrlResolver:
    c = clock or [1000.0]
    s = make_settings(supabase_url=supabase_url, supabase_service_role_key=SecretStr("k"))
    return MediaUrlResolver(s, signer=signer, clock=lambda: c[0])


async def test_public_private_external_and_refused_urls() -> None:
    signer = FakeSigner()
    r = resolver(signer)
    out = await r.resolve(
        [
            ("pub", "content-public/figures/ch01 fig#1.svg"),
            ("priv", "content-private/pdfs/a.pdf"),
            ("ext", "https://upload.wikimedia.org/x.png"),
            ("http", "http://example.org/x.png"),
            ("js", "javascript:alert(1)"),
            ("vault", "vault/passport.pdf"),
            ("mine", f"user-uploads/{OWNER}/a.png"),
            ("theirs", f"user-uploads/{uuid.uuid4()}/a.png"),
            ("none", None),
        ],
        OWNER,
    )
    assert out["pub"] == f"{SUPA}/storage/v1/object/public/content-public/figures/ch01%20fig%231.svg"
    assert out["priv"] is not None and "/object/sign/content-private/pdfs/a.pdf" in out["priv"]
    assert out["ext"] == "https://upload.wikimedia.org/x.png"
    assert out["mine"] is not None and "user-uploads" in out["mine"]
    for k in ("http", "js", "vault", "theirs", "none"):
        assert out[k] is None, k
    assert sorted(b for b, _, _ in signer.calls) == ["content-private", "user-uploads"]


async def test_signed_urls_are_cached_until_near_expiry() -> None:
    signer = FakeSigner()
    clock = [1000.0]
    r = resolver(signer, clock)
    first = await r.resolve([("a", "content-private/a.pdf")], OWNER)
    clock[0] += 1800  # half the TTL: still cached
    second = await r.resolve([("a", "content-private/a.pdf")], OWNER)
    assert first == second and len(signer.calls) == 1
    clock[0] += 1700  # under 10 minutes left: re-signed
    third = await r.resolve([("a", "content-private/a.pdf")], OWNER)
    assert third != first and len(signer.calls) == 2


async def test_signing_failure_and_missing_supabase_degrade_to_none() -> None:
    signer = FakeSigner()
    signer.fail = True
    out = await resolver(signer).resolve([("a", "content-private/a.pdf"), ("b", "content-public/b.svg")], OWNER)
    assert out == {"a": None, "b": f"{SUPA}/storage/v1/object/public/content-public/b.svg"}
    out = await resolver(FakeSigner(), supabase_url=None).resolve([("b", "content-public/b.svg")], OWNER)
    assert out == {"b": None}


def test_bucket_names_come_from_app_config() -> None:
    s = make_settings(supabase_url=SUPA, supabase_service_role_key=SecretStr("k"))
    b = configured_buckets(s)
    assert (b.content_public, b.content_private, b.user_uploads) == (
        "content-public",
        "content-private",
        "user-uploads",
    )


async def test_renamed_buckets_are_honoured_and_the_old_names_refused() -> None:
    signer = FakeSigner()
    s = make_settings(supabase_url=SUPA, supabase_service_role_key=SecretStr("k"))
    renamed = StorageBuckets(
        content_public="pub-x", content_private="priv-x", user_uploads="up-x", vault="vault-x", generated_media="gen-x"
    )
    r = MediaUrlResolver(s, signer=signer, buckets=renamed)
    out = await r.resolve(
        [
            ("pub", "pub-x/a.svg"),
            ("priv", "priv-x/a.pdf"),
            ("mine", f"up-x/{OWNER}/a.png"),
            ("theirs", f"up-x/{uuid.uuid4()}/a.png"),
            ("old", "content-public/a.svg"),
        ],
        OWNER,
    )
    assert out["pub"] == f"{SUPA}/storage/v1/object/public/pub-x/a.svg"
    assert out["priv"] is not None and out["mine"] is not None
    assert out["theirs"] is None and out["old"] is None


async def test_expired_entries_are_evicted() -> None:
    signer = FakeSigner()
    clock = [1000.0]
    r = resolver(signer, clock)
    await r.resolve([("a", "content-private/a.pdf"), ("b", "content-private/b.pdf")], OWNER)
    assert r.cached_count == 2
    clock[0] += SIGNED_URL_TTL_S + 1
    await r.resolve([("c", "content-private/c.pdf")], OWNER)
    assert r.cached_count == 1


async def test_concurrent_requests_share_one_sign_call_and_do_not_serialise() -> None:
    gate = asyncio.Event()
    calls: list[tuple[str, list[str]]] = []
    active = [0, 0]  # now, max

    async def slow(bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
        calls.append((bucket, paths))
        active[0] += 1
        active[1] = max(active[1], active[0])
        await gate.wait()
        active[0] -= 1
        return {p: f"{SUPA}/sign/{bucket}/{p}" for p in paths}

    s = make_settings(supabase_url=SUPA, supabase_service_role_key=SecretStr("k"))
    r = MediaUrlResolver(s, signer=slow)
    same = [asyncio.create_task(r.resolve([(f"m{i}", "content-private/a.pdf")], OWNER)) for i in range(5)]
    other = asyncio.create_task(r.resolve([("o", "content-private/b.pdf")], OWNER))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    # the second object's sign call started while the first is still waiting: no lock around the network call
    assert active[1] == 2
    gate.set()
    outs = await asyncio.gather(*same, other)
    assert [c for c in calls if c[1] == ["a.pdf"]] == [("content-private", ["a.pdf"])]
    assert {o[f"m{i}"] for i, o in enumerate(outs[:5])} == {f"{SUPA}/sign/content-private/a.pdf"}
    assert outs[5]["o"] == f"{SUPA}/sign/content-private/b.pdf"


async def test_a_cancelled_request_does_not_strand_the_others() -> None:
    gate = asyncio.Event()

    async def slow(bucket: str, paths: list[str], expires_in: int) -> dict[str, str]:
        await gate.wait()
        return {p: f"{SUPA}/sign/{p}" for p in paths}

    s = make_settings(supabase_url=SUPA, supabase_service_role_key=SecretStr("k"))
    r = MediaUrlResolver(s, signer=slow)
    owner_task = asyncio.create_task(r.resolve([("a", "content-private/a.pdf")], OWNER))
    await asyncio.sleep(0)
    waiter = asyncio.create_task(r.resolve([("b", "content-private/a.pdf")], OWNER))
    await asyncio.sleep(0)
    owner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner_task
    # the signing request was cancelled: the waiter gets None now (no hang) and the next request signs afresh
    assert (await asyncio.wait_for(waiter, 1.0)) == {"b": None}
    gate.set()
    assert (await r.resolve([("c", "content-private/a.pdf")], OWNER))["c"] == f"{SUPA}/sign/a.pdf"


def test_scan_collects_ids_and_unknown_types() -> None:
    body = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": "x", "marks": [{"type": "bold"}, {"type": "glow"}]}],
            },
            {"type": "figure", "attrs": {"mediaId": "f1"}},
            {"type": "figure", "attrs": {"mediaId": "f1"}},
            {"type": "video", "attrs": {"mediaId": "v1"}},
            {"type": "pdf", "attrs": {"mediaId": "p1", "page": 3}},
            {"type": "questionEmbed", "attrs": {"questionId": "q1"}},
            {"type": "callout", "attrs": {"variant": "pearl"}, "content": [{"type": "hologram"}]},
            "not a node",
            {"content": []},
        ],
    }
    s = scan_body(body)
    assert s.media_ids == ["f1", "v1", "p1"]
    assert s.question_ids == ["q1"]
    assert s.unknown_nodes == {"hologram", "<not-an-object>", "<missing-type>"}
    assert s.unknown_marks == {"glow"}
    assert not s.truncated


def test_scan_bounds_depth() -> None:
    node: dict[str, object] = {"type": "paragraph"}
    for _ in range(100):
        node = {"type": "callout", "content": [node]}
    assert scan_body({"type": "doc", "content": [node]}).truncated


def test_scan_ignores_non_objects() -> None:
    assert scan_body(None).media_ids == []
    assert scan_body([1, 2]).unknown_nodes == set()


@pytest.mark.parametrize(
    ("total", "per", "sections", "expected"),
    [
        (45, {"sys.renal": 29, "sys.cardio": 16, "sys.endo": 1}, set(), ["sys.renal", "sys.cardio"]),
        (10, {"sys.a": 3, "sys.b": 2}, set(), ["sys.a"]),  # minimum 3 questions
        (100, {"sys.a": 9, "sys.b": 10}, set(), ["sys.b"]),  # minimum 10 %
        (0, {}, {"sys.c"}, ["sys.c"]),  # section tags always count
        (5, {"sys.a": 5}, {"sys.b"}, ["sys.a", "sys.b"]),
    ],
)
def test_chapter_systems(total: int, per: dict[str, int], sections: set[str], expected: list[str]) -> None:
    assert chapter_systems(total, per, sections) == expected


@pytest.mark.parametrize(
    ("done", "last", "expected"),
    [
        (set(), None, "s1"),
        (set(), "s2", "s2"),
        ({"s2"}, "s2", "s3"),
        ({"s2", "s3"}, "s3", "s1"),  # wraps to the first unfinished section
        ({"s1", "s2", "s3"}, "s3", None),
        (set(), "gone", "s1"),
    ],
)
def test_next_section(done: set[str], last: str | None, expected: str | None) -> None:
    assert next_section(["s1", "s2", "s3"], done, last) == expected


def test_position_input_rejects_non_finite_and_rounds() -> None:
    ok = ReadingPositionIn(chapter_id="ch01", section_id="ch01-s01", offset=1.234567)
    assert ok.offset == 1.2346
    with pytest.raises(ValidationError):
        ReadingPositionIn(chapter_id="ch01", section_id="ch01-s01", offset=float("nan"))
    with pytest.raises(ValidationError):
        ReadingPositionIn(chapter_id="../x", section_id="ch01-s01", offset=0)


def test_report_body_is_trimmed_and_bounded() -> None:
    assert ReportIssueIn(ref_type="section", ref_id="ch01-s01", body="  typo ").body == "typo"
    with pytest.raises(ValidationError):
        ReportIssueIn(ref_type="section", ref_id="ch01-s01", body="x" * 4001)


def test_owned_attachments_lists_only_the_callers_visible_figures_per_section() -> None:
    other = uuid.UUID("22222222-2222-4222-8222-222222222222")
    rows = [
        {"section_id": "s1", "media_id": "shared-fig", "owner_user_id": None},
        {"section_id": "s1", "media_id": "mine-a", "owner_user_id": OWNER},
        {"section_id": "s1", "media_id": "theirs", "owner_user_id": other},
        {"section_id": "s2", "media_id": "mine-b", "owner_user_id": str(OWNER)},
        {"section_id": "s2", "media_id": "mine-gone", "owner_user_id": OWNER},
        {"section_id": "s1", "media_id": "mine-a", "owner_user_id": OWNER},
    ]
    visible = {"shared-fig", "mine-a", "theirs", "mine-b"}
    assert owned_attachments(rows, OWNER, visible) == {"s1": ["mine-a"], "s2": ["mine-b"]}
    assert owned_attachments(rows, other, visible) == {"s1": ["theirs"]}
    assert owned_attachments([], OWNER, visible) == {}
