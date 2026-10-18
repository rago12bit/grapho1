// Break it: one change on a GRAPH.COPY of Kestrel, then the verdict before and after.
import { $, $$, api, cypher, esc, mountShell } from "./shell.js";

mountShell({ tenants: false });
let EXPS = [], cur = null;
const results = {};

const verdict = (v, cls) => `
  <div class="vs-col ${cls}"><div class="h">${cls === "after" ? "After, on the copy" : "Before"}</div>
    <div class="lead">${esc(v.leader)}</div>
    <div class="conf">${esc(v.confidence)} confidence · score ${v.score.toFixed(2)}</div>
    <div class="mini">${v.top.map((t, i) => `<div class="${i === 0 ? "first" : ""}"><span>${esc(t.label)}<span class="b"><i style="width:${Math.round(t.score * 100)}%"></i></span></span><span class="r" style="text-align:right">${t.score.toFixed(2)}</span></div>`).join("")}</div>
  </div>`;

function panel() {
  const e = cur, r = results[e.id];
  $("#panel").innerHTML = `
    <div class="card-h"><h3>Selected change</h3><span class="chip">${esc(e.incident)}</span></div>
    <div class="body">
      <h3>${esc(e.title)}</h3>
      <div class="lbl">What changes</div><p>${esc(e.what)}</p>
      <div class="lbl">What should move</div><p>${esc(e.expect)}</p>
      <div class="lbl">The write, on the copy</div>
      <pre class="code" style="white-space:pre-wrap">${cypher((r ? r.cypher : e.cypher).join("\n"))}</pre>
      <div class="go"><button class="btn primary" id="go">${r ? "Break it again" : "Break it"} <span aria-hidden="true">→</span></button>
        <span class="faint">${r ? `copied in ${r.copy_ms} ms · judged in ${r.judge_ms} ms · copy dropped` : "Runs on a GRAPH.COPY; the original graph is not touched."}</span></div>
      ${r ? `
        <div class="vs-grid">${verdict(r.before, "before")}${verdict(r.after, "after")}</div>
        <div class="moved${r.moved ? "" : " no"}">${r.leader_changed ? `The verdict moved: <b>${esc(r.before.leader)}</b> → <b>${esc(r.after.leader)}</b>, confidence ${esc(r.before.confidence)} → ${esc(r.after.confidence)}.`
          : r.moved ? `Same leader, weaker case: score ${r.before.score.toFixed(2)} → ${r.after.score.toFixed(2)}, confidence ${esc(r.before.confidence)} → ${esc(r.after.confidence)}.` : "Nothing moved."}</div>` : ""}
    </div>`;
  $("#go").onclick = () => run(e.id);
}

function list() {
  $("#list").innerHTML = EXPS.map((e) => {
    const r = results[e.id];
    return `<button class="exp" data-id="${esc(e.id)}" aria-pressed="${cur && cur.id === e.id}"><span class="dot"></span><span>
      <span class="t">${esc(e.title)}</span>
      <span class="m"><span class="chip via">${esc(e.algo)}</span><span class="chip">${esc(e.incident)}</span></span>
      ${r ? `<span class="res">${esc(r.before.leader)} → <b>${esc(r.after.leader)}</b> · ${esc(r.after.confidence)}</span>` : ""}</span></button>`;
  }).join("");
  $$(".exp").forEach((b) => b.onclick = () => { cur = EXPS.find((x) => x.id === b.dataset.id); list(); panel(); });
}

async function run(id) {
  const btn = $("#go");
  if (btn) { btn.disabled = true; btn.textContent = "Copying the graph…"; }
  try { results[id] = await api(`/api/kestrel/experiments/${encodeURIComponent(id)}/run`, { method: "POST" }); }
  catch (err) { if (btn) { btn.disabled = false; btn.textContent = "Break it"; } alert(err.message); return; }
  if (cur && cur.id === id) panel();
  list();
  summary();
}

function summary() {
  const done = EXPS.filter((e) => results[e.id]);
  if (done.length < 2) return;
  const box = $("#summary");
  box.hidden = false;
  box.innerHTML = `<div class="card-h"><h3>Every run so far</h3><span class="chip ok">${done.filter((e) => results[e.id].moved).length} of ${done.length} moved the verdict</span></div>
    <div class="scroll-x"><table class="tbl"><thead><tr><th>Change</th><th>Incident</th><th>Before</th><th>After</th><th class="r">Confidence</th><th class="r">Copy + judge</th></tr></thead><tbody>
    ${done.map((e) => { const r = results[e.id]; return `<tr><td>${esc(e.title)}</td><td class="mono">${esc(e.incident)}</td><td class="mono">${esc(r.before.leader)}</td>
      <td class="mono" style="color:${r.leader_changed ? "var(--hot)" : "var(--ink)"}">${esc(r.after.leader)}</td><td class="r">${esc(r.before.confidence)} → ${esc(r.after.confidence)}</td><td class="r">${(r.copy_ms + r.judge_ms).toFixed(0)} ms</td></tr>`; }).join("")}
    </tbody></table></div>`;
}

$("#all").onclick = async () => {
  const b = $("#all");
  b.disabled = true;
  for (const e of EXPS) { b.textContent = `Running ${EXPS.indexOf(e) + 1} of ${EXPS.length}…`; await run(e.id); }
  b.disabled = false; b.textContent = "Run all seven";
  $("#summary").scrollIntoView({ behavior: "smooth", block: "start" });
};

(async () => {
  try { EXPS = await api("/api/experiments"); }
  catch (e) { $("#list").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  cur = EXPS[0];
  list();
  panel();
})();
