"""Sock Shop and Train Ticket: every RCAEval RE2 and RE3 case of each, as tenants, with the operation graph, hot
endpoints and error log patterns from their traces and logs."""
import pytest
from fastapi.testclient import TestClient

from faultline import agent, reasoning, seed, tools
from faultline.db import Graph


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


@pytest.fixture(scope="module")
def tenants():
    for t in ("sockshop", "trainticket"):
        seed.build(t, force=True)
    return {t: Graph(t) for t in ("sockshop", "trainticket")}


def _score(g, tenant):
    hit = hit3 = 0
    for c in seed.rcaeval_cases(tenant)["open"]:
        names = [s["service"] for s in agent.judged(g, c["id"])["suspects"]]
        hit += names[:1] == [c["root_cause"]]
        hit3 += c["root_cause"] in names[:3]
    return hit, hit3


def test_every_case_is_loaded_and_the_open_ones_are_scored_as_published(tenants):
    """README quotes these: Sock Shop 21/27 first (24 in the top three), Train Ticket 19/26 (21)."""
    for t, (n_open, n_all, score) in {"sockshop": (27, 120, (21, 24)), "trainticket": (26, 120, (19, 21))}.items():
        g = tenants[t]
        incs = tools.list_incidents(g, None)
        assert len(incs) == n_all and sum(i["status"] == "open" for i in incs) == n_open
        assert _score(g, t) == score


def test_train_ticket_has_an_operation_graph_from_its_traces(tenants):
    g = tenants["trainticket"]
    assert g.read("MATCH (o:Operation) RETURN count(o) AS n")[0]["n"] >= 150
    calls = g.read("""
        MATCH (a:Operation)-[c:CALLS]->(b:Operation) WHERE a.service <> b.service
        RETURN count(c) AS n, min(c.rps) AS lo""")[0]
    assert calls["n"] >= 60 and calls["lo"] > 0          # the other ~140 stay inside a service (handler → repository)
    # every operation-level call runs along a service-level CALLS edge
    stray = g.read("""
        MATCH (a:Operation)-[:CALLS]->(b:Operation) WHERE a.service <> b.service
        AND NOT (:Service {name: a.service})-[:CALLS]->(:Service {name: b.service})
        RETURN count(*) AS n""")[0]["n"]
    assert stray == 0
    card = tools.service_card(g, "ts-travel-service")
    assert card["endpoints"] and any(d["service"] == "ts-route-service" for e in card["endpoints"] for d in e["calls"])


def test_the_argument_cites_endpoints_and_log_patterns(tenants):
    g = tenants["trainticket"]
    seen = set()
    for c in seed.rcaeval_cases("trainticket")["open"]:
        b = reasoning.build(g, agent.judged(g, c["id"]))
        for e in b["hypotheses"][0]["supports"] if b["hypotheses"] else []:
            if e["kind"] in ("endpoint", "logs"):
                seen.add(e["kind"])
                assert e["cites"][0]["label"] in ("Operation", "LogPattern")
    assert seen == {"endpoint", "logs"}


def test_the_entry_point_itself_can_be_the_cause(tenants):
    """RE3 Sock Shop plants code faults in front-end itself. The judge has to be able to say so."""
    g = tenants["sockshop"]
    front = [c for c in seed.rcaeval_cases("sockshop")["open"] if c["root_cause"] == "front-end"]
    tops = [agent.judged(g, c["id"])["suspects"][0]["service"] for c in front]
    assert len(front) == 3 and tops.count("front-end") == 2


def test_paging_the_right_owner_clears_a_train_ticket_incident(tenants):
    case = seed.rcaeval_cases("trainticket")["open"][0]
    g = tenants["trainticket"]
    with _client() as c:
        act = tools.propose_action(g, case["id"], "page_owner", case["root_cause"], "Page the owner")
        out = c.post(f"/api/trainticket/actions/{act['id']}", json={"approve": True, "who": "t"}).json()
        assert out["verification"]["verified"]
    seed.build("trainticket", force=True)
