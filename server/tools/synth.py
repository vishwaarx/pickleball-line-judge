"""Synthetic down-the-line court scenes with a bouncing ball, for benchmarking and fake cameras.

World frame: X lateral (metres, + = OUT side, image right), Y up, Z along the line away from
the camera. The line's OUTER edge is X=0; the paint occupies X in [-0.0508, 0] (IN side).
Camera sits on the line extension at X=0, height `cam_h`, pitched down by `pitch_deg`.
"""
from __future__ import annotations

import math
import os
import random
import subprocess
from dataclasses import dataclass, field

import cv2
import numpy as np

G = 9.81
BALL_R = 0.0375
LINE_W = 0.0508
BALL_BGR = (40, 230, 240)


@dataclass
class Scene:
    width: int = 1280
    height: int = 720
    hfov_deg: float = 66.0
    cam_h: float = 0.35
    pitch_deg: float = 6.0
    floor_bgr: tuple = (120, 110, 30)  # blue-green indoor court
    line_bgr: tuple = (240, 242, 245)
    kitchen_z: float = 7.0
    noise_sigma: float = 2.0
    blur_steps: int = 3  # sub-positions drawn per frame to mimic a 1/(2 fps) shutter
    seed: int = 0

    def __post_init__(self):
        self.f = self.width / (2 * math.tan(math.radians(self.hfov_deg) / 2))
        self.cx, self.cy = self.width / 2, self.height / 2
        th = math.radians(self.pitch_deg)
        self.F = np.array([0.0, -math.sin(th), math.cos(th)])
        self.R = np.array([1.0, 0.0, 0.0])
        self.D = np.array([0.0, -math.cos(th), -math.sin(th)])
        self.rng = random.Random(self.seed)
        self._bg = self._render_background()

    # --------------------------------------------------------------- camera
    def project(self, X: float, Y: float, Z: float):
        P = np.array([X, Y - self.cam_h, Z])
        depth = float(P @ self.F)
        if depth <= 0.05:
            return None
        u = self.cx + self.f * float(P @ self.R) / depth
        v = self.cy + self.f * float(P @ self.D) / depth
        return u, v, depth

    def ball_radius_px(self, depth: float) -> float:
        return self.f * BALL_R / depth

    def line_calibration(self):
        """(p1, p2, in_pt) in image px for the outer edge: far, near, and a point on the IN side."""
        far = self.project(0.0, 0.0, 12.0)
        near = self.project(0.0, 0.0, 1.2)
        inside = self.project(-0.5, 0.0, 4.0)
        return (far[0], far[1]), (near[0], near[1]), (inside[0], inside[1])

    # ------------------------------------------------------------ rendering
    def _poly(self, pts3d):
        pts = []
        for X, Y, Z in pts3d:
            p = self.project(X, Y, Z)
            if p is None:
                return None
            pts.append([p[0], p[1]])
        return np.array(pts, np.float32)

    SS = 4  # supersampling factor: exact box-filter anti-aliasing, no edge fattening

    def _render_background(self) -> np.ndarray:
        ss = self.SS
        big = np.empty((self.height * ss, self.width * ss, 3), np.uint8)
        big[:] = self.floor_bgr
        # far wall above the horizon
        horizon = self.project(0.0, 0.0, 400.0)
        if horizon is not None:
            big[: int(horizon[1] * ss)] = (60, 50, 45)

        def fill(pts3d):
            poly = self._poly(pts3d)
            if poly is not None:
                # pixel i is centred at i, so continuous coordinate u maps to supersampled index u*ss - 0.5
                p = np.round((poly + 0.5) * ss - 0.5).astype(np.int32)
                cv2.fillPoly(big, [p], self.line_bgr, cv2.LINE_8)

        # the judged line (outer edge at X=0, paint toward -X)
        fill([(-LINE_W, 0, 1.0), (0, 0, 1.0), (0, 0, 14.0), (-LINE_W, 0, 14.0)])
        # a crossing line (kitchen) on the IN side
        fill([(-6.1, 0, self.kitchen_z), (0, 0, self.kitchen_z),
              (0, 0, self.kitchen_z + LINE_W), (-6.1, 0, self.kitchen_z + LINE_W)])
        img = cv2.resize(big, (self.width, self.height), interpolation=cv2.INTER_AREA)
        # subtle floor texture so MOG2 has something realistic to model
        tex = np.random.default_rng(self.seed).normal(0, 4, (self.height, self.width, 1)).astype(np.float32)
        return np.clip(img.astype(np.float32) + tex, 0, 255).astype(np.uint8)

    def _draw_disc(self, img: np.ndarray, u: float, v: float, r: float, color, alpha: float) -> np.ndarray:
        """Anti-aliased disc by supersampling a small ROI; alpha blends it over the image."""
        ss = self.SS
        x0, y0 = int(math.floor(u - r - 2)), int(math.floor(v - r - 2))
        x1, y1 = int(math.ceil(u + r + 2)), int(math.ceil(v + r + 2))
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(self.width, x1), min(self.height, y1)
        if x1 <= x0 or y1 <= y0:
            return img
        roi = img[y0:y1, x0:x1]
        big = cv2.resize(roi, ((x1 - x0) * ss, (y1 - y0) * ss), interpolation=cv2.INTER_NEAREST)
        cu = int(round((u - x0 + 0.5) * ss - 0.5))
        cv_ = int(round((v - y0 + 0.5) * ss - 0.5))
        cv2.circle(big, (cu, cv_), int(round(r * ss)), color, -1, cv2.LINE_8)
        disc = cv2.resize(big, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        img[y0:y1, x0:x1] = cv2.addWeighted(disc, alpha, roi, 1 - alpha, 0)
        return img

    def frame(self, ball_positions: list[tuple[float, float, float]] | None, decoy=None) -> np.ndarray:
        img = self._bg.copy()
        if decoy is not None:
            X, Z, w, h = decoy
            # a leg: dark trousers with a pale shoe at the bottom 8 cm
            for (y0, y1, col) in ((0.08, h, (35, 30, 30)), (0.0, 0.08, (200, 205, 210))):
                quad = self._poly([(X, y0, Z), (X + w, y0, Z), (X + w, y1, Z), (X, y1, Z)])
                if quad is not None:
                    cv2.fillPoly(img, [np.round(quad * 16).astype(np.int32)], col, cv2.LINE_AA, shift=4)
        if ball_positions:
            n = len(ball_positions)
            for i, (X, Y, Z) in enumerate(ball_positions):
                p = self.project(X, Y, Z)
                if p is None:
                    continue
                u, v, depth = p
                r = self.ball_radius_px(depth)
                if r < 1:
                    continue
                alpha = 1.0 if n == 1 else 0.55 + 0.45 * (i == n - 1)
                img = self._draw_disc(img, u, v, r, BALL_BGR, alpha)
        if self.noise_sigma > 0:
            noise = np.random.default_rng(self.rng.randrange(1 << 30)).normal(0, self.noise_sigma, img.shape)
            img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)
        return img


@dataclass
class Bounce:
    """One ball flight that bounces at (Xb, Zb) at t=0."""
    Xb: float  # lateral position of the ball CENTRE at contact (m); gap = Xb - BALL_R
    Zb: float
    vX: float = 1.0  # lateral speed (m/s), + toward OUT
    vZ: float = -7.0  # along the line (m/s), - toward the camera
    v_in: float = 4.0  # downward speed at impact (m/s)
    cor: float = 0.62
    friction: float = 0.8  # horizontal speed kept after the bounce
    t_before: float = 0.35
    t_after: float = 0.30

    @property
    def gap_m(self) -> float:
        return self.Xb - BALL_R

    def position(self, t: float):
        if t < 0:
            h = -self.v_in * t - 0.5 * G * t * t
            X = self.Xb + self.vX * t
            Z = self.Zb + self.vZ * t
        else:
            h = self.cor * self.v_in * t - 0.5 * G * t * t
            X = self.Xb + self.friction * self.vX * t
            Z = self.Zb + self.friction * self.vZ * t
        if h < 0:
            return None
        return X, BALL_R + h, Z


def expected_verdict(gap_m: float, margin_m: float = 0.02) -> str:
    if gap_m <= 0:
        return "IN"
    if gap_m < margin_m:
        return "TOO_CLOSE"
    return "OUT"


def foot_decoy(t: float, X: float = 0.25, Z: float = 3.0):
    """A dark leg near the line that walks: lifts 12 cm for 0.25 s every 0.8 s, drifts 0.4 m/s along Z."""
    phase = (t + 10.0) % 0.8
    lift = 0.12 if phase < 0.25 else 0.0
    return (X, Z + 0.4 * t, 0.10, 0.45 + lift)  # X, Z, width, height; drawn standing on the floor


def render_bounce(scene: Scene, b: Bounce, fps: float, phase: float = 0.0, lead_frames: int = 0, decoy=None):
    """Yield (t, frame) with the bounce at t=0. lead_frames of empty floor first for the background model.

    decoy: optional callable t -> (X, Z, w, h) for a moving obstacle (see foot_decoy)."""
    dt = 1.0 / fps
    t = -b.t_before - lead_frames * dt + phase * dt
    for _ in range(lead_frames):
        yield t, scene.frame(None, decoy=decoy(t) if decoy else None)
        t += dt
    while t <= b.t_after:
        positions = []
        if scene.blur_steps > 1:
            # sub-positions across the open-shutter half of the frame interval
            for k in range(scene.blur_steps):
                ts = t - (dt / 2) * (1 - k / (scene.blur_steps - 1))
                p = b.position(ts)
                if p is not None:
                    positions.append(p)
        else:
            p = b.position(t)
            if p is not None:
                positions.append(p)
        yield t, scene.frame(positions or None, decoy=decoy(t) if decoy else None)
        t += dt


def write_y4m(frames, path: str, fps: int, size: tuple[int, int]) -> None:
    """Uncompressed 4:2:0 Y4M, the format Chrome's --use-file-for-fake-video-capture accepts."""
    w, h = size
    with open(path, "wb") as f:
        f.write(f"YUV4MPEG2 W{w} H{h} F{fps}:1 Ip A1:1 C420jpeg\n".encode())
        for img in frames:
            yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV_I420)
            f.write(b"FRAME\n")
            f.write(yuv.tobytes())


def write_mp4(frames, path: str, fps: int, size: tuple[int, int]) -> None:
    w, h = size
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
           "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", path]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for img in frames:
        p.stdin.write(img.tobytes())
    p.stdin.close()
    p.wait()


def demo_sequence(scene: Scene, fps: int, bounces: list[Bounce], gap_s: float = 1.2, lead_s: float = 2.0):
    """A clip with several bounces separated by empty floor; returns list of frames and truth."""
    frames = []
    truth = []
    dt = 1.0 / fps
    n_lead = int(lead_s * fps)
    for _ in range(n_lead):
        frames.append(scene.frame(None))
    for b in bounces:
        start = len(frames)
        for t, fr in render_bounce(scene, b, fps):
            frames.append(fr)
        truth.append({"start_frame": start, "end_frame": len(frames), "gap_cm": b.gap_m * 100,
                      "expected": expected_verdict(b.gap_m), "Zb": b.Zb, "vX": b.vX, "vZ": b.vZ})
        for _ in range(int(gap_s * fps)):
            frames.append(scene.frame(None))
    return frames, truth


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="write a synthetic demo clip")
    ap.add_argument("out", help="output .y4m or .mp4")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    scene = Scene(width=args.width, height=args.height, seed=args.seed)
    gaps_cm = [-6, -1, 0.5, 1.5, 3, 5, 8, -4, 2.5, 12]
    bounces = []
    rnd = random.Random(args.seed)
    for g in gaps_cm:
        bounces.append(Bounce(Xb=g / 100 + BALL_R, Zb=rnd.choice([2.5, 4.0, 6.0]), vX=rnd.choice([0.5, 2.0, 3.5]),
                              vZ=rnd.choice([-7.0, -5.0, 6.0]), v_in=rnd.choice([3.5, 5.0])))
    frames, truth = demo_sequence(scene, args.fps, bounces)
    if args.out.endswith(".y4m"):
        write_y4m(frames, args.out, args.fps, (args.width, args.height))
    else:
        write_mp4(frames, args.out, args.fps, (args.width, args.height))
    with open(os.path.splitext(args.out)[0] + ".truth.json", "w") as f:
        json.dump({"fps": args.fps, "calibration": scene.line_calibration(), "bounces": truth}, f, indent=1)
    print(f"wrote {args.out}: {len(frames)} frames, {len(bounces)} bounces")
