// Replay: run the investigation live (server-sent events, one per tool call), keep the steps, then play them back
// at the speed you pick. The graph lights each service as a step reaches it.
import { $, $$, api, esc, mountShell, tenant } from "./shell.js";

mountShell();
const T = tenant();
const q = new URLSearchParams(location.search);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
let cy = null, G = null, services = [], incident = null, steps = [], done = null, i = -1, timer = null, speed = 1;

async function boot() {
  let incs = [];
  try {
    const [open, solved, g] = await Promise.all([api(`/api/${T}/incidents?status=open`), api(`/api/${T}/incidents?status=resolved`), api(`/api/${T}/graph`)]);
    incs = [...open, ...solved];
    G = g;
  } catch (e) { $("#verdict").textContent = `Couldn't read ${T}: ${e.message}`; return; }
  const want = q.get("incident") || incs[0]?.id;
  $("#inc").innerHTML = incs.map((x) => `<option value="${esc(x.id)}"${x.id === want ? " selected" : ""}>${esc(x.id)} · ${esc(x.title.slice(0, 40))}${x.status === "resolved" ? " (solved)" : ""}</option>`).join("");
  $("#inc").onchange = () => { const u = new URL(location.href); u.searchParams.set("incident", $("#inc").value); u.searchParams.delete("play"); history.replaceState(null, "", u); pick(); };
  pick();
  if (q.get("play") === "1") record();
}

function pick() {
  stop();
  incident = $("#inc").value;
  steps = []; done = null; i = -1;
  const opt = $("#inc").selectedOptions[0];
  $("#h-id").textContent = `${T} · ${incident}`;
  $("#h-title").textContent = opt ? opt.textContent.split(" · ").slice(1).join(" · ").replace(" (solved)", "") : "";
  $("#verdict").innerHTML = "Every step is a real tool call against FalkorDB, recorded from a live run when you press play.";
  $("#tl").innerHTML = '<p class="empty">Press <b>Watch the run</b>.</p>';
  $("#src").textContent = "not run yet";
  $("#rc").innerHTML = '<p class="faint">The verdict appears when the run reaches it.</p>';
  $("#rc-chip").textContent = "waiting";
  $("#sus").innerHTML = '<p class="empty">Ranked at the end of the run.</p>';
  bar();
  api(`/api/${T}/incidents/${encodeURIComponent(incident)}`).then((inc) => graph(inc.symptom)).catch(() => graph(null));
}

// The services the incident can reach: its symptom and everything below it on the call graph. That is where the
// agents look, so the picture stays readable even for Train Ticket's 68 services.
// `only`, once a run is recorded and the reachable part is large: just what the run touched, what alerted, and the
// path from the symptom to the cause.
let SYM = null;
function graph(symptom, only = null) {
  SYM = symptom;
  const out = {};
  G.edges.forEach((e) => { (out[e.source] = out[e.source] || []).push(e.target); });
  const keep = new Set(symptom ? [symptom] : G.nodes.map((n) => n.id));
  const depth = { [symptom]: 0 };
  for (let ring = [symptom], d = 1; symptom && ring.length; d++) {
    ring = ring.flatMap((id) => (out[id] || []).filter((m) => !keep.has(m)));
    ring = [...new Set(ring)];
    ring.forEach((m) => { keep.add(m); depth[m] = d; });
  }
  const reachable = keep.size;
  if (only) [...keep].forEach((id) => { if (!only.has(id)) keep.delete(id); });
  services = [...keep];
  const nodes = G.nodes.filter((n) => keep.has(n.id)), edges = G.edges.filter((e) => keep.has(e.source) && keep.has(e.target));
  $("#flow-n").textContent = only ? `${nodes.length} of ${reachable} reachable services · the ones this run touched`
    : `${nodes.length} of ${G.nodes.length} services · reachable from ${symptom || "the entry points"}`;
  if (cy) cy.destroy();
  const many = nodes.length > 12;            // a wide fan-out reads better as rings around the symptom
  cy = cytoscape({
    container: $("#rcy"), minZoom: 0.2, maxZoom: 2.2, wheelSensitivity: 0.25,
    elements: [...nodes.map((n) => ({ data: { id: n.id, label: n.id.replace(/^ts-/, "").replace(/-service$/, "") } })), ...edges.map((e, k) => ({ data: { id: `c${k}`, source: e.source, target: e.target } }))],
    style: [
      { selector: "node", style: { shape: "round-rectangle", width: "label", height: 24, padding: "9px", "background-color": "#111115", "border-width": 1, "border-color": "#2c2c34",
        label: "data(label)", color: "#71717a", "font-family": "IBM Plex Mono", "font-size": 11, "text-valign": "center", "text-halign": "center",
        "underlay-color": "#ffb347", "underlay-opacity": 0, "underlay-padding": 7, "underlay-shape": "round-rectangle",
        "transition-property": "border-color, background-color, color, underlay-opacity", "transition-duration": "0.35s" } },
      { selector: "edge", style: { width: 1.2, "line-color": "#26262c", "target-arrow-color": "#26262c", "target-arrow-shape": "triangle", "arrow-scale": 0.7, "curve-style": "bezier",
        "transition-property": "line-color, target-arrow-color, width", "transition-duration": "0.35s" } },
      { selector: "node.seen", style: { "border-color": "#ffb347", color: "#f4f4f5", "background-color": "#1c140a" } },
      { selector: "edge.seen", style: { "line-color": "#6b5a3a", "target-arrow-color": "#6b5a3a", width: 1.6 } },
      { selector: "node.cur", style: { "border-width": 2, "border-color": "#ffb347", color: "#ffb347", "underlay-opacity": 0.22 } },
      { selector: "node.sym", style: { "border-color": "#ff6b4a", "background-color": "#24110b", color: "#ff9a80", "border-width": 2, "underlay-color": "#ff6b4a", "underlay-opacity": 0.14 } },
      { selector: "node.root", style: { "border-color": "#6ee7b7", "background-color": "#0e231c", color: "#6ee7b7", "border-width": 2.5, "underlay-color": "#6ee7b7", "underlay-opacity": 0.3 } },
      { selector: "edge.lit", style: { "line-color": "#ff6b4a", "target-arrow-color": "#ff6b4a", width: 2.6 } },
    ],
    layout: many
      ? { name: "concentric", concentric: (n) => 100 - (depth[n.id()] ?? 9), levelWidth: () => 1, minNodeSpacing: 10, padding: 30, animate: false }
      : { name: "breadthfirst", directed: true, roots: symptom ? [symptom] : undefined, spacingFactor: 1.15, padding: 30, animate: false },
  });
  if (symptom) cy.getElementById(symptom).addClass("sym");
  cy.on("mouseover", "node", (e) => { $("#rcy").title = e.target.id(); });
  if (steps.length) show(i);
}

function record() {
  stop();
  steps = []; done = null; i = -1;
  $("#play").disabled = true;
  $("#play").textContent = "Running live…";
  $("#src").textContent = "recording a live run";
  $("#tl").innerHTML = "";
  const es = new EventSource(`/api/${T}/incidents/${encodeURIComponent(incident)}/investigate/stream`);
  const t0 = performance.now();
  es.addEventListener("step", (e) => { steps.push(JSON.parse(e.data)); $("#tl-n").textContent = `${steps.length} steps`; });
  es.addEventListener("done", (e) => {
    es.close();
    done = JSON.parse(e.data);
    focus();
    const fdb = steps.reduce((a, s) => a + (s.ms || 0), 0);
    $("#src").textContent = `recorded just now · ${steps.length} steps · ${fdb.toFixed(0)} ms in FalkorDB · ${Math.round(performance.now() - t0)} ms in all`;
    $("#play").disabled = false;
    $("#play").textContent = "▶ Run it again";
    timeline();
    i = -1;
    play();
  });
  es.addEventListener("error", (e) => {
    es.close();
    let msg = "the stream closed";
    try { msg = JSON.parse(e.data).detail; } catch {}
    $("#src").textContent = `failed: ${msg}`;
    $("#play").disabled = false; $("#play").textContent = "▶ Try again";
  });
}

// After a run: if the reachable part is too big to read, keep the services that matter to this run.
function focus() {
  if (!cy || cy.nodes().length <= 22 || !SYM) return;
  const keep = new Set([SYM, ...steps.flatMap(touched), ...(done?.ranking?.alert_cone || [])]);
  const root = done?.findings?.root_cause_service;
  if (root) {
    keep.add(root);
    const path = cy.elements().aStar({ root: cy.getElementById(SYM), goal: cy.getElementById(root), directed: true });
    if (path.found) path.path.nodes().forEach((n) => keep.add(n.id()));
  }
  if (keep.size < cy.nodes().length) graph(SYM, keep);
}

function timeline() {
  $("#tl").innerHTML = steps.map((s, k) => `
    <div class="ev" data-k="${k}"><span class="d"></span><div>
      <div><span class="tool">${esc(s.tool)}</span><span class="role">${esc(s.role || "")}</span></div>
      ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}
      <div class="res">${s.queries} ${s.queries === 1 ? "query" : "queries"} · ${s.ms} ms · ${esc(s.result)}</div></div></div>`).join("")
    + `<div class="ev" data-k="${steps.length}"><span class="d"></span><div><div><span class="tool">verdict</span><span class="role">judge</span></div><div class="note">${esc(done?.findings?.change_id || done?.findings?.root_cause_service || "")} · ${esc(done?.findings?.confidence || "")} confidence</div></div></div>`;
  $$(".ev").forEach((el) => el.onclick = () => { stop(); show(+el.dataset.k); });
  $("#scrub").max = String(steps.length);
  bar();
}

const touched = (s) => {
  const text = Object.values(s.input || {}).join(" ") + " " + (s.result || "");
  return services.filter((name) => new RegExp(`(^|[^\\w-])${name.replace(/[-]/g, "\\-")}([^\\w-]|$)`).test(text));
};

function show(k) {
  i = Math.max(-1, Math.min(k, steps.length));
  $$(".ev").forEach((el) => {
    const n = +el.dataset.k;
    el.classList.toggle("cur", n === i);
    el.classList.toggle("seen", n < i);
  });
  const cur = $(`.ev[data-k="${i}"]`);
  const tl = $("#tl");                                       // follow the current step inside the list, not the page
  if (cur) tl.scrollTo({ top: cur.offsetTop - tl.clientHeight / 2 + cur.clientHeight / 2, behavior: "smooth" });
  if (cy) {
    cy.nodes().removeClass("seen cur root"); cy.edges().removeClass("lit seen");
    const now = i < steps.length && i >= 0 ? touched(steps[i]) : [];
    for (let k2 = 0; k2 <= Math.min(i, steps.length - 1); k2++) touched(steps[k2]).forEach((n) => cy.getElementById(n).addClass("seen"));
    now.forEach((n) => cy.getElementById(n).addClass("cur"));
    cy.edges().filter((e) => e.source().hasClass("seen") && e.target().hasClass("seen")).addClass("seen");
    // a short pulse on what this step just reached
    const cur = cy.nodes(".cur");
    cy.nodes().stop(true).removeStyle("underlay-opacity");     // drop the last pulse so classes decide again
    if (!reduced && cur.length) cur.animate({ style: { "underlay-opacity": 0.45 } }, { duration: 180, complete: () => cur.animate({ style: { "underlay-opacity": 0.22 } }, { duration: 320 }) });
  }
  if (i >= steps.length && done) verdict(); else partial();
  bar();
}

function partial() {
  $("#rc-chip").textContent = i < 0 ? "waiting" : "investigating";
  $("#rc").innerHTML = `<p class="faint">${i < 0 ? "The verdict appears when the run reaches it." : `Step ${i + 1} of ${steps.length}: ${esc(steps[i].tool)}.`}</p>`;
  $("#verdict").innerHTML = i < 0 ? $("#verdict").innerHTML : `<b>${esc(steps[i].role || "")}</b> · ${esc(steps[i].note || steps[i].tool)}`;
}

function verdict() {
  const f = done.findings || {}, r = done.ranking || {};
  const lead = f.change_id || f.root_cause_service || "no cause";
  $("#verdict").innerHTML = `<b>${esc(lead)}</b> ${esc((done.answer || f.summary || "").replace(/^.*?is the most likely cause of [^.]*\.\s*/, ""))}`;
  $("#rc-chip").textContent = `${f.confidence || r.confidence || ""} confidence`;
  $("#rc-chip").className = "chip ok";
  const cc = done.claim_check;
  $("#rc").innerHTML = `<div class="lbl" style="margin-top:0">Cause</div><div class="lead">${esc(lead)}</div>
    <p>${esc(f.summary || done.answer || "")}</p>
    ${cc ? `<p class="mono" style="font-size:12px;margin-top:10px">✓ ${cc.supported} of ${cc.total} claims checked against the graph</p>` : ""}
    <div class="go" style="margin-top:14px"><a class="btn sm primary" href="/app?tenant=${encodeURIComponent(T)}&incident=${encodeURIComponent(incident)}">Open the case file</a></div>`;
  const sus = (r.suspects || []).slice(0, 6);
  $("#sus-n").textContent = `${(r.suspects || []).length} ranked`;
  $("#sus").innerHTML = sus.map((s, k) => {
    const label = s.change?.id || (s.type === "host" ? `${s.service} (host)` : `${s.service} (no change)`);
    return `<div class="sus${k === 0 ? " first" : ""}"><span>${esc(label)} <span class="faint">· ${esc(s.service)}</span></span><span class="b"><i style="width:${Math.round(s.score * 100)}%"></i></span><span class="r">${s.score.toFixed(2)}</span></div>`;
  }).join("") || '<p class="empty">Nothing to rank.</p>';
  if (cy && f.root_cause_service) {
    cy.getElementById(f.root_cause_service).addClass("root");
    const sym = cy.nodes(".sym")[0];
    if (sym) {
      const path = cy.elements().aStar({ root: sym, goal: cy.getElementById(f.root_cause_service), directed: true });
      if (path.found) path.path.edges().addClass("lit");
    }
  }
}

function bar() {
  const n = steps.length ? steps.length + 1 : 0;
  $("#count").textContent = `Event ${Math.max(0, i + 1)} / ${n}`;
  $("#scrub").value = String(Math.max(0, i));
}

function play() {
  stop();
  if (!steps.length) return record();
  if (i >= steps.length) i = -1;
  timer = setInterval(() => { if (i >= steps.length) return stop(); show(i + 1); }, 650 / speed);
}
function stop() { if (timer) clearInterval(timer); timer = null; }

$("#play").onclick = record;
$("#pause").onclick = () => (timer ? stop() : play());
$("#prev").onclick = () => { stop(); show(i - 1); };
$("#next").onclick = () => { stop(); show(i + 1); };
$("#scrub").oninput = (e) => { stop(); show(+e.target.value); };
$$("#speed button").forEach((b) => b.onclick = () => {
  speed = +b.dataset.s;
  $$("#speed button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  if (timer) play();
});
boot();
