---
title: Naadi player
emoji: ▶️
colorFrom: gray
colorTo: gray
sdk: static
pinned: false
---

# Naadi YouTube player page

A static page that runs the official YouTube IFrame Player API for Naadi, on an origin of its own
(`https://arrowassassin-naadi-player.static.hf.space`), so the third-party script never shares an origin with the
signed-in app. It holds no cookies, no storage and no secrets.

- Naadi embeds it in an `<iframe>` (`sandbox="allow-scripts allow-same-origin"`: same-origin here means *this* page's
  own origin) and talks to it only through typed `postMessage` messages (`apps/web/src/video/bridge.ts`).
- Both sides check `event.origin` exactly. This page accepts `init` only from the Naadi origin named in `?parent=`,
  and only when that origin is on the allow-list in `player.js` and is also the page that actually embeds it
  (`location.ancestorOrigins` or `document.referrer`). Anything else is ignored.
- Nothing is requested from YouTube until Naadi sends `init`, and Naadi adds this frame only when a student presses
  play.
- Its own CSP (meta tag in `index.html`): `script-src 'self' https://www.youtube.com https://s.ytimg.com`,
  `frame-src https://www.youtube-nocookie.com`, `connect-src 'none'`.

Deploy: `scripts/deploy_player_space.sh` (needs `HF_TOKEN`) or the `deploy-player` workflow. The Space is created once
by the owner; the script never creates it and never changes hardware.
