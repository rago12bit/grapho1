// On-call: who to page for each open incident, who is on call now and next, and the rota behind it.
import { $, $$, api, clock, esc, mountShell, tenant } from "./shell.js";

mountShell();
const T = tenant();
const ACK = `faultline.ack.${T}`;
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
const day = (t) => new Date(t * 1000).toUTCString().slice(0, 11);
const acked = () => { try { return JSON.parse(localStorage.getItem(ACK) || "[]"); } catch { return []; } };
const setAcked = (xs) => { try { localStorage.setItem(ACK, JSON.stringify(xs)); } catch {} };
let filter = "needs";

async function load() {
  let d;
  try { d = await api(`/api/${encodeURIComponent(T)}/oncall`); }
  catch (e) { $("#oc").innerHTML = `<p class="empty">Couldn't read the rota: ${esc(e.message)}</p>`; return; }
  render(d);
}

function render(d) {
  const ack = acked();
  const waiting = d.pages.filter((p) => !ack.includes(p.incident));
  const onNow = d.teams.filter((t) => t.now);
  const s = d.stats;
  const pages = d.pages.filter((p) => filter === "all" || !ack.includes(p.incident));
  $("#oc").innerHTML = `
    <div class="banner">
      <span class="bell" aria-hidden="true">◉</span>
      <div><b>${waiting.length} ${waiting.length === 1 ? "page" : "pages"} waiting for an ack</b><div class="faint mono" style="font-size:12px">${d.pages.length} open incidents · judged just now</div></div>
      <span class="who">${onNow.length ? `On call now: ${onNow.slice(0, 3).map((t) => `<b>${esc(t.now.name)}</b> (${esc(t.team)})`).join(", ")}${onNow.length > 3 ? ` +${onNow.length - 3} more` : ""}` : "No rota in this tenant's graph"}</span>
    </div>
    <div class="tiles">
      <div class="card tile"><div class="l">Open incidents</div><div class="v">${s.open}</div><div class="s">each with a team to page</div></div>
      <div class="card tile"><div class="l">Pages · 7 days</div><div class="v">${s.pages_7d ?? 0}</div><div class="s">of ${s.alerts_7d} alerts were page severity</div></div>
      <div class="card tile"><div class="l">Solved · 30 days</div><div class="v">${s.resolved_30d}</div><div class="s">closed with a root cause</div></div>
      <div class="card tile"><div class="l">Time to restore</div><div class="v">${s.mttr_min ?? "–"}<small>${s.mttr_min != null ? "min" : ""}</small></div><div class="s">mean, solved in the last 30 days</div></div>
    </div>
    <div class="oc-grid">
      <div class="card">
        <div class="card-h"><h3>Pager inbox</h3>
          <div class="seg" role="group" aria-label="Filter"><button data-f="needs" aria-pressed="${filter === "needs"}">Needs ack · ${waiting.length}</button><button data-f="all" aria-pressed="${filter === "all"}">All · ${d.pages.length}</button></div></div>
        ${pages.length ? pages.map((p) => pageCard(p, ack.includes(p.incident))).join("") : `<p class="empty">${d.pages.length ? "Everything is acknowledged." : "No open incidents."}</p>`}
      </div>
      <div class="card">
        <div class="card-h"><h3>On call now</h3><span class="chip ok">${onNow.length} of ${d.teams.length} teams covered</span></div>
        ${d.teams.map((t) => `
          <div class="now-row"><span class="av">${initials(t.now?.name)}</span>
            <div><div class="n">${esc(t.now?.name || "nobody")}</div><div class="t">${esc(t.team)} · ${t.people.length} people</div></div>
            <div class="u">${t.now ? `until ${day(t.now.to)} ${clock(t.now.to)}` : ""}${t.next ? `<br>next: ${esc(t.next.name)}` : ""}</div></div>`).join("")}
      </div>
    </div>
    <div class="card" style="margin-top:18px">
      <div class="card-h"><h3>Rota · the last six weeks</h3><span class="chip via">(:Person)-[:ON_SHIFT]->(:OnCallShift)-[:COVERS]->(:Team)</span></div>
      <div class="scroll-x">${rota(d)}</div>
    </div>`;
  $$(".seg button").forEach((b) => b.onclick = () => { filter = b.dataset.f; render(d); });
  $$("[data-ack]").forEach((b) => b.onclick = () => {
    const xs = acked(), id = b.dataset.ack;
    setAcked(xs.includes(id) ? xs.filter((x) => x !== id) : [...xs, id]);
    render(d);
  });
}

function pageCard(p, isAcked) {
  const sevHot = p.severity === "SEV1";
  return `
    <div class="page-card${isAcked ? " acked" : ""}">
      <div class="pc-top"><span class="chip${sevHot ? " hot" : " warn"}">${esc(p.severity)}</span><span class="mono faint" style="font-size:12px">${esc(p.incident)}</span>
        <span class="chip">${esc(p.confidence)} confidence</span><span class="sp"></span><span class="mono faint" style="font-size:12px">started ${clock(p.started)}</span></div>
      <div class="pc-title">${esc(p.title)}</div>
      <div class="pc-meta">noticed on ${esc(p.symptom)} · judge blames <b>${esc(p.blamed)}</b> on ${esc(p.service)} · owned by ${esc(p.team || "nobody")}</div>
      <div class="pc-who"><span class="av hot">${initials(p.page?.name)}</span>
        <div><div class="n">${p.page ? `Page ${esc(p.page.name)}` : "No one on call for that team"}</div>
          <div class="r">${esc(p.team || "")} on call now${p.was_on_call && p.page && p.was_on_call.handle !== p.page.handle ? ` · ${esc(p.was_on_call.name)} was on call when it started` : p.was_on_call ? " · also on call when it started" : ""}</div></div></div>
      <div class="pc-act"><button class="btn sm${isAcked ? "" : " primary"}" data-ack="${esc(p.incident)}">${isAcked ? "Un-ack" : "Ack"}</button>
        <a class="btn sm" href="/app?tenant=${encodeURIComponent(T)}&incident=${encodeURIComponent(p.incident)}">Open incident</a>
        <a class="btn sm" href="/replay?tenant=${encodeURIComponent(T)}&incident=${encodeURIComponent(p.incident)}&play=1">Watch the run</a></div>
      ${isAcked ? '<div class="faint mono" style="font-size:11px;margin-top:8px">acknowledged in this browser</div>' : ""}
    </div>`;
}

function rota(d) {
  const weeks = [...new Set(d.teams.flatMap((t) => t.rota.map((r) => r.from)))].sort((a, b) => a - b).slice(-6);
  if (!weeks.length) return '<p class="empty">This tenant has no on-call shifts in its graph.</p>';
  return `<table class="rota"><thead><tr><th>Team</th>${weeks.map((w) => `<th>${day(w)}</th>`).join("")}</tr></thead><tbody>
    ${d.teams.map((t) => `<tr><td><b>${esc(t.team)}</b></td>${weeks.map((w) => {
      const s = t.rota.find((r) => r.from === w);
      const cur = s && s.from <= d.now && d.now < s.to;
      return `<td class="${cur ? "cur" : ""}">${s ? esc(s.name) : "–"}</td>`;
    }).join("")}</tr>`).join("")}</tbody></table>`;
}

load();
setInterval(load, 30000);
