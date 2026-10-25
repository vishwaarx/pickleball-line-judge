# Pickleball line judge

Phones film the court lines and stream to a Mac over local Wi‑Fi. The Mac calls each bounce **IN / OUT / TOO CLOSE → IN / NO CALL**, says it out loud, and saves a 5-frame evidence strip. It runs fully offline. Geometry makes the call, not an ML model.

## Status (handoff, 2026-10-24)

1. Works end to end with **simulated phones**: 46 unit tests pass, 99.7 % call-correct on a 336-case synthetic bench, 3-phone WebRTC end-to-end passes, and a 2-hour soak stayed flat.
2. **Not yet verified on real phones, a real court, or the live Jev API.** See the table in `TEST-REPORT.md` §7.
3. Open work is listed at the bottom.

## Read in this order

1. `README.md` (this file): setup and layout
2. `PLAN.md`: locked design decisions and the build checklist
3. `TEST-REPORT.md`: what was tested, the numbers, what changed after review, what's unverified
4. `RESEARCH.md`: sources (USAP rules, Hawk-Eye method, WebRTC on phones)

## Requirements

1. **macOS** (speech uses `say`; offline Wi‑Fi uses Internet Sharing)
2. [uv](https://docs.astral.sh/uv/). `start.sh` uses it to create a Python 3.13 venv
3. [mkcert](https://github.com/FiloSottile/mkcert): `brew install mkcert && mkcert -install` (once per Mac)
4. Node 18+, only for the simulated-phone e2e tests

## Run

```sh
cp .env.example .env      # optional; only needed for the Jev confirmer
./start.sh                # first run creates server/.venv and installs deps
./start.sh --no-speak     # silent, for testing
./start.sh --host 192.168.2.1 --port 8443   # pin the IP shown in the phone QR
```

1. Dashboard: https://localhost:8443/
2. Phones: same Wi‑Fi, scan the QR on the dashboard, accept the cert warning once, then tap **Start camera**
3. Calibrate each camera from the dashboard: far end of the line, near end, a point on the court side, then the ball (or Skip)

The full phone + venue checklist is in `TEST-REPORT.md` §4–5.

## Test

```sh
cd server
.venv/bin/python -m pytest -q          # 46 unit tests, ~4 s
.venv/bin/python tools/bench.py --quick   # synthetic accuracy matrix (drop --quick for the full 336 cases)
```

End-to-end with fake phones (headless Chrome uses a synthetic clip as its camera). Keep `./start.sh --no-speak` running in another terminal:

```sh
cd server
mkdir -p clips
.venv/bin/python tools/synth.py clips/demo30.y4m --fps 30     # also writes clips/demo30.truth.json
(cd e2e && npm install && npx playwright install chromium)
node e2e/fake_phone.mjs --name "Left sideline" --clip "$PWD/clips/demo30.y4m" --seconds 90 &
.venv/bin/python tools/e2e_check.py --cam "Left sideline" --truth clips/demo30.truth.json
```

## Layout

```
start.sh               run this; loads .env, creates the venv, starts server/app.py
server/app.py          HTTPS (mkcert), /cam phone page, /offer WebRTC, /ws dashboard, /api/*
server/pipeline.py     per-camera worker: detect → track → bounce → judge → evidence → speak
server/judge/          geometry.py (verdict bands), bounce.py (path intersection),
                       detector.py, tracker.py, linescale.py (paint-width scale + click snap),
                       replay.py (3D bounce replay from one camera, shown by the dashboard's "3D replay")
server/confirm_jev.py  optional Jev second opinion (TYPESAFE_API_KEY + internet only)
server/static/         dashboard (index.html) and phone page (cam.html), vanilla JS
server/tools/          synth.py (court renderer), bench.py, feed_video.py, e2e_check.py
server/e2e/            Playwright fake phones
server/tests/          pytest suite
```

These folders are created at runtime and are git-ignored: `server/certs/` (TLS key), `server/events/` (evidence strips), `server/calib/` (per-venue calibrations), `server/clips/`, `server/logs/`.

## Config

| Variable | Default | Purpose |
|---|---|---|
| `TYPESAFE_API_KEY` | unset | Turns on the Jev confirmer. Unset = offline, no-op |
| `TYPESAFE_API_URL` | `https://api.typesafe.ai/v1/systemone` | Jev endpoint |
| `TYPESAFE_MODEL` | `jev-preview` | Jev model (images need 1.14) |
| `PICKLE_LOG` | `INFO` | Console log level |

The out margin and speech rate are set on the dashboard under **Settings**.

## Open work

1. Real-phone test: iPhone Safari and Android Chrome fps, cert bypass, CPU with 3 phones (`TEST-REPORT.md` §4)
2. Mac Internet Sharing with no upstream internet
3. Detection on the real hall floor, lights and ball, and with real players' feet near the line
4. Jev against the live API (no key yet)
5. Not built: local YOLO ball verifier, WebSocket-JPEG fallback, per-client dashboard send queues
