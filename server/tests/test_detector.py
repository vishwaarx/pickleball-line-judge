import cv2
import numpy as np
import pytest

from judge.detector import BallDetector, hsv_range_from_bgr

YELLOW = (40, 230, 240)  # BGR
FLOORS = {
    "dark": (30, 30, 30),
    "blue": (150, 70, 20),
    "green": (40, 120, 30),
}


def frame_with_ball(floor_bgr, cx, cy, r=9, w=640, h=360):
    img = np.empty((h, w, 3), np.uint8)
    img[:] = floor_bgr
    # a white court line to make sure it is never detected as a ball
    cv2.line(img, (300, 0), (330, h), (235, 235, 235), 6)
    if cx is not None:
        cv2.circle(img, (int(round(cx)), int(round(cy))), r, YELLOW, -1, lineType=cv2.LINE_AA)
    return img


@pytest.mark.parametrize("floor", list(FLOORS))
def test_detects_moving_yellow_ball_on_each_floor(floor):
    det = BallDetector(color_range=hsv_range_from_bgr(YELLOW))
    bg = frame_with_ball(FLOORS[floor], None, None)
    for _ in range(30):
        det.detect(bg)
    hits = []
    for i in range(10):
        cx, cy = 100 + 25 * i, 200 - 6 * i
        cands = det.detect(frame_with_ball(FLOORS[floor], cx, cy))
        assert cands, f"no candidate at step {i}"
        best = cands[0]
        hits.append((abs(best.cx - cx), abs(best.cy - cy), best.r))
    for dx, dy, r in hits:
        assert dx <= 1.0 and dy <= 1.0
        assert 7.0 <= r <= 11.0


def test_motion_only_mode_still_finds_the_ball():
    det = BallDetector(color_range=None)
    bg = frame_with_ball(FLOORS["dark"], None, None)
    for _ in range(30):
        det.detect(bg)
    cands = det.detect(frame_with_ball(FLOORS["dark"], 200, 150))
    assert cands and abs(cands[0].cx - 200) <= 1.0


def test_large_moving_blob_is_rejected():
    det = BallDetector(color_range=None)
    bg = frame_with_ball(FLOORS["dark"], None, None)
    for _ in range(30):
        det.detect(bg)
    img = bg.copy()
    cv2.rectangle(img, (100, 100), (220, 300), (200, 200, 200), -1)  # a leg
    assert det.detect(img) == []


def test_hsv_range_from_bgr_brackets_the_colour():
    lo, hi = hsv_range_from_bgr(YELLOW)
    h, s, v = cv2.cvtColor(np.uint8([[YELLOW]]), cv2.COLOR_BGR2HSV)[0, 0]
    assert lo[0] <= h <= hi[0]
    assert lo[1] <= s and lo[2] <= v
