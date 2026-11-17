"""The Space's endpoints validate before any GPU function runs (review): invalid input and a disabled video request
never ask ZeroGPU for a slot. gradio, spaces, torch and diffusers are replaced by stubs (CPU only)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

SPACE = Path(__file__).resolve().parents[4] / "deploy" / "gen-space"


class _Error(Exception):
    pass


def load_app(monkeypatch: pytest.MonkeyPatch, *, video: bool) -> tuple[types.ModuleType, list[str]]:
    gpu_calls: list[str] = []

    def gpu(*_a: Any, **_k: Any) -> Any:
        def deco(fn: Any) -> Any:
            def run(*args: Any, **kwargs: Any) -> Any:
                gpu_calls.append(fn.__name__)
                return fn(*args, **kwargs)

            run.__name__ = fn.__name__
            return run

        return deco

    class _Blocks:
        def __enter__(self) -> _Blocks:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    gr = types.ModuleType("gradio")
    gr.Error = _Error  # type: ignore[attr-defined]
    gr.Blocks = _Blocks  # type: ignore[attr-defined]
    gr.api = lambda *a, **k: None  # type: ignore[attr-defined]
    spaces = types.ModuleType("spaces")
    spaces.GPU = gpu  # type: ignore[attr-defined]
    torch = types.ModuleType("torch")
    torch.bfloat16 = "bf16"  # type: ignore[attr-defined]
    diffusers = types.ModuleType("diffusers")

    class _Pipe:
        @classmethod
        def from_pretrained(cls, *_a: Any, **_k: Any) -> _Pipe:
            return cls()

    diffusers.FluxPipeline = _Pipe  # type: ignore[attr-defined]
    diffusers.LTXPipeline = _Pipe  # type: ignore[attr-defined]
    for name, mod in {"gradio": gr, "spaces": spaces, "torch": torch, "diffusers": diffusers}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setenv("NAADI_GEN_VIDEO", "1" if video else "0")
    monkeypatch.syspath_prepend(str(SPACE))
    monkeypatch.delitem(sys.modules, "validation", raising=False)
    spec = importlib.util.spec_from_file_location("gen_space_app", SPACE / "app.py")
    assert spec and spec.loader
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    return app, gpu_calls


@pytest.mark.parametrize(
    ("args", "code"),
    [(("", 512, 512, 1), "prompt_empty"), (("x", 4096, 512, 1), "size_out_of_range")],
)
def test_invalid_image_input_is_refused_without_a_gpu_slot(
    monkeypatch: pytest.MonkeyPatch, args: tuple[Any, ...], code: str
) -> None:
    app, gpu_calls = load_app(monkeypatch, video=False)
    with pytest.raises(_Error, match=code):
        app.image(*args)
    assert gpu_calls == []


def test_invalid_or_disabled_video_never_reaches_the_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    app, gpu_calls = load_app(monkeypatch, video=False)
    with pytest.raises(_Error, match="video_disabled"):
        app.video("valve", 3, 1)
    app, gpu_calls = load_app(monkeypatch, video=True)
    app.video_pipe = object()  # pretend the model loaded
    with pytest.raises(_Error, match="seconds_out_of_range"):
        app.video("valve", 9, 1)
    assert gpu_calls == []


def test_valid_requests_do_reach_the_gpu_function_with_clean_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    app, gpu_calls = load_app(monkeypatch, video=False)
    monkeypatch.setattr(app, "_render_image", lambda *a: gpu_calls.append(f"render{a}") or b"RIFFxxxxWEBP")
    out = app.image("  a   heart ", 1024, 777, 5)
    assert gpu_calls == [("render('a heart', 1024, 768, 5)")] and out["height"] == 768 and out["mime"] == "image/webp"
