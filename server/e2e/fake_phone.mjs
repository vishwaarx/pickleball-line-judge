// One fake phone: Chrome's fake camera plays a .y4m clip into the real /cam page over WebRTC.
// node fake_phone.mjs --name "Left sideline" --clip /path/demo30.y4m [--url https://localhost:8443/cam] [--seconds 60]
import { chromium } from "playwright";

const args = Object.fromEntries(
  process.argv.slice(2).reduce((acc, a, i, arr) => {
    if (a.startsWith("--")) acc.push([a.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : "1"]);
    return acc;
  }, [])
);
const url = args.url || "https://localhost:8443/cam";
const name = args.name || "Fake phone";
const clip = args.clip;
const seconds = Number(args.seconds || 60);
const quality = args.quality || "720";
if (!clip) { console.error("--clip <file.y4m> is required"); process.exit(2); }

const tag = `[${name}]`;
const browser = await chromium.launch({
  headless: true,
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    `--use-file-for-fake-video-capture=${clip}`,
    "--ignore-certificate-errors",
    "--autoplay-policy=no-user-gesture-required",
  ],
});
const context = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 420, height: 860 } });
const page = await context.newPage();
page.on("console", (m) => { if (m.type() !== "debug") console.log(`${tag} console: ${m.text()}`); });
page.on("pageerror", (e) => console.log(`${tag} pageerror: ${e.message}`));

const full = `${url}?name=${encodeURIComponent(name)}&quality=${quality}&auto=1`;
console.log(`${tag} opening ${full}`);
await page.goto(full, { waitUntil: "load" });

const t0 = Date.now();
let lastLine = "";
while (Date.now() - t0 < seconds * 1000) {
  await page.waitForTimeout(2000);
  const conn = await page.locator("#conn").textContent().catch(() => "?");
  const stats = await page.locator("#stats").textContent().catch(() => "?");
  const err = await page.locator("#err").textContent().catch(() => "");
  const line = `${conn} | ${stats}${err ? " | ERR " + err : ""}`;
  if (line !== lastLine) { console.log(`${tag} ${line}`); lastLine = line; }
}
console.log(`${tag} done after ${seconds}s`);
await browser.close();
