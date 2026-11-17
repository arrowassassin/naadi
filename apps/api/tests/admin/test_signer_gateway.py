"""Signed URLs reach the browser on the gateway origin, never on a direct storage-api host the CSP doesn't list."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from pydantic import SecretStr

from naadi_api.chapters_media import MediaUrlResolver
from naadi_api.settings import Settings

GATEWAY = "http://localhost:54321"
DIRECT = "http://localhost:55435"


class _Bucket:
    def __init__(self, base: str):
        self.base = base

    async def create_signed_urls(self, paths: list[str], expires_in: int) -> list[dict[str, Any]]:
        # storage3 makes the signed URL absolute on its own base URL.
        return [{"path": p, "signedURL": f"{self.base}/object/sign/content-private/{p}?token=t"} for p in paths]


class _Client:
    def __init__(self, url: str, headers: dict[str, str]):
        self.url = url.rstrip("/")

    def from_(self, bucket: str) -> _Bucket:
        return _Bucket(self.url)


@pytest.mark.asyncio
async def test_direct_storage_urls_are_rebased_onto_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "storage3", types.SimpleNamespace(AsyncStorageClient=_Client))
    settings = Settings.model_construct(
        supabase_url=GATEWAY, storage_url=DIRECT, storage_service_key=SecretStr("k"), supabase_service_role_key=None
    )
    sign = MediaUrlResolver(settings)._signer_or_default()
    assert sign is not None
    out = await sign("content-private", ["staging/figures/fig-1.svg"], 60)
    url = out["staging/figures/fig-1.svg"]
    assert url == f"{GATEWAY}/storage/v1/object/sign/content-private/staging/figures/fig-1.svg?token=t"
    assert DIRECT not in url
