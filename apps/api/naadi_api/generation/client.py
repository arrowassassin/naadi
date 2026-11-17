"""The generation client: the only code that talks to the `naadi-gen` Space.

`GenerationClient` is the seam the service uses (and tests replace). `GradioSpaceClient` is the real one, built on
the official `gradio_client` (no raw HTTP): it submits a job to the private ZeroGPU Space, polls the job's status
for progress (queued → generating) and returns the bytes. ZeroGPU bills nothing; when the owner's daily GPU quota
is spent the Space answers with a quota error, which becomes `QuotaExceeded` with the reset time. It is never
retried in a loop and never rerouted to paid inference.

The token is the fine-grained `HF_GEN_TOKEN` (access to that Space only), read through `settings.py`.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from ..logs import get_logger

log = get_logger("naadi.generation")

Kind = Literal["image", "video"]
Stage = Literal["queued", "generating"]
ProgressFn = Callable[[Stage, int | None], Awaitable[None]]

MAX_IMAGE_SIDE = 1024
MAX_VIDEO_SECONDS = 5
MAX_VIDEO_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
POLL_INTERVAL_S = 1.0
JOB_TIMEOUT_S = {"image": 120.0, "video": 300.0}
_WEBP = b"RIFF"
_MP4_BRAND = b"ftyp"


class GenerationClientError(Exception):
    """A failure with a stable `code` (never provider text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class QuotaExceeded(GenerationClientError):
    """ZeroGPU's own daily quota is spent. `resets_at` is parsed from the error, else the next 00:00 UTC."""

    def __init__(self, resets_at: datetime) -> None:
        super().__init__("generation_paused")
        self.resets_at = resets_at


@dataclass(frozen=True, slots=True)
class GenerationRequest:
    kind: Kind
    prompt: str
    seed: int
    width: int = 1024
    height: int = 1024
    seconds: int = MAX_VIDEO_SECONDS


@dataclass(frozen=True, slots=True)
class GenerationResult:
    data: bytes
    mime: str
    width: int
    height: int
    duration_s: float | None
    model: str


class GenerationClient(Protocol):
    async def generate(self, req: GenerationRequest, progress: ProgressFn) -> GenerationResult: ...


# ── quota errors ──

_TRY_AGAIN = re.compile(r"try again in\s+(\d{1,3}(?::\d{1,2}){1,2})", re.IGNORECASE)
_QUOTA_WORDS = (
    "exceeded your gpu quota",
    "gpu quota",
    "zerogpu quota",
    "quota exceeded",
    "daily quota",
    "out of quota",
)


def next_utc_midnight(now: datetime) -> datetime:
    now = now.astimezone(UTC)
    return datetime(now.year, now.month, now.day, tzinfo=UTC) + timedelta(days=1)


def is_quota_error(message: str) -> bool:
    low = message.lower()
    return any(w in low for w in _QUOTA_WORDS)


def parse_quota_reset(message: str, now: datetime) -> datetime:
    """Reset time from a ZeroGPU quota error ("Try again in 0:23:57" / "23:57"), else the next 00:00 UTC."""
    m = _TRY_AGAIN.search(message)
    if m:
        parts = [int(p) for p in m.group(1).split(":")]
        h, mi, sec = [0, *parts] if len(parts) == 2 else parts
        delta = timedelta(hours=h, minutes=mi, seconds=sec)
        if timedelta(0) < delta <= timedelta(hours=26):
            return now.astimezone(UTC) + delta
    return next_utc_midnight(now)


# ── result decoding ──


def _check_kind(kind: Kind, data: bytes) -> None:
    if kind == "image":
        if not (data[:4] == _WEBP and data[8:12] == b"WEBP"):
            raise GenerationClientError("generation_bad_result")
        if len(data) > MAX_IMAGE_BYTES:
            raise GenerationClientError("generation_bad_result")
    else:
        if data[4:8] != _MP4_BRAND or len(data) > MAX_VIDEO_BYTES:
            raise GenerationClientError("generation_bad_result")


def decode_result(kind: Kind, payload: Any, model: str) -> GenerationResult:
    """The Space returns `{mime, data_b64, width, height, duration_s?, seed}`; check it before anything is stored."""
    if not isinstance(payload, dict):
        raise GenerationClientError("generation_bad_result")
    raw = payload.get("data_b64")
    width, height = payload.get("width"), payload.get("height")
    if not isinstance(raw, str) or not isinstance(width, int) or not isinstance(height, int):
        raise GenerationClientError("generation_bad_result")
    if not (0 < width <= 1024 and 0 < height <= 1024):
        raise GenerationClientError("generation_bad_result")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GenerationClientError("generation_bad_result") from exc
    _check_kind(kind, data)
    mime = "image/webp" if kind == "image" else "video/mp4"
    if payload.get("mime") != mime:
        raise GenerationClientError("generation_bad_result")
    duration = payload.get("duration_s")
    if kind == "video":
        if not isinstance(duration, (int, float)) or not 0 < duration <= MAX_VIDEO_SECONDS + 0.5:
            raise GenerationClientError("generation_bad_result")
        duration = round(float(duration), 2)
    else:
        duration = None
    return GenerationResult(data=data, mime=mime, width=width, height=height, duration_s=duration, model=model)


# ── the real client ──


class GradioSpaceClient:
    """`gradio_client` against the private `naadi-gen` Space. The library is synchronous: calls run in a thread."""

    def __init__(
        self,
        *,
        space_id: str,
        token: str,
        models: dict[Kind, str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        client_factory: Callable[[str, str], Any] | None = None,
    ) -> None:
        self._space_id = space_id
        self._token = token
        self._models = models
        self._clock = clock
        self._factory = client_factory or self._default_factory
        self._client: Any = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _default_factory(space_id: str, token: str) -> Any:
        from gradio_client import Client

        # download_files=False: results are returned inline; analytics off: nothing about our use leaves.
        return Client(space_id, token=token, verbose=False, download_files=False, analytics_enabled=False)

    async def _get(self) -> Any:
        async with self._lock:
            if self._client is None:
                self._client = await asyncio.to_thread(self._factory, self._space_id, self._token)
            return self._client

    async def generate(self, req: GenerationRequest, progress: ProgressFn) -> GenerationResult:
        try:
            client = await self._get()
        except Exception as exc:
            self._client = None
            log.warning("generation_connect_failed", error=type(exc).__name__)
            raise GenerationClientError("generation_unavailable") from exc
        if req.kind == "image":
            kwargs: dict[str, Any] = {
                "prompt": req.prompt,
                "width": req.width,
                "height": req.height,
                "seed": req.seed,
                "api_name": "/image",
            }
        else:
            kwargs = {"prompt": req.prompt, "seconds": req.seconds, "seed": req.seed, "api_name": "/video"}
        job = await asyncio.to_thread(lambda: client.submit(**kwargs))
        timeout = JOB_TIMEOUT_S[req.kind]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        last: tuple[Stage, int | None] | None = None
        try:
            while not await asyncio.to_thread(job.done):
                if loop.time() > deadline:
                    await asyncio.to_thread(job.cancel)
                    raise GenerationClientError("generation_timeout")
                stage, rank = self._stage(await asyncio.to_thread(job.status))
                if (stage, rank) != last:
                    last = (stage, rank)
                    await progress(stage, rank)
                await asyncio.sleep(POLL_INTERVAL_S)
            payload = await asyncio.to_thread(job.result, 1)
        except GenerationClientError:
            raise
        except Exception as exc:
            message = str(exc)
            if is_quota_error(message):
                raise QuotaExceeded(parse_quota_reset(message, self._clock())) from exc
            log.warning("generation_failed", kind=req.kind, error=type(exc).__name__)
            raise GenerationClientError("generation_failed") from exc
        return decode_result(req.kind, payload, self._models[req.kind])

    @staticmethod
    def _stage(update: Any) -> tuple[Stage, int | None]:
        code = str(getattr(getattr(update, "code", None), "value", "")).upper()
        if code in {"STARTING", "JOINING_QUEUE", "IN_QUEUE", "QUEUE_FULL"}:
            rank = getattr(update, "rank", None)
            return "queued", rank if isinstance(rank, int) else None
        return "generating", None


class FakeGenerationClient:
    """Deterministic in-process client for tests and the non-production stub: tiny real WebP / MP4 bytes."""

    def __init__(self, *, fail_with: str | None = None, quota_reset: datetime | None = None) -> None:
        self.calls: list[GenerationRequest] = []
        self.fail_with = fail_with
        self.quota_reset = quota_reset

    async def generate(self, req: GenerationRequest, progress: ProgressFn) -> GenerationResult:
        self.calls.append(req)
        await progress("queued", 1)
        if "FAILME" in req.prompt:  # the e2e stub's way to ask for a failed generation
            raise GenerationClientError("generation_failed")
        if self.quota_reset is not None:
            raise QuotaExceeded(self.quota_reset)
        if self.fail_with is not None:
            raise GenerationClientError(self.fail_with)
        await progress("generating", None)
        if req.kind == "image":
            return GenerationResult(tiny_webp(), "image/webp", 64, 64, None, "fake-image")
        return GenerationResult(tiny_mp4(), "video/mp4", 64, 64, 1.0, "fake-video")


def tiny_webp(size: int = 64, rgb: tuple[int, int, int] = (110, 80, 200)) -> bytes:
    """A real WebP made at call time (no sample images in the repo)."""
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), rgb).save(buf, format="WEBP")
    return buf.getvalue()


def tiny_mp4() -> bytes:
    """A minimal ISO-BMFF `ftyp` box: enough for the container sniff; tests never play it."""
    return b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 32


__all__ = [
    "MAX_IMAGE_SIDE",
    "MAX_VIDEO_SECONDS",
    "FakeGenerationClient",
    "GenerationClient",
    "GenerationClientError",
    "GenerationRequest",
    "GenerationResult",
    "GradioSpaceClient",
    "QuotaExceeded",
    "decode_result",
    "is_quota_error",
    "next_utc_midnight",
    "parse_quota_reset",
    "tiny_mp4",
    "tiny_webp",
]
