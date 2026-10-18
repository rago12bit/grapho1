// The landing page. Everything it shows about INC-302 comes from Kestrel's seed data; the counts on the cube and
// the incident lists are read live from the API.
import { $, $$, api, ago, clock, cypher, esc, mountShell } from "./shell.js";
import { mountContours } from "./contours.js";

mountShell({ tenants: false });
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---- hero ---------------------------------------------------------------------------------------------------
mountContours($("#contours"));

const LABELS = [
  ["Service", "A running service. CALLS edges between services are the paths a failure travels."],
  ["Change", "A deploy, config change or flag flip: CHANGED onto a service, MADE by a person, at a time."],
  ["Alert", "What monitoring fired, FIRED_ON a service. CAUSED_BY links say which alert explains which."],
  ["Incident", "What people are paged about: a symptom, a start, and once solved a ROOT_CAUSE."],
  ["Hypothesis", "One suspect for one incident, with its score and the evidence for and against it."],
  ["Evidence", "One reason for or against a hypothesis. It CITES the alert, change or incident it rests on."],
  ["Action", "A proposed fix with its proof from a graph copy attached, waiting for a person to approve it."],
  ["Person", "Who makes changes, approves fixes and goes on call. Experts are ranked from the graph."],
  ["Team", "Owns services, and is covered by an on-call rota."],
  ["OnCallShift", "A week of one person on call for one team: who was on call when it started, not just now."],
  ["Commit", "What a deploy SHIPS: the commits that touched the service, so you know which to read first."],
  ["Deployment", "A Kubernetes workload from real manifests: replicas, limits and the image it runs."],
  ["Pod", "Where a deployment runs, ON_NODE. Two failing services on one node point at the node."],
  ["FalkorDB", "One graph per tenant. Every verdict on this page is a query on it.", true],
  ["Node", "A machine. When what runs on it alerts and nothing changed, the host itself is the suspect."],
  ["Image", "The container image a deployment RUNS_IMAGE."],
  ["ConfigMap", "Configuration a deployment reads. A config change SETS one of its keys."],
  ["ConfigKey", "One key, linking a config change to every deployment exposed to it."],
  ["Journey", "A customer journey (checkout, login) that USES services, with an SLO and an error budget."],
  ["Lesson", "What an incident TAUGHT, written when it is closed and found again next time."],
  ["Runbook", "What to do for a service, linked so the investigator can cite it."],
  ["Episode", "One agent's handoff, written to the graph so the next agent reads it from there."],
  ["Investigation", "One run of the agents on an incident: its steps, its queries, its verdict."],
  ["Operation", "An endpoint inside a service, from real traces, with its own time and its callees'."],
  ["LogPattern", "A templated error log line, counted per service, from real logs."],
  ["Zone", "An availability zone a machine sits IN_ZONE."],
  ["Org", "The tenant itself. Every company is its own graph."],
];
(async () => {
  const counts = {};
  try {
    for (const t of await api("/api/tenants")) for (const [l, n] of Object.entries(t.nodes || {})) counts[l] = (counts[l] || 0) + n;
  } catch {}
  const pieces = LABELS.map(([label, text, core]) => ({ label, text, core: !!core, count: counts[label] }));
  const tip = $("#tip"), hint = $("#hint"), calls = $("#callouts"), box = $(".hero-cube");
  try {
    const { mountCube } = await import("./cube.js");
    mountCube($("#cube"), pieces, {
      onOpen: (open) => {
        calls.classList.toggle("off", open);
        hint.innerHTML = open ? "<i></i> Hover or tap a piece · tap outside to close" : "<i></i> Click the cube";
      },
      onPick: (p, x, y) => {
        if (!p) { tip.hidden = true; return; }
        const n = p.core ? "the engine" : p.count != null ? `${p.count.toLocaleString()} now` : "";
        tip.innerHTML = `<b>${esc(p.core ? "FalkorDB" : `:${p.label}`)}<span>${n}</span></b><p>${esc(p.text)}</p>`;
        tip.hidden = false;
        tip.style.left = `${Math.max(8, Math.min(box.clientWidth - 258, x + 16))}px`;
        tip.style.top = `${Math.max(8, Math.min(box.clientHeight - 120, y - 20))}px`;
      },
    });
  } catch {
    // no WebGL, or three.js couldn't load: the page works without the cube
    $("#cube").style.visibility = "hidden";
    hint.hidden = true;
  }
})();

// ---- the live incident room, tilted until you scroll to it -------------------------------------------------
{
  const win = $("#window"), frame = $("#win-frame"), cover = $("#win-cover");
  const load = () => { if (!frame.src) frame.src = frame.dataset.src; };
  new IntersectionObserver(([x]) => { if (x.isIntersecting) load(); }, { rootMargin: "400px" }).observe(win);
  cover.onclick = () => { load(); cover.remove(); frame.tabIndex = 0; frame.focus(); };
  if (!reduced) {
    const tilt = () => {
      const r = win.getBoundingClientRect();
      const p = Math.max(0, Math.min(1, 1 - (r.top - innerHeight * 0.12) / (innerHeight * 0.75)));
      win.style.setProperty("--tilt", `${(1 - p) * 18}deg`);
      win.style.setProperty("--sc", String(0.92 + 0.08 * p));
    };
    addEventListener("scroll", tilt, { passive: true });
    tilt();
  }
}

// ---- numbers count up once seen --------------------------------------------------------------------------------
{
  const io = new IntersectionObserver((xs) => xs.forEach((x) => {
    if (!x.isIntersecting) return;
    io.unobserve(x.target);
    const el = x.target, to = +el.dataset.count, dec = +el.dataset.dec;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / 1100), e = 1 - Math.pow(1 - p, 3);
      el.textContent = (to * e).toFixed(dec);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }), { threshold: 0.6 });
  if (!reduced) $$("[data-count]").forEach((el) => io.observe(el));
}

// ---- FalkorDB features strip ---------------------------------------------------------------------------------
{
  const ALGOS = ["algo.WCC", "algo.SPpaths", "algo.betweenness", "algo.labelPropagation", "algo.pageRank", "GRAPH.COPY",
    "GRAPH.RO_QUERY", "vecf32 vector index", "full-text index", "CALLS*1..5", "CAUSED_BY*1..12", "UNWIND … MERGE"];
  $("#track").innerHTML = [...ALGOS, ...ALGOS].map((a) => `<span>${esc(a)}</span>`).join("");
}

// ---- the statement fills in word by word as it scrolls up -------------------------------------------------------
{
  const p = $("#statement");
  p.innerHTML = p.textContent.split(" ").map((w) => `<span class="w">${esc(w)}</span>`).join(" ");
  const words = $$(".w", p);
  const fill = () => {
    const r = p.getBoundingClientRect();
    const k = Math.max(0, Math.min(1, (innerHeight * 0.85 - r.top) / (r.height + innerHeight * 0.35)));
    const n = Math.round(k * words.length);
    words.forEach((w, i) => w.classList.toggle("on", i < n));
  };
  if (reduced) words.forEach((w) => w.classList.add("on"));
  else { addEventListener("scroll", fill, { passive: true }); fill(); }
}

// ---- Fig 0.1 / 0.2 / 0.3: small drawings of the three ideas ---------------------------------------------------
const iso = (x, y, z, ox, oy) => [ox + (x - y) * 0.866, oy + (x + y) * 0.5 - z];
const plane = (z, ox, oy, s = 46) => `M${[[-s, -s], [s, -s], [s, s], [-s, s]].map(([x, y]) => iso(x, y, z, ox, oy).join(",")).join("L")}Z`;
const mono = (x, y, t, fill = "#6b6b74", size = 8, anchor = "start") => `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${fill}" font-size="${size}" font-family="IBM Plex Mono">${t}</text>`;
{
  // 0.1: three layers (symptom, services, changes); one path drops through all of them, one stops short
  const L = [36, 0, -36], O = [120, 72];
  const a = iso(-14, -10, L[0], ...O), b = iso(6, -4, L[1], ...O), c = iso(18, 14, L[2], ...O);
  const x1 = iso(-30, 20, L[1], ...O), x2 = iso(-34, 30, L[2], ...O);
  $("#fig1").innerHTML = `
    ${L.map((z, i) => `<path d="${plane(z, ...O)}" fill="${i === 0 ? "rgba(255,107,74,.05)" : "rgba(255,255,255,.015)"}" stroke="#2c2c34"/>`).join("")}
    ${mono(206, iso(46, 46, L[0], ...O)[1] - 26, "symptom")}${mono(206, iso(46, 46, L[1], ...O)[1] - 26, "services")}${mono(206, iso(46, 46, L[2], ...O)[1] - 26, "changes")}
    <path d="M${x1}L${x2}" stroke="#4a4a52" stroke-dasharray="3 4"/>
    ${mono(x2[0] - 40, x2[1] + 14, "× off path", "#6b6b74", 9)}
    <path d="M${a}L${b}L${c}" stroke="#ff6b4a" stroke-width="2" fill="none" class="flow"/>
    ${[a, b, c].map((q, i) => `<circle cx="${q[0]}" cy="${q[1]}" r="${i === 2 ? 5 : 4}" fill="${i === 2 ? "#ff6b4a" : "#f4f4f5"}"/>`).join("")}`;
  // 0.2: hypotheses weighed: evidence for grows left, against grows right
  const bars = (y, label, pro, con, hot) => `
    ${mono(20, y - 6, label, hot ? "#f4f4f5" : "#a1a1aa", 10)}
    <rect x="20" y="${y}" width="200" height="10" rx="3" fill="#18181d"/>
    <rect x="${120 - pro}" y="${y}" width="${pro}" height="10" rx="3" fill="${hot ? "#ff6b4a" : "#4b4b55"}"><animate attributeName="width" values="0;${pro};${pro}" keyTimes="0;.4;1" dur="5s" repeatCount="indefinite"/><animate attributeName="x" values="120;${120 - pro};${120 - pro}" keyTimes="0;.4;1" dur="5s" repeatCount="indefinite"/></rect>
    <rect x="120" y="${y}" width="${con}" height="10" rx="3" fill="#33333b"><animate attributeName="width" values="0;${con};${con}" keyTimes="0;.4;1" dur="5s" repeatCount="indefinite"/></rect>`;
  $("#fig2").innerHTML = `
    <line x1="120" y1="12" x2="120" y2="140" stroke="#2c2c34" stroke-dasharray="2 3"/>
    ${mono(114, 10, "for", "#6ee7b7", 8, "end")}${mono(126, 10, "against")}
    ${bars(36, "D-5201 · 0.96 · leads", 92, 8, true)}${bars(80, "C-5101 · 0.63", 50, 44)}${bars(124, "F-5202 · 0.53", 34, 60)}`;
  // 0.3: the graph and its copy; on the copy the fix clears the two alerts, again and again
  const dots = [[-24, -18], [10, -26], [22, 8], [-8, 16], [-30, 24]], O3 = [120, 80];
  const g = (z, copy) => dots.map(([x, y], i) => {
    const [cx, cy] = iso(x, y, z, ...O3), hot = i < 2;
    return `<circle cx="${cx}" cy="${cy}" r="4" fill="${hot ? "#ff6b4a" : "#a1a1aa"}">${copy && hot ? '<animate attributeName="fill" values="#ff6b4a;#ff6b4a;#6ee7b7;#6ee7b7;#ff6b4a" keyTimes="0;.35;.5;.9;1" dur="4s" repeatCount="indefinite"/>' : ""}</circle>`;
  }).join("");
  $("#fig3").innerHTML = `
    <path d="${plane(-14, ...O3)}" fill="rgba(255,255,255,.015)" stroke="#2c2c34"/>
    <path d="${plane(34, ...O3)}" fill="rgba(255,107,74,.04)" stroke="#ff6b4a" stroke-dasharray="4 4"/>
    <path d="M${iso(-24, -18, -14, ...O3)}L${iso(-24, -18, 34, ...O3)}" stroke="#2c2c34" stroke-dasharray="2 3"/>
    ${g(-14, false)}${g(34, true)}
    ${mono(196, iso(46, 46, 34, ...O3)[1] - 26, "GRAPH.COPY", "#ff6b4a")}${mono(196, iso(46, 46, -14, ...O3)[1] - 26, "real graph")}`;
}

// ---- where it sits -------------------------------------------------------------------------------------------
{
  const IN = [
    ["Alerts", "Alertmanager webhook", "Alertmanager posts firing alerts to /alerts/alertmanager; each becomes an Alert node FIRED_ON its service."],
    ["Deploys", "CI webhook", "CI posts deploys, config changes and flag flips to /changes, with the commits each one ships."],
    ["Kubernetes", "manifests", "Real manifests give deployments, pods, nodes, images and the config keys each one reads."],
    ["Owners", "CODEOWNERS · commits", "Who owns each service and who actually changes it, from the projects' own repositories."],
    ["On-call", "weekly rota", "Shifts in the graph answer who was on call when it started, not only who is on call now."],
    ["Telemetry", "metrics · logs · traces", "Real metrics, logs and traces from 360 public RCAEval failures, turned into alerts, operations and log patterns."],
  ];
  const OUT = [
    ["One approved fix", "with its proof", "A single action, tried on a GRAPH.COPY first. A person approves it, then the alerts decide whether it worked."],
    ["Who to page", "owner · expert · on call", "The team that owns what broke (not what noticed), its on-call person, and who knows it best."],
    ["Post-mortem", "markdown", "The case file as a post-mortem: timeline, cause, evidence, the fix and what it predicted."],
    ["Any agent", "MCP tools", "The same tools over MCP. Every answer comes with the Cypher it ran."],
  ];
  const svg = $("#sits");
  const box = (x, y, w, h, title, sub, id, end) => `
    <g class="part" data-id="${id}" tabindex="0" role="button" aria-label="${esc(title)}">
      <rect class="box" x="${x}" y="${y}" width="${w}" height="${h}" rx="10" fill="#111115" stroke="#2c2c34"/>
      <text x="${end ? x + w - 14 : x + 14}" y="${y + 22}" text-anchor="${end ? "end" : "start"}" fill="#f4f4f5" font-size="14" font-weight="600" font-family="Inter">${esc(title)}</text>
      ${mono(end ? x + w - 14 : x + 14, y + 40, esc(sub), "#6b6b74", 11, end ? "end" : "start")}
    </g>`;
  const cx = 500, cy = 330;
  const plate = (z, f, s) => `<path d="M${[[-150, 0], [0, -86], [150, 0], [0, 86]].map(([x, y]) => [cx + x, cy + y - z]).join("L")}Z" fill="${f}" stroke="${s}"/>`;
  let h = `${mono(40, 40, "EVIDENCE IN", "#6b6b74", 11)}${mono(960, 40, "PROOF OUT", "#6b6b74", 11, "end")}
    ${plate(-18, "#0b0b0e", "#1f1f25")}${plate(0, "#121216", "#3a3a44")}`;
  const pts = [[-60, -10], [-10, -40], [40, -12], [0, 20], [70, 18], [-50, 30]].map(([x, y]) => [cx + x, cy + y - 6]);
  [[0, 1], [1, 2], [2, 4], [1, 3], [3, 5], [3, 4]].forEach(([a, b]) => { h += `<line x1="${pts[a][0]}" y1="${pts[a][1]}" x2="${pts[b][0]}" y2="${pts[b][1]}" stroke="${a === 1 && b === 3 ? "#ff6b4a" : "#3a3a44"}" stroke-width="1.5"/>`; });
  pts.forEach((p, i) => { h += `<circle cx="${p[0]}" cy="${p[1]}" r="${i === 3 ? 6 : 4.5}" fill="${i === 3 ? "#ff6b4a" : "#a1a1aa"}"/>`; });
  h += `<g class="part" data-id="core" tabindex="0" role="button" aria-label="FalkorDB graph">${plate(0, "transparent", "transparent")}
      <text x="${cx}" y="${cy + 124}" text-anchor="middle" fill="#f4f4f5" font-size="15" font-weight="600" font-family="Inter">FalkorDB graph</text>
      ${mono(cx, cy + 142, "one per tenant · paths · copies · memory", "#6b6b74", 11, "middle")}</g>`;
  h += box(cx - 130, 66, 260, 54, "Faultline agents", "triage · investigate · skeptic · respond", "agents");
  h += `<path d="M${cx - 40} 120 L${cx - 40} ${cy - 92}" stroke="#ff6b4a" class="flow" fill="none"/><path d="M${cx + 40} ${cy - 92} L${cx + 40} 120" stroke="#6ee7b7" class="flow" fill="none"/>
    ${mono(cx - 50, 184, "HYPOTHESES", "#6b6b74", 10, "end")}${mono(cx + 50, 184, "VERDICTS", "#6b6b74", 10)}`;
  IN.forEach(([t, s], i) => {
    const y = 60 + i * 70, d = `M262 ${y + 27} C 330 ${y + 27}, 330 ${cy}, ${cx - 150} ${cy}`;
    h += `<path d="${d}" stroke="#26262c" fill="none"/><path d="${d}" stroke="#ff6b4a" stroke-opacity=".5" class="flow" fill="none"/>` + box(40, y, 222, 54, t, s, `in${i}`);
  });
  OUT.forEach(([t, s], i) => {
    const y = 120 + i * 84, d = `M${cx + 150} ${cy} C 680 ${cy}, 680 ${y + 27}, 738 ${y + 27}`;
    h += `<path d="${d}" stroke="#26262c" fill="none"/><path d="${d}" stroke="#6ee7b7" stroke-opacity=".45" class="flow" fill="none"/>` + box(738, y, 222, 54, t, s, `out${i}`, true);
  });
  svg.innerHTML = h;
  const TEXT = {
    core: ["FalkorDB graph", "Every company is its own graph. The investigation walks CALLS paths, copies the graph to prove a fix, and keeps what happened as memory on the nodes."],
    agents: ["Faultline agents", "Triage, investigator, skeptic and responder hand off through the graph. They propose; the graph checks every claim before it is shown."],
  };
  IN.forEach(([t, , d], i) => { TEXT[`in${i}`] = [t, d]; });
  OUT.forEach(([t, , d], i) => { TEXT[`out${i}`] = [t, d]; });
  const info = $("#sits-info");
  const sel = (id) => {
    $$(".part", svg).forEach((p) => p.classList.toggle("sel", p.dataset.id === id));
    info.innerHTML = `<b>${esc(TEXT[id][0])}</b><p>${esc(TEXT[id][1])}</p>`;
  };
  $$(".part", svg).forEach((p) => {
    p.addEventListener("click", () => sel(p.dataset.id));
    p.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sel(p.dataset.id); } });
  });
}

// ---- how it decides: the steps scroll, the figure stays and changes ----------------------------------------------
const STEPS = [
  { name: "Alert", algo: "FIRED_ON · alerts_near", file: "tools.py · alerts_near",
    text: "At <b>13:40</b> mobile-bff pages: 30% of logins failing. Two minutes earlier auth started answering 401s. The investigator reads what is alerting near the symptom, in time order.",
    cap: "mobile-bff pages at 13:40; auth has been failing since 13:38.",
    q: `MATCH (a:Alert)-[:FIRED_ON]->(s:Service)
WHERE s.name IN $svcs AND a.at >= $from AND a.at <= $to
RETURN a.id AS id, s.name AS service, a.at AS at, a.signal AS signal
ORDER BY a.at` },
  { name: "Path", algo: "CALLS*1..5 · changes_near", file: "tools.py · dependencies, changes_near",
    text: "Everything mobile-bff depends on, up to five hops: <b>22 services</b>. Six changes landed on that path in the window. A change anywhere else is out before anything is scored.",
    cap: "Walk the call graph down from the symptom; only changes on that path are suspects.",
    q: `MATCH p = (s:Service {name: $svc})-[:CALLS*1..5]->(d:Service)
WITH d, min(length(p)) AS hops
MATCH (c:Change)-[:CHANGED]->(d)
WHERE c.at >= $from AND c.at <= $to
RETURN c.id, d.name, hops` },
  { name: "Argue", algo: "Hypothesis · Evidence · CITES", file: "reasoning.py · write",
    text: "Each suspect becomes a <b>Hypothesis</b> with evidence for and against. D-5201 leads: auth alerted 240 s after it, mobile-bff 120 s after that. Users never alerted under F-5202, and C-5101's alerts belong to INC-301.",
    cap: "Three hypotheses, each with evidence that cites the node it rests on.",
    q: `UNWIND $hyps AS h
CREATE (i)-[:HAS_HYPOTHESIS]->(x:Hypothesis {id: h.id, score: h.score})
WITH x, h
MATCH (c:Change {id: h.blames_id})
CREATE (x)-[:BLAMES]->(c)` },
  { name: "Prove", algo: "GRAPH.COPY · CAUSED_BY", file: "proof.py · preview_fix",
    text: "Rolling back D-5201 is tried on a <b>GRAPH.COPY</b>: every alert whose CAUSED_BY chain ends at the deploy is cleared there, and the judge is asked again. It clears both of INC-302's alerts and leaves nothing behind.",
    cap: "On the copy: A-9102 → A-9101 → D-5201. The rollback clears 2 of 2.",
    q: `GRAPH.COPY faultline_kestrel proof_7c1e
MATCH p = (a:Alert)-[:CAUSED_BY*1..12]->(root:Change {id: $root})
MATCH (a)-[:FIRED_ON]->(s:Service)
RETURN DISTINCT a.id AS alert, s.name AS service` },
  { name: "Act", algo: "Action · APPROVED", file: "tools.py · decide_action",
    text: "One action goes out: <b>roll back auth 4.2.0 → 4.1.7</b>, with its proof attached. A person approves it; then the alerts decide whether it worked, checked against what the copy predicted.",
    cap: "Approved by a person, verified by monitoring: as predicted on the graph copy.",
    q: `MATCH (a:Action {id: $id})
SET a.status = 'executed', a.decided_by = $who, a.decided_at = $at
WITH a
OPTIONAL MATCH (p:Person {handle: $who})
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:APPROVED]->(a))` },
  { name: "Remember", algo: "ROOT_CAUSE · Lesson", file: "tools.py · resolve_incident",
    text: "The root cause, the lesson and the fix's track record go back into the graph. The next auth incident starts with this one as a <b>precedent</b>, and a person's \"not the cause\" becomes a training label.",
    cap: "Written back: the next incident on auth starts warm.",
    q: `MATCH (i:Incident {id: $inc}), (c:Change {id: $cid})
MERGE (i)-[:ROOT_CAUSE]->(c)
CREATE (i)-[:TAUGHT]->(:Lesson {text: $lesson, at: $at})` },
];
{
  $("#steps").innerHTML = STEPS.map((s, i) => `
    <article class="step" data-i="${i}">
      <div class="step-h"><span class="n">0${i + 1}</span><h3>${s.name}</h3></div>
      <p class="algo">${esc(s.algo)}</p>
      <p>${s.text}</p>
      <div class="qcard"><div class="qcard-h"><b>Cypher</b><span>${esc(s.file)}</span><button class="copy" data-q="${i}">Copy</button></div>
      <pre class="code">${cypher(s.q)}</pre></div>
    </article>`).join("");
  $("#prog").innerHTML = STEPS.map((s, i) => `<button data-i="${i}" aria-label="Step ${i + 1}: ${s.name}">${s.name}</button>`).join("");
  $$("#prog button").forEach((b) => b.onclick = () => $(`.step[data-i="${b.dataset.i}"]`).scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" }));
  $$(".step .copy").forEach((b) => b.onclick = () => copy(STEPS[b.dataset.q].q, b));

  const N = {
    inc: [470, 46, "INC-302", "INCIDENT"], sym: [470, 168, "mobile-bff", ""], auth: [270, 118, "auth", ""], users: [270, 222, "users", ""],
    pdb: [270, 326, "payments-db", ""], d: [74, 118, "D-5201", "DEPLOY"], f: [74, 222, "F-5202", "FLAG"], c: [74, 326, "C-5101", "CONFIG"],
    act: [470, 290, "rollback auth", "ACTION"], les: [470, 378, "Lesson", "LESSON"],
  };
  const svg = $("#story");
  const node = (k) => { const [x, y, t, kind] = N[k]; return `
    <g class="n" id="n-${k}"><rect x="${x - 56}" y="${y - 17}" width="112" height="34" rx="9" fill="#111115" stroke="#2c2c34"/>
    <text x="${x}" y="${y + 4.5}" text-anchor="middle" fill="#a1a1aa" font-size="12.5" font-family="IBM Plex Mono">${t}</text>
    ${kind ? mono(x, y - 22, kind, "#4b4b55", 8.5, "middle") : ""}</g>`; };
  const edge = (id, d, label, lx, ly) => `<path class="e" id="e-${id}" d="${d}" fill="none" stroke="#2c2c34" stroke-width="1.4"/>${label ? `<text class="x" id="t-${id}" x="${lx}" y="${ly}" fill="#4b4b55" font-size="8.5" font-family="IBM Plex Mono">${label}</text>` : ""}`;
  const badge = (id, x, y, text) => `<g class="x" id="b-${id}"><rect x="${x}" y="${y}" width="${text.length * 6.1 + 14}" height="18" rx="9" fill="#24110b" stroke="#ff6b4a" stroke-opacity=".5"/>${mono(x + 7, y + 12.5, text, "#ff8a6e", 9.5)}</g>`;
  const note = (id, x, y, text, color = "#6b6b74") => `<text class="x" id="${id}" x="${x}" y="${y}" fill="${color}" font-size="10" font-family="IBM Plex Mono">${text}</text>`;
  svg.innerHTML = `
    ${mono(74, 22, "CHANGES", "#6b6b74", 9.5, "middle")}${mono(270, 22, "SERVICES", "#6b6b74", 9.5, "middle")}${mono(470, 22, "SYMPTOM", "#6b6b74", 9.5, "middle")}
    <rect class="x" id="copy" x="8" y="66" width="544" height="290" rx="14" fill="rgba(255,107,74,.03)" stroke="#ff6b4a" stroke-dasharray="5 5"/>
    ${note("copy-t", 20, 345, "GRAPH.COPY · proof_7c1e", "#ff6b4a")}
    ${edge("calls1", "M414 168 C 370 168, 360 118, 326 118", "CALLS", 352, 136)}
    ${edge("calls2", "M270 135 L270 205", "CALLS", 276, 174)}
    ${edge("calls3", "M414 176 C 360 190, 380 326, 326 326", "4 hops", 362, 262)}
    ${edge("ch1", "M130 118 L214 118", "CHANGED", 146, 112)}
    ${edge("ch2", "M130 222 L214 222", "CHANGED", 146, 216)}
    ${edge("ch3", "M130 326 L214 326", "CHANGED", 146, 320)}
    <path class="e" id="e-cb" d="M470 151 C 470 92, 300 84, 276 101 M264 101 C 220 80, 110 80, 80 101" fill="none" stroke="transparent" stroke-width="2.6"/>
    ${note("cb-t", 330, 86, "CAUSED_BY", "#ff6b4a")}
    ${edge("act", "M414 300 C 250 400, 110 390, 74 343", "REVERTS", 200, 380)}
    ${edge("root", "M414 42 C 220 34, 90 56, 74 101", "ROOT_CAUSE", 188, 44)}
    ${edge("les", "M526 52 C 556 120, 556 330, 526 372", "TAUGHT", 512, 220)}
    ${Object.keys(N).map(node).join("")}
    ${badge("sym", 422, 190, "A-9102 · 13:40")}
    ${badge("auth", 222, 140, "A-9101 · 13:38")}
    ${note("pdb-a", 226, 356, "alerts: INC-301's")}
    ${note("users-a", 246, 254, "no alert")}
    ${note("s-d", 20, 150, "0.96 · leads", "#ff6b4a")}
    ${note("s-f", 20, 254, "0.53 · users quiet")}
    ${note("s-c", 20, 358, "0.63 · INC-301's")}
    ${note("pred", 352, 238, "predicted: clears 2 of 2", "#6ee7b7")}`;

  const paint = (k, mode) => {
    const g = svg.querySelector(`#n-${k}`), r = g.querySelector("rect"), t = g.querySelector("text");
    const C = { off: ["#111115", "#26262c", "#4b4b55", 0.45], idle: ["#111115", "#2c2c34", "#a1a1aa", 1], on: ["#16161b", "#5b5b64", "#f4f4f5", 1],
      hot: ["#24110b", "#ff6b4a", "#f4f4f5", 1], ok: ["#0e231c", "#6ee7b7", "#6ee7b7", 1], hidden: ["#111115", "#2c2c34", "#a1a1aa", 0] }[mode];
    r.setAttribute("fill", C[0]); r.setAttribute("stroke", C[1]); t.setAttribute("fill", C[2]); g.style.opacity = C[3];
  };
  const line = (id, mode) => {
    const e = svg.querySelector(`#e-${id}`), t = svg.querySelector(`#t-${id}`);
    const C = { hidden: ["#2c2c34", 0, 1.4], idle: ["#2c2c34", 1, 1.4], on: ["#5b5b64", 1, 1.6], hot: ["#ff6b4a", 1, 2.4], ok: ["#6ee7b7", 1, 1.8] }[mode];
    e.setAttribute("stroke", C[0]); e.style.opacity = C[1]; e.setAttribute("stroke-width", C[2]);
    if (t) { t.style.opacity = C[1]; t.setAttribute("fill", mode === "hot" ? "#ff6b4a" : mode === "ok" ? "#6ee7b7" : "#4b4b55"); }
  };
  const H = "hidden";
  const STATE = [
    { n: { inc: "on", sym: "hot", auth: "hot", users: "idle", pdb: "idle", d: H, f: H, c: H, act: H, les: H },
      e: { calls1: "idle", calls2: "idle", calls3: "idle", ch1: H, ch2: H, ch3: H, cb: H, act: H, root: H, les: H }, x: ["b-sym", "b-auth"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "on", pdb: "on", d: "on", f: "on", c: "on", act: H, les: H },
      e: { calls1: "on", calls2: "on", calls3: "on", ch1: "on", ch2: "on", ch3: "on", cb: H, act: H, root: H, les: H }, x: ["b-sym", "b-auth", "pdb-a", "users-a"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "on", pdb: "on", d: "hot", f: "off", c: "off", act: H, les: H },
      e: { calls1: "on", calls2: "idle", calls3: "idle", ch1: "hot", ch2: "idle", ch3: "idle", cb: H, act: H, root: H, les: H },
      x: ["b-sym", "b-auth", "pdb-a", "users-a", "s-d", "s-f", "s-c"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "off", pdb: "off", d: "hot", f: "off", c: "off", act: H, les: H },
      e: { calls1: "idle", calls2: "idle", calls3: "idle", ch1: "idle", ch2: "idle", ch3: "idle", cb: "hot", act: H, root: H, les: H },
      x: ["b-sym", "b-auth", "copy", "copy-t", "cb-t", "pred"] },
    { n: { inc: "on", sym: "ok", auth: "ok", users: "off", pdb: "off", d: "on", f: "off", c: "off", act: "hot", les: H },
      e: { calls1: "ok", calls2: "idle", calls3: "idle", ch1: "on", ch2: "idle", ch3: "idle", cb: H, act: "hot", root: H, les: H }, x: [] },
    { n: { inc: "hot", sym: "ok", auth: "ok", users: "idle", pdb: "idle", d: "hot", f: "idle", c: "idle", act: "ok", les: "on" },
      e: { calls1: "idle", calls2: "idle", calls3: "idle", ch1: "on", ch2: "idle", ch3: "idle", cb: H, act: "ok", root: "hot", les: "on" }, x: [] },
  ];
  const ALL_X = ["b-sym", "b-auth", "pdb-a", "users-a", "s-d", "s-f", "s-c", "copy", "copy-t", "cb-t", "pred"];
  let cur = -1;
  const go = (i) => {
    if (i === cur) return;
    cur = i;
    const s = STATE[i];
    Object.entries(s.n).forEach(([k, m]) => paint(k, m));
    Object.entries(s.e).forEach(([k, m]) => line(k, m));
    ALL_X.forEach((id) => { svg.querySelector(`#${id}`).style.opacity = s.x.includes(id) ? 1 : 0; });
    $$("#prog button").forEach((b, j) => { b.classList.toggle("on", j === i); b.classList.toggle("done", j < i); });
    $$(".step").forEach((el, j) => el.classList.toggle("on", j === i));
    $("#fig-n").textContent = `Fig 2.${i + 1}`;
    $("#fig-algo").textContent = STEPS[i].algo;
    $("#fig-cap").textContent = STEPS[i].cap;
  };
  go(0);
  const io = new IntersectionObserver((xs) => xs.forEach((x) => { if (x.isIntersecting) go(+x.target.dataset.i); }), { rootMargin: "-45% 0px -45% 0px" });
  $$(".step").forEach((el) => io.observe(el));
}

// ---- product tour: the real pages, live -------------------------------------------------------------------------
{
  const TOUR = [
    ["Incident room", "/app?tenant=kestrel&incident=INC-302", "One incident, decided in the open.", "The failure path lit hop by hop, every suspect argued with evidence that cites the graph, and the fix with its proof, waiting for a person."],
    ["On-call", "/oncall?tenant=kestrel", "Page the team that owns what broke.", "Who is on call now and next for every team, the rota behind it, and for each open incident who to page: the owners of the blamed service, not of the symptom."],
    ["Explore", "/explore?tenant=kestrel", "The whole graph, every label.", "Every node and edge of a tenant on one canvas. Filter by label, search, open any node to see its properties, neighbours and the Cypher behind the view."],
    ["Break it", "/break", "Change one thing, watch the verdict move.", "Seven experiments, each on its own GRAPH.COPY: cut a call, move a deploy, lose an alert, say \"not the cause\". The real graph is never touched."],
    ["Replay", "/replay?tenant=kestrel&incident=INC-302", "Watch the run, step by step.", "The investigation streamed as it runs: every tool call, the FalkorDB time it took, and the graph lighting up as the agents reach each service."],
  ];
  const tabs = $("#tour-tabs"), body = $("#tour-body");
  tabs.innerHTML = TOUR.map(([n], i) => `<button role="tab" aria-selected="${i === 0}" data-i="${i}">${esc(n)}</button>`).join("");
  let live = false;
  const show = (i) => {
    const [name, href, h, p] = TOUR[i];
    $$("button", tabs).forEach((b) => b.setAttribute("aria-selected", String(+b.dataset.i === i)));
    body.innerHTML = `
      <div class="tour-text"><span class="n">0${i + 1} · ${esc(name)}</span><h3>${esc(h)}</h3><p>${esc(p)}</p>
        <a class="btn primary" href="${esc(href)}">Open it live <span aria-hidden="true">→</span></a></div>
      <div class="tour-shot"><div class="win-bar"><i></i><i></i><i></i><span class="mono">localhost:8090${esc(href.split("?")[0])}</span></div>
        <iframe title="${esc(name)}, live" ${live ? "src" : "data-src"}="${esc(href)}"></iframe></div>`;
  };
  $$("button", tabs).forEach((b) => b.onclick = () => { live = true; show(+b.dataset.i); });
  show(0);
  new IntersectionObserver(([x], o) => {
    if (!x.isIntersecting) return;
    o.disconnect(); live = true;
    const f = $("iframe", body);
    if (f && f.dataset.src) f.src = f.dataset.src;
  }, { rootMargin: "300px" }).observe(body);
}

// ---- results bars ------------------------------------------------------------------------------------------------
{
  for (const box of $$(".bars")) {
    const rows = JSON.parse(box.dataset.bars);
    const pair = box.classList.contains("pair");
    if (pair) box.insertAdjacentHTML("beforebegin", '<div class="legend"><span><i style="background:#26262d"></i>AC@1</span><span><i style="background:#1b1b21"></i>Avg@5</span></div>');
    box.innerHTML = rows.map(([name, v, me, avg]) => `
      <div class="bar${me ? " me" : ""}"><span class="name">${esc(name)}</span><span class="rail">
        <span class="fill" data-w="${v}">${v.toFixed(2)}</span>${pair ? `<span class="fill alt" data-w="${avg}">${avg.toFixed(2)}</span>` : ""}</span></div>`).join("");
  }
  const grow = (el) => $$(".fill", el).forEach((f) => { f.style.width = `${100 * f.dataset.w}%`; });
  const io = new IntersectionObserver((xs) => xs.forEach((x) => { if (x.isIntersecting) { grow(x.target); io.unobserve(x.target); } }), { threshold: 0.3 });
  $$(".chart").forEach((c) => (reduced ? grow(c) : io.observe(c)));
}

// ---- run: open incidents to watch live, and what memory already holds -------------------------------------------
(async () => {
  try {
    const sets = await Promise.all([["kestrel", "Kestrel Pay"], ["sockshop", "Sock Shop · real"], ["trainticket", "Train Ticket · real"]].map(async ([t, name]) =>
      (await api(`/api/${t}/incidents?status=open`)).slice(0, t === "kestrel" ? 5 : 2).map((i) => ({ ...i, tenant: t, tname: name }))));
    $("#scenarios").innerHTML = sets.flat().map((i) => `
      <div class="sc"><span class="id">${esc(i.id)}</span>
        <div><div class="t">“${esc(i.title)}”</div><div class="m">${esc(i.tname)} · ${esc(i.severity)} · symptom <b>${esc(i.symptom)}</b></div></div>
        <a class="btn sm" href="/replay?tenant=${encodeURIComponent(i.tenant)}&amp;incident=${encodeURIComponent(i.id)}&amp;play=1">Run</a></div>`).join("");
  } catch { $("#scenarios").innerHTML = '<p class="empty">FalkorDB is offline, so there is nothing to run.</p>'; }
  try {
    const mem = await api("/api/kestrel/memory");
    $("#solved-n").textContent = `${mem.length} in Kestrel's memory`;
    $("#solved").innerHTML = mem.slice(0, 8).map((m) => `
      <div class="solved"><div><b class="mono" style="font-weight:500">${esc(m.id)}</b> ${esc(m.title)}<div class="faint" style="font-size:12.5px">${esc(m.cause || "")}</div></div>
        <span class="m">${ago(m.started)}</span></div>`).join("");
  } catch { $("#solved").innerHTML = '<p class="empty">Memory is unavailable while FalkorDB is offline.</p>'; }
})();

// ---- copy buttons ------------------------------------------------------------------------------------------------
function copy(text, btn) {
  const done = () => { const t = btn.textContent; btn.textContent = "Copied"; setTimeout(() => { btn.textContent = t; }, 1200); };
  navigator.clipboard?.writeText(text).then(done, done);
}
$$("[data-copy]").forEach((b) => b.onclick = () => copy($(`#${b.dataset.copy}`).textContent, b));
