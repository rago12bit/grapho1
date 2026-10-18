// The call graph canvas. Two scopes: the whole system (every service, sized by PageRank) and the open case
// (only the services the case touches, laid out again so the failure path reads top to bottom).
import { $, act, esc, sleep, state, tapi } from "./util.js";

let cy = null;
let home = {};          // node positions in the whole-system layout, restored when switching back
let shown = null;       // the case spec currently drawn, if any

// Cytoscape draws on a canvas and can't read CSS, so its stylesheet is built from the page's colour tokens and
// rebuilt when the theme changes.
function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const v = (n) => cs.getPropertyValue(n).trim();
  return { bg: v("--node-bg"), line: v("--node-line"), text: v("--node-text"), ink: v("--text"), muted: v("--muted"),
    accent: v("--accent"), amber: v("--amber"), symptom: v("--symptom-bg"), onAccent: v("--on-accent") };
}

function style() {
  const t = tokens();
  return [
    { selector: "node", style: {
      shape: "rectangle", width: "label", height: 30, padding: "data(pad)",
      "background-color": t.bg, "border-width": 1, "border-color": t.line,
      label: "data(id)", color: t.text, "font-family": "IBM Plex Mono, monospace", "font-size": 11, "font-weight": 600,
      "text-valign": "center", "text-halign": "center",
      "transition-property": "background-color, border-color, opacity, color", "transition-duration": "250ms",
    } },
    { selector: "node[kind = 'datastore']", style: { "border-style": "double", "border-width": 3 } },
    { selector: "node[kind = 'external']", style: { "border-style": "dashed" } },
    { selector: "node[?exposed]", style: { "border-color": t.muted, color: t.ink } },
    { selector: "edge", style: {
      width: 1.2, "line-color": t.line, "target-arrow-color": t.line, "target-arrow-shape": "triangle",
      "arrow-scale": 0.7, "curve-style": "taxi", "taxi-direction": "downward", "taxi-turn": "45%",
      "transition-property": "line-color, width, opacity", "transition-duration": "250ms",
    } },
    { selector: ".gone", style: { display: "none" } },
    { selector: ".dim", style: { opacity: 0.22 } },
    { selector: "node.alerting", style: { "border-color": t.amber, "border-width": 2, color: t.ink } },
    { selector: "node.symptom", style: { "border-color": t.accent, "border-width": 2, color: t.ink, "background-color": t.symptom } },
    { selector: "node.suspect", style: { "background-color": t.accent, "border-color": t.accent, color: t.onAccent } },
    { selector: "node.lit", style: { color: t.ink } },
    { selector: "node.sel", style: { "border-color": t.ink, "border-width": 3 } },
    { selector: "edge.path", style: { "line-color": t.accent, "target-arrow-color": t.accent, width: 2.4, opacity: 1, "z-index": 9 } },
    { selector: "edge.hit", style: { "line-color": t.ink, "target-arrow-color": t.ink, width: 1.8, opacity: 1 } },
    { selector: "edge.caused", style: { label: "data(lag)", "font-family": "IBM Plex Mono, monospace", "font-size": 9, color: t.ink,
      "text-background-color": t.bg, "text-background-opacity": 1, "text-background-padding": 2, "text-rotation": "none" } },
    { selector: "node.changed", style: { "border-color": t.accent, "border-style": "dashed", "border-width": 2 } },
    { selector: "edge.reach", style: { "line-color": t.amber, "target-arrow-color": t.amber, width: 1.6, "line-style": "dashed", opacity: 0.9 } },
  ];
}

export function restyle() {
  if (cy) cy.style(style());
}

export async function loadGraph() {
  const g = await tapi("/graph");
  state.graph = g;
  const max = Math.max(...g.nodes.map((n) => n.pagerank), 0.0001);
  const elements = [
    ...g.nodes.map((n) => ({ data: { id: n.id, kind: n.kind, team: n.team, exposed: n.exposed, pad: `${Math.round(8 + 10 * (n.pagerank / max))}px` } })),
    ...g.edges.map((e) => ({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target } })),
  ];
  if (cy) cy.destroy();
  shown = null;
  cy = window.cytoscape({ container: $("#cy"), elements, style: style(), wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 2.5,
    layout: { name: "breadthfirst", directed: true, spacingFactor: 1.05, padding: 24, nodeDimensionsIncludeLabels: true,
      roots: g.nodes.filter((n) => n.exposed).map((n) => n.id) } });
  home = Object.fromEntries(cy.nodes().map((n) => [n.id(), { ...n.position() }]));
  cy.on("tap", "node", (e) => act.showService(e.target.id()));
  window.__cy = cy;
  scopeButtons();
}

export function fit(cap = 1.3) {
  if (!cy) return;
  cy.resize();
  cy.fit(cy.elements().not(".gone"), 36);
  if (cy.zoom() > cap) { cy.zoom(cap); cy.center(cy.elements().not(".gone")); }
}

export function clear() {
  if (!cy) return;
  cy.elements().removeClass("dim symptom suspect alerting path hit lit sel reach caused changed");
  caption(null);
}

export function caption(html) {
  const c = $("#caption");
  c.hidden = !html;
  c.innerHTML = html || "";
}

/** What an open case touches: the symptom, the top suspects' paths, everything alerting in the window, the
 *  callers the root cause reaches, and the other incidents the same cause explains. */
export function caseSpec(r, timeline) {
  const names = new Set([r.incident.symptom]);
  (r.suspects || []).slice(0, 3).forEach((s) => s.path.forEach((n) => names.add(n)));
  const top = r.suspects?.[0];
  (top?.host?.services || []).forEach((x) => names.add(x));
  const alerting = [...new Set((timeline?.alerts || []).map((a) => a.service))];
  // A big incident (Train Ticket's reach 30+ services) would shrink every node to nothing: then keep the alerts
  // the leader explains and the roots of the independent faults, and leave the rest to the timeline.
  const keep = alerting.length <= 14 ? alerting
    : [...(top?.footprint?.explained || []).slice(0, 12), ...(r.other_faults || []).map((o) => o.service)];
  keep.forEach((s) => names.add(s));
  (r.impact?.blocks || []).slice(0, alerting.length <= 14 ? 99 : 4).forEach((b) => b.path.forEach((n) => names.add(n)));
  (top?.also_explains || []).forEach((o) => { o.path.forEach((n) => names.add(n)); names.add(o.symptom); });
  return { names };
}

function scopeButtons() {
  document.querySelectorAll("#scope button").forEach((b) => {
    b.classList.toggle("active", b.dataset.scope === state.scope);
    b.disabled = b.dataset.scope === "case" && !shown;
  });
}

export function setScope(scope) {
  state.scope = scope;
  if (shown) draw(shown, false);
  scopeButtons();
}

function draw(spec, animate = true) {
  shown = spec;
  if (!cy) return;
  const keep = cy.nodes().filter((n) => spec.names.has(n.id()));
  if (state.scope === "case") {
    cy.elements().addClass("gone");
    keep.removeClass("gone");
    keep.connectedEdges().filter((e) => keep.has(e.source()) && keep.has(e.target())).removeClass("gone");
    const vis = cy.elements().not(".gone");
    const roots = keep.filter((n) => n.data("exposed") || !n.incomers("node").some((m) => keep.has(m)));
    // A wide fan-out (Train Ticket's gateway calls a dozen services) reads better as rings: the entry point in the
    // middle, one ring per hop below it.
    const depth = {};
    if (keep.length > 10) {
      let ring = roots.map((n) => n.id()), d = 0;
      ring.forEach((id) => { depth[id] = 0; });
      while (ring.length) {
        d += 1;
        ring = ring.flatMap((id) => cy.$id(id).outgoers("node").filter((m) => keep.has(m) && depth[m.id()] == null).map((m) => m.id()));
        ring = [...new Set(ring)];
        ring.forEach((id) => { depth[id] = d; });
      }
    }
    const lay = keep.length > 10
      ? vis.layout({ name: "concentric", concentric: (n) => 100 - (depth[n.id()] ?? 9), levelWidth: () => 1, minNodeSpacing: 18,
          padding: 30, nodeDimensionsIncludeLabels: true, animate, animationDuration: 350, fit: false })
      : vis.layout({ name: "breadthfirst", directed: true, roots, spacingFactor: 1.1, padding: 30, nodeDimensionsIncludeLabels: true,
          animate, animationDuration: 350, fit: false });
    lay.one("layoutstop", () => fit());
    lay.run();
  } else {
    cy.elements().removeClass("gone");
    cy.nodes().positions((n) => home[n.id()] || n.position());
  }
  fit();
  scopeButtons();
}

export function showSystem() {
  shown = null;
  replay = null;
  const bar = document.querySelector("#scrub");
  if (bar) bar.hidden = true;
  if (!cy) return;
  cy.elements().removeClass("gone");
  cy.nodes().positions((n) => home[n.id()] || n.position());
  clear();
  fit(1.25);
  scopeButtons();
}

/** Draw the case: scope it, then mark the symptom, alerting services and the leader's path. */
export function showCase(r, timeline, { animate = false } = {}) {
  draw(caseSpec(r, timeline), true);
  mark(r, timeline, !animate);
  setupReplay(r, timeline);
}

// --- replay: scrub through the incident and watch the graph change -----------------------------------------

let replay = null;

function setupReplay(r, timeline) {
  const bar = $("#scrub");
  const events = [...(timeline?.alerts || []).map((a) => ({ ...a, type: "alert" })), ...(timeline?.changes || []).map((c) => ({ ...c, type: "change" }))]
    .filter((e) => e.at && cy.$id(e.service).nonempty()).sort((a, b) => a.at - b.at);
  if (!events.length) { bar.hidden = true; replay = null; return; }
  const t0 = events[0].at - 120, t1 = Math.max(events[events.length - 1].at, r.incident.started) + 120;
  replay = { r, timeline, events, t0, t1 };
  const range = $("#scrub-t");
  range.min = 0; range.max = 1000; range.value = 1000;
  bar.hidden = false;
  $("#scrub-label").textContent = "whole incident";
}

/** The graph as it stood at time t: what had changed, what was alerting, which causal hops had happened. */
export function replayAt(frac) {
  if (!replay || !cy) return;
  const { r, events, t0, t1 } = replay;
  const t = t0 + (t1 - t0) * frac;
  cy.elements().not(".gone").removeClass("alerting suspect path caused lit symptom changed reach").addClass("dim");
  const sym = cy.$id(r.incident.symptom);
  sym.removeClass("dim");
  const seen = events.filter((e) => e.at <= t);
  seen.forEach((e) => cy.$id(e.service).removeClass("dim").addClass(e.type === "alert" ? "alerting" : "changed"));
  if (seen.some((e) => e.type === "alert" && e.service === r.incident.symptom) || t >= r.incident.started) sym.addClass("symptom");
  const at = new Map(seen.filter((e) => e.type === "alert").map((e) => [e.id, e.at]));
  for (const l of r.reasoning?.chain?.links || []) {
    if (l.to_label !== "Alert" || !l.direct || !at.has(l.from) || !at.has(l.to)) continue;
    const e = cy.$id(`${l.from_service}->${l.to_service}`);
    if (e.nonempty()) e.data("lag", `+${l.lag_s}s`).removeClass("dim").addClass("caused path");
  }
  const last = seen[seen.length - 1];
  const hhmm = (x) => new Date(x * 1000).toISOString().slice(11, 16);
  $("#scrub-label").textContent = `${hhmm(t)} UTC · ${seen.filter((e) => e.type === "alert").length} alerting, ${seen.filter((e) => e.type === "change").length} changed`
    + (last ? ` · last: ${last.type === "alert" ? `${last.service} alerted` : `${last.id} on ${last.service}`}` : "");
  if (frac >= 1) mark(r, replay.timeline, true);
}

export async function playReplay(v) {
  if (!replay) return;
  const range = $("#scrub-t");
  for (let i = 0; i <= 100; i++) {
    if (v !== state.view) return;
    range.value = i * 10;
    replayAt(i / 100);
    await sleep(80);
  }
}

function mark(r, timeline, full) {
  clear();
  const top = r.suspects?.[0];
  cy.elements().not(".gone").addClass("dim");
  const sym = cy.$id(r.incident.symptom);
  sym.removeClass("dim").addClass("symptom lit");
  new Set((timeline?.alerts || []).map((a) => a.service)).forEach((s) => cy.$id(s).removeClass("dim").addClass("alerting"));
  if (!top) return;
  top.path.forEach((n) => cy.$id(n).removeClass("dim").addClass("lit"));
  if (full) {
    for (let i = 0; i < top.path.length - 1; i++) cy.$id(`${top.path[i]}->${top.path[i + 1]}`).removeClass("dim").addClass("path");
    (top.host?.services || [top.service]).forEach((x) => cy.$id(x).removeClass("dim").addClass("suspect"));
    reach(r);
    caused(r);
  }
}

/** Label each call edge a causal link travelled with how long the failure took to climb it. */
function caused(r) {
  for (const l of r.reasoning?.chain?.links || []) {
    if (l.to_label !== "Alert" || !l.direct) continue;
    const e = cy.$id(`${l.from_service}->${l.to_service}`);
    if (e.nonempty()) e.data("lag", `+${l.lag_s}s`).removeClass("dim").addClass("caused");
  }
}

function reach(r) {
  for (const b of r.impact?.blocks || []) {
    const p = b.path;   // cause first, up to the affected caller
    for (let i = 0; i < p.length - 1; i++) {
      const e = cy.$id(`${p[i + 1]}->${p[i]}`);
      if (!e.hasClass("path")) e.removeClass("dim").addClass("reach");
      cy.$id(p[i + 1]).removeClass("dim");
    }
  }
}

/** Walk the failure path hop by hop, then the other incidents the same cause explains. */
export async function animate(r, timeline, v) {
  const top = r.suspects?.[0];
  if (!cy || !top) return;
  mark(r, timeline, false);
  caption(`<b>${esc(r.incident.id)}</b> · following ${esc(top.path.join(" → "))}`);
  for (let i = 0; i < top.path.length - 1; i++) {
    await sleep(360);
    if (v !== state.view) return;
    cy.$id(`${top.path[i]}->${top.path[i + 1]}`).removeClass("dim").addClass("path");
    cy.$id(top.path[i + 1]).removeClass("dim");
  }
  await sleep(220);
  cy.$id(top.service).addClass("suspect");
  caption(`<b>${esc(r.incident.id)}</b> · ${top.change ? `${esc(top.change.id)} on <b>${esc(top.service)}</b>, ${top.hops} hop${top.hops === 1 ? "" : "s"} below ${esc(r.incident.symptom)}` : `no change on the path; <b>${esc(top.service)}</b> failed first`}`);
  for (const o of top.also_explains || []) {
    await sleep(300);
    if (v !== state.view) return;
    cy.$id(o.symptom).removeClass("dim").addClass("symptom lit");
    for (let i = 0; i < o.path.length - 1; i++) {
      await sleep(220);
      cy.$id(`${o.path[i]}->${o.path[i + 1]}`).removeClass("dim").addClass("path");
      cy.$id(o.path[i + 1]).removeClass("dim");
    }
  }
  if (top.also_explains?.length) caption(`<b>${esc(top.change.id)}</b> on <b>${esc(top.service)}</b> explains ${[r.incident.id, ...top.also_explains.map((o) => o.id)].map(esc).join(" and ")}`);
  await sleep(250);
  if (v === state.view) { reach(r); caused(r); }
}

/** Light up a set of services (answers, teams, a service card) on the whole-system view. */
export function highlight(names, { select = null } = {}) {
  if (!cy) return;
  if (shown) showSystem();
  clear();
  const set = new Set(names);
  if (!set.size) return;
  cy.elements().addClass("dim");
  set.forEach((n) => cy.$id(n).removeClass("dim").addClass("lit"));
  cy.edges().forEach((e) => { if (set.has(e.source().id()) && set.has(e.target().id())) e.removeClass("dim").addClass("hit"); });
  if (select) cy.$id(select).addClass("sel");
}
