"""A fix is proved on a copy of the graph before anyone approves it, and the outcome is checked against that."""
import pytest
from fastapi.testclient import TestClient

from faultline import proof, tools, world
from faultline.db import Graph, list_tenants

FIXES = [  # incidents, kind, target, change: the right fix for each planted Kestrel fault
    (["INC-301", "INC-304"], "revert_config", "payments-db", "C-5101"),
    (["INC-302"], "rollback", "auth", "D-5201"),
    (["INC-303"], "page_owner", "catalog-es", None),
    (["INC-305"], "page_owner", "k8s-node-4", None),
]


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


@pytest.mark.parametrize("incidents, kind, target, change", FIXES)
def test_the_prediction_is_what_actually_clears(fresh, incidents, kind, target, change):
    p = proof.preview_fix(fresh, incidents, kind, target, change)
    assert p["verdict"] == "resolves"
    truly = {a for a, cause in world.TRUE_CAUSES["kestrel"].items() if cause in (change, f"infra:{target}")}
    assert set(p["predicted_clear"]) <= truly          # nothing predicted that the fix wouldn't really clear
    act = tools.propose_action(fresh, incidents[0], kind, target, "fix", change, also_for=incidents[1:])
    with _client() as c:
        out = c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "t"}).json()
    v = out["verification"]
    assert v["verified"] and v["summary"].startswith("As predicted on the graph copy.")
    assert all(r["as_predicted"] for r in v["incidents"])


def test_a_decoy_fix_is_shown_to_do_nothing_before_and_fails_after(fresh):
    act = tools.propose_action(fresh, "INC-301", "rollback", "cart", "Roll back cart", "D-5102")
    assert act["preview"]["verdict"] == "clears nothing" and act["preview"]["predicted_clear"] == []
    with _client() as c:
        out = c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "t"}).json()
    assert not out["verification"]["verified"] and out["verification"]["summary"].startswith("Not what")
    assert tools.get_incident(fresh, "INC-301")["status"] == "open"


def test_a_partial_fix_says_what_it_leaves_and_why(fresh):
    """Reverting the pool change while the symptom is mobile-bff's: INC-302's own alerts stay, and the judge on the
    copy says what explains them then."""
    p = proof.preview_fix(fresh, ["INC-301", "INC-302"], "revert_config", "payments-db", "C-5101")
    by = {i["incident"]: i for i in p["incidents"]}
    assert p["verdict"] == "partial" and by["INC-301"]["resolves"]
    assert {x["service"] for x in by["INC-302"]["leaves"]} == {"auth", "mobile-bff"}
    assert by["INC-302"]["then_explained_by"] == "D-5201"


def test_the_proof_never_touches_the_real_graph(fresh):
    copies = {t for t in list_tenants() if t.startswith("proof_")}
    before = fresh.read("MATCH (a:Alert) WHERE a.cleared_at IS NOT NULL RETURN count(a) AS n")[0]["n"]
    proof.preview_fix(fresh, ["INC-302"], "rollback", "auth", "D-5201")
    assert fresh.read("MATCH (a:Alert) WHERE a.cleared_at IS NOT NULL RETURN count(a) AS n")[0]["n"] == before
    assert fresh.read("MATCH (a:Alert {id: 'A-9101'}) RETURN count(a) AS n")[0]["n"] == 1
    assert fresh.read("MATCH ()-[r:CAUSED_BY]->() RETURN count(r) AS n")[0]["n"] == 0
    assert {t for t in list_tenants() if t.startswith("proof_")} == copies   # the copy it made is gone
