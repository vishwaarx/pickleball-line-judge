"""Line geometry and the in/out decision.

All coordinates are image pixels at full capture resolution, y growing downward.
The calibrated line is the OUTER edge of the painted line (court boundary, rule 3.A.2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

BALL_RADIUS_CM = 3.7  # 7.3–7.5 cm diameter per USAP equipment standards
DEFAULT_MARGIN_CM = 2.0


class Verdict(str, Enum):
    IN = "IN"
    OUT = "OUT"
    TOO_CLOSE = "TOO_CLOSE"  # inside the margin -> treated as IN (rule 8.D)
    NO_CALL = "NO_CALL"


@dataclass
class LineCal:
    p1: tuple[float, float]
    p2: tuple[float, float]
    in_sign: float = 1.0  # multiply raw distance by this so positive == IN side

    def _normal(self) -> tuple[float, float]:
        dx = self.p2[0] - self.p1[0]
        dy = self.p2[1] - self.p1[1]
        length = math.hypot(dx, dy)
        if length == 0:
            raise ValueError("line points coincide")
        return (-dy / length, dx / length)

    def raw_distance(self, x: float, y: float) -> float:
        nx, ny = self._normal()
        return (x - self.p1[0]) * nx + (y - self.p1[1]) * ny

    def set_in_side(self, x: float, y: float) -> None:
        """Declare which side of the line is the court, from a click at (x, y)."""
        self.in_sign = 1.0 if self.raw_distance(x, y) >= 0 else -1.0

    def x_at_row(self, y: float) -> float:
        if self.p1[1] == self.p2[1]:
            return (self.p1[0] + self.p2[0]) / 2
        t = (y - self.p1[1]) / (self.p2[1] - self.p1[1])
        return self.p1[0] + t * (self.p2[0] - self.p1[0])

    def to_dict(self) -> dict:
        return {"p1": list(self.p1), "p2": list(self.p2), "in_sign": self.in_sign}

    @classmethod
    def from_dict(cls, d: dict) -> "LineCal":
        return cls(p1=tuple(d["p1"]), p2=tuple(d["p2"]), in_sign=float(d.get("in_sign", 1.0)))


def signed_distance(line: LineCal, x: float, y: float) -> float:
    """Perpendicular distance from (x, y) to the line; positive on the IN side."""
    return line.in_sign * line.raw_distance(x, y)


@dataclass
class ContactJudgement:
    verdict: Verdict
    d_px: float  # signed distance of the contact point, + = IN side
    gap_px: float  # visible gap between ball silhouette and line edge (+ = daylight on the OUT side)
    gap_cm: float
    cm_per_px: float
    margin_px: float
    r_px: float
    notes: list[str] = field(default_factory=list)


def judge_contact(line: LineCal, x: float, y_contact: float, r: float,
                  margin_px: float | None = None, margin_cm: float | None = None) -> ContactJudgement:
    """Decide IN / OUT / TOO_CLOSE for a ball whose contact point is (x, y_contact) with radius r px.

    Rule 8.E: "out" only when there is clearly visible space between line and ball. So:
    gap <= 0 (silhouette touches or overlaps the boundary)  -> IN
    0 < gap < margin                                        -> TOO_CLOSE (treated as IN)
    gap >= margin                                           -> OUT
    """
    if r <= 0:
        raise ValueError("ball radius must be positive")
    cm_per_px = BALL_RADIUS_CM / r
    if margin_px is None:
        margin_px = (DEFAULT_MARGIN_CM if margin_cm is None else margin_cm) / cm_per_px
    d = signed_distance(line, x, y_contact)
    gap = -d - r
    if gap <= 0:
        verdict = Verdict.IN
    elif gap < margin_px:
        verdict = Verdict.TOO_CLOSE
    else:
        verdict = Verdict.OUT
    return ContactJudgement(
        verdict=verdict, d_px=d, gap_px=gap, gap_cm=gap * cm_per_px,
        cm_per_px=cm_per_px, margin_px=margin_px, r_px=r,
    )
