# Overnight build & test report — read this first

Built and tested 2026-10-18, 00:30 → ~03:00, while you slept. Everything below ran on this Mac; nothing touched a real phone yet.

## 1. State of play (short)

1. **It works end to end without a phone.** Three headless Chromes, each playing a synthetic court-line clip as its *camera*, opened the real phone page over HTTPS, streamed WebRTC into the Mac, got calibrated through the real API, and the live server judged **105 bounces in 75 s with every IN/OUT/TOO-CLOSE label correct** (4 flagged rows were ±0.5 cm reads right at the boundary, still IN by rule, plus two far-out balls whose *number* was 2 cm loose). `server/logs/e2e_run.txt`.
2. **Accuracy on synthetic ground truth (336 cases, 30 & 60 fps, 2.5–6 m, lateral 0.5–3 m/s, toward and away), final run on the reviewed build `server/logs/bench_full3.txt` (identical to the pre-review run):** **call-correct 99.7 %** (IN and TOO CLOSE counted as the same call, which is what rule 8.D says), exact label 88 % (the rest are balls placed *exactly* on the edge reading +0.1–0.5 cm → TOO CLOSE), **0 missed, 0 no-call, harmful 0.3 %** (1 in 336: a 1.0 cm gap read as 2.2 → OUT instead of TOO CLOSE), gap error **0.40 cm mean, 0.8 cm p90**, ~3 ms per frame.
3. **The remaining weak spot:** balls receding fast *away* from the camera at 30 fps have the largest gap error (0.53 cm mean, 1.1 cm p90) and the one harmful call. Balls coming toward the camera — the sideline case — 0.28 cm mean. An earlier run missed 4 % of near, receding balls; that was the detector's max blob size (30 px) and is fixed (45 px).
4. **A walking leg next to the line produced zero false calls** (synthetic shoe that steps and rests; the dwell gate rejects anything that sits on the floor > 2 frames; the size gate rejects anything bigger than a ball can be at that row).
5. **Real footage (YouTube 1080p60 doubles, wide broadcast view, uncalibrated, no colour set):** the detector tracked the whole time and fired 119 "bounces" in 90 s — all shoes/paddles of far players seen through the net. Expected for that view; on court you set the ball colour at calibration and the camera sees a narrow strip. Still the reason to test with a real ball before trusting it.
6. **CPU/memory with three streams (30+60+30 fps, 720p):** server ~135 % of one core (Mac has 15), RSS ~3.6 GB before I trimmed the ring buffer (now ~2.5 GB expected). A 2-hour soak was running when I finished — see §6.

## 2. What was built

```
pickleball/
  start.sh              ← run this (prints dashboard + phone URL, writes server/logs/pickle.log)
  server/app.py         HTTPS (mkcert), /cam phone page, /offer WebRTC, /ws dashboard hub, /api/*
  server/pipeline.py    per-camera worker: detect → track → bounce → judge → evidence strip → say
  server/judge/         geometry.py (verdict bands), bounce.py (path intersection), detector.py,
                        tracker.py, linescale.py (paint-width scale + click snap)
  server/confirm_jev.py Jev second opinion (only with TYPESAFE_API_KEY + internet)
  server/static/        index.html dashboard, cam.html phone page (vanilla, offline)
  server/tools/         synth.py (court renderer), bench.py (accuracy matrix), feed_video.py,
                        e2e_check.py (scores the live server against truth)
  server/e2e/           fake_phone.mjs (headless Chrome with a clip as camera), screenshot.mjs
  server/events/        every call: strip .jpg + .json  (bench/ and feed/ are test output)
  server/calib/         one json per camera NAME — survives restarts and phone reconnects
```

Tests: `cd server && .venv/bin/python -m pytest -q` → **40 pass**.

## 3. Decisions made overnight (so you know why it behaves this way)

1. **Scale comes from the paint, not the ball.** First benchmark read gaps 1–2.5 cm too far *inside*: a motion-blurred blob looks bigger than the ball. Now the line's own 5.08 cm width is measured at 16 rows at calibration, fitted as a function of row, and gives cm/px and the expected ball radius. Error dropped from 0.97 → 0.28 cm on the same cases. Side benefit: your calibration clicks are **snapped to the real paint edge** (sub-pixel), so a 4 px sloppy click costs nothing.
2. **Calibration survives WebRTC's resolution ramp.** Chrome starts at 640×360 and climbs to 1280×720 over ~20 s; the calibration is rescaled automatically and the paint is re-measured. (This bug made every first-run verdict wrong; fixed and re-verified.)
3. **One name = one camera.** A phone that reconnects with a new browser id but the same line name replaces the stale tile and inherits its calibration.
4. **Verdict semantics:** `OUT` needs a visible gap ≥ 2.0 cm (Settings → "Out margin"); `TOO CLOSE` = IN; `NO CALL` when fewer than 2 frames on a side of the bounce unless the gap is ≥ 5 cm either way. IN calls more than 15 cm inside are logged but not spoken.
5. **Speech was off all night** (`--no-speak`) so the Mac stayed silent. `say` itself was verified by writing to a file. `./start.sh` runs with speech on.
6. **Jev** is wired as a confirmer per TypeSafe's documented request shape (assets + choice/noul questions, `TypeSafe-Danger-Zone-Images` header). Untested against the live API — no key. It never blocks a call.

## 3b. Independent code review (02:20–02:50) and what it changed

A separate reviewer agent read every file and ran its own experiments. It found 5 critical issues, all fixed and covered by new tests (`tests/test_pipeline.py`, 46 tests total now):

1. **A saved calibration got rescaled twice** after a restart (file had no resolution stamp; WebRTC's 640→1280 ramp then doubled the line off-frame). Files now carry `work_size`; legacy files are ignored.
2. **Stale test calibrations on disk** matched the default line names. Deleted; and any calibration loaded from disk must now *find the paint where it says* within its first second, otherwise the tile goes MOVED with "saved calibration does not match this view".
3. **Calibration clicks weren't tied to the snapshot's resolution.** The dashboard now sends the snapshot size; the server scales the clicks.
4. **Wake lock was never re-acquired** after iOS/Android released it (notification shade, call). Fixed — this one would have killed phones mid-match.
5. **Two phones picking the same line name replaced each other every 30 s.** The second phone now gets a clear refusal on its screen.

Also fixed from the warnings: the moved-guard's **aperture problem** (a straight line matches equally well anywhere along itself — the reviewer measured false MOVED on 47 of 80 checks; now only motion *across* the line counts and "no shift" wins ties), events deduped by a uid instead of a restarting counter, the dashboard state ticker no longer dies on an exception, a slow dashboard client can't stall the others, calibration writes are atomic, the stream clock restarting on reconnect clears track state, aspect-ratio changes pause instead of mis-rescaling, a failed paint re-measure no longer discards a good scale, ball colour is sampled from the frame you clicked on (and there's a "Clear colour" button), evidence strips draw the *judged* radius, NO CALL is not spoken for cameras that simply aren't calibrated, verdicts are spoken before the strip is written, idle cameras are reaped after 10 min, phones can't cache stale JS.

Not done (judged acceptable for tomorrow): per-client send queues for the dashboard (bounded by a 2 s timeout instead), events/ retention, a WebSocket-JPEG fallback path.

## 4. Your 10-minute morning checklist (phones only)

1. `cd "~/Documents/Old Desktop/sagan-brain/Projects/personal projects/pickleball" && ./start.sh` (if a server is already running from the night, it says "address already in use" — `kill $(lsof -tnP -iTCP:8443 -sTCP:LISTEN)` then start again). Dashboard: https://localhost:8443/ (no warning on the Mac).
2. **iPhone, Safari:** same Wi‑Fi as the Mac → scan the QR on the dashboard → *Show details → visit this website* → **Start camera** → Allow. Read the stats line: it must say **1280×720 · 60 fps camera · sending ~60 fps**. If it says 30, that's the phone's ceiling for Safari; fine, just know it. If the camera refuses even after the warning, open https://<mac-ip>:8443/ca.pem on the phone, install the profile, then Settings → General → About → Certificate Trust Settings → enable.
3. **Android, Chrome:** same steps; *Advanced → Proceed*. Read the fps line.
4. On the dashboard the tile must go **LIVE** with a moving preview within ~5 s. Click **Calibrate**: far end of a floor line's outer edge, near end, a point on the "court" side, then click the ball (or Skip) → Save. Log says `snapped … paint width … cm/px`. If the log says "could not measure the paint width", the line is too thin/low-contrast from where the phone sits — move it closer or use a brighter line.
5. **Tape test (2 min):** a strip of tape on the floor as the line, phone propped 1–2 m away looking along it, roll/bounce the ball across it a few times. Expect a spoken verdict and an evidence strip within a second. Bounce it clearly outside, clearly inside, and on the tape.
6. **Bump test:** nudge the phone 1 cm → tile stays LIVE (guard auto-shifts); nudge it 5 cm → tile goes **MOVED**, calls pause → Calibrate again (3 clicks).
7. **Both phones at once** for a minute: watch "fps in" vs "fps judged" on both tiles — they should match.
8. **Mac Internet Sharing (offline Wi‑Fi):** System Settings → General → Sharing → Internet Sharing ⓘ → Share your connection from: *iPhone USB* (your saved profile) or *Thunderbolt Bridge* → To computers using: *Wi‑Fi* → Wi‑Fi Options: name, WPA2 password, channel → toggle on. Restart `./start.sh`; the QR should now show `192.168.2.1` (the sharing bridge is preferred automatically).
9. **Speech:** Settings → "Test voice" on the dashboard; set Mac volume to max; it has to be heard across a hall.
10. Charge both phones to 100 %; bring a power bank per phone if you have them.

## 5. Venue setup (20 minutes before 18:00)

1. Mac on power, lid open, `./start.sh`, dashboard full-screen, volume max.
2. Wi‑Fi: Internet Sharing on (or a friend's hotspot — then Jev could run too, if you ever get a key).
3. Phones: one per line, **on the line's extension**, 1.5–2 m beyond the court, propped low against a bag so the lens looks straight down the line. Sidelines: behind the near baseline. Baseline: 3–4 m beyond a sideline. Three phones = the half-court the receiving team stands in.
4. Each phone: join Wi‑Fi → QR → Start camera → tap "Dim screen". Calibrate each from the Mac (4 clicks). Set the ball colour on the first one by clicking the ball.
5. Tell the group once: *the tool is advisory, it defaults to IN on anything within 2 cm, and the five-frame strip is the evidence.* (Rule 8.J bans outside input unless agreed beforehand.)

## 6. Soak test (running when I stopped)

Three fake phones streaming into the final (post-review) server from **02:44**, baseline **RSS 2.0 GB, CPU ~137 % of one core**; the live end-to-end on this exact build: 111 calls in 75 s, 107 matched, 4 boundary reads within ~1 cm (`server/logs/e2e_run2.txt`). Check in the morning:
`grep -c "pickle.cam.*\(IN\|OUT\|TOO_CLOSE\)" server/logs/pickle.log` (calls so far), `ps -o rss=,pcpu= -p $(lsof -tnP -iTCP:8443 -sTCP:LISTEN)` (memory should be flat, ~2–3 GB), and `grep -ci "error\|traceback" server/logs/pickle.log` (should be 0). Stop the fake phones with `pkill -f fake_phone.mjs`.

**Soak check 03:17 (33 min in):** RSS 2.03 GB (+25 MB since 02:44 — flat), CPU 136 %, 2,890 calls, 0 errors, 0 guard/MOVED events, all three LIVE at 30/60/30 fps with the paint scale. Verdict mix 856 IN / 1,564 OUT / 470 TOO CLOSE, which is the clips' 3:5:2 truth mix. Evidence folder had reached 886 MB, so a pruner now keeps the newest 2,000 strips per day (~600 MB).

**Restart under load 03:18:** killed and restarted the server while all three phones streamed. Phones reconnected on their own in ~12 s, each saved calibration loaded, was re-verified against the live paint (snap shift 0.0–0.1 px), and the next 45 s of calls scored **64/64 correct** without anyone re-calibrating. That is the "Mac rebooted at the venue" scenario, and it is now a non-event.

**Final soak check 04:18 (1 h 34 min of streaming, 1 h since the restart):** RSS **1.93 GB** (below the 2.03 GB baseline — no leak), CPU 139 %, **5,218 calls, 0 errors, 0 guard/MOVED events**, pruner holding `events/` at 256 MB, all three tiles LIVE at 60/30/30 fps with the paint scale; Mac had 9.5 GB free. Verdict mix 1,571 IN / 2,808 OUT / 839 TOO CLOSE — still the clips' truth ratio. **Soak passed.** The server was left running (`--no-speak`, pid in `lsof -tnP -iTCP:8443`); the fake phones expire on their own around 04:44, after which the tiles show NO SIGNAL — that's expected, not a fault.

## 7. Not verified (and the one place each will show)

| Unverified | Where you'll see it |
|---|---|
| iOS Safari grants 60 fps over WebRTC; Android Chrome fps per phone | the stats line on the phone page |
| "Proceed" on the self-signed warning unlocks the camera on each phone | Start camera → Allow prompt appears, or the error line names the cause |
| Mac Internet Sharing with no upstream on this macOS build | the QR switches to 192.168.2.1 |
| Detection on *that* hall's floor, lights and ball | the tile preview shows yellow dots following the ball |
| Jev live API (shape follows the docs; no key to test) | log line `jev:` or silence |
| Real players' feet near the line (synthetic leg passed; real shoes untested) | a wrong "bounce" would show a shoe in the strip — send me the jpg |
