"""Optional second opinion from Jev (TypeSafe AI System One) on an evidence strip.

Geometry makes the call; Jev only confirms the event ("is that a ball, and which tile shows it on
the floor?"). Runs only when TYPESAFE_API_KEY is set and the Mac is online; otherwise it is a no-op.

Request shape per https://docs.typesafe.ai/introduction/quickstart and https://docs.typesafe.ai/images :
  POST https://api.typesafe.ai/v1/systemone   Authorization: Bearer <key>
  headers: TypeSafe-Danger-Zone-Images: true   (images are opt-in and marked not production-ready)
  body: {"model": "jev-preview", "state": {...image refs...}, "assets": {ref: {"type":"image","format":"jpg","data_base64":...}},
         "questions": {name: {"type": "choice"|"noul"|"score", "instructions": str, "criteria": ...}}}
  answers: {name: {"type": ..., "choice"/"noul"/"score": ..., "confidence": ..., "probabilities": {...}}}
Images must be 280–1568 px per side, so tiles are padded up to 280 px.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

import cv2
import numpy as np

log = logging.getLogger("pickle.jev")

API_URL = os.environ.get("TYPESAFE_API_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("TYPESAFE_MODEL", "jev-preview")  # Box/images need 1.14 = jev-preview as of 2026-10-14
MIN_SIDE = 280


def available() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _tile_b64(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    if min(h, w) < MIN_SIDE:
        s = MIN_SIDE / min(h, w)
        img = cv2.resize(img, (int(round(w * s)), int(round(h * s))), interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return base64.b64encode(buf.tobytes()).decode("ascii")


def build_request(zooms: list[np.ndarray], contact_index: int, cam_name: str) -> dict:
    """Tiles are the zoomed crops around the computed contact point, in time order."""
    assets = {}
    refs = []
    for i, z in enumerate(zooms):
        ref = f"tile{i}"
        assets[ref] = {"type": "image", "format": "jpg", "data_base64": _tile_b64(z)}
        refs.append(ref)
    state = {
        "context": (f"Pickleball line-judge camera '{cam_name}' looking along a court line. These tiles are "
                    f"consecutive video frames, a few milliseconds apart, cropped around the spot where the "
                    f"software thinks the ball touched the floor. Tile index {contact_index} is the software's pick."),
        "tiles": refs,
    }
    questions = {
        "is_ball": {
            "type": "noul",
            "instructions": "A pickleball (small plastic ball with holes) is visible in the tiles, not a shoe, paddle, shadow or reflection.",
        },
        "touch_tile": {
            "type": "choice",
            "instructions": "Which tile shows the ball at its lowest point, touching the floor?",
            "criteria": {ref: f"tile {i}" for i, ref in enumerate(refs)},
        },
    }
    return {"model": MODEL, "state": state, "assets": assets, "questions": questions}


def ask(zooms: list[np.ndarray], contact_index: int, cam_name: str, timeout: float = 6.0) -> dict | None:
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None
    body = json.dumps(build_request(zooms, contact_index, cam_name)).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "TypeSafe-Danger-Zone-Images": "true",
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        log.warning("jev http %s: %s", e.code, e.read()[:300])
        return None
    except Exception as exc:  # offline, DNS, timeout: silently skip
        log.info("jev unavailable (%s)", exc.__class__.__name__)
        return None
    ms = (time.monotonic() - t0) * 1000
    ans = data.get("answers", {})
    is_ball = ans.get("is_ball", {}).get("noul")
    pick = ans.get("touch_tile", {}).get("choice")
    conf = ans.get("touch_tile", {}).get("confidence")
    result = {
        "model": data.get("model"),
        "is_ball": is_ball,
        "touch_tile": int(pick[4:]) if isinstance(pick, str) and pick.startswith("tile") else None,
        "touch_confidence": conf,
        "agrees": (isinstance(pick, str) and pick == f"tile{contact_index}"),
        "ms": round(ms),
    }
    log.info("jev: ball=%s touch=%s conf=%s agrees=%s in %d ms", is_ball, pick, conf, result["agrees"], ms)
    return result


def ask_async(zooms, contact_index, cam_name, callback) -> None:
    """Fire-and-forget; callback(result or None) on a worker thread."""
    if not available():
        return

    def run():
        callback(ask(zooms, contact_index, cam_name))

    threading.Thread(target=run, daemon=True, name="jev").start()
