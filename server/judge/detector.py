"""Ball candidates from motion (MOG2 background subtraction) optionally gated by colour."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Candidate:
    cx: float
    cy: float
    r: float  # half of the blob's minor axis: a streaked ball still reports its true radius
    area: float
    bbox: tuple[float, float, float, float]
    fill: float = 1.0


def hsv_range_from_bgr(bgr, h_tol: int = 12, s_frac: float = 0.45, v_frac: float = 0.45):
    h, s, v = (int(c) for c in cv2.cvtColor(np.uint8([[list(bgr)]]), cv2.COLOR_BGR2HSV)[0, 0])
    lo = (max(0, h - h_tol), max(40, int(s * s_frac)), max(50, int(v * v_frac)))
    hi = (min(180, h + h_tol), 255, 255)
    return lo, hi


class BallDetector:
    def __init__(self, color_range=None, min_r: float = 2.5, max_r: float = 45.0,
                 history: int = 150, var_threshold: float = 24.0, max_aspect: float = 4.5,
                 min_fill: float = 0.3):
        self.color_range = color_range
        self.min_r, self.max_r = min_r, max_r
        self.max_aspect, self.min_fill = max_aspect, min_fill
        self.mog = cv2.createBackgroundSubtractorMOG2(history=history, varThreshold=var_threshold,
                                                      detectShadows=False)
        self.kernel = np.ones((3, 3), np.uint8)
        self.last_mask: np.ndarray | None = None

    def set_color(self, color_range) -> None:
        self.color_range = color_range

    def detect(self, frame_bgr: np.ndarray) -> list[Candidate]:
        fg = self.mog.apply(frame_bgr)
        if self.color_range is not None:
            hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
            cm = cv2.inRange(hsv, np.array(self.color_range[0], np.uint8), np.array(self.color_range[1], np.uint8))
            cm = cv2.dilate(cm, self.kernel)
            fg = cv2.bitwise_and(fg, cm)
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, self.kernel)
        self.last_mask = fg
        n, _labels, stats, cents = cv2.connectedComponentsWithStats(fg, connectivity=8)
        out: list[Candidate] = []
        for i in range(1, n):
            x, y, w, h, area = (float(v) for v in stats[i])
            r = min(w, h) / 2.0
            if r < self.min_r or r > self.max_r:
                continue
            if max(w, h) / max(min(w, h), 1.0) > self.max_aspect:
                continue
            fill = area / max(w * h, 1.0)
            if fill < self.min_fill:
                continue
            out.append(Candidate(cx=float(cents[i][0]), cy=float(cents[i][1]), r=r, area=area,
                                 bbox=(x, y, w, h), fill=fill))
        out.sort(key=lambda c: (-c.fill * min(c.area, 400.0)))
        return out
