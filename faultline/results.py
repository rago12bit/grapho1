"""Every measured number, read from the files that publish it, for the Results page.

Nothing here is typed in twice: the RCAEval tables come from docs/rcaeval-results.md (written by `make eval`), the
scale runs from docs/scale.md (written by `python -m faultline.scale`), and the planted-fault eval, the held-out
history and the learning table from the README's own tables. If a number changes there, the page changes with it.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tables(text: str) -> list[list[list[str]]]:
    """Every markdown table in `text`, as rows of cells (header first, the --- line dropped)."""
    out, cur = [], []
    for line in text.splitlines():
        line = line.strip()                                 # tables nested in a list item are indented
        if line.startswith("|"):
            cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
            if not all(set(c) <= set("-: ") for c in cells):
                cur.append(cells)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _table(text: str, first: str) -> list[list[str]]:
    for t in _tables(text):
        if t[0][0].startswith(first):
            return t
    raise KeyError(first)


def _num(s: str) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return float(m.group()) if m and s.strip() not in ("–", "-") else None


def _pair(s: str) -> list[float | None]:
    xs = re.findall(r"\d\.\d+", s)
    return [float(xs[0]), float(xs[1])] if len(xs) >= 2 else [None, None]


def rcaeval(doc: str) -> dict:
    t = _table(doc, "method")
    sets = [re.sub(r"\s*\(\d+\)", "", h) for h in t[0][1:]]
    sizes = [int(m.group(1)) if (m := re.search(r"\((\d+)\)", h)) else None for h in t[0][1:]]
    methods = [{"name": r[0], "cells": [_pair(c) for c in r[1:]],
                "note": (m.group(1) if (m := re.search(r"\(on (\d+)\)", r[-1])) else None)} for r in t[1:]]
    tables = [x for x in _tables(doc) if x[0][0] == "method"]   # the per-dataset table, then the paper's Table 6
    published = [{"name": r[0], "ac1_ours": _num(r[1]), "ac1_published": _num(r[2]),
                  "avg5_ours": _num(r[3]), "avg5_published": _num(r[4])} for r in tables[1][1:]] if len(tables) > 1 else []
    return {"datasets": sets, "sizes": sizes, "methods": methods, "re2_tt_published": published}


def scale(doc: str) -> list[dict]:
    runs = []
    for part in re.split(r"^## ", doc, flags=re.M)[1:]:
        head = part.splitlines()[0]
        m = re.search(r"([\d,]+) services · ([\d,]+) nodes · ([\d,]+) edges", part)
        planted = re.search(r"planted change first in (\d+) \((\d+)%\), in the top three in (\d+)", part)
        total = re.search(r"Judge on the (\d+) planted incidents", part)
        rows = _table(part, "query")[1:]
        runs.append({"title": head.strip(), "services": int(m.group(1).replace(",", "")),
                     "nodes": int(m.group(2).replace(",", "")), "edges": int(m.group(3).replace(",", "")),
                     "queries": [{"query": r[0], "runs": int(r[1]), "p50_ms": _num(r[2]), "p95_ms": _num(r[3])} for r in rows],
                     "planted": {"incidents": int(total.group(1)), "first": int(planted.group(1)),
                                 "top3": int(planted.group(3))} if planted and total else None})
    return runs


def readme(doc: str) -> dict:
    syn = next(t for t in _tables(doc) if t[0][1].startswith("deep"))
    kinds = [re.sub(r"\s*\(\d+\)", "", h) for h in syn[0][1:]]
    synthetic = [{"name": r[0], "values": [_num(c) for c in r[1:]]} for r in syn[1:]]
    conf = re.search(r"\*\*high\*\* confidence \((\d+) cases\): right ([\d.]+)%; \*\*medium\*\* confidence \((\d+) cases\): right ([\d.]+)%; "
                     r"\*\*low\*\* confidence \((\d+) cases\): right ([\d.]+)%", doc)
    seeds = [{"seed": int(s), "judge": float(a), "skeptic": float(b)}
             for a, b, s in re.findall(r"([\d.]+)% → ([\d.]+)% \(seed (\d+)\)", doc)]
    learning = re.search(r"top-1 ([\d.]+)% → ([\d.]+)%\. Without the precedent factor the second round scores ([\d.]+)%", doc)
    learned = next(t for t in _tables(doc) if t[0][0].startswith("open incidents"))
    held = _table(doc, "RE2 Train Ticket (90 held-out cases)")
    return {
        "synthetic": {"kinds": kinds, "rows": synthetic},
        "confidence": [{"label": l, "cases": int(n), "right": float(p)} for l, n, p in
                       zip(("high", "medium", "low"), conf.group(1, 3, 5), conf.group(2, 4, 6))] if conf else [],
        "held_out_seeds": seeds,
        "learning_rerun": {"before": float(learning.group(1)), "after": float(learning.group(2)),
                           "no_precedent": float(learning.group(3))} if learning else None,
        "learned": [{"company": re.sub(r"\s*\(\d+\)", "", r[0]), "open": int(re.search(r"\((\d+)\)", r[0]).group(1)),
                     "hand": int(r[1]), "learned": int(r[2]),
                     "brier": [float(x) for x in re.findall(r"\d\.\d+", r[3])]} for r in learned[1:]],
        "held_out_tt": [{"name": r[0], "ac1": _num(r[1]), "avg5": _num(r[2])} for r in held[1:]],
    }


@lru_cache(maxsize=1)
def _load(stamp: tuple) -> dict:
    out = {"rcaeval": rcaeval((ROOT / "docs" / "rcaeval-results.md").read_text())}
    out["scale"] = scale((ROOT / "docs" / "scale.md").read_text())
    out.update(readme((ROOT / "README.md").read_text()))
    return out


def load() -> dict:
    files = [ROOT / "docs" / "rcaeval-results.md", ROOT / "docs" / "scale.md", ROOT / "README.md"]
    return _load(tuple(f.stat().st_mtime for f in files))
