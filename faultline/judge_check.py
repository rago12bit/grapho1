"""The checks behind `make judge-check`, on scratch graphs: each prints PASS or FAIL with what it saw.

Run inside the app image with a scratch prefix, so the demo graphs are never touched:

    docker compose run --rm -e FAULTLINE_GRAPH_PREFIX=judgecheck_ app python -m faultline.judge_check

No API key, nothing remote. Every graph it makes is dropped at the end.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from . import agent, db, seed, site, tools
from .db import Graph, client

# what the offline agents should conclude for each Kestrel incident (README, "Five incidents")
EXPECTED = {"INC-301": "C-5101", "INC-302": "D-5201", "INC-303": "catalog-es (no change)",
            "INC-304": "C-5101", "INC-305": "k8s-node-4 (shared host)"}
README = Path(__file__).resolve().parents[1] / "README.md"
rows: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        t = time.perf_counter()
        try:
            ok, detail = fn()
        except Exception as e:  # a check that crashes is a failed check, with the reason
            ok, detail = False, f"{type(e).__name__}: {e}"
        rows.append((name, ok, f"{detail} ({time.perf_counter() - t:.1f} s)"))
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)
        return fn
    return wrap


def main() -> int:
    if not db.GRAPH_PREFIX.startswith("judgecheck"):
        print("refusing to run: set FAULTLINE_GRAPH_PREFIX=judgecheck_ so the demo graphs are left alone")
        return 2
    seed.build("kestrel", force=True)
    g = Graph("kestrel")

    @check("FalkorDB engine")
    def _():
        e = site.engine()
        return bool(e["version"]), f"FalkorDB {e['version']}, ping {e['ping_ms']} ms"

    @check("agents name each Kestrel cause")
    def _():
        got = {}
        for inc, want in EXPECTED.items():
            r = agent.investigate_offline("kestrel", inc, propose=False)
            got[inc] = tools._label(r["ranking"]["suspects"][0])
        right = sum(got[i] == w for i, w in EXPECTED.items())
        return right == len(EXPECTED), f"{right}/{len(EXPECTED)} right: " + ", ".join(f"{i} {got[i]}" for i in EXPECTED)

    @check("depth 1 vs full depth")
    def _():
        s = tools.depth_sweep(g, "INC-301")
        one = s["depths"][0]["leader"]
        return (one == "payments (no change)" and s["final"] == "C-5101" and s["settles_at"] == 2,
                f"INC-301 at depth 1 blames {one}; from depth {s['settles_at']} {s['final']}")

    @check("a name not in the graph")
    def _():
        a = agent.ask_offline("kestrel", "what breaks if billing-svc goes down?")
        return bool(a.get("not_in_graph")) and "not that it is safe" in a["answer"], a["answer"][:90] + "…"

    @check("fixes proved on graph copies")
    def _():
        p = site.prove_fixes(g)
        held = sum(x["holds"] for x in p)
        return held == len(p), f"{held}/{len(p)} hold, decoy clears {sum(len(x['predicted_clear']) for x in p if x.get('decoy'))} alerts"

    @check("break-it experiments move the verdict")
    def _():
        moved = [site.break_it(g, e["id"])["moved"] for e in site.EXPERIMENTS]
        left = [t for t in tools_graphs() if "break_" in t or "proof_" in t]
        return all(moved) and not left, f"{sum(moved)}/{len(moved)} moved, {len(left)} copies left behind"

    @check("planted-fault eval matches the README")
    def _():
        out = subprocess.run([sys.executable, "-m", "faultline.eval", "--n", "150", "--seed", "7", "--json", "/tmp/jc_eval.json"],
                             capture_output=True, text=True, timeout=600)
        if out.returncode:
            return False, out.stderr.strip()[-200:]
        top1 = json.loads(Path("/tmp/jc_eval.json").read_text())["phase1"]["top1"]["full + skeptic"]["all"]
        m = re.search(r"\| \*\*Hypocenter \(judge \+ skeptic\)\*\* \|(?:[^|]*\|){5} \*\*([\d.]+)%\*\*", README.read_text())
        want = float(m.group(1)) if m else None
        return want is not None and abs(top1 - want) < 0.05, f"seed 7, 150 scenarios: {top1}% top-1 (README: {want}%)"

    @check("concurrent investigations stay isolated")
    def _():
        out = subprocess.run([sys.executable, "-m", "faultline.bench", "--runs", "24", "--workers", "8"],
                             capture_output=True, text=True, timeout=600).stdout
        ok = "0 cross-run handoff reads" in out and "0 verdicts differ" in out
        return ok, " ".join(out.strip().splitlines()[-2:])[:160]

    for name in tools_graphs():
        client().connection.execute_command("GRAPH.DELETE", name)
    failed = [r for r in rows if not r[1]]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} checks passed")
    return 1 if failed else 0


def tools_graphs() -> list[str]:
    return [n for n in client().list_graphs() if n.startswith(db.GRAPH_PREFIX)]


if __name__ == "__main__":
    sys.exit(main())
