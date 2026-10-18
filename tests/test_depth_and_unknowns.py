"""The two checks the organisers' post asks for: the same question at depth 1 and at full depth, and a question
about something that isn't in the graph, which should be answered as "not modelled", never as "safe"."""
from fastapi.testclient import TestClient

from faultline import agent, site, tools


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_one_hop_blames_the_victim_and_two_hops_find_the_pool_change(fresh):
    s = tools.depth_sweep(fresh, "INC-301")
    by = {r["depth"]: r for r in s["depths"]}
    # one hop below checkout only reaches payments, which alerted, so a join-depth look blames it, confidently
    assert (by[1]["leader"], by[1]["confidence"]) == ("payments (no change)", "high")
    assert by[1]["path"] == ["checkout", "payments"]
    # one hop further is payments-db, where the connection-pool change landed
    assert [by[d]["leader"] for d in (2, 3, 4, 5)] == ["C-5101"] * 4
    assert by[2]["path"] == ["checkout", "payments", "payments-db"]
    assert s["settles_at"] == 2 and s["final"] == "C-5101"
    assert by[1]["services_searched"] < by[2]["services_searched"]


def test_a_capped_search_says_why_it_ruled_a_change_out(fresh):
    r = tools.rank_suspects(fresh, "INC-301", depth=1)
    pool = next(c for c in r["ruled_out"] if c["id"] == "C-5101")
    assert pool["reason"] == "payments-db is not on checkout's dependency path within 1 hop"
    assert all("within" not in c["reason"] for c in tools.rank_suspects(fresh, "INC-301")["ruled_out"])


def test_depth_report_scores_every_depth_against_the_true_cause(fresh):
    r = site.depth_report(fresh)
    assert r["scored"] == 5
    assert r["right_at"] == {1: 3, 2: 5, 3: 5, 4: 5, 5: 5}
    assert r["changed"] == ["INC-301", "INC-305"]
    inc305 = next(x for x in r["incidents"] if x["incident"] == "INC-305")
    assert inc305["depths"][0]["right"] is False and inc305["depths"][1]["right"] is True   # the shared host, 2 hops down


def test_a_name_that_is_not_in_the_graph_is_not_modelled_not_safe(fresh):
    a = agent.ask_offline("kestrel", "what breaks if billing-svc goes down?")
    assert a["not_in_graph"] is True
    assert "Nothing in the graph is called billing-svc" in a["answer"] and "not that it is safe" in a["answer"]
    step = a["steps"][0]
    assert step["tool"] == "find_named" and step["result"]["found"] == [] and step["cypher"]   # the lookup it ran


def test_near_names_are_offered_and_known_names_still_answer(fresh):
    a = agent.ask_offline("kestrel", "who owns kafka-connect")
    assert a["not_in_graph"] and "events-kafka" in a["answer"]
    assert "6 services affected" in agent.ask_offline("kestrel", "what breaks if ledger-db goes down?")["answer"]


def test_a_node_that_is_not_a_service_says_what_it_is(fresh):
    a = agent.ask_offline("kestrel", "who owns D-5201")
    assert a["not_in_graph"] is False
    assert a["answer"].startswith("D-5201 is in the graph as a Change on auth")


def test_no_callers_reads_as_an_entry_point_or_as_unknown(fresh):
    assert "internet-facing entry point" in agent.ask_offline("kestrel", "what breaks if mobile-bff goes down?")["answer"]
    leaf = fresh.read("""MATCH (s:Service) WHERE NOT ()-[:CALLS]->(s) AND NOT coalesce(s.internet_exposed, false)
                         RETURN s.name AS s LIMIT 1""")
    if leaf:
        res = tools.blast_radius(fresh, leaf[0]["s"])
        assert agent._summarize("blast_radius", leaf[0]["s"], res, None).endswith("unknown rather than safe.")


def test_routes(fresh):
    with _client() as c:
        d = c.get("/api/kestrel/incidents/INC-301/depth").json()
        assert d["settles_at"] == 2 and len(d["depths"]) == tools.MAX_HOPS
        assert c.get("/api/kestrel/incidents/INC-999/depth").status_code == 404
        assert c.get("/api/kestrel/depth").json()["right_at"]["1"] == 3
