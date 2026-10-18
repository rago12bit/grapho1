// Left tab: what the agents remember. Solved incidents with their cause, fix and lesson, plus a full-text
// search over everything remembered (FalkorDB full-text indexes on lessons, changes, alerts and titles).
import { $, $$, act, ago, esc, on, state, tapi } from "./util.js";
import { empty, label, loading, queries } from "./parts.js";

export async function loadMemory() {
  const box = $("#tab-memory");
  box.innerHTML = loading("Reading memory");
  const [rows, st, fixes, lrn] = await Promise.all([tapi("/memory"), tapi("/memory/stats"), tapi("/memory/fixes"), tapi("/learning")]);
  box.innerHTML = `
    <div class="tiles">
      ${[[st.solved, "solved", "with a root cause"], [st.lessons, "lessons", "cited by later cases"], [st.runs, "agent runs", `${st.handoffs} handoffs`]]
        .map(([n, l, d]) => `<div class="tile"><div class="n">${n}</div><div class="l"><b>${l}</b> <span class="faint">${d}</span></div></div>`).join("")}
    </div>
    <form id="msearch" class="msearch"><input id="mq" placeholder="search memory: pool, eviction, timeout…" autocomplete="off" aria-label="Search memory"><button class="btn">Search</button></form>
    <div id="mresults"></div>
    ${fixesHtml(fixes)}
    ${learningHtml(lrn.model)}
    <div class="label-row">${label("Agents under load")}<span class="label faint">on a copy of this graph</span></div>
    <p class="faint small">Many four-agent investigations at once, sharing one FalkorDB. Each run hands off only through its own Episode nodes; the check is that no run ever reads another's, and every run reaches the same verdict as a run on its own.</p>
    <div class="bench-btns">${[10, 25, 50].map((n) => `<button class="btn" data-bench="${n}">${n} investigations</button>`).join("")}</div>
    <div id="bench-out"></div>
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
  on(box, "#retrain", "click", async (b) => {
    b.disabled = true;
    $("#retrain-out").textContent = " fitting…";
    try {
      const r = await tapi("/learning/train", { method: "POST" });
      $("#retrain-out").textContent = r.trained ? ` done: ${r.enabled ? "in use" : "not in use"}` : ` ${r.reason}`;
      await loadMemory();
    } catch (e) { $("#retrain-out").textContent = ` ${e.message}`; b.disabled = false; }
  });
  on(box, "[data-bench]", "click", async (b) => {
    const out = $("#bench-out");
    $$("[data-bench]", box).forEach((x) => { x.disabled = true; });
    out.innerHTML = loading(`Running ${b.dataset.bench} investigations, 8 at a time`);
    try {
      const r = await tapi(`/bench?runs=${b.dataset.bench}`, { method: "POST" });
      const iso = r.cross_run_reads === 0 && r.incomplete_runs === 0, same = r.verdict_mismatches === 0;
      out.innerHTML = `<div class="bench fade-in">
        <div class="bench-big"><div class="${iso ? "ok" : "bad"}"><span class="serif">${iso ? "✓" : "✗"}</span> isolation<small>${r.cross_run_reads} crossed handoffs · ${r.incomplete_runs} incomplete runs</small></div>
          <div class="${same ? "ok" : "bad"}"><span class="serif">${same ? "✓" : "✗"}</span> consistency<small>${r.runs - r.verdict_mismatches}/${r.runs} same verdict as a single run</small></div></div>
        <div class="tiles bench-tiles">${[[r.runs_per_s, "investigations / s"], [`${r.p50_s}s`, "p50 each"], [`${r.p95_s}s`, "p95 each"]].map(([n, l]) => `<div class="tile"><div class="n">${n}</div><div class="l faint">${l}</div></div>`).join("")}</div>
        <p class="faint small">${r.runs} runs over ${r.incidents.map(esc).join(", ")} in ${r.wall_s}s · ${r.retries} queries retried because FalkorDB was busy · the copy is deleted afterwards.</p></div>`;
    } catch (e) {
      out.innerHTML = `<p class="small bad">${esc(e.message)}</p>`;
    } finally {
      $$("[data-bench]", box).forEach((x) => { x.disabled = false; });
    }
  });
}

function learningHtml(m) {
  if (!m) return `<div class="label-row">${label("Learned from outcomes")}</div><p class="faint small">Not enough solved incidents here to learn weights from (it needs at least 40 suspects and 5 known causes). The judge uses its hand-set weights.</p>`;
  const ev = m.evaluation || {}, cv = ev.cross_validation, hand = ev.hand, lrn = ev.learned;
  const keys = Object.keys(m.weights);
  const max = Math.max(...keys.map((k) => Math.abs(m.weights[k])), 0.01);
  const bars = keys.map((k) => `<div class="lw"><span class="mono tiny">${esc(k)}</span><span class="lwbar ${m.weights[k] < 0 ? "neg" : ""}"><i style="width:${Math.round(100 * Math.abs(m.weights[k]) / max)}%"></i></span><span class="num tiny">${m.weights[k] > 0 ? "+" : ""}${m.weights[k].toFixed(2)}</span></div>`).join("");
  const cal = (c) => (c?.calibration || []).map((b) => `<span class="calb" title="${b.n} cases said ${b.stated}, mean ${b.mean_stated}"><b>${Math.round(100 * b.mean_stated)}%</b> said · <b>${Math.round(100 * b.came_true)}%</b> true <span class="faint">(${b.n})</span></span>`).join("");
  return `<div class="label-row">${label("Learned from outcomes")}<span class="label ${m.enabled ? "ok" : "faint"}">${m.enabled ? "in use" : "not in use"}</span></div>
    <p class="faint small">A logistic regression over ${m.rows} silent-failure suspects from this company's solved incidents (${m.positives} were the cause), plus every “not the cause” and “confirmed” people gave. It is used only if, in cross-validation over the solved incidents, it names the cause at least as often as the hand-set weights.</p>
    <div class="lws">${bars}</div>
    ${cv ? `<p class="small">History, cross-validated: hand weights right on <b>${cv.hand_top1}</b> of ${cv.incidents}, learned on <b>${cv.learned_top1}</b>.</p>` : ""}
    ${hand && lrn ? `<p class="small">Open incidents (not used to fit or to decide): hand <b>${hand.top1}/${hand.n}</b> · learned <b>${lrn.top1}/${lrn.n}</b>; Brier score ${hand.brier} → <b>${lrn.brier}</b> (lower is better calibrated).</p>
      <div class="cals"><div><span class="label faint">hand confidence</span>${cal(hand)}</div><div><span class="label faint">learned probability</span>${cal(lrn)}</div></div>` : ""}
    ${(m.feedback || []).length ? `<div class="label-row">${label("People's verdicts")}</div><ul class="plain small">${m.feedback.map((f) => `<li><span class="mono">${esc(f.incident)}</span> ${esc(f.target)}: <b class="${f.verdict === "confirmed" ? "ok" : "warn"}">${f.verdict === "confirmed" ? "confirmed" : "not the cause"}</b> <span class="faint">by ${esc(f.by)}${f.note ? `, “${esc(f.note)}”` : ""}</span></li>`).join("")}</ul>` : ""}
    <button class="btn" id="retrain">Retrain from outcomes</button><span id="retrain-out" class="faint small"></span>`;
}

function fixesHtml(fixes) {
  if (!fixes.length) return "";
  return `<div class="label-row">${label("Procedural · fixes that worked")}<span class="label faint">verified, not assumed</span></div>
    <p class="faint small fx-sub">Counted from every approved fix and whether the alerts it was meant to clear actually cleared afterwards.</p>
    <ul class="fixes">${fixes.slice(0, 12).map((f) => {
      const n = f.worked + f.failed;
      return `<li>
        <div class="fx1"><span class="mono">${esc(f.kind)}</span> <span class="faint">on</span> <button class="svc-inline" data-svc="${esc(f.service)}">${esc(f.service)}</button>
          <span class="fx-n"><span class="ok">${f.worked} worked</span>${f.failed ? ` <span class="bad">${f.failed} failed</span>` : ""}${f.unchecked ? ` <span class="faint">${f.unchecked} unchecked</span>` : ""}</span></div>
        <div class="meter ok-meter"><i style="width:${n ? Math.round(100 * f.worked / n) : 0}%"></i></div>
        <div class="faint tiny mono">${f.history.map((h) => `${h.verified === false ? "✗" : h.verified ? "✓" : "·"} ${(h.incidents || []).map(esc).join(",")}`).join("  ")}</div>
      </li>`;
    }).join("")}</ul>`;
}
