"""The Space client with a fake `gradio_client`: progress, quota → paused with the reset time, bad results."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from naadi_api.generation.client import (
    FakeGenerationClient,
    GenerationClientError,
    GenerationRequest,
    GradioSpaceClient,
    QuotaExceeded,
    decode_result,
    is_quota_error,
    next_utc_midnight,
    parse_quota_reset,
    tiny_mp4,
    tiny_webp,
)

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


def payload(kind: str = "image", **over: Any) -> dict[str, Any]:
    data = tiny_webp() if kind == "image" else tiny_mp4()
    body: dict[str, Any] = {
        "mime": "image/webp" if kind == "image" else "video/mp4",
        "data_b64": base64.b64encode(data).decode(),
        "width": 64,
        "height": 64,
        "seed": 1,
    }
    if kind == "video":
        body["duration_s"] = 3.0
    body.update(over)
    return body


class FakeJob:
    def __init__(self, statuses: list[str], result: Any = None, error: Exception | None = None) -> None:
        self._statuses = statuses
        self._result = result
        self._error = error
        self.cancelled = False
        self.polls = 0

    def done(self) -> bool:
        return self.polls >= len(self._statuses)

    def status(self) -> Any:
        code = self._statuses[min(self.polls, len(self._statuses) - 1)]
        self.polls += 1
        return SimpleNamespace(code=SimpleNamespace(value=code), rank=2 if code == "IN_QUEUE" else None)

    def result(self, timeout: float | None = None) -> Any:
        if self._error:
            raise self._error
        return self._result

    def cancel(self) -> None:
        self.cancelled = True


class FakeGradio:
    def __init__(self, job: FakeJob) -> None:
        self.job = job
        self.calls: list[dict[str, Any]] = []

    def submit(self, **kwargs: Any) -> FakeJob:
        self.calls.append(kwargs)
        return self.job


def make(job: FakeJob) -> tuple[GradioSpaceClient, FakeGradio]:
    fake = FakeGradio(job)
    c = GradioSpaceClient(
        space_id="owner/space",
        token="hf_test_token_value",
        models={"image": "img-model", "video": "vid-model"},
        clock=lambda: NOW,
        client_factory=lambda _space, _token: fake,
    )
    return c, fake


async def test_image_job_reports_queue_then_generating_and_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("naadi_api.generation.client.POLL_INTERVAL_S", 0.0)
    job = FakeJob(["IN_QUEUE", "PROCESSING", "PROCESSING"], result=payload())
    c, fake = make(job)
    seen: list[tuple[str, int | None]] = []

    async def progress(stage: str, rank: int | None) -> None:
        seen.append((stage, rank))

    res = await c.generate(GenerationRequest("image", "heart", 7, 1024, 768), progress)
    assert seen == [("queued", 2), ("generating", None)]
    assert res.mime == "image/webp" and res.width == 64 and res.model == "img-model" and res.duration_s is None
    assert fake.calls == [{"prompt": "heart", "width": 1024, "height": 768, "seed": 7, "api_name": "/image"}]


async def test_video_job_uses_the_video_endpoint_and_checks_the_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("naadi_api.generation.client.POLL_INTERVAL_S", 0.0)
    c, fake = make(FakeJob(["PROCESSING"], result=payload("video")))

    async def progress(stage: str, rank: int | None) -> None: ...

    res = await c.generate(GenerationRequest("video", "valve", 1, seconds=4), progress)
    assert res.mime == "video/mp4" and res.duration_s == 3.0 and res.model == "vid-model"
    assert fake.calls == [{"prompt": "valve", "seconds": 4, "seed": 1, "api_name": "/video"}]


@pytest.mark.parametrize(
    "message",
    [
        "You have exceeded your GPU quota (75s requested vs. 60s left). Try again in 0:23:57",
        "ZeroGPU quota exceeded for this account",
    ],
)
async def test_quota_error_becomes_paused_with_a_reset_time(message: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("naadi_api.generation.client.POLL_INTERVAL_S", 0.0)
    c, _ = make(FakeJob(["PROCESSING"], error=RuntimeError(message)))

    async def progress(stage: str, rank: int | None) -> None: ...

    with pytest.raises(QuotaExceeded) as e:
        await c.generate(GenerationRequest("image", "x", 1), progress)
    assert e.value.code == "generation_paused"
    expected = datetime(2026, 11, 17, 10, 23, 57, tzinfo=UTC) if "Try again" in message else next_utc_midnight(NOW)
    assert e.value.resets_at == expected


async def test_other_errors_are_a_stable_code_and_never_leak_provider_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("naadi_api.generation.client.POLL_INTERVAL_S", 0.0)
    c, _ = make(FakeJob(["PROCESSING"], error=RuntimeError("secret detail hf_abc123")))

    async def progress(stage: str, rank: int | None) -> None: ...

    with pytest.raises(GenerationClientError) as e:
        await c.generate(GenerationRequest("image", "x", 1), progress)
    assert e.value.code == "generation_failed"
    assert "hf_abc123" not in str(e.value)


async def test_connect_failure_is_generation_unavailable() -> None:
    def boom(_space: str, _token: str) -> Any:
        raise OSError("space is asleep")

    c = GradioSpaceClient(space_id="o/s", token="t", models={"image": "a", "video": "b"}, client_factory=boom)

    async def progress(stage: str, rank: int | None) -> None: ...

    with pytest.raises(GenerationClientError) as e:
        await c.generate(GenerationRequest("image", "x", 1), progress)
    assert e.value.code == "generation_unavailable"


def test_parse_quota_reset_forms() -> None:
    assert parse_quota_reset("Try again in 1:02:03", NOW) == datetime(2026, 11, 17, 11, 2, 3, tzinfo=UTC)
    assert parse_quota_reset("try again in 12:30", NOW) == datetime(2026, 11, 17, 10, 12, 30, tzinfo=UTC)
    assert parse_quota_reset("quota gone", NOW) == datetime(2026, 11, 18, tzinfo=UTC)
    # an absurd wait falls back to the next UTC midnight, never a date years away
    assert parse_quota_reset("Try again in 999:00:00", NOW) == datetime(2026, 11, 18, tzinfo=UTC)
    assert is_quota_error("You have exceeded your GPU quota") and not is_quota_error("boom")


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        payload(width=2000),
        payload(mime="image/png"),
        payload(data_b64="!!notbase64!!"),
        payload(data_b64=base64.b64encode(b"not a webp at all").decode()),
    ],
)
def test_decode_result_refuses_anything_that_is_not_the_expected_media(bad: Any) -> None:
    with pytest.raises(GenerationClientError) as e:
        decode_result("image", bad, "m")
    assert e.value.code == "generation_bad_result"


def test_video_over_five_seconds_is_refused() -> None:
    with pytest.raises(GenerationClientError):
        decode_result("video", payload("video", duration_s=9.0), "m")


async def test_fake_client_makes_real_tiny_media() -> None:
    f = FakeGenerationClient()
    stages: list[str] = []

    async def progress(stage: str, rank: int | None) -> None:
        stages.append(stage)

    res = await f.generate(GenerationRequest("image", "x", 1), progress)
    assert res.data[:4] == b"RIFF" and res.data[8:12] == b"WEBP"
    assert stages == ["queued", "generating"]
