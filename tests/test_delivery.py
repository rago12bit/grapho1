"""The delivery layer: the commits each deploy shipped, which one to look at first, releases and delivery metrics."""
from faultline import agent, reasoning, seed, tools
from faultline.db import Graph


def test_the_commit_that_changed_request_path_code_comes_first(fresh):
    c = tools.commits_in(fresh, "D-5201", "INC-302")
    msgs = [x["message"] for x in c["commits"]]
    assert msgs[0] == "auth: validate tokens against the rotating JWKS cache"
    assert {"jwks", "rotating"} <= set(c["best"]["shared_words"])
    others = {x["message"]: x for x in c["commits"][1:]}
    assert others["auth: docs: JWKS rotation runbook"]["code_share"] == 0          # docs don't count as code
    assert others["auth: table tests for expired and rotated keys"]["code_share"] == 0


def test_the_argument_names_the_commit_and_the_graph_keeps_the_citation(fresh):
    r = agent.judged(fresh, "INC-302")
    lead = reasoning.build(fresh, r)["hypotheses"][0]
    ev = next(e for e in lead["supports"] if e["kind"] == "commit")
    assert "validate tokens" in ev["text"] and ev["cites"][0]["label"] == "Commit"
    agent.investigate("kestrel", "INC-302")
    cited = fresh.read("""
        MATCH (:Hypothesis {status: 'standing'})-[:SUPPORTED_BY]->(:Evidence {kind: 'commit'})-[:CITES]->(c:Commit)
        RETURN c.message AS m""")
    assert cited == [{"m": "auth: validate tokens against the rotating JWKS cache"}]


def test_the_investigator_looks_inside_the_deploy(fresh):
    out = agent.investigate_offline("kestrel", "INC-302", propose=False)
    step = next(s for s in out["steps"] if s["tool"] == "commits_in")
    assert step["role"] == "investigator" and step["result"]["best"]["message"].endswith("rotating JWKS cache")


def test_delivery_metrics_come_from_the_graph(fresh):
    m = {d["team"]: d for d in tools.delivery_metrics(fresh)}
    # INC-260 (pricing deploy, Checkout) and INC-221 (fraud-scoring deploy, Payments) are the failed deploys in
    # the last 90 days; INC-188's auth deploy is older
    assert m["Checkout"]["failed"] == 1 and m["Payments"]["failed"] == 1 and m["Identity"]["failed"] == 0
    assert m["Checkout"]["change_failure_rate"] == round(1 / m["Checkout"]["deploys_90d"], 3)
    assert m["Payments"]["restore_min"] == 62.5        # INC-212 (payments-db, 95 min) and INC-221 (fraud-scoring, 30 min)
    assert all(d["lead_min"] > 0 for d in m.values() if d["deploys"])


def test_boutique_releases_are_real_and_the_images_point_at_them():
    seed.build("boutique", force=True)
    g = Graph("boutique")
    tags = [r["t"] for r in g.read("MATCH (v:Version) RETURN v.tag AS t ORDER BY v.at")]
    assert tags[-1] == "v0.10.7" and len(tags) == 8
    built = g.read("MATCH (:Deployment)-[:RUNS_IMAGE]->(i:Image)-[:BUILT_FROM]->(v:Version) RETURN count(i) AS n, collect(DISTINCT v.tag) AS v")[0]
    assert built["n"] >= 10 and built["v"] == ["v0.10.7"]
    rel = tools.releases(g, "checkoutservice")
    assert rel and [r["at"] for r in rel] == sorted((r["at"] for r in rel), reverse=True)


def test_synthetic_commits_are_the_same_every_build(fresh):
    a = fresh.read("MATCH (:Change {id: 'D-5201'})-[:SHIPS]->(c:Commit) RETURN c.sha AS s ORDER BY s")
    seed.build("kestrel", force=True)
    assert Graph("kestrel").read("MATCH (:Change {id: 'D-5201'})-[:SHIPS]->(c:Commit) RETURN c.sha AS s ORDER BY s") == a
