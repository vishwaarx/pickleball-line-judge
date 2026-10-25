"""HTTPS server: phone camera page, WebRTC ingest, dashboard with live state over WebSocket."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import os
import socket
import ssl
import subprocess
import time
import uuid
from dataclasses import asdict

from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

import logsetup
from pipeline import CameraWorker, Settings, Speaker

log = logsetup.setup(os.environ.get("PICKLE_LOG", "INFO"))

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")
EVENTS = os.path.join(HERE, "events")
CALIB = os.path.join(HERE, "calib")
CERTS = os.path.join(HERE, "certs")


class Hub:
    """Fan-out of state/preview/event messages to dashboard WebSockets."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None):
        self.loop = loop  # replaced with the running loop in on_startup
        self.clients: set[web.WebSocketResponse] = set()
        self.events: list[dict] = []
        self.next_id = 1

    def add_event(self, d: dict) -> dict:
        d["id"] = self.next_id
        self.next_id += 1
        self.events.append(d)
        del self.events[:-1000]
        return d

    def broadcast_threadsafe(self, msg: dict) -> None:
        self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.broadcast(msg)))

    async def broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg)

        async def send(ws):
            try:
                await asyncio.wait_for(ws.send_str(data), timeout=2.0)
                return None
            except Exception:
                return ws  # slow or dead client: drop it rather than stall the others

        dead = await asyncio.gather(*(send(ws) for ws in list(self.clients)))
        for ws in dead:
            if ws is not None:
                self.clients.discard(ws)
                asyncio.ensure_future(ws.close())


class Manager:
    def __init__(self, hub: Hub, settings: Settings, speaker: Speaker | None):
        self.hub = hub
        self.settings = settings
        self.speaker = speaker
        self.cams: dict[str, CameraWorker] = {}
        self.pcs: dict[str, RTCPeerConnection] = {}

    def get_or_create(self, cam_id: str, name: str) -> CameraWorker:
        cam = self.cams.get(cam_id)
        if cam is None or cam.name_label != name:
            if cam is not None:
                cam.stop()
            # one line, one camera: a new phone taking over a name replaces the stale worker
            for other_id, other in list(self.cams.items()):
                if other_id != cam_id and other.name_label == name:
                    log.info("replacing camera '%s' (%s, %s) with %s", name, other_id, other.status, cam_id)
                    self.remove(other_id)
            cam = CameraWorker(cam_id, name, self.settings, self._on_event, self._on_preview,
                               EVENTS, CALIB, self.speaker)
            cam.start()
            self.cams[cam_id] = cam
        return cam

    def remove(self, cam_id: str) -> None:
        cam = self.cams.pop(cam_id, None)
        if cam:
            cam.stop()
        pc = self.pcs.pop(cam_id, None)
        if pc:
            asyncio.ensure_future(pc.close())

    def _on_event(self, call) -> None:
        d = asdict(call)

        def deliver():
            # a later delivery of the same event (by uid) carries Jev's confirmation or the 3D replay: patch, don't append
            for e in reversed(self.hub.events[-50:]):
                if e.get("uid") == d.get("uid"):
                    e["confirmation"] = d.get("confirmation")
                    e["replay"] = d.get("replay")
                    asyncio.ensure_future(self.hub.broadcast({"type": "event", "event": e, "update": True}))
                    return
            # event ids are assigned on the loop thread so they stay ordered with the broadcast
            asyncio.ensure_future(self.hub.broadcast({"type": "event", "event": self.hub.add_event(d)}))

        self.hub.loop.call_soon_threadsafe(deliver)

    def _on_preview(self, cam_id: str, jpg_b64: str) -> None:
        self.hub.broadcast_threadsafe({"type": "preview", "cam": cam_id, "jpg": jpg_b64})

    def state(self) -> dict:
        return {
            "type": "state",
            "cams": [c.state() for c in self.cams.values()],
            "settings": asdict(self.settings),
            "join_url": self.join_url,
            "hosts": self.hosts,
            "events": len(self.hub.events),
            "time": time.time(),
        }

    join_url = ""
    hosts: list[str] = []


# ----------------------------------------------------------------------------- helpers
MAX_STRIPS_PER_DAY = 2000  # ~600 MB; a 2-hour match produces a few hundred


def prune_evidence(root: str, keep: int) -> None:
    """Delete the oldest evidence strips (+ their json) beyond `keep` per day folder."""
    try:
        for day in os.listdir(root):
            d = os.path.join(root, day)
            if not os.path.isdir(d) or not day[:4].isdigit():
                continue
            jpgs = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
            for f in jpgs[:-keep] if len(jpgs) > keep else []:
                for ext in (".jpg", ".json"):
                    try:
                        os.remove(os.path.join(d, f[:-4] + ext))
                    except FileNotFoundError:
                        pass
            if len(jpgs) > keep:
                log.info("pruned %d old evidence strips in %s", len(jpgs) - keep, day)
    except Exception:
        log.exception("evidence prune failed")


def lan_ips() -> list[str]:
    """IPv4 addresses, Internet-Sharing bridge first, then Wi-Fi, then the rest."""
    out = subprocess.run(["ifconfig"], capture_output=True, text=True).stdout
    found: list[tuple[str, str]] = []
    iface = None
    for line in out.splitlines():
        if line and not line.startswith(("\t", " ")):
            iface = line.split(":")[0]
        elif "inet " in line and iface:
            ip = line.split()[1]
            if not ip.startswith("127."):
                found.append((iface, ip))

    def rank(item):
        name = item[0]
        return (0 if name.startswith("bridge") else 1 if name == "en0" else 2, name)

    return [ip for _, ip in sorted(found, key=rank)]


def ensure_cert(hosts: list[str]) -> tuple[str, str]:
    os.makedirs(CERTS, exist_ok=True)
    cert, key = os.path.join(CERTS, "cert.pem"), os.path.join(CERTS, "key.pem")
    names = ["localhost", "127.0.0.1", "pickle.local"] + hosts
    stamp = os.path.join(CERTS, "hosts.txt")
    if os.path.exists(cert) and os.path.exists(stamp) and open(stamp).read().split() == names:
        return cert, key
    subprocess.run(["mkcert", "-cert-file", cert, "-key-file", key, *names], check=True,
                   capture_output=True)
    with open(stamp, "w") as f:
        f.write(" ".join(names))
    return cert, key


# ----------------------------------------------------------------------------- routes
async def index(request):
    return web.FileResponse(os.path.join(STATIC, "index.html"))


async def cam_page(request):
    return web.FileResponse(os.path.join(STATIC, "cam.html"))


async def ca_pem(request):
    root = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True).stdout.strip()
    path = os.path.join(root, "rootCA.pem")
    if not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Content-Type": "application/x-pem-file",
                                           "Content-Disposition": "attachment; filename=pickle-ca.pem"})


async def qr_png(request):
    import qrcode

    mgr: Manager = request.app["mgr"]
    img = qrcode.make(mgr.join_url, box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return web.Response(body=buf.getvalue(), content_type="image/png")


async def read_json(request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest(text="expected a JSON body")
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected a JSON object")
    return body


def bad_request(message: str) -> web.Response:
    return web.json_response({"error": message}, status=400)


async def offer(request):
    mgr: Manager = request.app["mgr"]
    params = await read_json(request)
    # check the offer before anything is created: a bad request must not leave a camera behind
    sdp = params.get("sdp")
    if params.get("type") != "offer" or not isinstance(sdp, str) or not sdp.startswith("v=0") or "m=video" not in sdp:
        return bad_request("expected a WebRTC offer with video")
    cam_id = str(params.get("cam_id") or uuid.uuid4().hex[:8])[:32]
    name = str(params.get("name") or "Camera").strip()[:40] or "Camera"
    for other_id, other in mgr.cams.items():
        if other_id != cam_id and other.name_label == name and other.status != "NO SIGNAL":
            # two phones on one line would replace each other every 30 s; refuse the second one
            log.warning("offer from %s for '%s' refused: %s is already streaming that line", request.remote, name, other_id)
            return web.json_response({"error": f"Another phone is already \"{name}\". Pick a different line name."},
                                     status=409)
    existed = cam_id in mgr.cams and mgr.cams[cam_id].name_label == name
    cam = mgr.get_or_create(cam_id, name)
    cam.phone_settings = params.get("settings") if isinstance(params.get("settings"), dict) else {}
    log.info("offer from %s '%s' %s phone=%s", request.remote, name, cam_id, cam.phone_settings)

    old = mgr.pcs.pop(cam_id, None)
    if old is not None:
        log.info("closing previous connection for %s", cam_id)
        await old.close()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    mgr.pcs[cam_id] = pc

    @pc.on("track")
    def on_track(track):
        if track.kind != "video":
            return
        log.info("[%s] video track started", name)

        async def pump():
            n = 0
            while True:
                try:
                    frame = await track.recv()
                except MediaStreamError:
                    log.info("[%s] video track ended after %d frames", name, n)
                    break
                n += 1
                cam.push(frame)

        asyncio.ensure_future(pump())

    @pc.on("connectionstatechange")
    async def on_state():
        log.info("[%s] connection %s", name, pc.connectionState)
        if pc.connectionState in ("failed", "closed"):
            await pc.close()
            if mgr.pcs.get(cam_id) is pc:
                mgr.pcs.pop(cam_id, None)

    try:
        await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
    except Exception as exc:  # an SDP aiortc cannot use
        log.warning("offer from %s for '%s' rejected: %r", request.remote, name, exc)
        await pc.close()
        if mgr.pcs.get(cam_id) is pc:
            mgr.pcs.pop(cam_id, None)
        if not existed:
            mgr.remove(cam_id)
        return bad_request("could not use this WebRTC offer")
    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
                              "cam_id": cam_id})


async def snapshot(request):
    mgr: Manager = request.app["mgr"]
    cam = mgr.cams.get(request.match_info["cam_id"])
    if cam is None:
        raise web.HTTPNotFound()
    data = cam.snapshot_jpeg()
    if data is None:
        raise web.HTTPServiceUnavailable(text="no frame yet")
    return web.Response(body=data, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})


async def api_state(request):
    return web.json_response(request.app["mgr"].state())


async def api_events(request):
    events = request.app["hub"].events
    after = request.query.get("after")
    if after is not None:
        try:
            a = int(after)
        except ValueError:
            raise web.HTTPBadRequest(text="after must be an integer event id")
        return web.json_response([e for e in events if e["id"] > a])
    return web.json_response(events[-50:])


def _point(v) -> tuple[float, float] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        return None
    try:
        x, y = float(v[0]), float(v[1])
    except (TypeError, ValueError):
        return None
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def calibration_error(p1, p2, inside) -> str | None:
    """Clicks that cannot define a line, in words the operator can act on."""
    if p1 is None or p2 is None:
        return "Click the far end and the near end of the line."
    if inside is None:
        return "Click a point on the court side of the line."
    length = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if length < 20:
        return "The two ends of the line are too close together. Click the far end, then the near end."
    off = abs((inside[0] - p1[0]) * (p1[1] - p2[1]) + (inside[1] - p1[1]) * (p2[0] - p1[0])) / length
    if off < 5:
        return "The court-side click is on the line. Click a point clearly on the court side."
    return None


async def api_calibrate(request):
    mgr: Manager = request.app["mgr"]
    cam = mgr.cams.get(request.match_info["cam_id"])
    if cam is None:
        raise web.HTTPNotFound()
    body = await read_json(request)
    p1, p2, inside = _point(body.get("p1")), _point(body.get("p2")), _point(body.get("in"))
    err = calibration_error(p1, p2, inside)
    if err:
        return bad_request(err)
    view = _point(body.get("view"))
    ball = _point(body.get("ball")) if body.get("ball") is not None else None
    try:
        res = cam.calibrate(p1, p2, inside, ball, coords=body.get("coords", "work"), view=view)
    except RuntimeError as exc:
        raise web.HTTPServiceUnavailable(text=str(exc))
    except ValueError as exc:
        return bad_request(str(exc))
    return web.json_response(res)


async def api_cam_action(request):
    mgr: Manager = request.app["mgr"]
    cam_id = request.match_info["cam_id"]
    action = request.match_info["action"]
    cam = mgr.cams.get(cam_id)
    if action == "remove":
        mgr.remove(cam_id)
        return web.json_response({"ok": True})
    if cam is None:
        raise web.HTTPNotFound()
    if action == "clear_ball":
        cam.clear_ball_color()
    elif action == "reset_guard":
        if not cam.rearm_guard():
            return web.json_response({"ok": False, "error": cam.moved_reason})
    else:
        raise web.HTTPBadRequest(text="unknown action")
    return web.json_response({"ok": True})


async def api_settings(request):
    mgr: Manager = request.app["mgr"]
    if request.method == "POST":
        try:
            mgr.settings.update(await read_json(request))
        except ValueError as exc:
            return bad_request(str(exc))
    return web.json_response(asdict(mgr.settings))


async def api_say(request):
    mgr: Manager = request.app["mgr"]
    body = await read_json(request)
    if mgr.speaker:
        mgr.speaker.say(str(body.get("text") or "Testing. Out by 3. Left sideline.")[:200])
    return web.json_response({"ok": True})


async def ws_handler(request):
    hub: Hub = request.app["hub"]
    mgr: Manager = request.app["mgr"]
    ws = web.WebSocketResponse(heartbeat=20, compress=False)
    await ws.prepare(request)
    hub.clients.add(ws)
    await ws.send_str(json.dumps(mgr.state()))
    for ev in hub.events[-20:]:
        await ws.send_str(json.dumps({"type": "event", "event": ev, "replay": True}))
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("type") == "settings":
                    try:
                        mgr.settings.update(data.get("settings", {}))
                    except ValueError as exc:
                        log.warning("dashboard settings rejected: %s", exc)
    finally:
        hub.clients.discard(ws)
    return ws


async def state_ticker(app):
    mgr: Manager = app["mgr"]
    hub: Hub = app["hub"]
    last_reap = time.monotonic()
    while True:
        try:
            await hub.broadcast(mgr.state())
            if time.monotonic() - last_reap > 60:
                last_reap = time.monotonic()
                # a phone that left for good keeps a thread and ~400 MB of ring buffer: reap after 10 min
                for cam_id, cam in list(mgr.cams.items()):
                    idle = time.monotonic() - cam.last_frame_wall
                    if cam.status == "NO SIGNAL" and idle > 600 and cam_id not in mgr.pcs:
                        log.info("reaping camera '%s' (%s): no frames for %.0f s", cam.name_label, cam_id, idle)
                        mgr.remove(cam_id)
                await asyncio.to_thread(prune_evidence, EVENTS, MAX_STRIPS_PER_DAY)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("state ticker error")
        await asyncio.sleep(0.5)


async def on_startup(app):
    # worker threads hand events/previews to this loop; it must be the one run_app actually runs
    app["hub"].loop = asyncio.get_running_loop()
    app["ticker"] = asyncio.ensure_future(state_ticker(app))


async def on_cleanup(app):
    app["ticker"].cancel()
    mgr: Manager = app["mgr"]
    for pc in list(mgr.pcs.values()):
        await pc.close()
    for cam in mgr.cams.values():
        cam.stop()


def build_app(port: int, host_override: str | None, speak: bool) -> tuple[web.Application, ssl.SSLContext]:
    hub = Hub()
    speaker = Speaker() if speak else None
    mgr = Manager(hub, Settings(), speaker)
    hosts = lan_ips()
    primary = host_override or (hosts[0] if hosts else "127.0.0.1")
    mgr.hosts = hosts
    mgr.join_url = f"https://{primary}:{port}/cam"
    cert, key = ensure_cert(hosts + ([host_override] if host_override else []))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)

    @web.middleware
    async def no_cache(request, handler):
        # phones must never keep a stale cam.js/dashboard.js after a fix
        resp = await handler(request)
        if request.path.startswith(("/static/", "/cam")) or request.path == "/":
            resp.headers["Cache-Control"] = "no-store"
        return resp

    app = web.Application(client_max_size=8 * 1024 * 1024, middlewares=[no_cache])
    app["hub"], app["mgr"] = hub, mgr
    app.router.add_get("/", index)
    app.router.add_get("/cam", cam_page)
    app.router.add_get("/ca.pem", ca_pem)
    app.router.add_get("/qr.png", qr_png)
    app.router.add_post("/offer", offer)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/state", api_state)
    app.router.add_get("/api/events", api_events)
    app.router.add_get("/api/snapshot/{cam_id}.jpg", snapshot)
    app.router.add_post("/api/cam/{cam_id}/calibrate", api_calibrate)
    app.router.add_post("/api/cam/{cam_id}/{action}", api_cam_action)
    app.router.add_route("*", "/api/settings", api_settings)
    app.router.add_post("/api/say", api_say)
    os.makedirs(EVENTS, exist_ok=True)
    app.router.add_static("/events", EVENTS, show_index=True)
    app.router.add_static("/static", STATIC)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app, ctx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8443)
    ap.add_argument("--host", default=None, help="IP to advertise in the join URL/QR")
    ap.add_argument("--no-speak", action="store_true")
    args = ap.parse_args()
    app, ctx = build_app(args.port, args.host, speak=not args.no_speak)
    mgr: Manager = app["mgr"]
    log.info("Dashboard: https://localhost:%d/", args.port)
    log.info("Phones:    %s   (all hosts: %s)", mgr.join_url, ", ".join(mgr.hosts))
    print(f"\n  Dashboard  https://localhost:{args.port}/\n  Phones     {mgr.join_url}\n", flush=True)
    web.run_app(app, port=args.port, ssl_context=ctx, print=None)


if __name__ == "__main__":
    main()
