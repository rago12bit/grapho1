"""What the site's pages read: the engine, the on-call view, the whole graph, and break-it experiments on a copy."""
import pytest
from fastapi.testclient import TestClient

from faultline import site
from faultline.db import list_tenants


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


# what each experiment does to INC-302 / INC-301 on Kestrel: the leader after, and its confidence
AFTER = {"cut-path": ("C-5101", "medium"), "late-deploy": ("F-5202", "low"), "blind-auth": ("C-5101", "low"),
         "users-first": ("F-5202", "low"), "not-the-cause": ("F-5202", "low"), "low-risk": ("D-5201", "medium"),
         "pool-gone": ("payments-db (no change)", "high")}


@pytest.mark.parametrize("exp", ["cut-path", "late-deploy", "blind-auth", "users-first", "not-the-cause", "low-risk", "pool-gone"])
def test_each_experiment_moves_the_verdict_as_it_says(fresh, exp):
    r = site.break_it(fresh, exp)
    assert r["moved"]
    assert (r["after"]["leader"], r["after"]["confidence"]) == AFTER[exp]
    assert r["before"]["leader"] == ("C-5101" if r["experiment"]["incident"] == "INC-301" else "D-5201")


def test_experiments_run_on_a_copy_and_leave_the_real_graph_alone(fresh):
    copies = {t for t in list_tenants() if t.startswith("break_")}
    calls = fresh.read("MATCH ()-[r:CALLS]->(:Service {name: 'auth'}) RETURN count(r) AS n")[0]["n"]
    site.break_it(fresh, "cut-path")
    site.break_it(fresh, "not-the-cause")
    assert fresh.read("MATCH ()-[r:CALLS]->(:Service {name: 'auth'}) RETURN count(r) AS n")[0]["n"] == calls > 0
    assert fresh.read("MATCH (f:Feedback) RETURN count(f) AS n")[0]["n"] == 0
    assert {t for t in list_tenants() if t.startswith("break_")} == copies     # each copy is dropped


def test_the_shown_write_is_the_one_that_runs():
    assert [e["id"] for e in site.EXPERIMENTS] == list(AFTER)        # every experiment is checked above
    for e in site.EXPERIMENTS:
        shown = "\n".join(site.mutation(e))
        for q in e.get("cypher", []):
            assert "\n".join(line.strip() for line in q.strip().splitlines()) in shown


def test_oncall_pages_the_team_that_owns_the_blamed_service(fresh):
    d = site.oncall(fresh)
    by = {p["incident"]: p for p in d["pages"]}
    # INC-302 is noticed on mobile-bff but blamed on auth's deploy: Identity is paged, not mobile-bff's owners
    assert by["INC-302"]["blamed"] == "D-5201" and by["INC-302"]["team"] == "Identity"
    assert by["INC-302"]["page"]["name"] == "Sam Whitfield"
    assert by["INC-305"]["team"] == "Platform"
    assert all(t["now"] for t in d["teams"]) and len(d["teams"]) == 6
    now = {t["team"]: t["now"] for t in d["teams"]}
    assert now["Identity"]["name"] == "Sam Whitfield" and now["Checkout"]["name"] == "Priya Raman"   # the seed's on-call people
    assert all(x["from"] <= d["now"] < x["to"] for x in now.values())
    assert {t["team"]: t["next"]["name"] for t in d["teams"]}["Identity"] == "Noor Haddad"
    assert d["stats"]["open"] == len(d["pages"]) == 5


def test_explore_returns_the_whole_graph_past_the_tool_row_cap(fresh):
    d = site.explore(fresh)
    n = fresh.read("MATCH (n) RETURN count(n) AS n")[0]["n"]
    assert len(d["nodes"]) == n > 200 and len(d["edges"]) > 200
    keys = {x["key"] for x in d["nodes"]}
    assert all(e["source"] in keys and e["target"] in keys for e in d["edges"])
    only = site.explore(fresh, ["Service"])
    assert {x["label"] for x in only["nodes"]} == {"Service"} and len(only["nodes"]) == 24


def test_a_node_opens_with_its_neighbours(fresh):
    key = fresh.read("MATCH (s:Service {name: 'auth'}) RETURN id(s) AS k")[0]["k"]
    d = site.node(fresh, key)
    assert d["label"] == "Service" and d["props"]["name"] == "auth"
    assert {"CALLS", "CHANGED", "OWNED_BY"} <= {n["type"] for n in d["neighbours"]}
    with pytest.raises(KeyError):
        site.node(fresh, 10 ** 9)


def test_engine_reports_falkordb_and_every_graph(fresh):
    e = site.engine()
    assert e["engine"] == "FalkorDB" and e["version"].count(".") == 2
    kestrel = next(g for g in e["graphs"] if g["tenant"] == "kestrel")
    assert kestrel["nodes"] == fresh.read("MATCH (n) RETURN count(n) AS n")[0]["n"]


def test_the_experiment_routes(fresh):
    with _client() as c:
        assert len(c.get("/api/experiments").json()) == 7
        assert c.post("/api/kestrel/experiments/low-risk/run").json()["after"]["leader"] == "D-5201"
        assert c.post("/api/harbor/experiments/low-risk/run").status_code == 400
        assert c.post("/api/kestrel/experiments/nope/run").status_code == 404
