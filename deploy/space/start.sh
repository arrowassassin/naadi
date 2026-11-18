#!/bin/sh
# Space entrypoint (deploy/space/Dockerfile CMD). SearXNG first (background, loopback only), then the migrations,
# then the API in the foreground (exec, so it receives the container's signals).
set -eu

if [ "${NAADI_SEARXNG:-on}" != "off" ] && [ -x /opt/searxng/.venv/bin/granian ]; then
  (
    # A fresh secret per container start: SearXNG signs its own cookies with it. Never printed.
    SEARXNG_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
    export SEARXNG_SECRET
    export SEARXNG_SETTINGS_PATH=/home/user/app/deploy/searxng/settings.yml
    cd /opt/searxng/src
    while :; do
      /opt/searxng/.venv/bin/granian --interface wsgi --host 127.0.0.1 --port 8888 --workers 1 \
        --log-level warning searx.webapp:app || true
      echo '{"event": "searxng.exited", "level": "warning", "logger": "naadi.start"}'
      sleep 10
    done
  ) &
fi

python -m naadi_api.agent.migrate_all
exec uvicorn naadi_api.agent.host:app --host 0.0.0.0 --port 7860 --workers 1 --proxy-headers \
  --forwarded-allow-ips '*' --no-access-log --log-config naadi_api/uvicorn_logging.json --timeout-graceful-shutdown 10
