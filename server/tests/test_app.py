"""HTTP handlers reject bad input with a 400 and a reason, and a bad offer leaves no camera behind."""
import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import app
from pipeline import Settings


class _StubCam:
    name_label = "Left sideline"
    status = "LIVE"

    def calibrate(self, *a, **k):
        raise AssertionError("bad clicks must be rejected before the camera is touched")


def run(coro_fn):
    async def go():
        mgr = app.Manager(app.Hub(), Settings(), None)
        a = web.Application()
        a["mgr"], a["hub"] = mgr, mgr.hub
        a.router.add_post("/offer", app.offer)
        a.router.add_post("/api/cam/{cam_id}/calibrate", app.api_calibrate)
        a.router.add_route("*", "/api/settings", app.api_settings)
        async with TestClient(TestServer(a)) as client:
            await coro_fn(client, mgr)
    asyncio.run(go())


def test_bad_offers_get_400_and_create_no_camera():
    async def check(client, mgr):
        for body in ({"name": "x"}, {"name": "x", "type": "offer", "sdp": "hello"}, [1, 2]):
            r = await client.post("/offer", json=body)
            assert r.status == 400
        r = await client.post("/offer", data="not json", headers={"content-type": "application/json"})
        assert r.status == 400
        r = await client.post("/offer", json={"name": "Garbage", "type": "offer", "sdp": "v=0\r\nnonsense\r\n"})
        assert r.status == 400
        # looks like video but aiortc cannot use it: the camera created for it is removed again
        r = await client.post("/offer", json={"name": "Garbage", "type": "offer",
                                              "sdp": "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"})
        assert r.status == 400
        assert mgr.cams == {} and mgr.pcs == {}
    run(check)


def test_calibration_clicks_that_cannot_make_a_line_get_a_reason():
    async def check(client, mgr):
        mgr.cams["c1"] = _StubCam()
        cases = [
            ({"p1": [600, 400], "p2": [602, 401], "in": [500, 400]}, "too close together"),
            ({"p1": [600, 300], "p2": [600, 600]}, "court side"),
            ({"p1": [600, 300], "p2": [600, 600], "in": [601, 450]}, "on the line"),
            ({"p1": "far", "p2": [600, 600], "in": [500, 450]}, "far end"),
        ]
        for body, words in cases:
            r = await client.post("/api/cam/c1/calibrate", json=body)
            assert r.status == 400
            assert words in (await r.json())["error"]
    run(check)


def test_settings_endpoint_rejects_bad_values():
    async def check(client, mgr):
        r = await client.post("/api/settings", json={"margin_cm": "abc"})
        assert r.status == 400 and "margin_cm" in (await r.json())["error"]
        r = await client.post("/api/settings", json={"speak": "false"})
        assert r.status == 400
        r = await client.post("/api/settings", json={"margin_cm": 2.5})
        assert r.status == 200 and (await r.json())["margin_cm"] == 2.5
    run(check)
