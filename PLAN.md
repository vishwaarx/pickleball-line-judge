# Pickleball line judge — locked plan (2026-10-18)

Research and sources: [[RESEARCH]]. This file is the decision record + build checklist.

## Decisions

1. **Offline by design.** Phones → Wi‑Fi → Mac. The Mac hosts the Wi‑Fi (Internet Sharing) or any router; no internet required. HTTPS via mkcert self-signed; each phone taps "proceed" once.
2. **The judge is geometry.** Motion ∩ colour blob → single-ball tracker → bounce = vertical reversal, refined by intersecting the in/out paths in time (Hawk-Eye's method at 50/60 Hz) → ball edge vs calibrated line edge at the contact row.
3. **Verdicts follow Rule 8.D/8.E:** `IN` · `OUT` (visible gap ≥ margin) · `TOO CLOSE → IN` · `NO CALL` (too few frames: ball too fast for this phone's fps). Gap reported in cm, scaled from the ball's own radius (3.7 cm) in that frame.
4. **Evidence over verdicts.** Every call ships a 5-frame strip with the track and line drawn, saved to `server/events/`. Verdict is spoken with macOS `say`.
5. **One phone = one line, any count.** Camera sits on the line's extension, low, looking down the line; left/right of the line chosen at calibration. 3 phones = one half-court.
6. **Models:** geometry is primary. Jev 1.14 (`jev-preview`, image header) is a *confirmer* — "which tile shows the ball on the floor / is this a ball" — used only when the Mac is online; skipped silently otherwise. Slot stays open for a local YOLO26n ball verifier if testing shows false blobs.
7. **Phones propped, not mounted** → camera-moved guard: auto-refit the line for small shifts, red RECALIBRATE for large ones.

## Build checklist

- [x] Phase 1 Capture: HTTPS server, phone page (rear cam, 60 fps ask, fps readout, wake lock, QR), aiortc ingest, dashboard tiles with live preview + fps — verified with 3 headless-Chrome fake phones (2026-10-18 02:05)
- [x] Phase 2 Judge: geometry/bounce/detector/tracker (40 tests), calibration UI with paint-edge snap + paint-width scale, verdict + say, evidence strip, ring buffer
- [x] Phase 3 Harden: moved guard, NO CALL logic, resolution-ramp rescale, dwell + size gates against feet; WebSocket-JPEG fallback NOT built (WebRTC proved stable)
- [x] Phase 4: Jev confirmer wired (needs TYPESAFE_API_KEY + internet); YOLO verifier not built
- [ ] Morning (Vishwaa): iPhone Safari + Android Chrome over Wi‑Fi — cert tap, granted fps, 3-phone CPU; Mac Internet Sharing; speech volume
- [ ] Venue checklist: Wi‑Fi up, QR on screen, 3 phones placed + calibrated, spare yellow ball, Mac on power, lid open

See [[TEST-REPORT]] for results.

## Manual steps (owner cost)

Per phone ≈ 7 taps · per camera 4 clicks · Mac 2 actions · re-calibrate after a bump 3 clicks.

## Test plan

- Unit: signed distance + verdict bands; bounce time/position on synthetic 30/60 fps trajectories (±0.5 px noise) within 1.5 px; detector finds a moving yellow disc on dark/blue/green floors within 1 px.
- Integration (home): Mac's own camera via localhost → whole WebRTC path; then iPhone Safari + Android Chrome over Wi‑Fi: granted fps, cert bypass, 3-stream CPU.
- Field (venue, first 5 min): detection on the real floor/ball, moved guard, speech audible.
