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

// ---- where it sits: inputs on raised tiles, the graph on a plate, the agents above it, proof going out ------------
{
  const IN = [
    ["Alerts", "Alertmanager webhook", "alert", "Alertmanager posts firing alerts to /alerts/alertmanager; each becomes an Alert node FIRED_ON its service."],
    ["Deploys", "CI webhook · commits", "branch", "CI posts deploys, config changes and flag flips to /changes, with the commits each one ships."],
    ["Kubernetes", "manifests", "pods", "Real manifests give deployments, pods, nodes, images and the config keys each one reads."],
    ["Owners", "CODEOWNERS · history", "person", "Who owns each service and who actually changes it, from the projects' own repositories."],
    ["On-call", "weekly rota", "clock", "Shifts in the graph answer who was on call when it started, not only who is on call now."],
    ["Telemetry", "metrics · logs · traces", "bars", "Real metrics, logs and traces from 360 public RCAEval failures, turned into alerts, operations and log patterns."],
  ];
  const OUT = [
    ["One approved fix", "with its proof", "check", "A single action, tried on a GRAPH.COPY first. A person approves it, then the alerts decide whether it worked."],
    ["Who to page", "owner · expert · on call", "bell", "The team that owns what broke (not what noticed), its on-call person, and who knows it best."],
    ["Post-mortem", "markdown", "doc", "The case file as a post-mortem: timeline, cause, evidence, the fix and what it predicted."],
    ["Any agent", "MCP tools", "plug", "The same tools over MCP. Every answer comes with the Cypher it ran."],
  ];
  // icons in a 40×40 square centred on 0, drawn onto the tile's top face
  const ICON = {
    alert: '<path d="M-14 4 L-6 4 L-2 -8 L3 10 L7 0 L13 0"/><circle cx="13" cy="-8" r="2.5" fill="#ff6b4a" stroke="none"/>',
    branch: '<circle cx="-10" cy="-10" r="3"/><circle cx="-10" cy="10" r="3"/><circle cx="10" cy="-2" r="3"/><path d="M-10 -7 L-10 7 M-10 -2 C -10 -2, 0 -2, 7 -2"/>',
    pods: '<rect x="-13" y="-13" width="11" height="11"/><rect x="2" y="-13" width="11" height="11"/><rect x="-13" y="2" width="11" height="11"/><rect x="2" y="2" width="11" height="11"/>',
    person: '<circle cx="0" cy="-6" r="5"/><path d="M-11 12 C -11 2, 11 2, 11 12"/>',
    clock: '<circle cx="0" cy="0" r="12"/><path d="M0 -7 L0 0 L6 4"/>',
    bars: '<path d="M-12 12 L-12 2 M-4 12 L-4 -6 M4 12 L4 -1 M12 12 L12 -11"/>',
    check: '<rect x="-14" y="-7" width="28" height="14" rx="7"/><circle cx="7" cy="0" r="4" fill="#6ee7b7" stroke="none"/>',
    bell: '<path d="M-9 6 L-9 -2 C -9 -12, 9 -12, 9 -2 L9 6 L12 9 L-12 9 Z"/><path d="M-3 12 L3 12"/>',
    doc: '<path d="M-10 -13 L6 -13 L11 -8 L11 13 L-10 13 Z"/><path d="M-5 -4 L6 -4 M-5 2 L6 2 M-5 8 L2 8"/>',
    plug: '<path d="M-12 -6 L-4 -6 L-4 6 L-12 6 Z M-4 0 L4 0 M4 -9 L12 -9 L12 9 L4 9 Z"/>',
  };
  const K = 1.45, F = (x, y) => `matrix(${0.866 * K},${0.5 * K},${-0.866 * K},${0.5 * K},${x},${y})`;
  const W = 34.6 * K, Hh = 20 * K;                         // the top face's half width and half height
  const slab = (x, y, t, top, side, stroke, w = W, hh = Hh) => `
    <path d="M${x - w} ${y} L${x} ${y + hh} L${x} ${y + hh + t} L${x - w} ${y + t} Z" fill="${side}" stroke="${stroke}" stroke-width=".8"/>
    <path d="M${x + w} ${y} L${x} ${y + hh} L${x} ${y + hh + t} L${x + w} ${y + t} Z" fill="${side}" stroke="${stroke}" stroke-width=".8" opacity=".85"/>
    <path class="face" d="M${x} ${y - hh} L${x + w} ${y} L${x} ${y + hh} L${x - w} ${y} Z" fill="${top}" stroke="${stroke}" stroke-width="1"/>`;
  const tile = (x, y, icon) => `${slab(x, y, 10, "#141418", "#0c0c0f", "#34343d")}
    <path d="M${x} ${y - Hh + 6} L${x + W - 10} ${y} L${x} ${y + Hh - 6} L${x - W + 10} ${y} Z" fill="none" stroke="#24242b"/>
    <g transform="${F(x, y)}" fill="none" stroke="#d4d4d8" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"><g transform="scale(.55)">${ICON[icon]}</g></g>`;
  const label = (x, y, t, sub, end) => `
    <text x="${x}" y="${y - 3}" text-anchor="${end ? "end" : "start"}" fill="#f4f4f5" font-size="17" font-weight="500" font-family="Inter">${esc(t)}</text>
    <text x="${x}" y="${y + 18}" text-anchor="${end ? "end" : "start"}" fill="#8b8b94" font-size="13" font-family="Inter">${esc(sub)}</text>`;
  const caps = (x, y, t, anchor = "start", fill = "#8b8b94", size = 12) => `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${fill}" font-size="${size}" letter-spacing="2.4" font-family="IBM Plex Mono">${t}</text>`;
  const diamond = (path, color, dur, begin = 0, rev = false) => `<path d="M0 -4 L6 0 L0 4 L-6 0 Z" fill="${color}"><animateMotion dur="${dur}s" begin="${begin}s" repeatCount="indefinite" rotate="0"${rev ? ' keyPoints="1;0" keyTimes="0;1" calcMode="linear"' : ""}><mpath href="#${path}"/></animateMotion></path>`;

  const svg = $("#sits");
  let h = `<defs><pattern id="isogrid" width="56" height="32.3" patternUnits="userSpaceOnUse"><path d="M0 0 L56 32.3 M56 0 L0 32.3" stroke="#18181d" stroke-width="1"/></pattern>
    <radialGradient id="plateglow"><stop offset="0" stop-color="#7c6cf6" stop-opacity=".22"/><stop offset="1" stop-color="#7c6cf6" stop-opacity="0"/></radialGradient></defs>
    <rect width="1000" height="620" fill="url(#isogrid)"/>`;
  // the graph plate
  const PX = 500, PY = 400, PW = 160, PH = 92;
  const plate = (y, top, stroke, t) => `
    <path d="M${PX - PW} ${y} L${PX} ${y + PH} L${PX} ${y + PH + t} L${PX - PW} ${y + t} Z" fill="#0b0b0e" stroke="${stroke}" stroke-width="1"/>
    <path d="M${PX + PW} ${y} L${PX} ${y + PH} L${PX} ${y + PH + t} L${PX + PW} ${y + t} Z" fill="#0b0b0e" stroke="${stroke}" stroke-width="1"/>
    <path d="M${PX} ${y - PH} L${PX + PW} ${y} L${PX} ${y + PH} L${PX - PW} ${y} Z" fill="${top}" stroke="${stroke}" stroke-width="1.4"/>`;
  h += `<ellipse cx="${PX}" cy="${PY + 10}" rx="240" ry="130" fill="url(#plateglow)"/>`;
  // flows in: from each input tile to a point along the plate's upper-left edge
  const INX = 250, INY = (i) => 112 + i * 78, OUTX = 760, OUTY = (i) => 150 + i * 98;
  IN.forEach((_, i) => {
    const t = (i + 0.5) / IN.length, tx = PX - PW + PW * t, ty = PY - PH * t;
    h += `<path id="fin${i}" class="wire" data-for="in${i}" d="M${INX + W} ${INY(i)} C ${INX + W + 90} ${INY(i)}, ${tx - 90} ${ty + 30}, ${tx} ${ty}" fill="none" stroke="#5b5b66" stroke-dasharray="3 5"/>`;
    h += diamond(`fin${i}`, i % 3 === 0 ? "#ff6b4a" : "#e4e4e7", 4 + i * 0.4, -i * 0.7);
  });
  h += plate(PY + 20, "#0e0e12", "#2c2c34", 12) + plate(PY, "#121218", "#7c6cf6", 14);
  // a small graph on the plate, drawn on its face
  const nodes = [[-48, -10], [-14, -34], [22, -40], [-4, 2], [36, -2], [-30, 30], [18, 34]];
  const edges = [[0, 1], [1, 2], [1, 3], [3, 4], [2, 4], [3, 5], [3, 6], [4, 6]];
  const hot = new Set(["1-3", "3-6"]);
  h += `<g transform="matrix(${0.866 * 1.55},${0.5 * 1.55},${-0.866 * 1.55},${0.5 * 1.55},${PX},${PY})">
    ${edges.map(([a, b]) => `<line x1="${nodes[a][0]}" y1="${nodes[a][1]}" x2="${nodes[b][0]}" y2="${nodes[b][1]}" stroke="${hot.has(`${a}-${b}`) ? "#6ee7b7" : "#6b6b74"}" stroke-width="1.6" vector-effect="non-scaling-stroke"/>`).join("")}
    ${nodes.map(([x, y], i) => `<circle cx="${x}" cy="${y}" r="${i === 1 ? 5 : 3.6}" fill="${i === 1 ? "#ff6b4a" : "#0b0b0e"}" stroke="${i === 1 ? "#ff6b4a" : "#d4d4d8"}" stroke-width="1.2" vector-effect="non-scaling-stroke"/>`).join("")}</g>`;
  h += `<g class="part" data-id="core" tabindex="0" role="button" aria-label="FalkorDB graph"><path d="M${PX} ${PY - PH} L${PX + PW} ${PY} L${PX} ${PY + PH} L${PX - PW} ${PY} Z" fill="transparent"/></g>`;
  // the agents plate and its two links to the graph
  const AX = 500, AY = 168;
  h += `<path id="fhyp" d="M${AX - 22} ${AY + 40} L${AX - 22} ${PY - PH + 14}" stroke="#5b5b66" stroke-dasharray="3 5" fill="none"/>
    <path id="fver" d="M${AX + 22} ${PY - PH + 14} L${AX + 22} ${AY + 40}" stroke="#5b5b66" stroke-dasharray="3 5" fill="none"/>
    ${diamond("fhyp", "#e4e4e7", 2.6)}${diamond("fver", "#8b7cf6", 2.6, -1.3)}
    ${caps(AX - 34, 268, "HYPOTHESES", "end")}${caps(AX + 34, 268, "VERDICTS")}`;
  h += `<g class="part" data-id="agents" tabindex="0" role="button" aria-label="Hypocenter agents">
    ${slab(AX, AY, 12, "#121216", "#0b0b0e", "#3a3a44", W * 1.9, Hh * 1.9)}
    ${[[-34, 0, "#a1a1aa"], [0, -14, "#a1a1aa"], [32, 2, "#6ee7b7"]].map(([dx, dy, c]) => {
      const x = AX + dx, y = AY + dy - 8;
      return `<path d="M${x} ${y - 9} L${x + 11} ${y - 3} L${x} ${y + 3} L${x - 11} ${y - 3} Z M${x - 11} ${y - 3} L${x - 11} ${y + 9} L${x} ${y + 15} L${x + 11} ${y + 9} L${x + 11} ${y - 3} M${x} ${y + 3} L${x} ${y + 15}" fill="#0e0e12" stroke="${c}" stroke-width="1.3" stroke-linejoin="round"/>`;
    }).join("")}
    <text x="${AX}" y="${AY - 104}" text-anchor="middle" fill="#f4f4f5" font-size="19" font-weight="500" font-family="Inter">Hypocenter agents</text>
    ${caps(AX, AY - 80, "TRIAGE · INVESTIGATE · SKEPTIC · RESPOND", "middle")}</g>`;
  // flows out: from the agents plate to each output tile
  OUT.forEach((_, i) => {
    h += `<path id="fout${i}" class="wire" data-for="out${i}" d="M${AX + W * 1.9} ${AY} C ${AX + W * 1.9 + 110} ${AY}, ${OUTX - W - 120} ${OUTY(i)}, ${OUTX - W} ${OUTY(i)}" fill="none" stroke="#5b5b66" stroke-dasharray="3 5"/>`;
    h += diamond(`fout${i}`, "#6ee7b7", 3.4 + i * 0.4, -i * 0.8);
  });
  IN.forEach(([t, sub, icon], i) => {
    h += `<g class="part" data-id="in${i}" tabindex="0" role="button" aria-label="${esc(t)}">${tile(INX, INY(i), icon)}${label(INX - W - 22, INY(i), t, sub, true)}</g>`;
  });
  OUT.forEach(([t, sub, icon], i) => {
    h += `<g class="part" data-id="out${i}" tabindex="0" role="button" aria-label="${esc(t)}">${tile(OUTX, OUTY(i), icon)}${label(OUTX + W + 22, OUTY(i), t, sub)}</g>`;
  });
  h += caps(30, 36, "EVIDENCE IN", "start", "#6b6b74", 11) + caps(970, 36, "PROOF OUT", "end", "#6b6b74", 11);
  h += `<circle cx="${PX + 190}" cy="${PY + 98}" r="5" fill="#8b7cf6"/>
    <text x="${PX + 204}" y="${PY + 104}" fill="#f4f4f5" font-size="20" font-weight="500" font-family="Inter">FalkorDB graph</text>
    ${caps(PX + 204, PY + 132, "ONE GRAPH PER TENANT")}${caps(PX + 204, PY + 154, "PATHS · COPIES · CAUSED_BY")}${caps(PX + 204, PY + 176, "MEMORY ON THE NODES")}`;
  svg.innerHTML = h;

  const TEXT = {
    core: ["FalkorDB graph", "Every company is its own graph. The investigation walks CALLS paths, copies the graph to prove a fix, and keeps what happened as memory on the nodes."],
    agents: ["Hypocenter agents", "Triage, investigator, skeptic and responder hand off through the graph. They propose; the graph checks every claim before it is shown."],
  };
  IN.forEach(([t, , , d], i) => { TEXT[`in${i}`] = [t, d]; });
  OUT.forEach(([t, , , d], i) => { TEXT[`out${i}`] = [t, d]; });
  const info = $("#sits-info");
  const idle = info.innerHTML;
  const sel = (id) => {
    $$(".part", svg).forEach((p) => p.classList.toggle("sel", p.dataset.id === id));
    $$(".wire", svg).forEach((w) => w.classList.toggle("on", w.dataset.for === id));
    info.innerHTML = id ? `<b>${esc(TEXT[id][0])}</b><p>${esc(TEXT[id][1])}</p>` : idle;
  };
  $$(".part", svg).forEach((p) => {
    p.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") sel(p.dataset.id); });
    p.addEventListener("click", () => sel(p.dataset.id));
    p.addEventListener("focus", () => sel(p.dataset.id));
    p.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sel(p.dataset.id); } });
  });
  svg.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") sel(null); });
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
    q: `GRAPH.COPY hypocenter_kestrel proof_7c1e
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
  $$("#prog button").forEach((b) => b.onclick = () => {
    const el = $(`.step[data-i="${b.dataset.i}"]`);
    if (window.__lenis) window.__lenis.scrollTo(el, { offset: -innerHeight / 2 + el.offsetHeight / 2 });
    else el.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  });
  $$(".step .copy").forEach((b) => b.onclick = () => copy(STEPS[b.dataset.q].q, b));

  // Layout: three columns, every label placed where no edge or node runs through it; arrows show direction.
  const N = {
    act: [86, 64, "rollback auth", "ACTION"], inc: [508, 64, "INC-302", "INCIDENT"],
    d: [86, 158, "D-5201", "DEPLOY"], f: [86, 288, "F-5202", "FLAG"], c: [86, 410, "C-5101", "CONFIG"],
    auth: [300, 158, "auth", ""], users: [300, 288, "users", ""], pdb: [300, 410, "payments-db", ""],
    sym: [508, 208, "mobile-bff", ""], les: [508, 410, "Lesson", "LESSON"],
  };
  const svg = $("#story");
  const node = (k) => { const [x, y, t, kind] = N[k]; return `
    <g class="n" id="n-${k}"><rect x="${x - 60}" y="${y - 18}" width="120" height="36" rx="10" fill="#111115" stroke="#2c2c34"/>
    <text x="${x}" y="${y + 5}" text-anchor="middle" fill="#a1a1aa" font-size="13.5" font-weight="500" font-family="IBM Plex Mono">${t}</text>
    ${kind ? mono(x, y - 25, kind, "#5b5b66", 9.5, "middle") : ""}</g>`; };
  const edge = (id, d, label, lx, ly, anchor = "middle") => `<path class="e" id="e-${id}" d="${d}" fill="none" stroke="#2c2c34" stroke-width="1.4" marker-end="url(#arr)"/>${label ? `<text class="x" id="t-${id}" x="${lx}" y="${ly}" text-anchor="${anchor}" fill="#4b4b55" font-size="10" font-family="IBM Plex Mono">${label}</text>` : ""}`;
  const badge = (id, x, y, text) => `<g class="x" id="b-${id}"><rect x="${x}" y="${y}" width="${text.length * 6.6 + 16}" height="20" rx="10" fill="#24110b" stroke="#ff6b4a" stroke-opacity=".55"/>${mono(x + 8, y + 14, text, "#ff9a80", 10.5)}</g>`;
  const note = (id, x, y, text, color = "#6b6b74", anchor = "middle") => `<text class="x" id="${id}" x="${x}" y="${y}" text-anchor="${anchor}" fill="${color}" font-size="11" font-family="IBM Plex Mono">${text}</text>`;
  svg.setAttribute("viewBox", "0 0 600 470");
  svg.innerHTML = `
    <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="context-stroke"/></marker></defs>
    ${mono(86, 18, "CHANGES", "#6b6b74", 10.5, "middle")}${mono(300, 18, "SERVICES", "#6b6b74", 10.5, "middle")}${mono(508, 18, "SYMPTOM", "#6b6b74", 10.5, "middle")}
    <rect class="x" id="copy" x="12" y="120" width="576" height="338" rx="16" fill="rgba(255,107,74,.035)" stroke="#ff6b4a" stroke-dasharray="5 5"/>
    ${note("copy-t", 578, 140, "GRAPH.COPY · proof_7c1e", "#ff6b4a", "end")}
    ${edge("calls1", "M448 208 C 405 208, 405 158, 362 158", "CALLS", 405, 170)}
    ${edge("calls2", "M332 176 L332 268", "CALLS", 340, 226, "start")}
    ${edge("calls3", "M448 220 C 410 270, 420 410, 362 410", "via 4 hops", 424, 336, "start")}
    ${edge("ch1", "M146 158 L238 158", "CHANGED", 192, 150)}
    ${edge("ch2", "M146 288 L238 288", "CHANGED", 192, 280)}
    ${edge("ch3", "M146 410 L238 410", "CHANGED", 192, 402)}
    ${edge("act", "M42 82 L42 138", "REVERTS", 50, 114, "start")}
    ${edge("root", "M448 70 L148 146", "ROOT_CAUSE", 300, 100)}
    ${edge("les", "M568 74 C 598 160, 598 320, 568 392", "TAUGHT", 584, 300, "end")}
    ${Object.keys(N).map(node).join("")}
    ${badge("sym", 462, 232, "A-9102 · 13:40")}
    ${badge("auth", 202, 182, "A-9101 · 13:38")}
    ${note("pdb-a", 300, 446, "alerts belong to INC-301")}
    ${note("users-a", 270, 320, "no alert")}
    ${note("s-d", 86, 192, "0.96 · leads", "#ff6b4a")}
    ${note("s-f", 86, 322, "0.53 · users quiet")}
    ${note("s-c", 86, 444, "0.63 · INC-301's")}
    ${note("pred", 508, 282, "predicted: clears 2 of 2", "#6ee7b7")}`;

  const paint = (k, mode) => {
    const g = svg.querySelector(`#n-${k}`), r = g.querySelector("rect"), t = g.querySelector("text");
    const C = { off: ["#111115", "#26262c", "#4b4b55", 0.45], idle: ["#111115", "#2c2c34", "#a1a1aa", 1], on: ["#16161b", "#5b5b64", "#f4f4f5", 1],
      hot: ["#24110b", "#ff6b4a", "#f4f4f5", 1], ok: ["#0e231c", "#6ee7b7", "#6ee7b7", 1], hidden: ["#111115", "#2c2c34", "#a1a1aa", 0] }[mode];
    r.setAttribute("fill", C[0]); r.setAttribute("stroke", C[1]); t.setAttribute("fill", C[2]); g.style.opacity = C[3];
  };
  // an edge's mode; on the Prove step, the two edges the CAUSED_BY chain runs along are relabelled
  const line = (id, mode, label) => {
    const e = svg.querySelector(`#e-${id}`), t = svg.querySelector(`#t-${id}`);
    const C = { hidden: ["#2c2c34", 0, 1.4], idle: ["#2c2c34", 1, 1.4], on: ["#5b5b64", 1, 1.6], hot: ["#ff6b4a", 1, 2.4], ok: ["#6ee7b7", 1, 1.8] }[mode];
    e.setAttribute("stroke", C[0]); e.style.opacity = C[1]; e.setAttribute("stroke-width", C[2]);
    if (t) {
      t.style.opacity = C[1];
      t.setAttribute("fill", mode === "hot" ? "#ff6b4a" : mode === "ok" ? "#6ee7b7" : "#5b5b66");
      t.textContent = label || t.dataset.base || (t.dataset.base = t.textContent);
    }
  };
  const H = "hidden";
  const STATE = [
    { n: { inc: "on", sym: "hot", auth: "hot", users: "idle", pdb: "idle", d: H, f: H, c: H, act: H, les: H },
      e: { calls1: "idle", calls2: "idle", calls3: "idle", ch1: H, ch2: H, ch3: H, act: H, root: H, les: H }, x: ["b-sym", "b-auth"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "on", pdb: "on", d: "on", f: "on", c: "on", act: H, les: H },
      e: { calls1: "on", calls2: "on", calls3: "on", ch1: "on", ch2: "on", ch3: "on", act: H, root: H, les: H }, x: ["b-sym", "b-auth", "pdb-a", "users-a"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "on", pdb: "on", d: "hot", f: "off", c: "off", act: H, les: H },
      e: { calls1: "on", calls2: "idle", calls3: "idle", ch1: "hot", ch2: "idle", ch3: "idle", act: H, root: H, les: H },
      x: ["b-sym", "b-auth", "pdb-a", "users-a", "s-d", "s-f", "s-c"] },
    { n: { inc: "on", sym: "hot", auth: "hot", users: "off", pdb: "off", d: "hot", f: "off", c: "off", act: H, les: H },
      e: { calls1: ["hot", "CAUSED_BY"], calls2: "idle", calls3: "idle", ch1: ["hot", "CAUSED_BY"], ch2: "idle", ch3: "idle", act: H, root: H, les: H },
      x: ["b-sym", "b-auth", "copy", "copy-t", "pred"] },
    { n: { inc: "on", sym: "ok", auth: "ok", users: "off", pdb: "off", d: "on", f: "off", c: "off", act: "hot", les: H },
      e: { calls1: "ok", calls2: "idle", calls3: "idle", ch1: "on", ch2: "idle", ch3: "idle", act: "hot", root: H, les: H }, x: [] },
    { n: { inc: "hot", sym: "ok", auth: "ok", users: "idle", pdb: "idle", d: "hot", f: "idle", c: "idle", act: "ok", les: "on" },
      e: { calls1: "idle", calls2: "idle", calls3: "idle", ch1: "on", ch2: "idle", ch3: "idle", act: "ok", root: "hot", les: "on" }, x: [] },
  ];
  const ALL_X = ["b-sym", "b-auth", "pdb-a", "users-a", "s-d", "s-f", "s-c", "copy", "copy-t", "pred"];
  let cur = -1;
  const go = (i) => {
    if (i === cur) return;
    cur = i;
    const s = STATE[i];
    Object.entries(s.n).forEach(([k, m]) => paint(k, m));
    Object.entries(s.e).forEach(([k, m]) => (Array.isArray(m) ? line(k, ...m) : line(k, m)));
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
