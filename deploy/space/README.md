---
title: Naadi
emoji: 🫀
colorFrom: green
colorTo: purple
sdk: docker
app_port: 7860
short_description: USMLE prep for IMGs — learn, practise, plan
pinned: false
startup_duration_timeout: 30m
---

# Naadi (single Docker Space)

One container serves the built web app (`/`) and the API (`/api/*`) from the same origin: no CORS, one bill.

- Source: the Naadi monorepo; this Space is deployed by `scripts/deploy_space.sh` (CI on `main`).
- Runtime secrets arrive as environment variables from the Space settings (see `docs/SECRETS.md` in the repo).
- Health: `GET /api/health` (database, storage, OpenRouter quota, Tavily credits) and `GET /api/ready`.
