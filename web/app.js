"use strict";

const state = { tenant: "kestrel", incidents: [], selected: null, last: null, cy: null, health: null, view: 0 };
const $ = (s, el = document) => el.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const hhmm = (ts) => new Date(ts * 1000).toISOString().slice(11, 16) + " UTC";
const ago = (ts) => {
  const m = Math.round(((state.health?.now ?? ts) - ts) / 60);
  if (m < 90) return `${m} min ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

/* ---------- header ---------- */
async function loadHealth() {
  const pill = $("#db-pill");
  try {
    state.health = await api("/api/health");
    pill.className = "pill ok";
    const mode = $("#mode-pill");
    mode.textContent = state.health.claude ? `agent: ${state.health.model}` : "agent: offline playbook";
    mode.className = "pill mode" + (state.health.claude ? " claude" : "");
    mode.title = state.health.claude ? "Claude chooses which graph tools to call" :
      "No ANTHROPIC_API_KEY: a fixed playbook calls the same graph tools";
  } catch {
    pill.className = "pill bad";
    $("#db-stats").textContent = "offline";
  }
}

async function loadTenants() {
  const list = await api("/api/tenants");
  const nav = $("#tenants");
  nav.innerHTML = list.map((t) => `<button data-t="${esc(t.id)}" class="${t.id === state.tenant ? "active" : ""}">${esc(t.title)}</button>`).join("");
  const cur = list.find((t) => t.id === state.tenant);
  const n = Object.values(cur.nodes).reduce((a, b) => a + b, 0);
  const e = Object.values(cur.edges).reduce((a, b) => a + b, 0);
  $("#db-stats").textContent = `${n} nodes · ${e} edges`;
  nav.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => switchTenant(b.dataset.t)));
}

async function switchTenant(t) {
  state.tenant = t;
  state.selected = null;
  state.last = null;
  try { localStorage.setItem("faultline.tenant", t); } catch {}
  renderEmpty();
  await Promise.all([loadTenants(), loadGraph(), loadIncidents(), loadMemory()]);
}

/* ---------- graph ---------- */
async function loadGraph() {
  const g = await api(`/api/${state.tenant}/graph`);
  const max = Math.max(...g.nodes.map((n) => n.pagerank), 0.0001);
  const elements = [
    ...g.nodes.map((n) => ({ data: { id: n.id, kind: n.kind, team: n.team, exposed: n.exposed, size: 22 + 30 * (n.pagerank / max) } })),
    ...g.edges.map((e) => ({ data: { id: `${e.source}->${e.target}`, source: e.source, target: e.target } })),
  ];
  if (state.cy) state.cy.destroy();
  state.cy = cytoscape({
    container: $("#cy"),
    elements,
    wheelSensitivity: 0.2,
    style: [
      { selector: "node", style: {
        width: "data(size)", height: "data(size)", "background-color": "#232933", "border-width": 2, "border-color": "#3a4350",
        label: "data(id)", color: "#aab2bd", "font-family": "JetBrains Mono, monospace", "font-size": 10,
        "text-valign": "bottom", "text-margin-y": 5, "text-outline-color": "#0d0f12", "text-outline-width": 3,
        "transition-property": "background-color, border-color, opacity", "transition-duration": "250ms",
      } },
      { selector: "node[kind = 'datastore']", style: { shape: "barrel" } },
      { selector: "node[kind = 'external']", style: { shape: "diamond", "border-style": "dashed" } },
      { selector: "node[?exposed]", style: { "border-color": "#6aa8ff" } },
      { selector: "edge", style: {
        width: 1.4, "line-color": "#2c333d", "target-arrow-color": "#2c333d", "target-arrow-shape": "triangle",
        "arrow-scale": 0.8, "curve-style": "bezier", "transition-property": "line-color, width, opacity", "transition-duration": "250ms",
      } },
      { selector: ".dim", style: { opacity: 0.25 } },
      { selector: "node.alerting", style: { "border-color": "#f5b942", "border-width": 3 } },
      { selector: "node.symptom", style: { "border-color": "#ff5d5d", "border-width": 4, color: "#e6e8eb" } },
      { selector: "node.suspect", style: { "background-color": "#ff7a45", "border-color": "#ff7a45", color: "#ffb08f" } },
      { selector: "node.lit", style: { color: "#e6e8eb" } },
      { selector: "edge.path", style: { "line-color": "#ff7a45", "target-arrow-color": "#ff7a45", width: 3.2, opacity: 1 } },
      { selector: "edge.hit", style: { "line-color": "#6aa8ff", "target-arrow-color": "#6aa8ff", width: 2.4, opacity: 1 } },
    ],
    layout: {
      name: "breadthfirst", directed: true, spacingFactor: 1.05, padding: 30,
      roots: g.nodes.filter((n) => n.exposed).map((n) => n.id),
    },
  });
}

function clearGraph() {
  if (!state.cy) return;
  state.cy.elements().removeClass("dim symptom suspect alerting path hit lit");
  caption(null);
}

function caption(html) {
  const c = $("#caption");
  c.hidden = !html;
  c.innerHTML = html || "";
}

async function animatePath(path, alerts, symptom, suspect) {
  const cy = state.cy;
  cy.elements().addClass("dim");
  const keep = new Set([...path, ...alerts]);
  keep.forEach((id) => cy.$id(id).removeClass("dim").addClass("lit"));
  cy.$id(symptom).addClass("symptom");
  alerts.forEach((id) => cy.$id(id).addClass("alerting"));
  for (let i = 0; i < path.length - 1; i++) {
    await sleep(380);
    const e = cy.$id(`${path[i]}->${path[i + 1]}`);
    e.removeClass("dim").addClass("path");
    cy.$id(path[i + 1]).removeClass("dim");
  }
  await sleep(250);
  if (suspect) cy.$id(suspect).addClass("suspect");
}

async function drawLinked(path, symptom) {
  const cy = state.cy;
  cy.$id(symptom).removeClass("dim").addClass("symptom lit");
  for (let i = 0; i < path.length - 1; i++) {
    await sleep(250);
    cy.$id(`${path[i]}->${path[i + 1]}`).removeClass("dim").addClass("path");
    cy.$id(path[i + 1]).removeClass("dim");
  }
}

function highlightServices(names, edges = []) {
  const cy = state.cy;
  clearGraph();
  if (!names.length) return;
  cy.elements().addClass("dim");
  names.forEach((n) => cy.$id(n).removeClass("dim").addClass("lit"));
  edges.forEach(([a, b]) => cy.$id(`${a}->${b}`).removeClass("dim").addClass("hit"));
}

/* ---------- left column ---------- */
async function loadIncidents() {
  state.incidents = await api(`/api/${state.tenant}/incidents`);
  const sev = (i) => Number(String(i.severity).replace(/\D/g, "")) || 9;
  const open = state.incidents.filter((i) => i.status !== "resolved").sort((a, b) => sev(a) - sev(b) || b.started - a.started);
  const done = state.incidents.filter((i) => i.status === "resolved").slice(0, 3);
  $("#incident-list").innerHTML = [...open, ...done].map((i) => `
    <li><button data-id="${esc(i.id)}" class="${state.selected === i.id ? "active" : ""}">
      <span class="row1"><span class="sev ${esc(i.severity)}">${esc(i.severity)}</span>${esc(i.id)}<span class="state ${esc(i.status)}">${esc(i.status)}</span></span>
      <span class="title">${esc(i.title)}</span>
      <span class="meta">symptom on <b>${esc(i.symptom)}</b> · ${ago(i.started)}</span>
    </button></li>`).join("") || `<li class="hint">No incidents.</li>`;
  $("#incident-list").querySelectorAll("button").forEach((b) => b.addEventListener("click", () => selectIncident(b.dataset.id)));
}

async function loadMemory() {
  const rows = await api(`/api/${state.tenant}/memory`);
  $("#memory-list").innerHTML = rows.map((r) => `
    <li>
      <div class="mtitle">${esc(r.id)} · ${esc(r.title)}</div>
      <div class="cause">${esc(r.cause_kind || "")} on <b>${esc(r.cause_service || "?")}</b>: ${esc(r.cause || "")}<br>${ago(r.started)} · fixed: ${esc((r.fixes || []).join("; "))}</div>
      ${(r.lessons || []).map((l) => `<div class="lesson">${esc(l)}</div>`).join("")}
    </li>`).join("") || `<li class="hint">Nothing learned yet.</li>`;
}

/* ---------- case file ---------- */
function renderEmpty() {
  state.view++;
  clearGraph();
  $("#case").innerHTML = $("#case").dataset.empty;
}

async function selectIncident(id) {
  const v = ++state.view;
  state.selected = id;
  state.last = null;
  $("#incident-list").querySelectorAll("button").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
  const inc = await api(`/api/${state.tenant}/incidents/${id}`);
  if (v !== state.view) return;
  clearGraph();
  state.cy.elements().addClass("dim");
  state.cy.$id(inc.symptom).removeClass("dim").addClass("symptom lit");
  renderCase(inc);
}

function renderCase(inc, result = null) {
  const prior = inc.investigations?.length
    ? `<div class="sub">${inc.investigations.length} earlier investigation${inc.investigations.length > 1 ? "s" : ""} · last blamed ${esc(inc.investigations[0].top_change || inc.investigations[0].top_service || "nothing")} (${esc(inc.investigations[0].confidence)})</div>` : "";
  $("#case").innerHTML = `
    <div class="case">
      <div class="sub">${esc(inc.id)} · <span class="sev ${esc(inc.severity)}">${esc(inc.severity)}</span> · started ${hhmm(inc.started)} · ${esc(inc.status)}</div>
      <h2>${esc(inc.title)}</h2>
      <div class="sub">symptom on ${esc(inc.symptom)} (${esc(inc.team)})</div>
      ${prior}
      <div class="section"><h3>Alerts on ${esc(inc.symptom)}</h3>
        ${inc.alerts.map((a) => `<div class="mono">${hhmm(a.at)} · ${esc(a.signal)}</div>`).join("") || `<div class="muted">none</div>`}
      </div>
      <div class="bar">
        <button id="go" class="primary">${result ? "Investigate again" : "Investigate"}</button>
        <span id="busy" class="muted"></span>
      </div>
      <div id="result"></div>
      <div id="actions"></div>
    </div>`;
  $("#go").addEventListener("click", () => investigate(inc));
  renderActions(inc);
}

async function investigate(inc) {
  const v = ++state.view;
  const btn = $("#go");
  btn.disabled = true;
  $("#busy").innerHTML = `<span class="spinner"></span> ${state.health?.claude ? "Claude is walking the graph…" : "walking the graph…"}`;
  clearGraph();
  let out;
  try {
    out = await api(`/api/${state.tenant}/incidents/${inc.id}/investigate`, { method: "POST" });
  } catch (e) {
    if (v === state.view) { $("#busy").textContent = e.message; btn.disabled = false; }
    return;
  }
  if (v !== state.view) return;
  state.last = out;
  $("#busy").textContent = "";
  btn.disabled = false;
  btn.textContent = "Investigate again";
  await renderResult(out, v);
  if (v !== state.view) return;
  const fresh = await api(`/api/${state.tenant}/incidents/${inc.id}`);
  if (v !== state.view) return;
  renderActions(fresh);
  loadIncidents();
}

async function renderResult(out, v = state.view) {
  const box = $("#result");
  const f = out.findings || {};
  const r = out.ranking;
  const top = r?.suspects?.[0];
  box.innerHTML = `
    ${out.fallback_reason ? `<div class="banner">Claude was unavailable (${esc(out.fallback_reason)}); the offline playbook answered.</div>` : ""}
    <div class="section"><h3>What the agent did · ${esc(out.mode === "claude" ? out.model : "offline playbook")}</h3><ol class="steps" id="steps"></ol></div>`;
  const ol = $("#steps");
  let role = null;
  for (const s of out.steps) {
    if (s.role && s.role !== role) {
      role = s.role;
      const h = document.createElement("li");
      h.className = "rolehead";
      h.innerHTML = `<span class="role ${esc(role)}">${esc(role)} agent</span><span class="muted">${ROLE_NOTE[role] || ""}</span>`;
      ol.appendChild(h);
    }
    const li = document.createElement("li");
    li.className = [s.error ? "err" : "", s.tool.endsWith("_handoff") ? "handoff" : ""].join(" ").trim();
    li.innerHTML = stepHtml(s);
    ol.appendChild(li);
    await sleep(140);
    if (v !== state.view) return;
  }
  if (top) {
    const alerting = [...new Set([...(top.evidence_alerts || []).map((a) => a.service), r.incident.symptom])];
    caption(`<b>${esc(r.incident.id)}</b> · following ${esc(top.path.join(" → "))}`);
    await animatePath(top.path, alerting, r.incident.symptom, top.service);
    if (v !== state.view) return;
    caption(`<b>${esc(r.incident.id)}</b> · ${top.change ? `${esc(top.change.id)} on <b>${esc(top.service)}</b>, ${top.hops} hop${top.hops === 1 ? "" : "s"} below ${esc(r.incident.symptom)}` : `no change on the path; <b>${esc(top.service)}</b> failed first`}`);
    for (const o of top.also_explains || []) {
      await sleep(300);
      if (v !== state.view) return;
      await drawLinked(o.path, o.symptom);
    }
    if (top.also_explains?.length) caption(`<b>${esc(top.change.id)}</b> on <b>${esc(top.service)}</b> explains ${[r.incident.id, ...top.also_explains.map((o) => o.id)].map(esc).join(" and ")}`);
  }
  const shared = top?.change && (!f.change_id || f.change_id === top.change.id) ? (top.also_explains || []) : [];
  const verdict = document.createElement("div");
  verdict.className = "section";
  verdict.innerHTML = `
    <h3>Verdict</h3>
    <div class="verdict ${f.change_id ? "" : "none"}">
      <div class="head"><span class="big">${esc(f.change_id || (f.root_cause_service ? f.root_cause_service : "no cause found"))}</span>
        <span class="conf ${esc(f.confidence)}">${esc(f.confidence || "")} confidence</span></div>
      ${shared.length ? `<div class="shared"><b>Shared root cause.</b> ${esc(top.change.id)} also explains ${shared.map((o) => `<button class="linkbtn" data-inc="${esc(o.id)}">${esc(o.id)}</button> ${esc(o.title)}`).join(", ")}. One fix covers ${shared.length === 1 ? "both" : "all of them"}.</div>` : ""}
      <p>${esc(f.summary || out.answer || "")}</p>
      ${top?.footprint ? `<div class="seq"><span class="muted">How it spread:</span> ${top.footprint.sequence.map((x) => `<span class="mono">${esc(x.service)} ${hhmm(x.at).slice(0, 5)}</span>`).join(" → ")}${top.footprint.in_order ? ` <span class="ok">in the predicted order</span>` : ""}</div>` : ""}
      ${r?.other_faults?.length ? `<div class="others"><b>Other faults active below ${esc(r.incident.symptom)}</b>${r.other_faults.map((o) => `<div>${esc((o.change || {}).id || o.service)}${o.change ? "" : " (no change)"} explains ${esc(o.explains.join(", "))}${o.incidents.length ? ` · ${o.incidents.map((i) => `<button class="linkbtn" data-inc="${esc(i.id)}">${esc(i.id)}</button>`).join(" ")}` : ""}</div>`).join("")}</div>` : ""}
      ${f.evidence?.length ? `<ul class="evidence">${f.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>` : ""}
      ${f.ruled_out?.length ? `<div class="section"><h3>Ruled out</h3><ul class="ruled">${f.ruled_out.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
    </div>`;
  box.appendChild(verdict);
  verdict.querySelectorAll(".linkbtn").forEach((b) => b.addEventListener("click", () => selectIncident(b.dataset.inc)));
  if (r?.suspects?.length) box.appendChild(suspectsTable(r));
}

const ROLE_NOTE = {
  triage: "what is failing, what changed, what is alerting",
  investigator: "scores suspects and simulates each one's footprint",
  responder: "owner, runbook, and the fix to approve",
};

function stepHtml(s) {
  const args = Object.entries(s.input || {}).filter(([k]) => k !== "summary" && k !== "reason")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : Array.isArray(v) ? (v.length > 3 ? `[${v.length} items]` : JSON.stringify(v)) : v && typeof v === "object" ? "{…}" : JSON.stringify(v)}`).join(", ");
  const q = s.cypher || [];
  return `
    <div class="tool">${esc(s.tool)}(${esc(args)})</div>
    ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}
    ${s.error ? `<div class="note" style="color:var(--red)">${esc(s.error)}</div>` : ""}
    ${q.length ? `<details><summary>${q.length} Cypher quer${q.length === 1 ? "y" : "ies"} · ${q.reduce((a, x) => a + x.rows, 0)} rows</summary>
      ${q.map((x) => `<pre class="cypher">${cypher(x.cypher)}</pre><div class="qmeta">${esc(JSON.stringify(x.params))} · ${x.rows} rows · ${x.ms} ms</div>`).join("")}
    </details>` : ""}`;
}

function cypher(text) {
  return esc(text).replace(/\b(MATCH|OPTIONAL MATCH|WHERE|RETURN|WITH|ORDER BY|LIMIT|CALL|YIELD|CREATE|MERGE|SET|UNWIND|FOREACH|AND|OR|IN|AS|DESC|IS NULL|IS NOT NULL|CASE|WHEN|THEN|ELSE|END|collect|count|min|nodes|length)\b/g, '<span class="kw">$1</span>');
}

function suspectsTable(r) {
  const names = ["recency", "proximity", "alert_chain", "risk", "precedent", "footprint"];
  const sig = ["alert_chain", "earliness", "precedent", "proximity", "footprint", "unchanged"];
  const el = document.createElement("div");
  el.className = "section";
  el.innerHTML = `<h3>Suspects, scored by the graph</h3>
    <table class="suspects"><thead><tr><th>score</th><th>suspect</th><th>path</th></tr></thead><tbody>
    ${r.suspects.map((s, i) => {
      const keys = s.type === "change" ? names : sig;
      return `<tr class="${i === 0 ? "top" : ""}">
        <td class="score">${s.score.toFixed(2)}</td>
        <td>${s.change ? `<b>${esc(s.change.id)}</b> ${esc(s.change.kind)} on ${esc(s.service)}<br><span class="muted">${esc(s.change.summary)}</span>`
          : `<b>${esc(s.service)}</b> alerting, no change`}
          <div class="factors" style="grid-template-columns:repeat(${keys.length},1fr)">${keys.map((k) => `<span class="factor" title="${k} ${s.factors[k]}"><i style="width:${Math.round(100 * (s.factors[k] || 0))}%"></i></span>`).join("")}</div>
          <div class="flabels" style="grid-template-columns:repeat(${keys.length},1fr)">${keys.map((k) => `<span>${k.replace("_", " ")}</span>`).join("")}</div>
          <div class="fp">explains ${s.footprint.explained.length} of ${s.footprint.observed} alerting${s.footprint.in_order ? ", in order" : ", out of order"}</div>
          ${s.also_explains?.length ? `<div class="linked">also explains ${s.also_explains.map((o) => esc(o.id)).join(", ")}</div>` : ""}</td>
        <td class="mono">${esc(s.path.join(" → "))}</td></tr>`;
    }).join("")}
    </tbody></table>
    ${r.ruled_out.length ? `<p class="muted" style="font-size:12px">Off the dependency path, so ruled out: ${r.ruled_out.map((c) => `${esc(c.id)} (${esc(c.service)})`).join(", ")}</p>` : ""}`;
  return el;
}

function renderActions(inc) {
  const box = $("#actions");
  if (!box) return;
  const acts = inc.actions || [];
  const executed = acts.some((a) => a.status === "executed");
  const f = state.last?.findings;
  box.innerHTML = `
    ${acts.length ? `<div class="section"><h3>Fixes${acts.some((a) => a.status === "proposed") ? " · need your approval" : ""}</h3>
      ${acts.map((a) => `<div class="action ${esc(a.status)}">
        <div class="k">${esc(a.id)} · ${esc(a.kind)} · ${esc(a.target)}${(a.covers || []).length > 1 ? ` · covers ${a.covers.map(esc).join(", ")}` : ""}</div>
        <div class="s">${esc(a.summary)}</div>
        ${a.status === "proposed" ? `<div class="btns"><button class="primary" data-a="${esc(a.id)}" data-ok="1">Approve</button><button class="ghost" data-a="${esc(a.id)}" data-ok="0">Reject</button></div>`
          : `<div class="done">${esc(a.status)} by ${esc(a.decided_by || "?")}${a.status === "executed" ? " · written to the graph (simulated, no real systems touched)" : ""}</div>`}
      </div>`).join("")}</div>` : ""}
    ${inc.status !== "resolved" && (executed || f) ? `<div class="section resolve"><h3>Close it and teach the graph</h3>
      <textarea id="lesson">${esc(f ? (f.change_id ? `${f.change_id} on ${f.root_cause_service} caused this. ` : `Infrastructure failure on ${f.root_cause_service}. `) : "")}</textarea>
      <div class="bar"><button id="resolve" class="primary">Resolve and remember</button><span class="muted">Writes ROOT_CAUSE and a Lesson. The next similar incident cites it.</span></div></div>`
    : inc.status === "resolved" ? `<div class="section"><h3>Resolved</h3><p class="muted">This incident is in the agent's memory now.</p></div>` : ""}`;
  box.querySelectorAll("button[data-a]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    await api(`/api/${state.tenant}/actions/${b.dataset.a}`, { method: "POST", body: JSON.stringify({ approve: b.dataset.ok === "1", who: "you" }) });
    const fresh = await api(`/api/${state.tenant}/incidents/${inc.id}`);
    renderActions(fresh);
    loadIncidents();
  }));
  const rb = $("#resolve");
  if (rb) rb.addEventListener("click", async () => {
    rb.disabled = true;
    const body = { lesson: $("#lesson").value.trim() || "Resolved.", change_id: f?.change_id || null,
      cause_service: f?.change_id ? null : (f?.root_cause_service || null) };
    await api(`/api/${state.tenant}/incidents/${inc.id}/resolve`, { method: "POST", body: JSON.stringify(body) });
    const fresh = await api(`/api/${state.tenant}/incidents/${inc.id}`);
    renderActions(fresh);
    await Promise.all([loadIncidents(), loadMemory()]);
  });
}

/* ---------- ask ---------- */
async function ask(q) {
  const v = ++state.view;
  state.selected = null;
  $("#incident-list").querySelectorAll("button").forEach((b) => b.classList.remove("active"));
  $("#case").innerHTML = `<div class="case"><div class="sub">question</div><h2>${esc(q)}</h2><div class="bar"><span class="spinner"></span><span class="muted">asking the graph…</span></div></div>`;
  let out;
  try {
    out = await api(`/api/${state.tenant}/ask`, { method: "POST", body: JSON.stringify({ question: q }) });
  } catch (e) {
    if (v === state.view) $("#case").innerHTML = `<div class="case"><h2>${esc(q)}</h2><p class="muted">${esc(e.message)}</p></div>`;
    return;
  }
  if (v !== state.view) return;
  if (out.ranking) {
    $("#case").innerHTML = `<div class="case"><div class="sub">question</div><h2>${esc(q)}</h2><div id="result"></div><div id="actions"></div></div>`;
    return renderResult(out, v);
  }
  $("#case").innerHTML = `<div class="case"><div class="sub">question · ${esc(out.mode === "claude" ? out.model : "offline")}</div><h2>${esc(q)}</h2>
    <div class="answer">${esc(out.answer)}</div>
    ${out.fallback_reason ? `<div class="banner">Claude was unavailable; offline routing answered.</div>` : ""}
    ${out.steps.length ? `<div class="section"><h3>Graph queries</h3><ol class="steps">${out.steps.map((s) => `<li>${stepHtml(s)}</li>`).join("")}</ol></div>` : ""}</div>`;
  // light up whatever the answer touched
  const names = new Set();
  const edges = [];
  for (const s of out.steps) {
    const r = s.result;
    if (!r) continue;
    if (r.callers) { names.add(r.service); r.callers.forEach((c) => names.add(c.service)); }
    if (r.waves) { names.add(r.service); r.waves.forEach((w) => w.services.forEach((x) => names.add(x))); }
    if (r.service && r.team) names.add(r.service);
    if (Array.isArray(r)) r.forEach((x) => x.service && names.add(x.service));
  }
  if (state.cy) state.cy.edges().forEach((e) => { if (names.has(e.source().id()) && names.has(e.target().id())) edges.push([e.source().id(), e.target().id()]); });
  highlightServices([...names], edges);
}

/* ---------- boot ---------- */
function wire() {
  $("#case").dataset.empty = $("#case").innerHTML;
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("active", x === t));
    $("#tab-incidents").hidden = t.dataset.tab !== "incidents";
    $("#tab-memory").hidden = t.dataset.tab !== "memory";
  }));
  $("#ask").addEventListener("submit", (e) => { e.preventDefault(); const q = $("#q").value.trim(); if (q) ask(q); });
  document.querySelectorAll(".chips button").forEach((b) => b.addEventListener("click", () => { $("#q").value = b.dataset.q; ask(b.dataset.q); }));
  let t;
  window.addEventListener("resize", () => {
    clearTimeout(t);
    t = setTimeout(() => { if (state.cy) { state.cy.resize(); state.cy.fit(undefined, 30); } }, 150);
  });
  $("#reset").addEventListener("click", async () => {
    if (!confirm("Rebuild the demo graphs? Actions, investigations and resolutions you made are discarded.")) return;
    await api("/api/reset", { method: "POST" });
    await switchTenant(state.tenant);
  });
}

(async function boot() {
  wire();
  try { state.tenant = localStorage.getItem("faultline.tenant") || "kestrel"; } catch {}
  await loadHealth();
  await switchTenant(state.tenant);
})();
