"""HTTP API and the static UI."""
from __future__ import annotations

import asyncio
import hmac
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, bench, ingest, postmortem, reasoning, seed, tools, upload, world
from .db import Graph, list_tenants

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed.build_all(force=False)
    yield


app = FastAPI(title="Faultline", lifespan=lifespan)

# --- running on a public server ---------------------------------------------------------------------------
# FAULTLINE_PUBLIC=1 turns on what a shared, internet-facing demo needs: per-client rate limits on the routes that
# do real work, and the webhook routes (alerts, changes) only with FAULTLINE_INGEST_TOKEN, so strangers can look,
# investigate, approve and upload, but can't feed the graph fake monitoring data.
PUBLIC = os.environ.get("FAULTLINE_PUBLIC") == "1"
INGEST_TOKEN = os.environ.get("FAULTLINE_INGEST_TOKEN", "")
# route suffix -> (requests, per seconds), per client address
LIMITS = [("/investigate", 20, 60), ("/upload", 4, 600), ("/bench", 2, 60), ("/reset", 2, 300), ("/ask", 30, 60),
          ("/alerts/alertmanager", 60, 60), ("/changes", 60, 60)]
_hits: dict[tuple[str, str], deque] = {}


def _client(request) -> str:
    # behind the bundled Caddy proxy the client is in X-Forwarded-For; otherwise the socket peer
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "?")


@app.middleware("http")
async def public_guard(request, call_next):
    if PUBLIC and request.method == "POST":
        path = request.url.path
        if path.endswith(("/alerts/alertmanager", "/changes")) and (
                not INGEST_TOKEN or not hmac.compare_digest(request.headers.get("x-faultline-token", ""), INGEST_TOKEN)):
            return JSONResponse({"detail": "webhooks need X-Faultline-Token on this server"}, status_code=403)
        for suffix, n, per in LIMITS:
            if path.endswith(suffix):
                q = _hits.setdefault((_client(request), suffix), deque())
                now_ = time.monotonic()
                while q and now_ - q[0] > per:
                    q.popleft()
                if len(q) >= n:
                    return JSONResponse({"detail": f"slow down: at most {n} of these per {per} s"}, status_code=429,
                                        headers={"Retry-After": str(int(per - (now_ - q[0])) + 1)})
                q.append(now_)
                break
    return await call_next(request)


MAX_INGEST_BYTES = 1_000_000


@app.middleware("http")
async def cap_ingest(request, call_next):
    # Webhook bodies come from outside; refuse anything large before reading it.
    if request.method == "POST" and request.url.path.endswith(("/alerts/alertmanager", "/changes")):
        n = request.headers.get("content-length")
        if n is None or not n.isdigit() or int(n) > MAX_INGEST_BYTES:
            return JSONResponse({"detail": "body too large or without a length"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def revalidate_static(request, call_next):
    # The UI is plain ES modules; make the browser revalidate them so an update never runs against stale code.
    res = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        res.headers["Cache-Control"] = "no-cache"
    return res


def _g(tenant: str) -> Graph:
    if tenant not in seed.TENANTS and not (tenant.startswith(upload.PREFIX) and upload.is_upload(tenant)):
        raise HTTPException(404, f"unknown tenant {tenant}")
    return Graph(tenant)


def _guard(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except KeyError as e:
        raise HTTPException(404, str(e).strip("'"))
    except ValueError as e:
        raise HTTPException(400, str(e))


class Ask(BaseModel):
    question: str = Field(min_length=2, max_length=500)


class Decision(BaseModel):
    approve: bool
    who: str = Field(default="you", max_length=60)


class ChangeIn(BaseModel):
    kind: str = Field(pattern="^(deploy|config|flag)$")
    service: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=3, max_length=300)
    at: int | str
    author: str | None = Field(default=None, max_length=60)
    version: str | None = Field(default=None, max_length=40)
    key: str | None = Field(default=None, max_length=120)
    old: str | None = Field(default=None, max_length=120)
    new: str | None = Field(default=None, max_length=120)
    risk: str = "medium"
    id: str | None = Field(default=None, max_length=12)


class AMAlert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: str | int
    endsAt: str | int | None = None
    fingerprint: str | None = Field(default=None, max_length=64)


class AMPayload(BaseModel):
    """Alertmanager's webhook body (Grafana alerting can send the same)."""
    status: str = "firing"
    alerts: list[AMAlert] = Field(max_length=500)


class Resolution(BaseModel):
    change_id: str | None = None
    cause_service: str | None = None
    lesson: str = Field(min_length=3, max_length=1000)


# Shown in the header. From the test suite and the two evals in this repo (README, "Evaluation" and
# "Public data"); update them when those numbers change.
PROOF = {"tests": 102, "planted": "95.8–97.5%", "real": "285/360"}


@app.get("/api/health")
def health():
    return {"ok": True, "graphs": list_tenants(), "claude": agent.claude_available(), "model": agent.MODEL,
            "now": tools.now(), "proof": PROOF, "public": PUBLIC}


# What each company's data is, said plainly in the UI next to its name.
DATA_KIND = {"kestrel": "synthetic company", "boutique": "real telemetry (RCAEval) · real owners", "harbor": "synthetic company",
             "sockshop": "real telemetry and logs (RCAEval RE2 + RE3)", "trainticket": "real telemetry, traces and logs (RCAEval RE2 + RE3)"}


@app.get("/api/tenants")
def tenants():
    built = [{"id": t, "title": title, "kind": "demo", "data": DATA_KIND.get(t, ""), **tools.stats(Graph(t))}
             for t, title in seed.TENANTS.items()]
    mine = [{"id": o["id"], "title": o["title"], "kind": "uploaded", "data": f"your upload · {o['format']}",
             **tools.stats(Graph(o["id"]))} for o in upload.uploaded()]
    return built + mine


@app.post("/api/upload")
async def upload_case(request: Request, filename: str = "upload.json"):
    """Bring your own incident: Faultline's incident JSON, or an RCAEval-layout case zip. Becomes a new tenant."""
    n = request.headers.get("content-length")
    if n is None or not n.isdigit() or int(n) > upload.MAX_ZIP_BYTES:
        raise HTTPException(413, "upload too large (40 MB at most) or without a length")
    data = await request.body()
    try:
        return await asyncio.to_thread(upload.handle, data, filename[:120], request.headers.get("content-type", ""))
    except upload.Busy as e:
        raise HTTPException(409, str(e))
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e).strip("'"))


@app.get("/api/{tenant}/graph")
def graph(tenant: str):
    return tools.graph_view(_g(tenant))


@app.get("/api/{tenant}/incidents")
def incidents(tenant: str, status: str | None = None):
    return tools.list_incidents(_g(tenant), status)


@app.get("/api/{tenant}/incidents/{incident_id}")
def incident(tenant: str, incident_id: str):
    g = _g(tenant)
    inc = _guard(tools.get_incident, g, incident_id)
    return {**inc, "actions": tools.list_actions(g, incident_id),
            "investigations": tools.past_investigations(g, incident_id)}


@app.get("/api/{tenant}/incidents/{incident_id}/case")
def case(tenant: str, incident_id: str):
    """Read-only case file for an incident, ready the moment it is opened."""
    _guard(tools.get_incident, _g(tenant), incident_id)
    return agent.preview(tenant, incident_id)


@app.get("/api/{tenant}/incidents/{incident_id}/postmortem.md")
def postmortem_md(tenant: str, incident_id: str):
    """The incident as a Markdown postmortem, built only from the graph. A draft until it is resolved."""
    out = _guard(postmortem.postmortem, _g(tenant), incident_id)
    name = f"{incident_id}-postmortem{'' if out['final'] else '-draft'}.md"
    return PlainTextResponse(out["markdown"], media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/{tenant}/board")
def board(tenant: str):
    """The risk board: every unresolved incident with its leading suspect and how far it reaches."""
    g = _g(tenant)
    out = []
    for inc in tools.list_incidents(g, None):
        row = dict(inc)
        if inc["status"] != "resolved":
            r = agent.judged(g, inc["id"])
            top = r["suspects"][0] if r["suspects"] else None
            row["leader"] = tools._label(top) if top else None
            row["leader_service"] = top["service"] if top else None
            row["confidence"] = r["confidence"]
            row["explains"] = top["footprint"]["explained"] if top else []
        out.append(row)
    return out


@app.get("/api/{tenant}/company")
def company(tenant: str):
    g = _g(tenant)
    return {**tools.company(g), "traces": g.trace.queries}


@app.get("/api/{tenant}/services/{service}")
def service(tenant: str, service: str):
    """One service's card: owners, experts, callers, changes, history, runbooks, and the queries behind it."""
    g = _g(tenant)
    return {**_guard(tools.service_card, g, service), "traces": g.trace.queries}


@app.get("/api/{tenant}/incidents/{incident_id}/argument")
def argument(tenant: str, incident_id: str):
    """The argument stored by the last investigation, read back from the graph, with the queries that read it."""
    g = _g(tenant)
    _guard(tools.get_incident, g, incident_id)
    return {"hypotheses": reasoning.argument(g, incident_id), "traces": g.trace.queries}


@app.get("/api/{tenant}/alerts/{alert_id}/why")
def why(tenant: str, alert_id: str):
    g = _g(tenant)
    return {**reasoning.why(g, alert_id), "traces": g.trace.queries}


@app.get("/api/{tenant}/incidents/{incident_id}/timeline")
def timeline(tenant: str, incident_id: str):
    return _guard(tools.timeline, _g(tenant), incident_id)


@app.post("/api/{tenant}/incidents/{incident_id}/investigate")
def investigate(tenant: str, incident_id: str):
    _guard(tools.get_incident, _g(tenant), incident_id)
    try:
        with ingest.exclusive(tenant, incident_id):
            return agent.investigate(tenant, incident_id)
    except ingest.Busy as e:
        raise HTTPException(409, str(e))


@app.post("/api/{tenant}/changes")
def change(tenant: str, body: ChangeIn):
    """A deploy, config or flag change, posted by CI/CD as it lands."""
    return _guard(ingest.record_change, _g(tenant), body.kind, body.service, body.summary, body.at, body.author,
                  body.version, body.key, body.old, body.new, body.risk, body.id)


@app.post("/api/{tenant}/alerts/alertmanager")
def alertmanager(tenant: str, body: AMPayload):
    """Alerts pushed by monitoring. New paging alerts open an incident (see ingest.py for the rule) and the
    agents investigate it at once; resolved alerts can verify a fix that was waiting for recovery."""
    g = _g(tenant)
    out = _guard(ingest.ingest_alertmanager, g, body.model_dump())
    out["investigations"] = []
    for iid in out["opened"]:
        try:
            with ingest.exclusive(tenant, iid):
                run = agent.investigate(tenant, iid)
        except ingest.Busy:
            continue
        f = run["findings"]
        out["investigations"].append({
            "incident": iid, "run_id": run.get("run_id"), "root_cause": f.get("change_id") or f.get("root_cause_service"),
            "confidence": run["ranking"]["confidence"], "summary": f.get("summary")})
    return out


@app.post("/api/{tenant}/incidents/{incident_id}/resolve")
def resolve(tenant: str, incident_id: str, body: Resolution):
    return _guard(tools.resolve_incident, _g(tenant), incident_id, body.change_id, body.lesson, body.cause_service)


@app.post("/api/{tenant}/actions/{action_id}")
def decide(tenant: str, action_id: str, body: Decision):
    """A person approves or rejects. On approval the fix is applied to the simulated system (world.py, which
    stands in for production), then verified from the alerts in the graph alone."""
    g = _g(tenant)
    out = _guard(tools.decide_action, g, action_id, body.approve, body.who)
    if out["status"] == "executed":
        a = g.read("""MATCH (a:Action {id: $id})-[:TARGETS]->(s:Service)
                      OPTIONAL MATCH (a)-[:FOR]->(i:Incident {source: 'alertmanager'})
                      RETURN a.kind AS kind, s.name AS target, a.change_id AS change_id, a.decided_at AS at,
                             count(i) AS live""", {"id": action_id})[0]
        if a["live"]:
            # A real incident: only monitoring can say whether it worked. The next resolved alert verifies it.
            g.write("MATCH (a:Action {id: $id}) SET a.verification = 'waiting for monitoring to report recovery'",
                    {"id": action_id})
            out["verification"] = {"action": action_id, "verified": None, "summary": "waiting for monitoring"}
        else:
            world.apply_fix(g, tenant, a["kind"], a["target"], a["change_id"], a["at"])
            out["verification"] = tools.verify_fix(g, action_id)
    return out


@app.post("/api/{tenant}/ask")
def ask(tenant: str, body: Ask):
    _g(tenant)
    return agent.ask(tenant, body.question)


@app.get("/api/{tenant}/memory")
def memory(tenant: str):
    """What the agent has learned: resolved incidents, their causes, fixes and lessons."""
    g = _g(tenant)
    return g.read("""
        MATCH (i:Incident {status: 'resolved'})-[:SYMPTOM_ON]->(sym:Service)
        OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(c:Change)-[:CHANGED]->(cs:Service)
        OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
        OPTIONAL MATCH (i)-[:TAUGHT]->(l:Lesson)
        RETURN i.id AS id, i.title AS title, i.started AS started, sym.name AS symptom,
               cs.name AS cause_service, c.kind AS cause_kind, c.summary AS cause,
               collect(DISTINCT a.summary) AS fixes, collect(DISTINCT l.text) AS lessons,
               collect(DISTINCT a.decided_by)[0] AS approved_by
        ORDER BY started DESC""")


@app.get("/api/{tenant}/memory/fixes")
def memory_fixes(tenant: str):
    """Procedural memory: each kind of fix on each service, how often it worked (alerts cleared) or failed."""
    return tools.fix_track_record(_g(tenant))


@app.post("/api/{tenant}/bench")
async def bench_run(tenant: str, runs: int = 25):
    """Many four-agent investigations at once on a copy of this graph: speed, isolation, consistency."""
    _g(tenant)
    try:
        return await asyncio.to_thread(bench.run_once, runs, 8, tenant)
    except bench.Busy as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/{tenant}/memory/search")
def memory_search(tenant: str, q: str):
    """Full-text search over lessons, change summaries, alert signals and incident titles."""
    g = _g(tenant)
    return {"hits": tools.search_memory(g, q[:200]), "traces": g.trace.queries}


@app.get("/api/{tenant}/memory/stats")
def memory_stats(tenant: str):
    """The numbers above the Memory tab: what is remembered, and how often the agents have used it."""
    g = _g(tenant)
    r = g.read("""
        OPTIONAL MATCH (i:Incident {status: 'resolved'}) WITH count(i) AS solved
        OPTIONAL MATCH (l:Lesson) WITH solved, count(l) AS lessons
        OPTIONAL MATCH (v:Investigation) WITH solved, lessons, count(v) AS runs
        OPTIONAL MATCH (e:Episode) WITH solved, lessons, runs, count(e) AS handoffs
        OPTIONAL MATCH (x:Incident) WHERE x.signature IS NOT NULL
        RETURN solved, lessons, runs, handoffs, count(x) AS shapes""")[0]
    return r


@app.post("/api/reset")
def reset():
    return seed.build_all(force=True)


app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")
