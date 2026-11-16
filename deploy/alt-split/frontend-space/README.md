---
title: Naadi
emoji: 🫀
colorFrom: green
colorTo: purple
sdk: static
app_build_command: pnpm install --frozen-lockfile && pnpm --filter @naadi/web build
app_file: apps/web/dist/index.html
short_description: USMLE prep for IMGs — learn, practise, plan
pinned: false
---
# Naadi web (Static Space)
Vite 8 + React 19 SPA. Built on push by the Space. Talks to the backend Space at `VITE_API_BASE`.
Build-time variables (public, not secret): `VITE_API_BASE`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
