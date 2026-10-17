"""Graph tools the agent (Claude, the offline investigator, or an MCP client) calls.

Every tool takes a tenant Graph, returns plain JSON, and records the Cypher it ran in g.trace.
Reads go through GRAPH.RO_QUERY. The only writes are actions, investigations and resolutions,
and those are explicit tools a human gates in the UI.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from typing import Any

from .db import Graph, assert_read_only
from .seed import NOW

WINDOW_MIN = 120          # look for changes up to 2h before the incident started
AFTER_GRACE_MIN = 5       # and a few minutes after (clock skew, slow rollouts)
MAX_HOPS = 5
RECENCY_HALF_MIN = 45

WEIGHTS = {"recency": 0.20, "proximity": 0.05, "alert_chain": 0.30, "risk": 0.15, "precedent": 0.15, "footprint": 0.15}
SIGNAL_WEIGHTS = {"alert_chain": 0.25, "earliness": 0.15, "precedent": 0.15, "proximity": 0.05, "footprint": 0.20,
                  "unchanged": 0.20}
RISK = {"high": 1.0, "medium": 0.5, "low": 0.2}


def now() -> int:
    """Demo clock. The synthetic world is frozen at the seed's NOW so every run tells the same story."""
    return NOW


# --- reading the graph -------------------------------------------------------------------------

def list_incidents(g: Graph, status: str | None = "open") -> list[dict]:
    rows = g.read("""
        MATCH (i:Incident)-[:SYMPTOM_ON]->(s:Service)
        WHERE $status IS NULL OR i.status = $status
        RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status,
               i.started AS started, s.name AS symptom
        ORDER BY i.started DESC""", {"status": status})
    return rows


def get_incident(g: Graph, incident_id: str) -> dict:
    rows = g.read("""
        MATCH (i:Incident {id: $id})-[:SYMPTOM_ON]->(s:Service)-[:OWNED_BY]->(t:Team)
        OPTIONAL MATCH (a:Alert)-[:FIRED_ON]->(s) WHERE a.at >= i.started - 3600 AND a.at <= i.started + 3600
        RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status,
               i.started AS started, s.name AS symptom, t.name AS team,
               collect({id: a.id, at: a.at, signal: a.signal, severity: a.severity}) AS alerts""",
                  {"id": incident_id})
    if not rows:
        raise KeyError(f"no incident {incident_id}")
    inc = rows[0]
    inc["alerts"] = [a for a in inc["alerts"] if a.get("id")]
    return inc


def dependencies(g: Graph, service: str, max_hops: int = MAX_HOPS) -> list[dict]:
    """Everything `service` calls, directly or through other services, with the fewest hops to reach it."""
    max_hops = max(1, min(int(max_hops), MAX_HOPS))
    return g.read(f"""
        MATCH p = (s:Service {{name: $svc}})-[:CALLS*1..{max_hops}]->(d:Service)
        WITH d, min(length(p)) AS hops
        RETURN d.name AS service, d.kind AS kind, hops
        ORDER BY hops, service""", {"svc": service})


def changes_near(g: Graph, services: list[str], start: int, window_min: int = WINDOW_MIN) -> list[dict]:
    return g.read("""
        MATCH (c:Change)-[:CHANGED]->(s:Service)
        WHERE s.name IN $svcs AND c.at >= $from AND c.at <= $to AND c.kind IN ['deploy', 'config', 'flag']
        OPTIONAL MATCH (p:Person)-[:MADE]->(c)
        RETURN c.id AS id, c.kind AS kind, c.at AS at, c.summary AS summary, c.risk AS risk,
               c.version AS version, c.key AS key, c.old AS old, c.new AS new,
               s.name AS service, p.handle AS author
        ORDER BY c.at DESC""",
                  {"svcs": services, "from": start - window_min * 60, "to": start + AFTER_GRACE_MIN * 60})


def alerts_near(g: Graph, services: list[str], start: int, window_min: int = WINDOW_MIN) -> list[dict]:
    return g.read("""
        MATCH (a:Alert)-[:FIRED_ON]->(s:Service)
        WHERE s.name IN $svcs AND a.at >= $from AND a.at <= $to
        RETURN a.id AS id, s.name AS service, a.at AS at, a.severity AS severity, a.signal AS signal
        ORDER BY a.at""", {"svcs": services, "from": start - window_min * 60, "to": start + 30 * 60})


def all_changes_near(g: Graph, start: int, window_min: int = WINDOW_MIN) -> list[dict]:
    return g.read("""
        MATCH (c:Change)-[:CHANGED]->(s:Service)
        WHERE c.at >= $from AND c.at <= $to AND c.kind IN ['deploy', 'config', 'flag']
        RETURN c.id AS id, c.kind AS kind, c.at AS at, c.summary AS summary, s.name AS service
        ORDER BY c.at DESC""", {"from": start - window_min * 60, "to": start + AFTER_GRACE_MIN * 60})


def paths_between(g: Graph, source: str, target: str) -> list[list[str]]:
    if source == target:
        return [[source]]
    rows = g.read(f"""
        MATCH p = (a:Service {{name: $a}})-[:CALLS*1..{MAX_HOPS}]->(b:Service {{name: $b}})
        RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops
        ORDER BY hops
        LIMIT 20""", {"a": source, "b": target})
    return [r["path"] for r in rows]


def similar_incidents(g: Graph, service: str, kind: str | None = None) -> list[dict]:
    """Resolved incidents whose root cause was on `service`: what broke, how it was fixed, what we learned."""
    return g.read("""
        MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(c:Change)-[:CHANGED]->(s:Service {name: $svc})
        WHERE $kind IS NULL OR c.kind = $kind
        OPTIONAL MATCH (i)-[:RESOLVED_BY]->(a:Action)
        OPTIONAL MATCH (i)-[:TAUGHT]->(l:Lesson)
        OPTIONAL MATCH (i)-[:SYMPTOM_ON]->(sym:Service)
        RETURN i.id AS incident, i.title AS title, i.started AS started, sym.name AS symptom,
               c.kind AS cause_kind, c.summary AS cause, a.kind AS fix, a.summary AS fix_summary,
               l.text AS lesson
        ORDER BY i.started DESC""", {"svc": service, "kind": kind})


def who_owns(g: Graph, service: str) -> dict:
    rows = g.read("""
        MATCH (s:Service {name: $svc})-[:OWNED_BY]->(t:Team)
        OPTIONAL MATCH (p:Person)-[:MEMBER_OF]->(t)
        RETURN s.name AS service, t.name AS team,
               [x IN collect({handle: p.handle, name: p.name, on_call: p.on_call}) WHERE x.handle IS NOT NULL] AS people""",
                  {"svc": service})
    if not rows:
        raise KeyError(f"no service {service}")
    r = rows[0]
    r["on_call"] = next((p for p in r["people"] if p["on_call"]), None)
    return r


def blast_radius(g: Graph, service: str) -> dict:
    """Who is hurt if `service` fails: every service that calls it, directly or transitively."""
    callers = g.read(f"""
        MATCH p = (u:Service)-[:CALLS*1..{MAX_HOPS}]->(s:Service {{name: $svc}})
        WITH u, min(length(p)) AS hops
        RETURN u.name AS service, hops, u.internet_exposed AS internet_exposed, u.tier AS tier
        ORDER BY hops, service""", {"svc": service})
    rank = critical_services(g, limit=100)
    pr = next((r for r in rank if r["service"] == service), None)
    return {
        "service": service,
        "callers": callers,
        "exposed_entry_points": [c["service"] for c in callers if c["internet_exposed"]],
        "pagerank": pr["score"] if pr else None,
        "criticality_rank": pr["rank"] if pr else None,
    }


def critical_services(g: Graph, limit: int = 10) -> list[dict]:
    """PageRank over CALLS: services many paths lead into are the ones an outage spreads from."""
    rows = g.read("""
        CALL algo.pageRank('Service', 'CALLS') YIELD node, score
        RETURN node.name AS service, node.kind AS kind, score
        ORDER BY score DESC""")
    for i, r in enumerate(rows, 1):
        r["rank"] = i
        r["score"] = round(r["score"], 4)
    return rows[:limit]


def run_cypher(g: Graph, query: str) -> list[dict]:
    """Read-only escape hatch for questions the fixed tools don't cover. Capped and time-limited."""
    assert_read_only(query)
    return g.read(query)


# --- the judge -----------------------------------------------------------------------------------

def _alerted(alerts: list[dict], service: str, after: int) -> dict | None:
    return next((a for a in alerts if a["service"] == service and a["at"] >= after), None)


def _alert_chain(path: list[str], alerts: list[dict], after: int) -> tuple[float, list[dict]]:
    """How well alerts trace a path from the suspect back up to the symptom.

    1.0: every service below the symptom on the path (the suspect included) alerted after the change, and
    in the order a failure spreading upwards would produce (deeper services first). Out-of-order alerts
    keep 70%. A suspect that never alerts itself (queues and databases often don't) still earns 0.6 when
    every service between it and the symptom alerted, as long as there is at least one. Otherwise a broken
    chain counts half of its unbroken run from the suspect upwards."""
    below = path[1:] if len(path) > 1 else path
    seen = [(svc, _alerted(alerts, svc, after)) for svc in below]
    if all(a for _, a in seen):
        return (1.0 if _in_order(path, alerts, after) else 0.7), [a for _, a in seen]
    middle = seen[:-1]
    if middle and all(a for _, a in middle) and not seen[-1][1]:
        return 0.6, [a for _, a in middle]
    run = 0
    for _, a in reversed(seen):
        if not a:
            break
        run += 1
    return 0.5 * run / len(seen), [a for _, a in seen if a]


def _in_order(path: list[str], alerts: list[dict], after: int) -> bool:
    """A failure spreads upwards: walking from the suspect to the symptom, first alerts should not go back in time."""
    times = [a["at"] for a in (_alerted(alerts, svc, after) for svc in reversed(path)) if a]
    return all(x <= y for x, y in zip(times, times[1:]))


def _callers(g: Graph, service: str, cache: dict) -> dict[str, int]:
    """Every service that calls `service`, directly or transitively, with its distance."""
    if service not in cache:
        rows = g.read(f"""
            MATCH p = (u:Service)-[:CALLS*1..{MAX_HOPS}]->(s:Service {{name: $svc}})
            RETURN u.name AS service, min(length(p)) AS d""", {"svc": service})
        cache[service] = {r["service"]: r["d"] for r in rows}
    return cache[service]


def _recency(lag_min: float) -> float:
    """Halves every 45 minutes before the incident; a change after it started gets a flat 0.3."""
    return math.exp(-max(lag_min, 0) / RECENCY_HALF_MIN * math.log(2)) if lag_min >= 0 else 0.3


def _from_below(g: Graph, service: str, cone: set[str], alerts: list[dict], at: int) -> bool:
    """True when a service below `service` in the symptom's alert cone was already alerting by the time of the
    change, or alerted before `service` itself did: the failure started deeper, not here."""
    below = {d["service"] for d in dependencies(g, service)} & cone
    if not below:
        return False
    own = min((a["at"] for a in alerts if a["service"] == service and a["at"] >= at), default=None)
    first_below = min((a["at"] for a in alerts if a["service"] in below), default=None)
    if first_below is None:
        return False
    return first_below <= at or (own is not None and first_below < own)


def _alert_cone(g: Graph, symptom: str, area: set[str], alerts: list[dict]) -> set[str]:
    """Alerting services joined to the symptom through other alerting services. Faults elsewhere below the
    symptom are real but separate; they shouldn't count against the cause of this one."""
    alerting = {a["service"] for a in alerts} | {symptom}
    edges = g.read("""
        MATCH (a:Service)-[:CALLS]->(b:Service) WHERE a.name IN $area AND b.name IN $area
        RETURN a.name AS a, b.name AS b""", {"area": sorted(area)})
    down: dict[str, list[str]] = {}
    for e in edges:
        down.setdefault(e["a"], []).append(e["b"])
    cone, todo = {symptom}, [symptom]
    while todo:
        for nxt in down.get(todo.pop(), []):
            if nxt in alerting and nxt not in cone:
                cone.add(nxt)
                todo.append(nxt)
    return cone


def _footprint(g: Graph, service: str, alerts: list[dict], after: int, area: set[str], cache: dict,
               cone: set[str] | None = None) -> dict:
    """The fault simulator: if `service` broke at `after`, which alerting services would that explain?

    A failure can only show up on the service itself and on what calls it. Of the services alerting in the
    incident's area, the ones in that predicted footprint are explained; the rest need another cause."""
    observed = {a["service"] for a in alerts if a["service"] in area}
    reach = {service: 0, **_callers(g, service, cache)}
    explained = sorted((svc for svc in observed if svc in reach and _alerted(alerts, svc, after)),
                       key=lambda svc: (reach[svc], svc))
    sequence = [{"service": svc, "distance": reach[svc], "at": _alerted(alerts, svc, after)["at"]} for svc in explained]
    in_cone = observed & (cone or observed)
    return {
        "explained": explained,
        "unexplained": sorted(observed - set(explained)),
        "observed": len(observed),
        "coverage": round(len(explained) / len(observed), 3) if observed else 0.0,
        "cone": sorted(in_cone),
        "cone_coverage": round(len(in_cone & set(explained)) / len(in_cone), 3) if in_cone else 0.0,
        "sequence": sequence,
        "in_order": all(a["at"] <= b["at"] for a, b in zip(sequence, sequence[1:]) if a["distance"] < b["distance"]),
    }


def simulate_failure(g: Graph, service: str) -> dict:
    """What-if before you ship: if `service` failed now, the order in which the alerts would arrive,
    the internet-facing entry points hit, and the teams that would be paged."""
    cache: dict = {}
    reach = _callers(g, service, cache)
    if not g.read("MATCH (s:Service {name: $svc}) RETURN s.name AS s", {"svc": service}):
        raise KeyError(f"no service {service}")
    teams = g.read("""
        MATCH (s:Service)-[:OWNED_BY]->(t:Team) WHERE s.name IN $svcs
        OPTIONAL MATCH (p:Person {on_call: true})-[:MEMBER_OF]->(t)
        RETURN t.name AS team, collect(DISTINCT s.name) AS services, collect(DISTINCT p.name) AS on_call""",
                   {"svcs": [service, *reach]})
    exposed = {r["service"] for r in g.read(
        "MATCH (s:Service) WHERE s.name IN $svcs AND s.internet_exposed RETURN s.name AS service",
        {"svcs": list(reach)})}
    waves: dict[int, list[str]] = {}
    for svc, d in sorted(reach.items(), key=lambda x: (x[1], x[0])):
        waves.setdefault(d, []).append(svc)
    return {"service": service, "waves": [{"distance": d, "services": v} for d, v in sorted(waves.items())],
            "affected": len(reach), "internet_exposed": sorted(exposed), "teams_paged": teams,
            "runbooks": runbooks(g, service)}


def _precedent(g: Graph, service: str, kind: str | None) -> tuple[float, list[dict]]:
    same = similar_incidents(g, service)
    if not same:
        return 0.0, []
    if kind is None:
        hits = [s for s in same if s["cause_kind"] not in ("deploy", "config", "flag")]
    else:
        hits = [s for s in same if s["cause_kind"] == kind]
    return (1.0, hits) if hits else (0.5, same)


def _recommend(g: Graph, cand: dict) -> dict:
    if cand["type"] == "signal":
        return {"kind": "page_owner", "target": cand["service"],
                "summary": f"No change explains this. Page the {cand['service']} owner to check the infrastructure."}
    kind = cand["change"]["kind"]
    svc = cand["service"]
    if kind == "deploy":
        prev = g.read("""
            MATCH (c:Change {kind: 'deploy'})-[:CHANGED]->(s:Service {name: $svc})
            WHERE c.at < $at AND c.version IS NOT NULL
            RETURN c.version AS version ORDER BY c.at DESC LIMIT 1""", {"svc": svc, "at": cand["change"]["at"]})
        to = prev[0]["version"] if prev else "previous release"
        return {"kind": "rollback", "target": svc, "change_id": cand["change"]["id"], "to_version": to,
                "summary": f"Roll back {svc} {cand['change'].get('version') or ''} to {to}".replace("  ", " ")}
    if kind == "config":
        c = cand["change"]
        return {"kind": "revert_config", "target": svc, "change_id": c["id"],
                "summary": f"Revert {c.get('key') or 'config'} on {svc} from {c.get('new')} back to {c.get('old')}"}
    return {"kind": "disable_flag", "target": svc, "change_id": cand["change"]["id"],
            "summary": f"Turn off {cand['change'].get('key') or 'the flag'} on {svc}"}


def also_explains(g: Graph, change: dict, exclude: str) -> list[dict]:
    """Other open incidents this change fully explains: it sits on their symptom's dependency path, landed
    inside their window, and alerts trace an unbroken path from it up to their symptom. One change behind
    several incidents means one fix for all of them."""
    out = []
    others = g.read("""
        MATCH (i:Incident {status: 'open'})-[:SYMPTOM_ON]->(s:Service)
        WHERE i.id <> $exclude AND i.started >= $at - $grace AND i.started <= $at + $window
        RETURN i.id AS id, i.title AS title, i.started AS started, s.name AS symptom""",
                    {"exclude": exclude, "at": change["at"], "grace": AFTER_GRACE_MIN * 60, "window": WINDOW_MIN * 60})
    for other in others:
        start = other["started"]
        sym = other["symptom"]
        if change["service"] != sym and change["service"] not in {d["service"] for d in dependencies(g, sym)}:
            continue
        paths = paths_between(g, sym, change["service"])
        alerts = alerts_near(g, sorted({svc for p in paths for svc in p}), start)
        full = next((p for p in paths if _alert_chain(p, alerts, change["at"])[0] >= 1.0), None)
        if full:
            out.append({"id": other["id"], "title": other["title"], "symptom": sym, "path": full})
    return out


def _other_faults(g: Graph, suspects: list[dict], incident_id: str) -> list[dict]:
    """Alerts the leading suspect can't explain point at independent faults. Greedily pick the suspects that
    explain most of what is left, and name the open incidents those faults already belong to."""
    if not suspects:
        return []
    left = set(suspects[0]["footprint"]["unexplained"])
    anchor = get_incident(g, incident_id)["started"]
    found = []
    # An independent fault need not reach this symptom; it must explain leftover alerts on its own, in order.
    pool = [s for s in suspects[1:] if len(s["footprint"]["explained"]) >= 2 and s["footprint"]["in_order"]]
    while left and pool:
        best = max(pool, key=lambda s: (len(left & set(s["footprint"]["explained"])), s["score"]))
        hit = left & set(best["footprint"]["explained"])
        if not hit:
            break
        linked = g.read("""
            MATCH (i:Incident {status: 'open'})-[:SYMPTOM_ON]->(s:Service)
            WHERE i.id <> $inc AND s.name IN $svcs AND abs(i.started - $at) <= $window
            RETURN i.id AS id, i.title AS title""",
                        {"inc": incident_id, "svcs": best["footprint"]["explained"], "at": anchor, "window": WINDOW_MIN * 60})
        found.append({"service": best["service"], "change": best["change"], "type": best["type"],
                      "explains": sorted(hit), "incidents": [{"id": i["id"], "title": i["title"]} for i in linked]})
        left -= hit
        pool.remove(best)
    return found


def runbooks(g: Graph, service: str) -> list[dict]:
    """Procedural memory: how the team fixes things on this service."""
    return g.read("""
        MATCH (r:Runbook)-[:FOR_SERVICE]->(:Service {name: $svc})
        RETURN r.id AS id, r.title AS title, r.when AS applies_to, r.steps AS steps""", {"svc": service})


def rank_suspects(g: Graph, incident_id: str, limit: int = 6) -> dict:
    """Score every change on the symptom's dependency path, plus alerting dependencies with no change.

    Returns the ranked suspects with a per-factor breakdown, the path each one travels to the symptom,
    and the changes ruled out because nothing connects them to the symptom."""
    inc = get_incident(g, incident_id)
    sym, start = inc["symptom"], inc["started"]
    deps = dependencies(g, sym)
    hops = {sym: 0, **{d["service"]: d["hops"] for d in deps}}
    on_path = list(hops)
    changes = changes_near(g, on_path, start)
    alerts = alerts_near(g, on_path, start)
    first_symptom_alert = min((a["at"] for a in alerts if a["service"] == sym), default=start)
    area, cache = set(on_path), {}
    cone = _alert_cone(g, sym, area, alerts)

    suspects: list[dict] = []
    for c in changes:
        svc = c["service"]
        paths = paths_between(g, sym, svc)
        best = max(((_alert_chain(p, alerts, c["at"]), p) for p in paths), key=lambda x: (x[0][0], -len(x[1])))
        (chain, chain_alerts), path = best
        if _from_below(g, svc, cone, alerts, c["at"]):
            chain *= 0.5  # the failure was already climbing from deeper down: this change is not where it started
        lag_min = (start - c["at"]) / 60
        recency = _recency(lag_min)
        prec, prec_rows = _precedent(g, svc, c["kind"])
        fp = _footprint(g, svc, alerts, c["at"], area, cache, cone)
        factors = {
            "recency": round(recency, 3),
            "proximity": round(1 / (1 + hops[svc]), 3),
            "alert_chain": round(chain, 3),
            "risk": RISK.get(c["risk"] or "low", 0.2),
            "precedent": prec,
            "footprint": fp["cone_coverage"],
        }
        suspects.append({
            "type": "change", "service": svc, "change": c, "hops": hops[svc], "path": path,
            "minutes_before": round(lag_min, 1), "factors": factors,
            "score": round(sum(WEIGHTS[k] * v for k, v in factors.items()), 3),
            "evidence_alerts": chain_alerts, "precedents": prec_rows, "footprint": fp,
        })

    alerting = {a["service"] for a in alerts}
    for svc in alerting - {sym}:
        # Only the deepest alerting services: if something below it is alerting too, that one is the lead.
        below = {d["service"] for d in dependencies(g, svc)} if svc in hops else set()
        if below & alerting:
            continue
        first = min(a["at"] for a in alerts if a["service"] == svc)
        # A recent change on this service or beneath it explains the alert better than "it just broke":
        # then this is a symptom (possibly of a silent cause below), not an independent failure.
        before = [c for c in changes if c["service"] in below | {svc} and c["at"] <= first]
        last = max((c["at"] for c in before), default=None)
        recent = _recency((start - last) / 60) if last is not None else 0.0
        if recent >= 0.5:
            continue
        paths = paths_between(g, sym, svc)
        best = max(((_alert_chain(p, alerts, first - 1), p) for p in paths), key=lambda x: (x[0][0], -len(x[1])))
        (chain, chain_alerts), path = best
        prec, prec_rows = _precedent(g, svc, None)
        fp = _footprint(g, svc, alerts, first - 1, area, cache, cone)
        factors = {
            "alert_chain": round(chain, 3),
            "earliness": 1.0 if first <= first_symptom_alert else 0.3,
            "precedent": prec,
            "proximity": round(1 / (1 + hops[svc]), 3),
            "footprint": fp["cone_coverage"],
            "unchanged": round(1 - recent, 3),
        }
        suspects.append({
            "type": "signal", "service": svc, "change": None, "hops": hops[svc], "path": path,
            "minutes_before": round((start - first) / 60, 1), "factors": factors,
            "score": round(sum(SIGNAL_WEIGHTS[k] * v for k, v in factors.items()), 3),
            "evidence_alerts": chain_alerts, "precedents": prec_rows, "footprint": fp,
        })

    suspects.sort(key=lambda s: (s["score"], s["footprint"]["coverage"]), reverse=True)
    suspects = suspects[:limit]
    for i, s in enumerate(suspects):
        s["recommended_action"] = _recommend(g, s)
        s["owner"] = who_owns(g, s["service"])
        s["also_explains"] = also_explains(g, s["change"], incident_id) if s["change"] and i < 3 else []
        if s["also_explains"]:
            s["recommended_action"]["also_for"] = [o["id"] for o in s["also_explains"]]

    other_faults = _other_faults(g, suspects, incident_id)

    path_set = set(on_path)
    ruled_out = [
        {**c, "reason": f"{c['service']} is not on {sym}'s dependency path"}
        for c in all_changes_near(g, start) if c["service"] not in path_set
    ]
    top = suspects[0] if suspects else None
    margin = round(top["score"] - suspects[1]["score"], 3) if len(suspects) > 1 else (top["score"] if top else 0)
    confidence = "none" if not top else "high" if top["score"] >= 0.75 and margin >= 0.2 else \
        "medium" if top["score"] >= 0.5 else "low"
    return {
        "incident": inc, "dependency_count": len(deps), "suspects": suspects, "alert_cone": sorted(cone),
        "ruled_out": ruled_out, "confidence": confidence, "margin": margin, "other_faults": other_faults,
        "weights": {"change": WEIGHTS, "signal": SIGNAL_WEIGHTS},
    }


# --- writes (human-gated) ------------------------------------------------------------------------

def propose_action(g: Graph, incident_id: str, kind: str, target: str, summary: str,
                   change_id: str | None = None, reason: str = "", also_for: list[str] | None = None) -> dict:
    """Propose a fix. also_for links the same fix to other open incidents the change explains, so one
    approval mitigates all of them."""
    if kind not in ("rollback", "revert_config", "disable_flag", "page_owner"):
        raise ValueError(f"unknown action kind {kind}")
    get_incident(g, incident_id)
    same = g.read("""
        MATCH (a:Action {status: 'proposed', kind: $kind})-[:FOR]->(:Incident {id: $inc}),
              (a)-[:TARGETS]->(:Service {name: $target})
        RETURN a.id AS id, a.summary AS summary""", {"inc": incident_id, "kind": kind, "target": target})
    if same:  # re-running an investigation must not stack duplicate proposals
        return {"id": same[0]["id"], "kind": kind, "target": target, "summary": same[0]["summary"],
                "status": "proposed", "already_proposed": True}
    aid = "ACT-" + uuid.uuid4().hex[:8].upper()
    g.write("""
        MATCH (i:Incident {id: $inc}), (s:Service {name: $target})
        CREATE (a:Action {id: $id, kind: $kind, summary: $summary, reason: $reason, status: 'proposed',
                          at: $at, change_id: $change})
        CREATE (a)-[:FOR]->(i), (a)-[:TARGETS]->(s)
        WITH a
        OPTIONAL MATCH (c:Change {id: $change})
        FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | CREATE (a)-[:REVERTS]->(c))""",
            {"inc": incident_id, "target": target, "id": aid, "kind": kind, "summary": summary,
             "reason": reason, "at": now(), "change": change_id})
    linked = [i for i in (also_for or []) if i != incident_id]
    if linked:
        g.write("""
            MATCH (a:Action {id: $id}), (i:Incident) WHERE i.id IN $ids AND i.status = 'open'
            CREATE (a)-[:FOR]->(i)""", {"id": aid, "ids": linked})
    return {"id": aid, "kind": kind, "target": target, "summary": summary, "status": "proposed",
            "covers": [incident_id] + linked}


def list_actions(g: Graph, incident_id: str) -> list[dict]:
    return g.read("""
        MATCH (a:Action)-[:FOR]->(:Incident {id: $inc})
        OPTIONAL MATCH (a)-[:TARGETS]->(s:Service)
        OPTIONAL MATCH (a)-[:FOR]->(all:Incident)
        WITH a, s, collect(DISTINCT all.id) AS covers
        RETURN a.id AS id, a.kind AS kind, a.summary AS summary, a.reason AS reason, a.status AS status,
               a.at AS at, a.decided_by AS decided_by, s.name AS target, a.change_id AS change_id, covers
        ORDER BY a.at""", {"inc": incident_id})


def decide_action(g: Graph, action_id: str, approve: bool, who: str) -> dict:
    """A human approves or rejects. Approval 'executes' the action in the simulated world: it writes the
    compensating change to the graph and marks the incident mitigated. Nothing outside the graph is touched."""
    rows = g.read("""
        MATCH (a:Action {id: $id})-[:FOR]->(i:Incident), (a)-[:TARGETS]->(s:Service)
        OPTIONAL MATCH (a)-[:REVERTS]->(c:Change)
        RETURN a.status AS status, a.kind AS kind, a.summary AS summary, i.id AS incident, s.name AS target,
               c.id AS change_id, c.version AS version, c.old AS old, c.key AS key""", {"id": action_id})
    if not rows:
        raise KeyError(f"no action {action_id}")
    a = rows[0]
    if a["status"] != "proposed":
        raise ValueError(f"{action_id} is already {a['status']}")
    if not approve:
        g.write("MATCH (a:Action {id: $id}) SET a.status = 'rejected', a.decided_by = $who, a.decided_at = $at",
                {"id": action_id, "who": who, "at": now()})
        return {"id": action_id, "status": "rejected"}
    g.write("""
        MATCH (a:Action {id: $id})
        SET a.status = 'executed', a.decided_by = $who, a.decided_at = $at""",
            {"id": action_id, "who": who, "at": now()})
    covered = [r["id"] for r in g.read(
        "MATCH (:Action {id: $id})-[:FOR]->(i:Incident) RETURN i.id AS id", {"id": action_id})]
    if a["kind"] != "page_owner":
        g.write("""
            MATCH (a:Action {id: $id})-[:FOR]->(i:Incident) WHERE i.status = 'open'
            SET i.status = 'mitigated'""", {"id": action_id})
        g.write("""
            MATCH (a:Action {id: $id})-[:TARGETS]->(s:Service)
            CREATE (a)-[:CREATED]->(:Change {id: 'R-' + $id, kind: 'remediation', at: $at, summary: a.summary,
                                             risk: 'low'})-[:CHANGED]->(s)""", {"id": action_id, "at": now()})
    return {"id": action_id, "status": "executed", "incident": a["incident"], "incidents": covered}


def resolve_incident(g: Graph, incident_id: str, root_cause_change_id: str | None, lesson: str,
                     cause_service: str | None = None) -> dict:
    """Close the incident and write what was learned. This is the memory the next investigation reads."""
    get_incident(g, incident_id)
    if root_cause_change_id:
        g.write("""
            MATCH (i:Incident {id: $inc}), (c:Change {id: $cid})
            MERGE (i)-[:ROOT_CAUSE]->(c)""", {"inc": incident_id, "cid": root_cause_change_id})
    elif cause_service:
        g.write("""
            MATCH (i:Incident {id: $inc}), (s:Service {name: $svc})
            CREATE (i)-[:ROOT_CAUSE]->(:Change {id: 'X-' + $inc, kind: 'infra', at: i.started,
                                                summary: 'No change: infrastructure failure on ' + $svc,
                                                risk: 'high'})-[:CHANGED]->(s)""",
                {"inc": incident_id, "svc": cause_service})
    g.write("""
        MATCH (i:Incident {id: $inc})
        SET i.status = 'resolved', i.resolved = $at
        CREATE (i)-[:TAUGHT]->(:Lesson {text: $lesson, at: $at})
        WITH i
        MATCH (a:Action {status: 'executed'})-[:FOR]->(i)
        MERGE (i)-[:RESOLVED_BY]->(a)""", {"inc": incident_id, "lesson": lesson, "at": now()})
    return {"incident": incident_id, "status": "resolved"}


def record_investigation(g: Graph, incident_id: str, mode: str, ranking: dict, summary: str,
                         steps: int) -> str:
    """Each investigation is kept: the next one on the same incident starts from what this one found."""
    iid = "INV-" + uuid.uuid4().hex[:8].upper()
    top = ranking["suspects"][0] if ranking["suspects"] else None
    g.write("""
        MATCH (i:Incident {id: $inc})
        CREATE (v:Investigation {id: $id, at: $at, mode: $mode, summary: $summary, confidence: $conf,
                                 steps: $steps, top_service: $svc, top_change: $chg, wall_at: $wall})
        CREATE (v)-[:INVESTIGATED]->(i)
        WITH v
        OPTIONAL MATCH (c:Change {id: $chg})
        FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | CREATE (v)-[:BLAMED]->(c))""",
            {"inc": incident_id, "id": iid, "at": now(), "mode": mode, "summary": summary[:2000],
             "conf": ranking["confidence"], "steps": steps, "svc": top["service"] if top else None,
             "chg": top["change"]["id"] if top and top["change"] else None, "wall": int(time.time())})
    return iid


def past_investigations(g: Graph, incident_id: str) -> list[dict]:
    return g.read("""
        MATCH (v:Investigation)-[:INVESTIGATED]->(:Incident {id: $inc})
        RETURN v.id AS id, v.mode AS mode, v.summary AS summary, v.confidence AS confidence,
               v.top_service AS top_service, v.top_change AS top_change, v.wall_at AS wall_at
        ORDER BY v.wall_at DESC LIMIT 5""", {"inc": incident_id})


def graph_view(g: Graph) -> dict:
    """Services and CALLS edges for the UI canvas, with PageRank for node size."""
    nodes = g.read("""
        MATCH (s:Service)-[:OWNED_BY]->(t:Team)
        RETURN s.name AS id, s.kind AS kind, s.tier AS tier, s.internet_exposed AS exposed, t.name AS team""")
    edges = g.read("MATCH (a:Service)-[:CALLS]->(b:Service) RETURN a.name AS source, b.name AS target")
    pr = {r["service"]: r["score"] for r in critical_services(g, limit=100)}
    for n in nodes:
        n["pagerank"] = pr.get(n["id"], 0)
    return {"nodes": nodes, "edges": edges}


def stats(g: Graph) -> dict[str, Any]:
    labels = g.read("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS n ORDER BY n DESC")
    edges = g.read("MATCH ()-[r]->() RETURN type(r) AS type, count(r) AS n ORDER BY n DESC")
    return {"nodes": {r["label"]: r["n"] for r in labels}, "edges": {r["type"]: r["n"] for r in edges}}


# --- agent handoffs and scored links -------------------------------------------------------------

ROLES = ("triage", "investigator", "responder")


def new_run_id() -> str:
    return "RUN-" + uuid.uuid4().hex[:8].upper()


def write_handoff(g: Graph, incident_id: str, role: str, summary: str, data: dict | None = None,
                  run_id: str | None = None) -> dict:
    """An agent's output, written to the graph. The next agent reads it back from there, never from memory,
    so a later session (or a different agent) can pick the incident up where this one stopped."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role}")
    get_incident(g, incident_id)
    eid = "EP-" + uuid.uuid4().hex[:8].upper()
    g.write("""
        MATCH (i:Incident {id: $inc})
        CREATE (e:Episode {id: $id, role: $role, run: $run, summary: $summary, data: $data, at: $at, wall_at: $wall})
        CREATE (e)-[:ABOUT]->(i)
        WITH e
        OPTIONAL MATCH (prev:Episode {run: $run}) WHERE prev.id <> $id AND prev.wall_at <= $wall
        WITH e, prev ORDER BY prev.wall_at DESC LIMIT 1
        FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END | CREATE (e)-[:READ]->(prev))""",
            {"inc": incident_id, "id": eid, "role": role, "run": run_id or eid, "summary": summary[:2000],
             "data": json.dumps(data or {}, default=str)[:20000], "at": now(), "wall": time.time()})
    return {"id": eid, "role": role, "run": run_id or eid}


def read_handoff(g: Graph, incident_id: str, role: str | None = None, run_id: str | None = None) -> dict | None:
    """The latest handoff on an incident (optionally from one role, or one investigation run)."""
    rows = g.read("""
        MATCH (e:Episode)-[:ABOUT]->(:Incident {id: $inc})
        WHERE ($role IS NULL OR e.role = $role) AND ($run IS NULL OR e.run = $run)
        RETURN e.id AS id, e.role AS role, e.run AS run, e.summary AS summary, e.data AS data
        ORDER BY e.wall_at DESC LIMIT 1""", {"inc": incident_id, "role": role, "run": run_id})
    if not rows:
        return None
    r = rows[0]
    r["data"] = json.loads(r["data"] or "{}")
    return r


def record_suspicions(g: Graph, incident_id: str, ranking: dict) -> None:
    """Keep the judge's verdicts as scored edges: (Incident)-[:SUSPECTED_CAUSE {score, rank}]->(Change|Service),
    and (Incident)-[:SIMILAR_TO {score, reason}]->(past Incident) for every precedent it leaned on."""
    g.write("MATCH (:Incident {id: $inc})-[r:SUSPECTED_CAUSE|SIMILAR_TO]->() DELETE r", {"inc": incident_id})
    rows = [{"rank": i + 1, "score": s["score"], "change": (s["change"] or {}).get("id"), "service": s["service"],
             "coverage": s["footprint"]["coverage"]} for i, s in enumerate(ranking["suspects"][:5])]
    g.write("""
        UNWIND $rows AS r
        MATCH (i:Incident {id: $inc})
        OPTIONAL MATCH (c:Change {id: r.change})
        OPTIONAL MATCH (s:Service {name: r.service})
        WITH i, r, CASE WHEN c IS NULL THEN s ELSE c END AS target
        CREATE (i)-[:SUSPECTED_CAUSE {rank: r.rank, score: r.score, coverage: r.coverage, confidence: $conf}]->(target)""",
            {"inc": incident_id, "rows": rows, "conf": ranking["confidence"]})
    sims = {}
    for s in ranking["suspects"][:3]:
        for p in s["precedents"]:
            kind = (s["change"] or {}).get("kind")
            score = 1.0 if p["cause_kind"] == kind or (kind is None and p["cause_kind"] not in ("deploy", "config", "flag")) else 0.5
            score = round(score * s["score"], 3)
            if score > sims.get(p["incident"], {}).get("score", 0):
                sims[p["incident"]] = {"past": p["incident"], "score": score,
                                       "reason": f"{p['cause_kind']} on {s['service']}: {p['cause']}"}
    if sims:
        g.write("""
            UNWIND $sims AS x
            MATCH (i:Incident {id: $inc}), (p:Incident {id: x.past})
            CREATE (i)-[:SIMILAR_TO {score: x.score, reason: x.reason}]->(p)""",
                {"inc": incident_id, "sims": list(sims.values())})
