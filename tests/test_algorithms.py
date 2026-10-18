"""BFS, SSpaths and MSF doing the work: dependencies, callers and how a failure spread.

BFS and SSpaths replaced variable-length Cypher walks, so they are checked against those walks on every service
and every depth, including a graph with call cycles (Train Ticket has them)."""
from fastapi.testclient import TestClient

from faultline import agent, tools
from faultline.db import Graph, Trace, list_tenants


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def _walk_down(g, svc, h):
    return g.read(f"""MATCH p = (s:Service {{name: $svc}})-[:CALLS*1..{h}]->(d:Service) WITH d, min(length(p)) AS hops
                      RETURN d.name AS service, d.kind AS kind, hops ORDER BY hops, service""", {"svc": svc})


def _walk_up(g, svc):
    return g.read(f"""MATCH p = (u:Service)-[:CALLS*1..{tools.MAX_HOPS}]->(s:Service {{name: $svc}})
                      WITH u, min(length(p)) AS hops
                      RETURN u.name AS service, hops, u.internet_exposed AS internet_exposed, u.tier AS tier
                      ORDER BY hops, service""", {"svc": svc})


def _same_as_the_walks(g):
    for svc in [r["s"] for r in g.read("MATCH (s:Service) RETURN s.name AS s")]:
        for h in range(1, tools.MAX_HOPS + 1):
            assert tools.dependencies(g, svc, h) == _walk_down(g, svc, h), (svc, h)
        assert tools.callers(g, svc) == _walk_up(g, svc), svc


def test_bfs_and_sspaths_give_exactly_what_the_walks_gave(fresh):
    _same_as_the_walks(fresh)


def test_the_same_on_a_graph_with_call_cycles(fresh):
    g = Graph("algo_cycles")
    try:
        g.write("""CREATE (a:Service {name: 'a', kind: 'service', tier: 1, internet_exposed: true}),
                          (b:Service {name: 'b', kind: 'service', tier: 2, internet_exposed: false}),
                          (c:Service {name: 'c', kind: 'service', tier: 2, internet_exposed: false}),
                          (d:Service {name: 'd', kind: 'datastore', tier: 3, internet_exposed: false}),
                          (e:Service {name: 'e', kind: 'service', tier: 2, internet_exposed: false}),
                          (a)-[:CALLS]->(b), (b)-[:CALLS]->(c), (c)-[:CALLS]->(b), (c)-[:CALLS]->(d),
                          (e)-[:CALLS]->(e), (e)-[:CALLS]->(d)""")
        _same_as_the_walks(g)
        assert {"service": "b", "kind": "service", "hops": 2} in tools.dependencies(g, "b")   # b -> c -> b
        assert [r["service"] for r in tools.callers(g, "e")] == ["e"]                         # its own caller, one hop
    finally:
        g.drop()


def test_the_engine_does_the_walking(fresh):
    t = Trace()
    g = Graph("kestrel", t)
    tools.dependencies(g, "checkout")
    tools.blast_radius(g, "payments-db")
    tools.spread_tree(g, "INC-302")
    text = "\n".join(q["cypher"] for q in t.queries)
    assert "algo.BFS" in text and "algo.SSpaths" in text and "algo.MSF" in text
    assert "*1.." not in text.split("algo.MSF")[0]      # no variable-length walk before the algorithms


def test_spread_tree_finds_one_tree_per_problem_with_the_timing(fresh):
    r = tools.spread_tree(fresh, "INC-302")
    assert r["count"] == tools.alert_clusters(fresh, "INC-302")["count"] == 3
    by = {t["origin"]: t for t in r["trees"]}
    assert set(by) == {"auth", "payments-db", "catalog-es"}
    assert by["auth"]["has_symptom"] and r["trees"][0]["origin"] == "auth"
    # the deploy's failure reached mobile-bff two minutes after auth, the same lag the causal chain carries
    assert by["auth"]["links"] == [{"from": "auth", "to": "mobile-bff", "gap_s": 120}]
    assert [(x["from"], x["to"]) for x in by["payments-db"]["links"]] == [("payments-db", "payments"), ("payments", "checkout")]
    assert not [n for n in list_tenants() if n.startswith("scratch_")]        # the scratch graph is dropped


def test_spread_tree_shows_two_sources_meeting_for_a_shared_host(fresh):
    t = tools.spread_tree(fresh, "INC-305")["trees"][0]
    into_gateway = sorted(x["from"] for x in t["links"] if x["to"] == "api-gateway")
    assert into_gateway == ["catalog", "users"]           # both on k8s-node-4, neither calls the other


def test_a_lone_alert_is_its_own_tree(fresh):
    fresh.write("MATCH (a:Alert) WHERE NOT (a)-[:FIRED_ON]->(:Service {name: 'search'}) DETACH DELETE a")
    r = tools.spread_tree(fresh, "INC-303")       # only search's alerts left: nothing to join, MSF is not called
    assert [t["services"] for t in r["trees"]] == [["search"]] and r["trees"][0]["links"] == []


def test_the_investigator_uses_it_and_routes_serve_it(fresh):
    steps = agent.investigate_offline("kestrel", "INC-302", propose=False)["steps"]
    st = next(s for s in steps if s["tool"] == "spread_tree")
    assert st["role"] == "investigator" and st["result"]["count"] == 3
    with _client() as c:
        assert c.get("/api/kestrel/incidents/INC-302/spread").json()["count"] == 3
        assert c.get("/api/kestrel/incidents/INC-999/spread").status_code == 404
