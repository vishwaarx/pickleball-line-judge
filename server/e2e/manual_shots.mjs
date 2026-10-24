// Screenshots for the user manual, taken from the live system (one fake phone streaming).
// node manual_shots.mjs <outdir> <clip.y4m> [base]
import { chromium, devices } from "playwright";
import fs from "node:fs";

const out = process.argv[2], clip = process.argv[3], base = process.argv[4] || "https://localhost:8443";
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
    `--use-file-for-fake-video-capture=${clip}`, "--ignore-certificate-errors", "--autoplay-policy=no-user-gesture-required"],
});

// ---- dashboard
const desk = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const d = await desk.newPage();
await d.goto(base + "/", { waitUntil: "networkidle" });
await d.waitForTimeout(4000);
await d.screenshot({ path: `${out}/dashboard.png` });

// calibration dialog, three clicks in
const tile = d.locator(".tile").first();
await tile.locator(".act-calib").click();
await d.waitForSelector("#calibImg");
await d.waitForFunction(() => { const i = document.getElementById("calibImg"); return i.complete && i.naturalWidth > 0; });
await d.waitForTimeout(400);
const box = await d.locator("#calibCanvas").boundingBox();
const at = (fx, fy) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
let p = at(0.5, 0.24); await d.mouse.click(p.x, p.y);
await d.waitForTimeout(200);
p = at(0.5, 0.74); await d.mouse.click(p.x, p.y);
await d.waitForTimeout(200);
await d.screenshot({ path: `${out}/calibrate-2.png` });
p = at(0.33, 0.55); await d.mouse.click(p.x, p.y);
await d.waitForTimeout(300);
await d.screenshot({ path: `${out}/calibrate-3.png` });
await d.locator("#calibCancel").click();

// join info + settings open
await d.locator(".tools details").nth(1).evaluate((el) => (el.open = true));
await d.locator(".tools details").nth(0).evaluate((el) => (el.open = true));
await d.waitForTimeout(300);
await d.locator(".side").screenshot({ path: `${out}/side-panel.png` });
await desk.close();

// ---- phone
const phone = await browser.newContext({ ...devices["iPhone 13"], ignoreHTTPSErrors: true });
const pg = await phone.newPage();
await pg.goto(base + "/cam", { waitUntil: "networkidle" });
await pg.waitForTimeout(400);
await pg.screenshot({ path: `${out}/phone-setup.png` });
await pg.selectOption("#name", { label: "Right sideline" });
await pg.locator("#start").click();
await pg.waitForTimeout(7000);
await pg.screenshot({ path: `${out}/phone-live.png` });
await pg.locator("#dim").click();
await pg.waitForTimeout(1500);
await pg.screenshot({ path: `${out}/phone-dim.png` });
await pg.locator("#dimmer").click();
await pg.locator("#stop").click();
await phone.close();
await browser.close();
console.log("manual screenshots written to", out);
