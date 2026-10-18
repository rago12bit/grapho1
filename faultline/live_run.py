"""`make live-run`: the four agents played by Claude on one incident, written down as a transcript.

Needs ANTHROPIC_API_KEY in your environment (it is never written anywhere) and spends real tokens: one
investigation is roughly ten model requests. It runs on scratch graphs (prefix livecheck_), dropped afterwards.

    ANTHROPIC_API_KEY=... make live-run                 # Kestrel INC-302
    ANTHROPIC_API_KEY=... make live-run INC=INC-301

The transcript (docs/live-run-<incident>.md) shows every tool each agent chose, what it was told, the findings,
which claims the graph backed, whether the model agreed with the deterministic judge, and the tokens used.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import agent, db, seed, tools
from .db import Graph, client

OUT = Path(__file__).resolve().parents[1] / "docs"


def _short(x, n: int = 160) -> str:
    s = json.dumps(x, default=str) if not isinstance(x, str) else x
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("incident", nargs="?", default="INC-302")
    ap.add_argument("--tenant", default="kestrel")
    a = ap.parse_args()
    if not agent.claude_available():
        print("ANTHROPIC_API_KEY is not set: nothing was run. Export it in this shell, then run make live-run again.")
        return 2
    if not db.GRAPH_PREFIX.startswith("livecheck"):
        print("refusing to run: set FAULTLINE_GRAPH_PREFIX=livecheck_ so the demo graphs are left alone")
        return 2
    seed.build(a.tenant, force=True)
    t0 = time.perf_counter()
    try:
        out = agent.investigate(a.tenant, a.incident)
    finally:
        took = time.perf_counter() - t0
        for name in client().list_graphs():
            if name.startswith(db.GRAPH_PREFIX):
                client().connection.execute_command("GRAPH.DELETE", name)
    if out["mode"] != "claude":
        print(f"the model was not reached ({out.get('fallback_reason')}); the offline agents answered instead")
        return 1
    judge = tools._label(out["ranking"]["suspects"][0]) if out["ranking"]["suspects"] else "nothing"
    f = out["findings"] or {}
    check = out["claim_check"]
    lines = [f"# Live run: {a.tenant} {a.incident}, four agents played by {out['model']}", "",
             f"Run on {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} with `make live-run`, {took:.0f} s, "
             f"{out['usage']['requests']} model requests, {out['usage']['input_tokens']:,} input tokens "
             f"({out['usage']['cache_read_input_tokens']:,} read from cache), {out['usage']['output_tokens']:,} output tokens.", "",
             "## What each agent chose to do", "", "| # | agent | tool | input | result |", "|---|---|---|---|---|"]
    for i, st in enumerate(out["steps"], 1):
        res = f"error: {st['error']}" if st["error"] else _short(st["result"], 120)
        lines.append(f"| {i} | {st['role']} | `{st['tool']}` | `{_short(st['input'], 90)}` | {res.replace('|', '/')} |")
    lines += ["", "## Findings", "", f"- **Blamed:** {f.get('change_id') or f.get('root_cause_service')} "
              f"({f.get('confidence')} confidence). The deterministic judge leads with **{judge}**: "
              + ("they agree." if (f.get("change_id") or "") in judge or (f.get("root_cause_service") or "-") in judge else "they disagree."),
              f"- **Summary:** {f.get('summary', '')}", "",
              f"## Claims checked against the graph: {check['supported']} of {check['total']} backed", ""]
    for c in check["claims"]:
        lines.append(f"- {'✓' if c['ok'] else '✗'} {c['text']}" + (f" ({'; '.join(c['problems'])})" if c.get("problems") else ""))
    lines += ["", "Claims the graph can't back are dropped from the evidence before anyone sees it."]
    path = OUT / f"live-run-{a.incident}.md"
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:4]))
    print(f"\nwrote {path.relative_to(OUT.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
