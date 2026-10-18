"""`make mutants`: break each safeguard on purpose, one at a time, and check a test notices.

Every mutant below is one exact edit to the code. It is applied to a scratch copy of the repository, never to the
files you have, and the tests that guard it run against that copy. A mutant is *caught* when a test fails. A
mutant whose line no longer exists in the code is reported as stale, so the list can't quietly rot.

    docker compose run --rm app python -m faultline.mutants          # all of them, ~3 minutes
    docker compose run --rm app python -m faultline.mutants depth-cap  # just one

Tests run on graphs prefixed mutant_, which are dropped at the end; the demo graphs are never touched.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY = ("faultline", "tests", "web", "docs", "README.md")

# id, what breaking it would mean, file, exact text, replacement, the tests that should notice
MUTANTS: list[tuple[str, str, str, str, str, list[str]]] = [
    ("depth-cap", "the depth limit is ignored, so depth 1 sees everything",
     "faultline/tools.py", "deps = dependencies(g, sym, depth)", "deps = dependencies(g, sym)",
     ["tests/test_depth_and_unknowns.py"]),
    ("not-in-graph", "a name the graph doesn't have gets the help text, not 'not modelled'",
     "faultline/agent.py", "                return _not_in_graph(tenant, asked)\n", "                pass\n",
     ["tests/test_depth_and_unknowns.py"]),
    ("bfs-cycles", "BFS forgets a service on a call cycle depends on itself (Train Ticket)",
     "faultline/tools.py", "    if loop and min(loop) <= max_hops:\n        out.append(", "    if False:\n        out.append(",
     ["tests/test_algorithms.py"]),
    ("msf-direction", "the spread tree's links lose their timing order",
     "faultline/tools.py", "early, late = (a, b) if (first[a], a) <= (first[b], b) else (b, a)", "early, late = a, b",
     ["tests/test_algorithms.py"]),
    ("alert-order", "alerts climbing the path in the wrong order count as in order",
     "faultline/tools.py", "    return all(x <= y for x, y in zip(times, times[1:]))", "    return True",
     ["tests/test_faultline.py"]),
    ("climbed-past", "a change the failure climbed past is no longer marked down",
     "faultline/tools.py", "            chain *= 0.5  # the failure", "            chain *= 1.0  # the failure",
     ["tests/test_faultline.py", "tests/test_case_file.py"]),
    ("not-the-cause", "a person's 'not the cause' no longer drops the suspect",
     "faultline/tools.py", "                s[\"score\"] = 0.0          # a person", "                pass                      # a person",
     ["tests/test_learning.py", "tests/test_site_pages.py"]),
    ("skeptic-mute", "the skeptic can never overturn the leader",
     "faultline/tools.py", "            break\n        lead = better\n", "            break\n        break\n",
     ["tests/test_faultline.py", "tests/test_case_file.py"]),
    ("claim-service", "'C-5101 changed auth' passes although C-5101 changed payments-db",
     "faultline/tools.py", "            out.append(f\"{cid} changed {real}, not {svc}\")", "            pass",
     ["tests/test_case_file.py", "tests/test_faultline.py"]),
    ("claim-ruled-out", "a ruled-out change can be called the cause",
     "faultline/tools.py", "            out.append(f\"{cid} cannot be the cause: {sem['ruled'][cid]}\")", "            pass",
     ["tests/test_case_file.py", "tests/test_faultline.py"]),
    ("claim-order", "'A alerted before B' passes when the times say otherwise",
     "faultline/tools.py", "elif (rel == \"before\" and not ta < tb)", "elif False and (rel == \"before\" and not ta < tb)",
     ["tests/test_case_file.py", "tests/test_faultline.py"]),
    ("verify-always", "a fix is 'verified' even when its alerts keep firing",
     "faultline/tools.py", "verdict = \"verified\" if all_clear or held else", "verdict = \"verified\" if True else",
     ["tests/test_faultline.py", "tests/test_proof.py"]),
    ("dup-proposals", "re-running an investigation stacks duplicate fixes",
     "faultline/tools.py", "    if same:  # re-running", "    if False:  # re-running",
     ["tests/test_faultline.py"]),
    ("free-cypher-writes", "the free-form Cypher tool accepts writes",
     "faultline/db.py", "    if _WRITE_WORDS.search(cypher):\n        raise", "    if False:\n        raise",
     ["tests/test_faultline.py"]),
    ("tenant-names", "a tenant id like ../kestrel reaches another graph",
     "faultline/db.py", "    if not re.fullmatch(r\"[a-z][a-z0-9_]{0,31}\", tenant):", "    if False:",
     ["tests/test_faultline.py"]),
    ("answer-key", "the judge imports the simulator's answer key",
     "faultline/tools.py", "from .db import Graph, assert_read_only\n", "from .db import Graph, assert_read_only\nfrom . import world  # noqa: F401\n",
     ["tests/test_case_file.py"]),
    ("webhook-token", "on a public server anyone can post alerts and changes",
     "faultline/api.py", "if path.endswith((\"/alerts/alertmanager\", \"/changes\")) and (",
     "if False and path.endswith((\"/alerts/alertmanager\", \"/changes\")) and (",
     ["tests/test_live.py"]),
    ("public-writes", "the public MCP endpoint offers propose_action",
     "faultline/mcp_server.py", "if os.environ.get(\"FAULTLINE_PUBLIC\") != \"1\":", "if True:",
     ["tests/test_live.py"]),
    ("break-copy", "a Break-it experiment leaves its graph copy behind",
     "faultline/site.py", "    finally:\n        sandbox.drop()", "    finally:\n        pass",
     ["tests/test_site_pages.py"]),
    ("proof-copy", "a fix proof leaves its graph copy behind",
     "faultline/proof.py", "    finally:\n        sandbox.drop()", "    finally:\n        pass",
     ["tests/test_proof.py"]),
    ("follow-up-memory", "'who owns it?' forgets what the conversation was about",
     "faultline/agent.py", "        mentioned = [previous]\n", "        pass\n",
     ["tests/test_conversation.py"]),
    ("chat-mentions-case", "a message about 'Payments' the team is filed under the payments service",
     "faultline/chat.py", "    found = [w for w in re.findall(r\"[A-Za-z0-9][\\w.-]*[A-Za-z0-9]|[A-Za-z0-9]\", text) if w in names]",
     "    found = [w.lower() for w in re.findall(r\"[A-Za-z0-9][\\w.-]*[A-Za-z0-9]|[A-Za-z0-9]\", text) if w.lower() in names]",
     ["tests/test_chat.py"]),
    ("role-tools", "an agent can call a tool its role doesn't have",
     "faultline/agent.py", "            if u.name not in names:", "            if False:",
     ["tests/test_claude_path.py"]),
    ("keep-false-claims", "claims Claude makes that the graph can't back stay in the findings",
     "faultline/agent.py", "if e not in bad]", "if True]",
     ["tests/test_claude_path.py"]),
    ("proof-decoy", "the live fix proofs call a fix that predicts nothing 'held'",
     "faultline/site.py", "(bool(p[\"predicted_clear\"]) != bool(f.get(\"decoy\")))", "True",
     ["tests/test_results.py"]),
]


def _copy(dst: Path) -> None:
    for name in COPY:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dst / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst / name)


def _pytest(cwd: Path, targets: list[str]) -> tuple[int, str]:
    env = {**os.environ, "FAULTLINE_GRAPH_PREFIX": "mutant_", "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *targets],
                       cwd=cwd, env=env, capture_output=True, text=True, timeout=900)
    failed = re.search(r"^FAILED (\S+)", p.stdout, re.M)
    return p.returncode, failed.group(1) if failed else p.stdout.strip().splitlines()[-1] if p.stdout.strip() else p.stderr[-200:]


def _drop_scratch() -> None:
    from .db import client
    for name in client().list_graphs():
        if name.startswith("mutant_"):
            client().connection.execute_command("GRAPH.DELETE", name)


def main(only: list[str]) -> int:
    chosen = [m for m in MUTANTS if not only or m[0] in only]
    targets = sorted({t for m in chosen for t in m[5]})
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "base"
        base.mkdir()
        _copy(base)
        code, detail = _pytest(base, targets)            # the unbroken code must pass, or a "catch" means nothing
        if code != 0:
            print(f"the unbroken code fails its own tests ({detail}); fix that first")
            return 2
        print(f"unbroken: {len(targets)} test files pass\n")
        rows = []
        for mid, meaning, path, old, new, tests in chosen:
            work = Path(tmp) / mid
            shutil.copytree(base, work)
            f = work / path
            text = f.read_text()
            if text.count(old) != 1:
                rows.append((mid, "STALE", f"the line to break appears {text.count(old)} times in {path}", meaning))
            else:
                f.write_text(text.replace(old, new))
                t = time.perf_counter()
                code, detail = _pytest(work, tests)
                status = "caught" if code == 1 else "MISSED" if code == 0 else f"ERROR {code}"
                rows.append((mid, status, f"{detail} ({time.perf_counter() - t:.0f} s)", meaning))
            print(f"{rows[-1][1]:7} {mid:20} {rows[-1][3]}\n        {rows[-1][2]}", flush=True)
            shutil.rmtree(work)
    _drop_scratch()
    caught = sum(r[1] == "caught" for r in rows)
    print(f"\n{caught} of {len(rows)} mutants caught")
    return 0 if caught == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
