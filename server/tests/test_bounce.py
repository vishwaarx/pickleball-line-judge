import random

import pytest

from judge.bounce import BounceDetector, TrackPoint

G = 9.81


def synth_track(fps, v_in=5.0, cor=0.65, vx_mps=3.0, px_per_m=400.0,
                x0=640.0, y_ground=600.0, r_px=9.0, phase=0.0, noise=0.5, seed=1,
                t_before=0.20, t_after=0.20):
    """Ball bounce at t=0 at image x0 on the ground row y_ground (centre row = y_ground - r).

    Image y grows downward. height h(t): falling h = -v_in*t - g t^2/2 (t<0),
    rising h = cor*v_in*t - g t^2/2 (t>0). Horizontal motion is uniform.
    """
    rnd = random.Random(seed)
    dt = 1.0 / fps
    pts = []
    t = -t_before + phase * dt
    while t <= t_after:
        h = (-v_in * t - 0.5 * G * t * t) if t < 0 else (cor * v_in * t - 0.5 * G * t * t)
        if h >= 0:
            x = x0 + vx_mps * t * px_per_m + rnd.uniform(-noise, noise)
            y = (y_ground - r_px) - h * px_per_m + rnd.uniform(-noise, noise)
            pts.append(TrackPoint(t=t + 10.0, x=x, y=y, r=r_px))  # shift t so it is never negative
        t += dt
    return pts


def run(pts):
    det = BounceDetector()
    events = []
    for p in pts:
        ev = det.update(p)
        if ev is not None:
            events.append(ev)
    return events


@pytest.mark.parametrize("fps", [30, 60])
@pytest.mark.parametrize("phase", [0.0, 0.3, 0.7])
def test_bounce_position_recovered_within_tolerance(fps, phase):
    pts = synth_track(fps=fps, phase=phase)
    events = run(pts)
    assert len(events) == 1, f"expected exactly one bounce, got {len(events)}"
    ev = events[0]
    assert ev.quality == "good"
    assert ev.x == pytest.approx(640.0, abs=2.0)
    assert ev.t == pytest.approx(10.0, abs=0.006)
    # contact row = centre apex + r
    assert ev.y_contact == pytest.approx(600.0, abs=3.0)
    assert ev.r == pytest.approx(9.0, abs=1.0)


def test_fast_ball_with_one_frame_each_side_is_low_quality():
    # 30 fps but only one sample on each side of the bounce reaches the detector
    pts = synth_track(fps=30, t_before=0.05, t_after=0.05, phase=0.5)
    assert len(pts) <= 4
    events = run(pts)
    if events:
        assert events[0].quality == "low"


def test_arc_apex_in_flight_is_not_a_bounce():
    # ball flying over the frame: y decreases then increases (a minimum in image y) -> no bounce
    det = BounceDetector()
    pts = []
    for i in range(20):
        t = i / 30.0
        h = 1.0 + 4.0 * t - 0.5 * G * t * t  # up then down
        pts.append(TrackPoint(t=t, x=100 + 20 * i, y=600 - 300 * h, r=8))
    assert all(det.update(p) is None for p in pts)


def test_rolling_ball_does_not_trigger():
    det = BounceDetector()
    for i in range(30):
        assert det.update(TrackPoint(t=i / 30.0, x=100 + 5 * i, y=590 + random.Random(i).uniform(-0.4, 0.4), r=8)) is None


def test_foot_resting_on_floor_is_not_a_bounce():
    det = BounceDetector()
    ys = [500, 540, 580, 600, 600.4, 599.8, 600.2, 580, 540, 500]  # down, sits for 4 frames, up
    for i, y in enumerate(ys):
        assert det.update(TrackPoint(t=i / 30.0, x=300 + 2 * i, y=y, r=10)) is None


def test_fast_ball_touching_for_one_frame_still_counts():
    det = BounceDetector()
    ys = [420, 480, 540, 600, 560, 520, 480]  # one frame at the bottom
    events = [det.update(TrackPoint(t=i / 30.0, x=300 + 20 * i, y=y, r=9)) for i, y in enumerate(ys)]
    assert any(e is not None for e in events)


def test_same_bounce_not_reported_twice():
    pts = synth_track(fps=60, t_after=0.4)
    assert len(run(pts)) == 1


def test_second_bounce_after_gap_is_reported():
    first = synth_track(fps=60)
    second = [TrackPoint(t=p.t + 2.0, x=p.x + 50, y=p.y, r=p.r) for p in synth_track(fps=60, seed=2)]
    det = BounceDetector()
    events = [e for p in first + second if (e := det.update(p)) is not None]
    assert len(events) == 2
