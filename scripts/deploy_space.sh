#!/usr/bin/env bash
# Deploy Naadi to the Docker Space and smoke-test it. See scripts/deploy_space.py for the steps.
# Usage: scripts/deploy_space.sh [--space ns/name] [--hardware cpu-basic] [--dry-run] [--smoke-only]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -n "${HF_TOKEN:-}" ]] || { echo "deploy: HF_TOKEN is not set" >&2; exit 1; }
command -v uv >/dev/null || { echo "deploy: uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }
# Keep the hash-locked requirements in step with apps/api/uv.lock before shipping.
# Same relative invocation as `pnpm gen:requirements` (uv writes the command into the file header).
(cd "$ROOT" && uv --directory apps/api export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt >/dev/null)
if ! git -C "$ROOT" diff --quiet -- apps/api/requirements.txt; then
  echo "deploy: apps/api/requirements.txt was stale and has been regenerated; commit it first" >&2
  exit 1
fi
exec uv run --script "$ROOT/scripts/deploy_space.py" "$@"
