"""Per-camera processing: frames in, verdict events + previews out.

One CameraWorker thread per phone. Frames arrive from aiortc (av.VideoFrame) or from a test
feeder (numpy array). All judge coordinates are in the worker's "work" resolution, which is
the capture resolution downscaled to at most WORK_W wide.
"""
from __future__ import annotations

import base64
import collections
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime

import cv2
import numpy as np

import confirm_jev
from judge.bounce import BounceDetector, BounceEvent, TrackPoint
from judge.detector import BallDetector, hsv_range_from_bgr
from judge.geometry import BALL_RADIUS_CM, LineCal, Verdict, judge_contact
from judge.linescale import LineScale, fit_scale, measure_line, snap_line
from judge.tracker import BallTracker

log = logging.getLogger("pickle.cam")

WORK_W = 1280
RING_FRAMES = 150  # 2.5 s at 60 fps; ~1.4 GB for three 720p cameras
PREVIEW_W = 480
PREVIEW_INTERVAL = 0.25
GUARD_INTERVAL = 2.0

COLOR_LINE = (80, 255, 120)
COLOR_IN_TICK = (80, 255, 120)
COLOR_TRAIL = (60, 240, 255)
COLOR_CONTACT = (255, 80, 255)
VERDICT_BGR = {
    Verdict.IN: (90, 200, 60),
    Verdict.OUT: (60, 60, 240),
    Verdict.TOO_CLOSE: (40, 170, 255),
    Verdict.NO_CALL: (150, 150, 150),
}
VERDICT_LABEL = {
    Verdict.IN: "IN",
    Verdict.OUT: "OUT",
    Verdict.TOO_CLOSE: "TOO CLOSE - IN",
    Verdict.NO_CALL: "NO CALL",
}


@dataclass
class Settings:
    margin_cm: float = 2.0
    announce_window_cm: float = 15.0  # IN bounces farther inside than this are logged, not spoken
    speak: bool = True
    announce_in: bool = True
    low_quality_call_cm: float = 5.0  # with <2 frames on a side, still call if the gap is this clear

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, type(getattr(self, k))(v))


@dataclass
class Calibration:
    line: LineCal | None = None
    ball_bgr: tuple | None = None
    ref_rect: tuple | None = None  # x, y, w, h of the guard patch at work scale
    scale: LineScale | None = None  # cm/px from the paint width, by row
    snapped: bool = False
    work_size: tuple[int, int] | None = None  # resolution the line/scale are expressed in

    def to_dict(self) -> dict:
        return {
            "line": self.line.to_dict() if self.line else None,
            "ball_bgr": list(self.ball_bgr) if self.ball_bgr else None,
            "scale": self.scale.to_dict() if self.scale else None,
            "snapped": self.snapped,
            "work_size": list(self.work_size) if self.work_size else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        c = cls()
        if d.get("line"):
            c.line = LineCal.from_dict(d["line"])
        if d.get("ball_bgr"):
            c.ball_bgr = tuple(int(v) for v in d["ball_bgr"])
        c.scale = LineScale.from_dict(d.get("scale"))
        c.snapped = bool(d.get("snapped", False))
        if d.get("work_size"):
            c.work_size = (int(d["work_size"][0]), int(d["work_size"][1]))
        return c


@dataclass
class CallEvent:
    cam_id: str
    cam_name: str
    wall_time: str
    verdict: str
    label: str
    gap_cm: float | None
    gap_px: float | None
    quality: str
    n_before: int
    n_after: int
    fps_in: float
    x: float
    y: float
    r: float
    announce: bool
    reason: str = ""
    strip_url: str = ""
    confirmation: dict | None = None
    seq: int = 0
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:10])


class CameraWorker(threading.Thread):
    def __init__(self, cam_id: str, name: str, settings: Settings, on_event, on_preview,
                 events_dir: str, calib_dir: str, speaker=None):
        super().__init__(daemon=True, name=f"cam-{name}")
        self.cam_id = cam_id
        self.name_label = name
        self.settings = settings
        self.on_event = on_event
        self.on_preview = on_preview
        self.events_dir = events_dir
        self.calib_dir = calib_dir
        self.speaker = speaker

        self._frames: collections.deque = collections.deque(maxlen=4)
        self._cv = threading.Condition()
        self._stop_evt = threading.Event()
        self._lock = threading.RLock()  # calibration state: HTTP handlers write it, the worker reads it

        self.detector = BallDetector()
        self.tracker = BallTracker()
        self.bounce = BounceDetector()
        self.calib = Calibration()
        self.ref_patch: np.ndarray | None = None
        self.scale = 1.0
        self.capture_size: tuple[int, int] | None = None
        self.work_size: tuple[int, int] | None = None

        self.ring: collections.deque = collections.deque(maxlen=RING_FRAMES)
        self.trail: collections.deque = collections.deque(maxlen=60)
        self.last_contact: tuple[float, float, float, float] | None = None  # t, x, y, r

        self.last_frame_wall = 0.0
        self.last_preview = 0.0
        self.last_guard = 0.0
        self.last_t: float | None = None
        self.moved_shift = 0.0
        self.moved = False
        self.moved_reason = ""
        self.guard_low_streak = 0
        self._saved_work: tuple[int, int] | None = None
        self._verify_pending = False  # a calibration loaded from disk must prove it matches the view
        self._snapshot_frame: np.ndarray | None = None
        self.fps_in = 0.0
        self.fps_proc = 0.0
        self._in_count = 0
        self._proc_count = 0
        self._cand_count = 0
        self._size_rejects = 0
        self._stat_ticks = 0
        self._rate_t0 = time.monotonic()
        self.phone_settings: dict = {}
        self.seq = 0
        self._was_active = False
        self._scale_pending = False
        self._load_calibration()

    # ------------------------------------------------------------ frame input
    def push(self, frame, t: float | None = None) -> None:
        with self._cv:
            self._frames.append((frame, t))
            self._in_count += 1
            self.last_frame_wall = time.monotonic()
            self._cv.notify()

    def stop(self) -> None:
        self._stop_evt.set()
        with self._cv:
            self._cv.notify_all()

    # --------------------------------------------------------------- status
    @property
    def status(self) -> str:
        if time.monotonic() - self.last_frame_wall > 1.5:
            return "NO SIGNAL"
        if self.moved:
            return "MOVED"
        return "LIVE"

    def state(self) -> dict:
        return {
            "id": self.cam_id,
            "name": self.name_label,
            "status": self.status,
            "calibrated": self.calib.line is not None,
            "ball_color": self.calib.ball_bgr is not None,
            "fps_in": round(self.fps_in, 1),
            "fps_proc": round(self.fps_proc, 1),
            "capture": list(self.capture_size) if self.capture_size else None,
            "work": list(self.work_size) if self.work_size else None,
            "moved_shift": round(self.moved_shift, 1),
            "moved_reason": self.moved_reason if self.moved else "",
            "paint_scale": self.calib.scale is not None,
            "ball_bgr": list(self.calib.ball_bgr) if self.calib.ball_bgr else None,
            "phone": self.phone_settings,
            "line": self.calib.line.to_dict() if self.calib.line else None,
        }

    # ----------------------------------------------------------- calibration
    def _calib_path(self) -> str:
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.name_label)
        return os.path.join(self.calib_dir, f"{safe}.json")

    def _load_calibration(self) -> None:
        p = self._calib_path()
        if not os.path.exists(p):
            return
        try:
            with open(p) as f:
                loaded = Calibration.from_dict(json.load(f))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("[%s] ignoring unreadable calibration %s: %r", self.name_label, p, exc)
            return
        if loaded.line is not None and loaded.work_size is None:
            log.warning("[%s] ignoring calibration without a resolution stamp: %s", self.name_label, p)
            return
        with self._lock:
            self.calib = loaded
            self._saved_work = loaded.work_size
            if self.calib.ball_bgr:
                self.detector.set_color(hsv_range_from_bgr(self.calib.ball_bgr))
            self.ref_patch = None  # rebuilt from the next frame
            # trust nothing from disk until the paint is found where the file says it is
            self._verify_pending = self.calib.line is not None
        log.info("[%s] loaded calibration from %s (recorded at %s); will verify against the live view",
                 self.name_label, p, loaded.work_size)

    def _save_calibration(self) -> None:
        os.makedirs(self.calib_dir, exist_ok=True)
        with self._lock:
            self.calib.work_size = self.work_size or self._saved_work
            data = json.dumps(self.calib.to_dict(), indent=1)
        tmp = self._calib_path() + ".tmp"
        with open(tmp, "w") as f:
            f.write(data)
        os.replace(tmp, self._calib_path())

    def calibrate(self, p1, p2, in_pt, ball_pt=None, coords: str = "work", view=None) -> dict:
        """Points are (x, y) in the snapshot the operator clicked on.

        `view` is that snapshot's (width, height); if the stream changed resolution since the snapshot
        was taken, the points are scaled onto the current work size.
        """
        if self.work_size is None:
            raise RuntimeError("no frames yet")
        if coords == "work":
            sx = sy = 1.0
            if view and view[0] and view[1]:
                sx, sy = self.work_size[0] / float(view[0]), self.work_size[1] / float(view[1])
        else:
            sx = sy = self.scale
        line = LineCal(p1=(p1[0] * sx, p1[1] * sy), p2=(p2[0] * sx, p2[1] * sy))
        line.set_in_side(in_pt[0] * sx, in_pt[1] * sy)
        with self._lock:
            self.calib.line = line
            if ball_pt is not None:
                # sample the frame the operator actually clicked on, not whatever is live now
                frame = self._snapshot_frame if self._snapshot_frame is not None else self._latest_work_frame()
                if frame is not None:
                    fh, fw = frame.shape[:2]
                    bx = int(ball_pt[0] * (fw / float(view[0]) if view and view[0] else sx))
                    by = int(ball_pt[1] * (fh / float(view[1]) if view and view[1] else sy))
                    x0, x1 = max(0, bx - 2), min(fw, bx + 3)
                    y0, y1 = max(0, by - 2), min(fh, by + 3)
                    patch = frame[y0:y1, x0:x1].reshape(-1, 3)
                    if patch.size:
                        bgr = tuple(int(v) for v in np.median(patch, axis=0))
                        self.calib.ball_bgr = bgr
                        self.detector.set_color(hsv_range_from_bgr(bgr))
            self.ref_patch = None
            self.moved = False
            self.moved_reason = ""
            self.moved_shift = 0.0
            self.guard_low_streak = 0
            self.calib.scale = None
            self.calib.snapped = False
            self._verify_pending = False
            self._measure_line()
            self._scale_pending = self.calib.scale is None  # too few frames yet: measure once the ring fills
            self._save_calibration()
            log.info("[%s] calibrated line %s -> %s in_sign=%+d ball=%s scale=%s snapped=%s view=%s work=%s",
                     self.name_label, tuple(round(v) for v in self.calib.line.p1),
                     tuple(round(v) for v in self.calib.line.p2), int(self.calib.line.in_sign), self.calib.ball_bgr,
                     None if not self.calib.scale else f"{self.calib.scale.b:.1f}+{self.calib.scale.a:.4f}y px",
                     self.calib.snapped, view, self.work_size)
            return self.calib.to_dict()

    def _rescale_calibration(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        rx, ry = new[0] / old[0], new[1] / old[1]
        with self._lock:
            if abs(rx / ry - 1.0) > 0.02:
                # aspect changed (rotation, 4:3 <-> 16:9): a pure rescale would be wrong
                self.moved = True
                self.moved_reason = "camera aspect changed - calibrate again"
                self.moved_shift = 99.0
                log.warning("[%s] work size %s -> %s changes aspect; calls paused until recalibration",
                            self.name_label, old, new)
                return
            line = self.calib.line
            if line is not None:
                line.p1 = (line.p1[0] * rx, line.p1[1] * ry)
                line.p2 = (line.p2[0] * rx, line.p2[1] * ry)
            if self.calib.scale is not None:
                # width w = a*y + b in old px; in new px: w' = rx*w, y' = ry*y
                self.calib.scale = LineScale(a=self.calib.scale.a * rx / ry, b=self.calib.scale.b * rx,
                                             n=self.calib.scale.n, rms=self.calib.scale.rms * rx)
            self.calib.ref_rect = None
            self.moved = False
            self.moved_reason = ""
            self.moved_shift = 0.0
            self.guard_low_streak = 0
            self._scale_pending = self.calib.line is not None  # re-measure the paint at the new resolution
            self._save_calibration()
        log.info("[%s] work size %s -> %s: calibration rescaled (x%.2f, x%.2f)", self.name_label, old, new, rx, ry)

    def _background_gray(self):
        """Median of a few spread-out ring frames, so a ball or a foot does not count as paint."""
        if not self.ring:
            return None
        frames = list(self.ring)
        step = max(1, len(frames) // 7)
        picks = [cv2.cvtColor(f[1], cv2.COLOR_BGR2GRAY) for f in frames[::step][-7:]]
        if len(picks) == 1:
            return picks[0]
        return np.median(np.stack(picks), axis=0).astype(np.uint8)

    def _measure_line(self, min_frames: int = 8) -> bool:
        """Snap the clicked line to the paint edge and fit the cm/px scale from the paint width.

        Returns True when the paint was found along the line. Never discards a scale we already have."""
        with self._lock:
            line = self.calib.line
            if line is None or len(self.ring) < min_frames:
                return False
            gray = self._background_gray()
            samples = measure_line(gray, line)
            snapped = snap_line(line, samples)
            if snapped is not None:
                shift = max(abs(snapped.p1[0] - line.p1[0]), abs(snapped.p2[0] - line.p2[0]),
                            abs(snapped.p1[1] - line.p1[1]), abs(snapped.p2[1] - line.p2[1]))
                self.calib.line = snapped
                self.calib.snapped = True
                samples = measure_line(gray, snapped)
                log.info("[%s] snapped clicked line to paint edge (max shift %.1f px, %d samples)",
                         self.name_label, shift, len(samples))
            scale = fit_scale(samples)
            if scale is None:
                log.warning("[%s] could not measure the paint width (%d usable rows); %s", self.name_label,
                            len(samples), "keeping previous scale" if self.calib.scale else "using ball size as scale")
            else:
                self.calib.scale = scale
                mid_y = (self.calib.line.p1[1] + self.calib.line.p2[1]) / 2
                log.info("[%s] paint width %.1f px at mid-row (%d rows, rms %.2f px) -> %.2f cm/px",
                         self.name_label, scale.width_px(mid_y), scale.n, scale.rms, scale.cm_per_px(mid_y) or -1)
            return snapped is not None

    def clear_ball_color(self) -> None:
        with self._lock:
            self.calib.ball_bgr = None
            self.detector.set_color(None)
            self._save_calibration()

    def rearm_guard(self) -> None:
        with self._lock:
            self.ref_patch = None
            self.moved = False
            self.moved_reason = ""
            self.moved_shift = 0.0
            self.guard_low_streak = 0

    def _latest_work_frame(self):
        return self.ring[-1][1] if self.ring else None

    def snapshot_jpeg(self, quality: int = 90) -> bytes | None:
        frame = self._latest_work_frame()
        if frame is None:
            return None
        self._snapshot_frame = frame  # the frame the operator will click on
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    # ------------------------------------------------------------------ loop
    def run(self) -> None:
        while not self._stop_evt.is_set():
            with self._cv:
                while not self._frames and not self._stop_evt.is_set():
                    self._cv.wait(timeout=0.5)
                if self._stop_evt.is_set():
                    return
                frame, t = self._frames.popleft()
            try:
                self._process(frame, t)
            except Exception:  # keep the camera alive on a bad frame
                log.exception("[%s] frame error", self.name_label)

    def _to_bgr(self, frame):
        if isinstance(frame, np.ndarray):
            return frame
        return frame.to_ndarray(format="bgr24")

    def _frame_time(self, frame, t):
        if t is not None:
            return t
        ft = getattr(frame, "time", None)
        return float(ft) if ft is not None else time.monotonic()

    def _process(self, frame, t) -> None:
        t = self._frame_time(frame, t)
        if self.last_t is not None and t < self.last_t - 1.0:
            # a new WebRTC connection restarts the stream clock: never mix time bases
            log.info("[%s] stream clock restarted (%.1f -> %.1f); clearing track state", self.name_label, self.last_t, t)
            self.tracker.reset()
            self.bounce.reset()
            self.ring.clear()
            self.trail.clear()
            self._was_active = False
        self.last_t = t
        img = self._to_bgr(frame)
        H, W = img.shape[:2]
        if self.capture_size != (W, H):
            old_work = self.work_size or self._saved_work  # a saved line is in the resolution it was saved at
            self.capture_size = (W, H)
            self.scale = min(1.0, WORK_W / W)
            self.work_size = (int(round(W * self.scale)), int(round(H * self.scale)))
            self.tracker.reset()
            self.bounce.reset()
            self.ring.clear()
            self.trail.clear()
            self.ref_patch = None
            if old_work is not None and tuple(old_work) != self.work_size:
                # WebRTC ramps resolution up after connecting: carry the calibration across
                self._rescale_calibration(tuple(old_work), self.work_size)
            log.info("[%s] capture %dx%d -> work %dx%d (scale %.3f)", self.name_label, W, H,
                     self.work_size[0], self.work_size[1], self.scale)
        work = img if self.scale == 1.0 else cv2.resize(img, self.work_size, interpolation=cv2.INTER_AREA)
        self.ring.append((t, work))

        if (self._scale_pending or self._verify_pending) and len(self.ring) >= 30 and self.calib.line is not None:
            verifying = self._verify_pending
            self._scale_pending = self._verify_pending = False
            found = self._measure_line()
            if verifying and not found:
                with self._lock:
                    self.moved = True
                    self.moved_reason = "saved calibration does not match this view - calibrate again"
                    self.moved_shift = 99.0
                log.warning("[%s] saved calibration rejected: no paint edge where it says", self.name_label)
            self._save_calibration()

        cands = self.detector.detect(work)
        if self.calib.scale is not None and cands:
            # A ball on the floor at row y has radius r_exp(y); an airborne ball at that row is CLOSER
            # than the floor there, so it can only look bigger. Lower bound: half the floor radius
            # (thresholding shrinks blobs). Upper bound: the biggest ball possible, which is one on the
            # floor at the bottom of the frame, with slack for motion blur. Rejects legs, torsos, noise.
            cpp_near = self.calib.scale.cm_per_px(work.shape[0] - 1)
            r_max = (BALL_RADIUS_CM / cpp_near) * 1.8 if cpp_near else None
            kept = []
            for c in cands:
                cpp = self.calib.scale.cm_per_px(c.cy)
                r_min = 0.5 * BALL_RADIUS_CM / cpp if cpp else 0.0
                if c.r >= r_min and (r_max is None or c.r <= r_max):
                    kept.append(c)
            self._size_rejects += len(cands) - len(kept)
            cands = kept
        tp = self.tracker.update(cands, t)
        ev: BounceEvent | None = None
        if tp is not None:
            self.trail.append(tp)
            ev = self.bounce.update(tp)
            self._was_active = True
        elif self._was_active and not self.tracker.active:
            ev = self.bounce.flush()
            self._was_active = False
        if ev is not None:
            self._handle_bounce(ev)

        now = time.monotonic()
        self._proc_count += 1
        self._cand_count += len(cands)
        if now - self._rate_t0 >= 1.0:
            dt = now - self._rate_t0
            self.fps_in = self._in_count / dt
            self.fps_proc = self._proc_count / dt
            self._stat_ticks += 1
            if self._stat_ticks % 5 == 0:
                log.debug("[%s] in %.1f fps, judged %.1f fps, %.2f blobs/frame (%d wrong-size), track %s, guard shift %.1f",
                          self.name_label, self.fps_in, self.fps_proc,
                          self._cand_count / max(self._proc_count, 1), self._size_rejects, self.tracker.active,
                          self.moved_shift)
            self._in_count = 0
            self._proc_count = 0
            self._cand_count = 0
            self._size_rejects = 0
            self._rate_t0 = now
        if now - self.last_guard >= GUARD_INTERVAL and self.calib.line is not None:
            self.last_guard = now
            self._guard(work)
        if now - self.last_preview >= PREVIEW_INTERVAL:
            self.last_preview = now
            self._emit_preview(work)

    # ----------------------------------------------------------------- judge
    def _handle_bounce(self, ev: BounceEvent) -> None:
        with self._lock:  # one consistent view of line/scale/moved for this call
            line = self.calib.line
            scale = self.calib.scale
            moved, moved_reason = self.moved, self.moved_reason
        self.seq += 1
        wall = datetime.now().strftime("%H:%M:%S")
        r_eff, y_contact = ev.r, ev.y_contact
        if line is None or moved:
            verdict, gap_cm, gap_px = Verdict.NO_CALL, None, None
            reason = (moved_reason or "camera moved - recalibrate") if moved else "not calibrated"
        else:
            # radius from the paint-width scale when we have it: a blurred blob looks bigger than the ball
            scale_src = "blob"
            if scale is not None:
                cpp = scale.cm_per_px(ev.y)
                if cpp is not None:
                    r_eff, scale_src = BALL_RADIUS_CM / cpp, "paint"
            y_contact = ev.y + r_eff
            j = judge_contact(line, ev.x, y_contact, r_eff, margin_cm=self.settings.margin_cm)
            verdict, gap_cm, gap_px = j.verdict, j.gap_cm, j.gap_px
            reason = ""
            log.debug("[%s] radius blob %.1f px vs %s %.1f px (%.3f cm/px)", self.name_label, ev.r, scale_src,
                      r_eff, j.cm_per_px)
            if ev.quality == "low":
                clear = self.settings.low_quality_call_cm
                if gap_cm >= clear:
                    reason = "few frames, but gap is clear"
                elif gap_cm <= -clear:
                    verdict, reason = Verdict.IN, "few frames, but clearly inside"
                else:
                    verdict, reason = Verdict.NO_CALL, f"too few frames around the bounce ({self.fps_in:.0f} fps)"
        self.last_contact = (time.monotonic(), ev.x, y_contact, r_eff)
        announce = verdict in (Verdict.OUT, Verdict.TOO_CLOSE) or (
            verdict == Verdict.IN and gap_cm is not None and -gap_cm <= self.settings.announce_window_cm
        ) or (verdict == Verdict.NO_CALL and line is not None and not moved)  # silent when simply not set up
        if verdict == Verdict.IN and not self.settings.announce_in:
            announce = False
        call = CallEvent(
            cam_id=self.cam_id, cam_name=self.name_label, wall_time=wall, verdict=verdict.value,
            label=VERDICT_LABEL[verdict], gap_cm=None if gap_cm is None else round(gap_cm, 1),
            gap_px=None if gap_px is None else round(gap_px, 1), quality=ev.quality,
            n_before=ev.n_before, n_after=ev.n_after, fps_in=round(self.fps_in, 1),
            x=round(ev.x, 1), y=round(y_contact, 1), r=round(r_eff, 1), announce=announce,
            reason=reason, seq=self.seq,
        )
        log.info("[%s] %s gap=%s cm (%s px) quality=%s frames=%d+%d fps=%.0f at (%.0f,%.0f) r=%.1f (blob %.1f) %s",
                 self.name_label, call.verdict, call.gap_cm, call.gap_px, call.quality, call.n_before,
                 call.n_after, self.fps_in, ev.x, y_contact, r_eff, ev.r, call.reason)
        if announce and self.settings.speak and self.speaker is not None:
            self.speaker.say(self._phrase(call))  # speak first; the strip takes ~50 ms to write
        zooms, contact_idx = [], 0
        try:
            call.strip_url, zooms, contact_idx = self._write_evidence(ev, call)
        except Exception:
            log.exception("[%s] evidence error", self.name_label)
        if zooms and confirm_jev.available() and verdict != Verdict.NO_CALL:
            def _confirmed(result, call=call):
                if result is not None:
                    call.confirmation = result
                    self.on_event(call)  # same cam/uid: the dashboard patches the existing row
            confirm_jev.ask_async(zooms, contact_idx, self.name_label, _confirmed)
        self.on_event(call)

    def _phrase(self, call: CallEvent) -> str:
        name = self.name_label
        if call.verdict == Verdict.OUT.value:
            cm = f", by {call.gap_cm:.0f} centimetres" if call.gap_cm is not None else ""
            return f"Out, {name}{cm}."
        if call.verdict == Verdict.TOO_CLOSE.value:
            return f"Too close to call, {name}. In."
        if call.verdict == Verdict.NO_CALL.value:
            return f"No call, {name}."
        return f"In, {name}."

    # -------------------------------------------------------------- evidence
    def _draw_line(self, img, thickness=2) -> None:
        line = self.calib.line
        if line is None:
            return
        p1 = (int(round(line.p1[0])), int(round(line.p1[1])))
        p2 = (int(round(line.p2[0])), int(round(line.p2[1])))
        cv2.line(img, p1, p2, COLOR_LINE, thickness, cv2.LINE_AA)
        # ticks on the IN side so the side is visible on every frame
        nx, ny = line._normal()
        nx, ny = nx * line.in_sign, ny * line.in_sign
        for f in (0.2, 0.4, 0.6, 0.8):
            x = p1[0] + f * (p2[0] - p1[0])
            y = p1[1] + f * (p2[1] - p1[1])
            cv2.line(img, (int(x), int(y)), (int(x + 14 * nx), int(y + 14 * ny)), COLOR_IN_TICK, 2, cv2.LINE_AA)

    def _draw_trail(self, img, upto_t: float | None = None) -> None:
        pts = [p for p in self.trail if upto_t is None or p.t <= upto_t + 1e-6]
        for i, p in enumerate(pts[-30:]):
            cv2.circle(img, (int(round(p.x)), int(round(p.y))), 3, COLOR_TRAIL, -1, cv2.LINE_AA)

    def _draw_contact(self, img, x, y, r, verdict: Verdict | None = None) -> None:
        xi, yi = int(round(x)), int(round(y))
        col = VERDICT_BGR.get(verdict, COLOR_CONTACT) if verdict else COLOR_CONTACT
        cv2.circle(img, (xi, int(round(y - r))), int(round(r)), col, 2, cv2.LINE_AA)
        cv2.line(img, (xi - 10, yi), (xi + 10, yi), col, 2, cv2.LINE_AA)
        cv2.line(img, (xi, yi - 10), (xi, yi + 10), col, 2, cv2.LINE_AA)

    def _write_evidence(self, ev: BounceEvent, call: CallEvent) -> tuple[str, list, int]:
        if not self.ring:
            return "", [], 0
        frames = list(self.ring)
        times = np.array([f[0] for f in frames])
        k = int(np.argmin(np.abs(times - ev.t)))
        idxs = [i for i in range(k - 2, k + 3) if 0 <= i < len(frames)]
        contact_idx = idxs.index(k) if k in idxs else 0
        verdict = Verdict(call.verdict)
        tiles, zooms = [], []
        tile_w = 384
        for i in idxs:
            t_i, fr = frames[i]
            img = fr.copy()
            self._draw_line(img)
            self._draw_trail(img, upto_t=t_i)
            self._draw_contact(img, call.x, call.y, call.r, verdict)  # the judged geometry, not the raw blob
            ms = (t_i - ev.t) * 1000
            cv2.putText(img, f"{ms:+.0f} ms", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 4, cv2.LINE_AA)
            cv2.putText(img, f"{ms:+.0f} ms", (12, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
            h, w = img.shape[:2]
            tiles.append(cv2.resize(img, (tile_w, int(tile_w * h / w)), interpolation=cv2.INTER_AREA))
            half = int(max(60, 4 * call.r))
            cx, cy = int(round(call.x)), int(round(call.y - call.r))
            x0, y0 = max(0, cx - half), max(0, cy - half)
            x1, y1 = min(w, cx + half), min(h, cy + half)
            crop = img[y0:y1, x0:x1]
            if crop.size:
                zooms.append(cv2.resize(crop, (tile_w, tile_w), interpolation=cv2.INTER_CUBIC))
            else:
                zooms.append(np.zeros((tile_w, tile_w, 3), np.uint8))
        if not tiles:
            return "", [], 0
        th = tiles[0].shape[0]
        tiles = [cv2.resize(tl, (tile_w, th)) for tl in tiles]
        row1 = np.hstack(tiles)
        row2 = np.hstack(zooms)
        header = np.zeros((64, row1.shape[1], 3), np.uint8)
        header[:] = VERDICT_BGR[verdict]
        gap = "" if call.gap_cm is None else f"   gap {call.gap_cm:+.1f} cm"
        text = f"{call.label}   {self.name_label}{gap}   {call.wall_time}   {call.fps_in:.0f} fps   {call.quality}"
        cv2.putText(header, text, (16, 44), cv2.FONT_HERSHEY_DUPLEX, 1.2, (255, 255, 255), 2, cv2.LINE_AA)
        strip = np.vstack([header, row1, row2])
        day = datetime.now().strftime("%Y-%m-%d")
        d = os.path.join(self.events_dir, day)
        os.makedirs(d, exist_ok=True)
        safe = "".join(ch if ch.isalnum() else "_" for ch in self.name_label)
        base = f"{datetime.now().strftime('%H%M%S')}_{safe}_{call.verdict}_{self.seq}"
        cv2.imwrite(os.path.join(d, base + ".jpg"), strip, [cv2.IMWRITE_JPEG_QUALITY, 85])
        meta = asdict(call)
        meta["line"] = self.calib.line.to_dict() if self.calib.line else None
        meta["points"] = [asdict(p) for p in ev.points]
        with open(os.path.join(d, base + ".json"), "w") as f:
            json.dump(meta, f, indent=1)
        return f"/events/{day}/{base}.jpg", zooms, contact_idx

    # ------------------------------------------------------------- previews
    def _emit_preview(self, work: np.ndarray) -> None:
        img = work.copy()
        self._draw_line(img)
        self._draw_trail(img)
        if self.last_contact is not None and time.monotonic() - self.last_contact[0] < 4.0:
            _, x, y, r = self.last_contact
            self._draw_contact(img, x, y, r)
        h, w = img.shape[:2]
        small = cv2.resize(img, (PREVIEW_W, int(PREVIEW_W * h / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            self.on_preview(self.cam_id, base64.b64encode(buf.tobytes()).decode("ascii"))

    # ---------------------------------------------------------- moved guard
    def _guard(self, work: np.ndarray) -> None:
        """Has the phone been bumped? Template-match a band around the line against its reference.

        A straight line is ambiguous along its own direction (aperture problem), so only the shift
        across the line counts, and zero shift wins whenever it matches about as well as the best."""
        with self._lock:
            line = self.calib.line
            if line is None or self.moved:
                return
            gray = self._background_gray() if len(self.ring) >= 7 else cv2.cvtColor(work, cv2.COLOR_BGR2GRAY)
            H, W = gray.shape
            pad = 24
            x0 = int(max(0, min(line.p1[0], line.p2[0]) - pad))
            x1 = int(min(W, max(line.p1[0], line.p2[0]) + pad))
            y0 = int(max(0, min(line.p1[1], line.p2[1]) - pad))
            y1 = int(min(H, max(line.p1[1], line.p2[1]) + pad))
            if x1 - x0 < 16 or y1 - y0 < 16:
                return
            if self.ref_patch is None:
                self.ref_patch = gray[y0:y1, x0:x1].copy()
                self.calib.ref_rect = (x0, y0, x1 - x0, y1 - y0)
                return
            rx, ry, rw, rh = self.calib.ref_rect
            if self.ref_patch.shape != (rh, rw):
                self.ref_patch = None
                return
            search = 20
            sx0, sy0 = max(0, rx - search), max(0, ry - search)
            sx1, sy1 = min(W, rx + rw + search), min(H, ry + rh + search)
            win = gray[sy0:sy1, sx0:sx1]
            if win.shape[0] < rh or win.shape[1] < rw:
                return
            res = cv2.matchTemplate(win, self.ref_patch, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            zx, zy = rx - sx0, ry - sy0  # where "no shift" sits in the result map
            score0 = float(res[zy, zx]) if 0 <= zy < res.shape[0] and 0 <= zx < res.shape[1] else -1.0
            if score < 0.45:
                self.guard_low_streak += 1
                if self.guard_low_streak >= 6:  # ~12 s of not finding the line where it was
                    log.warning("[%s] guard: line not found for %d checks (score %.2f) -> MOVED",
                                self.name_label, self.guard_low_streak, score)
                    self.moved = True
                    self.moved_reason = "line not found where it was - calibrate again"
                    self.moved_shift = 99.0
                return
            self.guard_low_streak = 0
            if score - score0 < 0.02:
                self.moved_shift = 0.0  # the old position explains the view just as well
                return
            dx = (sx0 + loc[0]) - rx
            dy = (sy0 + loc[1]) - ry
            nx, ny = line._normal()
            across = dx * nx + dy * ny  # only motion across the line is observable or matters
            self.moved_shift = abs(float(across))
            if self.moved_shift <= 2.0:
                return
            if self.moved_shift <= 15.0:
                # small bump: slide the calibration across by the observed amount
                mx, my = across * nx, across * ny
                line.p1 = (line.p1[0] + mx, line.p1[1] + my)
                line.p2 = (line.p2[0] + mx, line.p2[1] + my)
                nrx, nry = int(round(rx + mx)), int(round(ry + my))
                if 0 <= nrx and nrx + rw <= W and 0 <= nry and nry + rh <= H:
                    self.calib.ref_rect = (nrx, nry, rw, rh)
                    self.ref_patch = gray[nry:nry + rh, nrx:nrx + rw].copy()
                else:
                    self.ref_patch = None
                self._save_calibration()
                log.info("[%s] guard: slid the line %.1f px across after a small bump", self.name_label, across)
                return
            self.moved = True
            self.moved_reason = "camera moved - calibrate again"
            log.warning("[%s] guard: line shifted %.1f px across -> MOVED", self.name_label, across)


class Speaker:
    """macOS `say`, serialized so verdicts never talk over each other."""

    def __init__(self, voice: str | None = None, rate: int = 200):
        import queue
        import subprocess

        self._q: "queue.Queue[str]" = queue.Queue()
        self._voice, self._rate = voice, rate
        self._subprocess = subprocess

        def loop():
            while True:
                text = self._q.get()
                cmd = ["say", "-r", str(self._rate)]
                if self._voice:
                    cmd += ["-v", self._voice]
                try:
                    self._subprocess.run(cmd + [text], check=False, timeout=15)
                except Exception:
                    log.exception("say failed")

        threading.Thread(target=loop, daemon=True, name="speaker").start()

    def say(self, text: str) -> None:
        if self._q.qsize() < 3:
            self._q.put(text)
