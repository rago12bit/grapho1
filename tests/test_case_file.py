"""The case-file API behind the UI, and the Boutique tenant built from real RCAEval failures."""
import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faultline import agent, seed, tools, world
from faultline.db import Graph


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


@pytest.fixture(scope="module")
def boutique():
    seed.build("boutique", force=True)
    return Graph("boutique")


# --- Boutique: real failures -------------------------------------------------------------------------

def test_boutique_is_built_from_the_benchmark(boutique):
    data = seed.boutique_cases()
    assert len(data["open"]) == 18 and len(data["done"]) == 36
    assert {c["rep"] for c in data["open"]} == {3} and {c["rep"] for c in data["done"]} == {1, 2}
    incs = tools.list_incidents(boutique, None)
    assert sum(i["status"] == "open" for i in incs) == 18 and sum(i["status"] == "resolved" for i in incs) == 36
    # call graph straight from the traces, alerts carry the real metric trace
    edges = {(r["a"], r["b"]) for r in boutique.read("MATCH (a:Service)-[:CALLS]->(b:Service) RETURN a.name AS a, b.name AS b")}
    assert ("frontend", "checkoutservice") in edges and ("cartservice", "redis") in edges
    t = tools.timeline(boutique, "INC-401")
    assert t["alerts"] and all(len(a["series"]) == 61 for a in t["alerts"])
    # no two cases overlap in time, so a case's window holds only its own alerts
    case = next(c for c in data["open"] if c["id"] == "INC-401")
    assert {a["id"] for a in t["alerts"]} == {a["id"] for a in case["alerts"]}


def test_open_titles_do_not_leak_the_answer(boutique):
    for c in seed.boutique_cases()["open"]:
        title = tools.get_incident(boutique, c["id"])["title"]
        assert c["root_cause"] not in title and c["fault"] not in title


def test_boutique_judge_score_is_what_we_publish(boutique):
    """README quotes 16/18 first and 16/18 in the top three on the open Boutique cases, with the weights learned
    from its solved incidents (hand-set weights: 15 and 16; v1 on traced edges alone: 15 and 17)."""
    hit = hit3 = 0
    for c in seed.boutique_cases()["open"]:
        names = [s["service"] for s in agent.judged(boutique, c["id"])["suspects"]]
        hit += names[:1] == [c["root_cause"]]
        hit3 += c["root_cause"] in names[:3]
    assert (hit, hit3) == (16, 16)


def test_score_ties_go_to_whatever_started_first(boutique, monkeypatch):
    """INC-405 (RCAEval recommendationservice loss): without anomaly strength, three suspects score exactly the
    same. The one whose alert came first leads, every time, instead of whichever row the database returned first."""
    monkeypatch.setattr(tools, "STRENGTH_W", 0.0)
    monkeypatch.setattr(tools, "learned_model", lambda g: None)
    r = tools.rank_suspects(boutique, "INC-405")
    a, b = r["suspects"][:2]
    assert a["score"] == b["score"] and a["first_alert"] < b["first_alert"]
    assert a["service"] == "recommendationservice"


def test_boutique_fix_is_checked_against_rcaeval(boutique):
    case = next(c for c in seed.boutique_cases()["open"] if c["id"] == "INC-402")
    wrong = "paymentservice" if case["root_cause"] != "paymentservice" else "emailservice"
    with _client() as c:
        bad = tools.propose_action(boutique, "INC-402", "page_owner", wrong, "Page the wrong team")
        out = c.post(f"/api/boutique/actions/{bad['id']}", json={"approve": True, "who": "t"}).json()
        assert not out["verification"]["verified"]
        good = tools.propose_action(boutique, "INC-402", "page_owner", case["root_cause"], "Page the owner")
        out = c.post(f"/api/boutique/actions/{good['id']}", json={"approve": True, "who": "t"}).json()
        assert out["verification"]["verified"]
    seed.build("boutique", force=True)


def test_answer_key_stays_out_of_the_agents():
    """world.py now reads RCAEval's answer key; the agents and the judge still never import world."""
    for mod in ("agent", "tools", "db"):
        tree = ast.parse((Path(__file__).parent.parent / "faultline" / f"{mod}.py").read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "world" not in names and not any(m and m.endswith("world") for m in mods)
    assert world.TRUE_CAUSES["boutique"]


# --- the case file ------------------------------------------------------------------------------------

def test_preview_is_the_agents_verdict_without_running_them(fresh):
    for inc in ("INC-301", "INC-302", "INC-303"):
        p = agent.preview("kestrel", inc)
        assert p["mode"] == "preview" and not p["steps"]
        assert p["claim_check"]["supported"] == p["claim_check"]["total"]
        run = agent.investigate("kestrel", inc)
        assert (p["findings"]["change_id"], p["findings"]["root_cause_service"]) == \
               (run["findings"]["change_id"], run["findings"]["root_cause_service"])
    # opening a case writes nothing
    assert not fresh.read("MATCH (v:Investigation)-[:INVESTIGATED]->(:Incident {id: 'INC-999'}) RETURN v")


def test_impact_blocks_name_path_exposure_owner_and_alerting(fresh):
    im = tools.impact(fresh, "payments-db", "INC-301")
    by = {b["service"]: b for b in im["blocks"]}
    assert im["affected"] == 5 and im["exposed"] == 3
    assert by["api-gateway"]["path"] == ["payments-db", "payments", "checkout", "api-gateway"]
    assert by["api-gateway"]["exposed"] and by["api-gateway"]["on_call"] == "Ravi Menon"
    assert by["payments"]["alerting"] and by["checkout"]["alerting"] and not by["api-gateway"]["alerting"]
    # internet-facing first
    assert [b["exposed"] for b in im["blocks"]] == sorted([b["exposed"] for b in im["blocks"]], reverse=True)


def test_board_shows_the_same_verdict_as_the_case_file(boutique):
    """The board must show the skeptic-reviewed verdict the case file opens with, wrong ones included, with the
    same confidence: INC-406 is a checkoutservice fault the judge blames on shippingservice, at low confidence."""
    with _client() as c:
        rows = {r["id"]: r for r in c.get("/api/boutique/board").json()}
        for inc in ("INC-401", "INC-406", "INC-418"):
            case = c.get(f"/api/boutique/incidents/{inc}/case").json()["ranking"]
            assert rows[inc]["leader_service"] == case["suspects"][0]["service"]
            assert rows[inc]["confidence"] == case["confidence"]
    assert rows["INC-406"]["leader_service"] == "shippingservice" and rows["INC-406"]["confidence"] == "low"
    assert "leader" not in rows["INC-101"]


def test_case_file_endpoints(fresh):
    with _client() as c:
        case = c.get("/api/kestrel/incidents/INC-301/case").json()
        assert case["ranking"]["impact"]["service"] == "payments-db" and case["graph"]["queries"] > 0
        assert {a["service"] for a in case["timeline"]["alerts"]} >= {"payments-db", "payments", "checkout"}
        card = c.get("/api/kestrel/services/payments-db").json()
        assert card["team"] == "Payments" and card["on_call"]["name"] == "Adebayo Okafor"
        assert card["experts"][0]["handle"] == "marta" and card["history"][0]["incident"] == "INC-212"
        assert {x["service"] for x in card["callers"]} >= {"payments", "checkout", "api-gateway"}
        assert card["traces"] and all("cypher" in q for q in card["traces"])
        assert c.get("/api/kestrel/services/nope").status_code == 404
        org = c.get("/api/kestrel/company").json()
        assert {t["team"] for t in org["teams"]} == set(seed.KESTREL["teams"].values())
        st = c.get("/api/kestrel/memory/stats").json()
        assert st["solved"] == 8 and st["lessons"] == 8 and st["shapes"] == 8
        hits = c.get("/api/kestrel/memory/search", params={"q": "pgbouncer"}).json()["hits"]
        assert any(h["ref"] == "C-5101" for h in hits)
        assert c.get("/static/js/main.js").headers["cache-control"] == "no-cache"


def test_header_numbers_match_the_repo():
    """The header shows the test count; it must be the real one."""
    from faultline.api import PROOF
    n = 0
    for f in Path(__file__).parent.glob("test_*.py"):
        tree = ast.parse(f.read_text())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                params = [d for d in node.decorator_list if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "parametrize"]
                if params and isinstance(params[0].args[1], ast.Name):   # the cases are a module-level list
                    named = next(a.value for a in tree.body if isinstance(a, ast.Assign)
                                 and any(getattr(t, "id", None) == params[0].args[1].id for t in a.targets))
                    n += len(named.elts)
                else:
                    n += len(params[0].args[1].elts) if params else 1
    assert PROOF["tests"] == n
