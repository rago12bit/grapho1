// Hero background: slow-moving contour lines, like a geological map of a fault. A noise field is traced with
// marching squares a few times a second; the pointer raises a gentle ridge where it is.
const F2 = 0.5 * (Math.sqrt(3) - 1), G2 = (3 - Math.sqrt(3)) / 6;
const GRAD = [[1, 1], [-1, 1], [1, -1], [-1, -1], [1, 0], [-1, 0], [0, 1], [0, -1]];
const perm = new Uint8Array(512);
{ const p = [...Array(256).keys()]; let s = 7; for (let i = 255; i > 0; i--) { s = (s * 16807) % 2147483647; const j = s % (i + 1); [p[i], p[j]] = [p[j], p[i]]; } for (let i = 0; i < 512; i++) perm[i] = p[i & 255]; }

// 2D simplex noise (the standard skew/unskew construction), in [-1, 1]
function noise(x, y) {
  const s = (x + y) * F2, i = Math.floor(x + s), j = Math.floor(y + s), t = (i + j) * G2;
  const x0 = x - (i - t), y0 = y - (j - t);
  const i1 = x0 > y0 ? 1 : 0, j1 = 1 - i1;
  const x1 = x0 - i1 + G2, y1 = y0 - j1 + G2, x2 = x0 - 1 + 2 * G2, y2 = y0 - 1 + 2 * G2;
  const ii = i & 255, jj = j & 255;
  let n = 0;
  for (const [dx, dy, gi] of [[x0, y0, perm[ii + perm[jj]]], [x1, y1, perm[ii + i1 + perm[jj + j1]]], [x2, y2, perm[ii + 1 + perm[jj + 1]]]]) {
    let tt = 0.5 - dx * dx - dy * dy;
    if (tt > 0) { tt *= tt; const g = GRAD[gi & 7]; n += tt * tt * (g[0] * dx + g[1] * dy); }
  }
  return 70 * n;
}

export function mountContours(canvas) {
  const ctx = canvas.getContext("2d");
  let W = 0, H = 0, cols = 0, rows = 0, field = null, mx = -1e4, my = -1e4;
  const CELL = 18, LEVELS = [-0.55, -0.35, -0.15, 0.05, 0.25, 0.45, 0.65];
  const size = () => {
    const dpr = Math.min(2, devicePixelRatio || 1);
    W = canvas.clientWidth; H = canvas.clientHeight;
    canvas.width = W * dpr; canvas.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    cols = Math.ceil(W / CELL) + 1; rows = Math.ceil(H / CELL) + 1;
    field = new Float32Array(cols * rows);
  };
  new ResizeObserver(size).observe(canvas);
  size();
  addEventListener("pointermove", (e) => { const r = canvas.getBoundingClientRect(); mx = e.clientX - r.left; my = e.clientY - r.top; }, { passive: true });

  let visible = true, last = 0;
  new IntersectionObserver(([x]) => { visible = x.isIntersecting; }).observe(canvas);
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const draw = (now) => {
    requestAnimationFrame(draw);
    if (!visible || now - last < 50) return;                 // ~20 fps is plenty for drifting lines
    last = now;
    const t = reduce ? 0 : now / 26000;
    for (let j = 0; j < rows; j++) for (let i = 0; i < cols; i++) {
      const x = i * CELL, y = j * CELL;
      const d = Math.hypot(x - mx, y - my);
      field[j * cols + i] = noise(x / 520 + t, y / 520 - t * 0.6) * 0.8 + noise(x / 190 - t, y / 190) * 0.22 + Math.exp(-(d * d) / 26000) * 0.45;
    }
    ctx.clearRect(0, 0, W, H);
    LEVELS.forEach((lv, li) => {
      ctx.beginPath();
      for (let j = 0; j < rows - 1; j++) for (let i = 0; i < cols - 1; i++) {
        const a = field[j * cols + i], b = field[j * cols + i + 1], c = field[(j + 1) * cols + i + 1], d = field[(j + 1) * cols + i];
        const code = (a > lv) | ((b > lv) << 1) | ((c > lv) << 2) | ((d > lv) << 3);
        if (code === 0 || code === 15) continue;
        const x = i * CELL, y = j * CELL;
        const top = [x + CELL * (lv - a) / (b - a), y], right = [x + CELL, y + CELL * (lv - b) / (c - b)];
        const bot = [x + CELL * (lv - d) / (c - d), y + CELL], left = [x, y + CELL * (lv - a) / (d - a)];
        const seg = (p, q) => { ctx.moveTo(p[0], p[1]); ctx.lineTo(q[0], q[1]); };
        switch (code) {
          case 1: case 14: seg(left, top); break;
          case 2: case 13: seg(top, right); break;
          case 3: case 12: seg(left, right); break;
          case 4: case 11: seg(right, bot); break;
          case 6: case 9: seg(top, bot); break;
          case 7: case 8: seg(left, bot); break;
          case 5: seg(left, top); seg(right, bot); break;
          case 10: seg(top, right); seg(left, bot); break;
        }
      }
      ctx.strokeStyle = li === 3 ? "rgba(255,107,74,0.22)" : `rgba(255,255,255,${0.045 + (li % 2) * 0.02})`;
      ctx.lineWidth = li === 3 ? 1.2 : 1;
      ctx.stroke();
    });
  };
  requestAnimationFrame(draw);
}
