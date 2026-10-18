"""User journeys and SLOs, and who was on call when: impact said in terms of what customers can't do."""
from faultline import seed, tools
from faultline.db import Graph


def test_an_incident_names_the_journeys_it_breaks_and_the_budget_it_burns(fresh):
    im = tools.impact(fresh, "payments-db", "INC-301")
    j = {x["journey"]: x for x in im["journeys"]}
    assert set(j) == {"checkout", "refunds"}
    assert j["checkout"]["budget_min"] == 43.2 and j["checkout"]["burned_min"] == 38.0      # 99.9% over 30 days; 38 min so far
    assert j["checkout"]["burned_share"] == round(38 / 43.2, 3)


def test_the_rota_says_who_was_on_call_then_not_now(fresh):
    week = 7 * 86400
    assert tools.on_call_at(fresh, "Identity", tools.now())["handle"] == "sam"              # on call now
    assert tools.on_call_at(fresh, "Identity", tools.now() - 9 * 86400)["handle"] == "noor"
    assert tools.on_call_at(fresh, "Identity", tools.now() - 2 * week - 3600)["handle"] == "sam"   # the rota came round
    assert tools.on_call_at(fresh, "Identity", tools.now() - 60 * week) is None             # before the rota on record
    assert tools.on_call_at(fresh, "Identity", tools.now() + 3 * week) is None              # after it: nobody is scheduled
    for team in seed.KESTREL["teams"].values():
        for k in range(seed.SHIFT_WEEKS - 1):
            at = tools.now() - k * week - 3600
            assert len(fresh.read("""MATCH (:Person)-[:ON_SHIFT]->(s:OnCallShift)-[:COVERS]->(:Team {name: $t})
                                     WHERE s.from <= $at AND $at < s.to RETURN s""", {"t": team, "at": at})) == 1


def test_slo_budget_counts_an_incident_against_the_journeys_that_need_its_cause(fresh):
    slo = {j["journey"]: {i["id"] for i in j["incidents"]} for j in tools.slo_status(fresh)}
    # INC-305 was noticed on web-frontend, which every journey uses, but the failure was on k8s-node-4's
    # catalog and users: it counts against search, sign-in and account, not checkout
    assert "INC-305" in slo["search"] and "INC-305" in slo["account"] and "INC-305" not in slo["checkout"]
    assert {"INC-301", "INC-304"} <= slo["checkout"] and slo["refunds"] == {"INC-301", "INC-304"}


def test_real_journeys_come_from_the_traces_without_invented_slos():
    seed.build("boutique", force=True)
    g = Graph("boutique")
    rows = g.read("MATCH (j:Journey) RETURN j.name AS n, j.slo AS slo, j.source AS src")
    assert len(rows) >= 10 and all(r["slo"] is None and r["src"] == "traces" for r in rows)
    order = {r["s"] for r in g.read("""MATCH (j:Journey)-[:USES]->(s:Service)
        WHERE j.name ENDS WITH 'CheckoutService/PlaceOrder' RETURN s.name AS s""")}
    # the traces stop at the cart (uninstrumented), but the client span names it, and the cart's datastore follows
    assert {"checkoutservice", "paymentservice", "cartservice", "redis"} <= order
    assert tools.slo_status(g) == []
