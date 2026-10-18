// Records the demo video of the real app, scene by scene, timed to the voiceover clips.
//   node demo/record.mjs <audio-dir> <out-dir>        (needs playwright-core 1.64.1 and the app on :8090)
// Writes <out-dir>/video.webm and <out-dir>/timeline.json (when each scene started, for the audio mix).
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const [audioDir, outDir] = process.argv.slice(2);
const APP = process.env.APP || "http://localhost:8090";
const W = 1440, H = 810;                          // laid out at 1440 px for readable text; upscaled to 1080p when mixed
const durations = Object.fromEntries(
  fs.readFileSync(path.join(audioDir, "durations.txt"), "utf8").trim().split("\n").map((l) => l.split(" ")).map(([k, v]) => [k, +v]));
const narration = JSON.parse(fs.readFileSync(new URL("./narration.json", import.meta.url), "utf8"));

await fetch(`${APP}/api/reset`, { method: "POST" });           // start from the clean demo state
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: W, height: H }, recordVideo: { dir: outDir, size: { width: W, height: H } } });
const HOLD = { s7_inc303: 800, s3_inc301: 600, s7d_memory: 800 };  // extra time where the UI needs it
const page = await ctx.newPage();
const t0 = Date.now();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const PACE = 0.6;                                   // the walkthrough runs at the same 1.5x pace as the voice
const pause = (ms) => sleep(ms * PACE);
const timeline = [];

// A visible cursor (headless video has none) and the title/number cards drawn over the app.
await page.addInitScript(() => {
  window.addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.style.cssText = "position:fixed;z-index:99999;width:22px;height:22px;border-radius:50%;border:2px solid #fff;" +
      "background:rgba(255,122,69,.55);pointer-events:none;transform:translate(-50%,-50%);transition:transform .12s;left:-50px;top:-50px";
    document.body.appendChild(c);
    window.addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; });
    window.addEventListener("mousedown", () => { c.style.transform = "translate(-50%,-50%) scale(.7)"; });
    window.addEventListener("mouseup", () => { c.style.transform = "translate(-50%,-50%)"; });
  });
});
await page.goto(`${APP}/app`);
await page.waitForSelector("#incident-list button");
await sleep(800);

async function card(html) {
  await page.evaluate((h) => {
    let o = document.getElementById("demo-card");
    if (!o) {
      o = document.createElement("div");
      o.id = "demo-card";
      o.style.cssText = "position:fixed;inset:0;z-index:99990;display:grid;place-items:center;background:#0d0f12;" +
        "color:#e6e8eb;font-family:'IBM Plex Sans',system-ui,sans-serif;opacity:0;transition:opacity .6s";
      document.body.appendChild(o);
    }
    o.innerHTML = h;
    const c = document.getElementById("demo-cursor");
    if (c) c.style.visibility = "hidden";
    requestAnimationFrame(() => (o.style.opacity = "1"));
  }, html);
}
async function uncard() {
  await page.evaluate(() => { const o = document.getElementById("demo-card"); if (o) o.style.opacity = "0"; });
  await sleep(650);
  await page.evaluate(() => {
    document.getElementById("demo-card")?.remove();
    const c = document.getElementById("demo-cursor");
    if (c) c.style.visibility = "visible";
  });
}
async function click(selector, text) {
  const el = text ? page.locator(selector, { hasText: text }).first() : page.locator(selector).first();
  await el.scrollIntoViewIfNeeded();
  const b = await el.boundingBox();
  await page.mouse.move(b.x + Math.min(b.width / 2, 120), b.y + b.height / 2, { steps: 14 });
  await sleep(150);
  await page.mouse.down(); await sleep(90); await page.mouse.up();
  await el.click({ force: true }).catch(() => {});
}
async function scrollRight(selector, block = "start", offset = 0) {
  await page.evaluate(([sel, blk, off]) => {
    const el = document.querySelector(sel), pane = document.querySelector(".right");
    if (!el || !pane) return;
    const top = el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop;
    const target = blk === "center" ? top - pane.clientHeight / 2 + el.clientHeight / 2 : top - off;
    pane.scrollTo({ top: target, behavior: "smooth" });
  }, [selector, block, offset]);
}
async function scene(id, body) {
  const start = (Date.now() - t0) / 1000;
  timeline.push({ id, start, text: narration.find((n) => n.id === id).text, duration: durations[id] });
  const until = Date.now() + durations[id] * 1000 + 300 + (HOLD[id] || 0);   // the clip, a short breath, any hold
  await body();
  const left = until - Date.now();
  if (left > 0) await sleep(left);
}

const titleCard = `<div style="text-align:center;max-width:1200px;zoom:.75">
  <svg viewBox="0 0 32 32" width="84" height="84" style="fill:none;stroke:#ff7a45;stroke-width:3"><path d="M4 26 L13 6 L17 17 L21 11 L28 26"/></svg>
  <div style="font-size:84px;font-weight:600;letter-spacing:.5px;margin-top:10px">Hypocenter</div>
  <div style="font-size:34px;color:#c3c8cf;margin-top:18px;line-height:1.35">Which change broke production,<br>and how did the failure get there?</div>
  <div style="font-size:20px;color:#8a93a0;margin-top:34px;font-family:'JetBrains Mono',monospace">an on-call agent on a FalkorDB graph · Graph Hacks 2026</div></div>`;

const evalCard = `<div style="width:1320px;zoom:.75">
  <div style="font-size:22px;letter-spacing:.12em;text-transform:uppercase;color:#8a93a0">Measured, not claimed</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-top:26px">
    <div style="border:1px solid #2f3743;border-radius:14px;padding:30px 34px">
      <div style="font-size:22px;color:#8a93a0">Planted faults · held-out seeds</div>
      <div style="font-size:92px;font-weight:600;color:#ff7a45;margin-top:6px">96–97.5%</div>
      <div style="font-size:24px;color:#c3c8cf">right cause first · 100% in top three</div>
      <div style="font-size:20px;color:#8a93a0;margin-top:14px">"blame the most recent change": 64%</div></div>
    <div style="border:1px solid #2f3743;border-radius:14px;padding:30px 34px">
      <div style="font-size:22px;color:#8a93a0">RCAEval · 36 unseen real failures</div>
      <div style="font-size:92px;font-weight:600;color:#ff7a45;margin-top:6px">86%</div>
      <div style="font-size:24px;color:#c3c8cf">right service first · 94% in top three</div>
      <div style="font-size:20px;color:#8a93a0;margin-top:14px">"blame the earliest alert": 86% · we publish both</div></div>
  </div>
  <div style="font-size:20px;color:#8a93a0;margin-top:26px;font-family:'JetBrains Mono',monospace">python -m faultline.eval · python -m faultline.rcaeval</div></div>`;

const closeCard = `<div style="text-align:center;zoom:.75">
  <svg viewBox="0 0 32 32" width="64" height="64" style="fill:none;stroke:#ff7a45;stroke-width:3"><path d="M4 26 L13 6 L17 17 L21 11 L28 26"/></svg>
  <div style="font-size:72px;font-weight:600;margin-top:8px">Hypocenter</div>
  <div style="font-size:30px;color:#c3c8cf;margin-top:16px;font-family:'JetBrains Mono',monospace">docker compose up -d --build</div>
  <div style="font-size:24px;color:#8a93a0;margin-top:26px">github.com/rago12bit/grapho1 · FalkorDB · Graph Hacks 2026</div></div>`;

await card(titleCard);
await pause(600);
await scene("s1_title", async () => {});
await uncard();

await scene("s2_graph", async () => {
  await page.mouse.move(W * 0.45, H * 0.35, { steps: 25 });
  await pause(2500);
  await page.mouse.move(W * 0.55, H * 0.55, { steps: 30 });
  await pause(2500);
  await page.mouse.move(250, 330, { steps: 20 });
});

await scene("s3_inc301", async () => {
  await click("#incident-list button", "INC-301");
  await pause(1500);
  await click("#go");
  await page.mouse.move(W * 0.42, H * 0.92, { steps: 20 });   // out of the way of the case file
  await page.waitForSelector("#handoffs", { timeout: 30000 });
  await page.evaluate(() => { document.querySelector("#handoffs").open = true; });
  await pause(800);
  await scrollRight("#steps", "start", 20);
});

await scene("s4_verdict", async () => {
  await scrollRight(".verdict-word", "start", 40);
  await pause(5000);
  await scrollRight(".sec.skeptic", "center");
  await pause(7000);
  await scrollRight(".shared", "center");
});

await scene("s5_approve", async () => {
  await page.waitForSelector("button[data-a][data-ok='1']", { timeout: 30000 });
  await scrollRight(".action", "center");
  await pause(1200);
  await click("button[data-a][data-ok='1']");
  await page.waitForSelector(".verify", { timeout: 30000 });   // approved and verified before moving on
  await pause(1000);
  await page.mouse.move(200, 300, { steps: 20 });
});

await scene("s6_inc302", async () => {
  await click("#incident-list button", "INC-302");
  await pause(1200);
  await click("#go");
  await page.mouse.move(W * 0.42, H * 0.92, { steps: 20 });
  await page.waitForSelector("#handoffs", { timeout: 30000 });
  await pause(1500);
  await scrollRight(".sec.groups", "center");
  await pause(5000);
  await scrollRight(".sec.others", "center");
});

// INC-303: let the agents run first, so the narration starts when the verdict is on screen.
await click("#incident-list button", "INC-303");
await pause(1000);
await click("#go");
await page.mouse.move(W * 0.42, H * 0.92, { steps: 20 });
await page.waitForSelector("#handoffs", { timeout: 30000 });
await page.evaluate(() => { document.querySelector("#handoffs").open = true; });
await pause(800);
await scrollRight(".verdict-word", "start", 40);
await pause(600);
await scene("s7_inc303", async () => {});

await scene("s7b_cypher", async () => {
  const sum = page.locator("#steps li", { hasText: "alert_clusters(" }).locator("summary").first();
  await scrollRight("#steps li:nth-child(1)", "start", 0);
  await page.evaluate(() => {
    const li = [...document.querySelectorAll("#steps li")].find((x) => x.textContent.includes("alert_clusters("));
    li?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
  await pause(900);
  const b = await sum.boundingBox();
  await page.mouse.move(b.x + 40, b.y + b.height / 2, { steps: 14 });
  await sum.click();
  await pause(600);
  await page.mouse.move(W * 0.42, H * 0.92, { steps: 14 });
});

await scene("s7c_whatif", async () => {
  await click("#q");
  await page.keyboard.type("what breaks if ledger-db goes down?", { delay: 12 });
  await page.keyboard.press("Enter");
  await pause(800);
  await page.evaluate(() => document.querySelector(".right").scrollTo({ top: 0 }));
  await page.mouse.move(W * 0.5, H * 0.45, { steps: 20 });
});

await scene("s7d_memory", async () => {
  await click("#incident-list button", "INC-301");
  await pause(900);
  await scrollRight("#lesson", "center");
  await pause(500);
  await click("#lesson");
  await page.keyboard.type("Pool limits on payments-db need a load test before they ship.", { delay: 18 });
  await pause(300);
  await click("#resolve");
  await pause(900);
  await click(".tab", "Memory");
  await page.mouse.move(W * 0.5, H * 0.6, { steps: 14 });   // off the memory card
});

await card(evalCard);
await scene("s8_eval", async () => {});
await card(closeCard);
await scene("s9_close", async () => {});
await pause(1000);

const video = page.video();
await ctx.close();
await browser.close();
const src = await video.path();
fs.renameSync(src, path.join(outDir, "video.webm"));
fs.writeFileSync(path.join(outDir, "timeline.json"), JSON.stringify(timeline, null, 2));
console.log("recorded", ((Date.now() - t0) / 1000).toFixed(1), "s;", timeline.map((t) => `${t.id}@${t.start.toFixed(1)}`).join(" "));
