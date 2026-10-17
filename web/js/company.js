// Left tab: the company as the graph sees it. Teams and on-call, chokepoints (betweenness), failure domains
// (label propagation), and a card per service: owners, who actually knows it, callers, changes, history.
import { $, act, ago, esc, on, plural, state, tapi } from "./util.js";
import { chain, empty, label, loading, queries, section } from "./parts.js";
import * as graph from "./graph.js";

let org = null;

export async function loadCompany() {
  org = null;
  state.service = null;
  renderShell();
  org = await tapi("/company");
  renderShell();
}

function services() {
  return [...(state.graph?.nodes || [])].sort((a, b) => b.pagerank - a.pagerank);
}

function renderShell() {
  const box = $("#tab-company");
  box.innerHTML = `
    <div class="label-row">${label("Services · by PageRank")}</div>
    <div class="svc-cloud">${services().map((s) => `<button data-svc="${esc(s.id)}" class="${state.service === s.id ? "on" : ""}">${esc(s.id)}</button>`).join("")}</div>
    <div id="company-body"></div>`;
  if (state.service) return card(state.service);
  const body = $("#company-body");
  if (!org) { body.innerHTML = loading("Querying the graph"); return; }
  const maxB = Math.max(...org.chokepoints.map((c) => c.betweenness), 1);
  body.innerHTML = `
    ${section("Teams · on call now", `<ul class="teams">${org.teams.map((t) => `<li><button data-team="${esc(t.team)}">
      <div class="t1"><b>${esc(t.team)}</b><span class="faint small">${plural(t.people, "person", "people")}</span></div>
      <div class="small">on call: ${t.on_call.map(esc).join(", ") || "<span class='faint'>nobody</span>"}</div>
      <div class="mono tiny faint">${t.services.map(esc).join(" · ")}</div></button></li>`).join("")}</ul>`)}
    ${section("Chokepoints · betweenness", `<ul class="choke">${org.chokepoints.filter((c) => c.betweenness > 0).map((c) => `<li><button data-svc="${esc(c.service)}">
      <span class="mono">${esc(c.service)}</span><span class="meter"><i style="width:${Math.round(100 * c.betweenness / maxB)}%"></i></span><span class="num faint">${c.betweenness}</span></button></li>`).join("")}</ul>
      <p class="faint small">The share of call paths that run through a service. A fault there cuts the most traffic.</p>`)}
    ${section("Failure domains · label propagation", `${org.domains.map((d) => `<div class="group"><span class="label">domain ${d.domain}</span> ${d.services.map(esc).join(", ")} <span class="faint small">· ${d.teams.map(esc).join(", ")}</span></div>`).join("")}`)}
    ${section("Graph queries behind this page", queries(org.traces, 4))}`;
}

export async function showService(name) {
  state.service = name;
  act.setTab("company");
  renderShell();
}

async function card(name) {
  const body = $("#company-body");
  body.innerHTML = loading(`Reading ${name}`);
  graph.highlight([name], { select: name });
  let c;
  try { c = await tapi(`/services/${encodeURIComponent(name)}`); } catch (e) { body.innerHTML = empty(`No service ${name}`, e.message); return; }
  if (state.service !== name) return;
  graph.highlight([name, ...c.callers.map((x) => x.service), ...c.calls.map((x) => x.service)], { select: name });
  const b = c.betweenness;
  body.innerHTML = `<div class="fade-in scard">
    <button class="link" id="back">← all teams</button>
    <h3 class="serif svc-title">${esc(c.service)}</h3>
    ${c.exposed_via.length ? `<p class="mono small warn">▲ reachable from the internet via ${c.exposed_via.map(esc).join(", ")}</p>` : ""}
    ${c.alerting_now ? `<p class="mono small hotl">● ${plural(c.alerting_now, "alert")} open in the last 2 h</p>` : ""}
    <dl class="facts tight">
      <dt class="label">Team</dt><dd>${esc(c.team)}${c.on_call ? ` · on call <b>${esc(c.on_call.name)}</b>` : ""}</dd>
      <dt class="label">Calls</dt><dd>${c.calls.length ? c.calls.map((x) => `<button class="svc-inline" data-svc="${esc(x.service)}">${esc(x.service)}</button>`).join(", ") : "<span class='faint'>nothing</span>"}</dd>
      <dt class="label">Called by</dt><dd>${c.callers.length ? `${plural(c.callers.length, "service")} upstream` : "<span class='faint'>nobody</span>"}</dd>
      <dt class="label">PageRank</dt><dd>#${c.rank} <span class="faint num">${c.pagerank}</span></dd>
      <dt class="label">Betweenness</dt><dd>${b ? `#${b.rank} <span class="faint num">${b.betweenness}</span>` : "–"}</dd>
      <dt class="label">Domain</dt><dd>${c.domain ? `${c.domain.services.length} services with ${c.domain.teams.map(esc).join(", ")}` : "–"}</dd>
    </dl>
    ${section("Who knows it · from what they did", c.experts.length ? `<ul class="experts-list">${c.experts.map((e) => `<li><div><b>${esc(e.name)}</b>${e.on_call ? ` <span class="label hotl">on call</span>` : ""}${e.owner_team ? ` <span class="label faint">owner team</span>` : ""}</div>
      <div class="faint small">${[e.fixed.length ? `approved fixes for ${e.fixed.map(esc).join(", ")}` : "", e.changed.length ? `made ${e.changed.map(esc).join(", ")}` : "",
        e.commits ? `${e.commits} commits to its code, last ${esc((e.last_commit || "").slice(0, 10))}` : ""].filter(Boolean).join(" · ")}</div><span class="num">${e.score}</span></li>`).join("")}</ul>
      ${c.bot_commits?.n ? `<p class="faint small">Plus ${c.bot_commits.n} commits by automation (${(c.bot_commits.b || []).map(esc).join(", ")}), kept apart.</p>` : ""}`
      : `<p class="faint small">Nobody on record.</p>`)}
    ${c.endpoints?.length ? section(`Endpoints · from the traces`, `<ul class="eps">${c.endpoints.map((o) => `<li><div class="mono small">${esc(o.op)}</div>
      <div class="num faint tiny">${o.rps} req/s · p50 ${o.p50_ms} ms · p99 ${o.p99_ms} ms${o.err ? ` · ${Math.round(100 * o.err)}% errors` : ""}</div>
      ${o.calls.length ? `<div class="tiny faint">→ ${o.calls.map((d) => `<span class="mono">${esc(d.service)}</span> ${esc(d.op)} <span class="num">${d.rps}/s</span>`).join("; ")}</div>` : ""}</li>`).join("")}</ul>`) : ""}
    ${c.log_patterns?.length ? section(`Error logs seen in incidents`, `<ul class="eps">${c.log_patterns.map((l) => `<li><div class="mono small">${esc(l.template)}</div>
      <div class="faint tiny">${l.new ? "new at the incident · " : ""}${plural(l.incidents, "incident")} · ${l.lines} lines · ${l.ids.map(esc).join(", ")}</div></li>`).join("")}</ul>`) : ""}
    ${section("Recent changes", c.changes.length ? `<ul class="plain">${c.changes.map((x) => `<li><span class="mono">${esc(x.id)}</span> <span class="label">${esc(x.kind)}</span> ${esc(x.summary)} <span class="faint small">${x.author ? `by ${esc(x.author)}, ` : ""}${ago(x.at)}</span></li>`).join("")}</ul>` : `<p class="faint small">No deploys, config or flag changes on record.</p>`)}
    ${section("Past incidents rooted here", c.history.length ? c.history.slice(0, 5).map((h) => `<div class="prec"><div class="prec-head"><span class="mono">${esc(h.incident)}</span> <b>${esc(h.cause_kind)}</b> <span class="faint small">${ago(h.started)}</span></div><div class="small">${esc(h.cause)}</div>${h.lesson ? `<div class="lesson">${esc(h.lesson)}</div>` : ""}</div>`).join("") + (c.history.length > 5 ? `<p class="faint small">+ ${c.history.length - 5} more</p>` : "") : `<p class="faint small">None.</p>`)}
    ${c.runbooks.length ? section("Runbooks", c.runbooks.map((r) => `<details class="rb"><summary><span class="mono">${esc(r.id)}</span> ${esc(r.title)}</summary><ol class="small">${(r.steps || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ol></details>`).join("")) : ""}
    ${section("Graph queries behind this card", queries(c.traces, 5))}
  </div>`;
}

export function wireCompany() {
  const box = $("#tab-company");
  on(box, "[data-svc]", "click", (b) => showService(b.dataset.svc));
  on(box, "#back", "click", () => { state.service = null; graph.clear(); renderShell(); act.restoreGraph?.(); });
  on(box, "[data-team]", "click", (b) => {
    const t = org?.teams.find((x) => x.team === b.dataset.team);
    if (t) graph.highlight(t.services);
  });
}
