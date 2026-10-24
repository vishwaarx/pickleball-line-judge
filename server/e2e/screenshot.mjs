// Screenshots of the dashboard (laptop) and the phone page (phone) for a visual review.
// node screenshot.mjs <outdir> [base]
import { chromium, devices } from "playwright";

const out = process.argv[2] || ".";
const base = process.argv[3] || "https://localhost:8443";
const browser = await chromium.launch({ headless: true, args: ["--ignore-certificate-errors"] });

const desk = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
const d = await desk.newPage();
await d.goto(base + "/", { waitUntil: "networkidle" });
await d.waitForTimeout(1500);
await d.screenshot({ path: `${out}/dashboard.png` });
// simulate a verdict so the band can be reviewed
await d.evaluate(() => {
  const ev = { verdict: "OUT", cam_name: "Left sideline", gap_cm: 3.4, wall_time: "18:42:10", fps_in: 30, n_before: 4, n_after: 3, reason: "", announce: true, strip_url: "" };
  window.__ws && window.__ws.onmessage({ data: JSON.stringify({ type: "event", event: ev }) });
});
await d.waitForTimeout(400);
await d.screenshot({ path: `${out}/dashboard-out.png` });
await d.evaluate(() => {
  const ev = { verdict: "TOO_CLOSE", cam_name: "Near baseline", gap_cm: 1.1, wall_time: "18:42:31", fps_in: 60, n_before: 4, n_after: 4, reason: "", announce: true, strip_url: "" };
  window.__ws && window.__ws.onmessage({ data: JSON.stringify({ type: "event", event: ev }) });
});
await d.waitForTimeout(400);
await d.screenshot({ path: `${out}/dashboard-close.png` });
await desk.close();

const phone = await browser.newContext({ ...devices["iPhone 13"], ignoreHTTPSErrors: true });
const p = await phone.newPage();
await p.goto(base + "/cam", { waitUntil: "networkidle" });
await p.waitForTimeout(500);
await p.screenshot({ path: `${out}/phone-setup.png` });
await phone.close();
await browser.close();
console.log("screenshots written to", out);
