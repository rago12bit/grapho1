"""Hypocenter against RCAEval's published baselines, on the same cases, scored the same way.

    docker compose run --rm app python -m faultline.rcaeval --suites re2,re3 --signals metrics --json data/rcaeval/faultline_metrics.json
    docker compose run --rm app python -m faultline.rcaeval --suites re2,re3 --signals all --json data/rcaeval/faultline_all.json
    (baselines: see baselines/run_baselines.py)
    docker compose run --rm app python -m faultline.compare

Every ranking, Hypocenter's included, goes through RCAEval's own service mapping (`name.split("_")[0]
.replace("-db", "")`, duplicates dropped) and is scored with AC@1, AC@3 and Avg@5 (the mean of AC@1..AC@5). A
method that fails or times out on a case scores zero on it. RCAEval's paper (arXiv 2412.17015, Table 6) publishes
per-method numbers for RE2 Train Ticket; they are printed next to ours as a check that the baselines were run
the way their authors ran them.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "rcaeval"
# Run here with RCAEval's own code. Trace methods (MicroRank, TraceRCA) are not: RCAEval's main.py feeds every
# method metrics only, so how their authors prepared spans for them isn't published, and our attempt did not
# reproduce their Table 6 numbers; those are quoted as published instead. CIRCA and PC-RandomWalk take 5-10 minutes
# per Train Ticket case here and were run on Online Boutique and Sock Shop only.
BASELINES = {"baro": "BARO", "nsigma": "NSigma", "circa": "CIRCA", "pc_randomwalk": "PC-RandomWalk"}
SKIP_TT = {"circa", "pc_randomwalk"}
# RCAEval paper, Table 6: RE2 Train Ticket, AVERAGE over the six fault types (AC@1, AC@3, Avg@5), as published.
PUBLISHED_RE2_TT = {
    "BARO": (0.67, 0.82, 0.80), "CIRCA": (0.32, 0.47, 0.46), "MicroRank": (0.16, 0.37, 0.31), "TraceRCA": (0.66, 0.79, 0.77),
    "CausalRCA": (0.22, 0.47, 0.43), "MicroCause": (0.10, 0.22, 0.20), "RCD": (0.09, 0.13, 0.13),
    "Multi-source BARO": (0.69, 0.82, 0.81), "Multi-source CIRCA": (0.06, 0.11, 0.13), "PDiagnose": (0.48, 0.70, 0.67),
    "Multi-source RCD": (0.10, 0.64, 0.54),
}
FAULT_ORDER = ["cpu", "mem", "disk", "socket", "delay", "loss"]


def _svc(name: str) -> str:
    return str(name).split("_")[0].replace("-db", "")


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names:
        s = _svc(n)
        if s not in out:
            out.append(s)
    return out


def _ac(ranks: list[str], truth: str) -> list[bool]:
    return [truth in ranks[:k] for k in range(1, 6)]


def load(signals: tuple[str, ...] = ("metrics", "all")) -> tuple[dict, dict[str, dict[str, list[bool]]]]:
    """Per case: the answer key, and per method the AC@1..5 hits."""
    meta: dict[str, dict] = {}
    hits: dict[str, dict[str, list[bool]]] = {}
    for sig in signals:
        path = DATA / f"faultline_{sig}.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text())["rows"]:
            meta.setdefault(r["case"], {}).update({k: r[k] for k in ("suite", "system", "fault", "truth")})
            if sig == "metrics" and r.get("confidence"):
                meta[r["case"]]["confidence"] = r["confidence"]       # the skeptic's label on the headline run
            for m, label in (("judge+skeptic", "Hypocenter"), ("earliest alert", "Earliest alert"), ("biggest anomaly", "Biggest anomaly")):
                name = f"{label} ({sig})" if label == "Hypocenter" or sig == "metrics" else None
                if name:
                    hits.setdefault(name, {})[r["case"]] = _ac(_dedupe(r["top"][m]), r["truth"])
    for key, label in BASELINES.items():
        path = DATA / "baselines" / f"{key}.json"
        if not path.exists():
            continue
        res = json.loads(path.read_text())
        for case, m in meta.items():
            if key in SKIP_TT and m["system"] == "tt":
                continue
            r = res.get(case)
            hits.setdefault(label, {})[case] = _ac(_dedupe(r.get("ranks") or []), m["truth"]) if r else None
    return meta, hits


def scores(cases: list[str], per_case: dict[str, list[bool] | None]) -> dict | None:
    rows = [per_case.get(c) for c in cases if c in per_case]
    if not rows:
        return None
    n = len(rows)
    missing = sum(r is None for r in rows)
    rows = [r or [False] * 5 for r in rows]
    return {"n": n, "missing": missing, "ac1": sum(r[0] for r in rows) / n, "ac3": sum(r[2] for r in rows) / n,
            "avg5": sum(sum(r) / 5 for r in rows) / n}


def groups(meta: dict) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for c, m in sorted(meta.items()):
        out.setdefault(f"{m['suite']}-{m['system'].upper()}", []).append(c)
    out["All 360"] = sorted(meta)
    return out


def table(meta: dict, hits: dict) -> str:
    gs = groups(meta)
    names = sorted(hits, key=lambda h: (not h.startswith("Hypocenter"), h))
    lines = ["| method | " + " | ".join(f"{g} ({len(cs)})" for g, cs in gs.items()) + " |",
             "|---|" + "---|" * len(gs)]
    for h in names:
        cells = []
        for g, cs in gs.items():
            s = scores(cs, hits[h])
            cells.append("–" if s is None else f"{s['ac1']:.2f} / {s['avg5']:.2f}" + (f" ({s['missing']} n/a)" if s["missing"] else "")
                         + (f" (on {s['n']})" if s["n"] != len(cs) else ""))
        lines.append(f"| {'**' + h + '**' if h.startswith('Hypocenter') else h} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def re2_tt_check(meta: dict, hits: dict) -> str:
    """Our runs of their baselines against their published RE2 Train Ticket numbers, averaged over fault types
    the way Table 6 averages them."""
    tt = [c for c, m in meta.items() if m["suite"] == "RE2" and m["system"] == "tt"]
    lines = ["| method | AC@1 ours | AC@1 published | Avg@5 ours | Avg@5 published |", "|---|---|---|---|---|"]
    for h in sorted(hits, key=lambda h: (not h.startswith("Hypocenter"), h)):
        per = [scores([c for c in tt if meta[c]["fault"] == f], hits[h]) for f in FAULT_ORDER]
        per = [p for p in per if p]
        if not per:
            continue
        ac1 = sum(p["ac1"] for p in per) / len(per)
        avg5 = sum(p["avg5"] for p in per) / len(per)
        pub = PUBLISHED_RE2_TT.get(h)
        lines.append(f"| {h} | {ac1:.2f} | {pub[0] if pub else '–'} | {avg5:.2f} | {pub[2] if pub else '–'} |")
    for h, pub in PUBLISHED_RE2_TT.items():
        if h not in hits:
            lines.append(f"| {h} (not run here) | – | {pub[0]} | – | {pub[2]} |")
    return "\n".join(lines)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The 95% Wilson score interval for k right out of n: honest at small n and near 0 or 1, unlike p ± 2 SE."""
    if not n:
        return 0.0, 0.0
    p = k / n
    mid, half = (p + z * z / (2 * n)) / (1 + z * z / n), z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, mid - half), min(1.0, mid + half)


def intervals(meta: dict, hits: dict) -> str:
    """AC@1 with its 95% interval, on all 360 failures and on the held-out Train Ticket set: where two methods'
    intervals overlap, the data can't tell them apart."""
    gs = groups(meta)
    lines = ["| set | method | right first | AC@1 | 95% interval |", "|---|---|---|---|---|"]
    for g in ("All 360", "RE2-TT"):
        for h in sorted(hits, key=lambda h: (not h.startswith("Hypocenter"), h)):
            rows = [hits[h].get(c) for c in gs[g] if c in hits[h]]
            if not rows or len(rows) != len(gs[g]):
                continue
            k, n = sum(bool(r and r[0]) for r in rows), len(rows)
            lo, hi = wilson(k, n)
            lines.append(f"| {g} | {h} | {k} of {n} | {k / n:.2f} | {lo:.2f}–{hi:.2f} |")
    return "\n".join(lines)


def confidence(meta: dict, hits: dict, method: str = "Hypocenter (metrics)") -> str:
    """How often the verdict was right at each confidence the skeptic gave it, on the real failures. A confident
    wrong blame is a wrong first answer given with high confidence."""
    lines = ["| confidence | cases | right first | wrong first | right, 95% interval |", "|---|---|---|---|---|"]
    levels = [("high", {"high"}), ("medium", {"medium"}), ("low", {"low"}), ("high or medium", {"high", "medium"})]
    for name, keep in levels:
        cs = [c for c, m in meta.items() if m.get("confidence") in keep and c in hits[method]]
        k, n = sum(hits[method][c][0] for c in cs), len(cs)
        lo, hi = wilson(k, n)
        lines.append(f"| {name} | {n} | {k} | {n - k} | {lo:.2f}–{hi:.2f} |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=DATA / "comparison.json")
    a = ap.parse_args()
    meta, hits = load()
    print("AC@1 / Avg@5 per dataset\n")
    print(table(meta, hits))
    print("\nRE2 Train Ticket, averaged over fault types, against RCAEval's published Table 6\n")
    print(re2_tt_check(meta, hits))
    print("\nAC@1 with 95% Wilson intervals\n")
    print(intervals(meta, hits))
    if any("confidence" in m for m in meta.values()):
        print("\nHypocenter (metrics) on all 360: right first at each confidence the skeptic gave\n")
        print(confidence(meta, hits))
    out = {g: {h: scores(cs, hits[h]) for h in hits} for g, cs in groups(meta).items()}
    a.json.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
