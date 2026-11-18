#!/usr/bin/env bash
# Upload deploy/player-space to the static Space that serves the YouTube player page on its own origin.
#   HF_TOKEN=... scripts/deploy_player_space.sh [--space ns/name] [--dry-run]
# The Space (default arrowassassin/naadi-player, SDK static) is created once by the owner: this script never
# creates it and never touches hardware or settings. Prints the page URL; with --verify it fetches it and checks
# that the page is the one just uploaded and carries its own CSP.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SPACE="${PLAYER_SPACE_ID:-arrowassassin/naadi-player}"
DRY=0
VERIFY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --space) SPACE="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --verify) VERIFY=1; shift ;;
    *) echo "usage: $0 [--space ns/name] [--dry-run] [--verify]" >&2; exit 2 ;;
  esac
done
[[ -n "${HF_TOKEN:-}" || "$DRY" == 1 ]] || { echo "deploy-player: HF_TOKEN is not set" >&2; exit 1; }
command -v uv >/dev/null || { echo "deploy-player: uv is required" >&2; exit 1; }
SRC="$ROOT/deploy/player-space"
grep -q '^  var ALLOW_LOOPBACK = false;$' "$SRC/player.js" || { echo "deploy-player: player.js must ship with ALLOW_LOOPBACK = false" >&2; exit 1; }
for f in README.md index.html player.js; do [[ -f "$SRC/$f" ]] || { echo "deploy-player: missing $SRC/$f" >&2; exit 1; }; done
if [[ "$DRY" == 1 ]]; then
  echo "deploy-player: would upload $SRC to $SPACE ($(ls "$SRC" | tr '\n' ' '))"
  exit 0
fi
exec uv run --with "huggingface_hub==2.4.0" python - "$SPACE" "$SRC" "$VERIFY" <<'PY'
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.errors import RepositoryNotFoundError

space, src, verify = sys.argv[1], Path(sys.argv[2]), sys.argv[3] == "1"
api = HfApi(token=os.environ["HF_TOKEN"])
try:
    api.space_info(space)
except RepositoryNotFoundError:
    sys.exit(f"deploy-player: {space} does not exist; the owner creates it once (SDK: static)")
info = api.upload_folder(
    repo_id=space,
    repo_type="space",
    folder_path=str(src),
    commit_message="deploy player page",
    allow_patterns=["README.md", "index.html", "player.js"],
)
ns, name = space.split("/")
host = f"https://{ns}-{name}".lower().replace("_", "-").replace(".", "-") + ".static.hf.space"
print(f"deploy-player: uploaded {getattr(info, 'oid', '')[:7]} -> {host}/")
if verify:
    import time

    want = hashlib.sha256((src / "player.js").read_bytes()).hexdigest()
    for _ in range(30):
        try:
            with urllib.request.urlopen(f"{host}/player.js", timeout=10) as r:  # noqa: S310 - https URL built above
                got = hashlib.sha256(r.read()).hexdigest()
            with urllib.request.urlopen(f"{host}/index.html", timeout=10) as r:  # noqa: S310
                page = r.read().decode()
            if got == want and "Content-Security-Policy" in page:
                print("deploy-player: verified (player.js matches, page carries its CSP)")
                break
        except OSError:
            pass
        time.sleep(4)
    else:
        sys.exit("deploy-player: the served files did not match the upload within 2 minutes")
PY
