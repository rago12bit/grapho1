"""Concurrency benchmark for agent memory (Track 02): many investigations at once on one graph.

    docker compose run --rm app python -m faultline.bench --runs 40 --workers 8

Copies the demo graph with GRAPH.COPY (the demo is never touched), then runs the four-agent playbook on the open
incidents in parallel. Every run has its own run id, and its agents hand off only through Episode nodes. Checks:
  - isolation: no Episode reads (READ) an Episode from another run, and every run has its four handoffs
  - consistency: every run reaches the same verdict as a single run on its own
Reports throughput, latency percentiles and how many queries had to be retried because FalkorDB was busy.
"""
from __future__ import annotations

import argparse
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import agent, db, seed, tools
from .db import Graph, client, graph_name

INCIDENTS = ["INC-301", "INC-302", "INC-303", "INC-304"]


def _verdict(out: dict) -> str | None:
    return out["findings"].get("change_id") or out["findings"].get("root_cause_service")


def run(runs: int = 40, workers: int = 8, source: str = "kestrel") -> dict:
    seed.build(source, force=False)
    tenant = "bench_" + uuid.uuid4().hex[:8]
    client().connection.execute_command("GRAPH.COPY", graph_name(source), graph_name(tenant))
    try:
        reference = {i: _verdict(agent.investigate_offline(tenant, i, propose=False)) for i in INCIDENTS}
        jobs = [(INCIDENTS[k % len(INCIDENTS)], f"BENCH-{k:04d}-{uuid.uuid4().hex[:6]}") for k in range(runs)]
        retries_before = db.retry_count

        def one(job):
            inc, rid = job
            t = time.perf_counter()
            out = agent.investigate_offline(tenant, inc, propose=False, run_id=rid)
            return inc, rid, _verdict(out), time.perf_counter() - t

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(one, jobs))
        wall = time.perf_counter() - t0

        g = Graph(tenant)
        crossed = g.read("""
            MATCH (e:Episode)-[:READ]->(p:Episode) WHERE e.run STARTS WITH 'BENCH-' AND e.run <> p.run
            RETURN count(*) AS n""")[0]["n"]
        per_run = {r["run"]: r["n"] for r in g.read("""
            MATCH (e:Episode) WHERE e.run STARTS WITH 'BENCH-' RETURN e.run AS run, count(e) AS n""")}
        incomplete = [rid for _, rid, _, _ in results if per_run.get(rid) != len(tools.ROLES)]
        mismatched = [(inc, rid) for inc, rid, v, _ in results if v != reference[inc]]
        lat = sorted(r[3] for r in results)
        return {
            "runs": runs, "workers": workers, "wall_s": round(wall, 2), "runs_per_s": round(runs / wall, 2),
            "p50_s": round(statistics.median(lat), 3), "p95_s": round(lat[int(0.95 * (len(lat) - 1))], 3),
            "retries": db.retry_count - retries_before, "cross_run_reads": crossed,
            "incomplete_runs": len(incomplete), "verdict_mismatches": len(mismatched), "reference": reference,
        }
    finally:
        Graph(tenant).drop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=40)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    r = run(a.runs, a.workers)
    print(f"{r['runs']} investigations, {r['workers']} at a time: {r['wall_s']} s, {r['runs_per_s']} runs/s, "
          f"p50 {r['p50_s']} s, p95 {r['p95_s']} s, {r['retries']} retried queries")
    print(f"isolation: {r['cross_run_reads']} cross-run handoff reads, {r['incomplete_runs']} incomplete runs; "
          f"consistency: {r['verdict_mismatches']} verdicts differ from a single run")


if __name__ == "__main__":
    main()
