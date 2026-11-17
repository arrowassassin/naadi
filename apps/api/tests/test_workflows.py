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
    # Push runs are grouped per commit, so a newer push never replaces a queued run on a busy branch.
    assert conc["group"] == "ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.sha }}"
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


def _steps() -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for f in sorted(WF.glob("*.yml")):
        for job_name, job in _load(f.name)["jobs"].items():
            if "uses" in job:  # a reusable workflow call
                out.append((f.name, job_name, {"uses": job["uses"]}))
            for step in job.get("steps", []):
                out.append((f.name, job_name, step))
    return out


def test_every_action_is_pinned_by_commit_sha() -> None:
    """Segment 22: third-party actions run with the repo's token, so a moved tag must not change what runs."""
    import re

    sha = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
    loose = [
        f"{f}:{job}: {step['uses']}"
        for f, job, step in _steps()
        if "uses" in step and not step["uses"].startswith("./") and not sha.match(step["uses"])
    ]
    assert loose == []
    # The trailing comment names the release, so a reviewer can read what the SHA is.
    for f in sorted(WF.glob("*.yml")):
        for line in f.read_text().splitlines():
            if re.search(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40}", line):
                assert re.search(r"# v\d", line), f"{f.name}: {line.strip()} has no '# vX.Y.Z' comment"


def test_ci_runs_the_dependency_audit_and_uploads_sboms() -> None:
    audit = _load("ci.yml")["jobs"]["audit"]
    runs = "\n".join(s.get("run", "") for s in audit["steps"])
    assert "pnpm audit --prod --audit-level high" in runs
    assert "pip-audit --strict --require-hashes" in runs
    assert "--sbom-format cyclonedx" in runs and "cyclonedx-py requirements" in runs
    assert any(s.get("with", {}).get("name") == "sbom" for s in audit["steps"])
    # Every push, every branch.
    assert _load("ci.yml")["on"]["push"]["branches"] == ["**"]


def test_e2e_is_sharded_per_browser() -> None:
    e2e = _load("ci.yml")["jobs"]["e2e"]
    assert e2e["strategy"]["matrix"]["shard"] == [1, 2, 3]
    assert e2e["timeout-minutes"] <= 60
    run = next(s["run"] for s in e2e["steps"] if s.get("name", "").startswith("Build and run e2e"))
    assert '--shard "${{ matrix.shard }}/3"' in run
