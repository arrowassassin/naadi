# /// script
# requires-python = ">=3.12"
# dependencies = ["huggingface_hub==2.4.0", "httpx==0.28.1"]
# ///
"""Deploy Naadi to its Docker Space and smoke-test it.

    uv run scripts/deploy_space.py [--space arrowassassin/naadi] [--hardware cpu-basic] [--dry-run]

1. Assembles a clean build context from git-tracked (and untracked, non-ignored) files only: root
   workspace files + lockfile, packages/, apps/web, apps/api (code, migrations + hash-locked requirements), config/,
   plus deploy/space/{Dockerfile,README.md,.dockerignore}. `.env*` can never be included, and the context is
   scanned for every secret value in the environment before anything is uploaded.
2. Requests the hardware (cpu-basic) if the Space is on something else, then uploads the context with
   huggingface_hub (the README card sets `sdk: docker`, `app_port: 7860`). Stale remote files are removed.
3. Waits until the new commit is RUNNING (fails on BUILD_ERROR / RUNTIME_ERROR / CONFIG_ERROR and prints
   the build or run log tail).
4. Smoke-tests GET /api/health (expects HTTP 200 and database ok) and GET / (the SPA) with the HF token.

Needs HF_TOKEN (write access to the Space). Prints names and statuses only, never values.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from huggingface_hub import HfApi
from huggingface_hub.errors import HfHubHTTPError

ROOT = Path(__file__).resolve().parent.parent
SPACE_DIR = ROOT / "deploy" / "space"

INCLUDE_FILES = {"package.json", "pnpm-lock.yaml", "pnpm-workspace.yaml", "tsconfig.base.json"}
INCLUDE_PREFIXES = ("packages/", "apps/web/", "apps/api/naadi_api/", "apps/api/migrations/", "config/", "data/")
INCLUDE_EXTRA = {"apps/api/requirements.txt", "apps/api/aegra.json"}
EXCLUDE_PARTS = ("/e2e/", "/__fixtures__/", "/test/", "__pycache__")
EXCLUDE_SUFFIXES = (".test.ts", ".test.tsx", ".pyc")
SECRET_ENV = (
    "HF_TOKEN",
    "DATABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_JWT_SECRET",
    "SUPABASE_ACCESS_TOKEN",
    "OPENROUTER_API_KEY",
    "NVIDIA_API_KEY",
    "TAVILY_API_KEY",
    "ADMIN_EMAILS",
    "YOUTUBE_API_KEY",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)
FAIL_STAGES = {"BUILD_ERROR", "RUNTIME_ERROR", "CONFIG_ERROR", "NO_APP_FILE"}


def die(msg: str) -> None:
    print(f"deploy: {msg}", file=sys.stderr)
    sys.exit(1)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout  # noqa: S603,S607 - fixed argv


def tracked_files() -> list[str]:
    out = git("ls-files", "-co", "--exclude-standard", "-z").split("\0")
    keep = []
    for f in out:
        if not f or not (ROOT / f).is_file():
            continue
        if Path(f).name.startswith(".env"):
            continue
        if not (f in INCLUDE_FILES or f in INCLUDE_EXTRA or f.startswith(INCLUDE_PREFIXES)):
            continue
        if any(p in f"/{f}" for p in EXCLUDE_PARTS) or f.endswith(EXCLUDE_SUFFIXES):
            continue
        keep.append(f)
    return sorted(keep)


def version_string() -> str:
    sha = git("rev-parse", "--short=12", "HEAD").strip()
    dirty = bool(git("status", "--porcelain", "--untracked-files=no").strip())
    return f"{sha}-dirty" if dirty else sha


def assemble(dest: Path) -> int:
    files = tracked_files()
    for f in files:
        target = dest / f
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, target)
    for name in ("Dockerfile", "README.md", ".dockerignore"):
        shutil.copy2(SPACE_DIR / name, dest / name)
    (dest / "apps" / "api" / "VERSION").write_text(version_string() + "\n", encoding="utf-8")
    return len(files) + 4


def scan_for_secrets(dest: Path) -> None:
    secrets = {n: os.environ[n] for n in SECRET_ENV if len(os.environ.get(n, "")) >= 8}
    for path in dest.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith(".env"):
            die(f"refusing to upload {path.relative_to(dest)}")
        try:
            data = path.read_bytes()
        except OSError:
            continue
        for name, value in secrets.items():
            if value.encode() in data:
                die(f"the value of {name} appears in {path.relative_to(dest)}; refusing to upload")


def space_host(api: HfApi, space: str) -> str:
    rt = api.get_space_runtime(space)
    for d in (rt.raw or {}).get("domains") or []:
        if d.get("domain"):
            return f"https://{d['domain']}"
    ns, name = space.split("/")
    return f"https://{ns}-{name}".lower().replace("_", "-").replace(".", "-") + ".hf.space"


def print_logs(space: str, build: bool) -> None:
    hf = shutil.which("hf")
    if not hf:
        return
    cmd = [hf, "spaces", "logs", space, "--tail", "120"] + (["--build"] if build else [])
    subprocess.run(cmd, check=False, timeout=60)  # noqa: S603 - fixed argv, resolved binary


BUILD_STAGES = {"BUILDING", "RUNNING_BUILDING", "APP_STARTING", "RUNNING_APP_STARTING"}


def wait_running(api: HfApi, space: str, commit: str, timeout_s: int) -> None:
    """Wait until *our* commit is RUNNING. A failure stage only counts once it belongs to our commit (the
    runtime reports its sha) or a build has started since the upload: right after the upload the runtime
    can still show the previous state (e.g. NO_APP_FILE on a fresh Space)."""
    start = time.monotonic()
    seen_build = False
    last = ""
    while time.monotonic() - start < timeout_s:
        rt = api.get_space_runtime(space)
        raw = rt.raw or {}
        stage = str(rt.stage)
        sha = raw.get("sha")
        ours = bool(sha) and sha == commit
        if stage != last:
            print(f"  runtime: {stage} (hardware {rt.hardware or '-'} requested {rt.requested_hardware or '-'})")
            last = stage
        if stage in BUILD_STAGES:
            seen_build = True
        if stage in FAIL_STAGES and (ours or seen_build):
            print_logs(space, build=stage in {"BUILD_ERROR", "CONFIG_ERROR"})
            die(f"Space stage {stage}")
        if stage == "RUNNING" and (ours or (not sha and seen_build)):
            return
        time.sleep(10)
    print_logs(space, build=True)
    die(f"timed out after {timeout_s}s waiting for RUNNING (last stage {last})")


def smoke(host: str, token: str, timeout_s: int = 300) -> None:
    headers = {"Authorization": f"Bearer {token}", "X-Request-ID": f"deploy-smoke-{int(time.time())}"}
    deadline = time.monotonic() + timeout_s
    body: dict[str, object] = {}
    status = 0
    with httpx.Client(timeout=30, headers=headers, follow_redirects=False) as http:
        while time.monotonic() < deadline:
            try:
                r = http.get(f"{host}/api/health")
                status = r.status_code
                if r.headers.get("content-type", "").startswith("application/json"):
                    body = r.json()
                if status == 200 and body.get("components"):
                    break
            except httpx.HTTPError as e:
                print(f"  /api/health: {type(e).__name__}")
            time.sleep(10)
        comps = body.get("components") or {}
        print(f"  /api/health: HTTP {status}, overall {body.get('status')}, version {body.get('version')}")
        for name, c in comps.items():  # type: ignore[union-attr]
            print(f"    {name:10s} {c.get('status'):9s} {c.get('latency_ms')} ms  {c.get('detail')}")
        if status != 200 or (comps.get("database") or {}).get("status") != "ok":  # type: ignore[union-attr]
            die("live /api/health is not healthy (database must be ok)")
        r = http.get(f"{host}/")
        ok = r.status_code == 200 and '<div id="root">' in r.text
        csp = r.headers.get("content-security-policy", "")
        shell = "found" if ok else "missing"
        print(f"  /: HTTP {r.status_code}, SPA shell {shell}, CSP {'present' if csp else 'MISSING'}")
        if not ok or "frame-ancestors" not in csp:
            die("live / did not serve the SPA with its CSP")
        ready = http.get(f"{host}/api/ready")
        print(f"  /api/ready: HTTP {ready.status_code} {ready.text.strip()[:120]}")
        if ready.status_code != 200:
            die("live /api/ready is not ready")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", default=os.environ.get("HF_SPACE_ID") or "arrowassassin/naadi")
    ap.add_argument("--hardware", default="cpu-basic")
    ap.add_argument("--timeout", type=int, default=1800, help="seconds to wait for RUNNING")
    ap.add_argument("--dry-run", action="store_true", help="assemble + scan the context, upload nothing")
    ap.add_argument("--smoke-only", action="store_true", help="skip the upload; smoke-test the live Space")
    args = ap.parse_args()

    token = os.environ.get("HF_TOKEN", "")
    if not token:
        die("HF_TOKEN is not set (GitHub: Settings → Secrets and variables → Actions → HF_TOKEN)")
    api = HfApi(token=token)

    if args.smoke_only:
        smoke(space_host(api, args.space), token)
        print("deploy: smoke OK")
        return 0

    with tempfile.TemporaryDirectory(prefix="naadi-space-") as tmp:
        ctx = Path(tmp)
        n = assemble(ctx)
        scan_for_secrets(ctx)
        print(f"deploy: build context has {n} files (version {version_string()})")
        if args.dry_run:
            for p in sorted(ctx.rglob("*")):
                if p.is_file():
                    print(f"  {p.relative_to(ctx)}")
            return 0

        try:
            rt = api.get_space_runtime(args.space)
        except HfHubHTTPError as e:
            die(f"cannot read Space {args.space}: HTTP {e.response.status_code if e.response else '?'}")
        if rt.requested_hardware != args.hardware:
            print(f"deploy: requesting {args.hardware} (was {rt.requested_hardware})")
            api.request_space_hardware(args.space, args.hardware)  # type: ignore[arg-type]

        info = api.upload_folder(
            repo_id=args.space,
            repo_type="space",
            folder_path=str(ctx),
            commit_message=f"deploy {version_string()}",
            delete_patterns=["*", "**/*"],  # remove files that are no longer in the context
        )
        commit = getattr(info, "oid", "") or ""
        print(f"deploy: uploaded commit {commit[:12]}")

    wait_running(api, args.space, commit, args.timeout)
    host = space_host(api, args.space)
    print(f"deploy: RUNNING at {host}")
    smoke(host, token)
    print("deploy: smoke OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
