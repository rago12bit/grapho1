"""The README's charts, drawn as plain SVG from the same files the Results page reads (and, for depth, from the
graph). Nothing is typed in: change a result and the chart changes. A test regenerates them and compares.

    docker compose run --rm -e FAULTLINE_GRAPH_PREFIX=charts_ -v "$PWD/docs:/app/docs" app python -m faultline.charts
"""
from __future__ import annotations

import math
from html import escape
from pathlib import Path

from . import db, results

OUT = Path(__file__).resolve().parents[1] / "docs" / "media" / "charts"
W = 760
BG, CARD, LINE, INK, INK2, INK3 = "#0b0b0e", "#141418", "#26262d", "#f4f4f5", "#b4b4be", "#7c7c88"
HOT, WARM, BAR, BAR2 = "#ff6b4a", "#ffb347", "#3a3a44", "#55555f"
FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, monospace"


def _frame(title: str, sub: str, body: str, h: int, foot: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {h}" width="{W}" height="{h}" role="img" aria-label="{escape(title)}">'
            f'<rect width="{W}" height="{h}" rx="14" fill="{BG}"/><rect x="1" y="1" width="{W - 2}" height="{h - 2}" rx="13" fill="none" stroke="{LINE}"/>'
            f'<text x="28" y="40" font-family="{FONT}" font-size="19" font-weight="700" fill="{INK}">{escape(title)}</text>'
            f'<text x="28" y="63" font-family="{FONT}" font-size="13" fill="{INK3}">{escape(sub)}</text>{body}'
            f'<text x="28" y="{h - 18}" font-family="{MONO}" font-size="11" fill="{INK3}">{escape(foot)}</text></svg>\n')


def _bars(rows: list[tuple[str, list[float], bool, str]], vmax: float, fmt, legend: list[str] | None = None, top: int = 92,
          label_w: int = 250) -> tuple[str, int]:
    """rows: (label, values (one or two), highlighted, note). Two values draw a thick and a thin bar."""
    out, y, span = [], top, W - label_w - 175
    if legend:
        out.append(f'<rect x="{label_w + 28}" y="{top - 22}" width="10" height="10" rx="2" fill="{HOT}"/>'
                   f'<text x="{label_w + 43}" y="{top - 13}" font-family="{MONO}" font-size="11" fill="{INK2}">{escape(legend[0])}</text>')
        if len(legend) > 1:
            x2 = label_w + 43 + 7 * len(legend[0]) + 22
            out.append(f'<rect x="{x2}" y="{top - 19}" width="10" height="4" rx="1" fill="{WARM}"/>'
                       f'<text x="{x2 + 15}" y="{top - 13}" font-family="{MONO}" font-size="11" fill="{INK2}">{escape(legend[1])}</text>')
        y += 6
    for label, vals, hi, note in rows:
        out.append(f'<text x="28" y="{y + 15}" font-family="{FONT}" font-size="13.5" font-weight="{700 if hi else 400}" '
                   f'fill="{INK if hi else INK2}">{escape(label)}</text>')
        x0 = label_w + 28
        out.append(f'<rect x="{x0}" y="{y + 2}" width="{span}" height="18" rx="4" fill="{CARD}"/>')
        w1 = max(2, span * vals[0] / vmax)
        out.append(f'<rect x="{x0}" y="{y + 2}" width="{w1:.1f}" height="18" rx="4" fill="{HOT if hi else BAR}"/>')
        if len(vals) > 1 and vals[1] is not None:
            w2 = max(2, span * vals[1] / vmax)
            out.append(f'<rect x="{x0}" y="{y + 22}" width="{w2:.1f}" height="5" rx="2" fill="{WARM if hi else BAR2}"/>')
        txt = note if fmt is None else " / ".join(fmt(v) for v in vals if v is not None) + (f"  {note}" if note else "")
        out.append(f'<text x="{x0 + span + 10}" y="{y + 15}" font-family="{MONO}" font-size="12" fill="{INK if hi else INK2}">{escape(txt)}</text>')
        y += 34 if len(vals) > 1 else 28
    return "".join(out), y


def real360(r: dict) -> str:
    rows = sorted(((m["name"].replace(" (metrics)", "") + (f" ({m['note']} cases)" if m["note"] else ""), m["cells"][-1],
                    m["name"].startswith("Hypocenter (metrics)"), "") for m in r["rcaeval"]["methods"] if m["cells"][-1][0] is not None),
                  key=lambda x: -x[1][0])
    rows = [(("Hypocenter, + logs and traces" if n == "Hypocenter (all)" else n), v, hi, note) for n, v, hi, note in rows]
    body, y = _bars(rows, 1.0, lambda v: f"{v:.2f}", ["AC@1 · right first", "Avg@5 · top five"])
    return _frame("All 360 real RCAEval failures, three systems",
                  "RCAEval's own baseline code, run here on the same cases, scored the same way", body, y + 46,
                  "docs/rcaeval-results.md · make eval")


def heldout(r: dict) -> str:
    rows = []
    for x in r["held_out_tt"]:
        if x["name"].startswith(("Hypocenter v2", "Hypocenter v1")):
            continue
        name = x["name"].replace(" (judge + skeptic)", "").replace(" (RCAEval's code, run here = published)", "").replace(" (RCAEval's code, run here)", "")
        rows.append((name, [x["ac1"], x["avg5"]], name.startswith("Hypocenter"), ""))
    rows.sort(key=lambda x: -x[1][0])
    body, y = _bars(rows, 1.0, lambda v: f"{v:.2f}", ["AC@1", "Avg@5"])
    return _frame("Held out: 90 Train Ticket failures nobody tuned on",
                  "NSigma ranks better on both. Kept in, not hidden.", body, y + 46, "README · Public data · make eval")


def planted(r: dict) -> str:
    syn = r["synthetic"]
    i = syn["kinds"].index("all")
    keep = ["Hypocenter (judge + skeptic)", "no recency", "no alert_chain", "no footprint", "no risk", "most recent change",
            "nearest recent change"]
    by = {row["name"]: row["values"][i] for row in syn["rows"]}
    rows = [(k.replace("Hypocenter (judge + skeptic)", "Hypocenter").replace("no ", "without "), [by[k]], k.startswith("Hypocenter"),
             "baseline" if "change" in k else "") for k in keep]
    body, y = _bars(rows, 100.0, lambda v: f"{v:.1f}%", label_w=230)
    return _frame("Planted faults: switch one factor off", "top-1 over 150 planted incidents with decoys, seed 7; two naive baselines",
                  body, y + 46, "python -m faultline.eval")


def confidence(r: dict) -> str:
    rows = [(f"says {c['label']} ({c['cases']} cases)", [c["right"]], c["label"] == "high", "") for c in r["confidence"]]
    body, y = _bars(rows, 100.0, lambda v: f"{v:.1f}%", label_w=230)
    return _frame("When it says high confidence, it is right", "confidence from the gap to the runner-up, checked on the planted eval",
                  body, y + 46, "python -m faultline.eval")


def depth(rep: dict) -> str:
    rows = [(f"depth {d}" + (" (a join could do this)" if d == "1" else ""), [n / rep["scored"] * 100], d != "1", f"{n}/{rep['scored']}")
            for d, n in ((str(k), v) for k, v in rep["right_at"].items())]
    body, y = _bars(rows, 100.0, lambda v: f"{v:.0f}%", label_w=230)
    return _frame("Depth 1 against the full depth", "Kestrel's open incidents judged with the search capped at each depth; right cause first",
                  body, y + 46, "tools.depth_sweep · /results#depth")


def scale(r: dict) -> str:
    small, big = r["scale"][0], r["scale"][-1]
    keep = ["dependencies, 5 hops (algo.BFS)", "blast radius (algo.SSpaths, incoming)", "the judge (rank_suspects)",
            "judge + skeptic + WCC groups", "PageRank over CALLS", "betweenness over CALLS"]
    q1 = {q["query"]: q["p50_ms"] for q in small["queries"]}
    q3 = {q["query"]: q["p50_ms"] for q in big["queries"]}
    lo, hi = math.log10(0.5), math.log10(1000)
    norm = lambda v: (math.log10(max(v, 0.5)) - lo) / (hi - lo)  # noqa: E731
    rows = [(k.replace(" (rank_suspects)", "").replace("judge + skeptic + WCC groups", "judge + skeptic + WCC"),
             [norm(q3[k]), norm(q1[k])], k.startswith("the judge"), f"{q3[k]:g} / {q1[k]:g} ms") for k in keep]
    body, y = _bars(rows, 1.0, None, [f"{big['services']:,} services", f"{small['services']:,}"])
    return _frame(f"At size: {big['nodes']:,} nodes, a verdict in {q3['the judge (rank_suspects)']:g} ms",
                  "p50 per query, log scale, on a laptop", body, y + 46,
                  "docs/scale.md · python -m faultline.scale")


def build() -> dict[str, str]:
    from . import seed, site
    from .db import Graph, client
    r = results.load()
    if not db.GRAPH_PREFIX.startswith(("charts", "faultlinetest")):
        raise SystemExit("set FAULTLINE_GRAPH_PREFIX=charts_ so the demo graphs are left alone")
    seed.build("kestrel", force=True)
    rep = site.depth_report(Graph("kestrel"))
    if db.GRAPH_PREFIX.startswith("charts"):
        for name in client().list_graphs():
            if name.startswith(db.GRAPH_PREFIX):
                client().connection.execute_command("GRAPH.DELETE", name)
    return {"real360.svg": real360(r), "heldout.svg": heldout(r), "planted.svg": planted(r),
            "confidence.svg": confidence(r), "depth.svg": depth(rep), "scale.svg": scale(r)}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    charts = build()
    for name, svg in charts.items():
        (OUT / name).write_text(svg)
    print("wrote", ", ".join(sorted(charts)))
