"""Pixel scale from the painted line itself.

Court lines are 2 in (5.08 cm) wide (rule 3.A.4.e). Measuring the paint's pixel width at several
rows gives cm-per-pixel as a function of image row, independent of the (blurred) ball blob.
For a flat floor the lateral pixel scale is linear in the row, so two parameters fit it.
The same profiles locate the paint's outer edge, which lets us snap a hand-clicked line to it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import LineCal

LINE_WIDTH_CM = 5.08


@dataclass
class WidthSample:
    x: float
    y: float
    width_px: float
    outer_offset_px: float  # where the paint's outer edge sits relative to the clicked line (+ = IN side)
    contrast: float


@dataclass
class LineScale:
    a: float  # width_px = a * y + b
    b: float
    n: int
    rms: float

    def width_px(self, y: float) -> float:
        return self.a * y + self.b

    def cm_per_px(self, y: float) -> float | None:
        w = self.width_px(y)
        if w < 1.5:
            return None
        return LINE_WIDTH_CM / w

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "n": self.n, "rms": self.rms}

    @classmethod
    def from_dict(cls, d: dict | None) -> "LineScale | None":
        if not d:
            return None
        return cls(a=float(d["a"]), b=float(d["b"]), n=int(d.get("n", 0)), rms=float(d.get("rms", 0)))


def _sample_profile(gray: np.ndarray, px: float, py: float, nx: float, ny: float, s_lo: int, s_hi: int) -> np.ndarray:
    s = np.arange(s_lo, s_hi + 1, dtype=np.float32)
    xs = (px + s * nx).astype(np.float32)
    ys = (py + s * ny).astype(np.float32)
    prof = cv2.remap(gray, xs.reshape(1, -1), ys.reshape(1, -1), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return prof.reshape(-1).astype(np.float32)


def measure_line(gray: np.ndarray, line: LineCal, n_samples: int = 16, s_out: int = 10, s_in: int = 48,
                 min_contrast: float = 20.0) -> list[WidthSample]:
    """Profiles perpendicular to the calibrated outer edge; paint is expected on the IN side (s > 0)."""
    nx, ny = line._normal()
    nx, ny = nx * line.in_sign, ny * line.in_sign  # unit normal pointing to the IN side
    out: list[WidthSample] = []
    H, W = gray.shape[:2]
    for f in np.linspace(0.06, 0.94, n_samples):
        px = line.p1[0] + f * (line.p2[0] - line.p1[0])
        py = line.p1[1] + f * (line.p2[1] - line.p1[1])
        if not (0 <= px < W and 0 <= py < H):
            continue
        prof = _sample_profile(gray, px, py, nx, ny, -s_out, s_in)
        s = np.arange(-s_out, s_in + 1)
        floor_in = np.median(prof[int(0.65 * len(prof)):])
        floor_out = np.median(prof[: max(2, s_out // 2)])
        floor = min(floor_in, floor_out) if abs(floor_in - floor_out) < 40 else floor_in
        near = prof[s_out - 4: s_out + 20]  # paint should start near s=0
        paint = float(np.max(near))
        contrast = paint - floor
        if contrast < min_contrast:
            continue
        thr = floor + 0.5 * contrast
        above = prof >= thr
        # first run of "above" that touches the neighbourhood of s=0
        idx = np.where(above)[0]
        if idx.size == 0:
            continue
        # pick the run containing the brightest near sample
        peak = int(np.argmax(near)) + (s_out - 4)
        if not above[peak]:
            continue
        lo = peak
        while lo - 1 >= 0 and above[lo - 1]:
            lo -= 1
        hi = peak
        while hi + 1 < len(prof) and above[hi + 1]:
            hi += 1
        if lo == 0 or hi == len(prof) - 1:
            continue  # band runs off the profile: not a line edge
        # sub-pixel crossings by linear interpolation
        def cross(i0, i1):
            v0, v1 = prof[i0], prof[i1]
            if v1 == v0:
                return float(s[i0])
            return float(s[i0] + (thr - v0) / (v1 - v0) * (s[i1] - s[i0]))
        e_outer = cross(lo - 1, lo)
        e_inner = cross(hi, hi + 1)
        width = e_inner - e_outer
        if width < 1.5 or width > 40:
            continue
        out.append(WidthSample(x=px, y=py, width_px=width, outer_offset_px=e_outer, contrast=contrast))
    return out


def fit_scale(samples: list[WidthSample], min_samples: int = 5) -> LineScale | None:
    if len(samples) < min_samples:
        return None
    ys = np.array([s.y for s in samples])
    ws = np.array([s.width_px for s in samples])
    if np.ptp(ys) < 20:
        a, b = 0.0, float(np.median(ws))
        rms = float(np.sqrt(np.mean((ws - b) ** 2)))
        return LineScale(a=a, b=b, n=len(samples), rms=rms)
    a, b = np.polyfit(ys, ws, 1)
    pred = a * ys + b
    res = ws - pred
    # one pass of outlier rejection
    keep = np.abs(res) <= max(1.0, 2.5 * np.std(res))
    if keep.sum() >= min_samples and keep.sum() < len(ys):
        a, b = np.polyfit(ys[keep], ws[keep], 1)
        pred = a * ys[keep] + b
        res = ws[keep] - pred
    rms = float(np.sqrt(np.mean(res ** 2)))
    if np.any(pred < 1.0):
        return None
    return LineScale(a=float(a), b=float(b), n=int(keep.sum()), rms=rms)


def snap_line(line: LineCal, samples: list[WidthSample], max_offset_px: float = 6.0, min_fraction: float = 0.6,
              n_expected: int = 16) -> LineCal | None:
    """Move the clicked line onto the measured outer edge. Returns a new LineCal or None if unsure."""
    if len(samples) < max(4, int(min_fraction * n_expected)):
        return None
    offs = np.array([s.outer_offset_px for s in samples])
    if abs(float(np.median(offs))) > max_offset_px:
        return None
    nx, ny = line._normal()
    nx, ny = nx * line.in_sign, ny * line.in_sign
    pts = np.array([[s.x + s.outer_offset_px * nx, s.y + s.outer_offset_px * ny] for s in samples])
    # total least squares line through the refined edge points
    mean = pts.mean(axis=0)
    _, _, vt = np.linalg.svd(pts - mean)
    d = vt[0]
    # keep the user's endpoints' extent along the direction
    def proj(p):
        t = float(np.dot(np.array(p) - mean, d))
        return (float(mean[0] + t * d[0]), float(mean[1] + t * d[1]))
    new = LineCal(p1=proj(line.p1), p2=proj(line.p2), in_sign=line.in_sign)
    # make sure the IN side sign still means the same half-plane
    probe = (line.p1[0] + 30 * nx, line.p1[1] + 30 * ny)
    if new.raw_distance(*probe) * new.in_sign < 0:
        new.in_sign = -new.in_sign
    return new
