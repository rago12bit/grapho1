// Left tab: what the agents remember. Solved incidents with their cause, fix and lesson, plus a full-text
// search over everything remembered (FalkorDB full-text indexes on lessons, changes, alerts and titles).
import { $, act, ago, esc, on, state, tapi } from "./util.js";
import { empty, label, loading, queries } from "./parts.js";

export async function loadMemory() {
  const box = $("#tab-memory");
  box.innerHTML = loading("Reading memory");
  const [rows, st] = await Promise.all([tapi("/memory"), tapi("/memory/stats")]);
  box.innerHTML = `
    <div class="tiles">
      ${[[st.solved, "solved", "with a root cause"], [st.lessons, "lessons", "cited by later cases"], [st.runs, "agent runs", `${st.handoffs} handoffs`]]
        .map(([n, l, d]) => `<div class="tile"><div class="n">${n}</div><div class="l"><b>${l}</b> <span class="faint">${d}</span></div></div>`).join("")}
    </div>
    <form id="msearch" class="msearch"><input id="mq" placeholder="search memory: pool, eviction, timeout…" autocomplete="off" aria-label="Search memory"><button class="btn">Search</button></form>
    <div id="mresults"></div>
    <div class="label-row">${label("Episodic · solved incidents")}<span class="label faint">${st.shapes} with a shape vector</span></div>
    <ol class="memory">
      ${rows.map((r) => `<li>
        <div class="m1"><span class="mono">${esc(r.id)}</span><span class="faint small">${ago(r.started)}</span></div>
        <div class="title">${esc(r.title)}</div>
        <div class="small"><b>${esc(r.cause_kind || "")}</b> on <button class="svc-inline" data-svc="${esc(r.cause_service || "")}">${esc(r.cause_service || "?")}</button>: ${esc(r.cause || "")}</div>
        <div class="small faint">fixed: ${esc((r.fixes || []).join("; "))}${r.approved_by ? ` · approved by ${esc(r.approved_by)}` : ""}</div>
        ${(r.lessons || []).map((l) => `<div class="lesson">${esc(l)}</div>`).join("")}
      </li>`).join("") || `<li>${empty("Nothing learned yet", "Resolve an incident and its lesson lands here.")}</li>`}
    </ol>`;
}

export function wireMemory() {
  const box = $("#tab-memory");
  box.addEventListener("submit", async (e) => {
    if (e.target.id !== "msearch") return;
    e.preventDefault();
    const q = $("#mq").value.trim();
    if (!q) return;
    $("#mresults").innerHTML = loading("Searching");
    const out = await tapi(`/memory/search?q=${encodeURIComponent(q)}`);
    $("#mresults").innerHTML = `<div class="mres">
      ${out.hits.length ? out.hits.map((h) => `<div class="prec"><div class="prec-head"><span class="label">${esc(h.kind)}</span> <span class="mono">${esc(h.ref)}</span>${h.service ? ` <span class="faint small">on ${esc(h.service)}</span>` : ""}</div><div class="small">${esc(h.text)}</div></div>`).join("")
        : `<p class="faint small">No matches for “${esc(q)}”.</p>`}
      ${queries(out.traces, 2)}</div>`;
  });
  on(box, "[data-svc]", "click", (b) => b.dataset.svc && act.showService(b.dataset.svc));
}
