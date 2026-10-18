// The README's Screens: twelve pages of the running app at 1440x900 and two at phone width, dark theme.
//   node demo/gallery.mjs docs/media/screens        (app on :8090; resets the demo data)
import { chromium } from "playwright-core";

const [out = "docs/media/screens"] = process.argv.slice(2);
const APP = process.env.APP || "http://localhost:8090";
await fetch(`${APP}/api/reset`, { method: "POST" });
await fetch(`${APP}/api/kestrel/incidents/INC-302/investigate`, { method: "POST" });
const browser = await chromium.launch();
const desk = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, colorScheme: "dark", isMobile: true, hasTouch: true });
const p = await desk.newPage();
const shot = (page, name) => page.screenshot({ path: `${out}/${name}.png` });
const into = (page, sel, off = 70) => page.evaluate(([s, o]) => {
  const el = document.querySelector(s); if (el) window.scrollTo(0, el.getBoundingClientRect().top + window.scrollY - o);
}, [sel, off]);
const caseTo = (page, text) => page.evaluate((t) => {
  const pane = document.querySelector("#case");
  const sec = [...pane.querySelectorAll("section, .sec")].find((e) => e.textContent.trim().toUpperCase().startsWith(t.toUpperCase()));
  if (sec) pane.scrollTo({ top: sec.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 12 });
}, text);

await p.goto(`${APP}/`); await p.waitForTimeout(3200); await shot(p, "01-landing");

await p.goto(`${APP}/app?tenant=kestrel&incident=INC-302`); await p.waitForSelector("#case .sec"); await p.waitForTimeout(2200);
await caseTo(p, "The argument"); await p.waitForTimeout(700); await shot(p, "02-argument");

await p.goto(`${APP}/app?tenant=boutique&incident=INC-404`); await p.waitForSelector("#case .sec"); await p.waitForTimeout(2200);
await caseTo(p, "Timeline"); await p.waitForTimeout(700); await shot(p, "03-real-failure");

await p.goto(`${APP}/replay?tenant=kestrel&incident=INC-302`); await p.waitForTimeout(1500);
await p.click("#play"); await p.waitForTimeout(9000); await shot(p, "04-replay");

await p.goto(`${APP}/break`); await p.waitForTimeout(1500);
await p.click('button.exp[data-id="cut-path"]'); await p.waitForTimeout(400); await p.click("#go"); await p.waitForTimeout(3500);
await shot(p, "05-break-it");

await p.evaluate(() => localStorage.setItem("faultline.tenant", "kestrel"));    // the pages remember the last company
await p.goto(`${APP}/oncall`); await p.waitForTimeout(2500); await shot(p, "06-on-call");

await p.goto(`${APP}/explore`); await p.waitForTimeout(4500); await shot(p, "07-explore");

await p.goto(`${APP}/results`); await p.waitForTimeout(1800); await into(p, "#real"); await p.waitForTimeout(1500); await shot(p, "08-results");

await into(p, "#depth"); await p.waitForTimeout(3500); await shot(p, "09-depth");

await p.goto(`${APP}/app?tenant=kestrel`); await p.waitForSelector("#q"); await p.waitForTimeout(1500);
await p.fill("#q", "why was C-5101 made?"); await p.press("#q", "Enter"); await p.waitForTimeout(2500); await shot(p, "10-decision-trail");

await p.goto(`${APP}/app?tenant=kestrel&incident=INC-302`); await p.waitForSelector("#case .sec"); await p.waitForTimeout(2200);
await caseTo(p, "Evidence gate"); await p.waitForTimeout(700); await shot(p, "13-evidence-gate");

await p.goto(`${APP}/preflight?tenant=kestrel&service=auth&kind=deploy`); await p.waitForSelector(".pf-decision", { timeout: 30000 });
await p.waitForTimeout(800); await into(p, "#form", 90); await p.waitForTimeout(900); await shot(p, "14-ship-it");

const m = await phone.newPage();
await m.goto(`${APP}/`); await m.waitForTimeout(3200); await shot(m, "11-phone-landing");
await m.goto(`${APP}/oncall`); await m.waitForTimeout(2500); await shot(m, "12-phone-on-call");
await browser.close();
console.log("wrote 14 screens to", out);
