"""HTTP API and the static UI."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, seed, tools, world
from .db import Graph, list_tenants

WEB = Path(__file__).resolve().parent.parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    seed.build_all(force=False)
    yield


app = FastAPI(title="Faultline", lifespan=lifespan)


@app.middleware("http")
async def revalidate_static(request, call_next):
    # The UI is plain ES modules; make the browser revalidate them so an update never runs against stale code.
    res = await call_next(request)
    if request.url.path.startswith("/static/") or request.url.path == "/":
        res.headers["Cache-Control"] = "no-cache"
    return res


def _g(tenant: str) -> Graph:
    if tenant not in seed.TENANTS:
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


class Resolution(BaseModel):
    change_id: str | None = None
    cause_service: str | None = None
    lesson: str = Field(min_length=3, max_length=1000)


# Shown in the header. From the test suite and the two evals in this repo (README, "Evaluation" and
# "Public data"); update them when those numbers change.
PROOF = {"tests": 63, "planted": "95.8–97.5%", "real": "33/36"}


@app.get("/api/health")
def health():
    return {"ok": True, "graphs": list_tenants(), "claude": agent.claude_available(), "model": agent.MODEL,
            "now": tools.now(), "proof": PROOF}


@app.get("/api/tenants")
def tenants():
    return [{"id": t, "title": title, **tools.stats(Graph(t))} for t, title in seed.TENANTS.items()]


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


@app.get("/api/{tenant}/incidents/{incident_id}/timeline")
def timeline(tenant: str, incident_id: str):
    return _guard(tools.timeline, _g(tenant), incident_id)


@app.post("/api/{tenant}/incidents/{incident_id}/investigate")
def investigate(tenant: str, incident_id: str):
    _guard(tools.get_incident, _g(tenant), incident_id)
    return agent.investigate(tenant, incident_id)


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
                      RETURN a.kind AS kind, s.name AS target, a.change_id AS change_id, a.decided_at AS at""",
                   {"id": action_id})[0]
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
