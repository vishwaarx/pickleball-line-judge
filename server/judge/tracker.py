"""Single-ball tracker that tolerates a few decoy blobs.

Keeps a handful of tentative tracks; the one that has moved consistently becomes the ball.
Association gates are measured from the LAST position with a radius of ~speed*dt so the
vertical reversal at a bounce never breaks the track.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .bounce import TrackPoint
from .detector import Candidate


@dataclass
class _Track:
    pts: deque = field(default_factory=lambda: deque(maxlen=12))
    hits: int = 0
    misses: int = 0
    updated: bool = False

    @property
    def last(self) -> TrackPoint:
        return self.pts[-1]

    def velocity(self) -> tuple[float, float]:
        if len(self.pts) < 2:
            return (0.0, 0.0)
        a, b = self.pts[-2], self.pts[-1]
        dt = b.t - a.t
        if dt <= 0:
            return (0.0, 0.0)
        return ((b.x - a.x) / dt, (b.y - a.y) / dt)

    def motion(self) -> float:
        if len(self.pts) < 2:
            return 0.0
        a, b = self.pts[0], self.pts[-1]
        return math.hypot(b.x - a.x, b.y - a.y)


class BallTracker:
    def __init__(self, confirm_hits: int = 2, max_misses: int = 4, min_motion_px: float = 6.0,
                 new_gate_px: float = 90.0, gate_floor_px: float = 50.0, max_tracks: int = 6):
        self.confirm_hits = confirm_hits
        self.max_misses = max_misses
        self.min_motion = min_motion_px
        self.new_gate = new_gate_px
        self.gate_floor = gate_floor_px
        self.max_tracks = max_tracks
        self.tracks: list[_Track] = []
        self.active_track: _Track | None = None

    @property
    def active(self) -> bool:
        return self.active_track is not None

    def reset(self) -> None:
        self.tracks.clear()
        self.active_track = None

    def update(self, cands: list[Candidate], t: float) -> TrackPoint | None:
        for tr in self.tracks:
            tr.updated = False
        used = set()
        # greedy association, best (track, candidate) pairs first
        pairs = []
        for ti, tr in enumerate(self.tracks):
            vx, vy = tr.velocity()
            dt = max(t - tr.last.t, 1e-3)
            speed = math.hypot(vx, vy)
            gate = self.new_gate if tr.hits < 2 else max(self.gate_floor, 1.3 * speed * dt + 4 * tr.last.r)
            px, py = tr.last.x + vx * dt, tr.last.y + vy * dt
            for ci, c in enumerate(cands):
                d_last = math.hypot(c.cx - tr.last.x, c.cy - tr.last.y)
                if d_last > gate:
                    continue
                d_pred = math.hypot(c.cx - px, c.cy - py)
                pairs.append((d_pred, ti, ci))
        pairs.sort()
        matched_tracks = set()
        for _d, ti, ci in pairs:
            if ti in matched_tracks or ci in used:
                continue
            tr, c = self.tracks[ti], cands[ci]
            tr.pts.append(TrackPoint(t=t, x=c.cx, y=c.cy, r=c.r))
            tr.hits += 1
            tr.misses = 0
            tr.updated = True
            matched_tracks.add(ti)
            used.add(ci)
        for ti, tr in enumerate(self.tracks):
            if ti not in matched_tracks:
                tr.misses += 1
        for ci, c in enumerate(cands):
            if ci not in used and len(self.tracks) < self.max_tracks:
                nt = _Track()
                nt.pts.append(TrackPoint(t=t, x=c.cx, y=c.cy, r=c.r))
                nt.hits = 1
                nt.updated = True
                self.tracks.append(nt)
        self.tracks = [tr for tr in self.tracks if tr.misses <= self.max_misses]
        # elect the ball: confirmed and actually moving
        eligible = [tr for tr in self.tracks if tr.hits >= self.confirm_hits and tr.motion() >= self.min_motion]
        if self.active_track in self.tracks and self.active_track in eligible:
            best = self.active_track
        else:
            best = max(eligible, key=lambda tr: (tr.hits, tr.motion()), default=None)
        self.active_track = best
        if best is not None and best.updated:
            return best.last
        return None
