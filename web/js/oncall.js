// On-call: who to page for each open incident, who is on call now and next, and the rota behind it.
import { $, $$, api, clock, esc, mountShell, tenant } from "./shell.js";

mountShell();
const T = tenant();
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();
const day = (t) => new Date(t * 1000).toUTCString().slice(0, 11);
const post = (path, body) => api(`/api/${encodeURIComponent(T)}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body || {}) });
const mmss = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
let filter = "needs", services = [], last = null;
const KIND = { rollback: "roll back", revert_config: "revert config", disable_flag: "turn off a flag", page_owner: "page the owner" };

async function load() {
  let d;
  try {
    await post("/pages/tick").catch(() => {});      // escalate anything left unanswered too long
    d = await api(`/api/${encodeURIComponent(T)}/oncall`);
  } catch (e) { $("#oc").innerHTML = `<p class="empty">Couldn't read the rota: ${esc(e.message)}</p>`; return; }
  if (!services.length) { try { services = (await api(`/api/${encodeURIComponent(T)}/graph`)).nodes.map((n) => n.id).sort(); } catch {} }
  last = d;
  render(d);
}

const settled = (pg) => pg && ["acked", "notice"].includes(pg.current.status);

function render(d) {
  const chain = Object.fromEntries((d.paging || []).map((x) => [x.incident, x]));
  const waiting = d.pages.filter((p) => !settled(chain[p.incident]));
  const onNow = d.teams.filter((t) => t.now);
  const s = d.stats;
  const pages = d.pages.filter((p) => filter === "all" || !settled(chain[p.incident]));
  $("#oc").innerHTML = `
    <div class="banner">
      <span class="bell" aria-hidden="true">◉</span>
      <div><b>${waiting.length} ${waiting.length === 1 ? "incident" : "incidents"} waiting for someone to take it</b><div class="faint mono" style="font-size:12px">${d.pages.length} open · unanswered pages escalate after ${Math.round(d.escalate_s / 60)} min · kept in the graph</div></div>
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
        <div class="card-h"><h3>Pager</h3>
          <div class="seg" role="group" aria-label="Filter"><button data-f="needs" aria-pressed="${filter === "needs"}">Needs someone · ${waiting.length}</button><button data-f="all" aria-pressed="${filter === "all"}">All · ${d.pages.length}</button></div></div>
        ${pages.length ? pages.map((p) => pageCard(p, chain[p.incident])).join("") : `<p class="empty">${d.pages.length ? "Every open incident has someone on it." : "No open incidents."}</p>`}
      </div>
      <div class="card">
        <div class="card-h"><h3>On call now</h3><span class="chip ok">${onNow.length} of ${d.teams.length} teams covered</span></div>
        ${d.teams.map((t) => `
          <div class="now-row"><span class="av">${initials(t.now?.name)}</span>
            <div><div class="n">${esc(t.now?.name || "nobody")}</div><div class="t">${esc(t.team)} · ${t.people.length} people</div></div>
            <div class="u">${t.now ? `until ${day(t.now.to)} ${clock(t.now.to)}` : ""}${t.next ? `<br>next: ${esc(t.next.name)}` : ""}</div></div>`).join("")}
      </div>
    </div>
    ${standingHtml(d)}
    <div class="card" style="margin-top:18px">
      <div class="card-h"><h3>Rota · the last six weeks</h3><span class="chip via">(:Person)-[:ON_SHIFT]->(:OnCallShift)-[:COVERS]->(:Team)</span></div>
      <div class="scroll-x">${rota(d)}</div>
    </div>`;
  $$(".seg button").forEach((b) => b.onclick = () => { filter = b.dataset.f; render(d); });
  const act = (sel, fn) => $$(sel).forEach((b) => b.onclick = async () => {
    b.disabled = true;
    try { await fn(b.dataset); } catch (e) { alert(e.message); }
    load();
  });
  act("[data-page]", (x) => post(`/incidents/${encodeURIComponent(x.page)}/page`));
  act("[data-ack]", (x) => post(`/pages/${encodeURIComponent(x.ack)}/ack`, { who: x.who || "on-call" }));
  act("[data-esc]", (x) => post(`/pages/${encodeURIComponent(x.esc)}/escalate`));
  act("[data-revoke]", (x) => post(`/standing/${encodeURIComponent(x.revoke)}/revoke`, { who: "on-call" }));
  const f = $("#sa-form");
  if (f) f.onsubmit = async (e) => {
    e.preventDefault();
    try { await post("/standing", { kind: $("#sa-kind").value, service: $("#sa-svc").value, uses: +$("#sa-uses").value, days: +$("#sa-days").value, who: "on-call" }); }
    catch (err) { alert(err.message); }
    load();
  };
}

const STATUS = { paged: "paged", acked: "acknowledged", escalated: "no answer, escalated", notice: "told, not paged", exhausted: "nobody left to page", unrouted: "nobody owns it" };

function pageCard(p, pg) {
  const sevHot = p.severity === "SEV1";
  const cur = pg?.current;
  let who;
  if (!cur) {
    who = `<div class="pc-who"><span class="av hot">${initials(p.page?.name)}</span>
        <div><div class="n">${p.page ? `Page ${esc(p.page.name)}` : "No one on call for that team"}</div>
          <div class="r">${esc(p.team || "")} on call now${p.was_on_call && p.page && p.was_on_call.handle !== p.page.handle ? ` · ${esc(p.was_on_call.name)} was on call when it started` : ""}</div></div></div>
      <div class="pc-act"><button class="btn sm primary" data-page="${esc(p.incident)}">Page ${esc(p.page?.name?.split(" ")[0] || "the owner")}</button>${links(p)}</div>`;
  } else {
    const live = cur.status === "paged";
    who = `<div class="pc-who"><span class="av${live ? " hot" : ""}">${initials(cur.name)}</span>
        <div><div class="n">${esc(cur.name || "nobody")} · ${esc(STATUS[cur.status] || cur.status)}${cur.level ? ` <span class="faint">level ${cur.level}</span>` : ""}</div>
          <div class="r">${esc(cur.why || "")}${cur.acked_by ? ` · by ${esc(cur.acked_by)}` : ""}</div>
          ${live ? `<div class="r" data-deadline="${Math.round(Date.now() / 1000 + cur.escalates_in)}">escalates in ${mmss(cur.escalates_in)}</div>` : ""}</div></div>
      <div class="pc-act">${live ? `<button class="btn sm primary" data-ack="${esc(cur.id)}" data-who="${esc(cur.handle || "on-call")}">Acknowledge as ${esc((cur.name || "on-call").split(" ")[0])}</button><button class="btn sm" data-esc="${esc(cur.id)}">Escalate now</button>` : ""}${links(p)}</div>
      ${pg.chain.length > 1 ? `<ol class="pc-chain">${pg.chain.map((c) => `<li><span class="mono">L${c.level}</span> ${esc(c.name || "–")} <span class="faint">${esc(STATUS[c.status] || c.status)}</span></li>`).join("")}</ol>` : ""}`;
  }
  return `
    <div class="page-card${settled(pg) ? " acked" : ""}">
      <div class="pc-top"><span class="chip${sevHot ? " hot" : " warn"}">${esc(p.severity)}</span><span class="mono faint" style="font-size:12px">${esc(p.incident)}</span>
        <span class="chip">${esc(p.confidence)} confidence</span><span class="sp"></span><span class="mono faint" style="font-size:12px">started ${clock(p.started)}</span></div>
      <div class="pc-title">${esc(p.title)}</div>
      <div class="pc-meta">noticed on ${esc(p.symptom)} · judge blames <b>${esc(p.blamed)}</b> on ${esc(p.service)} · owned by ${esc(p.team || "nobody")}</div>
      ${who}
    </div>`;
}

const links = (p) => `<a class="btn sm" href="/app?tenant=${encodeURIComponent(T)}&incident=${encodeURIComponent(p.incident)}">Open incident</a>`;

function standingHtml(d) {
  const sa = d.standing || [];
  const opts = (xs, sel) => xs.map((x) => `<option${x === sel ? " selected" : ""}>${esc(x)}</option>`).join("");
  return `<div class="card" style="margin-top:18px">
    <div class="card-h"><h3>Standing approvals</h3><span class="chip via">fires only when the gate confirms and the fix was proved</span></div>
    <form class="sa-form" id="sa-form">
      <select id="sa-kind" aria-label="Kind of fix">${Object.entries(KIND).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>
      <span class="faint">on</span><select id="sa-svc" aria-label="Service">${opts(services, "auth")}</select>
      <label class="faint">uses <input id="sa-uses" type="number" min="1" max="10" value="3"></label>
      <label class="faint">days <input id="sa-days" type="number" min="1" max="90" value="30"></label>
      <button class="btn sm primary" type="submit">Allow without paging</button>
    </form>
    ${sa.length ? sa.map((x) => `<div class="now-row"><div><div class="n">${esc(KIND[x.kind] || x.kind)} on ${esc(x.service)} <span class="faint mono">${esc(x.id)}</span></div>
        <div class="t">${x.active ? `${x.uses_left} of ${x.uses} uses left · until ${day(x.expires)}` : "revoked"} · granted by ${esc(x.by)}</div>
        ${x.decisions.map((c) => `<div class="t">${c.fired ? "✓ fired" : "✗ held back"} on ${esc(c.incident)}${c.reasons.length ? `: ${esc(c.reasons.join("; "))}` : ": nobody paged"}</div>`).join("")}</div>
        <div class="u">${x.active ? `<button class="btn sm" data-revoke="${esc(x.id)}">Revoke</button>` : ""}</div></div>`).join("")
      : `<p class="empty">None yet. A standing approval lets a repeat fault be fixed without waking anyone, within the terms you set; outside them it does nothing and says why.</p>`}
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
setInterval(load, 15000);
setInterval(() => $$("[data-deadline]").forEach((el) => { el.textContent = `escalates in ${mmss(Math.max(0, +el.dataset.deadline - Math.round(Date.now() / 1000)))}`; }), 1000);
