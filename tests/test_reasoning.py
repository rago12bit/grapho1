"""The judge's argument as a subgraph: hypotheses with evidence for and against, and the causal chain of alerts."""
from fastapi.testclient import TestClient

from faultline import agent, reasoning, tools


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_every_rejected_hypothesis_says_why_it_lost(fresh):
    r = agent.judged(fresh, "INC-302")
    hs = reasoning.hypotheses(r)
    lead = hs[0]
    assert lead["label"] == "D-5201" and lead["status"] == "standing" and not lead["contradicts"]
    assert len(hs) >= 4 and all(h["status"] == "rejected" for h in hs[1:])
    for h in hs[1:]:
        assert h["contradicts"], h["label"]
        assert any(e["kind"] == "rank" and "D-5201" in e["text"] for e in h["contradicts"])
    # C-5101 is a real fault, but not this incident's: it can't explain auth's alert
    c = next(h for h in hs if h["label"] == "C-5101")
    assert any("auth" in e["text"] for e in c["contradicts"] if e["kind"] == "check")


def test_causal_chain_runs_from_the_symptom_down_to_the_change(fresh):
    r = agent.judged(fresh, "INC-301")
    links = reasoning.causal_chain(fresh, r)["links"]
    by_service = {l["from_service"]: l for l in links}
    assert by_service["checkout"]["to_service"] == "payments"
    assert by_service["payments"]["to_service"] == "payments-db"
    root = by_service["payments-db"]
    assert root["to_label"] == "Change" and root["to"] == "C-5101" and root["lag_s"] > 0
    assert all(l["lag_s"] >= 0 and l["direct"] for l in links)


def test_a_silent_root_still_anchors_the_chain():
    """A database that never alerts (the change landed there) is still where the chain ends."""
    root = {"service": "db", "change": {"id": "C-1", "at": 1000}}
    alerts = [{"id": "A-2", "service": "web", "at": 1200}, {"id": "A-1", "service": "api", "at": 1100}]
    down = {"web": {"api"}, "api": {"db"}}
    links = {l["from_service"]: l for l in reasoning._chain(root, ["api", "web"], alerts, down, 1.0)}
    assert links["api"]["to_label"] == "Change" and links["api"]["to"] == "C-1" and links["api"]["lag_s"] == 100
    assert links["web"]["to"] == "A-1"


def test_a_silent_hop_in_the_middle_is_named(fresh):
    root = {"service": "db", "change": {"id": "C-1", "at": 1000}}
    alerts = [{"id": "A-1", "service": "db", "at": 1050}, {"id": "A-3", "service": "web", "at": 1300}]
    down = {"web": {"api"}, "api": {"db"}}
    links = {l["from_service"]: l for l in reasoning._chain(root, ["web"], alerts, down, 1.0)}
    assert links["web"]["to"] == "A-1" and not links["web"]["direct"] and links["web"]["via"] == ["api"]


def test_argument_and_chain_are_stored_and_read_back_with_cypher(fresh):
    out = agent.investigate("kestrel", "INC-302")
    w = out["reasoning_written"]
    assert w["hypotheses"] >= 4 and w["causal_links"] >= 2
    # every citation resolved to a real node
    n = fresh.read("MATCH (:Incident {id: 'INC-302'})-[:HAS_HYPOTHESIS]->()-[:SUPPORTED_BY|CONTRADICTED_BY]->()-[c:CITES]->() RETURN count(c) AS n")[0]["n"]
    assert n == w["citations"]
    stored = reasoning.argument(fresh, "INC-302")
    assert [h["label"] for h in stored][0] == "D-5201" and stored[0]["status"] == "standing"
    assert all(h["contradicts"] for h in stored[1:])
    blames = fresh.read("MATCH (h:Hypothesis {status: 'standing'})-[:BLAMES]->(c:Change) RETURN c.id AS id")
    assert [b["id"] for b in blames] == ["D-5201"]
    why = reasoning.why(fresh, "A-9102")
    assert [x["service"] for x in why["chain"]] == ["mobile-bff", "auth"] and why["root"]["id"] == "D-5201"
    # a second run replaces the argument instead of piling up a copy
    agent.investigate("kestrel", "INC-302")
    again = fresh.read("MATCH ()-[r:CAUSED_BY {incident: 'INC-302'}]->() RETURN count(r) AS n")[0]["n"]
    assert again == w["causal_links"]
    assert fresh.read("MATCH (:Incident {id: 'INC-302'})-[:HAS_HYPOTHESIS]->(h) RETURN count(h) AS n")[0]["n"] == w["hypotheses"]


def test_offline_agents_write_the_argument_and_follow_the_chain(fresh):
    out = agent.investigate_offline("kestrel", "INC-301", propose=False)
    tools_run = [s["tool"] for s in out["steps"]]
    assert tools_run.index("record_argument") < tools_run.index("why")
    why = next(s for s in out["steps"] if s["tool"] == "why")
    assert why["result"]["root"]["id"] == "C-5101"


def test_api_case_file_carries_the_argument(fresh):
    c = _client()
    case = c.get("/api/kestrel/incidents/INC-301/case").json()
    assert case["ranking"]["reasoning"]["hypotheses"][0]["label"] == "C-5101"
    assert case["ranking"]["reasoning"]["chain"]["links"]
    # the preview is read-only: nothing stored until the agents run
    assert c.get("/api/kestrel/incidents/INC-301/argument").json()["hypotheses"] == []
    c.post("/api/kestrel/incidents/INC-301/investigate")
    arg = c.get("/api/kestrel/incidents/INC-301/argument").json()
    assert arg["hypotheses"][0]["label"] == "C-5101" and arg["traces"]
    sym = next(a["id"] for a in case["ranking"]["alerts"] if a["service"] == "checkout")
    assert c.get(f"/api/kestrel/alerts/{sym}/why").json()["root"]["id"] == "C-5101"
