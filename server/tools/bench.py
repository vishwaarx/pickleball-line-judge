"""Accuracy benchmark: synthetic bounces straight into the CameraWorker (no network).

python tools/bench.py            # full matrix, prints a table and a summary
python tools/bench.py --quick    # smaller matrix
"""
from __future__ import annotations

import argparse
import itertools
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import logsetup  # noqa: E402

log = logsetup.setup("WARNING", "bench.log")

from pipeline import CameraWorker, Settings  # noqa: E402
from tools.synth import BALL_BGR, BALL_R, Bounce, Scene, expected_verdict, render_bounce  # noqa: E402


def run_case(scene: Scene, b: Bounce, fps: int, phase: float, events_dir: str, calib_dir: str, margin_cm: float,
             decoy=None):
    calls = []
    cam = CameraWorker("bench", f"bench-{fps}", Settings(margin_cm=margin_cm, speak=False),
                       on_event=calls.append, on_preview=lambda *a: None, events_dir=events_dir, calib_dir=calib_dir)
    cam.last_preview = float("inf")  # no previews in the bench
    p1, p2, in_pt = scene.line_calibration()
    from judge.detector import hsv_range_from_bgr
    t_proc = 0.0
    n = 0
    for t, frame in render_bounce(scene, b, fps, phase=phase, lead_frames=int(1.0 * fps), decoy=decoy):
        t0 = time.perf_counter()
        cam._process(frame, t + 100.0)
        t_proc += time.perf_counter() - t0
        n += 1
        if n == 1:  # calibrate once the worker knows the frame size, as the dashboard would
            cam.calibrate(p1, p2, in_pt, None, view=(frame.shape[1], frame.shape[0]))
            cam.calib.ball_bgr = BALL_BGR
            cam.detector.set_color(hsv_range_from_bgr(BALL_BGR))
    # the ball may leave the frame before two ascending frames arrive: flush like a lost track
    ev = cam.bounce.flush()
    if ev is not None:
        cam._handle_bounce(ev)
    return calls, t_proc / max(n, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--margin", type=float, default=2.0)
    ap.add_argument("--keep", action="store_true", help="keep evidence strips in server/events/bench")
    ap.add_argument("--decoy", action="store_true", help="add a walking leg next to the line in every case")
    args = ap.parse_args()
    from tools.synth import foot_decoy
    decoy = foot_decoy if args.decoy else None

    gaps_cm = [-6.0, -2.0, 0.0, 1.0, 2.5, 4.0, 8.0] if not args.quick else [-4.0, 0.5, 3.0]
    fps_list = [30, 60]
    depths = [2.5, 4.0, 6.0] if not args.quick else [3.0]
    laterals = [0.5, 3.0]
    alongs = [-7.0, 6.0] if not args.quick else [-7.0]
    phases = [0.0, 0.5]

    scene = Scene()
    events_dir = os.path.join(os.path.dirname(HERE), "events", "bench") if args.keep else tempfile.mkdtemp()
    calib_dir = tempfile.mkdtemp()
    rows = []
    t_start = time.time()
    for fps, gap, Zb, vX, vZ, phase in itertools.product(fps_list, gaps_cm, depths, laterals, alongs, phases):
        b = Bounce(Xb=gap / 100 + BALL_R, Zb=Zb, vX=vX, vZ=vZ)
        calls, ms = run_case(scene, b, fps, phase, events_dir, calib_dir, args.margin, decoy=decoy)
        expected = expected_verdict(b.gap_m, args.margin / 100)
        if not calls:
            rows.append(dict(fps=fps, gap=gap, Zb=Zb, vX=vX, vZ=vZ, phase=phase, expected=expected,
                             got="MISSED", gap_cm=None, quality="-", frames="-", ms=ms * 1000))
            continue
        c = calls[0]
        rows.append(dict(fps=fps, gap=gap, Zb=Zb, vX=vX, vZ=vZ, phase=phase, expected=expected, got=c.verdict,
                         gap_cm=c.gap_cm, quality=c.quality, frames=f"{c.n_before}+{c.n_after}", ms=ms * 1000,
                         extra=len(calls) - 1))

    # ---- report
    print(f"\n{len(rows)} cases in {time.time() - t_start:.0f}s, margin {args.margin} cm\n")
    hdr = f"{'fps':>3} {'gap':>5} {'Z':>4} {'vX':>4} {'vZ':>5} {'ph':>3}  {'expect':<9} {'got':<9} {'meas':>6} {'q':<4} {'frames':<7} {'ms/f':>5}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        meas = "" if r["gap_cm"] is None else f"{r['gap_cm']:+.1f}"
        flag = "" if r["got"] == r["expected"] else "  <-- " + ("missed" if r["got"] == "MISSED" else "wrong" if r["got"] != "NO_CALL" else "no call")
        print(f"{r['fps']:>3} {r['gap']:>+5.1f} {r['Zb']:>4.1f} {r['vX']:>4.1f} {r['vZ']:>+5.1f} {r['phase']:>3.1f}  "
              f"{r['expected']:<9} {r['got']:<9} {meas:>6} {r['quality']:<4} {r['frames']:<7} {r['ms']:>5.1f}{flag}")

    def summarize(sub, label):
        n = len(sub)
        if n == 0:
            return
        correct = sum(1 for r in sub if r["got"] == r["expected"])
        # IN and TOO_CLOSE are the same call under rule 8.D; count the call, not the label
        as_call = lambda v: "IN" if v in ("IN", "TOO_CLOSE") else v
        call_ok = sum(1 for r in sub if as_call(r["got"]) == as_call(r["expected"]))
        nocall = sum(1 for r in sub if r["got"] == "NO_CALL")
        missed = sum(1 for r in sub if r["got"] == "MISSED")
        wrong = n - correct - nocall - missed
        # a "harmful" error is calling OUT when it was IN/TOO_CLOSE, or IN when it was clearly OUT
        harmful = sum(1 for r in sub if (r["got"] == "OUT" and r["expected"] != "OUT") or
                      (r["got"] in ("IN", "TOO_CLOSE") and r["expected"] == "OUT"))
        errs = [abs(r["gap_cm"] - r["gap"]) for r in sub if r["gap_cm"] is not None]
        mae = sum(errs) / len(errs) if errs else float("nan")
        p90 = sorted(errs)[int(0.9 * (len(errs) - 1))] if errs else float("nan")
        ms = sum(r["ms"] for r in sub) / n
        extra = sum(r.get("extra", 0) for r in sub)
        print(f"{label:<18} n={n:<3} call-correct {call_ok / n:5.1%}  exact {correct / n:5.1%}  no-call {nocall / n:5.1%}  "
              f"missed {missed / n:5.1%}  harmful {harmful / n:5.1%}  false-extra {extra:<3} gap MAE {mae:4.2f} cm  p90 {p90:4.2f} cm  {ms:4.1f} ms/frame")

    print()
    summarize(rows, "all")
    for fps in fps_list:
        summarize([r for r in rows if r["fps"] == fps], f"fps {fps}")
    for Zb in depths:
        summarize([r for r in rows if r["Zb"] == Zb], f"depth {Zb} m")
    for vX in laterals:
        summarize([r for r in rows if r["vX"] == vX], f"lateral {vX} m/s")
    for vZ in alongs:
        summarize([r for r in rows if r["vZ"] == vZ], f"along {vZ:+} m/s")
    close = [r for r in rows if abs(r["gap"]) <= 2.5]
    summarize(close, "|gap| <= 2.5 cm")
    if args.keep:
        print(f"\nevidence strips: {events_dir}")


if __name__ == "__main__":
    main()
