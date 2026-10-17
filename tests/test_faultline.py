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
    assert roles == sorted(roles, key=["triage", "investigator", "responder"].index)
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
        ("triage", None), ("investigator", "triage"), ("responder", "investigator")]
    # each later agent's first step is reading the previous handoff back from the graph
    firsts = {}
    for st in out["steps"]:
        firsts.setdefault(st["role"], st["tool"])
    assert firsts == {"triage": "get_incident", "investigator": "read_handoff", "responder": "read_handoff"}


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
