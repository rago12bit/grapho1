// Results: drawn from /api/results, which reads docs/rcaeval-results.md, docs/scale.md and the README's tables.
import { $, $$, api, esc, mountShell } from "./shell.js";

mountShell({ tenants: false });
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
const NAME = { "Faultline (metrics)": "Faultline", "Faultline (all)": "Faultline, logs + traces as alerts",
  "Earliest alert (metrics)": "Earliest alert", "Biggest anomaly (metrics)": "Biggest anomaly" };
const nice = (n) => NAME[n] || n;
const isMe = (n) => /^Faultline/.test(n);
const f2 = (x) => (x == null ? "–" : x.toFixed(2));

// grow bars once they scroll into view
const io = new IntersectionObserver((xs) => xs.forEach((x) => {
  if (!x.isIntersecting) return;
  io.unobserve(x.target);
  $$("[data-w]", x.target).forEach((el) => { el.style.width = el.dataset.w; });
}), { threshold: 0.15 });
const grow = (el) => (reduced ? $$("[data-w]", el).forEach((b) => { b.style.width = b.dataset.w; }) : io.observe(el));

let D = null, metric = 0;

function kpis() {
  const all = D.rcaeval.methods.find((m) => m.name === "Faultline (metrics)").cells.at(-1)[0];
  const ns = D.rcaeval.methods.find((m) => m.name === "NSigma").cells.at(-1)[0];
  const tt = D.held_out_tt[0], nsTT = D.held_out_tt.find((r) => r.name.startsWith("NSigma"));
  const seeds = D.held_out_seeds.map((s) => s.skeptic);
  const big = D.scale.at(-1), judge = big.queries.find((q) => q.query.startsWith("the judge"));
  const K = [
    [f2(all), "", true, "right service first, 360 real failures", `NSigma ${f2(ns)} on the same cases`],
    [f2(tt.ac1), "", false, "right first on 90 held-out Train Ticket", `behind NSigma ${f2(nsTT.ac1)}`, true],
    [`${Math.min(...seeds)}–${Math.max(...seeds)}`, "%", false, "planted faults, held-out seeds", `${D.held_out_seeds.length} seeds × 400 scenarios`],
    [String(Math.round(judge.p50_ms)), "ms", false, `a verdict at ${big.services.toLocaleString()} services`, `${(big.nodes / 1000).toFixed(0)}k nodes · ${(big.edges / 1000).toFixed(0)}k edges`],
    ["4/4", "", false, "planted fixes cleared what the copy predicted", "the decoy cleared nothing"],
    [String(D.proof.tests), "", false, "tests, each fix with a fails-without-it check", "docker compose run --rm app pytest"],
  ];
  $("#kpis").innerHTML = K.map(([v, u, hot, l, s, bad]) => `<div class="kpi"><div class="v${hot ? " hot" : ""}"${v.length > 5 ? ' style="font-size:30px"' : ""}>${esc(v)}${u ? `<small>${u}</small>` : ""}</div><div class="l">${esc(l)}</div><div class="s${bad ? " bad" : ""}">${esc(s)}</div></div>`).join("");
}

function heat() {
  const R = D.rcaeval;
  const rows = [...R.methods].sort((a, b) => (isMe(b.name) - isMe(a.name)) || (b.cells.at(-1)[metric] ?? 0) - (a.cells.at(-1)[metric] ?? 0));
  const shade = (v) => (v == null ? "" : `background: rgba(255,107,74,${(0.06 + 0.62 * v * v).toFixed(3)})`);
  $("#heat").innerHTML = `<thead><tr><th>method</th>${R.datasets.map((d, i) => `<th>${esc(d)}<small>${R.sizes[i]} cases</small></th>`).join("")}</tr></thead><tbody>
    ${rows.map((m) => `<tr class="${m.name === "Faultline (metrics)" ? "me" : ""}"><td>${esc(nice(m.name))}</td>${m.cells.map((c, i) => {
      const v = c[metric];
      return `<td class="c${v == null ? " na" : ""}${i === m.cells.length - 1 ? " all" : ""}" style="${shade(v)}" title="${esc(nice(m.name))} · ${esc(R.datasets[i])}">${f2(v)}${i === m.cells.length - 1 && m.note ? `<small class="faint"> /${m.note}</small>` : ""}</td>`;
    }).join("")}</tr>`).join("")}</tbody>`;
  const ranked = [...R.methods].filter((m) => m.cells.at(-1)[metric] != null).sort((a, b) => b.cells.at(-1)[metric] - a.cells.at(-1)[metric]);
  $("#rank360").innerHTML = ranked.map((m) => {
    const v = m.cells.at(-1)[metric];
    return `<div class="hb${m.name === "Faultline (metrics)" ? " me" : ""}"><span class="n">${esc(nice(m.name))}${m.note ? ` · ${m.note}` : ""}</span><span class="r"><i data-w="${v * 100}%"></i></span><span class="val">${f2(v)}</span></div>`;
  }).join("");
  if (reduced) grow($("#rank360")); else $$("#rank360 [data-w]").forEach((b) => { b.style.width = b.dataset.w; });
}

function says() {
  const g = (n) => D.rcaeval.methods.find((m) => m.name === n).cells.at(-1);
  const me = g("Faultline (metrics)"), ns = g("NSigma"), all = g("Faultline (all)");
  $("#says").innerHTML = `
    <li><b>Ahead over all 360:</b> Faultline names the right service first in ${f2(me[0])} of cases; NSigma, the best baseline, ${f2(ns[0])}.</li>
    <li><b>Its top five are weaker:</b> Avg@5 ${f2(me[1])} against NSigma's ${f2(ns[1])}. When it misses, the cause is often lower in its list.</li>
    <li><b>Logs and traces as alerts</b> scored ${f2(all[0])}, below metric alerts, so the headline uses metric alerts and keeps logs and traces as evidence.</li>
    <li><b>CIRCA and PC-RandomWalk</b> take 5–10 minutes per Train Ticket case here; they were run on the other 240.</li>`;
}

function held() {
  const rows = D.held_out_tt;
  $("#heldbars").innerHTML = rows.map((r) => `
    <div class="hb${/^Faultline v3/.test(r.name) ? " me" : ""}"><span class="n" title="${esc(r.name)}">${esc(r.name.replace(" (judge + skeptic)", "").replace("(RCAEval's code, run here = published)", "· run here").replace("(RCAEval's code, run here)", "· run here"))}</span>
      <span class="r"><i class="a" data-w="${(r.ac1 ?? 0) * 100}%"></i><i class="b" data-w="${(r.avg5 ?? 0) * 100}%"></i></span><span class="val">${f2(r.ac1)}</span></div>`).join("");
  grow($("#heldbars"));
  const v = (n) => rows.find((r) => r.name.startsWith(n));
  const me = v("Faultline v3"), ns = v("NSigma");
  $("#heldnote").innerHTML = `NSigma ranks better here (${f2(ns.ac1)} against ${f2(me.ac1)}), and its top five are better (${f2(ns.avg5)} against ${f2(me.avg5)}). In 20 of Faultline's 26 misses the cause alerted but ranked below fifth among 68 services: that is where the next improvement has to come from.`;
  $("#steps3").innerHTML = ["Faultline v1", "Faultline v2", "Faultline v3"].map((n) => { const r = v(n); return `<div><b>${f2(r.ac1)}</b><span>${n.slice(10)} · AC@1</span></div>`; }).join("");
}

function planted() {
  const S = D.synthetic, all = S.kinds.indexOf("all");
  const seeds = D.held_out_seeds;
  $("#seedkpis").innerHTML = [
    ...seeds.map((s) => [`${s.skeptic}%`, `held-out seed ${s.seed}`, `judge alone ${s.judge}%`]),
    [`${D.learning_rerun.after}%`, "the same faults, second time", `first time ${D.learning_rerun.before}%`],
  ].map(([v, l, s]) => `<div class="kpi"><div class="v" style="font-size:30px">${esc(v)}</div><div class="l">${esc(l)}</div><div class="s">${esc(s)}</div></div>`).join("");
  $("#ablation").innerHTML = S.rows.map((r) => {
    const v = r.values[all], me = /^Faultline/.test(r.name);
    return `<div class="hb${me ? " me" : ""}"><span class="n">${esc(r.name)}</span><span class="r"><i data-w="${v}%"></i></span><span class="val">${v}%</span></div>`;
  }).join("");
  grow($("#ablation"));
  const col = { high: "var(--cool)", medium: "var(--warm)", low: "var(--hot)" };
  $("#conf").innerHTML = D.confidence.map((c) => `
    <div class="cf"><span class="lab ${c.label}">${c.label}</span><span class="track"><i data-w="${c.right}%" style="background:${col[c.label]};opacity:.75"></i><span>${c.cases} cases</span></span><span class="v">${c.right}%</span></div>`).join("");
  grow($("#conf"));
  const shade = (v) => (v == null ? "" : `background: rgba(110,231,183,${(0.04 + 0.55 * Math.pow(v / 100, 3)).toFixed(3)})`);
  $("#kinds").innerHTML = `<thead><tr><th>top-1 by kind · seed 7</th>${S.kinds.map((k) => `<th>${esc(k)}</th>`).join("")}</tr></thead><tbody>
    ${S.rows.map((r) => `<tr class="${/^Faultline/.test(r.name) ? "me" : ""}"><td>${esc(r.name)}</td>${r.values.map((v) => `<td class="c" style="${shade(v)}">${v == null ? "–" : `${v}%`}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

function learning() {
  $("#learned").innerHTML = D.learned.map((l) => `
    <div class="card lc"><h3>${esc(l.company)}</h3><div class="sub">${l.open} open incidents, the test</div>
      <div class="nums"><b>${l.learned}</b><span>named first, learned · ${l.hand} with hand weights</span></div>
      <div class="hbars" style="margin-top:12px">
        <div class="hb"><span class="n">hand-set</span><span class="r"><i data-w="${(l.hand / l.open) * 100}%"></i></span><span class="val">${l.hand}</span></div>
        <div class="hb me"><span class="n">learned</span><span class="r"><i data-w="${(l.learned / l.open) * 100}%"></i></span><span class="val">${l.learned}</span></div></div>
      <div class="brier">Brier ${l.brier[0]} → <b>${l.brier[1]}</b> <span class="faint">(lower is better calibrated)</span></div></div>`).join("");
  grow($("#learned"));
  const r = D.learning_rerun;
  $("#rerun").innerHTML = `<h3 class="h3">Memory: the same 150 planted faults again, after the first round was solved</h3>
    <div class="rerun"><div><div class="big2">${r.before}%</div><span class="faint mono" style="font-size:12px">first time</span></div><span class="arrow">→</span>
      <div><div class="big2" style="color:var(--cool)">${r.after}%</div><span class="faint mono" style="font-size:12px">second time</span></div>
      <p class="muted" style="margin:0;max-width:460px;font-size:14px">Precedent is what changed: without it the second round scores ${r.no_precedent}%. Past incidents, their causes and fixes are nodes the judge reads.</p></div>`;
}

function scale() {
  $("#scalecards").innerHTML = D.scale.map((s) => `
    <div class="card sc2"><div class="t">${s.services.toLocaleString()} services</div>
      <div class="facts"><div><b>${(s.nodes / 1000).toFixed(0)}k</b><span>nodes</span></div><div><b>${(s.edges / 1000).toFixed(0)}k</b><span>edges</span></div>
        <div><b>${Math.round(s.queries.find((q) => q.query.startsWith("the judge")).p50_ms)} ms</b><span>a verdict</span></div></div>
      ${s.planted ? `<p>The planted change named first in <b>${s.planted.first} of ${s.planted.incidents}</b> incidents, in the top three in ${s.planted.top3}.</p>` : ""}</div>`).join("");
  const [a, b] = [D.scale[0], D.scale.at(-1)];
  const max = Math.log10(Math.max(...b.queries.map((q) => q.p50_ms)) + 1);
  const w = (ms) => `${Math.max(1.5, (Math.log10(ms + 1) / max) * 100)}%`;
  $("#latency").innerHTML = b.queries.map((q) => {
    const small = a.queries.find((x) => x.query === q.query);
    return `<div class="lat${q.query.startsWith("the judge") ? " judge" : ""}"><span class="n">${esc(q.query)}</span><div class="bs">
      <span class="b"><i data-w="${w(small.p50_ms)}"></i>${small.p50_ms} ms</span><span class="b big"><i data-w="${w(q.p50_ms)}"></i>${q.p50_ms} ms</span></div></div>`;
  }).join("");
  grow($("#latency"));
}

async function proofs() {
  const btn = $("#runproofs");
  btn.disabled = true; btn.textContent = "Proving on graph copies…";
  const t0 = performance.now();
  try {
    const rows = await api("/api/results/proofs/run", { method: "POST" });
    const held = rows.filter((r) => r.holds).length;
    $("#proofmeta").textContent = `${held} of ${rows.length} held · ${Math.round(performance.now() - t0)} ms for ${rows.length} copies, all dropped`
      + (rows.some((r) => r.truly_caused.some((a) => !r.predicted_clear.includes(a))) ? " · * fired above the symptom, outside what the proof predicts" : "");
    $("#prooftbl").innerHTML = `<thead><tr><th>Incidents</th><th>Fix</th><th>On the copy</th><th>Predicted to clear</th><th>Planted as caused by it</th><th class="r">Held</th></tr></thead><tbody>
      ${rows.map((r) => `<tr><td class="mono">${esc(r.incidents.join(", "))}${r.decoy ? ' <span class="chip warn">decoy</span>' : ""}</td>
        <td>${esc(r.kind.replace("_", " "))} <span class="mono">${esc(r.change || r.target)}</span></td>
        <td><span class="verdict ${r.verdict === "resolves" ? "resolves" : "nothing"}">${esc(r.verdict)}</span></td>
        <td class="alerts">${r.predicted_clear.length ? esc(r.predicted_clear.join(" ")) : "nothing"}</td>
        <td class="alerts">${r.truly_caused.length ? r.truly_caused.map((a) => r.predicted_clear.includes(a) ? esc(a)
          : `<span class="faint" title="Fired above the symptom, outside the incident's alert cone; the proof only predicts what lies below it">${esc(a)}*</span>`).join(" ") : "nothing"}</td>
        <td class="r">${r.holds ? '<span class="ok-mark">✓</span>' : '<span class="bad-mark">✗</span>'} <span class="faint">${r.ms} ms</span></td></tr>`).join("")}</tbody>`;
  } catch (e) {
    $("#prooftbl").innerHTML = `<tbody><tr><td class="empty">${esc(e.message)}</td></tr></tbody>`;
  }
  btn.disabled = false; btn.textContent = "Run them again";
}

// the section nav follows the page
function toc() {
  const links = $$("#toc a");
  const obs = new IntersectionObserver((xs) => xs.forEach((x) => {
    if (x.isIntersecting) links.forEach((a) => a.classList.toggle("on", a.getAttribute("href") === `#${x.target.id}`));
  }), { rootMargin: "-40% 0px -55% 0px" });
  $$(".rs").forEach((s) => obs.observe(s));
}

(async () => {
  try { D = await api("/api/results"); }
  catch (e) { $("#kpis").innerHTML = `<p class="empty">${esc(e.message)}</p>`; return; }
  kpis(); heat(); says(); held(); planted(); learning(); scale(); toc();
  $("#breakline").textContent = `${D.experiments} one-line changes, each judged on its own GRAPH.COPY.`;
  $$("#metric button").forEach((b) => b.onclick = () => {
    metric = +b.dataset.m;
    $$("#metric button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    heat();
  });
  $("#runproofs").onclick = proofs;
  new IntersectionObserver(([x], o) => { if (x.isIntersecting) { o.disconnect(); proofs(); } }, { rootMargin: "200px" }).observe($("#proofs"));
})();
