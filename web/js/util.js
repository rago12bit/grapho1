// Shared state and small helpers. Every other module imports from here; nothing here imports them.

export const state = {
  tenant: "kestrel",
  tenants: [],
  health: null,
  graph: null,        // services and CALLS edges with PageRank, as /graph returns them
  board: [],          // incidents with their leading suspect
  selected: null,     // open incident id
  service: null,      // service shown in the Company tab
  last: null,         // the case currently in the right panel (preview or agent run)
  tab: "board",
  scope: "case",      // graph: "case" shows only what the open case touches, "system" shows everything
  view: 0,            // bumped on every navigation, so a slow response never paints over a newer screen
};

// Cross-module calls go through here, filled in by main.js, so modules never import each other in a cycle.
export const act = {};

export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];
export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const plural = (n, one, many = one + "s") => `${n} ${n === 1 ? one : many}`;

export const hhmm = (ts) => new Date(ts * 1000).toISOString().slice(11, 16) + " UTC";
export function ago(ts) {
  const m = Math.round(((state.health?.now ?? ts) - ts) / 60);
  if (m < 90) return `${Math.max(m, 0)} min ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
}

export async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "content-type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || res.statusText);
  return body;
}

export const tapi = (path, opts) => api(`/api/${state.tenant}${path}`, opts);

export function on(root, sel, type, fn) {
  root.addEventListener(type, (e) => {
    const el = e.target.closest(sel);
    if (el && root.contains(el)) fn(el, e);
  });
}
