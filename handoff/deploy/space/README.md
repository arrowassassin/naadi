---
title: Naadi
emoji: 🫀
colorFrom: green
colorTo: purple
sdk: docker
app_port: 7860
short_description: USMLE prep for IMGs — learn, practise, plan
pinned: false
---
# Naadi (single Docker Space)
One container serves the built web app (`/`) and the API (`/api/*`) from the same origin — no CORS, one bill.
Secrets come from Space secrets as environment variables (see HANDOFF.md §9).
