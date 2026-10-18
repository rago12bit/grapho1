// The header and footer every page around the incident room shares: nav, tenant picker, and FalkorDB's status
// (engine, version, ping, and the size of each graph), refreshed every few seconds.
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const api = async (path, opts) => {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

const KEY = "faultline.tenant";
export function tenant() {
  const q = new URLSearchParams(location.search).get("tenant");
  if (q) return q;
  try { return localStorage.getItem(KEY) || "kestrel"; } catch { return "kestrel"; }
}
export function setTenant(t) {
  try { localStorage.setItem(KEY, t); } catch {}
  const u = new URL(location.href);
  u.searchParams.set("tenant", t);
  location.href = u.toString();
}

const LINKS = [
  ["/app", "Incidents"], ["/oncall", "On-call"], ["/explore", "Explore"], ["/break", "Break it"], ["/replay", "Replay"],
  ["/app?tab=memory", "Memory"], ["/app?tab=company", "Company brain"], ["/results", "Results"],
];
const LOGO = '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M4 26 L13 6 L17 17 L21 11 L28 26"/></svg>';

export function mountShell({ tenants = true } = {}) {
  const here = location.pathname;
  const head = document.createElement("header");
  head.className = "sh";
  head.innerHTML = `
    <a class="sh-brand" href="/">${LOGO}<span>Hypocenter</span></a>
    <nav class="sh-nav" aria-label="Pages">${LINKS.map(([href, name]) =>
      `<a href="${href}"${href === here ? ' aria-current="page"' : ""}>${name}</a>`).join("")}</nav>
    <div class="sh-right">
      ${tenants ? `<label class="sh-tenant"><span>tenant</span><select id="sh-tenant" aria-label="Tenant"></select></label>` : ""}
      <div class="sh-db">
        <button class="sh-pill" id="sh-pill" aria-expanded="false" aria-controls="sh-drop"><i></i><b>FalkorDB</b> <span id="sh-state">connecting</span>
          <svg viewBox="0 0 10 10"><path d="M2 4 L5 7 L8 4"/></svg></button>
        <div class="sh-drop" id="sh-drop" hidden></div>
      </div>
      <button class="sh-burger" id="sh-burger" aria-label="Menu"><svg viewBox="0 0 16 16"><path d="M2 4h12M2 8h12M2 12h12"/></svg></button>
    </div>`;
  document.body.prepend(head);

  const foot = document.createElement("footer");
  foot.className = "sf";
  foot.innerHTML = `
    <div class="sf-row">
      <a class="sh-brand" href="/">${LOGO}<span>Hypocenter</span></a>
      ${LINKS.map(([href, name]) => `<a href="${href}">${name}</a>`).join("")}
      <span class="sf-note mono">On a FalkorDB graph · Kestrel Pay is synthetic · RCAEval is real</span>
    </div>
    <div class="sf-mark" aria-hidden="true">Hypocenter</div>`;
  document.body.append(foot);

  $("#sh-burger").onclick = () => head.classList.toggle("open");
  const pill = $("#sh-pill"), drop = $("#sh-drop");
  pill.onclick = (e) => { e.stopPropagation(); drop.hidden = !drop.hidden; pill.setAttribute("aria-expanded", String(!drop.hidden)); };
  document.addEventListener("click", (e) => { if (!drop.contains(e.target)) { drop.hidden = true; pill.setAttribute("aria-expanded", "false"); } });

  smooth();
  if (tenants) loadTenants();
  status();
  setInterval(status, 8000);
}

async function loadTenants() {
  const sel = $("#sh-tenant");
  try {
    const list = await api("/api/tenants");
    const cur = tenant();
    sel.innerHTML = list.map((t) => `<option value="${esc(t.id)}"${t.id === cur ? " selected" : ""}>${esc(t.title.replace(" · RCAEval", ""))}</option>`).join("");
    sel.onchange = () => setTenant(sel.value);
  } catch { sel.innerHTML = `<option>${esc(tenant())}</option>`; }
}

async function status() {
  const pill = $("#sh-pill"), state = $("#sh-state"), drop = $("#sh-drop");
  try {
    const e = await api("/api/engine");
    pill.className = "sh-pill ok";
    state.textContent = "ready";
    const cur = tenant();
    drop.innerHTML = `
      <h4>Engine</h4>
      <dl><dt>Database</dt><dd>${esc(e.engine)} ${esc(e.version || "")}</dd>
          <dt>Ping</dt><dd>${e.ping_ms} ms</dd>
          <dt>Nodes</dt><dd>${e.nodes.toLocaleString()}</dd>
          <dt>Edges</dt><dd>${e.edges.toLocaleString()}</dd></dl>
      <h4>Graphs · one per tenant</h4>
      <table>${e.graphs.map((g) => `<tr class="${g.tenant === cur ? "cur" : ""}"><td>${esc(g.tenant)}</td><td>${g.nodes.toLocaleString()} n</td><td>${g.edges.toLocaleString()} e</td></tr>`).join("")}</table>
      <p class="faint" style="margin:12px 0 0;font-size:12px">Browse it yourself: <a href="http://localhost:3003" target="_blank" rel="noopener">FalkorDB browser on :3003</a></p>`;
  } catch {
    pill.className = "sh-pill bad";
    state.textContent = "offline";
    drop.innerHTML = `<h4>Engine</h4><p class="muted" style="margin:0">FalkorDB isn't answering. Every page here is a graph query, so nothing can load until it is back; this reconnects by itself.</p>`;
  }
}

// Cypher colouring for code blocks written in our pages
const KW = /\b(MATCH|OPTIONAL|WHERE|WITH|RETURN|AND|OR|NOT|IN|MERGE|CREATE|SET|DELETE|DETACH|ORDER BY|DESC|LIMIT|UNWIND|CALL|YIELD|GRAPH\.COPY|AS|min|length|nodes|coalesce|count|collect|id|labels|type)\b/g;
export function cypher(text) {
  const e = esc(text);
  return e.split("\n").map((ln) => {
    const i = ln.indexOf("//");
    const code = i >= 0 ? ln.slice(0, i) : ln, note = i >= 0 ? ln.slice(i) : "";
    return code
      .replace(/&#39;[^&]*?&#39;/g, (m) => `\u0001s${m}\u0002`)
      .replace(/:([A-Z][A-Za-z_]*)/g, (m, l) => `:\u0001l${l}\u0002`)
      .replace(KW, (m) => `\u0001k${m}\u0002`)
      .replace(/\u0001(\w)([^\u0002]*)\u0002/g, '<span class="$1">$2</span>') + (note ? `<span class="c">${note}</span>` : "");
  }).join("\n");
}

export const ago = (t, now = Date.now() / 1000) => {
  const s = Math.max(0, now - t);
  return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
};
export const clock = (t) => new Date(t * 1000).toISOString().slice(11, 16) + " UTC";

// Smooth, eased wheel scrolling (Lenis). Canvases, iframes and scrolling lists keep their own wheel: they carry
// data-lenis-prevent. Off when the reader asked for reduced motion, and the page works the same without it.
async function smooth() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  $$("#cy, #rcy, .tl, iframe, .ex-canvas, .flowbox, .sits-scroll, .heat-wrap, .scroll-x").forEach((el) => el.setAttribute("data-lenis-prevent", ""));
  try {
    const { default: Lenis } = await import("https://cdn.jsdelivr.net/npm/lenis@1.1.13/dist/lenis.mjs");
    const lenis = new Lenis({ lerp: 0.09, smoothWheel: true, anchors: { offset: -70 } });
    const raf = (t) => { lenis.raf(t); requestAnimationFrame(raf); };
    requestAnimationFrame(raf);
    window.__lenis = lenis;
  } catch { /* offline: native scrolling */ }
}
