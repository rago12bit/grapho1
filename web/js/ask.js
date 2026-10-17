// The command bar over the graph: an incident id opens its case file; anything else is a question for the
// graph, answered in a strip under the bar with every tool call and query behind it.
import { $, act, esc, plural, state, tapi } from "./util.js";
import { queries } from "./parts.js";
import * as graph from "./graph.js";

const ID = /^\s*(INC-\d+)\s*$/i;

export async function run(text) {
  const q = text.trim();
  if (!q) return;
  $("#q").value = q;
  $("#q").blur();
  const m = q.match(ID);
  if (m) { dismiss(); return act.openIncident(m[1].toUpperCase()); }
  const v = ++state.view;
  const strip = $("#answer");
  strip.hidden = false;
  strip.innerHTML = `<div class="ans-body"><span class="spinner"></span> <span class="faint">asking the graph…</span></div>`;
  $("#run").disabled = true;
  let out;
  try {
    out = await tapi("/ask", { method: "POST", body: JSON.stringify({ question: q }) });
  } catch (e) {
    if (v === state.view) strip.innerHTML = `<div class="ans-body bad">${esc(e.message)}</div>${closeBtn()}`;
    return;
  } finally {
    $("#run").disabled = false;
  }
  if (v !== state.view) return;
  if (out.ranking && out.incident_id) { dismiss(); return act.openIncident(out.incident_id); }
  const calls = out.steps || [];
  strip.innerHTML = `<div class="ans-body">
      <p class="serif ans">${esc(out.answer)}</p>
      ${out.fallback_reason ? `<p class="banner">Claude was unavailable; offline routing answered.</p>` : ""}
      <details class="how"><summary>how this was answered · ${plural(calls.length, "tool call")} · ${out.mode === "claude" ? `${esc(out.model)} chose the tools` : "offline router, no LLM"}</summary>
        <ol>${calls.map((s) => `<li><span class="mono"><b>${esc(s.tool)}</b><span class="faint">(${esc(Object.values(s.input || {}).map((x) => JSON.stringify(x)).join(", "))})</span></span>${s.note ? ` <span class="faint small">→ ${esc(s.note)}</span>` : ""}${queries(s.cypher, 2)}</li>`).join("")}</ol>
      </details>
    </div>${closeBtn()}`;
  $("#answer .x").addEventListener("click", dismiss);
  graph.highlight(touched(calls));
}

const closeBtn = () => `<button class="x" aria-label="Dismiss">×</button>`;

export function dismiss() {
  const s = $("#answer");
  s.hidden = true;
  s.innerHTML = "";
}

// Whatever services the answer's tool results name, so the graph can light them up.
function touched(steps) {
  const names = new Set();
  for (const s of steps) {
    const r = s.result;
    if (!r) continue;
    if (r.callers) { names.add(r.service); r.callers.forEach((c) => names.add(c.service)); }
    if (r.waves) { names.add(r.service); r.waves.forEach((w) => w.services.forEach((x) => names.add(x))); }
    if (r.service && r.team) names.add(r.service);
    if (Array.isArray(r)) r.forEach((x) => { if (x.service) names.add(x.service); (x.services || []).forEach((y) => names.add(y)); });
  }
  return [...names].filter(Boolean);
}

/** Example prompts for the start panel, tuned to the tenant on screen. */
export function samples() {
  const open = state.board.filter((i) => i.status === "open");
  const top = open.slice().sort((a, b) => (a.severity > b.severity ? 1 : -1))[0];
  const common = [
    { label: "Single points of failure", q: "single points of failure" },
    { label: "Failure domains (label propagation)", q: "failure domains" },
  ];
  if (state.tenant === "kestrel") return [
    { label: "Checkout is slow: which change?", q: "INC-301" },
    { label: "Mobile logins failing, seven services alerting", q: "INC-302" },
    { label: "Search degraded and nothing changed", q: "INC-303" },
    { label: "What breaks if ledger-db goes down?", q: "what breaks if ledger-db goes down?" },
    { label: "Who knows payments-db?", q: "who knows payments-db" },
    ...common,
  ];
  if (state.tenant === "boutique") return [
    ...open.slice(0, 3).map((i) => ({ label: `Real failure: ${i.title}`, q: i.id })),
    { label: "What breaks if productcatalogservice fails?", q: "what breaks if productcatalogservice goes down?" },
    { label: "Has currencyservice failed before?", q: "has currencyservice failed before?" },
    ...common,
  ];
  return [...(top ? [{ label: top.title, q: top.id }] : []), { label: "Who owns shipments?", q: "who owns shipments?" }, ...common];
}
