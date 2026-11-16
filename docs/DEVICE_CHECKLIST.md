# Real-device checklist (run at the end of every milestone)

The cloud build runs Playwright WebKit, Chromium and Firefox at the device viewports. These checks need real hardware. Run them against the live Space URL and record the results in `docs/PROGRESS.md` (date, device, iPadOS/iOS version, pass/fail, notes).

**Devices:** iPad with Apple Pencil (primary), an iPhone, and a laptop with a mouse/trackpad.

## Every milestone

- [ ] **Install:** Safari → Share → Add to Home Screen. Launches standalone (no Safari chrome); icon and splash look right.
- [ ] **Persisted storage:** first launch from the home screen asks to keep data; Settings → Offline shows "storage persisted".
- [ ] **Orientation:** rotate portrait ↔ landscape on every screen touched this milestone. No clipped or overlapping content.
- [ ] **Split View ⅓ and ½, plus Stage Manager:** layout reflows to the phone or tablet layout with no horizontal scroll.
- [ ] **Hardware keyboard:** ⌘K opens search; focus ring is visible (3 px violet); Tab order is logical.
- [ ] **Sign-in:** Google and magic link both work in Safari *and* in the installed PWA (the magic link returns to the app).
- [ ] **Cold start:** open after the Space has slept. A loading state shows, not a blank page or a Gradio error.
- [ ] **iPhone:** bottom tab bar; the assistant opens full screen; every feature from this milestone is reachable.
- [ ] **Laptop:** mouse and keyboard only; every feature works.

## From M3 (ink)

- [ ] **Pencil draws, finger scrolls:** with the pen tool on, the finger scrolls and never draws. Two fingers zoom or pan.
- [ ] **Palm rejection:** rest a palm while writing. No stray strokes.
- [ ] **Pressure:** line width follows pressure.
- [ ] **Latency:** record a 240 fps slow-motion video of a fast stroke. Count frames from tip contact to pixel; target ≤ 20 ms (about 5 frames). Record the number.
- [ ] **Scroll performance:** a page with about 5k strokes still scrolls smoothly.
- [ ] **Scribble:** handwriting into any text field converts to text.
- [ ] **Persistence:** ink survives reload, rotation and zoom, and appears on the laptop.

## From M2 (exam integrity)

- [ ] **Timer across backgrounding:** start a timed block, switch apps for 2 minutes, return. The timer is within ±1 s of a stopwatch.
- [ ] **Exam mode:** the assistant bar is disabled and explanations stay hidden until the block ends.

## From M5 (offline)

- [ ] **Airplane-mode day:** "Download this week", turn on Airplane Mode, then read, answer questions, review cards and write ink. Turn networking back on: everything syncs and nothing is duplicated.
- [ ] **Conflict:** edit the same note offline on the iPad and online on the laptop. The result matches the documented rule (last writer wins per row; ink merges by stroke id).
- [ ] **Web Push:** from the home-screen PWA (iOS 16.4+), allow notifications; a reminder arrives.
