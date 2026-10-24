import math

import pytest

from judge.geometry import LineCal, Verdict, judge_contact, signed_distance


def vertical_line_in_left():
    # outer edge of the line runs straight down the image at x=100; IN side is to the left
    line = LineCal(p1=(100.0, 0.0), p2=(100.0, 720.0))
    line.set_in_side(50.0, 360.0)
    return line


def test_signed_distance_positive_on_in_side():
    line = vertical_line_in_left()
    assert signed_distance(line, 80.0, 300.0) == pytest.approx(20.0)
    assert signed_distance(line, 120.0, 300.0) == pytest.approx(-20.0)
    assert signed_distance(line, 100.0, 10.0) == pytest.approx(0.0)


def test_in_side_can_be_flipped_by_click():
    line = LineCal(p1=(100.0, 0.0), p2=(100.0, 720.0))
    line.set_in_side(150.0, 360.0)
    assert signed_distance(line, 120.0, 300.0) == pytest.approx(20.0)


def test_slanted_line_uses_perpendicular_distance():
    # 45 degree line through origin; point (10, 0) is 10/sqrt(2) away
    line = LineCal(p1=(0.0, 0.0), p2=(100.0, 100.0))
    line.set_in_side(0.0, 100.0)  # above-left is IN
    d = signed_distance(line, 10.0, 0.0)
    assert abs(d) == pytest.approx(10.0 / math.sqrt(2))
    assert d < 0  # (10,0) lies below-right of the line, so OUT side


def test_x_on_line_at_row_for_receding_line():
    line = LineCal(p1=(300.0, 700.0), p2=(340.0, 100.0))
    assert line.x_at_row(400.0) == pytest.approx(320.0)


@pytest.mark.parametrize(
    "cx, r, margin_px, expected",
    [
        (80.0, 10.0, 6.0, Verdict.IN),  # fully inside
        (105.0, 10.0, 6.0, Verdict.IN),  # centre just out but silhouette overlaps the line
        (110.0, 10.0, 6.0, Verdict.IN),  # edge exactly on the line counts as touching
        (113.0, 10.0, 6.0, Verdict.TOO_CLOSE),  # 3 px gap, inside the margin
        (118.0, 10.0, 6.0, Verdict.OUT),  # 8 px gap, clear
        (113.0, 10.0, 2.0, Verdict.OUT),  # tighter margin makes the same gap callable
    ],
)
def test_verdict_bands(cx, r, margin_px, expected):
    line = vertical_line_in_left()
    res = judge_contact(line, cx, 500.0, r, margin_px=margin_px)
    assert res.verdict == expected


def test_gap_is_reported_in_pixels_and_cm():
    line = vertical_line_in_left()
    # r = 10 px for a 3.7 cm radius ball -> 0.37 cm per px; 8 px gap = 2.96 cm
    res = judge_contact(line, 118.0, 500.0, 10.0, margin_px=6.0)
    assert res.gap_px == pytest.approx(8.0)
    assert res.gap_cm == pytest.approx(2.96, abs=0.01)
    assert res.cm_per_px == pytest.approx(0.37)


def test_margin_can_be_given_in_cm():
    line = vertical_line_in_left()
    # 2 cm margin at 0.37 cm/px = 5.4 px; a 5 px gap is TOO CLOSE, 6 px is OUT
    assert judge_contact(line, 115.0, 500.0, 10.0, margin_cm=2.0).verdict == Verdict.TOO_CLOSE
    assert judge_contact(line, 116.0, 500.0, 10.0, margin_cm=2.0).verdict == Verdict.OUT
