"""The runtime layer: deployments, pods, nodes and zones, images and config, from the real manifests (Boutique,
Sock Shop, Train Ticket) or Kestrel's synthetic cluster, and the judge's shared-host suspect."""
import json

import pytest
from fastapi.testclient import TestClient

from faultline import agent, eval as ev, manifests, reasoning, seed, tools
from faultline.db import Graph


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_a_failing_node_is_named_when_two_unrelated_services_fail_together(fresh):
    r = agent.judged(fresh, "INC-305")
    top = r["suspects"][0]
    assert top["type"] == "host" and top["service"] == "k8s-node-4"
    assert top["host"]["services"] == ["catalog", "users"] and top["footprint"]["cone_coverage"] == 1.0
    assert not r["other_faults"]                       # one cause, not two independent ones
    assert top["owner"]["team"] == "Platform" and top["recommended_action"]["target"] == "k8s-node-4"
    chain = reasoning.causal_chain(fresh, r)["links"]
    assert {(l["from_service"], l["to"]) for l in chain if l["to_label"] == "Node"} == {("catalog", "k8s-node-4"), ("users", "k8s-node-4")}


def test_without_placement_the_same_alerts_look_like_two_faults(fresh):
    fresh.write("MATCH (:Pod)-[r:ON_NODE]->() DELETE r")
    r = agent.judged(fresh, "INC-305")
    assert r["suspects"][0]["type"] == "signal" and r["suspects"][0]["service"] in ("catalog", "users")
    assert [o["service"] for o in r["other_faults"]] in (["catalog"], ["users"])


def test_chains_never_get_a_host_suspect(fresh):
    for inc in ("INC-301", "INC-302", "INC-303", "INC-304"):
        assert not [s for s in tools.rank_suspects(fresh, inc, limit=50)["suspects"] if s["type"] == "host"], inc


def test_draining_the_node_is_verified_by_the_alerts_clearing(fresh):
    act = tools.propose_action(fresh, "INC-305", "page_owner", "k8s-node-4", "Drain k8s-node-4")
    with _client() as c:
        out = c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "t"}).json()
    assert out["verification"]["verified"]
    with pytest.raises(KeyError):
        tools.propose_action(fresh, "INC-305", "page_owner", "k8s-node-99", "no such node")


def test_real_manifests_are_in_the_graph_and_secrets_are_not(tenants_built):
    tt = tools.runtime(Graph("trainticket"), "ts-travel-service")
    assert (tt["cpu_limit"], tt["mem_limit_mi"], tt["replicas"]) == (0.5, 2000.0, 1)
    assert tt["image"] == "codewisdom/ts-travel-service:1.0.0" and "nacos" in tt["config_maps"]
    ob = tools.runtime(Graph("boutique"), "adservice")
    assert ob["image"].endswith("adservice:v0.10.7") and ob["probe"] == "grpc"
    snap = json.loads(manifests.FIXTURE.read_text())
    values = [e for sys_ in snap["systems"].values() for d in sys_ for e in d["env"]
              if any(w in e["key"].upper() for w in manifests.SECRETISH)]
    assert values and all(e["value"] in (None, "<redacted>") for e in values)


def test_a_config_change_points_at_the_key_and_whoever_reads_it(fresh):
    rows = fresh.read("""
        MATCH (:Change {id: 'C-5101'})-[:SETS]->(k:ConfigKey)<-[:READS]-(d:Deployment)
        RETURN k.name AS key, collect(d.name) AS readers""")
    assert rows == [{"key": "pgbouncer.max_client_conn", "readers": ["payments-db"]}]


def test_a_resource_alert_cites_the_manifest_limit(tenants_built):
    """Wherever a suspect's alert is on CPU or memory, the argument cites the limit its manifest sets."""
    g = Graph("boutique")
    seen = 0
    for c in seed.boutique_cases()["open"]:
        r = agent.judged(g, c["id"])
        signal = {a["service"]: a["signal"] for a in r["alerts"]}
        for h in reasoning.build(g, r)["hypotheses"]:
            for e in h["supports"]:
                if e["kind"] == "limits":
                    seen += 1
                    assert signal[h["service"]].split(" ")[0] in ("cpu", "mem")
                    assert e["cites"] == [{"label": "Deployment", "id": h["service"]}]
    assert seen >= 3


def test_secret_values_never_reach_the_snapshot():
    doc = """
kind: Deployment
metadata: {name: api}
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: api
          image: example/api:1.2.3
          env:
            - {name: DB_PASSWORD, value: hunter2}
            - {name: API_TOKEN, value: abc}
            - {name: LOG_LEVEL, value: info}
            - name: DB_URL
              valueFrom: {secretKeyRef: {name: db, key: url}}
"""
    d = manifests.parse(doc, "x")[0]
    assert {e["key"]: e["value"] for e in d["env"]} == {"DB_PASSWORD": "<redacted>", "API_TOKEN": "<redacted>",
                                                        "LOG_LEVEL": "info", "DB_URL": None}
    assert d["secrets"] == ["db"] and (d["image"], d["tag"], d["replicas"]) == ("example/api", "1.2.3", 2)


def test_a_service_that_calls_another_never_shares_a_host_suspect(fresh):
    """cart calls pricing. Even if both fail on their own on the same node, the call between them is the better
    explanation, so no host suspect is made."""
    fresh.write("""
        MATCH (:Service {name: 'pricing'})-[:DEPLOYED_AS]->(d:Deployment), (n:Node {name: 'k8s-node-4'})
        CREATE (d)-[:HAS_POD]->(:Pod {name: 'pricing-x'})-[:ON_NODE]->(n)""")
    fresh.write("""
        MATCH (:Service {name: 'cart'})-[:DEPLOYED_AS]->(d:Deployment), (n:Node {name: 'k8s-node-4'})
        CREATE (d)-[:HAS_POD]->(:Pod {name: 'cart-x'})-[:ON_NODE]->(n)""")
    fp = {"explained": [], "unexplained": [], "observed": 2, "cone": [], "cone_coverage": 0.5, "sequence": [], "in_order": True}
    sig = lambda svc, at: {"service": svc, "first_alert": at, "footprint": fp, "hops": 1, "path": ["checkout", svc],  # noqa: E731
                           "minutes_before": 1, "evidence_alerts": [], "factors": {"alert_chain": 1.0, "unchanged": 1.0}}
    assert tools._host_suspects(fresh, [sig("cart", 100), sig("pricing", 160)], [], 200, {}, 0.0) == []
    assert tools._host_suspects(fresh, [sig("cart", 100), sig("catalog", 160)], [], 200, {}, 0.0)   # unrelated: one


def test_the_eval_plants_host_faults_and_finds_them():
    res = ev.run(n=15, seed=11, learning=False)
    assert res["phase1"]["counts"]["host"] == 3 and res["phase1"]["top1"]["full"]["host"] == 100.0


@pytest.fixture(scope="module")
def tenants_built():
    seed.build("boutique", force=True)
    seed.build("trainticket", force=True)
