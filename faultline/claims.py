"""Every number the README tags, checked against what measured it.

A claim in the README is wrapped in a tag that names where its value comes from:

    **<!-- claim:real.ac1 -->0.81<!-- /claim -->** right service first over all 360 RCAEval failures

GitHub hides the comments, so the page reads the same. Each key below computes the text the tag must contain, from
the file that published the measurement (docs/rcaeval-results.md from `make eval`, docs/scale.md from
`python -m faultline.scale`, docs/mutants.txt from `make mutants`, the README's own evaluation section) or from the
code itself. A number that drifts from its source, a tag with no key, or a key no tag uses is a failure.

    make claims                                 # the file-based claims (no database)
    pytest tests/test_claims.py                 # those, plus the live ones, on a freshly seeded graph

Live claims (marked below) are measured on a seeded Kestrel graph; `make claims` lists them as skipped.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Callable

from . import results, tools

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TAG = re.compile(r"<!-- claim:([\w.-]+) -->(.*?)<!-- /claim -->", re.S)


def strip(text: str) -> str:
    """The README with its claim tags removed, for anything that parses its tables or prose."""
    return re.sub(r"<!-- /?claim(?::[\w.-]+)? -->", "", text)


def _rca(method: str, dataset: str) -> list[float]:
    r = results.rcaeval((ROOT / "docs" / "rcaeval-results.md").read_text())
    i = r["datasets"].index(dataset)
    return next(m for m in r["methods"] if m["name"] == method)["cells"][i]


def _scale(services: int, query: str) -> float:
    run = next(x for x in results.scale((ROOT / "docs" / "scale.md").read_text()) if x["services"] == services)
    return next(q for q in run["queries"] if q["query"].startswith(query))["p50_ms"]


def _size(services: int, what: str) -> int:
    return next(x for x in results.scale((ROOT / "docs" / "scale.md").read_text()) if x["services"] == services)[what]


def _seeds() -> list[float]:
    return [s["skeptic"] for s in results.readme(strip(README.read_text()))["held_out_seeds"]]


def _mutants() -> str:
    m = re.search(r"(\d+) of (\d+) mutants caught", (ROOT / "docs" / "mutants.txt").read_text())
    return f"{m.group(1)} of {m.group(2)}"


def _pair(method: str, dataset: str) -> str:
    a, b = _rca(method, dataset)
    return f"{a:.2f} / {b:.2f}"


CLAIMS: dict[str, Callable[[], str]] = {
    "real.ac1": lambda: f"{_rca('Hypocenter (metrics)', 'All 360')[0]:.2f}",
    "real.nsigma": lambda: f"{_rca('NSigma', 'All 360')[0]:.2f}",
    "real.circa": lambda: f"{_rca('CIRCA', 'All 360')[0]:.2f}",
    "real.earliest": lambda: f"{_rca('Earliest alert (metrics)', 'All 360')[0]:.2f}",
    "real.baro": lambda: f"{_rca('BARO', 'All 360')[0]:.2f}",
    "heldout.ac1": lambda: f"{_rca('Hypocenter (metrics)', 'RE2-TT')[0]:.2f}",
    "heldout.avg5": lambda: f"{_rca('Hypocenter (metrics)', 'RE2-TT')[1]:.2f}",
    "heldout.nsigma": lambda: _pair("NSigma", "RE2-TT"),
    "heldout.nsigma_ac1": lambda: f"{_rca('NSigma', 'RE2-TT')[0]:.2f}",
    "heldout.baro": lambda: _pair("BARO", "RE2-TT"),
    "planted.range": lambda: f"{min(_seeds())}–{max(_seeds())}%",
    "scale.judge_3000": lambda: f"{round(_scale(3000, 'the judge')):d} ms",
    "scale.judge_1000": lambda: f"{_scale(1000, 'the judge'):g} ms",
    "scale.bfs_3000": lambda: f"{round(_scale(3000, 'dependencies')):d} ms",
    "scale.nodes_3000": lambda: f"{round(_size(3000, 'nodes'), -3):,}",
    "scale.edges_3000": lambda: f"{round(_size(3000, 'edges'), -3):,}",
    "scale.nodes_3000_k": lambda: f"{round(_size(3000, 'nodes') / 1000):d}k",
    "mutants": _mutants,
    "gate.checks": lambda: str(len(tools.GATE)),
    "tests": lambda: str(__import__("faultline.api", fromlist=["PROOF"]).PROOF["tests"]),   # itself checked by a test
}


def _casefile_queries() -> str:
    from . import agent
    return f"{agent.preview('kestrel', 'INC-302')['graph']['queries']} queries"


def _kestrel_confirmed() -> str:
    from . import agent
    from .db import Graph
    g = Graph("kestrel")
    open_ = tools.list_incidents(g)
    return f"{sum(agent.gate(g, i['id'])['verdict'] == 'confirmed' for i in open_)} of {len(open_)}"


# measured on a seeded Kestrel graph
LIVE: dict[str, Callable[[], str]] = {
    "casefile.queries": _casefile_queries,
    "kestrel.confirmed": _kestrel_confirmed,
}


def tags(text: str | None = None) -> list[tuple[str, str]]:
    return [(k, v.strip("*").strip()) for k, v in TAG.findall(README.read_text() if text is None else text)]


def check(live: bool = False, text: str | None = None) -> list[dict]:
    """One row per tag: its key, what the README says, what the source says, and whether they agree. Keys no tag
    uses come back as rows too, so a claim can't quietly fall out of the README."""
    known = {**CLAIMS, **LIVE}
    rows, used = [], set()
    for key, said in tags(text):
        used.add(key)
        if key not in known:
            rows.append({"key": key, "readme": said, "source": None, "ok": False, "why": "no such claim key"})
            continue
        if key in LIVE and not live:
            rows.append({"key": key, "readme": said, "source": None, "ok": None, "why": "live: checked by tests/test_claims.py"})
            continue
        want = known[key]()
        rows.append({"key": key, "readme": said, "source": want, "ok": said == want, "why": "" if said == want else "drifted"})
    for key in sorted(set(known) - used):
        rows.append({"key": key, "readme": None, "source": None, "ok": False, "why": "no tag in the README uses it"})
    return rows


def main() -> int:
    rows = check(live="--live" in sys.argv)
    for r in rows:
        mark = {True: "ok  ", False: "FAIL", None: "skip"}[r["ok"]]
        print(f"{mark}  {r['key']:<20} README {r['readme']!r:<16} source {r['source']!r:<16} {r['why']}")
    bad = [r for r in rows if r["ok"] is False]
    print(f"\n{sum(r['ok'] is True for r in rows)} claims match their source, {len(bad)} don't, "
          f"{sum(r['ok'] is None for r in rows)} live claims left to the tests")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
