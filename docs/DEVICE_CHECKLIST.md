# Real-device checklist (run at the end of every milestone)

The cloud build runs Playwright WebKit, Chromium and Firefox at the device viewports. These checks need real hardware. Run them against the live Space URL and record the results in `docs/PROGRESS.md` (date, device, iPadOS/iOS version, pass/fail, notes).

**Devices:** iPad with Apple Pencil (primary), an iPhone, and a laptop with a mouse/trackpad.

## Every milestone

- [ ] **Install:** Safari → Share → Add to Home Screen. Launches standalone (no Safari chrome); icon and splash look right.
- [ ] **Persisted storage:** first launch from the home screen asks to keep data; Settings → Offline shows "storage persisted".
- [ ] **Orientation:** rotate portrait ↔ landscape on every screen touched this milestone. No clipped or overlapping content.
- [ ] **Split View ⅓ and ½, plus Stage Manager:** layout reflows to the phone or tablet layout with no horizontal scroll.
- [ ] **Hardware keyboard:** ⌘K opens search; focus ring is visible (3 px violet); Tab order is logical.
- [ ] **Sign-in:** Google and magic link both work in Safari _and_ in the installed PWA (the magic link returns to the app).
- [ ] **Cold start:** open after the Space has slept. A loading state shows, not a blank page or a Gradio error.
- [ ] **iPhone:** bottom tab bar; the assistant opens full screen; every feature from this milestone is reachable.
- [ ] **Laptop:** mouse and keyboard only; every feature works.

## From M3 (ink)

- [ ] **Pencil draws, finger scrolls:** with the pen tool on, the finger scrolls and never draws. Two fingers zoom or pan.
- [ ] **Palm rejection:** rest a palm while writing. No stray strokes.
- [ ] **Pressure:** line width follows pressure.
- [ ] **Latency:** record a 240 fps slow-motion video of a fast stroke. Count frames from tip contact to pixel; target ≤ 20 ms (about 5 frames). Record the number.
- [ ] **Scroll performance:** 60 fps (120 on ProMotion) with ~5k strokes, measured with the ink lab's **Build 5,000-stroke page** then **Measure scroll** (/system/ink-lab, admin). Record the number.
- [ ] **Scribble:** handwriting into any text field converts to text.
- [ ] **Persistence:** ink survives reload, rotation and zoom, and appears on the laptop.

## From M2 (exam integrity)

- [ ] **Timer across backgrounding:** start a timed block, switch apps for 2 minutes, return. The timer is within ±1 s of a stopwatch.
- [ ] **Exam mode:** the assistant bar is disabled and explanations stay hidden until the block ends.

## From M5 (offline)

- [ ] **Airplane-mode day:** "Download this week", turn on Airplane Mode, then read, answer questions, review cards and write ink. Turn networking back on: everything syncs and nothing is duplicated.
- [ ] **Conflict:** edit the same note offline on the iPad and online on the laptop. The result matches the documented rule (last writer wins per row; ink merges by stroke id).
- [ ] **Web Push:** from the home-screen PWA (iOS 16.4+), allow notifications; a reminder arrives.

## From M6 (YouTube player and Anki)

- [ ] **Sandboxed official player (real YouTube, iPad Safari, Chrome, laptop):** open a lesson with a video and press play. It plays with no error 150/152/153; the speed buttons, the chapter list and "Note at m:ss" work; tapping a note seeks. The player page runs with an opaque origin (`sandbox="allow-scripts"`), so check that the video still loads, fullscreen works, and that nothing is requested from YouTube before the press (Network tab). If YouTube refuses to play inside the sandbox, report it: the bridge (`src/video/bridge.ts`) is the single place to change.
- [ ] **Anki round trip with a real deck of your own:** import an `.apkg` exported from the Anki desktop app (current and "support older versions" formats), review a card, export, import the file into Anki, and re-import it into Naadi (no duplicates).
  1. Needs a public Space or custom domain, and the owner's `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (docs/SECRETS.md). Until then Settings → Study plan → Reminders says "Reminders are not switched on yet" and shows no switch.
  2. In Safari add Naadi to the Home Screen, open it from there, go to Settings → Study plan → Reminders. In Safari itself (not installed) the section shows the install steps instead of a switch.
  3. Turn on reminders, allow notifications, press **Send a test**: the notification arrives within seconds, and tapping it opens Plan.
  4. At the daily reminder time (07:30 local) one notification says how many items are planned today; with items behind, a second one says so (at most two a day). Remove the device in Settings; no more arrive.
- [ ] **Calendar feed (Planner):** Settings → Study plan → Create calendar link, then **Subscribe in Calendar** (webcal). Apple Calendar lists the study days as all-day events with the item titles and durations. Make a new link: the old subscription stops updating. Turn it off: the events disappear after the next refresh.
- [ ] **Rebalance (Planner):** skip two days, open Plan: the amber "Behind" items and the violet suggestion show. Show changes lists each move; Accept moves the items to the coming study days and leaves assessment days and the rest day alone; Decline changes nothing.
