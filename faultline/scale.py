"""Scale benchmark: does the graph still answer quickly, and the judge still find the cause, at 1,000 services?

    docker compose run --rm app python -m faultline.scale                     # 1,000 services
    docker compose run --rm app python -m faultline.scale --services 3000     # bigger

Builds a synthetic company in its own graph (`scalebench`, dropped afterwards): services in layers (entry points,
backends-for-frontends, domain services, shared platform, datastores) each calling 2-5 below it; five operations per
service with operation-level calls; a deployment, 2-3 pods and a node per pod; teams and people; 90 days of
deploys and config changes; background alerts; and incidents with planted causes, the same way the synthetic eval
plants them (a change 1-3 hops below the symptom, alerts climbing to it, distractors around it). Then it times every
query the agents use, many times each, and checks how often the judge names the planted cause.

Everything here is invented. The numbers say how FalkorDB and the judge behave at this size on this machine, not
how a real company's graph would look.
"""
from __future__ import annotations

import argparse
import random
import statistics
import time
from pathlib import Path

from . import tools
from .db import Graph
from .seed import DAY, HOUR, MIN, NOW, _write_indexes

TENANT = "scalebench"
LAYERS = [("edge", 0.03), ("bff", 0.07), ("domain", 0.45), ("platform", 0.25), ("store", 0.20)]
BATCH = 5000


def _batches(g: Graph, cypher: str, rows: list[dict]) -> None:
    for i in range(0, len(rows), BATCH):
        g.write(cypher, {"rows": rows[i:i + BATCH]})


def build(services: int = 1000, incidents: int = 200, seed: int = 1) -> dict:
    rng = random.Random(seed)
    g = Graph(TENANT)
    g.drop()
    _write_indexes(g)
    for label, prop in (("Operation", "id"), ("Deployment", "name"), ("Node", "name"), ("Pod", "name")):
        g.write(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    t0 = time.time()
    layer_of, names = {}, []
    for layer, share in LAYERS:
        for k in range(max(2, int(services * share))):
            n = f"{layer}-{k:04d}"
            names.append(n)
            layer_of[n] = layer
    by_layer = {lay: [n for n in names if layer_of[n] == lay] for lay, _ in LAYERS}
    order = [lay for lay, _ in LAYERS]
    teams = [f"team-{k:02d}" for k in range(max(4, len(names) // 25))]
    team_of = {n: teams[i % len(teams)] for i, n in enumerate(names)}
    calls = set()
    for n in names:
        i = order.index(layer_of[n])
        if i == len(order) - 1:
            continue
        below = [m for lay in order[i + 1:i + 3] for m in by_layer[lay]]
        for m in rng.sample(below, min(len(below), rng.randint(2, 5))):
            calls.add((n, m))
    calls = sorted(calls)          # a set's order changes between processes; the rest of the build must not
    people = [{"handle": f"p{k:04d}", "name": f"Person {k}", "team": teams[k % len(teams)], "on_call": k < len(teams)}
              for k in range(len(teams) * 7)]
    g.write("UNWIND $t AS t CREATE (:Team {id: t, name: t})", {"t": teams})
    _batches(g, "UNWIND $rows AS p MATCH (t:Team {id: p.team}) CREATE (:Person {handle: p.handle, name: p.name, on_call: p.on_call})-[:MEMBER_OF]->(t)", people)
    _batches(g, """UNWIND $rows AS s MATCH (t:Team {id: s.team})
                   CREATE (:Service {name: s.name, kind: s.kind, tier: s.tier, internet_exposed: s.edge})-[:OWNED_BY]->(t)""",
             [{"name": n, "team": team_of[n], "kind": "datastore" if layer_of[n] == "store" else "service",
               "tier": 1 + order.index(layer_of[n]) // 2, "edge": layer_of[n] == "edge"} for n in names])
    _batches(g, "UNWIND $rows AS c MATCH (a:Service {name: c.a}), (b:Service {name: c.b}) CREATE (a)-[:CALLS]->(b)",
             [{"a": a, "b": b} for a, b in calls])
    ops = [{"id": f"{n} op{k}", "service": n, "name": f"op{k}", "rps": round(rng.uniform(0.1, 50), 2)} for n in names for k in range(5)]
    _batches(g, """UNWIND $rows AS o MATCH (s:Service {name: o.service})
                   CREATE (s)-[:EXPOSES]->(:Operation {id: o.id, name: o.name, service: o.service, rps: o.rps})""", ops)
    op_calls = [{"a": f"{a} op{rng.randrange(5)}", "b": f"{b} op{rng.randrange(5)}", "rps": round(rng.uniform(0.1, 20), 2)}
                for a, b in calls for _ in range(3)]
    _batches(g, """UNWIND $rows AS c MATCH (a:Operation {id: c.a}), (b:Operation {id: c.b})
                   CREATE (a)-[:CALLS {rps: c.rps}]->(b)""", op_calls)
    nodes = [f"node-{k:03d}" for k in range(max(6, len(names) // 10))]
    g.write("UNWIND $n AS n CREATE (:Node {name: n, zone: 'zone-' + toString(toInteger(right(n, 1)) % 3)})", {"n": nodes})
    pods = [{"svc": n, "pod": f"{n}-{k}", "node": rng.choice(nodes)} for n in names for k in range(rng.randint(2, 3))]
    _batches(g, "UNWIND $rows AS s MATCH (x:Service {name: s}) CREATE (x)-[:DEPLOYED_AS]->(:Deployment {name: s})",
             [n for n in names])
    _batches(g, """UNWIND $rows AS p MATCH (d:Deployment {name: p.svc}), (n:Node {name: p.node})
                   CREATE (d)-[:HAS_POD]->(:Pod {name: p.pod})-[:ON_NODE]->(n)""", pods)
    # 90 days of routine changes and background alerts, kept clear of the incident windows below
    changes = [{"id": f"BG-{k}", "kind": rng.choice(["deploy", "deploy", "config"]), "service": rng.choice(names),
                "at": NOW - 10 * DAY - rng.randint(0, 80 * DAY), "summary": "routine", "risk": "low"}
               for k in range(len(names) * 20)]
    alerts = [{"id": f"BA-{k}", "service": rng.choice(names), "at": NOW - 10 * DAY - rng.randint(0, 80 * DAY),
               "signal": "background noise"} for k in range(len(names) * 40)]
    # planted incidents, a day apart within the last 10 days, each with its own window
    planted = []
    callers = {}
    for a, b in calls:
        callers.setdefault(b, []).append(a)

    def path_up(culprit: str, hops: int) -> list[str]:
        p = [culprit]
        while len(p) <= hops and callers.get(p[-1]):
            p.append(rng.choice(callers[p[-1]]))
        return p
    for k in range(incidents):
        base = NOW - k * 70 * MIN - 2 * HOUR
        culprit = rng.choice(by_layer["platform"] + by_layer["store"])
        up = path_up(culprit, rng.randint(1, 3))
        if len(up) < 2:
            continue
        t = base - rng.randint(4, 40) * MIN
        cid = f"PC-{k}"
        changes.append({"id": cid, "kind": rng.choice(["deploy", "config"]), "service": culprit, "at": t,
                        "summary": f"planted change on {culprit}", "risk": rng.choice(["high", "medium", "low"])})
        t += rng.randint(1, 3) * MIN
        for svc in up:
            alerts.append({"id": f"PA-{k}-{svc}", "service": svc, "at": t, "signal": "planted"})
            t += rng.randint(1, 3) * MIN
        sym = up[-1]
        near = rng.choice([c for c in [sym] + [b for a, b in calls if a == sym] if c != culprit] or [sym])
        changes.append({"id": f"PD-{k}", "kind": "deploy", "service": near, "at": t - rng.randint(5, 30) * MIN,
                        "summary": "distractor close to the symptom", "risk": "high"})
        planted.append({"id": f"INC-{10000 + k}", "symptom": sym, "started": t - MIN, "cause": cid, "culprit": culprit})
    _batches(g, """UNWIND $rows AS c MATCH (s:Service {name: c.service})
                   CREATE (:Change {id: c.id, kind: c.kind, at: c.at, summary: c.summary, risk: c.risk})-[:CHANGED]->(s)""", changes)
    _batches(g, """UNWIND $rows AS a MATCH (s:Service {name: a.service})
                   CREATE (:Alert {id: a.id, at: a.at, severity: 'page', signal: a.signal})-[:FIRED_ON]->(s)""", alerts)
    _batches(g, """UNWIND $rows AS i MATCH (s:Service {name: i.symptom})
                   CREATE (:Incident {id: i.id, title: 'planted', started: i.started, status: 'open', severity: 'SEV2'})-[:SYMPTOM_ON]->(s)""",
             [{k: v for k, v in i.items() if k != "culprit"} for i in planted])
    counts = {"nodes": g.read("MATCH (n) RETURN count(n) AS n")[0]["n"], "edges": g.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]}
    return {"graph": g, "planted": planted, "names": names, "build_s": round(time.time() - t0, 1), **counts,
            "services": len(names), "calls": len(calls), "operations": len(ops), "op_calls": len(op_calls),
            "pods": len(pods), "changes": len(changes), "alerts": len(alerts)}


def _time(fn, n: int) -> dict:
    ts = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t) * 1000)
    ts.sort()
    return {"runs": n, "p50_ms": round(statistics.median(ts), 1), "p95_ms": round(ts[int(0.95 * (n - 1))], 1)}


def run(services: int = 1000, incidents: int = 200, reps: int = 20) -> dict:
    b = build(services, incidents)
    g, planted, rng = b["graph"], b["planted"], random.Random(2)
    pick = lambda: rng.choice(planted)  # noqa: E731
    deep = rng.choice([n for n in b["names"] if n.startswith("edge")])
    q = {
        "dependencies, 5 hops (CALLS*1..5)": lambda: tools.dependencies(g, deep),
        "blast radius (who calls it)": lambda: tools.blast_radius(g, rng.choice(b["names"])),
        "changes and alerts near an incident": lambda: (lambda p: (tools.changes_near(g, [p["symptom"]], p["started"]),
                                                                   tools.alerts_near(g, [p["symptom"]], p["started"])))(pick()),
        "the judge (rank_suspects)": lambda: tools.rank_suspects(g, pick()["id"]),
        "judge + skeptic + WCC groups": lambda: (lambda i: tools.skeptic_review(tools.rank_suspects(g, i), tools.alert_clusters(g, i)["groups"]))(pick()["id"]),
        "PageRank over CALLS": lambda: tools.critical_services(g, 10),
        "betweenness over CALLS": lambda: tools.single_points_of_failure(g, 10),
        "label propagation (failure domains)": lambda: tools.failure_domains(g),
        "SPpaths, 3 shortest (symptom to cause)": lambda: (lambda p: tools.failure_paths(g, p["symptom"], p["culprit"], 3))(pick()),
        "full-text search over changes and alerts": lambda: tools.search_memory(g, "planted change"),
    }
    slow = {"PageRank over CALLS", "betweenness over CALLS", "label propagation (failure domains)"}
    timings = {name: _time(fn, max(3, reps // 4) if name in slow else reps) for name, fn in q.items()}
    hit = hit3 = 0
    for p in planted:
        sus = tools.rank_suspects(g, p["id"], limit=10)["suspects"]
        ids = [(s["change"] or {}).get("id") for s in sus]
        hit += ids[:1] == [p["cause"]]
        hit3 += p["cause"] in ids[:3]
    g.drop()
    return {**{k: v for k, v in b.items() if k not in ("graph", "planted", "names")}, "incidents": len(planted),
            "timings": timings, "judge_top1": hit, "judge_top3": hit3}


def table(r: dict) -> str:
    lines = [f"{r['services']:,} services · {r['nodes']:,} nodes · {r['edges']:,} edges "
             f"({r['calls']:,} service calls, {r['operations']:,} operations and {r['op_calls']:,} operation calls, "
             f"{r['pods']:,} pods, {r['changes']:,} changes, {r['alerts']:,} alerts, {r['incidents']} incidents) · "
             f"built in {r['build_s']} s", "", "| query | runs | p50 | p95 |", "|---|---|---|---|"]
    for name, t in r["timings"].items():
        lines.append(f"| {name} | {t['runs']} | {t['p50_ms']} ms | {t['p95_ms']} ms |")
    lines.append("")
    lines.append(f"Judge on the {r['incidents']} planted incidents: the planted change first in "
                 f"{r['judge_top1']} ({round(100 * r['judge_top1'] / r['incidents'])}%), in the top three in {r['judge_top3']}.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--services", type=int, default=1000)
    ap.add_argument("--incidents", type=int, default=200)
    ap.add_argument("--reps", type=int, default=20)
    ap.add_argument("--md", type=Path, default=None)
    a = ap.parse_args()
    r = run(a.services, a.incidents, a.reps)
    out = table(r)
    print(out)
    if a.md:
        a.md.write_text(out + "\n")


if __name__ == "__main__":
    main()
