"""The graph does the work. For each FalkorDB algorithm Hypocenter uses, change exactly what it reads, on a
GRAPH.COPY, and check that the decision it feeds changes with it. The real graph is never touched. And without
FalkorDB there is no answer at all: nothing falls back to another store."""
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path

from faultline import tools
from faultline.db import Graph, client, graph_name


@contextmanager
def copy_of(g: Graph):
    name = "lb_" + uuid.uuid4().hex[:10]
    client().connection.execute_command("GRAPH.COPY", graph_name(g.tenant), graph_name(name))
    c = Graph(name)
    try:
        yield c
    finally:
        c.drop()


def cut(g: Graph, a: str, b: str) -> None:
    n = g.write("MATCH (:Service {name: $a})-[r:CALLS]->(:Service {name: $b}) DELETE r RETURN count(*) AS n", {"a": a, "b": b})
    assert n[0]["n"] == 1, f"no call {a} -> {b} to cut"


def add(g: Graph, a: str, b: str) -> None:
    g.write("MATCH (x:Service {name: $a}), (y:Service {name: $b}) CREATE (x)-[:CALLS]->(y)", {"a": a, "b": b})


def leader(g: Graph, inc: str) -> str:
    return tools._label(tools.rank_suspects(g, inc)["suspects"][0])


def test_bfs_dependencies_decide_what_can_be_blamed(fresh):
    assert leader(fresh, "INC-302") == "D-5201"
    with copy_of(fresh) as c:
        c.write("MATCH ()-[r:CALLS]->(:Service {name: 'auth'}) DELETE r")   # nothing calls auth any more
        assert "auth" not in {d["service"] for d in tools.dependencies(c, "mobile-bff")}
        assert leader(c, "INC-302") != "D-5201"
    assert leader(fresh, "INC-302") == "D-5201"


def test_sspaths_callers_decide_the_blast_radius(fresh):
    assert "checkout" in {x["service"] for x in tools.blast_radius(fresh, "payments-db")["callers"]}
    with copy_of(fresh) as c:
        cut(c, "checkout", "payments")
        assert "checkout" not in {x["service"] for x in tools.blast_radius(c, "payments-db")["callers"]}


def test_sppaths_failure_path_or_no_path(fresh):
    assert tools.failure_paths(fresh, "checkout", "payments-db")
    with copy_of(fresh) as c:
        cut(c, "payments", "payments-db")
        assert tools.failure_paths(c, "checkout", "payments-db") == []


def test_wcc_decides_how_many_separate_problems(fresh):
    assert tools.alert_clusters(fresh, "INC-302")["count"] == 3
    with copy_of(fresh) as c:
        add(c, "auth", "payments")                       # join the login group to the pool group
        assert tools.alert_clusters(c, "INC-302")["count"] == 2


def test_msf_decides_how_the_failure_spread(fresh):
    links = lambda g: {(x["from"], x["to"]) for t in tools.spread_tree(g, "INC-305")["trees"] for x in t["links"]}  # noqa: E731
    before = links(fresh)
    assert ("catalog", "api-gateway") in before
    with copy_of(fresh) as c:
        add(c, "users", "catalog")                       # a closer-in-time call between the two failing services
        after = links(c)
        assert after != before and ({("users", "catalog"), ("catalog", "users")} & after)


def test_betweenness_decides_the_chokepoint(fresh):
    top = tools.single_points_of_failure(fresh, 1)[0]["service"]
    assert top == "checkout"
    with copy_of(fresh) as c:
        c.write("MATCH (:Service {name: 'checkout'})-[r:CALLS]->() DELETE r")
        assert tools.single_points_of_failure(c, 1)[0]["service"] != "checkout"


def test_label_propagation_decides_the_failure_domains(fresh):
    domains = lambda g: sorted(tuple(sorted(d["services"])) for d in tools.failure_domains(g))  # noqa: E731
    before = domains(fresh)
    with copy_of(fresh) as c:
        c.write("MATCH (:Service {name: 'api-gateway'})-[r:CALLS]->() DELETE r")
        assert domains(c) != before


def test_pagerank_decides_criticality(fresh):
    assert tools.critical_services(fresh, 1)[0]["service"] != "sendgrid"
    with copy_of(fresh) as c:
        c.write("""MATCH (s:Service), (t:Service {name: 'sendgrid'}) WHERE s <> t AND NOT (s)-[:CALLS]->(t)
                   CREATE (s)-[:CALLS]->(t)""")
        assert tools.critical_services(c, 1)[0]["service"] == "sendgrid"


def test_without_falkordb_there_is_no_answer():
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "FALKOR_HOST": "127.0.0.1", "FALKOR_PORT": "1", "PYTHONPATH": str(root)}
    p = subprocess.run([sys.executable, "-c", "from faultline import agent; print(agent.investigate('kestrel', 'INC-302'))"],
                       env=env, cwd=root, capture_output=True, text=True, timeout=120)
    assert p.returncode != 0 and not p.stdout
    assert "Connection" in p.stderr                       # it fails on the database, it doesn't guess
