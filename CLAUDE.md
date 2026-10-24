# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Offline pickleball line judge. Phone browsers stream the court line over WebRTC to a Mac; the Mac calls each bounce `IN` / `OUT` / `TOO_CLOSE` (treated as IN) / `NO_CALL`, speaks it with macOS `say`, and writes a 5-frame evidence strip. **Geometry makes the call, not an ML model.** macOS-only (`say`, `ifconfig`, `mkcert`). Verified only with simulated phones; real phones/court/Jev API are untested (see `TEST-REPORT.md` §7).

Design decisions are locked in `PLAN.md`; results and the rationale for non-obvious behaviour are in `TEST-REPORT.md` §3/§3b. Read those before changing judging behaviour.

## Commands

All Python runs from `server/` — imports are flat (`import confirm_jev`, `from judge.geometry import ...`), and pytest sets `pythonpath = ["."]`.

```sh
./start.sh                       # creates server/.venv via uv (Python 3.13) on first run, then runs server/app.py
./start.sh --no-speak            # silent; use for testing
./start.sh --host 192.168.2.1 --port 8443   # pin the IP advertised in the phone QR
kill $(lsof -tnP -iTCP:8443 -sTCP:LISTEN)   # "address already in use"

cd server
.venv/bin/python -m pytest -q                               # unit tests (~4 s)
.venv/bin/python -m pytest tests/test_bounce.py -q          # one file
.venv/bin/python -m pytest -q -k saved_calibration          # one test by name
.venv/bin/python tools/bench.py --quick                     # synthetic accuracy matrix (drop --quick for all 336 cases)
.venv/bin/python tools/feed_video.py clip.mp4 [--line x1,y1,x2,y2 --inside x,y] [--ball x,y]   # real video through one worker
```

Requires `mkcert` installed (`brew install mkcert && mkcert -install`); `app.py` shells out to it on every start to (re)issue the TLS cert for the current LAN IPs. No linter or formatter is configured.

End-to-end with fake phones (headless Chrome uses a `.y4m` clip as its camera; keep `./start.sh --no-speak` running):

```sh
cd server && mkdir -p clips
.venv/bin/python tools/synth.py clips/demo30.y4m --fps 30        # also writes clips/demo30.truth.json
(cd e2e && npm install && npx playwright install chromium)
node e2e/fake_phone.mjs --name "Left sideline" --clip "$PWD/clips/demo30.y4m" --seconds 90 &
.venv/bin/python tools/e2e_check.py --cam "Left sideline" --truth clips/demo30.truth.json
```

## Architecture

### Process and threading model

- `app.py`: one aiohttp asyncio loop (HTTPS). `Manager` owns one `CameraWorker` per phone plus its `RTCPeerConnection`; `Hub` fans JSON out to dashboard WebSockets.
- `pipeline.CameraWorker` is a **thread per camera**. The aiortc track pump calls `cam.push(frame)`; the worker keeps only the newest 4 frames and processes them in `_process`.
- Workers call `on_event` / `on_preview` from their own thread. `Manager` marshals these onto the loop with `loop.call_soon_threadsafe`; `Hub.loop` is replaced with the running loop in `on_startup`. Keep any new worker-to-dashboard path thread-safe the same way.
- Calibration state is written by HTTP handlers and read by the worker, so it is guarded by `CameraWorker._lock` (RLock).
- `Speaker` is a single thread with one waiting slot. A newer call replaces the waiting one, but an IN never replaces a waiting OUT. A call older than `MAX_AGE_S` is dropped, and an OUT cuts off a non-OUT mid-sentence. `tests/test_speaker.py` swaps `say` for a stand-in command. Jev confirmations run on their own threads.

### Per-frame pipeline (`CameraWorker._process`)

downscale to "work" resolution (≤ `WORK_W`=1280 wide) → ring buffer (150 frames) → `judge/detector.py` (MOG2 motion ∩ optional HSV ball-colour mask) → size gate (expected ball radius per row from the paint scale) → `judge/tracker.py` (single-ball tracker with decoy tracks) → `judge/bounce.py` (bounce = local **max** of y since image y grows down; contact time from intersecting fitted descend/ascend paths; dwell gate rejects feet) → `_handle_bounce` → `judge/geometry.judge_contact` → speak, then write the evidence strip, then optionally ask Jev.

About `REPLAY_AFTER_S` (0.35 s) after each call, or sooner if the track is lost or another bounce arrives, `_finish_replay` runs `judge/replay.py` on the tracker trail around the bounce. It writes a `replay` key into that call's evidence JSON and re-emits the call with `replay: true`, under the same `uid`. The camera's height and tilt come from the paint-width scale: a flat floor makes width exactly linear in the row. The lens field of view is assumed (`ASSUMED_HFOV_DEG`). The dashboard's `static/replay.js` only draws this JSON. Distance along the line is the weak axis from one camera.

Every 2 s `_guard` template-matches a band around the line to detect a bumped phone. It only counts shift *across* the line (aperture problem). A shift of ≤ 15 px slides the line; a larger one sets `MOVED`, and calls become `NO_CALL` until recalibration. The dashboard's "Phone is back" button (`reset_guard`, shown only while MOVED) calls `rearm_guard`. It clears MOVED only if `_measure_line(recent=True)` finds paint at the calibrated line in the newest frames; otherwise it stays MOVED.

### Geometry and verdict rules

- The calibrated line is the **outer edge** of the paint. `LineCal.in_sign` makes signed distance positive on the IN side.
- `gap = -distance - r`: gap ≤ 0 → IN; 0 < gap < margin (default 2 cm) → TOO_CLOSE; gap ≥ margin → OUT (USAP rules 8.D/8.E).
- cm/px comes from the **paint width** (5.08 cm, `judge/linescale.py`, fitted linearly by image row), not the motion-blurred ball blob. The ball radius (3.7 cm) is only a fallback. Calibration clicks are snapped to the detected paint edge.
- A "low"-quality bounce (< 2 track points on a side) → NO_CALL unless |gap| ≥ `low_quality_call_cm` (5 cm).

### Calibration lifecycle (most bug-prone area)

- Saved to `server/calib/<camera NAME>.json`, keyed by **line name, not `cam_id`**. One name = one camera: a reconnecting phone with the same name inherits the calibration, and `/offer` refuses (409) a second *live* phone using a name already in use.
- Coordinates are in work resolution, and the file stores `work_size`. Files without it are ignored.
- WebRTC ramps resolution after connecting (e.g. 640×360 → 1280×720). `_rescale_calibration` carries the line and scale across. An aspect-ratio change sets MOVED instead of rescaling.
- A calibration loaded from disk is untrusted until `_measure_line` finds paint where it says (after 30 frames); otherwise the camera goes MOVED.
- A stream clock jumping backwards (new WebRTC connection) resets the tracker, bounce detector and ring buffer.
- Dashboard clicks are sent with the snapshot's `view` size and scaled to the current work size. The ball colour is sampled from that snapshot frame.

### Dashboard / phone protocol

- `/ws` sends `{"type": "state" | "preview" | "event"}`. State is broadcast every 0.5 s by `state_ticker`, which also reaps cameras idle > 10 min and prunes `events/` to 2000 strips per day.
- An event can arrive more than once with the same `uid`. Later copies carry Jev's `confirmation` and/or `replay: true` and are sent with `update: true`. `Manager._on_event` and the dashboard patch the row by `uid`.
- HTTP input is checked before it touches state: `read_json`, `calibration_error` and `Settings.update`, which raises `ValueError` and applies nothing. Bad input gets a 400 with `{"error": "<what to do>"}`, and the calibration overlay shows that text. `/offer` validates the SDP before creating a camera, and removes the camera again if aiortc rejects the offer.
- `server/static/` is vanilla JS with no CDN, because the venue is offline. It is served with `Cache-Control: no-store` so phones never run stale JS. The phone page (`cam.js`) persists `camId` and `camName` in localStorage.

### Jev confirmer

`confirm_jev.py` is optional and never blocks or changes a call. It is active only when `TYPESAFE_API_KEY` is set (loaded from `.env` by `start.sh`). It is untested against the live API.

## Testing patterns

- Tests and `tools/bench.py` drive `CameraWorker._process(frame, t)` directly with numpy frames rendered by `tools/synth.Scene`, with no network.
- Construct the worker with `Settings(speak=False)` and set `cam.last_preview = float("inf")` to skip previews.
- Use `tmp_path` for `events_dir`/`calib_dir` so stale calibrations on disk don't leak between runs.
- `server/logs/bench_full3.txt`, `e2e_run.txt` and `e2e_run2.txt` are committed accuracy evidence cited by `TEST-REPORT.md`. Everything else under `server/logs/`, `certs/`, `events/`, `calib/` and `clips/` is runtime output and git-ignored.
