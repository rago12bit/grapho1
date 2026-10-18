// Landing page: live graph status, the hero's causal-chain animation, scroll reveals and the results bars.
(() => {
  const $ = (s, el = document) => el.querySelector(s);
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- live status pill: what is actually loaded in FalkorDB right now
  (async () => {
    const pill = $("#db");
    try {
      const [h, t] = await Promise.all([fetch("/api/health").then((r) => r.json()), fetch("/api/tenants").then((r) => r.json())]);
      const nodes = t.reduce((n, x) => n + Object.values(x.nodes || {}).reduce((a, b) => a + b, 0), 0);
      pill.textContent = `FalkorDB · ${t.length} graphs · ${nodes.toLocaleString()} nodes`;
      if (h.ok) pill.parentElement.classList.add("ok");
    } catch {
      pill.textContent = "FalkorDB · offline";
    }
  })();

  // ---- hero: INC-302 on Kestrel's real call graph. The deploy lands, auth alerts 240 s later, mobile-bff 120 s
  // after that; the fix is proved on a copy and both alerts clear. Positions are laid out by hand, edges are real.
  const N = {
    "web-frontend": [0.28, 0.12], "mobile-bff": [0.80, 0.12],
    "api-gateway": [0.42, 0.30],
    "checkout": [0.14, 0.50], "cart": [0.32, 0.50], "search": [0.50, 0.52], "auth": [0.70, 0.46], "users": [0.86, 0.60],
    "payments": [0.10, 0.72], "pricing": [0.30, 0.72], "catalog-es": [0.50, 0.74], "session-redis": [0.68, 0.74], "users-db": [0.92, 0.80],
    "D-5201": [0.56, 0.88], "F-5202": [0.86, 0.95],
  };
  const E = [
    ["web-frontend", "api-gateway"], ["mobile-bff", "api-gateway"], ["mobile-bff", "auth"], ["api-gateway", "auth"],
    ["api-gateway", "checkout"], ["api-gateway", "cart"], ["api-gateway", "search"], ["api-gateway", "users"],
    ["checkout", "cart"], ["checkout", "payments"], ["checkout", "pricing"], ["cart", "pricing"], ["cart", "session-redis"],
    ["search", "catalog-es"], ["auth", "users"], ["auth", "session-redis"], ["users", "users-db"],
  ];
  const CHANGES = [["D-5201", "auth"], ["F-5202", "users"]];
  const CHAIN = ["D-5201", "auth", "mobile-bff"];
  const PHASES = [ // [ms, caption]
    [1600, "Kestrel Pay · 24 services · 13:34 UTC"],
    [1700, "13:34 · D-5201 deploys auth 4.2.0 (rotating JWKS cache)"],
    [2000, "13:38 · auth alerts: 401 rate 31% · +240 s after the deploy"],
    [2000, "13:40 · mobile-bff alerts: login failures 30% · +120 s"],
    [2400, "CAUSED_BY: A-9102 → A-9101 → D-5201 · F-5202, the flag on users, ruled out"],
    [2600, "Proved on a graph copy: rolling back D-5201 clears both alerts"],
  ];

  const cv = $("#chain");
  const cap = $("#art-cap");
  if (cv) {
    const ctx = cv.getContext("2d");
    let W = 0, H = 0, dpr = 1;
    const size = () => {
      dpr = Math.min(2, devicePixelRatio || 1);
      W = cv.clientWidth; H = cv.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    size();
    addEventListener("resize", size);
    const P = (k) => [N[k][0] * W, N[k][1] * H];
    const HOT = "#ff6b4a", WARM = "#ffb347", COOL = "#6ee7b7", DIM = "#3a3a42", INK = "#d4d4d8";

    let phase = 0, t0 = performance.now();
    const step = () => {
      phase = (phase + 1) % PHASES.length; t0 = performance.now();
      if (cap) cap.textContent = PHASES[phase][1];
      setTimeout(step, PHASES[phase][0]);
    };
    if (cap) cap.textContent = PHASES[0][1];
    if (!reduced) setTimeout(step, PHASES[0][0]); else { phase = 4; if (cap) cap.textContent = PHASES[4][1]; }

    const lit = (k) => { // 0 idle, 1 alerting, 2 cleared
      const i = CHAIN.indexOf(k);
      if (i < 0) return 0;
      if (phase === 5) return 2;
      return phase >= i + 1 ? 1 : 0;
    };

    const line = (a, b, color, w, dash) => {
      ctx.beginPath(); ctx.strokeStyle = color; ctx.lineWidth = w; ctx.setLineDash(dash || []);
      ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
    };

    const draw = (now) => {
      ctx.clearRect(0, 0, W, H);
      const k = Math.min(1, (now - t0) / 700);
      const fs = Math.max(10, Math.min(13, W / 40));
      // call edges
      for (const [a, b] of E) {
        const onChain = CHAIN.includes(a) && CHAIN.includes(b) && phase >= 3 && phase < 5;
        line(P(a), P(b), onChain ? "rgba(255,107,74,.55)" : "rgba(161,161,170,.18)", onChain ? 2 : 1);
      }
      // changes hang below what they changed
      for (const [c, s] of CHANGES) {
        const decoy = c !== "D-5201";
        line(P(c), P(s), decoy ? "rgba(161,161,170,.18)" : phase >= 1 && phase < 5 ? "rgba(255,179,71,.7)" : "rgba(161,161,170,.25)", 1.2, [4, 4]);
      }
      // the CAUSED_BY pulse climbing the chain
      if (phase >= 2 && phase <= 4) {
        const hop = phase === 4 ? 2 : phase - 1;           // which hop is travelling
        for (let h = 0; h < Math.min(hop, 2); h++) {
          const a = P(CHAIN[h]), b = P(CHAIN[h + 1]);
          const f = h === hop - 1 && phase < 4 ? k : 1;
          line(a, [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f], HOT, 3);
          if (f < 1) {
            ctx.beginPath(); ctx.fillStyle = HOT;
            ctx.arc(a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, 5, 0, 7); ctx.fill();
          }
        }
      }
      // nodes
      for (const name of Object.keys(N)) {
        const [x, y] = P(name);
        const isChange = /^[DF]-\d/.test(name);
        const s = lit(name);
        const r = isChange ? 6 : name === "mobile-bff" || name === "auth" ? 9 : 7;
        let fill = isChange ? "#1d1d23" : "#18181d", stroke = DIM, text = "rgba(212,212,216,.62)";
        if (name === "D-5201" && phase >= 1 && phase < 5) { stroke = WARM; text = WARM; }
        if (name === "F-5202" && phase === 4) text = "rgba(161,161,170,.4)";
        if (s === 1) { fill = HOT; stroke = HOT; text = INK; }
        if (s === 2) { fill = "#0f2a22"; stroke = COOL; text = COOL; }
        if (s === 1 && !reduced) {
          ctx.beginPath(); ctx.fillStyle = "rgba(255,107,74,.16)";
          ctx.arc(x, y, r + 8 + 4 * Math.sin(now / 260), 0, 7); ctx.fill();
        }
        ctx.beginPath(); ctx.fillStyle = fill; ctx.strokeStyle = stroke; ctx.lineWidth = 1.6;
        if (isChange) ctx.rect(x - r, y - r, 2 * r, 2 * r); else ctx.arc(x, y, r, 0, 7);
        ctx.fill(); ctx.stroke();
        ctx.fillStyle = text; ctx.font = `500 ${fs}px "IBM Plex Mono", monospace`; ctx.textAlign = "center";
        ctx.fillText(name, x, y + r + fs + 3);
      }
      requestAnimationFrame(draw);
    };
    requestAnimationFrame(draw);
  }

  // ---- Cypher: colour keywords, strings, comments and labels (the text is ours, written in landing.html)
  const KW = /\b(MATCH|OPTIONAL|WHERE|WITH|RETURN|AND|OR|NOT|IN|MERGE|CREATE|SET|ORDER BY|DESC|LIMIT|UNWIND|GRAPH\.COPY|AS|min|length|nodes|coalesce)\b/g;
  for (const el of document.querySelectorAll(".cypher code")) {
    const esc = el.textContent.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]);
    el.innerHTML = esc.split("\n").map((ln) => {
      const i = ln.indexOf("//");
      const code = i >= 0 ? ln.slice(0, i) : ln, note = i >= 0 ? ln.slice(i) : "";
      const body = code
        .replace(/'[^']*'/g, (m) => `\u0001s${m}\u0002`)
        .replace(/:([A-Z][A-Za-z_]*)/g, (m, l) => `:\u0001l${l}\u0002`)
        .replace(KW, (m) => `\u0001k${m}\u0002`)
        .replace(/\u0001(\w)([^\u0002]*)\u0002/g, '<span class="$1">$2</span>');
      return body + (note ? `<span class="c">${note}</span>` : "");
    }).join("\n");
  }

  // ---- results bars, drawn from the numbers written in the page
  for (const box of document.querySelectorAll(".bars")) {
    const rows = JSON.parse(box.dataset.bars);
    const pair = box.classList.contains("pair");
    if (pair) box.insertAdjacentHTML("beforebegin",
      '<div class="legend"><span><i style="background:#2a2a31"></i>AC@1</span><span><i style="background:#1d1d23"></i>Avg@5</span></div>');
    box.innerHTML = rows.map(([name, v, me, avg]) => `
      <div class="bar${me ? " me" : ""}">
        <span class="name">${name}</span>
        <span class="rail">
          <span class="fill" data-w="${v}">${v.toFixed(2)}</span>
          ${pair ? `<span class="fill alt" data-w="${avg}">${avg.toFixed(2)}</span>` : ""}
        </span>
      </div>`).join("");
  }

  // ---- reveal on scroll
  const grow = (el) => el.querySelectorAll(".fill").forEach((f) => { f.style.width = `${100 * f.dataset.w}%`; });
  if ("IntersectionObserver" in window && !reduced) {
    const io = new IntersectionObserver((xs) => xs.forEach((x) => {
      if (!x.isIntersecting) return;
      x.target.classList.add("on"); grow(x.target); io.unobserve(x.target);
    }), { threshold: 0.25 });
    document.querySelectorAll(".step, .chart").forEach((el) => io.observe(el));
  } else {
    document.querySelectorAll(".step").forEach((el) => el.classList.add("on"));
    document.querySelectorAll(".chart").forEach(grow);
  }
})();
