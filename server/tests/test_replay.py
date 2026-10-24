"""3D bounce replay: camera recovered from the paint, and a synthetic bounce rebuilt close to its truth."""
import glob
import json
import math
import os

from judge.bounce import TrackPoint
from judge.detector import hsv_range_from_bgr
from judge.linescale import LineScale
from judge.replay import camera_from_scale, flight_window
from pipeline import CameraWorker, Settings
from tools.synth import BALL_BGR, BALL_R, Bounce, Scene, render_bounce


def test_camera_height_and_tilt_come_from_the_paint_scale():
    scene = Scene()  # 0.35 m high, 6 degrees down, 66 degree lens
    f, cy = scene.f, scene.cy
    pitch = math.radians(scene.pitch_deg)
    a = 0.0508 * math.cos(pitch) / scene.cam_h  # paint width per row on a flat floor
    v_h = cy - f * math.tan(pitch)
    cam = camera_from_scale(LineScale(a=a, b=-a * v_h, n=16, rms=0.0), (scene.width, scene.height))
    assert abs(cam.h - scene.cam_h) < 1e-6
    assert abs(math.degrees(cam.pitch) - scene.pitch_deg) < 1e-6


def test_scale_that_shrinks_toward_the_camera_gives_no_camera():
    assert camera_from_scale(LineScale(a=-0.1, b=60.0, n=16, rms=0.0), (1280, 720)) is None


def test_flight_window_keeps_one_continuous_run_around_the_bounce():
    pts = [TrackPoint(t=t, x=0, y=0, r=5) for t in (0.0, 0.05, 0.6, 0.65, 0.70, 0.75, 1.3)]
    run = flight_window(pts, t_contact=0.68)
    assert [p.t for p in run] == [0.6, 0.65, 0.70, 0.75]


def test_synthetic_bounce_replay_matches_truth(tmp_path):
    scene = Scene()
    fps = 60
    b = Bounce(Xb=0.076 + BALL_R, Zb=6.0, vX=2.0, vZ=-7.0, v_in=4.0)  # 7.6 cm out, coming toward the camera
    events = []
    cam = CameraWorker("c1", "Left sideline", Settings(speak=False), on_event=events.append,
                       on_preview=lambda *a: None, events_dir=str(tmp_path / "events"), calib_dir=str(tmp_path / "calib"))
    cam.last_preview = float("inf")
    p1, p2, in_pt = scene.line_calibration()
    for n, (t, frame) in enumerate(render_bounce(scene, b, fps, phase=0.25, lead_frames=fps)):
        cam._process(frame, t + 100.0)
        if n == 0:
            cam.calibrate(p1, p2, in_pt, None, view=(frame.shape[1], frame.shape[0]))
            cam.calib.ball_bgr = BALL_BGR
            cam.detector.set_color(hsv_range_from_bgr(BALL_BGR))
    if cam._replay_pending is not None:  # the clip ends before the deadline
        cam._finish_replay()

    assert [e.verdict for e in events][:1] == ["OUT"]
    assert events[-1].replay and events[-1].uid == events[0].uid  # the same call, patched
    meta = json.load(open(glob.glob(os.path.join(str(tmp_path / "events"), "*", "*.json"))[0]))
    rep = meta["replay"]
    assert abs(rep["camera"]["height_m"] - scene.cam_h) < 0.02
    assert abs(rep["contact"]["lat"] - b.Xb) < 0.015  # ball centre 11.3 cm outside the line's outer edge
    assert abs(rep["contact"]["along"] - b.Zb) < 0.1 * b.Zb
    assert rep["before"] and rep["after"]
    # it came in from farther down the line and left toward the camera, rising
    assert rep["before"][0]["along"] > rep["contact"]["along"] > rep["after"][-1]["along"]
    assert rep["after"][-1]["up"] > 0.15
