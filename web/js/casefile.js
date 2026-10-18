// The right panel: the case file for an incident. It opens with the judge's read of the graph (no agents run
// yet, so the answer is on screen at once); running the four agents re-derives it step by step, records the
// handoffs in the graph and proposes a fix for a person to approve.
import { $, $$, act, ago, api, esc, hhmm, on, plural, sleep, state, tapi } from "./util.js";
import { chain, conf, empty, label, loading, queries, section, sev, spark, status, stepRow } from "./parts.js";
import * as graph from "./graph.js";

const ROLE_NOTE = {
  triage: "what is failing, what changed, what is alerting",
  investigator: "scores suspects and simulates each one's footprint",
  skeptic: "tries to knock the leader over before anyone acts",
  responder: "owner, runbook, and the fix to approve",
};

export async function openIncident(id, { run = false } = {}) {
  const v = ++state.view;
  state.selected = id;
  state.last = null;
  act.markBoard?.(id);
  const box = $("#case");
  box.innerHTML = `<article class="cf">${label(`Case file · ${esc(id)}`)}${loading("Reading the graph")}</article>`;
  box.scrollTo({ top: 0 });
  let inc, out;
  try {
    [inc, out] = await Promise.all([tapi(`/incidents/${id}`), tapi(`/incidents/${id}/case`)]);
  } catch (e) {
    if (v === state.view) box.innerHTML = `<article class="cf">${empty(`Could not open ${id}`, e.message)}</article>`;
    return;
  }
  if (v !== state.view) return;
  state.last = out;
  render(inc, out);
  graph.showCase(out.ranking, out.timeline);
  if (run) await runAgents(inc);
}

export async function runAgents(inc) {
  const v = ++state.view;
  const btn = $("#go");
  if (btn) { btn.disabled = true; btn.textContent = "Agents working…"; }
  $("#go-note").innerHTML = `<span class="spinner"></span> ${state.health?.claude ? "Claude is walking the graph" : "triage → investigator → skeptic → responder"}`;
  let out;
  try {
    out = await streamAgents(inc, v);
  } catch (e) {
    if (v === state.view) { $("#go-note").textContent = e.message; if (btn) { btn.disabled = false; btn.textContent = "Run the four agents"; } }
    return;
  }
  if (v !== state.view) return;
  const fresh = await tapi(`/incidents/${inc.id}`);
  if (v !== state.view) return;
  state.last = out;
  render(fresh, out);
  graph.showCase(out.ranking, out.timeline, { animate: true });
  act.refreshBoard?.();
  await graph.animate(out.ranking, out.timeline, v);
}

/** Run the agents over server-sent events: each tool call shows up as it happens, then the full result. The
 *  offline playbook is fast, so the server spaces the steps out (pace_ms) for watching; it waits, it doesn't fake. */
function streamAgents(inc, v) {
  if (!window.EventSource) return tapi(`/incidents/${inc.id}/investigate`, { method: "POST" });
  const live = document.createElement("ol");
  live.className = "live-steps";
  $("#go-note").after(live);
  return new Promise((resolve, reject) => {
    const es = new EventSource(`/api/${state.tenant}/incidents/${encodeURIComponent(inc.id)}/investigate/stream?pace_ms=${state.health?.claude ? 0 : 220}`);
    let n = 0;
    es.addEventListener("step", (e) => {
      if (v !== state.view) return;
      const st = JSON.parse(e.data);
      n += 1;
      live.insertAdjacentHTML("beforeend", `<li class="fade-in"><span class="label">${esc(st.role || "")}</span> <b class="mono">${esc(st.tool)}</b>
        <span class="faint small">${esc(st.note)}</span> <span class="num faint tiny">${st.queries} queries · ${st.ms} ms</span>
        ${st.error ? `<span class="small bad">${esc(st.error)}</span>` : ""}</li>`);
      live.lastElementChild.scrollIntoView({ block: "nearest" });
      $("#go-note").innerHTML = `<span class="spinner"></span> ${plural(n, "step")} so far · ${esc(st.role || "")} is working`;
    });
    es.addEventListener("done", (e) => { es.close(); resolve(JSON.parse(e.data)); });
    es.addEventListener("error", (e) => {
      es.close();
      try { reject(new Error(JSON.parse(e.data).detail)); } catch { reject(new Error("The stream was cut off")); }
    });
  });
}

function render(inc, out) {
  const f = out.findings || {};
  const r = out.ranking;
  const top = r?.suspects?.[0];
  const ran = out.mode !== "preview";
  const cc = out.claim_check;
  const ok = cc && cc.supported === cc.total && cc.verdict_consistent;
  const word = f.change_id || f.root_cause_service || "No cause found";
  const kind = f.change_id ? "root cause" : top?.host ? "nothing changed: a failing host" : f.root_cause_service ? "nothing changed: infrastructure" : "no cause found";
  const shared = top?.change && (!f.change_id || f.change_id === top.change.id) ? (top.also_explains || []) : [];
  const steps = out.steps || [];
  const graphMs = steps.reduce((a, s) => a + (s.cypher || []).reduce((b, q) => b + q.ms, 0), 0);

  $("#case").innerHTML = `
  <article class="cf fade-in">
    <div class="cf-top">${label(`Case file · ${esc(inc.id)}${ran ? ` · ${esc(out.run_id || "")}` : ""}`)}
      <span class="cf-links"><a class="link" id="export" href="/api/${esc(state.tenant)}/incidents/${esc(inc.id)}/postmortem.md" download>${inc.status === "resolved" ? "export postmortem" : "export draft postmortem"}</a>
      <button class="link" id="replay">replay traversal</button></span></div>
    <h2 class="verdict-word ${f.change_id ? "" : f.root_cause_service ? "infra" : "none"}">${esc(word)}</h2>
    <div class="chips-row">${sev(inc.severity)} ${status(inc.status)} ${conf(r?.confidence || f.confidence, r?.confidence_p)}
      ${cc ? `<span class="label ${ok ? "ok" : "bad"}" title="Every id, service and time in the verdict was looked up in the graph">${cc.supported}/${cc.total} claims checked</span>` : ""}</div>
    <div class="kind label">${esc(kind)}</div>
    <p class="headline">${esc(f.summary || out.answer || "")}</p>
    <p class="cf-sub">${esc(inc.title)} · symptom on <b>${esc(inc.symptom)}</b> (${esc(inc.team)}) · started ${hhmm(inc.started)}, ${ago(inc.started)}</p>
    ${out.fallback_reason ? `<div class="banner">Claude was unavailable (${esc(out.fallback_reason)}); the offline playbook answered.</div>` : ""}
    ${shared.length ? `<div class="shared"><b>Shared root cause.</b> ${esc(top.change.id)} also explains ${shared.map((o) => `<button class="linkbtn" data-inc="${esc(o.id)}">${esc(o.id)}</button> ${esc(o.title)}`).join(", ")}. One fix covers ${shared.length === 1 ? "both" : "all of them"}.</div>` : ""}

    <div class="go-row">
      <button id="go" class="btn solid">${ran ? "Run the agents again" : "Run the four agents"}</button>
      <span id="go-note" class="faint">${ran
        ? `${plural(steps.length, "step")} · ${graphMs.toFixed(0)} ms in FalkorDB · ${esc(out.mode === "claude" ? out.model : "offline playbook")}`
        : `Read straight from the graph: ${out.graph?.queries ?? "?"} queries, ${out.graph?.ms ?? "?"} ms. The agents re-derive it and propose the fix.`}</span>
    </div>

    ${top ? section("Root cause", facts(r, top)) : ""}
    ${chainHtml(r, out)}
    ${impactHtml(r)}
    ${actionsShell()}
    ${timelineHtml(out.timeline, r)}
    ${r?.skeptic ? section("Skeptic · tried to knock the leader over", skeptic(r.skeptic), `<span class="label ${r.skeptic.verdict === "upheld" ? "ok" : "warn"}">${esc(r.skeptic.verdict)}</span>`, "skeptic") : ""}
    ${argumentHtml(r, out)}
    ${groupsHtml(r)}
    ${othersHtml(r)}
    ${precedentHtml(r, top)}
    ${dropped(out)}
    ${f.evidence?.length ? section("Evidence", `<ul class="evidence">${f.evidence.map((e) => `<li>${esc(e)}</li>`).join("")}</ul>`) : ""}
    ${r?.suspects?.length ? section("Suspects · scored by the graph", suspects(r)) : ""}
    ${section("Agent handoffs", ran ? `<details id="handoffs" class="handoffs"><summary>How the agents got there · ${plural(steps.length, "step")}</summary>${stepsHtml(steps)}</details>`
      : `<p class="faint small">Not run yet. Everything above is the judge reading the graph directly. Run the agents to see each tool call and its Cypher, written to the graph as Episode handoffs.</p>`,
      ran ? `<span class="num faint">${graphMs.toFixed(0)} ms in graph</span>` : "")}
  </article>`;

  $("#go").addEventListener("click", () => runAgents(inc));
  $("#replay").addEventListener("click", () => { const v = ++state.view; graph.showCase(r, out.timeline, { animate: true }); graph.animate(r, out.timeline, v); });
  $$("#case .linkbtn[data-inc]").forEach((b) => b.addEventListener("click", () => openIncident(b.dataset.inc)));
  $$("#case [data-svc]").forEach((b) => b.addEventListener("click", () => act.showService(b.dataset.svc)));
  $("#ask-why")?.addEventListener("click", (e) => askWhy(e.target.dataset.alert));
  $$("#case [data-fb]").forEach((b) => b.addEventListener("click", async (e) => {
    e.preventDefault();
    e.stopPropagation();
    const note = b.dataset.fb === "not_cause" ? (prompt(`Why is ${b.dataset.target} not the cause? (optional)`) ?? null) : "";
    if (note === null) return;
    await tapi(`/incidents/${inc.id}/feedback`, { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ target: b.dataset.target, verdict: b.dataset.fb, who: "you", note }) });
    act.refreshBoard?.();
    openIncident(inc.id);
  }));
  renderActions(inc);
}

/** Each chain of CAUSED_BY links, from the top alert down to the change (or silent failure) it started at. The
 *  chain that reaches the symptom comes first; branches stop where they join an alert already shown. */
function chains(r) {
  const links = r?.reasoning?.chain?.links || [];
  const byFrom = new Map(links.map((l) => [l.from, l]));
  const pointed = new Set(links.filter((l) => l.to_label === "Alert").map((l) => l.to));
  const walk = (l, shown) => { const out = []; while (l && !shown.has(l.from)) { shown.add(l.from); out.push(l); l = l.to_label === "Alert" ? byFrom.get(l.to) : null; } return { links: out, joins: l?.from_service || null }; };
  const sym = r?.incident?.symptom;
  const tops = links.filter((l) => !pointed.has(l.from)).sort((a, b) => (b.from_service === sym) - (a.from_service === sym) || a.from.localeCompare(b.from));
  const shown = new Set();
  return tops.map((t) => walk(t, shown)).filter((c) => c.links.length);
}

function chainHtml(r, out) {
  const cs = chains(r);
  if (!cs.length) return "";
  const alerts = new Map((r.alerts || []).map((a) => [a.id, a]));
  const top = r.suspects[0];
  const changes = new Map((r.suspects || []).filter((s) => s.change).map((s) => [s.change.id, s.change]));
  const node = (svc, id, sig, at) => `<li class="cnode"><button class="svc-inline mono" data-svc="${esc(svc)}">${esc(svc)}</button>
    <span class="small">${esc(sig || "")}</span><span class="faint mono tiny">${id ? esc(id) : ""}${at ? ` · ${hhmm(at).slice(0, 5)}` : ""}</span></li>`;
  const link = (l) => `<li class="clink ${l.confidence >= 0.7 ? "" : "weak"}"><span>↓ caused by</span>
    <span class="num faint">${l.lag_s >= 0 ? `${l.lag_s} s earlier` : `${-l.lag_s} s later (out of order)`} · ${Math.round(100 * l.confidence)}%${l.direct ? "" : ` · via ${esc(l.via.join(", ") || "silent hops")}`}</span></li>`;
  const root = (l) => l.to_label === "Change"
    ? `<li class="cnode root"><b class="mono">${esc(l.to)}</b> <span class="small">${esc(changes.get(l.to)?.summary || "change")}</span></li>`
    : `<li class="cnode root"><b class="mono">${esc(l.to)}</b> <span class="small">failed on its own: nothing changed on or under it</span></li>`;
  const body = cs.map((c, i) => {
    const rows = c.links.map((l) => { const a = alerts.get(l.from) || {}; return node(l.from_service, l.from, a.signal, a.at) + link(l); }).join("");
    const end = c.links[c.links.length - 1];
    return `<ol class="cchain ${i ? "branch" : ""}">${i ? `<li class="label faint">${end.to_label === "Alert" ? "branch" : "independent fault"}</li>` : ""}${rows}${end.to_label === "Alert"
      ? `<li class="cnode"><span class="faint small">joins the chain at</span> <span class="mono">${esc(c.joins || end.to_service)}</span></li>` : root(end)}</ol>`;
  }).join("");
  const n = r.reasoning.chain.links.length;
  const stored = out.reasoning_written;
  const symAlert = cs[0].links[0];
  return section(`Causal chain · how ${esc(top.service)} reached ${esc(r.incident.symptom)}`, `${body}
    <p class="faint small">Topology decides which links are possible (a failure climbs from callee to caller); alert timing picks among them and sets each link's confidence.</p>
    ${stored ? `<p class="small">Stored as ${plural(stored.causal_links, "CAUSED_BY edge")}. <button class="link" id="ask-why" data-alert="${esc(symAlert.from)}">Ask the graph why ${esc(symAlert.from)} fired</button></p><div id="why-out"></div>`
      : `<p class="faint small">${plural(n, "link")} inferred. Running the agents writes them to the graph as CAUSED_BY edges you can query.</p>`}`, "", "causal");
}

async function askWhy(id) {
  const box = $("#why-out");
  box.innerHTML = loading("Walking CAUSED_BY");
  try {
    const w = await tapi(`/alerts/${encodeURIComponent(id)}/why`);
    box.innerHTML = `<p class="small">${w.chain.map((x) => `${esc(x.alert)} on <b>${esc(x.service)}</b>`).join(" ← ")} ← <b>${esc(w.root?.id || "?")}</b> ${w.root?.summary ? `<span class="faint">${esc(w.root.summary)}</span>` : ""}</p>${queries(w.traces, 2)}`;
  } catch (e) { box.innerHTML = `<p class="small warn">${esc(e.message)}</p>`; }
}

/** The argument: every hypothesis the judge scored, with what backs it and what undercuts it. */
function argumentHtml(r, out) {
  const hs = r?.reasoning?.hypotheses || [];
  if (!hs.length) return "";
  const ev = (e, ok) => `<li class="${ok ? "ok" : "no"}"><span>${ok ? "✓" : "✗"}</span><div>${esc(e.text)}${e.weight != null ? ` <span class="num faint">+${e.weight.toFixed(2)}</span>` : ""}${e.cites?.length ? `<div class="mono tiny faint">cites ${e.cites.slice(0, 6).map((c) => esc(c.id)).join(", ")}${e.cites.length > 6 ? ` +${e.cites.length - 6}` : ""}</div>` : ""}</div></li>`;
  const card = (h) => {
    const items = h.status === "standing" ? [...h.supports.map((e) => ev(e, true)), ...h.contradicts.map((e) => ev(e, false))]
      : [...h.contradicts.map((e) => ev(e, false)), ...h.supports.map((e) => ev(e, true))];
    const head = `<span class="label hs-${esc(h.status.replace(" ", "-"))}">${esc(h.status)}</span> <b class="mono">${esc(h.label)}</b> <span class="faint small">${esc(h.service)}</span>
      <span class="num">${h.score.toFixed(2)}</span><span class="faint small">${h.supports.length} for · ${h.contradicts.length} against</span>
      <span class="fb"><button class="link" data-fb="not_cause" data-target="${esc(h.label)}" title="Rule this out for this incident; the model learns from it">not the cause</button>
      <button class="link" data-fb="confirmed" data-target="${esc(h.label)}">confirm</button></span>`;
    return h.status === "standing" ? `<div class="hyp standing"><div class="hyp-head">${head}</div><ul class="checks">${items.join("")}</ul></div>`
      : `<details class="hyp"><summary class="hyp-head">${head}</summary><ul class="checks">${items.join("")}</ul></details>`;
  };
  const w = out.reasoning_written;
  return section(`The argument · ${plural(hs.length, "hypothesis", "hypotheses")}`, hs.map(card).join("")
    + `<p class="faint small">The skeptic's checks run against every hypothesis, so each rejected one says why it lost. ${w
      ? `Stored as ${w.hypotheses} Hypothesis and ${w.evidence} Evidence nodes with ${w.citations} CITES edges.` : "Running the agents stores it in the graph."}</p>`, "", "argument");
}

function facts(r, top) {
  const c = top.change;
  const owner = top.owner || {};
  const act_ = top.recommended_action || {};
  const rows = [
    ["Reached via", chain(top.path, true)],
    top.host ? ["Shared host", `<b>${esc(top.host.node)}</b> (${esc(top.host.zone)}) runs ${top.host.services.map((x) => `<button class="svc-inline" data-svc="${esc(x)}">${esc(x)}</button>`).join(" and ")}, which failed ${top.host.spread_min} min apart <span class="faint">with nothing else in common</span>`]
    : [c ? "Change" : "Failed first", c ? `<b>${esc(c.id)}</b> · ${esc(c.kind)} · ${esc(c.summary)}${c.author ? ` <span class="faint">by ${esc(c.author)}, ${top.minutes_before} min before</span>` : ""}`
      : `<b>${esc(top.service)}</b> <span class="faint">alerted ${top.minutes_before} min before the incident, no change on or under it</span>`],
    ["Spread", top.footprint.sequence.map((x) => `${esc(x.service)} <span class="faint">${hhmm(x.at).slice(0, 5)}</span>`).join(" → ") + (top.footprint.in_order ? ` <span class="ok">in order</span>` : ` <span class="warn">out of order</span>`)],
    ["Explains", `${top.footprint.explained.length} of ${top.footprint.observed} alerting services`],
    ["Fix", esc(act_.summary || "") + (act_.track_record ? `<div class="small ${act_.track_record.failed ? "warn" : "ok"}">${esc(act_.track_record.note)}</div>` : "")],
    ["Owners", `${esc(owner.team || "")}${owner.on_call ? ` · on call <b>${esc(owner.on_call.name)}</b>` : ""}`],
    ["Score", `<span class="num">${top.score.toFixed(2)}</span> · margin ${r.margin != null ? r.margin.toFixed(2) : "?"} over #2`],
  ];
  return `<dl class="facts">${rows.map(([k, v]) => `<dt class="label">${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
}

function impactHtml(r) {
  const im = r?.impact;
  if (!im) return "";
  if (!im.blocks.length) return section(`Blast radius · ${esc(im.service)}`, `<p class="faint small">Nothing calls ${esc(im.service)}, so a failure there reaches no other service.</p>`);
  const blocks = im.blocks.map((b) => `
    <div class="block">
      <div class="block-head"><button class="svc serif" data-svc="${esc(b.service)}">${esc(b.service)}</button>
        ${b.exposed ? `<span class="label warn">▲ internet-facing</span>` : ""}
        ${b.alerting ? `<span class="label hotl">● alerting now</span>` : `<span class="label faint">quiet</span>`}</div>
      <dl class="facts tight">
        <dt class="label">Reached via</dt><dd>${chain(b.path, false)}</dd>
        <dt class="label">Distance</dt><dd>${plural(b.hops, "hop")} above ${esc(im.service)} · tier ${esc(b.tier)}</dd>
        <dt class="label">Owners</dt><dd>${esc(b.team)}${b.on_call ? ` · on call ${esc(b.on_call)}` : ""}${b.on_call_then && b.on_call_then !== b.on_call ? ` <span class="faint">(${esc(b.on_call_then)} was on call when it started)</span>` : ""}</dd>
        <dt class="label">Callers</dt><dd>${b.callers} upstream</dd>
      </dl>
    </div>`).join("");
  return journeysHtml(im) + section(`Blast radius · ${plural(im.affected, "service")} reached`, blocks,
    `<span class="label ${im.exposed ? "warn" : "faint"}">${im.exposed} internet-facing</span>`);
}

function journeysHtml(im) {
  const js = im.journeys || [];
  if (!js.length) return "";
  return section(`User journeys hit · ${js.length}`, `<ul class="journeys">${js.slice(0, 8).map((j) => `<li>
      <div><b>${esc(j.journey)}</b> <span class="num faint">${j.rps} req/s</span>${j.slo ? ` <span class="label faint">SLO ${(100 * j.slo).toFixed(j.slo >= 0.999 ? 2 : 1)}%</span>` : ""}</div>
      ${j.burned_share != null ? `<div class="meter ${j.burned_share >= 1 ? "bad-meter" : j.burned_share >= 0.5 ? "warn-meter" : "ok-meter"}"><i style="width:${Math.min(100, Math.round(100 * j.burned_share))}%"></i></div>
        <div class="tiny faint">${j.burned_min} of ${j.budget_min} min of the 30-day error budget, if every request failed since it started</div>` : `<div class="tiny faint">needs ${esc(j.hit.join(", "))} · ${j.uses} services in all${j.source === "traces" ? " · from the traces" : ""}</div>`}
    </li>`).join("")}</ul>`, `<span class="label faint">${esc(im.service)} failing</span>`);
}

function timelineHtml(t, r) {
  if (!t || (!t.alerts.length && !t.changes.length)) return "";
  const lead = r?.suspects?.[0];
  const items = [
    ...t.changes.map((c) => ({ at: c.at, html: `<span class="tl-kind label">${esc(c.kind)}</span><span><b>${esc(c.id)}</b> ${esc(c.summary)}</span>`, cls: lead?.change?.id === c.id ? "lead" : "chg" })),
    ...t.alerts.map((a) => ({ at: a.at, html: `<span class="tl-kind label">alert</span><span><button class="svc-inline" data-svc="${esc(a.service)}">${esc(a.service)}</button> ${esc(a.signal)}${a.cleared_at ? ` <span class="ok small">cleared ${hhmm(a.cleared_at).slice(0, 5)}</span>` : ""}</span>${a.series ? spark(a.series, { onset: a.offset }) : ""}`, cls: lead?.service === a.service ? "lead" : "" })),
  ].sort((a, b) => a.at - b.at);
  const real = t.alerts.some((a) => a.series);
  return section(`Timeline · ${plural(t.alerts.length, "alert")}, ${plural(t.changes.length, "change")}`,
    `<ol class="timeline">${items.map((x) => `<li class="${x.cls}"><span class="num">${hhmm(x.at).slice(0, 5)}</span>${x.html}</li>`).join("")}</ol>
     ${real ? `<p class="faint small">Real telemetry from RCAEval. Each line is the robust z-score of the metric that fired; dashed: the detector's threshold (z = 5 for 30 s); faint line: when the fault was injected.</p>` : ""}`);
}

function skeptic(sk) {
  if (!sk.rounds?.length) return `<p class="faint small">Nothing to challenge: no suspect explains these alerts yet.</p>`;
  const last = sk.rounds[sk.rounds.length - 1];
  const handed = sk.rounds.filter((x) => x.handed_to).map((x) => `${esc(x.leader)} → ${esc(x.handed_to)}`);
  return `${handed.length ? `<p class="small">Handed over: ${handed.join(", ")}</p>` : ""}
    <ul class="checks">${last.checks.map((c) => `<li class="${c.passed ? "ok" : "no"}"><span>${c.passed ? "✓" : "✗"}</span><div>${esc(c.check)}<div class="faint small">${esc(c.detail)}</div></div></li>`).join("")}</ul>`;
}

function groupsHtml(r) {
  const gs = r?.alert_groups || [];
  if (gs.length < 2) return "";
  return section(`Alert groups · weakly connected components`, `<p class="small">${gs.length} separate groups, so ${gs.length} separate problems.</p>
    ${gs.map((g, i) => `<div class="group"><span class="label">group ${i + 1}</span> ${chain(g.services)} <span class="faint small">first: ${esc(g.first_to_alert)}</span></div>`).join("")}`, "", "groups");
}

function othersHtml(r) {
  if (!r?.other_faults?.length) return "";
  return section(`Other faults active below ${esc(r.incident.symptom)}`, r.other_faults.map((o) =>
    `<div class="other"><b>${esc((o.change || {}).id || o.service)}</b>${o.change ? "" : " (no change)"} explains ${esc(o.explains.join(", "))}${o.incidents.length ? ` · ${o.incidents.map((i) => `<button class="linkbtn" data-inc="${esc(i.id)}">${esc(i.id)}</button>`).join(" ")}` : ""}</div>`).join(""), "", "others");
}

function precedentHtml(r, top) {
  const prec = top?.precedents || [];
  const hits = (r?.memory_hits || []).filter((h) => h.kind === "lesson" || h.kind === "incident").slice(0, 3);
  const experts = (r?.experts || []).slice(0, 3);
  if (!prec.length && !hits.length && !experts.length) return "";
  return section("Memory · precedent", `
    ${prec.map((p) => `<div class="prec"><div class="prec-head"><button class="linkbtn mono">${esc(p.incident)}</button> <b>${esc(p.cause_kind)}</b> on ${esc(top.service)} <span class="faint">${ago(p.started)}</span></div>
      <div class="small">${esc(p.cause)} · fixed: ${esc(p.fix_summary || p.fix || "")}</div>${p.lesson ? `<div class="lesson">${esc(p.lesson)}</div>` : ""}</div>`).join("")}
    ${hits.map((h) => `<div class="prec"><div class="prec-head"><span class="label">full-text</span> ${esc(h.ref)}</div><div class="small">${esc(h.text)}</div></div>`).join("")}
    ${experts.length ? `<div class="experts"><span class="label">who knows ${esc(top?.service || "")}</span> ${experts.map((e) => `<span>${esc(e.name)} <span class="faint num">${e.score}</span></span>`).join(" · ")}</div>` : ""}`);
}

function dropped(out) {
  const cc = out.claim_check;
  if (!cc || (cc.supported === cc.total && cc.verdict_consistent)) return "";
  return `<div class="dropped"><b>Not backed by the graph${out.mode === "claude" ? " (dropped from the evidence)" : ""}</b>${cc.claims.filter((c) => !c.ok).map((c) => `<div>“${esc(c.text)}”: ${esc(c.problems.join("; "))}</div>`).join("")}${cc.verdict_problems.map((p) => `<div>${esc(p)}</div>`).join("")}</div>`;
}

function suspects(r) {
  const names = ["recency", "proximity", "alert_chain", "risk", "precedent", "footprint"];
  const sig = ["alert_chain", "earliness", "precedent", "proximity", "footprint", "unchanged"];
  return `<ol class="suspects">${r.suspects.map((s, i) => {
    const keys = s.type === "change" ? names : sig;
    return `<li class="sus${i === 0 ? " lead" : ""}">
      <div class="sus-head"><span class="score num">${s.score.toFixed(2)}</span>
        ${s.change ? `<span><b>${esc(s.change.id)}</b> ${esc(s.change.kind)} on ${esc(s.service)}</span>` : `<span><b>${esc(s.service)}</b> alerting, no change</span>`}</div>
      ${s.change ? `<div class="faint sus-sum">${esc(s.change.summary)}</div>` : ""}
      <div class="bars">${keys.map((k) => `<div class="fbar" title="${k} ${s.factors[k]}"><span class="factor"><i style="width:${Math.round(100 * (s.factors[k] || 0))}%"></i></span><span class="blabel">${k.replace("_", " ")}</span></div>`).join("")}</div>
      <div class="fp">explains ${s.footprint.explained.length} of ${s.footprint.observed} alerting${s.footprint.in_order ? ", in order" : ", out of order"} · ${chain(s.path)}</div>
      ${s.also_explains?.length ? `<div class="linked">also explains ${s.also_explains.map((o) => esc(o.id)).join(", ")}</div>` : ""}
    </li>`;
  }).join("")}</ol>
  ${r.ruled_out.length ? `<p class="faint small">Off the dependency path, so ruled out: ${r.ruled_out.map((c) => `${esc(c.id)} (${esc(c.service)})`).join(", ")}</p>` : ""}`;
}

function stepsHtml(steps) {
  let role = null, i = 0;
  const out = [];
  for (const s of steps) {
    if (s.role && s.role !== role) {
      role = s.role;
      out.push(`<li class="rolehead"><span class="role ${esc(role)}">${esc(role)} agent</span><span class="faint small">${ROLE_NOTE[role] || ""}</span></li>`);
    }
    out.push(stepRow(s, i++));
  }
  return `<ol class="steps" id="steps">${out.join("")}</ol>`;
}

/* ---------- fixes: proposals wait for a person ---------- */
function actionsShell() {
  return `<div id="actions"></div>`;
}

export function renderActions(inc) {
  const box = $("#actions");
  if (!box) return;
  const acts = inc.actions || [];
  const executed = acts.some((a) => a.status === "executed");
  const f = state.last?.mode !== "preview" ? state.last?.findings : null;
  const pending = acts.some((a) => a.status === "proposed");
  box.innerHTML = `
    ${acts.length ? section("Proposed fixes", acts.map((a) => `<div class="action ${esc(a.status)}">
        <div class="ak label">${esc(a.id)} · ${esc(a.kind)} · ${esc(a.target)}${(a.covers || []).length > 1 ? ` · covers ${a.covers.map(esc).join(", ")}` : ""}</div>
        <div class="s">${esc(a.summary)}</div>${a.status === "proposed" ? recordFor(a) : ""}
        ${a.status === "proposed" ? `<div class="btns"><button class="btn solid hot" data-a="${esc(a.id)}" data-ok="1">Approve fix</button><button class="btn quiet" data-a="${esc(a.id)}" data-ok="0">Reject</button></div>
            <p class="faint small">Approving applies it to the simulated system only. Then Faultline checks the alerts in the graph to see whether it worked.</p>`
          : `<div class="done">${esc(a.status)} by ${esc(a.decided_by || "?")}${a.status === "executed" ? " · applied to the simulated system" : ""}</div>
            ${a.verified === true ? `<div class="verify ok">✓ Verified: ${esc(a.verification)}</div>` : a.verified === false ? `<div class="verify no">✗ Did not work: ${esc(a.verification)}. The incident is open again.</div>` : a.verification ? `<div class="verify wait">… ${esc(a.verification)}</div>` : ""}`}
      </div>`).join(""), pending ? `<span class="label warn">needs your approval</span>` : "")
      : inc.status !== "resolved" ? section("Proposed fixes", `<p class="faint small">The responder agent proposes the fix. Run the agents to get one.</p>`) : ""}
    ${inc.status !== "resolved" && (executed || f) ? section("Close it and teach the graph", `
      <textarea id="lesson">${esc(f ? (f.change_id ? `${f.change_id} on ${f.root_cause_service} caused this. ` : `Infrastructure failure on ${f.root_cause_service}. `) : "")}</textarea>
      <div class="go-row"><button id="resolve" class="btn solid">Resolve and remember</button><span class="faint small">Writes ROOT_CAUSE and a Lesson. The next similar incident cites it.</span></div>`)
    : inc.status === "resolved" ? section("Resolved", `<p class="faint small">This incident is in the agents' memory now.</p>`) : ""}`;
  $$("button[data-a]", box).forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    await tapi(`/actions/${b.dataset.a}`, { method: "POST", body: JSON.stringify({ approve: b.dataset.ok === "1", who: "you" }) });
    renderActions(await tapi(`/incidents/${inc.id}`));
    act.refreshBoard?.();
    act.refreshMemory?.();
  }));
  const rb = $("#resolve");
  if (rb) rb.addEventListener("click", async () => {
    rb.disabled = true;
    // The cause: this view's verdict, else the change the executed fix reverted, else the last investigation's.
    const fixed = acts.find((a) => a.status === "executed" && a.change_id);
    const last = inc.investigations?.[0];
    const changeId = f?.change_id || fixed?.change_id || last?.top_change || null;
    const body = { lesson: $("#lesson").value.trim() || "Resolved.", change_id: changeId,
      cause_service: changeId ? null : (f?.root_cause_service || last?.top_service || null) };
    await tapi(`/incidents/${inc.id}/resolve`, { method: "POST", body: JSON.stringify(body) });
    renderActions(await tapi(`/incidents/${inc.id}`));
    act.refreshBoard?.();
    act.refreshMemory?.();
  });
}

function recordFor(a) {
  const rec = (state.last?.ranking?.suspects || []).map((s) => s.recommended_action).find((r) => r && r.kind === a.kind && r.target === a.target)?.track_record;
  return rec ? `<div class="small ${rec.failed ? "warn" : "ok"} rec">${esc(rec.note)}</div>` : "";
}
