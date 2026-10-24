import numpy as np
import pytest

from judge.geometry import LineCal
from judge.linescale import LINE_WIDTH_CM, fit_scale, measure_line, snap_line
from tools.synth import LINE_W, Scene


def scene_gray():
    sc = Scene(noise_sigma=1.0, seed=3)
    img = sc.frame(None)
    import cv2
    return sc, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def test_measured_width_matches_projection():
    sc, gray = scene_gray()
    p1, p2, in_pt = sc.line_calibration()
    line = LineCal(p1=p1, p2=p2)
    line.set_in_side(*in_pt)
    samples = measure_line(gray, line)
    assert len(samples) >= 10
    for s in samples:
        # ground truth width at this row: project both paint edges at the depth that lands on row s.y
        # cheaper: scan depths and find the one whose projection matches s.y
        best = None
        for Z in np.linspace(1.0, 14.0, 2601):
            p = sc.project(0.0, 0.0, Z)
            if p is None:
                continue
            if best is None or abs(p[1] - s.y) < best[0]:
                best = (abs(p[1] - s.y), Z)
        Z = best[1]
        outer = sc.project(0.0, 0.0, Z)
        inner = sc.project(-LINE_W, 0.0, Z)
        truth = abs(outer[0] - inner[0])
        assert s.width_px == pytest.approx(truth, abs=0.7), f"row {s.y:.0f}: {s.width_px:.2f} vs {truth:.2f}"
        assert abs(s.outer_offset_px) < 0.8


def test_scale_fit_gives_cm_per_px_within_5_percent():
    sc, gray = scene_gray()
    p1, p2, in_pt = sc.line_calibration()
    line = LineCal(p1=p1, p2=p2)
    line.set_in_side(*in_pt)
    scale = fit_scale(measure_line(gray, line))
    assert scale is not None
    for Z in (2.5, 4.0, 6.0):
        u, v, depth = sc.project(0.0, 0.0, Z)
        truth = 100.0 / (sc.f / depth)  # cm per px at that depth
        assert scale.cm_per_px(v) == pytest.approx(truth, rel=0.05)


def test_snap_recovers_edge_from_offset_clicks():
    sc, gray = scene_gray()
    p1, p2, in_pt = sc.line_calibration()
    # the user clicked 4 px to the IN side of the true edge at both ends
    line = LineCal(p1=(p1[0] - 4, p1[1]), p2=(p2[0] - 4, p2[1]))
    line.set_in_side(*in_pt)
    samples = measure_line(gray, line)
    snapped = snap_line(line, samples)
    assert snapped is not None
    assert snapped.p1[0] == pytest.approx(p1[0], abs=0.6)
    assert snapped.p2[0] == pytest.approx(p2[0], abs=0.6)
    # IN side preserved
    assert snapped.in_sign * snapped.raw_distance(*in_pt) > 0


def test_no_line_means_no_scale():
    gray = np.full((360, 640), 90, np.uint8)
    line = LineCal(p1=(320.0, 10.0), p2=(320.0, 350.0))
    line.set_in_side(100.0, 100.0)
    assert measure_line(gray, line) == []
    assert fit_scale([]) is None
