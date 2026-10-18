// README images from the running app: the INC-302 case file, the fix proved on a graph copy, and real
// RCAEval telemetry. Dark theme, 1440x900 at 2x.     node demo/screens.mjs docs/img   (app on :8090)
import { chromium } from "playwright-core";

const [out = "docs/img"] = process.argv.slice(2);
const APP = process.env.APP || "http://localhost:8090";
await fetch(`${APP}/api/reset`, { method: "POST" });
await fetch(`${APP}/api/kestrel/incidents/INC-302/investigate`, { method: "POST" });   // the agents propose the fix
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2, colorScheme: "dark" })).newPage();
const caseTo = (text) => page.evaluate((t) => {
  const pane = document.querySelector("#case");
  const sec = [...pane.querySelectorAll("section, .sec, h3, h4, .label")].find((e) => e.textContent.trim().toUpperCase().startsWith(t.toUpperCase()));
  pane.scrollTo({ top: sec ? sec.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 16 : 0 });
}, text);

await page.goto(`${APP}/app?tenant=kestrel&incident=INC-302`);
await page.waitForSelector("#case .sec", { timeout: 30000 });
await page.waitForTimeout(2500);
await page.screenshot({ path: `${out}/case-file.png` });
await caseTo("Proposed fixes");
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/proof.png` });

await page.goto(`${APP}/app?tenant=boutique&incident=INC-404`);
await page.waitForSelector("#case .sec", { timeout: 30000 });
await page.waitForTimeout(2000);
await caseTo("Timeline");
await page.waitForTimeout(800);
await page.screenshot({ path: `${out}/real-data.png` });
await browser.close();
console.log("wrote case-file.png, proof.png, real-data.png to", out);
