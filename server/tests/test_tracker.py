from judge.detector import Candidate
from judge.tracker import BallTracker


def cand(x, y, r=8.0):
    return Candidate(cx=x, cy=y, r=r, area=3.14 * r * r, bbox=(x - r, y - r, 2 * r, 2 * r))


def test_tracker_follows_the_consistent_mover_and_ignores_a_static_blob():
    tr = BallTracker()
    out = []
    for i in range(12):
        t = i / 30.0
        ball = cand(100 + 20 * i, 300 - 5 * i)
        junk = cand(400, 200)  # a shoe that twitched into the mask
        p = tr.update([junk, ball], t)
        out.append(p)
    confirmed = [p for p in out if p is not None]
    assert len(confirmed) >= 9
    assert all(abs(p.x - (100 + 20 * i)) < 1e-6 for i, p in enumerate(out) if p is not None)


def test_tracker_resets_after_misses():
    tr = BallTracker()
    for i in range(6):
        tr.update([cand(100 + 20 * i, 300)], i / 30.0)
    assert tr.active
    for i in range(6, 12):
        tr.update([], i / 30.0)
    assert not tr.active


def test_tracker_prefers_prediction_when_two_candidates_appear():
    tr = BallTracker()
    for i in range(5):
        tr.update([cand(100 + 20 * i, 300)], i / 30.0)
    # next expected at x=200; a decoy sits at x=150 (closer to the last point than to the prediction)
    p = tr.update([cand(150, 300), cand(200, 300)], 5 / 30.0)
    assert p is not None and p.x == 200
