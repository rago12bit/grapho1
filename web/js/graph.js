// The call graph canvas. Two scopes: the whole system (every service, sized by PageRank) and the open case
// (only the services the case touches, laid out again so the failure path reads top to bottom).
import { $, act, esc, sleep, state, tapi } from "./util.js";

let cy = null;
let home = {};          // node positions in the whole-system layout, restored when switching back
let shown = null;       // the case spec currently drawn, if any

const STYLE = [
  { selector: "node", style: {
    shape: "rectangle", width: "label", height: 30, padding: "data(pad)",
    "background-color": "#15140f", "border-width": 1, "border-color": "#3a372c",
    label: "data(id)", color: "#a9a391", "font-family": "IBM Plex Mono, monospace", "font-size": 11, "font-weight": 600,
    "text-valign": "center", "text-halign": "center",
    "transition-property": "background-color, border-color, opacity, color", "transition-duration": "250ms",
  } },
  { selector: "node[kind = 'datastore']", style: { "border-style": "double", "border-width": 3 } },
  { selector: "node[kind = 'external']", style: { "border-style": "dashed" } },
  { selector: "node[?exposed]", style: { "border-color": "#a9a391", color: "#ece7d9" } },
  { selector: "edge", style: {
    width: 1.2, "line-color": "#3a372c", "target-arrow-color": "#3a372c", "target-arrow-shape": "triangle",
    "arrow-scale": 0.7, "curve-style": "taxi", "taxi-direction": "downward", "taxi-turn": "45%",
    "transition-property": "line-color, width, opacity", "transition-duration": "250ms",
  } },
  { selector: ".gone", style: { display: "none" } },
  { selector: ".dim", style: { opacity: 0.22 } },
  { selector: "node.alerting", style: { "border-color": "#e2b04a", "border-width": 2, color: "#ece7d9" } },
  { selector: "node.symptom", style: { "border-color": "#ef6a55", "border-width": 2, color: "#ece7d9", "background-color": "#211510" } },
  { selector: "node.suspect", style: { "background-color": "#ef6a55", "border-color": "#ef6a55", color: "#1a0f0c" } },
  { selector: "node.lit", style: { color: "#ece7d9" } },
  { selector: "node.sel", style: { "border-color": "#ece7d9", "border-width": 3 } },
  { selector: "edge.path", style: { "line-color": "#ef6a55", "target-arrow-color": "#ef6a55", width: 2.4, opacity: 1, "z-index": 9 } },
  { selector: "edge.hit", style: { "line-color": "#ece7d9", "target-arrow-color": "#ece7d9", width: 1.8, opacity: 1 } },
  { selector: "edge.reach", style: { "line-color": "#e2b04a", "target-arrow-color": "#e2b04a", width: 1.6, "line-style": "dashed", opacity: 0.9 } },
];

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
  cy = window.cytoscape({ container: $("#cy"), elements, style: STYLE, wheelSensitivity: 0.2, minZoom: 0.2, maxZoom: 2.5,
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
  cy.elements().removeClass("dim symptom suspect alerting path hit lit sel reach");
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
  (timeline?.alerts || []).forEach((a) => names.add(a.service));
  (r.impact?.blocks || []).forEach((b) => b.path.forEach((n) => names.add(n)));
  const top = r.suspects?.[0];
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
    const lay = vis.layout({ name: "breadthfirst", directed: true, roots, spacingFactor: 1.1, padding: 30, nodeDimensionsIncludeLabels: true,
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
    cy.$id(top.service).addClass("suspect");
    reach(r);
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
  if (v === state.view) reach(r);
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
