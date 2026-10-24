import base64
import json

import numpy as np

import confirm_jev


def test_request_shape_matches_typesafe_docs(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert not confirm_jev.available()
    zooms = [np.full((120, 120, 3), 90, np.uint8) for _ in range(5)]
    req = confirm_jev.build_request(zooms, contact_index=2, cam_name="Left sideline")
    assert req["model"]
    assert set(req["questions"]) == {"is_ball", "touch_tile"}
    assert req["questions"]["is_ball"]["type"] == "noul"
    assert req["questions"]["touch_tile"]["type"] == "choice"
    assert list(req["questions"]["touch_tile"]["criteria"]) == [f"tile{i}" for i in range(5)]
    # every tile is an asset referenced from state, padded up to the 280 px minimum side
    assert req["state"]["tiles"] == list(req["assets"])
    for a in req["assets"].values():
        assert a["type"] == "image" and a["format"] == "jpg"
        raw = base64.b64decode(a["data_base64"])
        assert raw[:2] == b"\xff\xd8"  # JPEG magic
    import cv2
    img = cv2.imdecode(np.frombuffer(base64.b64decode(req["assets"]["tile0"]["data_base64"]), np.uint8), cv2.IMREAD_COLOR)
    assert min(img.shape[:2]) >= 280
    json.dumps(req)  # serialisable


def test_ask_is_noop_without_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert confirm_jev.ask([np.zeros((10, 10, 3), np.uint8)], 0, "x") is None
