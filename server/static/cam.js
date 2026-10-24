/* Phone camera page: getUserMedia -> WebRTC -> Mac. No framework, no CDN. */
(() => {
  const $ = (id) => document.getElementById(id);
  const qs = new URLSearchParams(location.search);
  const camId = localStorage.getItem("camId") || (() => {
    const id = Math.random().toString(36).slice(2, 10);
    localStorage.setItem("camId", id);
    return id;
  })();

  const nameSel = $("name"), customWrap = $("customWrap"), customName = $("customName");
  const qualitySel = $("quality"), startBtn = $("start"), errEl = $("err");
  const setup = $("setup"), live = $("live"), preview = $("preview");
  const camnameEl = $("camname"), statsEl = $("stats"), connEl = $("conn");
  const dimBtn = $("dim"), stopBtn = $("stop"), dimmer = $("dimmer"), dimstatus = $("dimstatus");
  const zoomWrap = $("zoomWrap"), zoom = $("zoom");

  let stream = null, track = null, pc = null, wakeLock = null, statsTimer = null;
  let reconnectTimer = null, graceTimer = null, backoff = 2000, connecting = false;
  let name = "", constraints = null;
  let lastBytes = 0, lastFrames = 0, lastStatsT = 0;
  let stopped = false;

  // remember the last choice; allow ?name=...&quality=...&auto=1 for tests
  if (localStorage.getItem("camName")) selectName(localStorage.getItem("camName"));
  if (qs.get("name")) selectName(qs.get("name"));
  if (qs.get("quality")) qualitySel.value = qs.get("quality");
  nameSel.addEventListener("change", () => { customWrap.hidden = nameSel.value !== "__custom"; });
  customWrap.hidden = nameSel.value !== "__custom";

  function selectName(n) {
    const opt = [...nameSel.options].find((o) => o.value === n || o.text === n);
    if (opt) nameSel.value = opt.value;
    else { nameSel.value = "__custom"; customName.value = n; }
  }
  function chosenName() {
    return nameSel.value === "__custom" ? (customName.value.trim() || "Camera") : nameSel.value;
  }
  function log(...a) { console.log("[cam]", ...a); }
  function showErr(msg) { errEl.textContent = msg; errEl.hidden = false; }
  function backToSetup(msg) {
    stopped = true;
    clearInterval(statsTimer);
    clearTimeout(reconnectTimer); clearTimeout(graceTimer);
    if (pc) { try { pc.close(); } catch (e) {} pc = null; }
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = track = null;
    live.hidden = true; setup.hidden = false;
    startBtn.disabled = false; startBtn.textContent = "Start camera";
    stopped = false;
    if (msg) showErr(msg);
  }

  function buildConstraints() {
    const q = qualitySel.value;
    const dims = q === "1080" ? [1920, 1080] : q === "480" ? [854, 480] : [1280, 720];
    return {
      audio: false,
      video: { facingMode: { ideal: "environment" }, width: { ideal: dims[0] }, height: { ideal: dims[1] }, frameRate: { ideal: 60 } },
    };
  }

  async function openCamera() {
    const s = await navigator.mediaDevices.getUserMedia(constraints);
    const t = s.getVideoTracks()[0];
    // ask again for 60 fps with the full set, so no browser resets width/height
    try { await t.applyConstraints(constraints.video); } catch (e) { log("applyConstraints", e.name); }
    try { t.contentHint = "motion"; } catch (e) {}
    t.addEventListener("ended", onTrackEnded);
    t.addEventListener("mute", () => log("track muted"));
    t.addEventListener("unmute", () => log("track unmuted"));
    return [s, t];
  }

  async function onTrackEnded() {
    // iOS ends the track on interruptions (call, Siri, camera app); get a new one and swap it in
    log("track ended; reopening camera");
    if (stopped) return;
    try {
      [stream, track] = await openCamera();
      preview.srcObject = stream;
      const sender = pc && pc.getSenders().find((s) => s.track === null || s.track.kind === "video");
      if (sender) await sender.replaceTrack(track);
      else scheduleReconnect(0);
    } catch (e) {
      log("reopen failed", e.name);
      setConn("camera lost - tap Stop and start again", "bad");
    }
  }

  async function start() {
    errEl.hidden = true;
    startBtn.disabled = true;
    startBtn.textContent = "Starting…";
    name = chosenName();
    localStorage.setItem("camName", name);
    constraints = buildConstraints();
    try {
      [stream, track] = await openCamera();
    } catch (e) {
      startBtn.disabled = false; startBtn.textContent = "Start camera";
      if (!window.isSecureContext) return showErr("Camera blocked: this page must be opened over https. Use the QR code on the Mac.");
      return showErr("Camera not allowed: " + (e.name || e) + ". Check the browser's camera permission for this site.");
    }
    preview.srcObject = stream;
    setup.hidden = true; live.hidden = false;
    camnameEl.textContent = name;
    setupZoom();
    await requestWakeLock();
    await connect();
    statsTimer = setInterval(updateStats, 1000);
  }

  function setupZoom() {
    try {
      const caps = track.getCapabilities ? track.getCapabilities() : {};
      if (caps.zoom && caps.zoom.max > caps.zoom.min) {
        zoom.min = caps.zoom.min; zoom.max = Math.min(caps.zoom.max, 3); zoom.step = caps.zoom.step || 0.1;
        zoom.value = track.getSettings().zoom || caps.zoom.min;
        zoomWrap.hidden = false;
        zoom.addEventListener("input", () => {
          track.applyConstraints({ advanced: [{ zoom: Number(zoom.value) }] }).catch(() => {});
        });
      }
    } catch (e) {}
  }

  async function requestWakeLock() {
    if (!("wakeLock" in navigator)) {
      setConn("this browser cannot keep the screen on - set Auto-Lock to Never", "bad");
      return;
    }
    try {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { log("wake lock released"); wakeLock = null; });
    } catch (e) { log("wake lock", e.name); wakeLock = null; }
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && track && !stopped) requestWakeLock();
  });

  function waitIce(pc, ms) {
    return new Promise((resolve) => {
      if (pc.iceGatheringState === "complete") return resolve();
      const t = setTimeout(resolve, ms);
      pc.addEventListener("icegatheringstatechange", () => {
        if (pc.iceGatheringState === "complete") { clearTimeout(t); resolve(); }
      });
    });
  }

  function applySenderParams(tx) {
    try {
      const p = tx.sender.getParameters();
      if (!p.encodings || !p.encodings.length) p.encodings = [{}];
      Object.assign(p.encodings[0], { maxBitrate: 6_000_000, maxFramerate: 60, scaleResolutionDownBy: 1.0 });
      p.degradationPreference = "balanced";
      return tx.sender.setParameters(p).catch((e) => log("setParameters", e.name));
    } catch (e) { log("sender params", e.name); return Promise.resolve(); }
  }

  // Chrome starts around 300 kb/s and climbs for 10-30 s, sending 640x360 meanwhile. libwebrtc reads
  // x-google-start-bitrate from the REMOTE description, so the hint goes into the Mac's answer.
  function mungeAnswer(sdp) {
    return sdp.replace(/a=fmtp:(\d+) ([^\r\n]*)\r?\n/g, (m, pt, params) =>
      (/apt=|x-google-start-bitrate/.test(params)) ? m
        : `a=fmtp:${pt} ${params};x-google-start-bitrate=4000;x-google-min-bitrate=1500;x-google-max-bitrate=8000\r\n`);
  }

  async function connect() {
    if (stopped || connecting) return;
    connecting = true;
    setConn("connecting to Mac", "");
    try {
      if (pc) { try { pc.close(); } catch (e) {} }
      pc = new RTCPeerConnection({ iceServers: [] });
      const tx = pc.addTransceiver(track, { direction: "sendonly", streams: [stream] });
      try {
        const caps = RTCRtpSender.getCapabilities && RTCRtpSender.getCapabilities("video");
        if (caps && tx.setCodecPreferences) {
          const h264 = caps.codecs.filter((c) => /h264/i.test(c.mimeType));
          const rest = caps.codecs.filter((c) => !/h264/i.test(c.mimeType));
          if (h264.length) tx.setCodecPreferences([...h264, ...rest]);
        }
      } catch (e) { log("codec prefs", e.name); }
      await applySenderParams(tx);
      pc.addEventListener("connectionstatechange", () => {
        log("pc", pc.connectionState);
        if (pc.connectionState === "connected") { backoff = 2000; clearTimeout(graceTimer); setConn("streaming to Mac", "ok"); }
        if (pc.connectionState === "failed") { setConn("lost the Mac, reconnecting…", "bad"); scheduleReconnect(0); }
        if (pc.connectionState === "disconnected") {
          // Wi-Fi hiccups recover on their own; only rebuild if it stays down
          setConn("Wi-Fi hiccup…", "bad");
          clearTimeout(graceTimer);
          graceTimer = setTimeout(() => { if (pc && pc.connectionState !== "connected") scheduleReconnect(0); }, 5000);
        }
      });
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      await waitIce(pc, 2500);
      const s = track.getSettings();
      const ctrl = new AbortController();
      const kill = setTimeout(() => ctrl.abort(), 8000);
      const res = await fetch("/offer", {
        method: "POST", headers: { "content-type": "application/json" }, signal: ctrl.signal,
        body: JSON.stringify({
          sdp: pc.localDescription.sdp, type: pc.localDescription.type, cam_id: camId, name,
          settings: { width: s.width, height: s.height, frameRate: s.frameRate, facingMode: s.facingMode, ua: navigator.userAgent },
        }),
      });
      clearTimeout(kill);
      if (res.status === 409) {
        const body = await res.json().catch(() => ({}));
        backToSetup(body.error || "That line already has a phone. Pick another name.");
        return;
      }
      if (!res.ok) throw new Error("offer rejected " + res.status);
      const ans = await res.json();
      await pc.setRemoteDescription({ type: ans.type, sdp: mungeAnswer(ans.sdp) });
      await applySenderParams(tx); // some browsers only honour parameters after negotiation
    } catch (e) {
      log("connect failed", e && e.name, e && e.message);
      setConn("cannot reach the Mac, retrying…", "bad");
      scheduleReconnect();
    } finally {
      connecting = false;
    }
  }

  function scheduleReconnect(delay) {
    if (stopped || reconnectTimer) return;
    const wait = delay == null ? backoff : delay;
    backoff = Math.min(backoff * 2, 15000);
    reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, wait);
  }

  function setConn(text, cls) {
    connEl.textContent = text; connEl.className = "conn " + cls;
    dimstatus.className = cls;
  }

  async function updateStats() {
    if (!pc || !track) return;
    const s = track.getSettings();
    let fps = null, kbps = null;
    try {
      const stats = await pc.getStats();
      const now = performance.now();
      stats.forEach((r) => {
        if (r.type === "outbound-rtp" && r.kind === "video") {
          if (lastStatsT) {
            const dt = (now - lastStatsT) / 1000;
            fps = (r.framesSent - lastFrames) / dt;
            kbps = ((r.bytesSent - lastBytes) * 8) / dt / 1000;
          }
          lastFrames = r.framesSent; lastBytes = r.bytesSent; lastStatsT = now;
        }
      });
    } catch (e) {}
    const cam = `${s.width}×${s.height}`;
    const camFps = s.frameRate ? `${Math.round(s.frameRate)} fps camera` : "";
    const sent = fps != null ? ` · sending ${Math.round(fps)} fps · ${(kbps / 1000).toFixed(1)} Mb/s` : "";
    statsEl.textContent = `${cam} · ${camFps}${sent}`;
    dimstatus.textContent = `${name} · ${Math.round(fps || 0)} fps`;
  }

  dimBtn.addEventListener("click", () => { dimmer.hidden = false; });
  dimmer.addEventListener("click", () => { dimmer.hidden = true; });
  stopBtn.addEventListener("click", () => backToSetup(""));
  startBtn.addEventListener("click", start);

  if (qs.get("auto") === "1") start();
})();
