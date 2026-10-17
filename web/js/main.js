// Boot: header, tabs, wiring. The other modules talk to each other through `act`, filled in here.
import { $, $$, act, api, esc, state, tapi } from "./util.js";
import * as graph from "./graph.js";
import { loadBoard, markBoard, wireBoard } from "./board.js";
import { loadMemory, wireMemory } from "./memory.js";
import { loadCompany, showService, wireCompany } from "./company.js";
import { openIncident, runAgents } from "./casefile.js";
import { dismiss, run, samples } from "./ask.js";
import { play } from "./tour.js";

const TABS = ["board", "memory", "company"];

Object.assign(act, {
  openIncident: async (id, o) => { setTab("board"); return openIncident(id, o); },
  runAgents: async (inc) => {
    if (state.selected !== inc.id) await openIncident(inc.id);
    return runAgents(await tapi(`/incidents/${inc.id}`));
  },
  showService,
  setTab,
  switchTenant,
  markBoard,
  refreshBoard: () => loadBoard(),
  refreshMemory: () => loadMemory(),
  restoreGraph: () => { if (state.last?.ranking) graph.showCase(state.last.ranking, state.last.timeline); else graph.showSystem(); },
});

function setTab(tab) {
  state.tab = tab;
  $$(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.tab === tab)));
  TABS.forEach((t) => { $(`#tab-${t}`).hidden = t !== tab; });
}

/* ---------- header ---------- */
async function health() {
  try {
    state.health = await api("/api/health");
    const wasDown = $("#db").classList.contains("down");
    $("#offline").hidden = true;
    $("#db").classList.remove("down");
    if (wasDown) loadTenants().catch(() => {});
    const p = state.health.proof;
    $("#proof").innerHTML = `${p.tests} tests · ${esc(p.planted)} planted faults · ${esc(p.real)} RCAEval`;
    const mode = $("#mode");
    mode.textContent = state.health.claude ? state.health.model : "offline playbook";
    mode.title = state.health.claude ? "Claude chooses which graph tools to call" : "No ANTHROPIC_API_KEY: a fixed playbook calls the same graph tools";
  } catch {
    $("#offline").hidden = false;
    $("#db").classList.add("down");
    $("#db-stats").textContent = "FalkorDB offline";
  }
}

async function loadTenants() {
  state.tenants = await api("/api/tenants");
  $("#tenants").innerHTML = state.tenants.map((t) =>
    `<button data-t="${esc(t.id)}" aria-current="${t.id === state.tenant}">${esc(t.title)}</button>`).join("");
  const cur = state.tenants.find((t) => t.id === state.tenant);
  const n = Object.values(cur?.nodes || {}).reduce((a, b) => a + b, 0);
  const e = Object.values(cur?.edges || {}).reduce((a, b) => a + b, 0);
  $("#db-stats").textContent = `FalkorDB · ${n.toLocaleString()} nodes · ${e.toLocaleString()} edges`;
}

async function switchTenant(t) {
  state.tenant = t;
  state.selected = null;
  state.last = null;
  state.service = null;
  state.board = [];
  state.view++;
  try { localStorage.setItem("faultline.tenant", t); } catch {}
  dismiss();
  start();
  await Promise.all([loadTenants(), graph.loadGraph()]);
  await Promise.all([loadBoard(), loadMemory(), loadCompany()]);
  start();
}

/* ---------- right panel when no case is open ---------- */
function start() {
  const box = $("#case");
  const blurb = {
    kestrel: "A synthetic payments company with planted faults: the place to watch the whole story.",
    boutique: "Real failures from the public RCAEval benchmark (Online Boutique): real call graph, real metrics, real root causes. The org chart is invented.",
    harbor: "A tiny second company. Its own graph: nothing leaks between tenants.",
  }[state.tenant] || "";
  box.innerHTML = `<div class="start fade-in">
    <span class="label">Start here</span>
    <h1 class="serif">Which change broke production, and how did the failure get there?</h1>
    <p>Faultline walks a FalkorDB graph of services, owners, deploys, config changes, alerts and solved incidents. It follows the failure from the symptom down the call graph to its cause, shows every hop and query, and proposes a fix that waits for your approval.</p>
    <p class="faint small">${esc(blurb)}</p>
    <button class="btn solid" id="start-tour">Play the guided demo</button>
    <section class="sec"><div class="label-row"><h3 class="label">Try one</h3></div>
      <ul class="try">${samples().map((s) => `<li><button data-q="${esc(s.q)}"><span>${esc(s.label)}</span><span class="mono faint">${esc(s.q)}</span></button></li>`).join("")}</ul></section>
    <section class="sec"><div class="label-row"><h3 class="label">How to read the graph</h3></div>
      <dl class="legend-dl">
        <dt><i class="k sym"></i></dt><dd>The symptom: where the incident was reported.</dd>
        <dt><i class="k sus"></i></dt><dd>The root cause the judge leads with.</dd>
        <dt><i class="k alr"></i></dt><dd>A service alerting in the incident's window.</dd>
        <dt><i class="k path"></i></dt><dd>The failure path, drawn hop by hop from symptom to cause.</dd>
        <dt><i class="k reach"></i></dt><dd>The blast radius: callers the failure reaches.</dd>
        <dt><i class="k ds"></i></dt><dd>A double border is a datastore; node size is PageRank.</dd>
      </dl></section>
  </div>`;
  $("#start-tour").addEventListener("click", play);
  $$("#case .try button").forEach((b) => b.addEventListener("click", () => run(b.dataset.q)));
  graph.showSystem();
}

function wire() {
  $("#tenants").addEventListener("click", (e) => { const b = e.target.closest("button[data-t]"); if (b && b.dataset.t !== state.tenant) switchTenant(b.dataset.t); });
  $$(".tab").forEach((t) => t.addEventListener("click", () => setTab(t.dataset.tab)));
  $("#cmd").addEventListener("submit", (e) => { e.preventDefault(); run($("#q").value); });
  $("#tour-btn").addEventListener("click", play);
  $("#tour-stop").addEventListener("click", play);
  $("#home").addEventListener("click", () => { state.selected = null; state.last = null; state.view++; markBoard(null); start(); });
  $$("#scope button").forEach((b) => b.addEventListener("click", () => graph.setScope(b.dataset.scope)));
  $("#reset").addEventListener("click", async () => {
    if (!confirm("Rebuild the demo graphs? Actions, investigations and resolutions you made are discarded.")) return;
    await api("/api/reset", { method: "POST" });
    await switchTenant(state.tenant);
  });
  window.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); $("#q").focus(); }
    if (e.key === "Escape") { $("#q").blur(); dismiss(); }
  });
  let t;
  new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => graph.fit(), 120); }).observe($("#cy"));
  wireBoard();
  wireMemory();
  wireCompany();
}

(async function boot() {
  wire();
  try { state.tenant = localStorage.getItem("faultline.tenant") || "kestrel"; } catch {}
  await health();
  setInterval(health, 5000);
  try { await switchTenant(state.tenant); } catch { await switchTenant("kestrel"); }
  if (new URLSearchParams(location.search).get("demo") === "1") setTimeout(play, 1200);
})();
