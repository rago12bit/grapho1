// The README's GIF: the four agents running on INC-302 in the real app, recorded headless.
//   node demo/gif.mjs data/demo/gif        (app on :8090), then demo/gif.sh turns it into docs/img/hypocenter.gif
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const [outDir = "data/demo/gif"] = process.argv.slice(2);
const APP = process.env.APP || "http://localhost:8090";
fs.mkdirSync(outDir, { recursive: true });
await fetch(`${APP}/api/reset`, { method: "POST" });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 810 }, colorScheme: "dark",
  recordVideo: { dir: outDir, size: { width: 1440, height: 810 } } });
const page = await ctx.newPage();
const sleep = (ms) => page.waitForTimeout(ms);
const caseTo = (text) => page.evaluate((t) => {
  const pane = document.querySelector("#case");
  const sec = [...pane.querySelectorAll("section, .sec")].find((e) => e.textContent.trim().toUpperCase().startsWith(t.toUpperCase()));
  if (sec) pane.scrollTo({ top: sec.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 12, behavior: "smooth" });
}, text);

const t0 = Date.now();
await page.goto(`${APP}/app?tenant=kestrel&incident=INC-302`);
await page.waitForSelector("#case .sec", { timeout: 30000 });
await sleep(2200);
await page.locator("#go").click();                      // the four agents, streamed step by step
await page.waitForSelector("#handoffs", { timeout: 60000 });
await sleep(1200);
await caseTo("Proposed fixes");                          // the fix, proved on a copy of the graph
await sleep(3200);
await caseTo("The argument");                            // hypotheses with evidence for and against
await sleep(3000);
const video = page.video();
await ctx.close();
await browser.close();
fs.renameSync(await video.path(), path.join(outDir, "clip.webm"));
fs.writeFileSync(path.join(outDir, "start.txt"), "1.2\n");   // seconds of page load to trim
console.log("recorded", ((Date.now() - t0) / 1000).toFixed(1), "s");
