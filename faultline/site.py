"""What the site's pages read beyond the incident room: the engine, the on-call view, the whole graph to explore,
and "break it": one change made on a GRAPH.COPY of the tenant, and how the verdict moves.

Nothing here writes to a tenant's graph. Break-it experiments run on a throwaway copy that is dropped afterwards,
the same way a fix is proved (proof.py).
"""
from __future__ import annotations

import time
import uuid

from . import tools
from .db import Graph, client, graph_name, list_tenants

# --- the engine ----------------------------------------------------------------------------------------------


def engine() -> dict:
    """FalkorDB's module version, a ping, and the size of every Faultline graph."""
    c = client().connection
    t = time.perf_counter()
    c.ping()
    ping_ms = round((time.perf_counter() - t) * 1000, 2)
    version = None
    for m in c.execute_command("MODULE", "LIST") or []:
        d = dict(zip(m[::2], m[1::2])) if isinstance(m, list) else m
        d = {(k.decode() if isinstance(k, bytes) else k): v for k, v in d.items()}
        name = d.get("name")
        if (name.decode() if isinstance(name, bytes) else name) == "graph":
            v = int(d.get("ver"))
            version = f"{v // 10000}.{v // 100 % 100}.{v % 100}"
    graphs = []
    for t_ in list_tenants():
        g = Graph(t_)
        n = g.read("MATCH (n) RETURN count(n) AS n")[0]["n"]
        e = g.read("MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
        graphs.append({"tenant": t_, "graph": graph_name(t_), "nodes": n, "edges": e})
    return {"engine": "FalkorDB", "version": version, "ping_ms": ping_ms, "graphs": graphs,
            "nodes": sum(x["nodes"] for x in graphs), "edges": sum(x["edges"] for x in graphs)}


# --- on call -------------------------------------------------------------------------------------------------

WEEK = 7 * 86400


def oncall(g: Graph) -> dict:
    """Who is on call for each team now and next, the rota behind it, and for each open incident who to page:
    the on-call person of the team that owns the service the judge blames (not the team that noticed)."""
    now = tools.now()
    teams = g.read("MATCH (t:Team) RETURN t.id AS id, t.name AS name ORDER BY t.name")
    shifts = g.read("""
        MATCH (p:Person)-[:ON_SHIFT]->(s:OnCallShift)-[:COVERS]->(t:Team)
        RETURN t.name AS team, p.handle AS handle, p.name AS name, s.from AS from, s.to AS to ORDER BY s.from""")
    members = g.read("""MATCH (p:Person)-[:MEMBER_OF]->(t:Team)
                        RETURN t.name AS team, collect(p.name) AS people""")
    people = {m["team"]: m["people"] for m in members}
    out_teams = []
    for t in teams:
        mine = [s for s in shifts if s["team"] == t["name"]]
        cur = next((s for s in mine if s["from"] <= now < s["to"]), None)
        nxt = None
        if cur:
            order = [s["handle"] for s in sorted(mine, key=lambda s: -s["from"])]
            cycle = list(dict.fromkeys(order))           # newest first: the rotation in reverse
            nxt = cycle[-1] if len(cycle) > 1 else cycle[0]
            nxt = next((s for s in mine if s["handle"] == nxt), None)
        out_teams.append({"team": t["name"], "people": people.get(t["name"], []),
                          "now": {k: cur[k] for k in ("handle", "name", "from", "to")} if cur else None,
                          "next": {"handle": nxt["handle"], "name": nxt["name"], "from": cur["to"]} if cur and nxt else None,
                          "rota": [{k: s[k] for k in ("handle", "name", "from", "to")} for s in mine[-6:]]})
    pages = []
    for inc in tools.list_incidents(g, "open"):
        rank = tools.rank_suspects(g, inc["id"], limit=1)
        top = rank["suspects"][0] if rank["suspects"] else None
        svc = top["service"] if top else inc["symptom"]
        try:
            team = tools.who_owns(g, svc)["team"]
        except KeyError:
            team = None
        pages.append({"incident": inc["id"], "title": inc["title"], "severity": inc["severity"],
                      "started": inc["started"], "symptom": inc["symptom"], "blamed": tools._label(top),
                      "service": svc, "team": team, "confidence": rank["confidence"],
                      "page": tools.on_call_at(g, team, now) if team else None,
                      "was_on_call": tools.on_call_at(g, team, inc["started"]) if team else None})
    week = g.read("""
        MATCH (a:Alert) WHERE a.at >= $since RETURN count(a) AS alerts,
              sum(CASE WHEN a.severity = 'page' THEN 1 ELSE 0 END) AS pages""", {"since": now - WEEK})[0]
    solved = g.read("""
        MATCH (i:Incident) WHERE i.resolved IS NOT NULL AND i.started >= $since
        RETURN count(i) AS n, avg(i.resolved - i.started) AS mttr""", {"since": now - 30 * 86400})[0]
    return {"now": now, "teams": out_teams, "pages": pages,
            "stats": {"open": len(pages), "pages_7d": week["pages"], "alerts_7d": week["alerts"],
                      "resolved_30d": solved["n"], "mttr_min": round(solved["mttr"] / 60) if solved["mttr"] else None}}


# --- explore -------------------------------------------------------------------------------------------------

EXPLORE_LIMIT = 1500


def _read_all(g: Graph, cypher: str, params: dict) -> list[dict]:
    """A read-only query without the agent tools' 200-row cap: the canvas needs the whole graph, up to its own
    EXPLORE_LIMIT."""
    res = g.g.ro_query(cypher, params)
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in res.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, row)) for row in res.result_set]


def explore(g: Graph, labels: list[str] | None = None, limit: int = EXPLORE_LIMIT) -> dict:
    """The tenant's graph as nodes and edges for the canvas: every label unless `labels` narrows it."""
    limit = max(1, min(int(limit), EXPLORE_LIMIT))
    where = "WHERE labels(n)[0] IN $labels" if labels else ""
    nodes = _read_all(g, f"""
        MATCH (n) {where}
        RETURN id(n) AS key, labels(n)[0] AS label,
               coalesce(n.name, n.id, n.handle, n.title, n.sha, n.text) AS name LIMIT $limit""",
                   {"labels": labels or [], "limit": limit})
    keys = [n["key"] for n in nodes]
    edges = _read_all(g, """
        MATCH (a)-[r]->(b) WHERE id(a) IN $keys AND id(b) IN $keys
        RETURN id(a) AS source, id(b) AS target, type(r) AS type""", {"keys": keys})
    for n in nodes:
        n["name"] = str(n["name"])[:60] if n["name"] is not None else n["label"]
    return {"nodes": nodes, "edges": edges, "truncated": len(nodes) == limit,
            "cypher": f"MATCH (n) {where} RETURN n LIMIT {limit}; MATCH (a)-[r]->(b) WHERE id(a) IN $keys AND id(b) IN $keys RETURN r"}


def node(g: Graph, key: int) -> dict:
    """One node's properties and its neighbours."""
    rows = g.read("MATCH (n) WHERE id(n) = $k RETURN labels(n)[0] AS label, properties(n) AS props", {"k": int(key)})
    if not rows:
        raise KeyError(f"no node {key}")
    nb = g.read("""
        MATCH (n)-[r]-(m) WHERE id(n) = $k
        RETURN type(r) AS type, startNode(r) = n AS out, id(m) AS key, labels(m)[0] AS label,
               coalesce(m.name, m.id, m.handle, m.title, m.sha) AS name LIMIT 60""", {"k": int(key)})
    props = {k: (v if not isinstance(v, (str, list)) or len(str(v)) < 300 else str(v)[:300] + "…")
             for k, v in rows[0]["props"].items() if k not in ("embedding", "vec")}
    return {"key": int(key), "label": rows[0]["label"], "props": props, "neighbours": nb}


# --- break it ------------------------------------------------------------------------------------------------
# Each experiment: one change to a copy of Kestrel, and the incident whose verdict it should move. The text says
# what is changed; what actually moves is computed every time and shown next to it.

EXPERIMENTS = [
    {"id": "cut-path", "incident": "INC-302", "title": "Cut auth out of mobile-bff's call path",
     "what": "Deletes every CALLS edge into auth, so nothing on mobile-bff's dependency path reaches it.",
     "expect": "D-5201 is no longer on the path from the symptom, so it can't be blamed, however recent it is.",
     "algo": "CALLS*1..5",
     "cypher": ["MATCH (:Service)-[r:CALLS]->(:Service {name: 'auth'}) DELETE r"]},
    {"id": "late-deploy", "incident": "INC-302", "title": "Ship the auth deploy after the alerts",
     "what": "Moves D-5201 to five minutes after mobile-bff started failing.",
     "expect": "A change that lands after the failure began cannot have started it. The lead goes to something earlier.",
     "algo": "alert order",
     "cypher": ["MATCH (c:Change {id: 'D-5201'}), (a:Alert {id: 'A-9102'}) SET c.at = a.at + 300"]},
    {"id": "blind-auth", "incident": "INC-302", "title": "Lose auth's alert",
     "what": "Deletes A-9101, auth's 401-rate alert, as if auth had no monitoring.",
     "expect": "Nothing alerting connects the symptom to the deploy any more: D-5201 falls behind and confidence drops.",
     "algo": "CAUSED_BY chain",
     "cypher": ["MATCH (a:Alert {id: 'A-9101'}) DETACH DELETE a"]},
    {"id": "users-first", "incident": "INC-302", "title": "Make users fail first",
     "what": "Adds a page on users four minutes before auth's, below the flag flip F-5202.",
     "expect": "The deepest service alerting first is now users, under F-5202, and the flag overtakes the deploy.",
     "algo": "alert order · depth",
     "cypher": ["""MATCH (s:Service {name: 'users'}), (a:Alert {id: 'A-9101'})
                   CREATE (:Alert {id: 'A-BRK-1', at: a.at - 240, severity: 'page', signal: 'p99 latency 3.1s',
                                   service: 'users'})-[:FIRED_ON]->(s)"""]},
    {"id": "not-the-cause", "incident": "INC-302", "title": "Tell it D-5201 is not the cause",
     "what": "Records a person's \"not the cause\" on D-5201, the way the button in the incident room does.",
     "expect": "Feedback takes a suspect out at once. The runner-up leads, with the confidence its own margin earns.",
     "algo": "Feedback node",
     "feedback": ("D-5201", "not_cause")},
    {"id": "low-risk", "incident": "INC-302", "title": "Call the auth deploy low-risk",
     "what": "Marks D-5201 risk 'low' instead of 'high' (it switched JWT validation).",
     "expect": "Risk is one factor among eight: the deploy should still lead on the alert chain, by less.",
     "algo": "judge factors",
     "cypher": ["MATCH (c:Change {id: 'D-5201'}) SET c.risk = 'low'"]},
    {"id": "pool-gone", "incident": "INC-301", "title": "Erase the payments-db pool change",
     "what": "Deletes C-5101, the pgbouncer change from 400 to 60 connections.",
     "expect": "The alerts still climb from payments-db, so it blames the datastore itself: a failure with no change to roll back.",
     "algo": "CALLS*1..5 · alert cone",
     "cypher": ["MATCH (c:Change {id: 'C-5101'}) DETACH DELETE c"]},
]


def mutation(exp: dict) -> list[str]:
    """The write an experiment makes on the copy, as shown to the person running it."""
    if exp.get("feedback"):
        target, verdict = exp["feedback"]
        return [f"// tools.give_feedback, as the incident room's button calls it",
                f"MATCH (i:Incident {{id: '{exp['incident']}'}})",
                f"CREATE (:Feedback {{target: '{target}', verdict: '{verdict}', by: 'break-it'}})-[:ABOUT]->(i)"]
    return ["\n".join(line.strip() for line in q.strip().splitlines()) for q in exp["cypher"]]


def _verdict(r: dict) -> dict:
    s = r["suspects"]
    return {"leader": tools._label(s[0]) if s else "nothing", "score": s[0]["score"] if s else 0.0,
            "service": s[0]["service"] if s else None, "confidence": r["confidence"],
            "confidence_p": r.get("confidence_p"),
            "top": [{"label": tools._label(x), "score": x["score"], "service": x["service"]} for x in s[:4]],
            "ruled_out": [c["id"] for c in r.get("ruled_out") or []][:6]}


def break_it(g: Graph, exp_id: str) -> dict:
    """Run one experiment on a copy of the graph and report the verdict before and after."""
    from .agent import judged
    exp = next((e for e in EXPERIMENTS if e["id"] == exp_id), None)
    if exp is None:
        raise KeyError(f"no experiment {exp_id}")
    inc = exp["incident"]
    before = _verdict(judged(g, inc))
    sandbox = Graph(f"break_{uuid.uuid4().hex[:10]}", g.trace)
    t = time.perf_counter()
    client().connection.execute_command("GRAPH.COPY", graph_name(g.tenant), graph_name(sandbox.tenant))
    copy_ms = round((time.perf_counter() - t) * 1000, 1)
    try:
        for q in exp.get("cypher", []):
            sandbox.write(q)
        if exp.get("feedback"):
            target, verdict = exp["feedback"]
            tools.give_feedback(sandbox, inc, target, verdict, "break-it")
        t = time.perf_counter()
        after = _verdict(judged(sandbox, inc))
        judge_ms = round((time.perf_counter() - t) * 1000, 1)
    finally:
        sandbox.drop()
    moved = (before["leader"] != after["leader"] or before["confidence"] != after["confidence"]
             or abs(before["score"] - after["score"]) >= 0.005)
    return {"experiment": {k: exp[k] for k in ("id", "incident", "title", "what", "expect", "algo")},
            "cypher": mutation(exp),
            "before": before, "after": after, "moved": moved,
            "leader_changed": before["leader"] != after["leader"],
            "copy_ms": copy_ms, "judge_ms": judge_ms}


# --- the planted fixes, proved live ---------------------------------------------------------------------------
# The right fix for each planted Kestrel fault, and one decoy (a rollback nothing alerting traces back to).
FIXES = [
    {"incidents": ["INC-301", "INC-304"], "kind": "revert_config", "target": "payments-db", "change": "C-5101"},
    {"incidents": ["INC-302"], "kind": "rollback", "target": "auth", "change": "D-5201"},
    {"incidents": ["INC-303"], "kind": "page_owner", "target": "catalog-es", "change": None},
    {"incidents": ["INC-305"], "kind": "page_owner", "target": "k8s-node-4", "change": None},
    {"incidents": ["INC-301"], "kind": "rollback", "target": "cart", "change": "D-5102", "decoy": True},
]


def prove_fixes(g: Graph) -> list[dict]:
    """Each fix tried on its own GRAPH.COPY: what it would clear, checked against the alerts the seed planted it as
    causing (world.TRUE_CAUSES). Nothing is approved and the real graph is not touched."""
    from . import proof, world
    truth = world.TRUE_CAUSES[g.tenant]
    out = []
    for f in FIXES:
        t = time.perf_counter()
        p = proof.preview_fix(g, f["incidents"], f["kind"], f["target"], f["change"])
        caused = sorted(a for a, cause in truth.items() if cause in (f["change"], f"infra:{f['target']}"))
        out.append({**f, "verdict": p["verdict"], "predicted_clear": p["predicted_clear"], "truly_caused": caused,
                    "summary": p["summary"], "ms": round((time.perf_counter() - t) * 1000, 1),
                    "holds": set(p["predicted_clear"]) <= set(caused) and (bool(p["predicted_clear"]) != bool(f.get("decoy")))})
    return out
