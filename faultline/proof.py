"""Prove a fix before anyone approves it, on a copy of the graph.

The argument says *why* Hypocenter blames something; this says *what fixing it will do*, before it is done:

1. Copy the tenant's graph (`GRAPH.COPY`), so nothing real is touched.
2. Write the incident's causal chain into the copy: `(Alert)-[:CAUSED_BY]->...->(Change | Service | Node)`.
3. The alerts the fix should clear are exactly those whose `CAUSED_BY` path ends at what the fix removes (the
   change it reverts, or the service or node it pages about). One Cypher query.
4. Clear them in the copy and ask the judge again. Whatever is still firing is what the fix will *not* clear,
   and the judge on the copy says what explains it (another fault, another incident) or that nothing does.
5. Drop the copy. The prediction is kept on the Action, and when the fix is approved `verify_fix` checks what
   monitoring reports against it: verified means the predicted alerts cleared and nothing outside the
   prediction was left firing.
"""
from __future__ import annotations

import json
import uuid

from . import reasoning, tools
from .db import Graph, client, graph_name


def _root_match(kind: str, target: str, change_id: str | None) -> tuple[str, dict]:
    """The node the fix removes, as a Cypher pattern."""
    if change_id and kind in ("rollback", "revert_config", "disable_flag"):
        return "(root:Change {id: $root})", {"root": change_id}
    return "(root) WHERE (root:Service OR root:Node) AND root.name = $root", {"root": target}


def preview_fix(g: Graph, incidents: list[str], kind: str, target: str, change_id: str | None = None) -> dict:
    """What the fix would clear and what it would leave, worked out on a throwaway copy of the graph."""
    from .agent import judged
    sandbox = Graph(f"proof_{uuid.uuid4().hex[:10]}", g.trace)
    client().connection.execute_command("GRAPH.COPY", graph_name(g.tenant), graph_name(sandbox.tenant))
    try:
        rankings = {i: judged(sandbox, i) for i in incidents}
        for i, r in rankings.items():
            reasoning.write(sandbox, i, reasoning.build(sandbox, r))
        pattern, params = _root_match(kind, target, change_id)
        clears = sandbox.read(f"""
            MATCH p = (a:Alert)-[:CAUSED_BY*1..12]->{pattern}
            MATCH (a)-[:FIRED_ON]->(s:Service)
            RETURN DISTINCT a.id AS alert, s.name AS service, a.signal AS signal ORDER BY a.id""", params)
        ids = [c["alert"] for c in clears]
        if ids:
            sandbox.write("MATCH (a:Alert) WHERE a.id IN $ids DETACH DELETE a", {"ids": ids})
        per = []
        for i, before in rankings.items():
            inc = tools.get_incident(sandbox, i)
            sym = inc["symptom"]
            area = [sym] + [d["service"] for d in tools.dependencies(sandbox, sym)]
            firing = [a for a in tools.alerts_near(sandbox, area, inc["started"]) if a["cleared_at"] is None]
            cone = tools._alert_cone(sandbox, sym, set(area), firing)
            left = [a for a in firing if a["service"] in cone]
            after = judged(sandbox, i) if left else None
            lead = after["suspects"][0] if after and after["suspects"] else None
            per.append({
                "incident": i,
                "clears": [c for c in clears if c["alert"] in {a["id"] for a in before.get("alerts") or []}],
                "leaves": [{"alert": a["id"], "service": a["service"], "signal": a["signal"]} for a in left],
                "then_explained_by": tools._label(lead) if lead else None,
                "resolves": not left,
                # faults below the symptom that belong to other incidents: the fix isn't meant to touch them
                "elsewhere": [{"label": tools._label(o), "incidents": [x["id"] for x in o["incidents"]]}
                              for o in before.get("other_faults") or []],
            })
        verdict = ("resolves" if ids and all(p["resolves"] for p in per) else
                   "partial" if ids else "clears nothing")
        return {"kind": kind, "target": target, "change_id": change_id, "verdict": verdict,
                "predicted_clear": ids, "incidents": per,
                "summary": _summary(verdict, ids, per)}
    finally:
        sandbox.drop()


def _summary(verdict: str, ids: list[str], per: list[dict]) -> str:
    if verdict == "clears nothing":
        return "On a copy of the graph this fix clears no alert: nothing alerting traces back to what it removes."
    parts = []
    for p in per:
        other = "; other faults below it stay with their own incidents: " + ", ".join(
            o["label"] + (f" ({', '.join(o['incidents'])})" if o["incidents"] else "") for o in p["elsewhere"]) if p["elsewhere"] else ""
        if p["resolves"]:
            parts.append(f"{p['incident']}: clears all {len(p['clears'])} of its alerts{other}")
        else:
            svcs = sorted({x["service"] for x in p["leaves"]})
            parts.append(f"{p['incident']}: clears {len(p['clears'])}, leaves {len(p['leaves'])} on {', '.join(svcs)}"
                         + (f" (then explained by {p['then_explained_by']})" if p["then_explained_by"] else ""))
    return "On a copy of the graph: " + "; ".join(parts) + "."


def attach(g: Graph, action_id: str) -> dict:
    """Prove a proposed action and keep the prediction on it."""
    rows = g.read("""
        MATCH (a:Action {id: $id})-[:TARGETS]->(t)
        OPTIONAL MATCH (a)-[:FOR]->(i:Incident)
        RETURN a.kind AS kind, t.name AS target, a.change_id AS change_id, collect(i.id) AS incidents""", {"id": action_id})
    if not rows:
        raise KeyError(f"no action {action_id}")
    r = rows[0]
    out = preview_fix(g, sorted(r["incidents"]), r["kind"], r["target"], r["change_id"])
    g.write("MATCH (a:Action {id: $id}) SET a.predicted_clear = $ids, a.preview = $p, a.preview_verdict = $v",
            {"id": action_id, "ids": out["predicted_clear"], "p": json.dumps(out), "v": out["verdict"]})
    return out
