"""Worker-level behaviour the code review flagged: saved calibrations across restarts and resolution ramps."""
import json
import os

import cv2
import numpy as np
import pytest

from pipeline import CameraWorker, Settings
from tools.synth import Scene


def make_worker(tmp_path, name="Left sideline"):
    events = []
    cam = CameraWorker("c1", name, Settings(speak=False), on_event=events.append, on_preview=lambda *a: None,
                       events_dir=str(tmp_path / "events"), calib_dir=str(tmp_path / "calib"))
    cam.last_preview = float("inf")
    return cam, events


def feed(cam, scene, n, size=None, t0=0.0, fps=30.0):
    for i in range(n):
        fr = scene.frame(None)
        if size is not None:
            fr = cv2.resize(fr, size, interpolation=cv2.INTER_AREA)
        cam._process(fr, t0 + i / fps)


def test_saved_calibration_survives_restart_and_resolution_ramp(tmp_path):
    scene = Scene(seed=5)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 35)
    p1, p2, in_pt = scene.line_calibration()
    cam.calibrate(p1, p2, in_pt, None, view=(1280, 720))
    saved_line = cam.calib.line
    saved = json.load(open(cam._calib_path()))
    assert saved["work_size"] == [1280, 720]
    cam.stop()

    # a new worker (server restart / phone reconnect) first sees WebRTC's 640x360 ramp, then 1280x720
    cam2, _ = make_worker(tmp_path)
    assert cam2.calib.line is not None and cam2._verify_pending
    feed(cam2, scene, 5, size=(640, 360))
    assert cam2.work_size == (640, 360)
    assert cam2.calib.line.p1[0] == pytest.approx(saved_line.p1[0] / 2, abs=0.6)
    feed(cam2, scene, 40, size=None, t0=10.0)
    assert cam2.work_size == (1280, 720)
    assert cam2.calib.line.p1[0] == pytest.approx(saved_line.p1[0], abs=0.6)
    assert cam2.calib.line.p2[1] == pytest.approx(saved_line.p2[1], abs=0.6)
    assert cam2.calib.scale is not None
    assert not cam2.moved  # the paint was found where the file said


def test_saved_calibration_that_does_not_match_view_pauses_calls(tmp_path):
    scene = Scene(seed=6)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 35)
    p1, p2, in_pt = scene.line_calibration()
    # a line clicked 120 px away from any paint, as if the file came from another court
    cam.calibrate((p1[0] + 120, p1[1]), (p2[0] + 120, p2[1]), in_pt, None, view=(1280, 720))
    cam.stop()
    cam2, _ = make_worker(tmp_path)
    feed(cam2, scene, 40)
    assert cam2.moved
    assert "saved calibration" in cam2.moved_reason


def test_legacy_calibration_without_resolution_is_ignored(tmp_path):
    os.makedirs(tmp_path / "calib")
    with open(tmp_path / "calib" / "Left_sideline.json", "w") as f:
        json.dump({"line": {"p1": [640, 100], "p2": [640, 600], "in_sign": 1}, "ball_bgr": None}, f)
    cam, _ = make_worker(tmp_path)
    assert cam.calib.line is None


def test_aspect_change_pauses_instead_of_rescaling(tmp_path):
    scene = Scene(seed=7)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 35)
    p1, p2, in_pt = scene.line_calibration()
    cam.calibrate(p1, p2, in_pt, None, view=(1280, 720))
    feed(cam, scene, 3, size=(960, 720), t0=5.0)  # 4:3 frames
    assert cam.moved and "aspect" in cam.moved_reason


def test_stream_clock_restart_clears_track_state(tmp_path):
    scene = Scene(seed=8)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 10, t0=100.0)
    assert len(cam.ring) == 10
    feed(cam, scene, 1, t0=0.0)  # new connection, clock back to zero
    assert len(cam.ring) == 1


def test_corrupt_calibration_file_is_ignored(tmp_path):
    os.makedirs(tmp_path / "calib")
    with open(tmp_path / "calib" / "Left_sideline.json", "w") as f:
        f.write("{not json")
    cam, _ = make_worker(tmp_path)
    assert cam.calib.line is None


def _knock(frame, px=40):
    return np.roll(frame, px, axis=1)  # the phone got knocked sideways


def test_phone_is_back_is_refused_while_the_paint_is_elsewhere(tmp_path):
    scene = Scene(seed=5)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 35)
    p1, p2, in_pt = scene.line_calibration()
    cam.calibrate(p1, p2, in_pt, None, view=(1280, 720))
    for i in range(10):
        cam._process(_knock(scene.frame(None)), 2.0 + i / 30)
    cam.moved = True  # what the guard does a few seconds after the knock
    assert cam.rearm_guard() is False
    assert cam.moved and "calibrate again" in cam.moved_reason


def test_phone_is_back_resumes_calls_when_the_paint_is_where_the_line_is(tmp_path):
    scene = Scene(seed=5)
    cam, _ = make_worker(tmp_path)
    feed(cam, scene, 35)
    p1, p2, in_pt = scene.line_calibration()
    cam.calibrate(p1, p2, in_pt, None, view=(1280, 720))
    for i in range(10):
        cam._process(_knock(scene.frame(None)), 2.0 + i / 30)
    cam.moved = True
    feed(cam, scene, 10, t0=3.0)  # put back where it was
    assert cam.rearm_guard() is True
    assert not cam.moved


def test_settings_reject_bad_values_without_applying_any():
    s = Settings()
    s.update({"margin_cm": 3, "unknown_key": 1})
    assert s.margin_cm == 3.0
    for bad in ({"margin_cm": "abc"}, {"margin_cm": -5}, {"margin_cm": float("nan")}, {"speak": "false"},
                {"margin_cm": True}):
        with pytest.raises(ValueError):
            s.update(bad)
    with pytest.raises(ValueError):
        s.update({"margin_cm": 4, "speak": "no"})
    assert s.margin_cm == 3.0 and s.speak is True
