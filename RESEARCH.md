# Pickleball line-call system — consolidated research (2026-10-18)

Match: Mon 2026-10-19, 18:00, indoor, good lights. Build window: ~36 h.

## 0. What you asked for, restated as constraints

| Constraint | Source |
|---|---|
| 2–6 phones, **mixed iPhone + Android**, each covering ONE line, dynamic count | you |
| **Live**, browser-based: open a link, tap start, stream to the Mac — no recording, no AirDrop | you |
| Camera framed on the line: left of line = IN, on line = IN, right = OUT, no ball = no call | you |
| Phones **propped against bags/bottles**, owners playing | you |
| **No internet at venue**; Mac shares its own Wi‑Fi | you |
| Lines that matter: **baselines + sidelines** | you |
| Mac: M5 Pro, 24 GB; Python 3.11/3.13/3.14, uv, ffmpeg, node, mkcert already installed | checked |

## 1. Headline findings (read these first)

1. **The rulebook already decides the hard cases for us.** 2026 USA Pickleball Rule 8.E: you may not call "out" unless you "clearly see a space between the line and the ball when it lands"; 8.D: "Any ball that cannot be promptly called 'out' must be considered 'in'." So the system's verdict must be **IN / OUT (visible gap) / TOO CLOSE → IN**. Not a 50/50 classifier. ([2026 rulebook PDF](https://pickleballcanada.org/wp-content/uploads/2026/03/2026-Official-Rulebook-EN.pdf), §8)
2. **No live stream from a phone exceeds 60 fps.** Continuity Camera, Camo, NDI, browser WebRTC — all 30–60 fps ([Apple](https://developer.apple.com/videos/play/wwdc2022/10018/), [Camo](https://camo.com/pricing), [NDI](https://docs.ndi.video/all/using-ndi/obs-and-3rd-party-software/how-to-use-ndi-hx-camera-app-ios)). At drive speed (20 m/s) the ball moves **67 cm per frame at 30 fps, 33 cm at 60 fps**; ground contact lasts 2–4 ms. You will almost never have the contact frame. **Hawk-Eye has the same problem at 50/60 Hz and solves it by fitting the trajectory and intersecting the in-and-out paths** ([ITF Hawk-Eye approval report](https://www.itftennis.com/media/6216/pat-13-002-pat-approval-report-hawk-eye-rev-6.pdf)). We do the same.
3. **Camera placement does more than any model.** Put the phone **on the extension of the line it judges**, looking down the line — the way ITF line judges sit ("on an extension of their respective lines… not elevated", [ITF Duties 2026](https://www.itftennis.com/media/2509/duties-procedures-for-officials-2026.pdf)). Then in/out becomes a left/right pixel offset, camera height doesn't matter, and motion blur runs *along* the line, not across it. Your "left = in, right = out" framing is exactly this.
4. **Jev (TypeSafe AI) cannot make the call; nor can any VLM.** Jev 1.14.0 (14 Oct 2026) added image input and a `Box` answer, but TypeSafe's own docs say: *"Images and Box questions aren't yet up to our standards to call ready for production"* and *"A target a few dozen pixels wide in a very large image can be missed"* ([docs.typesafe.ai/images](https://docs.typesafe.ai/images), [/primitives/box](https://docs.typesafe.ai/primitives/box)). Our ball is 10–20 px and the decision margin is ~2–3 px. Frontier VLMs score 58.6% on thin-line geometry tasks humans get ~100% on ([BlindTest, "Vision language models are blind"](https://arxiv.org/html/2407.06581v4)); Anthropic's docs call their coordinates "approximate" ([vision docs](https://platform.claude.com/docs/en/build-with-claude/vision)). Also: Jev is cloud-only — no internet at the venue = no Jev.
5. **The call is geometry, not AI.** Detect ball → track → find the bounce (vertical reversal / path kink) → compare ball edge to line edge at that image row. The closest working artefacts do exactly this: [SpartanWarrior7/line-calls](https://github.com/SpartanWarrior7/line-calls) (phones, honest 2–5 cm), [viceduong/pickleball-ref-ios](https://github.com/viceduong/pickleball-ref-ios) (IN/OUT/TOO_CLOSE, 3–5 cm band), [monocular tennis ELC paper](https://arxiv.org/abs/2107.09255) (99.4% overall, **81.8% on near-line balls**).
6. **Sidelines will work better than baselines.** A sideline camera sees the ball coming mostly *toward* it (small image motion, many frames around the bounce). A baseline camera sees the ball *crossing* its view at full speed (few frames). Expect good sideline calls at 30 fps, baseline calls that need 60 fps or a lob/serve-speed ball.
7. **Your "6 lines = 6 cameras" instinct is right.** Pixel budget: with 1080p and a normal lens, the 5 cm line is 11 px at 6 m and 7 px at 10 m. A sideline is 13.4 m, so one phone covers *its near half* well — each sideline is really two half-lines. 3 phones = one half-court (both sidelines' near halves + that baseline). 6 = full court.
8. **Browser camera access requires HTTPS.** The Mac is offline, so: mkcert self-signed cert → each phone taps "proceed anyway" once (to verify on a real phone tonight) or installs the mkcert root CA (~6 taps/phone). Escape hatch: if *one* friend hotspots, everyone has internet → real certificate, zero warnings, and Jev becomes usable as a second opinion.
9. **Zero-build fallbacks exist** but don't match your design: SwingVision (iOS only, one iPhone behind the baseline, $15–25/mo, claims 97% within 10 cm at 60 fps, [swing.vision](https://swing.vision/home/)); Pickleball Eye (free iPhone app, 45 s rolling replay, human judges, [App Store](https://apps.apple.com/us/app/pickleball-eye/id6737577281)).

## 2. Physics that sets the limits

Ball: 7.3–7.5 cm, 22–26 g ([USAP equipment standards](https://www.pickleball.ph/uploads/1/2/5/9/125919777/-_official_equipment_standards_manual.pdf)). Line: 5.08 cm, court measured to the **outside edge** (rule 3.A.2). Speeds: serve median 26.9 m/s (radar, [591-paddle study](https://www.mattspickleball.com/paddle-data-study)), rally average 11.8 m/s, rec drive assumed 18–25 m/s (no measured source), dink ~3–6 m/s.

| Live fps | Ball travel per frame @20 m/s | Frames with the contact (4 ms) | What we can do |
|---|---|---|---|
| 30 | 67 cm | ~12% | Fit trajectory; needs ≥2 frames in, ≥2 out |
| 60 | 33 cm | ~24% | Same, twice as robust |
| 120/240 | 17 / 8 cm | 48 / 96% | Not available live from a browser |

Blur: at 1/60 s shutter a 20 m/s ball smears 33 cm. Irrelevant for a sideline camera (motion is along the sight line); for a baseline camera we track the streak's centre. Indoor light helps — ask the phone for 60 fps and accept what `track.getSettings()` returns.

Bounce footprint: the ball skids 5–7 cm during contact; the "bounce" is a smear one ball-diameter long. Hawk-Eye's stated error: 2.6–3.6 mm with 10 cameras; consumer In/Out device: 20–30 mm measured ([review](https://gadgetsandwearables.com/2026/04/26/in-out-tennis-line-calling/)). Humans within 10 cm of a line: ~8% wrong. **Our honest target: 2–4 cm; anything inside that band is TOO CLOSE → IN.**

## 3. Capture path (decided by your constraints)

| Option | Mixed phones | Live | fps | Offline | Verdict |
|---|---|---|---|---|---|
| **Browser page → WebRTC → Mac** | yes | yes | 30–60 | yes (self-signed) | **chosen** |
| Browser page → JPEG over WebSocket | yes | yes | ~30 | yes | fallback mode in same page |
| Continuity Camera | iPhone only, **one phone per Mac** ([Apple](https://support.apple.com/en-us/102546)) | yes | 60 | yes | no |
| Larix / NDI / IP Webcam apps | yes but 3 different apps | yes | 30–60 | yes | no (install burden) |
| Record 240 fps + AirDrop on dispute | — | **no** | 240 | — | rejected by you |

Mac side: Python 3.13 + `aiortc` (WebRTC receive) + OpenCV; fallback ingest = [MediaMTX](https://github.com/bluenviron/mediamtx) (WHIP in, RTSP out). Measured LAN glass-to-glass for browser WHIP → MediaMTX: 180–310 ms ([benchmark](https://www.adaptnxt.com/blogs/mediamtx-whip-whep-latency-benchmarks-4-deployments)); aiortc direct should be lower.

Network: (a) Mac Internet Sharing with no upstream — creates a Wi‑Fi with DHCP; to test tonight. (b) One friend's phone hotspot — simpler, also gives internet. Either way all phones + Mac on one LAN, Mac shows a QR code to join.

Unknowns to measure on real phones tonight: iOS Safari fps actually granted (WebKit has had frameRate-constraint bugs, [bug 261461](https://bugs.webkit.org/show_bug.cgi?id=261461)); Android Chrome fps per phone; whether "proceed anyway" on the cert warning unlocks the camera on iOS Safari and Android Chrome.

## 4. Models — what's worth running

| Role | Model | Where | Latency | Cost | Use |
|---|---|---|---|---|---|
| **The call** | Geometry: motion+colour blob → Kalman track → path-kink bounce → edge-vs-line | Mac CPU | <5 ms/frame | 0 | **primary, always** |
| Ball verifier (parallel #2) | YOLO26n trained on Roboflow pickleball set (338 imgs, mAP50 77.7, [dataset](https://universe.roboflow.com/gamechangerv1/pickleball-detection-1oqlw)); 7.6 ms/frame on M4 via CoreML ([Ultralytics](https://docs.ultralytics.com/integrations/coreai)) | Mac ANE | ~8 ms | 0 | reject false blobs (shoes, shadows) — **if time permits** |
| Second opinion on the evidence card (parallel #3) | Jev 1.14 (`jev-preview`), 0.36 s median measured ([hands-on](https://1023jack.com/news/jev-can-see-now-i-pointed-it-at-30-of-my-own-images/)), $0.042/M tokens ([pricing](https://docs.typesafe.ai/models)) | cloud | ~0.4 s | ≈ $0 | "Is a ball touching the ground in this crop?" — **only if hotspot internet** |
| Local VLM alternative | Qwen3.5-9B 4-bit (≈6 GB, [LM Studio](https://lmstudio.ai/models/qwen/qwen3.5-9b)) or Moondream 3.1 | Mac GPU | 0.8–2 s | 0 | same gate role, offline; not for the call |
| Not for this | V-JEPA 2.1 (encoder only, no detector head, CUDA-first) · OpenAI Decisions API (`gpt-6-luna`, cloud) · Strands Decider 2B (vision not trained) | — | — | — | — |

Evidence that heat-map trackers beat YOLO on small balls: tennis broadcast, TrackNetV3 1.66 px error / recall 0.80 vs YOLO11 17.5 px / recall 0.42 ([RacketVision](https://arxiv.org/html/2511.17045)). TrackNet needs training data we don't have by tomorrow → day-2 upgrade. Only pickleball-trained TrackNet with weights: [AndrewDettor/TrackNet-Pickleball](https://github.com/AndrewDettor/TrackNet-Pickleball) (TensorFlow, 2023).

## 5. Proposed design

```
phone browser (per line)             Mac (M5 Pro)
┌──────────────────────┐   WebRTC    ┌────────────────────────────────────────┐
│ /cam  getUserMedia   │ ──────────▶ │ ingest (aiortc) → per-camera worker:    │
│ rear, 720p/1080p,    │   H.264     │  motion ∩ colour blob → track → bounce  │
│ ideal 60fps, wakelock│             │  → edge vs calibrated line → verdict    │
│ shows granted fps    │             │ ring buffer 4 s JPEG → evidence strip   │
└──────────────────────┘             │ dashboard (/) : tiles, calibrate, banner│
         QR to join                  │ `say "OUT, left sideline"`              │
                                     └────────────────────────────────────────┘
```

Placement (3 phones = one half-court; the half where the *receiving* team stands):

```
              far baseline
   ┌───────────────────────────┐
   │                           │
   │     (not covered)         │
   ├─ ─ ─ ─ ─ kitchen ─ ─ ─ ─ ─┤
   ╞═══════════ net ═══════════╡
   ├─ ─ ─ ─ ─ kitchen ─ ─ ─ ─ ─┤
   │▲                         ▲│   ▲ = phone on the sideline's extension,
   │                           │       1.5–2 m behind the near baseline,
   └───────────────────────────┘       looking down its sideline
   ▲                                   ◀ = phone on the near baseline's extension,
   ◀  (3–4 m beyond the sideline)         3–4 m out, looking along the baseline
```

Decision logic per camera:
1. Calibration (Mac UI, per camera): click 2 points on the **outer edge** of the line, click once on the IN side. Stored with a reference snapshot.
2. Each frame: motion mask (MOG2) ∩ colour prior (ball HSV, set at calibration by clicking the ball) → blobs 4–40 px, roundish → nearest-neighbour/Kalman track.
3. Bounce: lowest image point of the track with vertical velocity sign flip; refine by intersecting the incoming and outgoing path lines (Hawk-Eye's trick) → bounce point (x*, y*).
4. Verdict: signed distance from the ball's line-side edge to the line at row y*. `OUT` if gap ≥ margin (start 3 px, tune tonight), `IN` if overlapping or on the IN side, `TOO CLOSE → IN` inside the margin, `NO CALL` if <2 frames before or after the bounce (ball too fast for this phone's fps — the UI says so).
5. Output: banner + `say`, plus an evidence strip (5 frames around the bounce with the track and line drawn) — the strip settles arguments, not the word.
6. Camera-moved guard: every 2 s compare the line region to the calibration snapshot; auto-refit the line within ±15 px by Hough; beyond that → red "RECALIBRATE cam 2" and no calls from it.

## 6. Expected accuracy and failure modes (say this to the group beforehand)

- Sideline calls at 30–60 fps: expect correct IN/OUT for gaps ≥ 3 cm; TOO CLOSE inside.
- Baseline calls on drives at 30 fps: often NO CALL (ball crosses the view in 2–3 frames). Lobs, serves, drops: fine. 60 fps roughly doubles the callable share.
- Player's foot or paddle crossing the line region → the trajectory check (descending then ascending path) rejects most; the YOLO verifier would reject the rest.
- Bumped phone → RECALIBRATE alarm; the fix is 3 clicks.
- Ball colour similar to floor → detection falls back to motion only; tell me the ball and floor colours.
- Rule 8.J bans spectators' input; a tool is binding only if both teams agree *before* play. Agree it's advisory: evidence strip + default-IN.

## 7. Manual steps (your/owners' cost)

| Who | Step | Count |
|---|---|---|
| Each phone owner | join Wi‑Fi, scan QR, "proceed" on cert warning, pick line name, allow camera, prop phone on the line extension | ~7 taps, ~1 min |
| You, per camera | 2 clicks on the line + 1 click IN side (+ 1 click on the ball for colour) | 4 clicks |
| You, Mac | start Wi‑Fi sharing (or join hotspot), run `./start.sh`, keep lid open | 2 actions |
| Re-calibrate after a bump | 3 clicks | per incident |

## 8. Build plan

| Phase | Hours (mine) | Deliverable |
|---|---|---|
| 1. Capture | 2 | HTTPS server (mkcert), phone page (rear camera, fps readout, wake lock, QR join), aiortc ingest, dashboard tiles showing live frames + fps per phone |
| 2. Judge | 3 | calibration UI, blob/track/bounce, verdict + `say`, evidence strip, ring buffer |
| 3. Harden | 2 | camera-moved guard, NO CALL logic, WebSocket-JPEG fallback, tuning on a taped line at home with your phones |
| 4. Optional | 2 | YOLO26n verifier trained on the Roboflow set; Jev second opinion if hotspot |

Test tonight at home needs: your iPhone and/or Android, a pickleball, 1 m of tape on the floor as a "line", Mac on home Wi‑Fi.

## 9. Not verified (and how we'll know)

- fps a browser actually grants on *these* phones → the phone page prints it.
- Cert-warning bypass unlocks camera on iOS Safari / Android Chrome → test on your phone tonight; fallback = install mkcert root CA (~6 taps/phone).
- Mac Internet Sharing with no upstream works on this macOS → test tonight; fallback = phone hotspot.
- aiortc throughput for 3 × 720p60 on M5 Pro → measure; fallback = MediaMTX.
- Detection in *that* hall's light/floor/ball colour → first 5 minutes at the venue; bring a spare ball of a contrasting colour.
- Jev tokens-per-image and real Box accuracy on 15-px targets → unpublished; vendor says not production-ready.
- All speed/contact numbers are from radar/paddle studies and derivations; no measured pickleball drive speed exists.

## 10. Sources worth opening

- Rules §8 line calls: https://pickleballcanada.org/wp-content/uploads/2026/03/2026-Official-Rulebook-EN.pdf
- How Hawk-Eye does it with 60 Hz cameras: https://www.itftennis.com/media/6216/pat-13-002-pat-approval-report-hawk-eye-rev-6.pdf
- Where line judges sit (ITF): https://www.itftennis.com/media/2509/duties-procedures-for-officials-2026.pdf
- Jev image/Box limits (TypeSafe): https://docs.typesafe.ai/images · https://docs.typesafe.ai/primitives/box
- VLMs fail thin-line geometry: https://arxiv.org/html/2407.06581v4
- DIY phones line calls (plan + code): https://github.com/SpartanWarrior7/line-calls
- Pickleball IN/OUT/TOO_CLOSE POC: https://github.com/viceduong/pickleball-ref-ios
- Pickleball ball dataset: https://universe.roboflow.com/gamechangerv1/pickleball-detection-1oqlw
- YOLO26 on Apple Neural Engine: https://docs.ultralytics.com/integrations/coreai
- Browser WHIP → MediaMTX latency: https://www.adaptnxt.com/blogs/mediamtx-whip-whep-latency-benchmarks-4-deployments
