"""GitHub Actions invariants: a deploy is never cancelled, and only the deploy workflow sees HF_TOKEN."""

from __future__ import annotations

from typing import Any

import yaml

from .conftest import REPO_ROOT

WF = REPO_ROOT / ".github" / "workflows"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((WF / name).read_text())
    # YAML 1.1 reads the bare key `on` as True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def test_ci_cancels_only_superseded_pull_request_runs() -> None:
    """Push runs (main, the multiplayer shared branch) always finish, so busy branches get complete results
    and the newest main commit reaches deploy.yml; only superseded PR runs are cancelled."""
    ci = _load("ci.yml")
    conc = ci["concurrency"]
    assert conc["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"
    assert "deploy" not in ci["jobs"]
    assert "secrets." not in (WF / "ci.yml").read_text()


def test_deploy_runs_after_green_ci_on_main_and_is_never_cancelled() -> None:
    dep = _load("deploy.yml")
    assert dep["on"]["workflow_run"] == {"workflows": ["ci"], "types": ["completed"], "branches": ["main"]}
    assert dep["concurrency"] == {"group": "deploy-space", "cancel-in-progress": False}
    job = dep["jobs"]["deploy"]
    assert "concurrency" not in job or job["concurrency"].get("cancel-in-progress") is False
    cond = " ".join(job["if"].split())
    for clause in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_branch == 'main'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert clause in cond
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["ref"] == "${{ github.event.workflow_run.head_sha || github.sha }}"


def test_live_playwright_run_records_no_headers() -> None:
    """Traces/videos/HTML reports record request headers (the HF token); the live run writes none."""
    cfg = (REPO_ROOT / "apps" / "web" / "playwright.config.ts").read_text()
    assert 'trace: liveBaseUrl ? "off" : "retain-on-failure"' in cfg
    assert 'video: "off"' in cfg
    live = cfg[cfg.index("if (liveBaseUrl) {") :]
    live = live[: live.index("}")]
    assert '"junit"' in live and '"html"' not in live
    assert "retries: 0," in cfg
