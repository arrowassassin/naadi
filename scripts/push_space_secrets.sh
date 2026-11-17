#!/usr/bin/env bash
# Push Naadi's runtime configuration to the Hugging Face Space.
#   secrets   → hf spaces secrets add   (value read from the environment by NAME; never on argv)
#   variables → hf spaces variables add (fed on stdin via --env-file -)
#
# Values come from the process environment, then `.env` (local-dev fallback). Nothing is printed except names.
# Usage: scripts/push_space_secrets.sh [--yes] [--dry-run]
# Requires: hf CLI (huggingface_hub 2.4.0) and HF_TOKEN with write access to the Space.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
YES=0; DRY=0
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

# Local-dev fallback: export only keys not already set in the environment.
if [[ -f "$ROOT/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    line="${line#export }"
    key="${line%%=*}"; val="${line#*=}"
    [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || continue
    val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
    if [[ -z "${!key:-}" && -n "$val" ]]; then export "$key=$val"; fi
  done < "$ROOT/.env"
fi

SPACE="${HF_SPACE_ID:-arrowassassin/naadi}"

# Kept in sync with docs/SECRETS.md.
SECRETS=(DATABASE_URL SUPABASE_SERVICE_ROLE_KEY SUPABASE_JWT_SECRET OPENROUTER_API_KEY NVIDIA_API_KEY TAVILY_API_KEY
         HF_GEN_TOKEN ADMIN_EMAILS YOUTUBE_API_KEY R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY)
VARIABLES=(SUPABASE_URL SUPABASE_ANON_KEY VITE_SUPABASE_URL VITE_SUPABASE_ANON_KEY
           OPENROUTER_DAILY_CAP OPENROUTER_APP_URL OPENROUTER_APP_TITLE GOOGLE_OAUTH_CONFIGURED
           R2_ACCOUNT_ID R2_BUCKET R2_PUBLIC_BASE_URL APP_ENV APP_BASE_URL LOG_LEVEL)
FIXED_VARIABLES=("GRADIO_SSR_MODE=false")   # required by the free Gradio/ZeroGPU path (HANDOFF §2)

command -v hf >/dev/null || { echo "hf CLI not found: uv tool install huggingface_hub==2.4.0" >&2; exit 1; }
[[ -n "${HF_TOKEN:-}" ]] || { echo "HF_TOKEN is not set" >&2; exit 1; }

present_secrets=(); missing=()
for k in "${SECRETS[@]}"; do [[ -n "${!k:-}" ]] && present_secrets+=("$k") || missing+=("$k"); done
present_vars=()
for k in "${VARIABLES[@]}"; do [[ -n "${!k:-}" ]] && present_vars+=("$k") || missing+=("$k"); done

echo "Target Space: $SPACE"
echo "Secrets to set   (${#present_secrets[@]}): ${present_secrets[*]:-none}"
echo "Variables to set ($(( ${#present_vars[@]} + ${#FIXED_VARIABLES[@]} ))): ${present_vars[*]:-} GRADIO_SSR_MODE"
echo "Not set locally, skipped (${#missing[@]}): ${missing[*]:-none}"
echo "Values are never printed. Existing Space values for skipped names are left untouched."

if (( DRY )); then echo "--dry-run: nothing pushed."; exit 0; fi
if (( ! YES )); then
  read -r -p "Push these to $SPACE? Type 'yes' to continue: " ans
  [[ "$ans" == "yes" ]] || { echo "Aborted."; exit 1; }
fi

if (( ${#present_secrets[@]} )); then
  args=(); for k in "${present_secrets[@]}"; do args+=(-s "$k"); done
  hf spaces secrets add "$SPACE" "${args[@]}" --format quiet >/dev/null
  echo "✓ ${#present_secrets[@]} secret(s) set"
fi

{
  for kv in "${FIXED_VARIABLES[@]}"; do printf '%s\n' "$kv"; done
  for k in "${present_vars[@]}"; do printf '%s=%s\n' "$k" "${!k}"; done
} | hf spaces variables add "$SPACE" --env-file - --format quiet >/dev/null
echo "✓ $(( ${#present_vars[@]} + ${#FIXED_VARIABLES[@]} )) variable(s) set"

echo "Names now on the Space:"
hf spaces secrets ls "$SPACE" --format quiet 2>/dev/null | sed 's/^/  secret   /' || true
