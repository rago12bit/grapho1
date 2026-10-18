// The demo film: animated cards and the live app, scene by scene, each held for its voice clip.
//   node demo/film.mjs <audio-dir> <out-dir>      (app on :8090, playwright-core)
// Writes <out-dir>/video.webm and <out-dir>/timeline.json (scene starts and sound cues for demo/mix2.py).
import { chromium } from "playwright-core";
import fs from "node:fs";
import path from "node:path";

const [audioDir, outDir] = process.argv.slice(2);
const APP = process.env.APP || "http://localhost:8090";
const W = 1440, H = 810;
const dur = Object.fromEntries(fs.readFileSync(path.join(audioDir, "durations.txt"), "utf8").trim().split("\n")
  .map((l) => l.split(" ")).map(([k, v]) => [k, +v]));
const narration = JSON.parse(fs.readFileSync(new URL("./narration.json", import.meta.url), "utf8"));
const BREATH = 350;

await fetch(`${APP}/api/reset`, { method: "POST" });
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: W, height: H }, colorScheme: "dark",
  recordVideo: { dir: outDir, size: { width: W, height: H } } });
await ctx.addInitScript(() => { try { localStorage.setItem("faultline.tenant", "kestrel"); } catch {} });
const page = await ctx.newPage();
const t0 = Date.now();
const now = () => (Date.now() - t0) / 1000;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const timeline = [], cues = [];
const cue = (name, at = now()) => cues.push({ name, at });

// a visible cursor (headless video has none)
await page.addInitScript(() => {
  window.addEventListener("DOMContentLoaded", () => {
    const c = document.createElement("div");
    c.id = "demo-cursor";
    c.style.cssText = "position:fixed;z-index:2147483647;width:20px;height:20px;border-radius:50%;border:2px solid #fff;" +
      "background:rgba(255,107,74,.55);box-shadow:0 0 18px rgba(255,107,74,.7);pointer-events:none;transform:translate(-50%,-50%);transition:transform .12s;left:-60px;top:-60px";
    document.body.appendChild(c);
    window.addEventListener("mousemove", (e) => { c.style.left = e.clientX + "px"; c.style.top = e.clientY + "px"; });
    window.addEventListener("mousedown", () => { c.style.transform = "translate(-50%,-50%) scale(.65)"; });
    window.addEventListener("mouseup", () => { c.style.transform = "translate(-50%,-50%)"; });
  });
});

const CSS = `
#film{position:fixed;inset:0;z-index:2147483600;background:radial-gradient(1200px 700px at 50% 120%,#2a0e06 0%,#0a0a0d 55%,#060608 100%);
  color:#f5f5f7;font-family:'Inter Tight',Inter,-apple-system,'Helvetica Neue',sans-serif;overflow:hidden;opacity:0;transition:opacity .45s}
#film.on{opacity:1}
#film .grain{position:absolute;inset:0;background-image:radial-gradient(rgba(255,255,255,.035) 1px,transparent 1px);background-size:3px 3px;opacity:.6}
#film .center{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center}
#film .grad{background:linear-gradient(92deg,#ff5a36,#ffb347 60%,#ffd28a);-webkit-background-clip:text;background-clip:text;color:transparent}
#film .in{opacity:0;transform:translateY(26px);filter:blur(8px);animation:fin .7s cubic-bezier(.2,.8,.2,1) forwards}
#film .pop{opacity:0;transform:scale(.86);animation:fpop .6s cubic-bezier(.2,1.4,.3,1) forwards}
@keyframes fin{to{opacity:1;transform:none;filter:none}}
@keyframes fpop{to{opacity:1;transform:none}}
@keyframes ring{0%{transform:translate(-50%,-50%) scale(.2);opacity:.9}100%{transform:translate(-50%,-50%) scale(3.2);opacity:0}}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.25}}
@keyframes sweep{from{background-position:-200% 0}to{background-position:200% 0}}
@keyframes draw{to{stroke-dashoffset:0}}
@keyframes lit{to{fill:#ff6b4a;stroke:#ffb347}}
@keyframes grow{from{width:0}}
@keyframes shake{0%,100%{transform:translate(0,0)}20%{transform:translate(-6px,3px)}40%{transform:translate(5px,-4px)}60%{transform:translate(-4px,-2px)}80%{transform:translate(3px,4px)}}
#film .mono{font-family:'IBM Plex Mono',ui-monospace,Menlo,monospace}
#film .kick{font:600 15px 'IBM Plex Mono',ui-monospace,monospace;letter-spacing:.28em;text-transform:uppercase;color:#ff8a5c}
#chap{position:fixed;left:26px;bottom:150px;z-index:2147483500;display:flex;align-items:center;gap:12px;padding:10px 18px 10px 12px;border-radius:999px;
  background:rgba(12,12,16,.82);backdrop-filter:blur(10px);border:1px solid rgba(255,107,74,.45);box-shadow:0 10px 40px rgba(0,0,0,.5);
  font:600 15px 'Inter Tight',Inter,sans-serif;color:#fff;opacity:0;transform:translateX(-30px);transition:all .5s cubic-bezier(.2,.8,.2,1)}
#chap.on{opacity:1;transform:none}
#chap b{display:grid;place-items:center;width:30px;height:30px;border-radius:50%;background:linear-gradient(135deg,#ff5a36,#ffb347);color:#1a0a05;font:800 13px 'IBM Plex Mono',monospace}
#chap span{color:#ffb38a;font:500 12px 'IBM Plex Mono',monospace;letter-spacing:.12em;text-transform:uppercase;margin-left:4px}`;

async function card(html, d) {
  await page.evaluate(([h, css, d]) => {
    document.getElementById("chap")?.remove();
    if (!document.getElementById("film-css")) { const s = document.createElement("style"); s.id = "film-css"; s.textContent = css; document.head.appendChild(s); }
    let o = document.getElementById("film");
    if (!o) { o = document.createElement("div"); o.id = "film"; document.body.appendChild(o); }
    o.style.setProperty("--d", d + "s");
    o.innerHTML = `<div class="grain"></div>${h}`;
    requestAnimationFrame(() => o.classList.add("on"));
    const c = document.getElementById("demo-cursor"); if (c) c.style.visibility = "hidden";
  }, [html, CSS, d]);
}
async function uncard() {
  await page.evaluate(() => { const o = document.getElementById("film"); if (o) o.classList.remove("on"); });
  await sleep(450);
  await page.evaluate(() => { document.getElementById("film")?.remove(); const c = document.getElementById("demo-cursor"); if (c) c.style.visibility = "visible"; });
}
async function chapter(n, title, sub) {
  await page.evaluate(([n, title, sub, css]) => {
    if (!document.getElementById("film-css")) { const s = document.createElement("style"); s.id = "film-css"; s.textContent = css; document.head.appendChild(s); }
    document.getElementById("chap")?.remove();
    const c = document.createElement("div"); c.id = "chap";
    c.innerHTML = `<b>${n}</b>${title}<span>${sub}</span>`; document.body.appendChild(c);
    setTimeout(() => c.classList.add("on"), 60);
  }, [n, title, sub, CSS]);
  cue("whoosh");
}
async function go(url) {
  await page.goto(`${APP}${url}`, { waitUntil: "domcontentloaded" });
}
async function move(x, y, steps = 18) { await page.mouse.move(x, y, { steps }); }
async function click(locator) {
  const el = typeof locator === "string" ? page.locator(locator).first() : locator;
  await el.scrollIntoViewIfNeeded().catch(() => {});
  const b = await el.boundingBox();
  if (b) { await move(b.x + Math.min(b.width / 2, 140), b.y + b.height / 2, 16); await sleep(120); await page.mouse.down(); await sleep(80); await page.mouse.up(); }
  await el.click({ force: true }).catch(() => {});
}
async function caseTo(text, smooth = true) {
  await page.evaluate(([t, smooth]) => {
    const pane = document.querySelector("#case"); if (!pane) return;
    const sec = [...pane.querySelectorAll("section, .sec")].find((e) => e.textContent.trim().toUpperCase().startsWith(t.toUpperCase()));
    if (sec) pane.scrollTo({ top: sec.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 10, behavior: smooth ? "smooth" : "auto" });
  }, [text, smooth]);
}
async function scene(id, body) {
  const start = now();
  timeline.push({ id, start, text: narration.find((n) => n.id === id).text, duration: dur[id] });
  const until = Date.now() + dur[id] * 1000 + BREATH;
  await body(dur[id]);
  const left = until - Date.now();
  if (left > 0) await sleep(left);
}
const at = (frac, d) => sleep(Math.max(0, frac * d * 1000));

// ---------------------------------------------------------------- cards
const hook = (d) => `<div class="center" style="gap:16px;animation:shake .5s ${d * .74}s 2">
  <div class="kick in" style="animation-delay:.1s">INCIDENT · LIVE · <span style="animation:blink 1s infinite">●</span> PAGING</div>
  <div class="in mono" style="animation-delay:.25s;font-size:150px;font-weight:600;letter-spacing:-.02em;line-height:1;text-shadow:0 0 60px rgba(255,90,54,.55)">13:40</div>
  <div class="in" style="animation-delay:${d * .14}s;font-size:46px;font-weight:700;letter-spacing:-.02em"><span class="grad">30%</span> of mobile logins are failing</div>
  <div class="in" style="animation-delay:${d * .32}s;display:flex;gap:12px;margin-top:6px">${Array.from({ length: 7 }, (_, i) =>
    `<span style="width:20px;height:20px;border-radius:50%;background:#ff3b2f;box-shadow:0 0 22px #ff3b2f;animation:blink ${0.6 + i * .09}s infinite"></span>`).join("")}</div>
  <div class="in mono" style="animation-delay:${d * .44}s;font-size:22px;color:#c9c9d1">3 deploys · 1 config change · 1 feature flag · in the last hour</div>
  <div class="in" style="animation-delay:${d * .7}s;font-size:72px;font-weight:800;letter-spacing:-.04em;margin-top:22px">Which one broke <span class="grad">production?</span></div></div>`;

const title = (d) => `<div class="center">
  ${[0, .35, .7, 1.05].map((dl) => `<div style="position:absolute;left:50%;top:44%;width:340px;height:340px;border-radius:50%;border:2px solid rgba(255,107,74,.75);animation:ring 2.6s ${dl}s infinite ease-out"></div>`).join("")}
  <div style="position:absolute;left:50%;top:44%;width:18px;height:18px;margin:-9px 0 0 -9px;border-radius:50%;background:#ffb347;box-shadow:0 0 40px #ff6b4a,0 0 80px #ff6b4a"></div>
  <div class="pop" style="animation-delay:.15s;font-size:150px;font-weight:800;letter-spacing:-.06em;line-height:1;margin-top:-30px;
    background:linear-gradient(92deg,#fff 20%,#ffb347 45%,#ff5a36 55%,#fff 80%);background-size:200% 100%;-webkit-background-clip:text;background-clip:text;color:transparent;animation:fpop .6s .15s cubic-bezier(.2,1.4,.3,1) forwards,sweep 3.5s 1s linear infinite">Hypocenter</div>
  <div class="in" style="animation-delay:${d * .2}s;margin-top:26px;font-size:30px;font-weight:600">built by <span class="grad" style="font-weight:800">Raghav Gupta</span> · a student</div>
  <div class="in mono" style="animation-delay:${d * .32}s;margin-top:12px;font-size:18px;color:#a1a1aa;letter-spacing:.08em">GRAPH HACKS 2026 · WEMAKEDEVS × FALKORDB</div>
  <div class="in" style="animation-delay:${d * .55}s;margin-top:34px;font-size:34px;font-weight:700;letter-spacing:-.02em">Follow the alerts down. <span class="grad">Prove the fix first.</span></div></div>`;

const rule = (d) => {
  const nodes = [["symptom", 330, 300, 0], ["api", 560, 230, 1], ["auth", 800, 300, 2], ["db", 1040, 230, 3], ["deploy", 1180, 360, 4]];
  const order = { 4: .18, 3: .26, 2: .34, 1: .42, 0: .5 };
  return `<div class="center" style="justify-content:flex-start;padding-top:70px">
  <div class="in" style="animation-delay:.1s;font-size:96px;font-weight:800;letter-spacing:-.05em">No path, <span class="grad">no blame.</span></div>
  <svg width="1440" height="440" viewBox="0 0 1440 440" style="position:absolute;top:250px;left:0">
    ${nodes.slice(0, -1).map(([, x, y], i) => { const [, x2, y2] = nodes[i + 1]; return `<line x1="${x}" y1="${y}" x2="${x2}" y2="${y2}" stroke="#ff6b4a" stroke-width="4" stroke-dasharray="400" stroke-dashoffset="400" style="animation:draw .7s ${d * (.14 + i * .05)}s forwards"/>`; }).join("")}
    ${nodes.map(([n, x, y, i]) => `<g><circle cx="${x}" cy="${y}" r="34" fill="#16161b" stroke="#3a3a44" stroke-width="3" style="animation:lit .35s ${d * order[i]}s forwards"/>
      <text x="${x}" y="${y + 66}" text-anchor="middle" fill="#d4d4d8" font-size="20" font-family="IBM Plex Mono,monospace">${n}</text></g>`).join("")}
    <text x="760" y="140" text-anchor="middle" fill="#ffb38a" font-size="22" font-family="IBM Plex Mono,monospace" opacity="0" style="animation:fin .6s ${d * .5}s forwards">alerts climb the path, in order ↑</text>
  </svg>
  <div class="in" style="animation-delay:${d * .66}s;position:absolute;bottom:120px;display:flex;gap:70px;font-size:28px;font-weight:600">
    <span><span style="color:#a1a1aa">epicenter:</span> where you feel it</span><span><span class="grad">hypocenter:</span> where it started</span></div></div>`;
};

const results = (d) => {
  const bar = (label, v, hi, dl, note = "") => `<div style="display:grid;grid-template-columns:330px 1fr 120px;gap:18px;align-items:center;margin:12px 0">
    <span style="font-size:24px;font-weight:${hi ? 800 : 500};color:${hi ? "#fff" : "#c9c9d1"}">${label}</span>
    <div style="height:30px;border-radius:8px;background:#17171d;overflow:hidden"><div style="height:100%;width:${v}%;border-radius:8px;background:${hi ? "linear-gradient(90deg,#ff5a36,#ffb347)" : "#4b4b57"};animation:grow 1.1s ${dl}s cubic-bezier(.2,.8,.2,1) both"></div></div>
    <span class="mono" style="font-size:24px;font-weight:600">${note || (v / 100).toFixed(2)}</span></div>`;
  return `<div class="center" style="padding:0 160px;align-items:stretch;text-align:left">
    <div class="kick in" style="animation-delay:.05s">360 REAL FAILURES · RCAEVAL · THE BENCHMARK'S OWN BASELINES</div>
    <div class="in" style="animation-delay:.2s;font-size:62px;font-weight:800;letter-spacing:-.04em;margin:10px 0 26px">Right service first: <span class="grad">81%</span></div>
    ${bar("Hypocenter", 81, true, d * .18)}${bar("NSigma", 78, false, d * .22)}${bar("CIRCA", 71, false, d * .26)}${bar("BARO", 28, false, d * .3)}
    <div class="in" style="animation-delay:${d * .45}s;margin-top:28px;display:grid;grid-template-columns:1fr 1fr;gap:26px">
      <div style="padding:22px 26px;border-radius:16px;border:1px solid #2c2c35;background:#101014"><div class="mono" style="color:#ffb38a;font-size:15px;letter-spacing:.14em">HELD OUT · 90 FAILURES</div>
        <div style="font-size:34px;font-weight:800;margin-top:8px">NSigma <span style="color:#fff">0.76</span> · Hypocenter <span class="grad">0.71</span></div><div style="color:#a1a1aa;font-size:20px;margin-top:6px">It loses here, and the README says so.</div></div>
      <div style="padding:22px 26px;border-radius:16px;border:1px solid rgba(255,107,74,.5);background:#160d0a"><div class="mono" style="color:#ffb38a;font-size:15px;letter-spacing:.14em">WHEN IT'S SURE</div>
        <div style="font-size:52px;font-weight:800;margin-top:4px" class="grad">0 wrong</div><div style="color:#a1a1aa;font-size:20px">high-confidence calls on all 360</div></div></div></div>`;
};

const load = (d) => {
  const rows = [["algo.BFS", "nothing calls auth", "verdict D-5201 → C-5101"], ["algo.SSpaths", "cut checkout → payments", "blast radius 5 → 1"],
    ["algo.WCC", "join two alert groups", "3 problems → 2"], ["algo.MSF", "one closer call", "spread tree rewires"], ["algo.pageRank", "everything calls sendgrid", "most critical flips"]];
  return `<div class="center" style="padding:0 170px;align-items:stretch;text-align:left">
    <div class="kick in">THE GRAPH DOES THE WORK</div>
    <div class="in" style="animation-delay:.15s;font-size:56px;font-weight:800;letter-spacing:-.04em;margin:10px 0 24px">Change what it reads. <span class="grad">Watch the decision flip.</span></div>
    ${rows.map(([a, c, f], i) => `<div class="in" style="animation-delay:${d * (.12 + i * .08)}s;display:grid;grid-template-columns:240px 1fr 420px;gap:18px;align-items:center;padding:14px 20px;margin:6px 0;border-radius:12px;background:#111116;border:1px solid #26262e">
      <span class="mono" style="color:#ffb38a;font-size:22px;font-weight:600">${a}</span><span style="font-size:22px;color:#d4d4d8">${c}</span><span style="font-size:22px;font-weight:700">${f}</span></div>`).join("")}
    <div class="in" style="animation-delay:${d * .62}s;margin-top:20px;font-size:32px;font-weight:700">Pull FalkorDB out → <span class="grad">no answer at all.</span></div></div>`;
};

const close = (d) => `<div class="center">
  ${[0, .6, 1.2].map((dl) => `<div style="position:absolute;left:50%;top:30%;width:260px;height:260px;border-radius:50%;border:2px solid rgba(255,107,74,.6);animation:ring 3s ${dl}s infinite ease-out"></div>`).join("")}
  <div class="in" style="animation-delay:.1s;display:flex;gap:18px;margin-bottom:34px">
    ${[["242", "tests"], ["28/28", "breakages caught"], ["8/8", "algorithms at work"], ["360", "real failures"]].map(([n, l], i) =>
      `<div class="pop" style="animation-delay:${.15 + i * .25}s;padding:18px 26px;border-radius:16px;background:#111116;border:1px solid #2a2a33;min-width:190px">
        <div class="grad" style="font-size:46px;font-weight:800">${n}</div><div style="color:#a1a1aa;font-size:17px">${l}</div></div>`).join("")}</div>
  <div class="pop" style="animation-delay:${d * .42}s;font-size:120px;font-weight:800;letter-spacing:-.06em;line-height:1">Hypocenter</div>
  <div class="in" style="animation-delay:${d * .52}s;margin-top:18px;font-size:30px;font-weight:600">built by <span class="grad" style="font-weight:800">Raghav Gupta</span></div>
  <div class="in mono" style="animation-delay:${d * .6}s;margin-top:26px;font-size:21px;color:#e4e4e7">16-4-94-72.sslip.io · github.com/rago12bit/grapho1 · make judge-check</div>
  <div class="in" style="animation-delay:${d * .74}s;margin-top:30px;font-size:44px;font-weight:800;letter-spacing:-.03em">No path, <span class="grad">no blame.</span></div></div>`;

// ---------------------------------------------------------------- the film
await go("/");
await card(`<div class="center"></div>`, 1);
await sleep(900);

await scene("v01_hook", async (d) => { await card(hook(d), d); cue("riser", now() + d - 2.6); });
cue("impact");
await scene("v02_intro", async (d) => { await card(title(d), d); });
cue("whoosh");
await scene("v03_rule", async (d) => { await card(rule(d), d); });

await scene("v04_graph", async (d) => {
  await uncard();
  await chapter("01", "One graph per company", "FalkorDB");
  await move(W * 0.3, H * 0.5, 20); await at(.18, d);
  await move(1010, 470, 24); await at(.2, d);
  await page.mouse.down(); await sleep(90); await page.mouse.up();
  await page.mouse.click(1010, 470).catch(() => {});
  await at(.12, d); await move(1100, 380, 26); await at(.12, d); await move(930, 560, 26);
});

await scene("v05_real", async (d) => {
  await go("/app?tenant=boutique&incident=INC-404");
  await page.waitForSelector("#case .sec", { timeout: 30000 });
  await chapter("02", "A real failure", "RCAEval · Online Boutique");
  await move(820, 460, 20); await at(.3, d);
  await move(1200, 300, 20); await at(.15, d);
  await caseTo("Timeline"); await at(.2, d); await move(1300, 360, 14);
});

await scene("v06_agents", async (d) => {
  await go("/app?tenant=kestrel&incident=INC-302");
  await page.waitForSelector("#case .sec", { timeout: 30000 });
  await chapter("03", "Four agents, one graph", "triage · investigator · skeptic · responder");
  await at(.12, d);
  await click("#go");
  await move(700, 640, 20);
});

await scene("v07_verdict", async (d) => {
  await page.waitForSelector("#handoffs", { timeout: 60000 }).catch(() => {});
  await caseTo("Evidence gate", false); await page.evaluate(() => document.querySelector("#case")?.scrollTo({ top: 0, behavior: "smooth" }));
  await chapter("04", "The verdict", "the alerts climbed the path");
  await move(1200, 220, 20); await at(.22, d);
  await move(860, 420, 22); await at(.25, d);
  await caseTo("Alert groups"); await move(1180, 420, 18);
});

await scene("v08_gate", async (d) => {
  await caseTo("Evidence gate");
  await chapter("05", "The evidence gate", "8 checks · each with its Cypher");
  await move(1150, 260, 18); await at(.35, d);
  await page.evaluate(() => document.querySelector("#case")?.scrollBy({ top: 330, behavior: "smooth" }));
  await move(1150, 520, 18);
});

await scene("v09_proof", async (d) => {
  await caseTo("Proposed fixes");
  await chapter("06", "Proved before approval", "GRAPH.COPY");
  await move(1180, 330, 18); await at(.38, d);
  const ok = page.locator("button[data-a][data-ok='1']").first();
  if (await ok.count()) await click(ok);
  await at(.2, d); await page.waitForSelector(".verify", { timeout: 8000 }).catch(() => {});
  await caseTo("Proposed fixes");
});

await scene("v10_replay", async (d) => {
  await go("/replay?tenant=kestrel&incident=INC-301");
  await chapter("07", "Replay", "watch the run");
  await sleep(500); await click("#play"); await move(760, 500, 20);
});

await scene("v11_break", async (d) => {
  await go("/break");
  await chapter("08", "Break it", "one change on a copy");
  await sleep(600);
  await click('button.exp[data-id="cut-path"]'); await at(.15, d);
  await click("#go"); await move(1000, 640, 22);
});

await scene("v12_depth", async (d) => {
  await go("/results");
  await page.waitForTimeout(500);
  await page.evaluate(() => { const el = document.querySelector("#depth"); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + scrollY - 60 }); });
  await chapter("09", "Depth 1 vs depth 4", "and a name the graph doesn't have");
  await move(420, 420, 20); await at(.45, d);
  await page.evaluate(() => { const el = document.querySelector("#unanswer"); if (el) window.scrollTo({ top: el.getBoundingClientRect().top + scrollY - 260, behavior: "smooth" }); });
  await move(700, 520, 20);
});

await scene("v13_brain", async (d) => {
  await go("/app?tenant=kestrel");
  await page.waitForSelector("#q");
  await chapter("10", "The company brain", "decisions from the team's chat");
  await click("#q"); await page.keyboard.type("why was C-5101 made?", { delay: 22 }); await page.keyboard.press("Enter");
  await at(.5, d);
  await click("#q"); await page.keyboard.type("who owns it?", { delay: 22 }); await page.keyboard.press("Enter");
  await move(820, 300, 16);
});

await scene("v14_ship", async (d) => {
  await go("/preflight");
  await chapter("11", "Ship it?", "before the next deploy");
  await sleep(500);
  await click(page.locator("main button", { hasText: "deploy on auth" }));
  await at(.12, d); await click("#go"); await move(900, 560, 18);
});

await scene("v15_results", async (d) => { await card(results(d), d); cue("whoosh"); });
await scene("v16_load", async (d) => { await card(load(d), d); cue("whoosh"); });
cue("impact");
await scene("v17_close", async (d) => { await card(close(d), d); });
await sleep(2200);

const video = page.video();
await ctx.close(); await browser.close();
fs.renameSync(await video.path(), path.join(outDir, "video.webm"));
fs.writeFileSync(path.join(outDir, "timeline.json"), JSON.stringify({ scenes: timeline, cues, end: now() }, null, 2));
console.log("recorded", now().toFixed(1), "s;", timeline.map((t) => `${t.id}@${t.start.toFixed(1)}`).join(" "));
