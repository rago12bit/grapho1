// The guided demo: plays the main story on the live app, with a caption bar over the graph.
//   /?demo=1 starts it on load.
import { $, $$, act, api, sleep, state, tapi } from "./util.js";

let stop = false;
let running = false;

const STEPS = 9;

async function say(i, text, ms) {
  if (stop) throw new Error("stopped");
  const bar = $("#tour");
  bar.hidden = false;
  $("#tour-n").textContent = `${String(i).padStart(2, "0")}/${String(STEPS).padStart(2, "0")}`;
  $("#tour-text").textContent = text;
  $("#tour-progress").style.width = `${(i / STEPS) * 100}%`;
  await wait(ms);
}

async function wait(ms) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (stop) throw new Error("stopped");
    await sleep(100);
  }
}

function scrollCase(sel) {
  const el = document.querySelector(`#case ${sel}`);
  const pane = $("#case");
  if (!el || !pane) return;
  pane.scrollTo({ top: el.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 12, behavior: "smooth" });
}

export async function play() {
  if (running) { stop = true; return; }
  running = true;
  stop = false;
  $("#tour-btn").textContent = "Stop demo";
  $("#tour-btn").classList.remove("solid");
  try {
    await say(1, "Resetting the demo graphs…", 200);
    await api("/api/reset", { method: "POST" });
    await act.switchTenant("kestrel");
    act.setTab("board");
    await say(1, "Kestrel Pay: 24 services, six teams, two weeks of deploys, config and flag changes. Three incidents are open.", 4200);

    await act.openIncident("INC-301");
    await say(2, "Checkout is slow. The case file is there the moment it opens: the judge reads the graph directly. A pool limit cut on payments-db, two hops down, explains every alert in order.", 7000);
    scrollCase(".sec:nth-of-type(2)");
    await say(3, "Blast radius: who the failure reaches, by which path, which entry points face the internet, and who is on call for each.", 6000);

    $("#case").scrollTo({ top: 0, behavior: "smooth" });
    await act.runAgents({ id: "INC-301" });
    await say(4, "Four agents re-derive it through the graph: triage, investigator, skeptic, responder. They hand off only through Episode nodes, and every step shows its Cypher.", 6500);
    const d = $("#handoffs");
    if (d) { d.open = true; scrollCase("#handoffs"); }
    await wait(3500);

    scrollCase("#actions");
    await wait(1200);
    const approve = document.querySelector("#actions button[data-a][data-ok='1']");
    if (approve) approve.click();
    await say(5, "A person approves. The fix is applied to the simulated system and checked against the alerts in the graph alone. The same change caused INC-304, so one fix covers both.", 6500);

    await act.openIncident("INC-302");
    await say(6, "Mobile logins fail with seven services alerting. Weakly connected components split them into three separate groups, so three separate problems: the auth deploy explains its own.", 7000);

    await act.openIncident("INC-303");
    await say(7, "Search degraded and nothing changed. catalog-es went red first, so Faultline pages its owner instead of blaming the last deploy.", 6000);

    await act.showService("payments-db");
    await say(8, "The company as a graph: who owns payments-db, who actually knows it from the fixes they approved, chokepoints by betweenness, domains by label propagation.", 7000);

    await act.switchTenant("boutique");
    act.setTab("board");
    const first = state.board.find((i) => i.status === "open");
    if (first) await act.openIncident(first.id);
    await say(9, "Real data: 54 failures from the public RCAEval benchmark. 36 solved cases are memory, 18 are open. Each alert shows the real metric that fired. The judge names the right service first in 15 of the 18.", 9000);
    act.setTab("memory");
    await wait(4000);
  } catch {
    /* stopped */
  } finally {
    running = false;
    stop = false;
    $("#tour").hidden = true;
    $("#tour-btn").textContent = "Guided demo";
    $("#tour-btn").classList.add("solid");
  }
}

export function stopTour() { stop = true; }
