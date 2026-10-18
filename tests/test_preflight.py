"""Before a change ships: ship, review or hold, each reason from the graph, nothing written."""
import pytest
from fastapi.testclient import TestClient

from faultline import preflight


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def _size(g):
    return (g.read("MATCH (n) RETURN count(n) AS n")[0]["n"], g.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"])


def test_a_deploy_on_a_service_the_judge_blames_is_held(fresh):
    p = preflight.preflight(fresh, "auth", "deploy")
    assert p["decision"] == "hold"
    holds = [r for r in p["reasons"] if r["level"] == "hold"]
    assert holds[0]["cites"] == ["INC-302", "D-5201"]
    reviews = " ".join(r["text"] for r in p["reasons"] if r["level"] == "review")
    assert "INC-188" in reviews and "D-5201" in reviews            # a deploy on auth caused INC-188; D-5201 is stacked
    assert p["owner"]["team"] == "Identity" and p["reviewers"]


def test_a_quiet_leaf_ships_and_a_chokepoint_with_a_recent_change_gets_a_reviewer(fresh):
    assert preflight.preflight(fresh, "ledger-db", "config")["decision"] == "ship"
    p = preflight.preflight(fresh, "notifications", "deploy")
    assert p["decision"] == "review"
    assert p["centrality"]["betweenness_rank"] is not None and [c["id"] for c in p["recent_changes"]] == ["D-5301"]


def test_it_reads_only(fresh):
    before = _size(fresh)
    for svc, kind in [("auth", "deploy"), ("payments-db", "config"), ("search", "flag")]:
        preflight.preflight(fresh, svc, kind, "rotate the token cache")
    assert _size(fresh) == before


def test_a_service_the_graph_does_not_have_is_not_called_safe(fresh):
    with pytest.raises(KeyError, match="isn't modelled"):
        preflight.preflight(fresh, "billing-svc")
    with pytest.raises(ValueError):
        preflight.preflight(fresh, "auth", "reboot")


def test_the_routes(fresh):
    with _client() as c:
        got = c.get("/api/kestrel/preflight", params={"service": "auth", "kind": "deploy"}).json()
        assert got["decision"] == "hold" and got["traces"]
        assert c.post("/api/kestrel/preflight", json={"service": "ledger-db", "kind": "config"}).json()["decision"] == "ship"
        assert c.get("/api/kestrel/preflight", params={"service": "billing-svc"}).status_code == 404
        assert c.post("/api/kestrel/preflight", json={"service": "auth", "kind": "reboot"}).status_code == 422
