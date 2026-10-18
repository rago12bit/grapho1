// Ship it?: the pre-deploy check. Pick a service and a kind of change; the graph says ship, review or hold, and why.
import { $, $$, api, esc, mountShell, tenant } from "./shell.js";

mountShell();
const T = tenant();
const WORD = { ship: "Ship it", review: "Review first", hold: "Hold" };
const TRY = { kestrel: [["auth", "deploy"], ["notifications", "deploy"], ["ledger-db", "config"], ["payments-db", "config"]] };

function render(p) {
  const b = p.blast;
  const waves = b.waves.length ? b.waves.map((w) => `<div><span>wave ${w.distance}</span><span>${w.services.map(esc).join(", ")}</span></div>`).join("")
    : `<div><span>—</span><span>nothing calls ${esc(p.service)}</span></div>`;
  const ms = (p.traces || []).reduce((a, q) => a + q.ms, 0);
  $("#out").innerHTML = `
    <div class="card" style="margin-top:18px">
      <div class="pf-decision ${esc(p.decision)}"><span class="w">${WORD[p.decision]}</span><p>${esc(p.headline)}</p>
        <span class="chip">${esc(p.kind)} on ${esc(p.service)}</span></div>
    </div>
    <div class="pf-grid">
      <div class="card"><div class="card-h"><h3>Why</h3><span class="chip">${p.reasons.length} reasons · ${(p.traces || []).length} queries · ${ms.toFixed(0)} ms</span></div>
        <ul class="pf-reasons">${p.reasons.map((r) => `<li><span class="lv ${esc(r.level)}">${esc(r.level)}</span><div>${esc(r.text)}
          ${r.cites.length ? `<div class="cites">${r.cites.map(esc).join(" · ")}</div>` : ""}</div></li>`).join("")}</ul></div>
      <div class="pf-side">
        <div class="card"><div class="card-h"><h3>If it breaks</h3><span class="chip ${b.internet_exposed.length ? "warn" : ""}">${b.affected} services · ${b.internet_exposed.length} internet-facing</span></div>
          <div class="pf-waves">${waves}</div>
          ${b.journeys.length ? `<dl class="kv"><dt>Journeys</dt><dd>${b.journeys.map((j) => `${esc(j.journey)}${j.slo ? ` <span class="faint">SLO ${(100 * j.slo).toFixed(j.slo >= 0.999 ? 2 : 1)}%</span>` : ""}`).join(", ")}</dd></dl>` : ""}
          <dl class="kv"><dt>Paged</dt><dd>${b.teams_paged.map((t) => `${esc(t.team)}${t.on_call.length ? ` <span class="faint">(${t.on_call.map(esc).join(", ")})</span>` : ""}`).join(", ") || "—"}</dd>
            <dt>Centrality</dt><dd>#${p.centrality.pagerank_rank ?? "?"} of ${p.centrality.services} by PageRank${p.centrality.betweenness_rank ? `, #${p.centrality.betweenness_rank} by betweenness` : ""}</dd></dl></div>
        <div class="card"><div class="card-h"><h3>Who</h3></div>
          <dl class="kv"><dt>Owner</dt><dd>${esc(p.owner.team)}${p.owner.on_call ? ` · on call <b>${esc(p.owner.on_call.name)}</b>` : ""}</dd>
            <dt>Reviewer</dt><dd>${p.reviewers.map((r) => `${esc(r.name)}${r.fixed.length ? ` <span class="faint">fixed ${r.fixed.map(esc).join(", ")}</span>` : r.changed.length ? ` <span class="faint">made ${r.changed.slice(0, 3).map(esc).join(", ")}</span>` : ""}`).join("; ") || "—"}</dd>
            ${p.runbooks.length ? `<dt>Runbook</dt><dd>${p.runbooks.map((r) => esc(r.title)).join("; ")}</dd>` : ""}</dl></div>
      </div>
    </div>`;
}

async function check(e) {
  e?.preventDefault();
  const svc = $("#svc").value, kind = $("#kind").value, summary = $("#sum").value.trim();
  const btn = $("#go");
  btn.disabled = true;
  const u = new URL(location.href);
  u.searchParams.set("service", svc); u.searchParams.set("kind", kind);
  history.replaceState(null, "", u);
  try {
    render(await api(`/api/${encodeURIComponent(T)}/preflight?service=${encodeURIComponent(svc)}&kind=${kind}&summary=${encodeURIComponent(summary)}`));
  } catch (err) {
    $("#out").innerHTML = `<p class="empty">${esc(err.message)}</p>`;
  } finally { btn.disabled = false; }
}

(async () => {
  const q = new URLSearchParams(location.search);
  try {
    const g = await api(`/api/${encodeURIComponent(T)}/graph`);
    const names = g.nodes.map((n) => n.id).sort();
    $("#svc").innerHTML = names.map((n) => `<option>${esc(n)}</option>`).join("");
    if (q.get("service") && names.includes(q.get("service"))) $("#svc").value = q.get("service");
  } catch (err) { $("#out").innerHTML = `<p class="empty">${esc(err.message)}</p>`; return; }
  if (q.get("kind")) $("#kind").value = q.get("kind");
  const tries = TRY[T] || [];
  $("#try").innerHTML = tries.length ? `Try: ${tries.map(([s, k]) => `<button type="button" data-s="${esc(s)}" data-k="${esc(k)}">${esc(k)} on ${esc(s)}</button>`).join("")}` : "";
  $$("#try button").forEach((b) => b.onclick = () => { $("#svc").value = b.dataset.s; $("#kind").value = b.dataset.k; check(); });
  $("#form").addEventListener("submit", check);
  if (q.get("service")) check();
})();
