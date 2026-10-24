"""Bounce detection from a tracked ball path.

Image y grows downward, so a ground bounce is a local MAXIMUM of y: the ball descends
(y increasing), reverses, ascends (y decreasing). At 30–60 fps the contact frame is usually
missed, so the contact time is recovered by fitting the descending and ascending paths and
intersecting them (the same idea Hawk-Eye uses with 50/60 Hz cameras).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np


@dataclass
class TrackPoint:
    t: float
    x: float
    y: float
    r: float


@dataclass
class BounceEvent:
    t: float
    x: float
    y: float  # ball centre row at contact
    y_contact: float  # centre + r: where the ball meets the floor
    r: float
    quality: str  # "good" | "low"
    n_before: int
    n_after: int
    points: list[TrackPoint] = field(default_factory=list)
    note: str = ""


def _fit_line(ts: np.ndarray, ys: np.ndarray) -> tuple[float, float]:
    a, b = np.polyfit(ts, ys, 1)
    return float(a), float(b)


def _fit_shared_curvature(td: np.ndarray, yd: np.ndarray, ta: np.ndarray, ya: np.ndarray):
    """y = a t^2 + b_d t + c_d on the descending side, a t^2 + b_a t + c_a on the ascending side."""
    rows = []
    rhs = []
    for t, y in zip(td, yd):
        rows.append([t * t, t, 1.0, 0.0, 0.0])
        rhs.append(y)
    for t, y in zip(ta, ya):
        rows.append([t * t, 0.0, 0.0, t, 1.0])
        rhs.append(y)
    sol, *_ = np.linalg.lstsq(np.array(rows), np.array(rhs), rcond=None)
    a, bd, cd, ba, ca = (float(v) for v in sol)
    return a, bd, cd, ba, ca


class BounceDetector:
    def __init__(self, eps_px: float = 0.6, min_travel_px: float = 3.0, max_gap_s: float = 0.2,
                 window: int = 14, fit_pts: int = 4, dwell_eps_px: float = 1.5, max_dwell: int = 2):
        self.eps = eps_px
        self.min_travel = min_travel_px
        self.max_gap = max_gap_s
        self.window = window
        self.fit_pts = fit_pts
        self.dwell_eps = dwell_eps_px
        self.max_dwell = max_dwell  # a ball touches for <5 ms; a foot sits on the floor for many frames
        self.hist: deque[TrackPoint] = deque(maxlen=64)
        self._emitted_apex_t: float | None = None

    def reset(self) -> None:
        self.hist.clear()
        self._emitted_apex_t = None

    # ------------------------------------------------------------------ public
    def update(self, p: TrackPoint) -> BounceEvent | None:
        if self.hist and p.t - self.hist[-1].t > self.max_gap:
            self.reset()
        self.hist.append(p)
        return self._check(final=False)

    def flush(self) -> BounceEvent | None:
        """Call when the track is lost; may emit a low-quality event for a half-seen bounce."""
        ev = self._check(final=True)
        self.reset()
        return ev

    # ----------------------------------------------------------------- private
    def _runs(self, k: int, ys: list[float]) -> tuple[int, int]:
        s = k
        while s - 1 >= 0 and ys[s] - ys[s - 1] >= -self.eps:
            s -= 1
        e = k
        while e + 1 < len(ys) and ys[e + 1] - ys[e] <= self.eps:
            e += 1
        return s, e

    def _check(self, final: bool) -> BounceEvent | None:
        pts = list(self.hist)[-self.window:]
        n = len(pts)
        if n < 3:
            return None
        ys = [p.y for p in pts]
        k = int(np.argmax(ys))
        apex_t = pts[k].t
        if self._emitted_apex_t is not None and abs(apex_t - self._emitted_apex_t) < 1e-9:
            return None
        s, e = self._runs(k, ys)
        n_before, n_after = k - s, e - k
        rise, drop = ys[k] - ys[s], ys[k] - ys[e]
        if rise < self.min_travel or drop < self.min_travel:
            return None
        if n_before < 1 or n_after < 1:
            return None
        dwell = sum(1 for y in ys[s:e + 1] if ys[k] - y <= self.dwell_eps)
        if dwell > self.max_dwell:
            return None  # something rested on the floor: a shoe, not a ball
        good = n_before >= 2 and n_after >= 2
        if not good and not final:
            return None  # wait for more points unless the track has ended
        # keep only the last point of the ascending run as "e" if the ball keeps flying upward
        ev = self._solve(pts, s, k, e, good)
        if ev is not None:
            self._emitted_apex_t = apex_t
        return ev

    def _solve(self, pts: list[TrackPoint], s: int, k: int, e: int, good: bool) -> BounceEvent | None:
        seg = pts[s:e + 1]
        t0 = seg[0].t
        T = np.array([p.t - t0 for p in seg])
        Y = np.array([p.y for p in seg])
        X = np.array([p.x for p in seg])
        ka = k - s  # apex index inside seg
        best = None
        # the contact lies between the last descending and first ascending sample; the apex
        # sample may belong to either side, so try both splits and keep the better fit
        for m in (ka - 1, ka):
            d_idx = list(range(max(0, m + 1 - self.fit_pts), m + 1))
            a_idx = list(range(m + 1, min(len(seg), m + 1 + self.fit_pts)))
            if len(d_idx) < 2 or len(a_idx) < 2:
                continue
            td, yd, ta, ya = T[d_idx], Y[d_idx], T[a_idx], Y[a_idx]
            if len(d_idx) + len(a_idx) >= 6:
                a, bd, cd, ba, ca = _fit_shared_curvature(td, yd, ta, ya)
                if bd - ba == 0:
                    continue
                ts = (ca - cd) / (bd - ba)
                y_star = a * ts * ts + bd * ts + cd
                res = float(np.sum((a * td**2 + bd * td + cd - yd) ** 2) + np.sum((a * ta**2 + ba * ta + ca - ya) ** 2))
                slope_ok = (2 * a * td[-1] + bd) > 0 and (2 * a * ta[0] + ba) < 0
            else:
                ad, bd = _fit_line(td, yd)
                aa, ba = _fit_line(ta, ya)
                if ad - aa == 0:
                    continue
                ts = (ba - bd) / (ad - aa)
                y_star = ad * ts + bd
                res = float(np.sum((ad * td + bd - yd) ** 2) + np.sum((aa * ta + ba - ya) ** 2))
                slope_ok = ad > 0 and aa < 0
            if not slope_ok:
                continue
            lo, hi = td[-1], ta[0]
            span = hi - lo
            if not (lo - 0.6 * span <= ts <= hi + 0.6 * span):
                continue
            if best is None or res < best[0]:
                best = (res, ts, y_star, d_idx, a_idx)
        if best is None:
            if not good:
                p = pts[k]
                return BounceEvent(t=p.t, x=p.x, y=p.y, y_contact=p.y + p.r, r=p.r, quality="low",
                                   n_before=k - s, n_after=e - k, points=seg, note="apex only")
            return None
        _, ts, y_star, d_idx, a_idx = best
        # x is continuous through the bounce: fit each side and average at ts
        xs = []
        for idx in (d_idx, a_idx):
            if len(idx) >= 2:
                ax, bx = _fit_line(T[idx], X[idx])
                xs.append(ax * ts + bx)
        x_star = float(np.mean(xs)) if xs else float(X[ka])
        near = sorted(seg, key=lambda p: abs((p.t - t0) - ts))[:3]
        r_star = float(np.median([p.r for p in near]))
        quality = "good" if good else "low"
        return BounceEvent(
            t=float(ts + t0), x=x_star, y=float(y_star), y_contact=float(y_star + r_star), r=r_star,
            quality=quality, n_before=len(d_idx), n_after=len(a_idx), points=seg,
        )
