"""3D replay of one bounce from a single camera, for the dashboard's Hawk-Eye-style view.

Everything comes from what the judge already has: the tracked ball centres around the bounce, the judged
contact point, the calibrated line and the paint-width scale. On a flat floor the paint's pixel width is exactly
linear in the image row, w = a*v + b, which gives the camera's horizon row (-b/a) and, with the 5.08 cm paint,
its height. The lens's field of view is the one guess.

Lateral position and height come out well. Distance ALONG the line comes only from perspective and the ball's
size, so the arc's length along the line is approximate; the contact point itself is exact to the judge.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .bounce import TrackPoint
from .geometry import BALL_RADIUS_CM, LineCal
from .linescale import LINE_WIDTH_CM, LineScale

G = 9.81
ASSUMED_HFOV_DEG = 66.0  # typical phone main camera in 16:9; a 4 degree error moves distances along the line ~7 %
BALL_R_M = BALL_RADIUS_CM / 100
PAINT_M = LINE_WIDTH_CM / 100
RADIUS_WEIGHT = 0.15  # blob radius is inflated by motion blur, so it only nudges depth along the view


@dataclass
class Camera:
    f: float
    cx: float
    cy: float
    h: float  # height above the floor, m
    pitch: float  # radians, downward
    C: np.ndarray
    ex: np.ndarray  # world directions of image right, image down and the optical axis (X right, Y up, Z ahead)
    ey: np.ndarray
    ez: np.ndarray


def camera_from_scale(scale: LineScale, size: tuple[int, int], hfov_deg: float = ASSUMED_HFOV_DEG) -> Camera | None:
    W, H = size
    if scale.a <= 0:
        return None  # the paint must widen toward the camera
    f = W / (2 * math.tan(math.radians(hfov_deg) / 2))
    cx, cy = W / 2, H / 2
    v_h = -scale.b / scale.a
    pitch = math.atan2(cy - v_h, f)
    h = PAINT_M * math.cos(pitch) / scale.a
    if not (0 < h < 10):
        return None
    return Camera(f=f, cx=cx, cy=cy, h=h, pitch=pitch, C=np.array([0.0, h, 0.0]),
                  ex=np.array([1.0, 0.0, 0.0]),
                  ey=np.array([0.0, -math.cos(pitch), -math.sin(pitch)]),
                  ez=np.array([0.0, -math.sin(pitch), math.cos(pitch)]))


def _ray(cam: Camera, u: float, v: float) -> np.ndarray:
    """Not normalised: C + s*ray is the point at optical depth s."""
    return cam.ex * (u - cam.cx) / cam.f + cam.ey * (v - cam.cy) / cam.f + cam.ez


def _floor(cam: Camera, u: float, v: float) -> np.ndarray | None:
    d = _ray(cam, u, v)
    if d[1] >= -1e-6:
        return None  # at or above the horizon
    return cam.C + (-cam.h / d[1]) * d


def flight_window(points: list[TrackPoint], t_contact: float, before: float = 0.6, after: float = 0.4,
                  max_gap: float = 0.15) -> list[TrackPoint]:
    """The run of track points around the bounce: no time gap bigger than max_gap, within the window."""
    pts = sorted((p for p in points if t_contact - before <= p.t <= t_contact + after), key=lambda p: p.t)
    if not pts:
        return []
    k = min(range(len(pts)), key=lambda i: abs(pts[i].t - t_contact))
    lo = k
    while lo > 0 and pts[lo].t - pts[lo - 1].t <= max_gap:
        lo -= 1
    hi = k
    while hi + 1 < len(pts) and pts[hi + 1].t - pts[hi].t <= max_gap:
        hi += 1
    return pts[lo:hi + 1]


def _fit_segment(cam: Camera, pts: list[TrackPoint], P_b: np.ndarray, t_b: float) -> np.ndarray | None:
    """Velocity of a ballistic flight through P_b at t_b that best meets the rays to the observed centres."""
    if len(pts) < 2:
        return None
    A, rhs = [], []
    for p in pts:
        dt = p.t - t_b
        d = _ray(cam, p.x, p.y)
        dn = d / np.linalg.norm(d)
        perp = np.eye(3) - np.outer(dn, dn)
        base = P_b + np.array([0.0, -0.5 * G * dt * dt, 0.0]) - cam.C
        A.append(perp * dt)
        rhs.append(-perp @ base)
        depth_r = cam.f * BALL_R_M / max(p.r, 0.5)
        A.append(RADIUS_WEIGHT * (cam.ez * dt)[None, :])
        rhs.append(RADIUS_WEIGHT * np.array([depth_r - cam.ez @ base]))
    vel, *_ = np.linalg.lstsq(np.vstack(A), np.concatenate(rhs), rcond=None)
    return vel if np.all(np.isfinite(vel)) else None


def reconstruct(points: list[TrackPoint], t_contact: float, contact_xy: tuple[float, float], line: LineCal,
                scale: LineScale, size: tuple[int, int], hfov_deg: float = ASSUMED_HFOV_DEG,
                samples: int = 16) -> dict | None:
    """Court-frame replay: lat (m, + = OUT side of the line's outer edge), along (m from the camera's foot), up (m)."""
    cam = camera_from_scale(scale, size, hfov_deg)
    if cam is None:
        return None
    contact = _floor(cam, *contact_xy)
    l1, l2 = _floor(cam, *line.p1), _floor(cam, *line.p2)
    if contact is None or l1 is None or l2 is None or np.linalg.norm(l2 - l1) < 1e-6:
        return None
    along = (l2 - l1) / np.linalg.norm(l2 - l1)
    if along[2] < 0:
        along = -along  # point away from the camera
    lateral = np.cross(along, [0.0, 1.0, 0.0])
    # orient lateral so + is the OUT side: probe a point a little onto the calibrated IN side of the line
    nx, ny = line._normal()
    mx, my = (line.p1[0] + line.p2[0]) / 2, (line.p1[1] + line.p2[1]) / 2
    probe = _floor(cam, mx + 15 * nx * line.in_sign, my + 15 * ny * line.in_sign)
    if probe is not None and float((probe - l1) @ lateral) > 0:
        lateral = -lateral
    foot = cam.C - np.array([0.0, cam.h, 0.0])
    origin = l1 + along * float((foot - l1) @ along)

    def court(P: np.ndarray) -> dict:
        rel = P - origin
        return {"lat": round(float(rel @ lateral), 4), "along": round(float(rel @ along), 3), "up": round(float(P[1]), 4)}

    P_b = contact + np.array([0.0, BALL_R_M, 0.0])  # the ball's centre at contact
    before = [p for p in points if p.t <= t_contact]
    after = [p for p in points if p.t > t_contact]
    v_before = _fit_segment(cam, before, P_b, t_contact)
    if v_before is None:
        return None
    v_after = _fit_segment(cam, after, P_b, t_contact)

    def at(vel: np.ndarray, dt: float) -> np.ndarray:
        return P_b + vel * dt + np.array([0.0, -0.5 * G * dt * dt, 0.0])

    def path(vel, seg) -> list[dict]:
        if vel is None:
            return []
        t0, t1 = seg[0].t - t_contact, seg[-1].t - t_contact
        return [dict(t=round(float(dt), 4), **court(at(vel, dt))) for dt in np.linspace(t0, t1, samples)]

    observed = []
    for p in points:
        dt = p.t - t_contact
        vel = v_before if dt <= 0 else v_after
        if vel is not None:
            observed.append(dict(t=round(dt, 4), **court(at(vel, dt))))
    return {
        "camera": {"height_m": round(cam.h, 3), "pitch_deg": round(math.degrees(cam.pitch), 2), "hfov_deg": hfov_deg},
        "contact": court(contact),
        "before": path(v_before, before),
        "after": path(v_after, after),
        "observed": observed,
        "speed_in": round(float(np.linalg.norm(v_before)), 2),
        "speed_out": None if v_after is None else round(float(np.linalg.norm(v_after)), 2),
    }
