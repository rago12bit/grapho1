"""Acting once, landing the fix and checking it twice, and paging: page, acknowledge, escalate, standing approvals."""
import threading

import pytest
from fastapi.testclient import TestClient

from faultline import agent, paging, tools, world
from faultline.db import Graph


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def _propose_rollback(g):
    return tools.propose_action(g, "INC-302", "rollback", "auth", "Roll back auth 4.2.0 to 4.1.7", change_id="D-5201")["id"]


def test_twenty_four_approvals_at_once_run_the_fix_exactly_once(fresh):
    aid = _propose_rollback(fresh)
    won, refused = [], []

    def approve(i):
        try:
            won.append(tools.decide_action(Graph("kestrel"), aid, True, f"person-{i}"))
        except ValueError:
            refused.append(i)
    threads = [threading.Thread(target=approve, args=(i,)) for i in range(24)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(won) == 1 and len(refused) == 23
    n = fresh.read("""MATCH (a:Action {id: $id})
                      OPTIONAL MATCH (a)-[:CREATED]->(r:Change) OPTIONAL MATCH (cr:ChangeRequest)-[:FOR]->(a)
                      RETURN count(DISTINCT r) AS remediations, count(DISTINCT cr) AS requests""", {"id": aid})[0]
    assert n == {"remediations": 1, "requests": 1}


def test_an_approved_rollback_is_a_change_request_that_lands_and_works(fresh):
    with _client() as c:
        aid = _propose_rollback(fresh)
        out = c.post(f"/api/kestrel/actions/{aid}", json={"approve": True, "who": "sam"}).json()
        assert out["applied"]["state"] == "APPLIED" and out["verification"]["verified"] is True
        act = next(a for a in c.get("/api/kestrel/incidents/INC-302").json()["actions"] if a["id"] == aid)
    cr = act["change_request"]
    assert cr["path"] == "deploy/auth.yaml" and "-  image: auth:4.2.0" in cr["diff"] and "+  image: auth:4.1.7" in cr["diff"]
    assert cr["status"] == "applied" and act["applied"] == "APPLIED"


def test_a_deploy_that_never_lands_is_not_applied_and_nothing_recovers(fresh):
    world.FAIL_ROLLOUT.add("auth")
    try:
        with _client() as c:
            out = c.post(f"/api/kestrel/actions/{_propose_rollback(fresh)}", json={"approve": True, "who": "sam"}).json()
    finally:
        world.FAIL_ROLLOUT.discard("auth")
    assert out["applied"]["state"] == "NOT_APPLIED" and "auth:4.2.0" in out["applied"]["detail"]
    assert out["verification"]["verified"] is False


def test_the_decoy_lands_but_does_not_work(fresh):
    # rolling back the cart deploy is deployed fine, and clears nothing: a wrong fix, not a failed deploy
    aid = tools.propose_action(fresh, "INC-301", "rollback", "cart", "Roll back cart", change_id="D-5102")["id"]
    with _client() as c:
        out = c.post(f"/api/kestrel/actions/{aid}", json={"approve": True, "who": "priya"}).json()
    assert out["applied"]["state"] == "APPLIED" and out["verification"]["verified"] is False


def test_the_page_goes_to_the_owner_of_what_broke_and_escalates_down_the_chain(fresh):
    p1 = paging.page(fresh, "INC-302")
    assert (p1["level"], p1["name"], p1["status"]) == (1, "Sam Whitfield", "paged") and "Identity" in p1["why"]
    assert paging.page(fresh, "INC-302")["already_paged"]                 # paging again returns the live page
    p2 = paging.escalate(fresh, p1["id"])
    assert (p2["level"], p2["name"]) == (2, "Noor Haddad") and p2["status"] == "paged"
    with pytest.raises(ValueError):
        paging.acknowledge(fresh, p1["id"], "sam")                         # the escalated page is closed
    assert paging.acknowledge(fresh, p2["id"], "noor")["status"] == "acked"
    with pytest.raises(ValueError):
        paging.escalate(fresh, p2["id"])                                   # an acknowledged page doesn't escalate
    chain = next(x for x in paging.pages(fresh) if x["incident"] == "INC-302")["chain"]
    assert [c["status"] for c in chain] == ["escalated", "acked"]
    assert fresh.read("MATCH (:Page {id: $a})-[:ESCALATED_TO]->(:Page {id: $b}) RETURN count(*) AS n",
                      {"a": p1["id"], "b": p2["id"]})[0]["n"] == 1


def test_an_unanswered_page_escalates_on_its_own_and_the_chain_can_run_out(fresh, monkeypatch):
    p = paging.page(fresh, "INC-302")
    monkeypatch.setattr(paging, "ESCALATE_S", 0)
    statuses = []
    for _ in range(4):
        statuses += [x["status"] for x in paging.escalate_overdue(fresh)]
    chain = next(x for x in paging.pages(fresh) if x["incident"] == "INC-302")["chain"]
    assert chain[0]["id"] == p["id"] and chain[-1]["status"] == "exhausted"
    assert len({c["handle"] for c in chain if c["handle"]}) == len([c for c in chain if c["handle"]])   # nobody twice


def test_two_acknowledgements_at_once_one_wins(fresh):
    p = paging.page(fresh, "INC-301")
    got = []

    def ack(i):
        try:
            got.append(paging.acknowledge(Graph("kestrel"), p["id"], f"p{i}"))
        except ValueError:
            pass
    threads = [threading.Thread(target=ack, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == 1


def test_a_standing_approval_fixes_a_repeat_fault_without_waking_anyone(fresh):
    sa = paging.grant_standing(fresh, "rollback", "auth", "sam", uses=1, days=30)
    with _client() as c:
        out = c.post("/api/kestrel/incidents/INC-302/investigate").json()
    st = out["standing"]
    assert st["fired"] and st["approval"] == sa["id"] and st["reasons"] == []
    assert st["applied"]["state"] == "APPLIED" and st["verification"]["verified"] is True
    act = next(a for a in tools.list_actions(fresh, "INC-302") if a["id"] == st["action"])
    assert act["status"] == "executed" and act["decided_by"] == f"standing approval {sa['id']}" and act["standing"] == sa["id"]
    chain = next(x for x in paging.pages(fresh) if x["incident"] == "INC-302")["chain"]
    assert [c["status"] for c in chain] == ["notice"]                     # the owner is told, nobody is paged
    assert paging.page(fresh, "INC-302")["status"] == "notice"
    assert next(x for x in paging.standing(fresh) if x["id"] == sa["id"])["uses_left"] == 0


def test_outside_its_terms_a_standing_approval_does_nothing_and_says_why(fresh):
    sa = paging.grant_standing(fresh, "rollback", "auth", "sam", uses=1)
    fresh.write("MATCH (sa:StandingApproval {id: $id}) SET sa.uses_left = 0", {"id": sa["id"]})
    out = agent.investigate("kestrel", "INC-302")
    assert out["standing"]["fired"] is False and out["standing"]["reasons"] == ["no uses left"]
    assert next(a for a in tools.list_actions(fresh, "INC-302") if a["id"] == out["standing"]["action"])["status"] == "proposed"
    gate = {"verdict": "not_confident", "verdict_label": "Not confident", "leader": "D-5201",
            "sweep": [{"status": "leader", "change": "D-5201"}]}
    fresh.write("MATCH (sa:StandingApproval {id: $id}) SET sa.uses_left = 1", {"id": sa["id"]})
    why = paging.try_standing(fresh, out["standing"]["action"], gate)
    assert not why["fired"] and why["reasons"] == ["the evidence gate says not confident, not confirmed"]
    decisions = next(x for x in paging.standing(fresh) if x["id"] == sa["id"])["decisions"]
    assert [d["fired"] for d in decisions] == [False, False]


def test_the_paging_routes(fresh):
    with _client() as c:
        p = c.post("/api/kestrel/incidents/INC-302/page").json()
        assert p["name"] == "Sam Whitfield"
        assert c.post(f"/api/kestrel/pages/{p['id']}/ack", json={"who": "sam"}).json()["status"] == "acked"
        assert c.post(f"/api/kestrel/pages/{p['id']}/ack", json={"who": "sam"}).status_code == 400
        assert c.post("/api/kestrel/pages/tick").json() == {"escalated": []}
        sa = c.post("/api/kestrel/standing", json={"kind": "rollback", "service": "auth", "who": "sam"}).json()
        oc = c.get("/api/kestrel/oncall").json()
        assert oc["paging"][0]["current"]["status"] == "acked" and oc["standing"][0]["id"] == sa["id"]
        assert c.post(f"/api/kestrel/standing/{sa['id']}/revoke", json={"who": "sam"}).json()["active"] is False
        assert c.post("/api/kestrel/standing", json={"kind": "reboot", "service": "auth"}).status_code == 422
        assert c.post("/api/kestrel/standing", json={"kind": "rollback", "service": "nope"}).status_code == 404
