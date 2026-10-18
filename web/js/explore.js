// Explore: the tenant's whole graph on one canvas. Filter by label, find a node, open it to read its properties.
import { $, $$, api, cypher, esc, mountShell, tenant } from "./shell.js";

mountShell();
const T = tenant();
const COLOR = {
  Service: "#93c5fd", Change: "#ff6b4a", Alert: "#ff8a6e", Incident: "#ffb347", Hypothesis: "#c4b5fd", Evidence: "#a78bfa",
  Action: "#6ee7b7", Person: "#f9a8d4", Team: "#f472b6", OnCallShift: "#be185d", Commit: "#52525b", Deployment: "#67e8f9",
  Pod: "#22d3ee", Node: "#0ea5e9", Image: "#164e63", ConfigMap: "#a3e635", ConfigKey: "#65a30d", Journey: "#fde047",
  Lesson: "#facc15", Runbook: "#d6d3d1", Episode: "#78716c", Investigation: "#a8a29e", Operation: "#60a5fa", LogPattern: "#fb7185",
  Zone: "#334155", Org: "#f4f4f5",
};
const HIDE_FIRST = new Set(["Commit", "OnCallShift", "Image", "Episode", "Evidence"]);   // busy labels start hidden; one click shows them
const color = (l) => COLOR[l] || "#a1a1aa";
let cy = null, data = null, shown = null, layoutName = "cose";

$("#tenant-chip").textContent = `tenant · ${T}`;

async function load() {
  const t0 = performance.now();
  try { data = await api(`/api/${encodeURIComponent(T)}/explore`); }
  catch (e) { $("#cy").innerHTML = `<p class="empty">Couldn't read the graph: ${esc(e.message)}</p>`; return; }
  const ms = Math.round(performance.now() - t0);
  const counts = {};
  data.nodes.forEach((n) => { counts[n.label] = (counts[n.label] || 0) + 1; });
  const labels = Object.keys(counts).sort((a, b) => counts[b] - counts[a]);
  shown = new Set(labels.filter((l) => !HIDE_FIRST.has(l) || data.nodes.length < 250));
  $("#labels").innerHTML = labels.map((l) => `<button class="lab" data-l="${esc(l)}" aria-pressed="${shown.has(l)}"><i style="background:${color(l)}"></i>${esc(l)} <span class="faint">${counts[l]}</span></button>`).join("");
  $$(".lab").forEach((b) => b.onclick = () => {
    const l = b.dataset.l;
    shown.has(l) ? shown.delete(l) : shown.add(l);
    b.setAttribute("aria-pressed", String(shown.has(l)));
    draw();
  });
  $("#ex-stats").innerHTML = `<span class="chip ok">${data.nodes.length.toLocaleString()} nodes</span><span class="chip">${data.edges.length.toLocaleString()} edges</span><span class="chip">read in ${ms} ms</span>${data.truncated ? '<span class="chip warn">capped</span>' : ""}`;
  $("#dlg-q").innerHTML = cypher(data.cypher.replace("; ", "\n\n"));
  draw();
  topTable();
}

function draw() {
  const keep = new Set(data.nodes.filter((n) => shown.has(n.label)).map((n) => n.key));
  const deg = {};
  data.edges.forEach((e) => { if (keep.has(e.source) && keep.has(e.target)) { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; } });
  const els = [
    ...data.nodes.filter((n) => keep.has(n.key)).map((n) => ({ data: { id: String(n.key), label: n.name, kind: n.label, deg: deg[n.key] || 0 } })),
    ...data.edges.filter((e) => keep.has(e.source) && keep.has(e.target)).map((e, i) => ({ data: { id: `e${i}`, source: String(e.source), target: String(e.target), type: e.type } })),
  ];
  if (cy) cy.destroy();
  cy = cytoscape({
    container: $("#cy"), elements: els, minZoom: 0.1, maxZoom: 3, wheelSensitivity: 0.25,
    style: [
      { selector: "node", style: { "background-color": (n) => color(n.data("kind")), width: (n) => 8 + Math.min(26, Math.sqrt(n.data("deg")) * 4), height: (n) => 8 + Math.min(26, Math.sqrt(n.data("deg")) * 4),
        label: (n) => (n.data("deg") >= 4 || n.data("kind") === "Service" || n.data("kind") === "Incident" ? n.data("label") : ""), color: "#a1a1aa", "font-family": "IBM Plex Mono", "font-size": 8,
        "text-valign": "bottom", "text-margin-y": 3, "border-width": 0 } },
      { selector: "edge", style: { width: 0.6, "line-color": "#2c2c34", "curve-style": "haystack", opacity: 0.8 } },
      { selector: "node.hl", style: { "border-width": 2.5, "border-color": "#f4f4f5", label: "data(label)", color: "#f4f4f5", "z-index": 10 } },
      { selector: "node.nb", style: { label: "data(label)", color: "#d4d4d8" } },
      { selector: "edge.hl", style: { "line-color": "#ff6b4a", width: 1.6, opacity: 1, label: "data(type)", "font-size": 7, color: "#ff8a6e", "font-family": "IBM Plex Mono", "text-rotation": "autorotate" } },
      { selector: ".dim", style: { opacity: 0.12 } },
    ],
    layout: layoutOpts(),
  });
  cy.on("tap", "node", (e) => select(e.target.id()));
  cy.on("tap", (e) => { if (e.target === cy) clear(); });
}

function layoutOpts() {
  if (layoutName === "concentric") return { name: "concentric", concentric: (n) => n.data("deg"), levelWidth: () => 3, minNodeSpacing: 6, animate: false };
  if (layoutName === "breadthfirst") return { name: "breadthfirst", directed: true, spacingFactor: 0.9, animate: false };
  return { name: "cose", animate: false, nodeRepulsion: () => 9000, idealEdgeLength: () => 46, gravity: 0.6, numIter: 900, randomize: true };
}

function clear() { cy.elements().removeClass("hl nb dim"); }

async function select(id) {
  clear();
  const n = cy.getElementById(id);
  if (!n.length) return;
  const hood = n.closedNeighborhood();
  cy.elements().not(hood).addClass("dim");
  n.addClass("hl"); n.connectedEdges().addClass("hl"); n.neighborhood("node").addClass("nb");
  cy.animate({ center: { eles: n }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 350 });
  $("#detail").innerHTML = `<div class="card-h"><h3>Loading…</h3></div>`;
  try {
    const d = await api(`/api/${encodeURIComponent(T)}/nodes/${id}`);
    $("#detail").innerHTML = `
      <div class="card-h"><h3><span style="color:${color(d.label)}">:${esc(d.label)}</span></h3><span class="chip mono">id(n) = ${d.key}</span></div>
      <dl class="kv">${Object.entries(d.props).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</dd>`).join("") || "<dd class='faint'>no properties</dd>"}</dl>
      <div class="lbl" style="padding:0 18px">Neighbours · ${d.neighbours.length}</div>
      <div class="nb">${d.neighbours.map((m) => `<button data-k="${m.key}"><span class="rel">${m.out ? "→" : "←"} ${esc(m.type)}</span><span style="color:${color(m.label)}">●</span>${esc(m.name ?? m.label)}</button>`).join("")}</div>
      <pre class="code" style="margin:0 18px 18px;font-size:11px">${cypher(`MATCH (n)-[r]-(m) WHERE id(n) = ${d.key}\nRETURN type(r), m`)}</pre>`;
    $$("#detail [data-k]").forEach((b) => b.onclick = () => { const k = b.dataset.k; if (!cy.getElementById(k).length) return; select(k); });
  } catch (e) { $("#detail").innerHTML = `<p class="empty">${esc(e.message)}</p>`; }
}

function topTable() {
  const deg = {};
  data.edges.forEach((e) => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
  const rows = data.nodes.map((n) => ({ ...n, deg: deg[n.key] || 0 })).sort((a, b) => b.deg - a.deg).slice(0, 30);
  $("#top").innerHTML = `<thead><tr><th>node</th><th>label</th><th class="r">edges</th></tr></thead><tbody>${rows.map((r) =>
    `<tr data-k="${r.key}"><td class="mono">${esc(r.name)}</td><td><span style="color:${color(r.label)}">●</span> ${esc(r.label)}</td><td class="r">${r.deg}</td></tr>`).join("")}</tbody>`;
  $$("#top [data-k]").forEach((tr) => tr.onclick = () => {
    const k = tr.dataset.k, n = data.nodes.find((x) => String(x.key) === k);
    if (n && !shown.has(n.label)) { shown.add(n.label); $(`.lab[data-l="${n.label}"]`).setAttribute("aria-pressed", "true"); draw(); }
    select(k);
  });
}

$("#q").addEventListener("keydown", (e) => {
  if (e.key !== "Enter" || !cy) return;
  const q = e.target.value.trim().toLowerCase();
  const hit = cy.nodes().filter((n) => String(n.data("label")).toLowerCase() === q)[0] || cy.nodes().filter((n) => String(n.data("label")).toLowerCase().includes(q))[0];
  if (hit) select(hit.id());
});
$$("#layout button").forEach((b) => b.onclick = () => {
  layoutName = b.dataset.l;
  $$("#layout button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
  cy && cy.layout(layoutOpts()).run();
});
$("#fit").onclick = () => cy && cy.fit(undefined, 30);
$("#show-q").onclick = () => { $("#dlg").hidden = false; };
$("#dlg-x").onclick = () => { $("#dlg").hidden = true; };
$("#dlg").onclick = (e) => { if (e.target.id === "dlg") $("#dlg").hidden = true; };
load();
