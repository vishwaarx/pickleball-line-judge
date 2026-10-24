"""Run a video file through one CameraWorker, as if it were a phone, and report what the judge saw.

python tools/feed_video.py clip.mp4 [--line x1,y1,x2,y2 --inside x,y] [--ball x,y] [--speed 1] [--max-seconds 120]
Without --line the judge is uncalibrated (events come out as NO_CALL), but detection/tracking stats,
previews and evidence strips are still produced in server/events/feed/.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import logsetup  # noqa: E402

log = logsetup.setup("INFO", "feed.log")

from judge.detector import hsv_range_from_bgr  # noqa: E402
from pipeline import CameraWorker, Settings  # noqa: E402


def parse_pts(s: str):
    v = [float(x) for x in s.split(",")]
    return [(v[i], v[i + 1]) for i in range(0, len(v), 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--name", default="feed")
    ap.add_argument("--line", help="x1,y1,x2,y2 outer edge (far,near) in video pixels")
    ap.add_argument("--inside", help="x,y a point on the court side")
    ap.add_argument("--ball", help="x,y a pixel on the ball in the first frame, for colour")
    ap.add_argument("--ball-bgr", help="b,g,r ball colour")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--max-seconds", type=float, default=120.0)
    ap.add_argument("--save-previews", action="store_true", help="write a preview jpg every 2 s")
    args = ap.parse_args()

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if args.start:
        cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    out_dir = os.path.join(os.path.dirname(HERE), "events", "feed")
    calib_dir = os.path.join(os.path.dirname(HERE), "calib", "feed")
    os.makedirs(out_dir, exist_ok=True)
    events = []
    previews = []
    cam = CameraWorker("feed", args.name, Settings(speak=False), on_event=events.append,
                       on_preview=lambda cid, b64: previews.append(b64), events_dir=out_dir, calib_dir=calib_dir)
    log.info("video %s: %dx%d @ %.2f fps", args.video, W, H, fps)

    n = 0
    t_proc = 0.0
    blobs = 0
    tracked = 0
    first = True
    t0 = time.perf_counter()
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = n / fps
        if t > args.max_seconds:
            break
        tp0 = time.perf_counter()
        cam._process(frame, t)
        t_proc += time.perf_counter() - tp0
        n += 1
        blobs += len(cam.detector.last_mask is not None and cam.tracker.tracks)
        tracked += cam.tracker.active
        if first:
            first = False
            if args.line:
                (p1, p2) = parse_pts(args.line)
                inside = parse_pts(args.inside)[0]
                ball = parse_pts(args.ball)[0] if args.ball else None
                cam.calibrate([p1[0] * cam.scale, p1[1] * cam.scale], [p2[0] * cam.scale, p2[1] * cam.scale],
                              [inside[0] * cam.scale, inside[1] * cam.scale],
                              None if ball is None else [ball[0] * cam.scale, ball[1] * cam.scale])
            if args.ball_bgr:
                bgr = tuple(int(v) for v in args.ball_bgr.split(","))
                cam.calib.ball_bgr = bgr
                cam.detector.set_color(hsv_range_from_bgr(bgr))
        if args.save_previews and n % int(2 * fps) == 0 and previews:
            import base64
            with open(os.path.join(out_dir, f"preview_{args.name}_{int(t):04d}.jpg"), "wb") as f:
                f.write(base64.b64decode(previews[-1]))
    wall = time.perf_counter() - t0
    print(f"\n{n} frames in {wall:.1f}s wall ({n / max(wall, 1e-6):.0f} fps), judge {t_proc / max(n, 1) * 1000:.1f} ms/frame")
    print(f"tracker active on {tracked / max(n, 1):.1%} of frames; {len(events)} bounce events")
    for e in events:
        print(f"  {e.wall_time} {e.verdict:<9} gap={e.gap_cm} q={e.quality} {e.n_before}+{e.n_after} at ({e.x:.0f},{e.y:.0f}) r={e.r} {e.reason} {e.strip_url}")


if __name__ == "__main__":
    main()
