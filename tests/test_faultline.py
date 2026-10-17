import json

import pytest
from fastapi.testclient import TestClient

from faultline import agent, mcp_server, seed, tools
from faultline.db import Graph, graph_name


def test_seed_is_deterministic(fresh):
    first = tools.stats(fresh)
    seed.build("kestrel", force=True)
    assert tools.stats(Graph("kestrel")) == first
    assert first["nodes"]["Service"] == 24
    assert first["edges"]["CALLS"] == 32


@pytest.mark.parametrize("incident, cause, service", [
    ("INC-301", "C-5101", "payments-db"),   # config change two hops below the symptom
    ("INC-302", "D-5201", "auth"),          # deploy one hop below
    ("INC-303", None, "catalog-es"),        # nothing changed: infrastructure failure
])
def test_judge_finds_the_planted_cause(fresh, incident, cause, service):
    r = tools.rank_suspects(fresh, incident)
    top = r["suspects"][0]
    assert top["service"] == service
    assert (top["change"] or {}).get("id") == cause
    assert r["confidence"] == "high"
    assert top["path"][0] == r["incident"]["symptom"] and top["path"][-1] == service


def test_alert_chain_beats_a_closer_recent_change(fresh):
    # D-5102 is one hop from checkout and recent, but nothing on its path alerted.
    r = tools.rank_suspects(fresh, "INC-301")
    by_id = {s["change"]["id"]: s for s in r["suspects"] if s["change"]}
    assert by_id["D-5102"]["factors"]["proximity"] > by_id["C-5101"]["factors"]["proximity"]
    assert by_id["D-5102"]["factors"]["alert_chain"] == 0
    assert by_id["C-5101"]["factors"]["alert_chain"] == 1
    assert by_id["C-5101"]["score"] > by_id["D-5102"]["score"] + 0.4


def test_off_path_changes_are_ruled_out(fresh):
    r = tools.rank_suspects(fresh, "INC-301")
    assert {c["id"] for c in r["ruled_out"]} >= {"D-5103", "F-5202"}
    assert "D-5103" not in {(s["change"] or {}).get("id") for s in r["suspects"]}


def test_precedent_comes_from_resolved_incidents(fresh):
    r = tools.rank_suspects(fresh, "INC-301")
    top = r["suspects"][0]
    assert top["factors"]["precedent"] == 1.0
    assert top["precedents"][0]["incident"] == "INC-212"
    # without the memory the same change scores lower
    fresh.write("MATCH (:Incident {id: 'INC-212'})-[r:ROOT_CAUSE]->() DELETE r")
    again = tools.rank_suspects(fresh, "INC-301")["suspects"][0]
    assert again["factors"]["precedent"] == 0 and again["score"] < top["score"]


def test_read_only_tool_refuses_writes(fresh):
    with pytest.raises(ValueError):
        tools.run_cypher(fresh, "CREATE (:Oops)")
    # and the database refuses too, even if the keyword check is bypassed
    with pytest.raises(Exception):
        fresh.read("CREATE (:Oops)")
    assert fresh.read("MATCH (n:Oops) RETURN count(n) AS n")[0]["n"] == 0


def test_tenants_are_separate_graphs(fresh):
    harbor = Graph("harbor")
    assert graph_name("harbor") != graph_name("kestrel")
    with pytest.raises(KeyError):
        tools.who_owns(harbor, "payments")
    assert tools.who_owns(harbor, "shipments")["team"] == "Ops"
    assert {r["id"] for r in tools.list_incidents(harbor)} == {"INC-7"}
    with pytest.raises(ValueError):
        Graph("../kestrel")


def test_action_lifecycle_writes_memory(fresh):
    a = tools.propose_action(fresh, "INC-301", "revert_config", "payments-db", "Revert pool", change_id="C-5101")
    again = tools.propose_action(fresh, "INC-301", "revert_config", "payments-db", "Revert pool", change_id="C-5101")
    assert again["id"] == a["id"] and again["already_proposed"]
    assert tools.get_incident(fresh, "INC-301")["status"] == "open"     # proposing changes nothing

    assert tools.decide_action(fresh, a["id"], True, "tester")["status"] == "executed"
    assert tools.get_incident(fresh, "INC-301")["status"] == "mitigated"
    assert fresh.read("MATCH (:Action {id: $id})-[:CREATED]->(c:Change {kind: 'remediation'}) RETURN count(c) AS n",
                      {"id": a["id"]})[0]["n"] == 1
    with pytest.raises(ValueError):
        tools.decide_action(fresh, a["id"], True, "tester")

    tools.resolve_incident(fresh, "INC-301", "C-5101", "Pool limits need a load test.")
    hist = tools.similar_incidents(fresh, "payments-db", "config")
    assert hist[0]["incident"] == "INC-301" and hist[0]["lesson"] == "Pool limits need a load test."


def test_infra_resolution_becomes_precedent(fresh):
    tools.resolve_incident(fresh, "INC-303", None, "ES node died again.", cause_service="catalog-es")
    hist = tools.similar_incidents(fresh, "catalog-es")
    assert {h["incident"] for h in hist} >= {"INC-303", "INC-240"}


def test_offline_investigation_shows_its_work(fresh):
    out = agent.investigate("kestrel", "INC-302")
    assert out["mode"] == "offline"
    assert out["findings"]["change_id"] == "D-5201"
    assert "4.1." in out["ranking"]["suspects"][0]["recommended_action"]["to_version"]
    assert all(s["cypher"] for s in out["steps"]), "every step must carry the Cypher it ran"
    roles = [s["role"] for s in out["steps"]]
    assert roles == sorted(roles, key=["triage", "investigator", "skeptic", "responder"].index)
    assert "propose_action" in [s["tool"] for s in out["steps"] if s["role"] == "responder"]
    assert tools.past_investigations(fresh, "INC-302")[0]["top_change"] == "D-5201"


@pytest.mark.parametrize("q, needle", [
    ("who owns payments-db?", "Adebayo Okafor"),
    ("what breaks if ledger-db goes down?", "web-frontend"),
    ("runbook for catalog-es", "RB-3"),
    ("has catalog-es failed before?", "INC-240"),
    ("most critical services", "PageRank"),
])
def test_offline_questions(fresh, q, needle):
    assert needle in agent.ask_offline("kestrel", q)["answer"]


def test_mcp_tools_return_results_with_cypher(fresh):
    out = json.loads(mcp_server.rank_suspects("INC-301"))
    assert out["result"]["suspects"][0]["change"]["id"] == "C-5101"
    assert any("CALLS" in q for q in out["cypher"])
    with pytest.raises(ValueError):
        mcp_server.run_cypher("MATCH (n) DETACH DELETE n")


def test_api_end_to_end(fresh):
    with TestClient(__import__("faultline.api", fromlist=["app"]).app) as c:
        assert c.get("/api/health").json()["ok"]
        assert c.get("/api/nope/incidents").status_code == 404
        inv = c.post("/api/kestrel/incidents/INC-301/investigate").json()
        assert inv["findings"]["change_id"] == "C-5101"
        act = c.get("/api/kestrel/incidents/INC-301").json()["actions"][0]
        assert c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "t"}).json()["status"] == "executed"
        c.post("/api/kestrel/incidents/INC-301/resolve", json={"change_id": "C-5101", "lesson": "load test pools"})
        mem = c.get("/api/kestrel/memory").json()
        assert any(m["id"] == "INC-301" and "load test pools" in m["lessons"] for m in mem)
        assert c.get("/").status_code == 200


def test_one_change_explaining_two_incidents_is_linked(fresh):
    r301 = tools.rank_suspects(fresh, "INC-301")
    r304 = tools.rank_suspects(fresh, "INC-304")
    assert r304["suspects"][0]["change"]["id"] == "C-5101"
    assert [o["id"] for o in r301["suspects"][0]["also_explains"]] == ["INC-304"]
    assert [o["id"] for o in r304["suspects"][0]["also_explains"]] == ["INC-301"]
    assert r301["suspects"][0]["recommended_action"]["also_for"] == ["INC-304"]
    # a closer deploy with no alert chain explains nothing else
    cart = next(s for s in r301["suspects"] if (s["change"] or {}).get("id") == "D-5102")
    assert cart["also_explains"] == []
    # the auth deploy behind INC-302 is not on checkout's or payments' path
    assert tools.rank_suspects(fresh, "INC-302")["suspects"][0]["also_explains"] == []


def test_one_fix_mitigates_every_linked_incident(fresh):
    out = agent.investigate("kestrel", "INC-301")
    assert "INC-304" in out["findings"]["summary"]
    acts = tools.list_actions(fresh, "INC-304")
    assert len(acts) == 1 and set(acts[0]["covers"]) == {"INC-301", "INC-304"}
    # investigating the other incident reuses the same proposal
    agent.investigate("kestrel", "INC-304")
    assert [a["id"] for a in tools.list_actions(fresh, "INC-304")] == [acts[0]["id"]]
    done = tools.decide_action(fresh, acts[0]["id"], True, "tester")
    assert set(done["incidents"]) == {"INC-301", "INC-304"}
    assert {tools.get_incident(fresh, i)["status"] for i in ("INC-301", "INC-304")} == {"mitigated"}
    assert fresh.read("MATCH (c:Change {kind: 'remediation'}) RETURN count(c) AS n")[0]["n"] == 1


def test_agents_hand_off_only_through_the_graph(fresh):
    out = agent.investigate("kestrel", "INC-301")
    eps = fresh.read("""
        MATCH (e:Episode {run: $run})-[:ABOUT]->(:Incident {id: 'INC-301'})
        OPTIONAL MATCH (e)-[:READ]->(p:Episode)
        RETURN e.role AS role, p.role AS read ORDER BY e.wall_at""", {"run": out["run_id"]})
    assert [(e["role"], e["read"]) for e in eps] == [
        ("triage", None), ("investigator", "triage"), ("skeptic", "investigator"), ("responder", "skeptic")]
    # each later agent's first step is reading the previous handoff back from the graph
    firsts = {}
    for st in out["steps"]:
        firsts.setdefault(st["role"], st["tool"])
    assert firsts == {"triage": "get_incident", "investigator": "read_handoff", "skeptic": "read_handoff",
                      "responder": "read_handoff"}


def test_scored_cause_and_similarity_edges(fresh):
    agent.investigate("kestrel", "INC-301")
    sus = fresh.read("""MATCH (:Incident {id: 'INC-301'})-[r:SUSPECTED_CAUSE]->(x)
                        RETURN r.rank AS rank, coalesce(x.id, x.name) AS target ORDER BY rank""")
    assert sus[0] == {"rank": 1, "target": "C-5101"}
    sim = fresh.read("MATCH (:Incident {id: 'INC-301'})-[r:SIMILAR_TO]->(p) RETURN p.id AS past, r.score AS score")
    assert sim and sim[0]["past"] == "INC-212" and sim[0]["score"] > 0.8
    agent.investigate("kestrel", "INC-301")   # re-running replaces, never stacks
    assert fresh.read("MATCH (:Incident {id: 'INC-301'})-[r:SUSPECTED_CAUSE]->() RETURN count(r) AS n")[0]["n"] == len(sus)


def test_footprint_explains_the_alerts_in_order(fresh):
    top = tools.rank_suspects(fresh, "INC-301")["suspects"][0]
    fp = top["footprint"]
    assert fp["explained"] == ["payments-db", "payments", "checkout"] and fp["in_order"]
    assert fp["coverage"] == 1.0 and top["factors"]["footprint"] == 1.0


def test_independent_faults_are_separated(fresh):
    r = tools.rank_suspects(fresh, "INC-302")
    assert r["suspects"][0]["change"]["id"] == "D-5201"
    assert r["suspects"][0]["factors"]["footprint"] == 1.0          # full credit within its own alert cone
    others = {((o["change"] or {}).get("id") or o["service"]): {i["id"] for i in o["incidents"]} for o in r["other_faults"]}
    assert others == {"C-5101": {"INC-301", "INC-304"}, "catalog-es": {"INC-303"}}
    assert tools.rank_suspects(fresh, "INC-301")["other_faults"] == []


def test_silent_cause_and_failure_from_below():
    alerts = [{"service": "payments", "at": 100}, {"service": "checkout", "at": 110}]
    # the cause (payments-db) never alerted, but everything between it and the symptom did
    assert tools._alert_chain(["checkout", "payments", "payments-db"], alerts, 50)[0] == 0.6
    # one hop and silent: no independent evidence at all
    assert tools._alert_chain(["checkout", "cart"], alerts, 50)[0] == 0.0
    # alerts climbing the wrong way keep only 70%
    backwards = [{"service": "payments", "at": 120}, {"service": "payments-db", "at": 130}, {"service": "checkout", "at": 140}]
    assert tools._alert_chain(["checkout", "payments", "payments-db"], backwards, 50)[0] == 0.7


def test_simulate_failure_predicts_waves(fresh):
    sim = tools.simulate_failure(fresh, "payments-db")
    assert [w["services"] for w in sim["waves"]][:2] == [["payments"], ["checkout"]]
    assert set(sim["internet_exposed"]) == {"api-gateway", "mobile-bff", "web-frontend"}
    assert sim["runbooks"][0]["id"] == "RB-1"
    with pytest.raises(KeyError):
        tools.simulate_failure(fresh, "nope")


def test_eval_beats_baselines_and_every_factor_matters():
    from faultline import eval as ev
    res = ev.run(n=60, seed=5, learning=False)["phase1"]["top1"]
    full = res["full"]["all"]
    assert full >= 85
    assert full > res["most recent change"]["all"] + 15 and full > res["nearest recent change"]["all"] + 50
    assert res["no recency"]["all"] < full and res["no footprint"]["all"] <= full
    assert res["full"]["infra"] >= 90 and res["full"]["shared"] >= 90
    assert res["full + skeptic"]["all"] >= full


def test_confidence_comes_from_the_gap():
    hi, _ = tools.confidence_from(0.9, 0.6)
    close, label = tools.confidence_from(0.9, 0.02)
    weak, _ = tools.confidence_from(0.3, 0.3)
    assert hi > 0.99 and close < 0.5 and label == "low"
    assert weak < tools.confidence_from(0.9, 0.3)[0]     # a low-scoring leader is damped even when far ahead


def _sus(label, score, explained, in_order=True, chain=1.0, minutes=5.0):
    return {"type": "change", "service": label, "change": {"id": label}, "score": score, "minutes_before": minutes,
            "factors": {"alert_chain": chain},
            "footprint": {"explained": explained, "in_order": in_order, "sequence": []}}


def test_skeptic_overturns_a_leader_that_leaves_alerts_unexplained():
    ranking = {"incident": {"symptom": "web"}, "alert_cone": ["web", "api", "db"],
               "suspects": [_sus("C-1", 0.80, ["web"]), _sus("C-2", 0.72, ["web", "api", "db"])]}
    rv = tools.skeptic_review(ranking)
    assert rv["overturned"] and rv["leader"] == "C-2" and rv["suspects"][0]["change"]["id"] == "C-2"
    assert rv["rounds"][0]["handed_to"] == "C-2" and len(rv["rounds"]) == 2
    # a challenger too far behind does not take over, but the doubt is recorded
    ranking["suspects"][1]["score"] = 0.5
    rv = tools.skeptic_review(ranking)
    assert not rv["overturned"] and rv["verdict"] == "upheld with doubts" and rv["confidence"] != "high"


def test_skeptic_upholds_the_demo_verdicts(fresh):
    for inc, lead in [("INC-301", "C-5101"), ("INC-302", "D-5201"), ("INC-303", "catalog-es (no change)")]:
        rv = tools.skeptic_review(tools.rank_suspects(fresh, inc))
        assert rv["verdict"] == "upheld" and rv["leader"] == lead and rv["confidence"] == "high"


def test_claims_are_checked_against_the_graph(fresh):
    for inc in ("INC-301", "INC-302", "INC-303", "INC-304"):
        out = agent.investigate("kestrel", inc)
        assert out["claim_check"]["supported"] == out["claim_check"]["total"] > 3
        assert out["claim_check"]["verdict_consistent"]
    bad = {"summary": "C-9999 broke payments-db at 13:55 UTC", "change_id": "D-5201", "root_cause_service": "payments-db",
           "evidence": ["auth alerted at 13:38 UTC", "D-5201 by noor at 11:02 UTC"]}
    v = tools.verify_claims(fresh, "INC-302", bad, tools.rank_suspects(fresh, "INC-302"))
    assert [c["ok"] for c in v["claims"]] == [False, True, False]
    assert "C-9999 is not in the graph" in v["claims"][0]["problems"]
    assert v["verdict_problems"] == ["D-5201 changed auth, not payments-db"]


def test_unsupported_claude_claims_are_dropped(fresh, monkeypatch):
    def fake_claude(tenant, incident_id, run_id):
        ranking = tools.apply_review(*(lambda r: (r, tools.skeptic_review(r)))(tools.rank_suspects(Graph(tenant), incident_id)))
        return {"mode": "claude", "model": "x", "incident_id": incident_id, "run_id": run_id, "steps": [],
                "ranking": ranking, "answer": "",
                "findings": {"summary": "C-5101 caused it.", "change_id": "C-5101", "root_cause_service": "payments-db",
                             "confidence": "high", "evidence": ["payments alerted at 13:19 UTC", "INC-999 had the same cause"]}}
    monkeypatch.setattr(agent, "claude_available", lambda: True)
    monkeypatch.setattr(agent, "investigate_claude", fake_claude)
    out = agent.investigate("kestrel", "INC-301")
    assert out["findings"]["evidence"] == ["payments alerted at 13:19 UTC"]
    assert out["claim_check"]["supported"] == 2 and out["claim_check"]["total"] == 3


def test_rcaeval_detector_needs_a_sustained_spike():
    import numpy as np
    import pandas as pd
    from faultline import rcaeval as rc
    rng = np.random.default_rng(0)
    t = np.arange(0, 300)
    a = 1 + rng.normal(0, 0.05, t.size)
    a[150:] += 5                                    # sustained from t=150
    b = 1 + rng.normal(0, 0.05, t.size)
    b[160:170] += 5                                 # a 10-second blip: not enough
    m = pd.DataFrame({"time": t, "aservice_cpu": a, "bservice_latency-90": b, "cservice_workload": a})
    alerts = rc.detect(m, inject=120)
    assert [(x["service"], x["at"]) for x in alerts] == [("aservice", 150)]   # workload is not a metric we alert on


def test_rcaeval_call_edges_from_spans():
    import pandas as pd
    from faultline import rcaeval as rc
    rows = [("p", "", "frontendservice")] + [(f"c{i}", "p", "checkoutservice") for i in range(6)] + \
           [("x", "c0", "emailservice")]
    spans = pd.DataFrame(rows, columns=["spanID", "parentSpanID", "serviceName"])
    edges = rc.call_edges(spans)
    assert ("frontend", "checkoutservice") in edges          # 6 calls, above MIN_CALLS
    assert ("checkoutservice", "emailservice") not in edges  # 1 call, below
    assert ("cartservice", "redis") in edges


def test_later_alerts_below_are_knock_on_not_origin():
    """Real data (RCAEval): checkout broke first; currency below it slowed down 90 s later. The origin is the
    service that alerted first, not the deepest one alerting."""
    g = Graph("knockon")
    g.drop()
    seed._write_indexes(g)
    g.write("CREATE (:Team {id: 't', name: 'T'})")
    g.write("""UNWIND ['front', 'mid', 'low'] AS s MATCH (t:Team) CREATE (:Service {name: s, kind: 'service'})-[:OWNED_BY]->(t)""")
    g.write("MATCH (a:Service {name: 'front'}), (b:Service {name: 'mid'}), (c:Service {name: 'low'}) CREATE (a)-[:CALLS]->(b)-[:CALLS]->(c)")
    seed._write_alerts(g, [{"id": "A1", "service": "mid", "at": 1000, "severity": "page", "signal": "cpu"},
                           {"id": "A2", "service": "front", "at": 1030, "severity": "page", "signal": "latency"},
                           {"id": "A3", "service": "low", "at": 1090, "severity": "page", "signal": "latency"}])
    seed._write_incidents(g, [{"id": "INC-1", "title": "t", "symptom": "front", "started": 1030, "status": "open", "severity": "SEV2"}])
    try:
        assert tools.rank_suspects(g, "INC-1")["suspects"][0]["service"] == "mid"
    finally:
        g.drop()
