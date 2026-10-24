/* 3D bounce replay. Draws the reconstruction the server adds to each call's evidence json. No libraries, no CDN. */
(() => {
  const $ = (id) => document.getElementById(id);
  const overlay = $("replay"), cv = $("replayCanvas"), g = cv.getContext("2d");
  const ic = $("replayInset"), h = ic.getContext("2d");
  const R = 0.037;  // ball radius, m
  const VIEWS = {
    broadcast: { yaw: -0.95, pitch: 0.42, dist: 3.2 },
    line: { yaw: Math.PI, pitch: 0.12, dist: 4.5 },  // from behind the phone, as it sees the line
    top: { yaw: Math.PI / 2, pitch: 1.45, dist: 3.4 },  // distance along the line grows to the right
  };
  const VERDICT_VAR = { IN: "--in", OUT: "--out", TOO_CLOSE: "--close", NO_CALL: "--nocall" };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let ev = null, rec = null, path = [], target = null, cam = { ...VIEWS.broadcast }, C = {};
  let anim = 1, t0 = 0, raf = 0, drag = null;
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  async function open(e) {
    let meta = null;
    try {
      const res = await fetch(e.strip_url.replace(/\.jpg$/, ".json"), { cache: "no-store" });
      if (res.ok) meta = await res.json();
    } catch (err) { meta = null; }
    if (!meta || !meta.replay) { alert("This call has no replay yet."); return; }
    ev = e; rec = meta.replay;
    path = [...rec.before, ...rec.after];
    target = { x: rec.contact.lat * 0.5, y: 0.15, z: rec.contact.along };
    C = { floor: css("--replay-floor"), surround: css("--replay-surround"), paint: css("--line"), ball: css("--ball"),
          ink: css("--ink"), muted: css("--muted"), verdict: css(VERDICT_VAR[ev.verdict] || "--nocall") };
    $("replayTitle").textContent = `3D replay · ${ev.cam_name} · ${ev.wall_time}`;
    renderFacts();
    overlay.hidden = false;
    setView("broadcast");
    size(); drawInset(); play();
  }

  function close() { overlay.hidden = true; cancelAnimationFrame(raf); }

  function renderFacts() {
    const peak = rec.after.length ? Math.max(...rec.after.map((p) => p.up)) : null;
    const gap = ev.gap_cm == null ? "–" : `${ev.gap_cm > 0 ? "+" : ""}${ev.gap_cm.toFixed(1)} cm`;
    const rows = [
      ["Call", { IN: "In", OUT: "Out", TOO_CLOSE: "In (too close to call)", NO_CALL: "No call" }[ev.verdict] || ev.verdict],
      ["Gap to the line", gap],
      ["Bounce, from the camera", `${rec.contact.along.toFixed(1)} m`],
      ["Speed coming in", `${rec.speed_in.toFixed(1)} m/s`],
      ["Rebound height seen", peak == null ? "–" : `${Math.round(peak * 100)} cm`],
      ["Camera height", `${Math.round(rec.camera.height_m * 100)} cm`],
    ];
    const dl = $("replayFacts");
    dl.replaceChildren();
    for (const [k, v] of rows) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k; dd.textContent = v;
      dl.append(dt, dd);
    }
  }

  // ---------------------------------------------------------------- 3D view: x = lateral (+ OUT), y = up, z = along the line
  function size() {
    const d = devicePixelRatio || 1, r = cv.getBoundingClientRect();
    cv.width = Math.round(r.width * d); cv.height = Math.round(r.height * d);
  }
  function project(p) {
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw), cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const ex = target.x + cam.dist * cp * sy, ey = target.y + cam.dist * sp, ez = target.z + cam.dist * cp * cy;
    const dx = p.x - ex, dy = p.y - ey, dz = p.z - ez;
    const zc = -(dx * cp * sy + dy * sp + dz * cp * cy);
    if (zc < 0.05) return null;
    // the court frame (lat to the camera's right, up, along away from it) is left-handed: flip screen x so the
    // replay is not a mirror image of what the phone saw
    const xc = dz * sy - dx * cy, yc = -dx * sp * sy + dy * cp - dz * sp * cy;
    const f = Math.min(cv.width, cv.height) * 1.1;
    return { x: cv.width / 2 + f * xc / zc, y: cv.height / 2 - f * yc / zc, s: f / zc };
  }
  function poly(pts, fill) {
    const q = pts.map(project);
    if (q.some((p) => !p)) return;
    g.beginPath(); q.forEach((p, i) => (i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y))); g.closePath();
    g.fillStyle = fill; g.fill();
  }
  function seg(a, b, color, w) {
    const p = project(a), q = project(b);
    if (!p || !q) return;
    g.strokeStyle = color; g.lineWidth = w; g.beginPath(); g.moveTo(p.x, p.y); g.lineTo(q.x, q.y); g.stroke();
  }
  const P = (o) => ({ x: o.lat, y: o.up, z: o.along });

  function draw() {
    const d = devicePixelRatio || 1;
    g.clearRect(0, 0, cv.width, cv.height);
    const c = rec.contact, z0 = Math.max(0.3, c.along - 3), z1 = c.along + 3;
    poly([{ x: -1.2, y: 0, z: z0 }, { x: 1.2, y: 0, z: z0 }, { x: 1.2, y: 0, z: z1 }, { x: -1.2, y: 0, z: z1 }], C.surround);
    poly([{ x: -1.2, y: 0, z: z0 }, { x: 0, y: 0, z: z0 }, { x: 0, y: 0, z: z1 }, { x: -1.2, y: 0, z: z1 }], C.floor);
    poly([{ x: -0.0508, y: 0, z: z0 }, { x: 0, y: 0, z: z0 }, { x: 0, y: 0, z: z1 }, { x: -0.0508, y: 0, z: z1 }], C.paint);
    g.fillStyle = C.muted; g.font = `${Math.round(12 * d)}px system-ui`;
    for (let z = Math.ceil(z0); z <= z1; z++) {
      const p = project({ x: -0.14, y: 0, z });
      if (p) g.fillText(`${z} m`, p.x - 30 * d, p.y);
    }
    const mark = [];
    for (let k = 0; k < 24; k++) {
      const a = (k / 24) * Math.PI * 2;
      mark.push({ x: c.lat + 0.012 * Math.cos(a), y: 0.001, z: c.along + 0.03 * Math.sin(a) });
    }
    poly(mark, C.verdict);
    const tEnd = path[0].t + (path[path.length - 1].t - path[0].t) * anim;
    const shown = path.filter((p) => p.t <= tEnd + 1e-6);
    for (let i = 1; i < shown.length; i++) seg({ ...P(shown[i - 1]), y: 0.002 }, { ...P(shown[i]), y: 0.002 }, "rgba(0,0,0,0.35)", 3 * d);
    for (let i = 1; i < shown.length; i++) seg(P(shown[i - 1]), P(shown[i]), C.ball, 3 * d);
    g.fillStyle = C.ink;
    for (const o of rec.observed) {
      if (o.t > tEnd) continue;
      const p = project(P(o));
      if (p) { g.beginPath(); g.arc(p.x, p.y, 2.5 * d, 0, 7); g.fill(); }
    }
    const head = shown[shown.length - 1];
    const p = head && project(P(head));
    if (p) { g.fillStyle = C.ball; g.beginPath(); g.arc(p.x, p.y, Math.max(3 * d, R * p.s), 0, 7); g.fill(); }
  }

  // ---------------------------------------------------------------- the bounce mark next to the line, from above
  function drawInset() {
    const d = devicePixelRatio || 1, r = ic.getBoundingClientRect();
    ic.width = Math.round(r.width * d); ic.height = Math.round(r.height * d);
    const c = rec.contact, W = ic.width, H = ic.height;
    const left = Math.max(0.13, -c.lat + R + 0.06), right = Math.max(0.23, c.lat + R + 0.06);
    const s = W / (left + right), X = (lat) => (lat + left) * s, Y = H / 2;
    h.fillStyle = C.floor; h.fillRect(0, 0, X(0), H);
    h.fillStyle = C.surround; h.fillRect(X(0), 0, W - X(0), H);
    h.fillStyle = C.paint; h.fillRect(X(-0.0508), 0, 0.0508 * s, H);
    h.strokeStyle = C.ball; h.lineWidth = 2 * d; h.beginPath(); h.arc(X(c.lat), Y, R * s, 0, 7); h.stroke();
    h.fillStyle = C.verdict; h.beginPath(); h.ellipse(X(c.lat), Y, 0.012 * s, 0.03 * s, 0, 0, 7); h.fill();
    if (ev.gap_cm != null && ev.gap_cm > 0) {
      const edge = c.lat - R, yy = Y + R * s + 14 * d;
      if (edge > 0) {
        h.strokeStyle = C.ink; h.lineWidth = 1.5 * d;
        h.beginPath(); h.moveTo(X(0), yy); h.lineTo(X(edge), yy); h.stroke();
      }
      h.fillStyle = C.ink; h.font = `${Math.round(13 * d)}px system-ui`;
      h.fillText(`${ev.gap_cm.toFixed(1)} cm`, X(0) + 4 * d, yy + 18 * d);
    }
    h.fillStyle = C.muted; h.font = `${Math.round(11 * d)}px system-ui`;
    h.fillText("IN", 10 * d, 18 * d);
    h.fillText("OUT", W - 34 * d, 18 * d);
  }

  // ---------------------------------------------------------------- interaction
  function tick(now) {
    anim = Math.min(1, (now - t0) / 1600);
    draw();
    if (anim < 1) raf = requestAnimationFrame(tick);
  }
  function play() {
    cancelAnimationFrame(raf);
    if (reduce) { anim = 1; draw(); return; }
    anim = 0; t0 = performance.now(); raf = requestAnimationFrame(tick);
  }
  function setView(name) {
    cam = { ...VIEWS[name] };
    document.querySelectorAll("#replay [data-view]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === name)));
    if (rec) draw();
  }
  document.querySelectorAll("#replay [data-view]").forEach((b) => b.addEventListener("click", () => setView(b.dataset.view)));
  $("replayAgain").addEventListener("click", play);
  $("replayClose").addEventListener("click", close);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !overlay.hidden) close(); });
  cv.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, yaw: cam.yaw, pitch: cam.pitch }; cv.setPointerCapture(e.pointerId); });
  cv.addEventListener("pointermove", (e) => {
    if (!drag) return;
    cam.yaw = drag.yaw - (e.clientX - drag.x) * 0.008;
    cam.pitch = Math.max(0.05, Math.min(1.5, drag.pitch + (e.clientY - drag.y) * 0.006));
    draw();
  });
  cv.addEventListener("pointerup", () => { drag = null; });
  cv.addEventListener("wheel", (e) => { e.preventDefault(); cam.dist = Math.max(1, Math.min(12, cam.dist * (1 + e.deltaY * 0.001))); draw(); }, { passive: false });
  window.addEventListener("resize", () => { if (!overlay.hidden) { size(); drawInset(); draw(); } });

  window.openReplay = open;
})();
