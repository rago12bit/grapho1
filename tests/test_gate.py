"""The evidence gate: named checks, each with the query that decided it; no path is never treated as safe."""
import uuid

import pytest
from fastapi.testclient import TestClient

from faultline import agent, site, tools
from faultline.db import Graph, client, graph_name

KESTREL = ["INC-301", "INC-302", "INC-303", "INC-304", "INC-305"]


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


@pytest.fixture()
def cut(fresh):
    """A copy of Kestrel with every CALLS edge into auth deleted: D-5201's service is still alerting, unlinked."""
    g = Graph(f"gatecut_{uuid.uuid4().hex[:8]}")
    client().connection.execute_command("GRAPH.COPY", graph_name("kestrel"), graph_name(g.tenant))
    g.write("MATCH (:Service)-[r:CALLS]->(:Service {name: 'auth'}) DELETE r")
    yield g
    g.drop()


def test_every_kestrel_verdict_passes_all_eight_checks(fresh):
    for inc in KESTREL:
        gt = agent.gate(fresh, inc)
        assert gt["verdict"] == "confirmed", (inc, gt["headline"])
        assert [c["id"] for c in gt["checks"]] == [k for k, _, _ in tools.GATE]
        assert gt["passed"] == gt["total"] == 8


def test_each_check_shows_the_cypher_that_decided_it(fresh):
    checks = {c["id"]: c for c in agent.gate(fresh, "INC-302")["checks"]}
    assert "CALLS*1..5" in checks["G2"]["queries"][0]["cypher"]
    assert "Change {id: $id}" in checks["G1"]["queries"][0]["cypher"]
    assert any("algo.SSpaths" in q["cypher"] for q in checks["G5"]["queries"])
    assert any("algo.WCC" in q["cypher"] for q in checks["G5"]["queries"])
    assert checks["G6"]["queries"] == []                       # the sweep: G2 to G5 on every other suspect
    assert all(c["queries"] for k, c in checks.items() if k != "G6")


def test_the_sweep_runs_every_rival_through_the_same_checks(fresh):
    gt = agent.gate(fresh, "INC-302")
    sweep = {s["label"]: s for s in gt["sweep"]}
    assert sweep["D-5201"]["status"] == "leader"
    # F-5202's service never alerted; C-5101's alerts belong to INC-301: both fail the alert chain
    assert sweep["F-5202"]["status"] == sweep["C-5101"]["status"] == "rejected"
    assert "G4" in sweep["F-5202"]["failed"] and "G4" in sweep["C-5101"]["failed"]
    assert all(s["status"] != "survives" for s in gt["sweep"])


def test_a_rival_the_checks_cannot_tell_apart_keeps_it_not_confident(fresh):
    g = Graph(f"gaterival_{uuid.uuid4().hex[:8]}")
    client().connection.execute_command("GRAPH.COPY", graph_name("kestrel"), graph_name(g.tenant))
    try:   # a second deploy on auth a minute after D-5201: same path, same alerts, same footprint
        g.write("""MATCH (d:Change {id: 'D-5201'})-[:CHANGED]->(s:Service)
                   CREATE (:Change {id: 'D-5299', kind: 'deploy', at: d.at + 60, summary: 'auth: log format', risk: 'low'})-[:CHANGED]->(s)""")
        gt = agent.gate(g, "INC-302")
        g6 = next(c for c in gt["checks"] if c["id"] == "G6")
        assert gt["verdict"] == "not_confident" and not g6["passed"]
        rival = next(s for s in gt["sweep"] if s["status"] == "survives")
        assert {gt["leader"], rival["label"]} == {"D-5201", "D-5299"} and rival["label"] in g6["detail"]
    finally:
        g.drop()


def test_no_path_is_not_proof_of_safety(cut):
    r = tools.rank_suspects(cut, "INC-302")
    assert "D-5201" not in {c["id"] for c in r["ruled_out"]}          # not "ruled out" ...
    assert [(u["service"], u["changes"]) for u in r["unlinked"]] == [("auth", ["D-5201"])]   # ... but unlinked
    assert "Check the topology" in r["unlinked"][0]["reason"]
    gt = agent.gate(cut, "INC-302")
    assert gt["verdict"] == "cannot_prove" and gt["leader"] == "C-5101"
    g8 = next(c for c in gt["checks"] if c["id"] == "G8")
    assert not g8["passed"] and "auth started alerting 2 min before mobile-bff" in g8["detail"]
    d = next(s for s in gt["sweep"] if s["label"] == "D-5201")
    assert d["status"] == "not proven" and d["failed"] == ["G2"]


def test_alerts_that_belong_to_another_open_incident_are_not_a_topology_gap(fresh):
    # web-frontend alerts before mobile-bff (it calls INC-301's checkout): INC-301 accounts for it
    assert tools.rank_suspects(fresh, "INC-302")["unlinked"] == []


def test_break_it_reports_the_gate_before_and_after(fresh):
    r = site.break_it(fresh, "cut-path")
    assert r["before"]["gate"]["verdict"] == "confirmed"
    assert r["after"]["gate"]["verdict"] == "cannot_prove" and "G8" in r["after"]["gate"]["failed"]


def test_an_investigation_keeps_its_gate_run_in_the_graph(fresh):
    out = agent.investigate("kestrel", "INC-302")
    assert out["gate"]["verdict"] == "confirmed" and out["gate_written"]["tested"] == len(out["gate"]["sweep"])
    row = fresh.read("""
        MATCH (v:Investigation {id: $id})-[:GATED]->(gr:GateRun)
        OPTIONAL MATCH (gr)-[t:TESTED]->(x)
        RETURN gr.verdict AS verdict, gr.passed AS passed, collect({status: t.status, id: coalesce(x.id, x.name)}) AS tested""",
                      {"id": out["investigation_id"]})[0]
    assert row["verdict"] == "confirmed" and row["passed"] == 8
    tested = {t["id"]: t["status"] for t in row["tested"]}
    assert tested["D-5201"] == "leader" and tested["F-5202"] == "rejected"


def test_the_case_file_and_the_route_carry_the_gate(fresh):
    out = agent.preview("kestrel", "INC-302")
    assert out["gate"]["verdict"] == "confirmed" and "claim_check" not in out["gate"]
    assert out["claim_check"]["supported"] == out["claim_check"]["total"]
    with _client() as c:
        r = c.get("/api/kestrel/incidents/INC-302/gate")
        assert r.status_code == 200 and r.json()["leader"] == "D-5201"
        assert c.get("/api/kestrel/incidents/INC-999/gate").status_code == 404
