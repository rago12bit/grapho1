// HTML building blocks shared by every panel: section headers, chains, stamps, Cypher, sparklines.
import { esc, hhmm } from "./util.js";

export function section(title, body, right = "", cls = "") {
  return `<section class="sec ${cls}"><div class="label-row"><h3 class="label">${title}</h3>${right}</div>${body}</section>`;
}

export const label = (text, cls = "") => `<span class="label ${cls}">${text}</span>`;

/** a → b → c, the last one hot when `hot`. */
export function chain(items, hot = false) {
  return `<span class="chain">${items.map((s, i) =>
    `<span class="${hot && i === items.length - 1 ? "hot" : ""}">${esc(s)}</span>${i < items.length - 1 ? `<span class="arr">→</span><wbr>` : ""}`).join("")}</span>`;
}

export const sev = (s) => `<span class="stamp ${esc(s)}">${esc(s)}</span>`;
export const status = (s) => `<span class="label st-${esc(s)}">${esc(s)}</span>`;
export function conf(c, p) {
  if (!c) return "";
  return `<span class="label conf-${esc(c)}">${esc(c)} confidence${p != null ? ` · ${Math.round(100 * p)}%` : ""}</span>`;
}

export const empty = (title, hint = "") => `<div class="empty-box"><div class="serif">${esc(title)}</div>${hint ? `<p>${esc(hint)}</p>` : ""}</div>`;
export const loading = (what = "Loading") => `<div class="loading">${esc(what)}…</div>`;

const KW = /(\b(?:OPTIONAL MATCH|MATCH|WHERE|RETURN|WITH|ORDER BY|LIMIT|CALL|YIELD|DISTINCT|AS|AND|OR|NOT|IN|IS NULL|IS NOT NULL|UNWIND|MERGE|CREATE|SET|CASE|WHEN|THEN|ELSE|END|DESC|FOREACH)\b|\b(?:algo\.\w+|db\.idx\.[\w.]+|collect|count|min|max|nodes|length|size|labels|type|coalesce)(?=\s*\()|\$\w+|'[^']*')/g;

export function cypherHtml(text) {
  return String(text).split(KW).map((p, i) => {
    if (!p) return "";
    if (i % 2 === 0) return esc(p);
    if (p.startsWith("$")) return `<span class="c-param">${esc(p)}</span>`;
    if (p.startsWith("'")) return `<span class="c-str">${esc(p)}</span>`;
    if (/^[a-z]/.test(p)) return `<span class="c-fn">${esc(p)}</span>`;
    return `<span class="c-kw">${esc(p)}</span>`;
  }).join("");
}

/** One query per row; click to see it whole with its parameters. */
export function queries(list, max = 6) {
  if (!list?.length) return "";
  const rows = list.slice(0, max).map((q) => `
    <details class="cq"><summary><span class="sign"></span><span class="q1">${esc(q.cypher.replace(/\s+/g, " ").slice(0, 80))}</span>
      <span class="num">${q.ms} ms</span><span class="num">${q.rows}r</span></summary>
      <pre class="cypher">${cypherHtml(q.cypher)}</pre>
      ${q.params && Object.keys(q.params).length ? `<div class="qmeta">${esc(JSON.stringify(q.params).slice(0, 300))}</div>` : ""}
    </details>`).join("");
  return rows + (list.length > max ? `<div class="qmore">+ ${list.length - max} more</div>` : "");
}

/** Robust z-score of the metric that fired, around the fault (real telemetry only). The dashed line is the
 *  detector's threshold; the tick marks the onset. */
export function spark(series, { width = 120, height = 26, threshold = 5, step = 5, from = -60, onset = null } = {}) {
  if (!series?.length) return "";
  const capped = series.map((z) => Math.max(Math.min(z, 60), -5));
  const hi = Math.max(threshold * 2, ...capped), lo = Math.min(0, ...capped);
  const x = (i) => (i / (capped.length - 1)) * width;
  const y = (z) => height - 2 - ((z - lo) / (hi - lo || 1)) * (height - 4);
  const pts = capped.map((z, i) => `${x(i).toFixed(1)},${y(z).toFixed(1)}`).join(" ");
  const inj = x((0 - from) / step);
  const on = onset != null ? x((onset - from) / step) : null;
  return `<svg class="spark" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" aria-hidden="true">
    <line x1="0" x2="${width}" y1="${y(threshold)}" y2="${y(threshold)}" class="thr"/>
    <line x1="${inj}" x2="${inj}" y1="0" y2="${height}" class="inj"/>
    ${on != null ? `<line x1="${on}" x2="${on}" y1="0" y2="${height}" class="onset"/>` : ""}
    <polyline points="${pts}"/></svg>`;
}

export function stepRow(s, i) {
  const args = Object.entries(s.input || {}).filter(([k]) => k !== "summary" && k !== "reason")
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : Array.isArray(v) ? (v.length > 3 ? `[${v.length} items]` : JSON.stringify(v)) : v && typeof v === "object" ? "{…}" : JSON.stringify(v)}`).join(", ");
  const q = s.cypher || [];
  const ms = q.reduce((a, x) => a + x.ms, 0);
  return `<li class="step${s.error ? " err" : ""}${s.tool.endsWith("_handoff") ? " handoff" : ""}" style="animation-delay:${i * 40}ms">
    <div class="who label">${esc(s.role || "")}</div>
    <div class="what">
      ${s.note ? `<div class="note">${esc(s.note)}</div>` : ""}
      <div class="tool">${esc(s.tool)}(${esc(args)})</div>
      ${s.error ? `<div class="note bad">${esc(s.error)}</div>` : ""}
      ${queries(q, 3)}
    </div>
    <div class="num faint">${q.length ? `${ms.toFixed(1)} ms` : ""}</div>
  </li>`;
}

export const time = (ts) => `<span class="num">${hhmm(ts).slice(0, 5)}</span>`;
