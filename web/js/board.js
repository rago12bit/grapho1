// Left tab: the risk board. Every incident, open first by severity, each with the suspect the judge leads with
// right now, so you can see what is wrong before you open anything.
import { $, $$, act, ago, esc, on, state, tapi } from "./util.js";
import { label, loading, sev } from "./parts.js";

export async function loadBoard() {
  const box = $("#tab-board");
  if (!state.board.length) box.innerHTML = loading("Ranking suspects for every open incident");
  state.board = await tapi("/board");
  render();
}

function render() {
  const rows = state.board;
  const count = (st) => rows.filter((i) => i.status === st).length;
  const sevN = (i) => Number(String(i.severity).replace(/\D/g, "")) || 9;
  const open = rows.filter((i) => i.status !== "resolved").sort((a, b) => sevN(a) - sevN(b) || (a.status === "open" ? -1 : 1) - (b.status === "open" ? -1 : 1) || b.started - a.started);
  const done = rows.filter((i) => i.status === "resolved");
  const high = open.filter((i) => i.confidence === "high").length;
  $("#tab-board").innerHTML = `
    <div class="tiles">
      ${[[count("open"), "open", "no fix yet", "hot"], [count("mitigated"), "mitigated", "fix applied"], [done.length, "resolved", "in memory"]]
        .map(([n, l, d, cls]) => `<div class="tile"><div class="n ${cls || ""}">${n}</div><div class="l"><b>${l}</b> <span class="faint">${d}</span></div></div>`).join("")}
    </div>
    <div class="label-row">${label("Open · judge's leading suspect")}<span class="label faint">${high} high confidence</span></div>
    <ul id="incident-list" class="board">
      ${open.map((i) => row(i)).join("") || `<li class="faint small pad">Nothing open.</li>`}
      ${done.length ? `<li class="divider">${label(`Resolved · ${done.length}`)}</li>` + done.slice(0, 6).map((i) => row(i)).join("") : ""}
    </ul>`;
  markBoard(state.selected);
}

function row(i) {
  return `<li><button data-id="${esc(i.id)}" class="${i.status}">
    <div class="r1">${sev(i.severity)}<span class="mono">${esc(i.id)}</span><span class="label st-${esc(i.status)}">${esc(i.status)}</span><span class="faint small when">${ago(i.started)}</span></div>
    <div class="title">${esc(i.title)}</div>
    ${i.leader ? `<div class="lead-line mono"><span class="faint">→</span> ${esc(i.leader)} <span class="conf-${esc(i.confidence)}">${esc(i.confidence)}</span>${i.explains?.length ? ` <span class="faint">· explains ${i.explains.length}</span>` : ""}</div>`
      : `<div class="lead-line mono faint">symptom on ${esc(i.symptom)}</div>`}
  </button></li>`;
}

export function markBoard(id) {
  $$("#incident-list button").forEach((b) => b.classList.toggle("active", b.dataset.id === id));
}

export function wireBoard() {
  on($("#tab-board"), "#incident-list button", "click", (b) => act.openIncident(b.dataset.id));
}
