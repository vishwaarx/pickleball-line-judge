/* Court-side dashboard. State and previews arrive over one WebSocket. */
(() => {
  const $ = (id) => document.getElementById(id);
  const band = $("band"), word = $("word"), bandCam = $("bandCam"), bandGap = $("bandGap"), bandMeta = $("bandMeta");
  const tiles = $("tiles"), emptyCams = $("emptyCams"), tileTpl = $("tileTpl");
  const strip = $("strip"), stripLink = $("stripLink"), stripEmpty = $("stripEmpty"), logEl = $("log");
  const replayBtn = $("replayBtn");
  const marginIn = $("margin"), announceIn = $("announce"), speakIn = $("speak");
  const tileEls = new Map();
  const calls = new Map();  // key -> latest copy of each call, so later patches (Jev, replay) are seen
  let state = null, bandTimer = null, settingsDirty = false, shownKey = null;

  const LABEL = { IN: "In", OUT: "Out", TOO_CLOSE: "Close · in", NO_CALL: "No call" };
  // TOO CLOSE counts as IN (rule 8.D), so the big word says so; the band colour and the detail say it was close
  const BAND_WORD = { IN: "In", OUT: "Out", TOO_CLOSE: "In", NO_CALL: "No call" };
  const keyOf = (ev) => ev.uid || `${ev.cam_id}:${ev.seq}`;

  function connect() {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
    ws.onmessage = (m) => {
      const msg = JSON.parse(m.data);
      if (msg.type === "state") onState(msg);
      else if (msg.type === "preview") onPreview(msg);
      else if (msg.type === "event") msg.update ? onEventUpdate(msg.event) : onEvent(msg.event, msg.replay);
    };
    ws.onclose = () => setTimeout(connect, 1000);
    window.__ws = ws;
  }

  function onState(s) {
    state = s;
    $("joinUrl").textContent = s.join_url;
    $("joinUrl2").textContent = s.join_url;
    $("hosts").textContent = s.hosts.length > 1 ? "Other addresses: " + s.hosts.slice(1).join(", ") : "";
    if (!settingsDirty) {
      marginIn.value = s.settings.margin_cm;
      announceIn.value = s.settings.announce_window_cm;
      speakIn.checked = s.settings.speak;
    }
    const ids = new Set(s.cams.map((c) => c.id));
    for (const [id, el] of tileEls) if (!ids.has(id)) { el.remove(); tileEls.delete(id); }
    for (const cam of s.cams) renderTile(cam);
    emptyCams.hidden = s.cams.length > 0;
    if (band.classList.contains("idle")) {
      bandCam.textContent = s.cams.length ? `${s.cams.length} camera${s.cams.length > 1 ? "s" : ""} live` : "no cameras yet";
    }
  }

  function renderTile(cam) {
    let el = tileEls.get(cam.id);
    if (!el) {
      el = tileTpl.content.firstElementChild.cloneNode(true);
      tileEls.set(cam.id, el);
      tiles.appendChild(el);
      el.querySelector(".act-calib").addEventListener("click", () => openCalib(cam.id));
      el.querySelector(".act-guard").addEventListener("click", async () => {
        // the server resumes calls only if the paint is back where the line was calibrated
        const res = await fetch(`/api/cam/${cam.id}/reset_guard`, { method: "POST" });
        const body = await res.json().catch(() => ({}));
        if (body.ok === false) el.querySelector(".tile-note").textContent = `Still moved: ${body.error}.`;
      });
      el.querySelector(".act-color").addEventListener("click", () => fetch(`/api/cam/${cam.id}/clear_ball`, { method: "POST" }));
      el.querySelector(".act-remove").addEventListener("click", () => {
        if (confirm(`Remove ${cam.name}?`)) fetch(`/api/cam/${cam.id}/remove`, { method: "POST" });
      });
    }
    el.querySelector(".tile-name").textContent = cam.name;
    const pill = el.querySelector(".pill");
    const status = cam.status === "LIVE" && !cam.calibrated ? "UNCAL" : cam.status;
    pill.textContent = status === "UNCAL" ? "LIVE · not calibrated" : cam.status;
    pill.className = "pill " + status.replace(" ", "");
    const cap = cam.capture ? `${cam.capture[0]}×${cam.capture[1]}` : "";
    el.querySelector(".tile-fps").textContent = cam.status === "NO SIGNAL" ? "waiting for phone" : `${cap}, ${cam.fps_in} fps in, ${cam.fps_proc} fps judged`;
    el.querySelector(".act-guard").hidden = cam.status !== "MOVED";
    const colorBtn = el.querySelector(".act-color");
    colorBtn.hidden = !cam.ball_bgr;
    if (cam.ball_bgr) colorBtn.style.setProperty("--swatch", `rgb(${cam.ball_bgr[2]},${cam.ball_bgr[1]},${cam.ball_bgr[0]})`);
    const note = el.querySelector(".tile-note");
    if (cam.status === "MOVED") note.textContent = (cam.moved_reason || "Camera moved") + ". Calls paused.";
    else if (!cam.calibrated && cam.status === "LIVE") note.textContent = "Click Calibrate and mark the line.";
    else if (cam.calibrated && cam.status === "LIVE" && !cam.paint_scale) note.textContent = "Paint width not measured yet — scale falls back to ball size.";
    else if (cam.fps_in && cam.fps_in < 24) note.textContent = `Only ${cam.fps_in} fps from this phone — fast balls may get NO CALL.`;
    else note.textContent = "";
  }

  function onPreview(msg) {
    const el = tileEls.get(msg.cam);
    if (el) el.querySelector("img").src = "data:image/jpeg;base64," + msg.jpg;
  }

  function onEvent(ev, replay) {
    addLog(ev);
    if (ev.strip_url) showStrip(ev);
    if (!replay && (ev.announce || ev.verdict !== "IN")) showBand(ev);
  }

  function showBand(ev) {
    clearTimeout(bandTimer);
    band.className = "band " + ev.verdict;
    word.textContent = BAND_WORD[ev.verdict] || ev.verdict;
    bandCam.textContent = ev.cam_name;
    const bits = [ev.wall_time, `${ev.fps_in} fps`, `${ev.n_before}+${ev.n_after} frames around the bounce`];
    if (ev.verdict === "TOO_CLOSE") {
      bandGap.textContent = "Too close to call";
      if (ev.gap_cm != null) bits.unshift(`${ev.gap_cm.toFixed(1)} cm gap, inside the margin`);
    } else if (ev.gap_cm != null) {
      const g = ev.gap_cm;
      bandGap.textContent = g > 0 ? `${g.toFixed(1)} cm out` : `${Math.abs(g).toFixed(1)} cm inside`;
    } else bandGap.textContent = "";
    if (ev.reason) bits.push(ev.reason);
    bandMeta.textContent = bits.join(",  ");
    bandTimer = setTimeout(() => {
      band.className = "band idle";
      word.textContent = "Watching";
      bandGap.textContent = ""; bandMeta.textContent = "";
      bandCam.textContent = state && state.cams.length ? `${state.cams.length} cameras live` : "";
    }, 9000);
  }

  function showStrip(ev) {
    strip.src = ev.strip_url + "?t=" + Date.now();
    strip.hidden = false;
    stripLink.href = ev.strip_url;
    stripEmpty.hidden = true;
    shownKey = keyOf(ev);
    replayBtn.hidden = !ev.replay;
  }
  replayBtn.addEventListener("click", () => { const ev = calls.get(shownKey); if (ev) window.openReplay(ev); });

  function confirmationText(ev) {
    const c = ev.confirmation;
    if (!c) return "";
    const ball = c.is_ball == null ? "" : c.is_ball >= 0.5 ? "ball ✓" : "ball ?";
    const agree = c.agrees ? "frame ✓" : c.touch_tile != null ? `frame ${c.touch_tile}` : "";
    return ` · Jev: ${[ball, agree].filter(Boolean).join(" ")}`;
  }
  function logText(ev) {
    const gap = ev.gap_cm == null ? ""
      : ev.verdict === "TOO_CLOSE" ? `too close, ${ev.gap_cm.toFixed(1)} cm gap`
      : ev.gap_cm > 0 ? `${ev.gap_cm.toFixed(1)} cm out` : `${Math.abs(ev.gap_cm).toFixed(1)} cm in`;
    return `${ev.cam_name} ${gap ? "· " + gap : ""}${ev.reason ? " · " + ev.reason : ""}${confirmationText(ev)}`;
  }
  function addLog(ev) {
    const key = keyOf(ev);
    if (logEl.querySelector(`li[data-key="${key}"]`)) return;  // replayed on reconnect
    calls.set(key, ev);
    const li = document.createElement("li");
    li.className = ev.verdict;
    li.dataset.key = key;
    li.innerHTML = `<span class="v">${LABEL[ev.verdict] || ev.verdict}</span><span class="d"></span><span class="t">${ev.wall_time}</span>`;
    li.querySelector(".d").textContent = logText(ev);
    li.addEventListener("click", () => { const cur = calls.get(key) || ev; if (cur.strip_url) showStrip(cur); showBand(cur); });
    logEl.prepend(li);
    while (logEl.children.length > 60) { calls.delete(logEl.lastChild.dataset.key); logEl.lastChild.remove(); }
  }
  function onEventUpdate(ev) {
    const key = keyOf(ev);
    const cur = calls.get(key);
    if (cur) Object.assign(cur, ev);
    const li = logEl.querySelector(`li[data-key="${key}"]`);
    if (li) li.querySelector(".d").textContent = logText(cur || ev);
    if (key === shownKey) replayBtn.hidden = !(cur || ev).replay;
  }

  // ---- settings
  function pushSettings() {
    settingsDirty = false;
    fetch("/api/settings", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ margin_cm: Number(marginIn.value), announce_window_cm: Number(announceIn.value), speak: speakIn.checked }) });
  }
  for (const el of [marginIn, announceIn]) {
    el.addEventListener("input", () => { settingsDirty = true; });
    el.addEventListener("change", pushSettings);
  }
  speakIn.addEventListener("change", pushSettings);
  $("testSay").addEventListener("click", () => fetch("/api/say", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ text: "Out by 3. Left sideline." }) }));

  // ---- calibration
  const calib = $("calib"), calibImg = $("calibImg"), canvas = $("calibCanvas"), ctx = canvas.getContext("2d");
  const stepEl = $("calibStep"), saveBtn = $("calibSave"), skipBtn = $("calibSkip"), undoBtn = $("calibUndo");
  const STEPS = [
    "1 of 4 — click the FAR end of the line's outer edge",
    "2 of 4 — click the NEAR end of the line's outer edge",
    "3 of 4 — click anywhere on the court side (IN)",
    "4 of 4 — click the ball if one is visible, or skip",
    "Check the drawing, then Save",
  ];
  let calibCam = null, clicks = [];

  async function openCalib(camId) {
    calibCam = camId; clicks = [];
    const cam = state.cams.find((c) => c.id === camId);
    $("calibTitle").textContent = `Calibrate — ${cam ? cam.name : camId}`;
    calibImg.src = `/api/snapshot/${camId}.jpg?t=${Date.now()}`;
    calib.hidden = false;
    calibImg.onload = () => { fitCanvas(); draw(); };
    updateStep();
  }
  function fitCanvas() {
    const r = calibImg.getBoundingClientRect(), s = calibImg.parentElement.getBoundingClientRect();
    canvas.width = calibImg.naturalWidth; canvas.height = calibImg.naturalHeight;
    canvas.style.width = r.width + "px"; canvas.style.height = r.height + "px";
    canvas.style.left = (r.left - s.left) + "px"; canvas.style.top = (r.top - s.top) + "px";
  }
  window.addEventListener("resize", () => { if (!calib.hidden) { fitCanvas(); draw(); } });
  canvas.addEventListener("click", (e) => {
    if (clicks.length >= 4) return;
    const r = canvas.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width * canvas.width;
    const y = (e.clientY - r.top) / r.height * canvas.height;
    clicks.push([x, y]);
    updateStep(); draw();
  });
  undoBtn.addEventListener("click", () => { clicks.pop(); updateStep(); draw(); });
  skipBtn.addEventListener("click", () => { clicks[3] = null; clicks.length = 4; updateStep(); draw(); });
  $("calibCancel").addEventListener("click", () => { calib.hidden = true; });
  saveBtn.addEventListener("click", async () => {
    // the snapshot's own size travels with the clicks, so a resolution change in between cannot misplace the line
    const body = { p1: clicks[0], p2: clicks[1], in: clicks[2], ball: clicks[3] || null, coords: "work",
      view: [canvas.width, canvas.height] };
    const res = await fetch(`/api/cam/${calibCam}/calibrate`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (res.ok) { calib.hidden = true; return; }
    // keep the overlay open so Undo can fix the clicks; say what was wrong
    const err = await res.json().then((b) => b.error).catch(() => null);
    stepEl.textContent = err || (res.status === 503 ? "No picture from this phone yet. Wait a moment and try again." : `Could not save (error ${res.status}).`);
    stepEl.classList.add("error");
  });
  function updateStep() {
    const n = clicks.length;
    stepEl.classList.remove("error");
    stepEl.textContent = STEPS[Math.min(n, 4)];
    skipBtn.hidden = n !== 3;
    saveBtn.hidden = n < 4;
  }
  function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const lw = Math.max(2, canvas.width / 400);
    ctx.lineWidth = lw; ctx.font = `${Math.round(canvas.width / 50)}px system-ui`;
    const pt = (p, color, label) => {
      ctx.strokeStyle = color; ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(p[0], p[1], lw * 4, 0, Math.PI * 2); ctx.stroke();
      ctx.fillText(label, p[0] + lw * 6, p[1] - lw * 4);
    };
    if (clicks[0]) pt(clicks[0], "#2fd16a", "far");
    if (clicks[1]) {
      pt(clicks[1], "#2fd16a", "near");
      ctx.strokeStyle = "#2fd16a"; ctx.beginPath(); ctx.moveTo(...clicks[0]); ctx.lineTo(...clicks[1]); ctx.stroke();
    }
    if (clicks[2]) {
      pt(clicks[2], "#e6ff3d", "IN side");
      // shade the IN half-plane lightly
      const [a, b] = [clicks[0], clicks[1]];
      const nx = -(b[1] - a[1]), ny = b[0] - a[0];
      const s = Math.sign((clicks[2][0] - a[0]) * nx + (clicks[2][1] - a[1]) * ny) || 1;
      const L = Math.hypot(canvas.width, canvas.height) * 2;
      const dx = (b[0] - a[0]), dy = (b[1] - a[1]), len = Math.hypot(dx, dy) || 1;
      const ux = dx / len, uy = dy / len, vx = (nx / len) * s, vy = (ny / len) * s;
      ctx.fillStyle = "rgba(47, 209, 106, 0.12)";
      ctx.beginPath();
      ctx.moveTo(a[0] - ux * L, a[1] - uy * L); ctx.lineTo(a[0] + ux * L, a[1] + uy * L);
      ctx.lineTo(a[0] + ux * L + vx * L, a[1] + uy * L + vy * L); ctx.lineTo(a[0] - ux * L + vx * L, a[1] - uy * L + vy * L);
      ctx.closePath(); ctx.fill();
    }
    if (clicks[3]) pt(clicks[3], "#ff80ff", "ball");
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") calib.hidden = true; });

  connect();
})();
