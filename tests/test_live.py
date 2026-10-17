"""Track record, postmortem export, live webhooks, the in-app benchmark, uploads, public mode, real owners, theme."""
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faultline import api, bench, ingest, postmortem, seed, tools, upload
from faultline.db import Graph

ROOT = Path(__file__).resolve().parent.parent
T0 = seed.NOW + 7200


def _client():
    return TestClient(api.app)


def _am(fp, svc, start, summary="p99 high", end=None, severity="page"):
    a = {"status": "resolved" if end else "firing", "fingerprint": fp,
         "labels": {"alertname": "HighLatency", "service": svc, "severity": severity},
         "annotations": {"summary": summary}, "startsAt": start}
    if end:
        a["endsAt"] = end
    return a


def _fire(c, resolve_at=None):
    alerts = [_am("fp-1", "fraud-scoring", T0 + 180, end=resolve_at), _am("fp-2", "payments", T0 + 300, end=resolve_at),
              _am("fp-3", "checkout", T0 + 420, end=resolve_at)]
    return c.post("/api/kestrel/alerts/alertmanager",
                  json={"status": "resolved" if resolve_at else "firing", "alerts": alerts}).json()


def _deploy(c):
    return c.post("/api/kestrel/changes", json={"kind": "deploy", "service": "fraud-scoring", "version": "2.8.0",
                                                "author": "ade", "risk": "high", "at": T0,
                                                "summary": "fraud-scoring 2.8.0: model warm-up on first request"}).json()


# --- procedural memory --------------------------------------------------------------------------------------

def test_fix_track_record_counts_what_worked_and_what_did_not(fresh):
    rec = {(r["kind"], r["service"]): r for r in tools.fix_track_record(fresh)}
    assert (rec[("revert_config", "payments-db")]["worked"], rec[("revert_config", "payments-db")]["failed"]) == (1, 0)
    assert (rec[("rollback", "payments")]["worked"], rec[("rollback", "payments")]["failed"]) == (0, 1)
    # the responder cites it for the fix it proposes
    top = tools.rank_suspects(fresh, "INC-301")["suspects"][0]
    assert top["recommended_action"]["track_record"]["incidents"] == ["INC-212"]
    # a fix that does not clear the alerts adds a failure
    wrong = tools.propose_action(fresh, "INC-301", "rollback", "cart", "Roll back cart", change_id="D-5102")
    with _client() as c:
        c.post(f"/api/kestrel/actions/{wrong['id']}", json={"approve": True, "who": "t"})
    after = {(r["kind"], r["service"]): r for r in tools.fix_track_record(fresh)}
    assert after[("rollback", "cart")]["failed"] == 2


def test_a_failed_fix_is_not_recorded_as_what_resolved_it(fresh):
    wrong = tools.propose_action(fresh, "INC-301", "rollback", "cart", "Roll back cart", change_id="D-5102")
    with _client() as c:
        c.post(f"/api/kestrel/actions/{wrong['id']}", json={"approve": True, "who": "t"})
    tools.resolve_incident(fresh, "INC-301", "C-5101", "Pool limits need a load test.")
    by = fresh.read("MATCH (:Incident {id: 'INC-301'})-[:RESOLVED_BY]->(a:Action) RETURN a.id AS id")
    assert wrong["id"] not in {r["id"] for r in by}


# --- postmortem ----------------------------------------------------------------------------------------------

def test_postmortem_is_built_from_the_graph(fresh):
    with _client() as c:
        draft = c.get("/api/kestrel/incidents/INC-301/postmortem.md")
        assert "Draft postmortem" in draft.text and "draft.md" in draft.headers["content-disposition"]
        c.post("/api/kestrel/incidents/INC-301/investigate")
        act = c.get("/api/kestrel/incidents/INC-301").json()["actions"][0]
        c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "ade"})
        c.post("/api/kestrel/incidents/INC-301/resolve", json={"change_id": "C-5101", "lesson": "Load-test pool limits."})
        md = c.get("/api/kestrel/incidents/INC-301/postmortem.md").text
    assert md.startswith("# INC-301") and "Draft" not in md.split("\n")[2]
    assert "**C-5101** (config) on `payments-db`" in md and "made by Lin Zhao" in md
    assert "Verified: INC-301: all alerts cleared" in md and "Covered INC-301, INC-304" in md
    assert "> Load-test pool limits." in md and "## Appendix" in md
    assert re.search(r"\| 13:16 \| alert on `payments-db`", md)


# --- live input ------------------------------------------------------------------------------------------------

def test_webhook_opens_an_incident_and_the_agents_name_the_new_deploy(fresh):
    with _client() as c:
        assert _deploy(c)["id"] == "D-6001"
        out = _fire(c)
        assert out["opened"] == ["INC-501"] and len(out["created"]) == 3
        inv = out["investigations"][0]
        assert inv["root_cause"] == "D-6001" and inv["confidence"] == "high"
        inc = tools.get_incident(fresh, "INC-501")
        assert inc["symptom"] == "checkout" and inc["severity"] == "SEV2"
        # the same batch again changes nothing
        again = _fire(c)
        assert again["created"] == [] and again["opened"] == [] and len(again["updated"]) == 3
        # an alert for something that isn't in the graph is reported, not fatal
        odd = c.post("/api/kestrel/alerts/alertmanager", json={"alerts": [_am("fp-x", "nope", T0)]}).json()
        assert odd["ignored"][0]["service"] == "nope"


def test_live_fix_waits_for_monitoring_then_verifies(fresh):
    with _client() as c:
        _deploy(c)
        _fire(c)
        act = c.get("/api/kestrel/incidents/INC-501").json()["actions"][0]
        out = c.post(f"/api/kestrel/actions/{act['id']}", json={"approve": True, "who": "ade"}).json()
        assert out["verification"]["verified"] is None
        decided = fresh.read("MATCH (a:Action {id: $id}) RETURN a.decided_at AS t", {"id": act["id"]})[0]["t"]
        assert decided > T0 + 420          # stamped after the alerts it answers, not on the frozen demo clock
        res = _fire(c, resolve_at=decided + 600)
        assert res["reverified"][0]["verified"] is True
        assert c.get("/api/kestrel/incidents/INC-501").json()["actions"][0]["verified"] is True


def test_low_tier_alert_alone_does_not_open_an_incident(fresh):
    with _client() as c:
        out = c.post("/api/kestrel/alerts/alertmanager",
                     json={"alerts": [_am("fp-n", "notifications", T0, "queue backlog")]}).json()
    assert out["created"] and out["opened"] == []


def test_one_investigation_per_incident_at_a_time(fresh):
    with ingest.exclusive("kestrel", "INC-301"):
        with _client() as c:
            assert c.post("/api/kestrel/incidents/INC-301/investigate").status_code == 409


def test_webhook_bodies_are_capped(fresh):
    with _client() as c:
        big = json.dumps({"alerts": [_am(f"fp{i}", "checkout", T0, "x" * 2000) for i in range(600)]})
        assert c.post("/api/kestrel/alerts/alertmanager", content=big,
                      headers={"content-type": "application/json"}).status_code == 413


# --- benchmark in the app ---------------------------------------------------------------------------------------

def test_bench_route_checks_isolation_and_consistency(fresh):
    with _client() as c:
        r = c.post("/api/kestrel/bench?runs=10").json()
        assert r["runs"] == 10 and r["cross_run_reads"] == 0 and r["verdict_mismatches"] == 0
        assert not [t for t in Graph("kestrel").read("RETURN 1 AS x") if False]
        bench._one_at_a_time.acquire()
        try:
            assert c.post("/api/kestrel/bench?runs=10").status_code == 409
        finally:
            bench._one_at_a_time.release()
    from faultline.db import list_tenants
    assert not [t for t in list_tenants() if t.startswith("bench_")]


# --- bring your own incident -----------------------------------------------------------------------------------

def test_upload_the_example_incident(fresh):
    data = (ROOT / "web" / "examples" / "incident.json").read_bytes()
    with _client() as c:
        r = c.post("/api/upload?filename=incident.json", content=data, headers={"content-type": "application/json"})
        out = r.json()
        assert r.status_code == 200 and out["leader"] == "C-102" and out["tenant"].startswith("up_")
        assert any(t["id"] == out["tenant"] and t["kind"] == "uploaded" for t in c.get("/api/tenants").json())
        case = c.get(f"/api/{out['tenant']}/incidents/INC-1/case").json()
        assert case["findings"]["change_id"] == "C-102"
    Graph(out["tenant"]).drop()


@pytest.mark.parametrize("bad, msg", [
    (b"{not json", "not valid JSON"),
    (json.dumps({"services": ["a"], "calls": [["a", "zz"]], "incident": {"symptom": "a"}}).encode(), "unknown services"),
    (json.dumps({"services": ["a b"], "incident": {"symptom": "a b"}}).encode(), "bad service name"),
    (json.dumps({"services": ["a"], "incident": {"symptom": "q"}}).encode(), "incident.symptom"),
])
def test_upload_rejects_bad_json(fresh, bad, msg):
    with _client() as c:
        r = c.post("/api/upload?filename=x.json", content=bad, headers={"content-type": "application/json"})
    assert r.status_code == 400 and msg in r.json()["detail"]


def test_upload_zip_reads_only_the_expected_files():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../etc/inject_time.txt", "1")      # the right names, but outside the case: all ignored
        z.writestr("/abs/metrics.csv", "time\n1\n")
        z.writestr("a/../../traces.csv", "spanID\n1\n")
        z.writestr("notes.txt", "hello")
    with pytest.raises(ValueError, match="expected inject_time.txt"):
        upload.from_zip(buf.getvalue(), "evil.zip")


def test_uploads_are_evicted_beyond_the_cap(fresh, monkeypatch):
    monkeypatch.setattr(upload, "MAX_TENANTS", 2)
    spec = json.loads((ROOT / "web" / "examples" / "incident.json").read_text())
    made = [upload.handle(json.dumps(spec).encode(), "x.json", "")["tenant"] for _ in range(3)]
    left = {o["id"] for o in upload.uploaded()}
    assert made[-1] in left and len(left & set(made)) <= 2
    for t in made:
        Graph(t).drop()


# --- public mode ---------------------------------------------------------------------------------------------------

def test_public_mode_guards_webhooks_and_rate_limits(fresh, monkeypatch):
    monkeypatch.setattr(api, "PUBLIC", True)
    monkeypatch.setattr(api, "INGEST_TOKEN", "s3cret")
    monkeypatch.setattr(api, "LIMITS", [("/investigate", 2, 60)])
    api._hits.clear()
    with _client() as c:
        body = {"alerts": [_am("fp-p", "checkout", T0)]}
        assert c.post("/api/kestrel/alerts/alertmanager", json=body).status_code == 403
        assert c.post("/api/kestrel/alerts/alertmanager", json=body, headers={"X-Faultline-Token": "s3cret"}).status_code == 200
        codes = [c.post("/api/kestrel/incidents/INC-303/investigate").status_code for _ in range(3)]
        assert codes == [200, 200, 429]
    api._hits.clear()


def test_public_mcp_is_read_only():
    code = ("import asyncio; from faultline import mcp_server as m; "
            "print(sorted(t.name for t in asyncio.run(m.server.list_tools())))")
    env = {"FAULTLINE_PUBLIC": "1", "PATH": "/usr/bin:/bin", "PYTHONPATH": str(ROOT),
           "FALKOR_HOST": "x"}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=ROOT)
    names = out.stdout
    assert "rank_suspects" in names and "propose_action" not in names


# --- real owners -----------------------------------------------------------------------------------------------------

def test_boutique_owners_come_from_the_project():
    seed.build("boutique", force=True)
    g = Graph("boutique")
    snap = json.loads(seed.BOUTIQUE_OWNERS.read_text())
    teams = {r["t"] for r in g.read("MATCH (t:Team) RETURN t.name AS t")}
    assert teams == {"devrel-flagship-app-maintainers"} and "devrel-flagship-app-maintainers" in snap["codeowners"][0]["owners"][0]
    assert not g.read("MATCH (p:Person {on_call: true}) RETURN p")            # no invented on-call rota
    top = tools.experts(g, "shippingservice")[0]
    hist = {p["login"]: p["commits"] for p in snap["history"]["src/shippingservice"]["people"]}
    assert top["commits"] >= hist[top["handle"]] and top["commits"] == max(e["commits"] for e in tools.experts(g, "shippingservice"))
    assert not any(e["handle"].endswith("[bot]") for e in tools.experts(g, "frontend"))


# --- theme -----------------------------------------------------------------------------------------------------------

def test_graph_colours_come_from_the_theme_tokens():
    js = (ROOT / "web" / "js" / "graph.js").read_text()
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b|rgba?\(", js)
    css = (ROOT / "web" / "styles.css").read_text()
    dark = set(re.findall(r"(--[a-z0-9-]+):", css.split("/* Light")[0]))
    light_block = css.split(':root[data-theme="light"] {')[1].split("}")[0]
    light = set(re.findall(r"(--[a-z0-9-]+):", light_block))
    colours = {t for t in dark if t not in ("--mono", "--sans", "--serif")}
    assert colours <= light, sorted(colours - light)
