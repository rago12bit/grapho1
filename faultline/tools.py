"""Graph tools the agent (Claude, the offline investigator, or an MCP client) calls.

Every tool takes a tenant Graph, returns plain JSON, and records the Cypher it ran in g.trace.
Reads go through GRAPH.RO_QUERY. The only writes are actions, investigations and resolutions,
and those are explicit tools a human gates in the UI.
"""
from __future__ import annotations

import json
import math
import re
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
HOUR_S = 3600
SYMPTOM_CAN_BE_CAUSE = True
# Where alerts carry an anomaly strength (how many standard deviations their metrics moved: the RCAEval tenants),
# a silent-failure suspect's score blends in its strength relative to the strongest alert in the area. Kestrel's
# alerts carry none, so its scores are unchanged.
STRENGTH_W = 0.5
RISK = {"high": 1.0, "medium": 0.5, "low": 0.2}


def now() -> int:
    """Demo clock. The synthetic world is frozen at the seed's NOW so every run tells the same story."""
    return NOW


def clock(g: Graph, incident_ids: list[str]) -> int:
    """The time a decision on these incidents happens. The demo world is frozen at NOW, but incidents opened
    from live alerts can be later than that, so a decision is never stamped before the alerts it answers."""
    r = g.read("""
        MATCH (i:Incident)-[:SYMPTOM_ON]->(:Service) WHERE i.id IN $ids
        OPTIONAL MATCH (a:Alert) WHERE a.at >= i.started - 3600 AND a.at <= i.started + 7200
        RETURN max(i.started) AS started, max(a.at) AS last""", {"ids": incident_ids})
    latest = max([x for x in (r[0]["started"], r[0]["last"]) if x is not None], default=0) if r else 0
    return max(now(), latest + 60 if latest > now() else 0)


# --- reading the graph -------------------------------------------------------------------------

def list_incidents(g: Graph, status: str | None = "open") -> list[dict]:
    rows = g.read("""
        MATCH (i:Incident)-[:SYMPTOM_ON]->(s:Service)
        WHERE $status IS NULL OR i.status = $status
        RETURN i.id AS id, i.title AS title, i.severity AS severity, i.status AS status,
               i.started AS started, s.name AS symptom, i.source AS source
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
        RETURN a.id AS id, s.name AS service, a.at AS at, a.severity AS severity, a.signal AS signal,
               a.cleared_at AS cleared_at, a.strength AS strength
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
        MATCH (s)-[:OWNED_BY]->(t:Team) WHERE (s:Service OR s:Node) AND s.name = $svc
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


SHAPE_MIN = 0.9      # cosine similarity a past incident needs before its shape counts as precedent


def _shape_precedent(g: Graph, inc: dict, suspects: list[dict], incident_id: str) -> None:
    """Precedent by shape: resolved incidents on other services that told the same story (same kind of cause,
    same kind of thing broken, same depth below a similar symptom) are attached to each suspect as evidence.
    They do not change the score: in the planted-fault eval, crediting them helped look-alike decoys as much as
    real causes (learning round 98.2% -> 97.5% on seed 3), so only same-service precedent is scored."""
    for s in suspects:
        try:
            matches = similar_by_shape(g, suspect_signature(g, inc, s), k=3, exclude=incident_id)
        except Exception:      # graphs built before the vector index existed
            matches = []
        s["shape_matches"] = [m for m in matches if m["similarity"] >= SHAPE_MIN and m["cause_service"] != s["service"]]


def _precedent(g: Graph, service: str, kind: str | None) -> tuple[float, list[dict]]:
    same = similar_incidents(g, service)
    if not same:
        return 0.0, []
    if kind is None:
        hits = [s for s in same if s["cause_kind"] not in ("deploy", "config", "flag")]
    else:
        hits = [s for s in same if s["cause_kind"] == kind]
    return (1.0, hits) if hits else (0.5, same)


def fix_track_record(g: Graph, service: str | None = None, kind: str | None = None) -> list[dict]:
    """Procedural memory: how each kind of fix has done on each service. Counted at read time from executed
    actions and whether verify_fix saw the alerts clear (verified), never kept as counters that could drift."""
    rows = g.read("""
        MATCH (a:Action {status: 'executed'})-[:TARGETS]->(s)
        WHERE ($svc IS NULL OR s.name = $svc) AND ($kind IS NULL OR a.kind = $kind)
        OPTIONAL MATCH (a)-[:FOR]->(i:Incident)
        WITH a, s, collect(DISTINCT i.id) AS incs
        WITH a.kind AS kind, s.name AS service,
             sum(CASE WHEN a.verified = true THEN 1 ELSE 0 END) AS worked,
             sum(CASE WHEN a.verified = false THEN 1 ELSE 0 END) AS failed,
             sum(CASE WHEN a.verified IS NULL THEN 1 ELSE 0 END) AS unchecked,
             collect({action: a.id, incidents: incs, verified: a.verified, at: coalesce(a.decided_at, a.at),
                      summary: a.summary}) AS history
        RETURN kind, service, worked, failed, unchecked, history
        ORDER BY worked + failed DESC, failed DESC, service, kind""", {"svc": service, "kind": kind})
    for r in rows:
        for k in ("worked", "failed", "unchecked"):
            r[k] = int(r[k])
        r["history"] = sorted(r["history"], key=lambda h: -(h["at"] or 0))[:5]
    return rows


def _record_note(g: Graph, kind: str, target: str) -> dict | None:
    """What memory says about the fix the responder is about to propose."""
    rec = fix_track_record(g, target, kind)
    if not rec:
        return None
    r = rec[0]
    tried = r["worked"] + r["failed"]
    if not tried:
        return None
    incs = sorted({i for h in r["history"] for i in h["incidents"]})
    note = (f"{kind} on {target} worked {r['worked']} of {tried} time{'s' if tried > 1 else ''} before"
            f" ({', '.join(incs)})")
    if r["failed"]:
        note += ": it has not always cleared the alerts, so watch them after approving"
    return {"worked": r["worked"], "failed": r["failed"], "incidents": incs, "note": note}


def _recommend(g: Graph, cand: dict) -> dict:
    act = _recommend_kind(g, cand)
    rec = _record_note(g, act["kind"], act["target"])
    if rec:
        act["track_record"] = rec
    return act


def _recommend_kind(g: Graph, cand: dict) -> dict:
    if cand["type"] == "host":
        h = cand["host"]
        return {"kind": "page_owner", "target": h["node"],
                "summary": f"Cordon and drain {h['node']} ({h['zone']}) so {' and '.join(h['services'])} reschedule elsewhere; "
                           f"page the node's owner"}
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


CONF_SLOPE, CONF_MID = 12.0, 0.10


HOST_WEIGHTS = {"co_location": 0.25, "simultaneity": 0.15, "footprint": 0.25, "alert_chain": 0.15, "earliness": 0.10,
                "unchanged": 0.10}
HOST_SPREAD_MIN = 5          # services failing from one host do so within minutes of each other


def _host_suspects(g: Graph, signals: list[dict], alerts: list[dict], first_symptom_alert: int,
                   power: dict[str, float], top_power: float) -> list[dict]:
    """A machine as the cause. When two or more services each failed on their own (nothing below them was
    alerting first, nothing changed on them), and none of them calls another, a node they share is a better
    answer than either: one cause that explains both. Needs placement in the graph
    ((Service)-[:DEPLOYED_AS]->(:Deployment)-[:HAS_POD]->(:Pod)-[:ON_NODE]->(:Node)); without it, nothing."""
    if len(signals) < 2:
        return []
    by = {s["service"]: s for s in signals}
    rows = g.read("""
        MATCH (s:Service)-[:DEPLOYED_AS]->(:Deployment)-[:HAS_POD]->(:Pod)-[:ON_NODE]->(n:Node)
        WHERE s.name IN $svcs
        RETURN n.name AS node, n.zone AS zone, collect(DISTINCT s.name) AS services""", {"svcs": sorted(by)})
    out = []
    for r in rows:
        hosted = sorted(r["services"])
        if len(hosted) < 2:
            continue
        # one of them calling another is a chain, which the signal suspects already explain
        if any(h2 in {d["service"] for d in dependencies(g, h1)} for h1 in hosted for h2 in hosted if h1 != h2):
            continue
        sus = [by[h] for h in hosted]
        firsts = [x["first_alert"] for x in sus]
        spread_min = (max(firsts) - min(firsts)) / 60
        explained = sorted({e for x in sus for e in x["footprint"]["explained"]})
        fp0 = sus[0]["footprint"]
        cone = set(fp0["cone"])
        seq = sorted((q for x in sus for q in x["footprint"]["sequence"]), key=lambda q: (q["at"], q["service"]))
        seen, sequence = set(), []
        for q in seq:
            if q["service"] not in seen:
                seen.add(q["service"])
                sequence.append(q)
        fp = {"explained": explained, "unexplained": sorted((set(fp0["explained"]) | set(fp0["unexplained"])) - set(explained)),
              "observed": fp0["observed"], "coverage": round(len(explained) / fp0["observed"], 3) if fp0["observed"] else 0.0,
              "cone": fp0["cone"], "cone_coverage": round(len(cone & set(explained)) / len(cone), 3) if cone else 0.0,
              "sequence": sequence, "in_order": all(x["footprint"]["in_order"] for x in sus)}
        factors = {
            "co_location": round(len(hosted) / len(signals), 3),
            "simultaneity": round(math.exp(-spread_min / HOST_SPREAD_MIN), 3),
            "footprint": fp["cone_coverage"],
            "alert_chain": max(x["factors"]["alert_chain"] for x in sus),
            "earliness": 1.0 if min(firsts) <= first_symptom_alert else 0.3,
            "unchanged": min(x["factors"]["unchanged"] for x in sus),
        }
        score = sum(HOST_WEIGHTS[k] * v for k, v in factors.items())
        if top_power > 0:
            factors["strength"] = round(max(math.log1p(power.get(h, 0.0)) for h in hosted) / math.log1p(top_power), 3)
            score = (1 - STRENGTH_W) * score + STRENGTH_W * factors["strength"]
        lead = min(sus, key=lambda x: (x["first_alert"], x["service"]))
        out.append({
            "type": "host", "service": r["node"], "change": None, "hops": min(x["hops"] for x in sus),
            "path": lead["path"], "minutes_before": max(x["minutes_before"] for x in sus), "factors": factors,
            "score": round(score, 3), "evidence_alerts": [a for x in sus for a in x["evidence_alerts"]],
            "precedents": [], "footprint": fp, "first_alert": min(firsts),
            "host": {"node": r["node"], "zone": r["zone"], "services": hosted, "spread_min": round(spread_min, 1)},
        })
    return out


# --- learning from outcomes ----------------------------------------------------------------------------

LEARNED_FEATURES = ["alert_chain", "earliness", "proximity", "footprint", "unchanged", "strength", "track"]


def outcome_history(g: Graph) -> list[dict]:
    """Every solved incident with its root-cause service and the services that alerted in its window: the
    outcomes the learned model and the track record are counted from."""
    return g.read("""
        MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(:Change)-[:CHANGED]->(c:Service)
        OPTIONAL MATCH (a:Alert)-[:FIRED_ON]->(s:Service) WHERE a.at >= i.started - 7200 AND a.at <= i.started + 1800
        RETURN i.id AS id, c.name AS cause, collect(DISTINCT s.name) AS alerting""")


def track_record(history: list[dict], service: str, exclude: str | None = None) -> float:
    """Of the solved incidents where `service` alerted, the share it caused (smoothed: 1 of 2 with no history).
    A service that alerts in every incident and is rarely the cause scores low."""
    seen = [h for h in history if h["id"] != exclude and service in h["alerting"]]
    caused = sum(h["cause"] == service for h in seen)
    return round((caused + 1) / (len(seen) + 2), 3)


def learned_model(g: Graph) -> dict | None:
    rows = g.read("MATCH (m:Model {kind: 'signal'}) RETURN m.weights AS w, m.bias AS b, m.n AS n, m.enabled AS on")
    if not rows or not rows[0]["on"]:
        return None
    return {"w": json.loads(rows[0]["w"]), "b": rows[0]["b"], "n": rows[0]["n"]}


def learned_score(model: dict, factors: dict) -> float:
    z = model["b"] + sum(model["w"][k] * factors.get(k, 0.0) for k in LEARNED_FEATURES)
    return 1 / (1 + math.exp(-z))


def feedback_on(g: Graph, incident_id: str) -> dict[str, dict]:
    """What people said about this incident's suspects, keyed by the suspect's label."""
    rows = g.read("""
        MATCH (f:Feedback)-[:ABOUT]->(:Incident {id: $inc})
        RETURN f.target AS target, f.verdict AS verdict, f.by AS by, f.note AS note, f.at AS at ORDER BY f.at""",
                  {"inc": incident_id})
    return {r["target"]: r for r in rows}


def give_feedback(g: Graph, incident_id: str, target: str, verdict: str, by: str, note: str = "") -> dict:
    """A person's verdict on one suspect: 'not_cause' rules it out for this incident at once; 'confirmed' backs
    it. Either way it is kept as a labelled outcome the model learns from. `target` is a suspect's label as the
    judge prints it (a change id, 'svc (no change)', 'node (shared host)')."""
    if verdict not in ("not_cause", "confirmed"):
        raise ValueError("verdict is not_cause or confirmed")
    get_incident(g, incident_id)
    g.write("""
        MATCH (i:Incident {id: $inc})
        OPTIONAL MATCH (old:Feedback {target: $t})-[:ABOUT]->(i)
        DETACH DELETE old""", {"inc": incident_id, "t": target})
    g.write("""
        MATCH (i:Incident {id: $inc})
        CREATE (:Feedback {target: $t, verdict: $v, by: $by, note: $note, at: $at})-[:ABOUT]->(i)""",
            {"inc": incident_id, "t": target, "v": verdict, "by": by[:60], "note": note[:300], "at": clock(g, [incident_id])})
    return {"incident": incident_id, "target": target, "verdict": verdict, "by": by}


def confidence_from(top: float, gap: float) -> tuple[float, str]:
    """Confidence comes from how far the leader is ahead, not from its raw score: a 0.9 with a 0.88 runner-up
    is a coin toss. A logistic on the gap to the runner-up (0.5 at a 0.10 gap), damped when the leader itself
    scores low."""
    p = 1 / (1 + math.exp(-CONF_SLOPE * (gap - CONF_MID))) * min(1.0, top / 0.6)
    p = round(p, 3)
    return p, "high" if p >= 0.85 else "medium" if p >= 0.6 else "low"


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
        below_first = _from_below(g, svc, cone, alerts, c["at"])
        if below_first:
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
            "evidence_alerts": chain_alerts, "precedents": prec_rows, "footprint": fp, "from_below": below_first,
        })

    alerting = {a["service"] for a in alerts}
    model = learned_model(g)
    history = outcome_history(g) if model else []
    power = {a["service"]: max(a.get("strength") or 0.0, 0.0) for a in alerts if a.get("strength") is not None}
    top_power = max(power.values(), default=0.0)
    # The symptom service itself competes too: it can be where the failure started, when nothing it depends on
    # was alerting by the time it did (a fault in the entry point's own code, say).
    for svc in alerting if SYMPTOM_CAN_BE_CAUSE else alerting - {sym}:
        first = min(a["at"] for a in alerts if a["service"] == svc)
        # Only where the failure started: if something below this service was already alerting by the time it
        # alerted, that one is the lead. Things below that alert *later* are knock-on effects, not the origin.
        below = {d["service"] for d in dependencies(g, svc)} if svc in hops else set()
        if any(a["at"] <= first for a in alerts if a["service"] in below):
            continue
        # A recent change on this service or beneath it explains the alert better than "it just broke":
        # then this is a symptom (possibly of a silent cause below), not an independent failure.
        before = [c for c in changes if c["service"] in below | {svc} and c["at"] <= first]
        last = max((c["at"] for c in before), default=None)
        recent = _recency((start - last) / 60) if last is not None else 0.0
        if recent >= 0.5:
            continue
        paths = paths_between(g, sym, svc) if svc != sym else [[sym]]
        if not paths:
            continue
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
        score = sum(SIGNAL_WEIGHTS[k] * v for k, v in factors.items())
        if top_power > 0:
            factors["strength"] = round(math.log1p(power.get(svc, 0.0)) / math.log1p(top_power), 3)
            score = (1 - STRENGTH_W) * score + STRENGTH_W * factors["strength"]
        if model:
            # weights learned from this company's own solved incidents, plus how often this service turned out
            # to be the cause when it alerted before
            factors["track"] = track_record(history, svc, exclude=incident_id)
            score = (1 - SIGNAL_WEIGHTS["precedent"]) * learned_score(model, factors) + SIGNAL_WEIGHTS["precedent"] * prec
        suspects.append({
            "type": "signal", "service": svc, "change": None, "hops": hops[svc], "path": path,
            "minutes_before": round((start - first) / 60, 1), "factors": factors,
            "score": round(score, 3),
            "evidence_alerts": chain_alerts, "precedents": prec_rows, "footprint": fp, "first_alert": first,
        })

    suspects += _host_suspects(g, [s for s in suspects if s["type"] == "signal"], alerts, first_symptom_alert, power, top_power)
    said = feedback_on(g, incident_id)
    for s in suspects:
        f = said.get(_label(s))
        if f:
            s["feedback"] = f
            if f["verdict"] == "not_cause":
                s["score"] = 0.0          # a person who looked ruled it out: it goes to the bottom, with the reason
    _shape_precedent(g, inc, suspects, incident_id)
    # Ties go to whichever started first (a change's time, a silent failure's first alert), then by name, so
    # the same graph always gives the same answer whatever order the database returned rows in.
    suspects.sort(key=lambda s: (s["change"]["id"] if s["change"] else s["service"]))
    suspects.sort(key=lambda s: (s["score"], s["footprint"]["coverage"],
                                 -(s["change"]["at"] if s["change"] else s["first_alert"])), reverse=True)
    suspects = suspects[:limit] + [s for s in suspects[limit:] if s.get("feedback")]
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
    p_conf, confidence = confidence_from(top["score"] if top else 0.0, margin) if top else (0.0, "none")
    return {
        "incident": inc, "dependency_count": len(deps), "suspects": suspects, "alert_cone": sorted(cone),
        "ruled_out": ruled_out, "confidence": confidence, "confidence_p": p_conf, "margin": margin,
        "other_faults": other_faults, "alerts": alerts,
        "weights": {"change": WEIGHTS, "signal": SIGNAL_WEIGHTS, "host": HOST_WEIGHTS},
    }


# --- writes (human-gated) ------------------------------------------------------------------------

def propose_action(g: Graph, incident_id: str, kind: str, target: str, summary: str,
                   change_id: str | None = None, reason: str = "", also_for: list[str] | None = None) -> dict:
    """Propose a fix. also_for links the same fix to other open incidents the change explains, so one
    approval mitigates all of them."""
    if kind not in ("rollback", "revert_config", "disable_flag", "page_owner"):
        raise ValueError(f"unknown action kind {kind}")
    get_incident(g, incident_id)
    if not g.read("MATCH (s) WHERE (s:Service OR s:Node) AND s.name = $t RETURN s.name AS n", {"t": target}):
        raise KeyError(f"no service or node {target}")
    same = g.read("""
        MATCH (a:Action {status: 'proposed', kind: $kind})-[:FOR]->(:Incident {id: $inc}),
              (a)-[:TARGETS]->({name: $target})
        RETURN a.id AS id, a.summary AS summary""", {"inc": incident_id, "kind": kind, "target": target})
    if same:  # re-running an investigation must not stack duplicate proposals
        return {"id": same[0]["id"], "kind": kind, "target": target, "summary": same[0]["summary"],
                "status": "proposed", "already_proposed": True}
    aid = "ACT-" + uuid.uuid4().hex[:8].upper()
    g.write("""
        MATCH (i:Incident {id: $inc}), (s) WHERE (s:Service OR s:Node) AND s.name = $target
        CREATE (a:Action {id: $id, kind: $kind, summary: $summary, reason: $reason, status: 'proposed',
                          at: $at, change_id: $change})
        CREATE (a)-[:FOR]->(i), (a)-[:TARGETS]->(s)
        WITH a
        OPTIONAL MATCH (c:Change {id: $change})
        FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | CREATE (a)-[:REVERTS]->(c))""",
            {"inc": incident_id, "target": target, "id": aid, "kind": kind, "summary": summary,
             "reason": reason, "at": clock(g, [incident_id]), "change": change_id})
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
        OPTIONAL MATCH (a)-[:TARGETS]->(s)
        OPTIONAL MATCH (a)-[:FOR]->(all:Incident)
        WITH a, s, collect(DISTINCT all.id) AS covers
        RETURN a.id AS id, a.kind AS kind, a.summary AS summary, a.reason AS reason, a.status AS status,
               a.at AS at, a.decided_by AS decided_by, s.name AS target, a.change_id AS change_id, covers,
               a.verified AS verified, a.verification AS verification
        ORDER BY a.at""", {"inc": incident_id})


def decide_action(g: Graph, action_id: str, approve: bool, who: str) -> dict:
    """A human approves or rejects. Approval 'executes' the action in the simulated world: it writes the
    compensating change to the graph and marks the incident mitigated. Nothing outside the graph is touched."""
    rows = g.read("""
        MATCH (a:Action {id: $id})-[:FOR]->(i:Incident), (a)-[:TARGETS]->(s)
        OPTIONAL MATCH (a)-[:REVERTS]->(c:Change)
        RETURN a.status AS status, a.kind AS kind, a.summary AS summary, i.id AS incident, s.name AS target,
               c.id AS change_id, c.version AS version, c.old AS old, c.key AS key""", {"id": action_id})
    if not rows:
        raise KeyError(f"no action {action_id}")
    a = rows[0]
    if a["status"] != "proposed":
        raise ValueError(f"{action_id} is already {a['status']}")
    covered = [r["id"] for r in g.read(
        "MATCH (:Action {id: $id})-[:FOR]->(i:Incident) RETURN i.id AS id", {"id": action_id})]
    at = clock(g, covered)
    if not approve:
        g.write("MATCH (a:Action {id: $id}) SET a.status = 'rejected', a.decided_by = $who, a.decided_at = $at",
                {"id": action_id, "who": who, "at": at})
        return {"id": action_id, "status": "rejected"}
    g.write("""
        MATCH (a:Action {id: $id})
        SET a.status = 'executed', a.decided_by = $who, a.decided_at = $at
        WITH a
        OPTIONAL MATCH (p:Person {handle: $who})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:APPROVED]->(a))""",
            {"id": action_id, "who": who, "at": at})
    if a["kind"] != "page_owner":
        g.write("""
            MATCH (a:Action {id: $id})-[:FOR]->(i:Incident) WHERE i.status = 'open'
            SET i.status = 'mitigated'""", {"id": action_id})
        g.write("""
            MATCH (a:Action {id: $id})-[:TARGETS]->(s)
            CREATE (a)-[:CREATED]->(:Change {id: 'R-' + $id, kind: 'remediation', at: $at, summary: a.summary,
                                             risk: 'low'})-[:CHANGED]->(s)""", {"id": action_id, "at": at})
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
        MATCH (a:Action {status: 'executed'})-[:FOR]->(i) WHERE coalesce(a.verified, true)
        MERGE (i)-[:RESOLVED_BY]->(a)""", {"inc": incident_id, "lesson": lesson, "at": clock(g, [incident_id])})
    store_signature(g, incident_id)
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

ROLES = ("triage", "investigator", "skeptic", "responder")


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
        OPTIONAL MATCH (n:Node {name: r.service})
        WITH i, r, coalesce(c, s, n) AS target
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


# --- the skeptic ---------------------------------------------------------------------------------

SKEPTIC_TOLERANCE = 0.15   # a challenger this close to the leader's score may take over if it explains more
SKEPTIC_ROUNDS = 2


def _challenges(lead: dict, ranking: dict, others: list[dict], groups: list[dict] | None = None) -> tuple[list[dict], dict | None]:
    """Every check the skeptic runs against one leader. Returns the checks and, if one fails in a way another
    suspect answers better, that suspect."""
    cone = set(ranking.get("alert_cone") or [])
    sym = ranking["incident"]["symptom"]
    fp = lead["footprint"]
    explained = set(fp["explained"])
    missing = sorted(cone - explained - {sym}) if cone else []
    checks = []
    better = None
    if missing:
        rivals = [o for o in others if len(cone & set(o["footprint"]["explained"])) > len(cone & explained)
                  and o["score"] >= lead["score"] - SKEPTIC_TOLERANCE]
        better = max(rivals, key=lambda o: (len(cone & set(o["footprint"]["explained"])), o["score"]), default=None)
        checks.append({"check": "explains every alert joined to the symptom", "passed": False,
                       "detail": f"leaves {', '.join(missing)} unexplained"
                                 + (f"; {_label(better)} explains more and scores {better['score']}" if better else
                                    "; no other suspect does better within reach")})
    else:
        checks.append({"check": "explains every alert joined to the symptom", "passed": True,
                       "detail": f"{len(cone & explained)} of {len(cone)}"})
    checks.append({"check": "alerts arrive in the predicted order", "passed": bool(fp["in_order"]),
                   "detail": " → ".join(x["service"] for x in fp["sequence"]) or "no alerts after it"})
    if lead["change"]:
        checks.append({"check": "the change landed before the incident", "passed": lead["minutes_before"] >= 0,
                       "detail": f"{lead['minutes_before']:g} min before"})
    chain = lead["factors"]["alert_chain"]
    checks.append({"check": "alerts trace a full path to the symptom", "passed": chain >= 0.7,
                   "detail": "silent cause under a fully alerting path" if chain == 0.6 else f"alert chain {chain}"})
    if groups:
        # Every connected group of alerts (WCC) needs an owner: the leader's own group, or an independent fault.
        covered = set(explained)
        for o in ranking.get("other_faults") or []:
            covered |= set(o["explains"])
        orphans = [g for g in groups if not g["has_symptom"] and not covered & set(g["services"])]
        checks.append({"check": "every separate group of alerts (WCC) is accounted for", "passed": not orphans,
                       "detail": f"{len(groups)} group(s)" + (f"; nothing explains {', '.join(g['first_to_alert'] for g in orphans)}" if orphans else "")})
    runner = next((o for o in others if o is not lead), None)
    gap = round(lead["score"] - runner["score"], 3) if runner else lead["score"]
    checks.append({"check": "clearly ahead of the runner-up", "passed": gap >= 0.1,
                   "detail": f"{gap} ahead of {_label(runner)}" if runner else "no runner-up"})
    return checks, better


def _label(s: dict | None) -> str:
    if not s:
        return "nothing"
    if s.get("type") == "host":
        return f"{s['service']} (shared host)"
    return (s["change"] or {}).get("id") or f"{s['service']} (no change)"


def skeptic_review(ranking: dict, groups: list[dict] | None = None) -> dict:
    """Challenge the judge's leader: what doesn't it explain, did its alerts come in the right order, did the
    change land before the incident, is it clearly ahead? If another suspect explains more of the symptom's
    alerts and scores within reach, it takes over, and the new leader faces the same checks (two rounds at
    most). Deterministic: the same ranking always gets the same review."""
    sus = list(ranking["suspects"])
    if not sus:
        return {"verdict": "no suspect", "rounds": [], "leader": None, "overturned": False, "failed_checks": 0,
                "confidence_p": 0.0, "confidence": "none", "suspects": sus}
    lead, rounds, tried = sus[0], [], set()
    for _ in range(SKEPTIC_ROUNDS):
        tried.add(_label(lead))
        others = [o for o in sus if o is not lead]
        checks, better = _challenges(lead, ranking, others, groups)
        rounds.append({"leader": _label(lead), "score": lead["score"], "checks": checks,
                       "handed_to": _label(better) if better and _label(better) not in tried else None})
        if not better or _label(better) in tried:
            break
        lead = better
    failed = sum(not c["passed"] for c in rounds[-1]["checks"])
    overturned = lead is not sus[0]
    ordered = [lead] + [o for o in sus if o is not lead]
    runner = ordered[1]["score"] if len(ordered) > 1 else 0.0
    p, _ = confidence_from(lead["score"], lead["score"] - runner)
    p = round(p * (0.85 ** failed), 3)
    label = "high" if p >= 0.85 else "medium" if p >= 0.6 else "low"
    verdict = "overturned" if overturned else "upheld" if failed == 0 else "upheld with doubts"
    return {"verdict": verdict, "overturned": overturned, "leader": _label(lead), "rounds": rounds,
            "failed_checks": failed, "confidence_p": p, "confidence": label, "suspects": ordered}


def apply_review(ranking: dict, review: dict) -> dict:
    """The ranking as the skeptic left it: leader first, confidence from the review."""
    return {**ranking, "suspects": review["suspects"], "confidence": review["confidence"],
            "confidence_p": review["confidence_p"], "skeptic": {k: v for k, v in review.items() if k != "suspects"}}


def challenge_leader(g: Graph, incident_id: str) -> dict:
    """Tool form for agents: rank, split the alerts into connected groups (WCC), then review."""
    ranking = rank_suspects(g, incident_id)
    review = skeptic_review(ranking, alert_clusters(g, incident_id)["groups"])
    return {k: v for k, v in apply_review(ranking, review).items() if k in ("skeptic", "confidence", "confidence_p")} | {
        "leader": review["suspects"][0] if review["suspects"] else None}


# --- checking claims against the graph -----------------------------------------------------------

_ID = re.compile(r"\b(?:H-INC|INC|ACT|RB|[CDFAH])-[0-9A-Z]{1,8}\b")
_TIME = re.compile(r"\b(\d{2}):(\d{2}) UTC\b")


_SVC = r"([a-z][a-z0-9]*(?:-[a-z0-9]+)*)"


def _claim_context(g: Graph, ranking: dict, ids: list[str], services: set[str], start: int) -> dict:
    """What the graph says about the things a verdict might claim: which service each change touched, which
    suspects may be called a cause, which were ruled out, and when each service first alerted."""
    changed = {r["id"]: r["s"] for r in g.read(
        "MATCH (c:Change)-[:CHANGED]->(s:Service) WHERE c.id IN $ids RETURN c.id AS id, s.name AS s", {"ids": ids})}
    sus = ranking.get("suspects") or []
    lead = sus[0] if sus else None
    causes = {_label(lead)} if lead else set()
    if lead and not lead["change"]:
        causes.add(lead["service"])
    for o in ranking.get("other_faults") or []:
        causes.add((o["change"] or {}).get("id") or o["service"])
    scores = {_label(x): x["score"] for x in sus}
    ruled = {c["id"]: c["reason"] for c in ranking.get("ruled_out") or []}
    first = {}
    for a in alerts_near(g, sorted(services), start):
        first[a["service"]] = min(first.get(a["service"], a["at"]), a["at"])
    return {"changed": changed, "causes": causes, "lead": _label(lead) if lead else None, "scores": scores,
            "ruled": ruled, "first": first, "services": services}


def _meaning_problems(text: str, sem: dict) -> list[str]:
    """Claims must also be right, not just refer to real things: a change must have touched the service the
    claim pairs it with, only the leader or an independent fault may be called a cause, and "A alerted
    before B" must match the alert times."""
    out = []
    for cid, svc in re.findall(rf"\b([CDF]-\d+)\)?\s+(?:changed|on|to|touched|deployed to)\s+{_SVC}", text):
        real = sem["changed"].get(cid)
        if real and svc in sem["services"] and svc != real:
            out.append(f"{cid} changed {real}, not {svc}")
    causal = re.findall(r"\b([CDF]-\d+)\)?\s+(?:caused|is the (?:most likely |likely |root )?cause|is behind)\b", text) + \
        re.findall(r"\bcaused by ([CDF]-\d+)\b", text)
    for cid in causal:
        if cid in sem["causes"]:
            continue
        if cid in sem["ruled"]:
            out.append(f"{cid} cannot be the cause: {sem['ruled'][cid]}")
        elif cid in sem["scores"]:
            out.append(f"{cid} is not the cause: it scored {sem['scores'][cid]}, the leader is {sem['lead']}")
        elif cid in sem["changed"]:
            out.append(f"{cid} is not among the suspects")
    for a, rel, b in re.findall(rf"\b{_SVC} alerted (before|after) {_SVC}\b", text):
        ta, tb = sem["first"].get(a), sem["first"].get(b)
        if ta is None or tb is None:
            out.append(f"no alert on {a if ta is None else b} to compare")
        elif (rel == "before" and not ta < tb) or (rel == "after" and not ta > tb):
            clock = lambda x: time.strftime("%H:%M UTC", time.gmtime(x))  # noqa: E731
            out.append(f"{a} first alerted at {clock(ta)}, {b} at {clock(tb)}: {a} did not alert {rel} {b}")
    for a in re.findall(rf"\b{_SVC} alerted first\b", text):
        if sem["first"] and a in sem["first"] and sem["first"][a] > min(sem["first"].values()):
            earliest = min(sem["first"], key=sem["first"].get)
            out.append(f"{earliest} alerted before {a}")
    return out


def verify_claims(g: Graph, incident_id: str, findings: dict, ranking: dict) -> dict:
    """Every id, service and clock time a verdict cites must exist in the graph, and the verdict itself must
    hang together: the blamed change exists, changed the named service, and is one of the judge's suspects.
    Claims that fail are reported (and dropped from the evidence by the caller)."""
    items = [("summary", findings.get("summary") or "")] + [("evidence", e) for e in findings.get("evidence") or []]
    ids = sorted({m for _, t in items for m in _ID.findall(t)} | ({findings["change_id"]} if findings.get("change_id") else set()))
    known = {r["id"]: r for r in g.read("""
        MATCH (n) WHERE n.id IN $ids
        RETURN n.id AS id, labels(n)[0] AS label, n.at AS at""", {"ids": ids})} if ids else {}
    services = {r["s"] for r in g.read("MATCH (s:Service) RETURN s.name AS s")}
    hosts = {r["s"] for r in g.read("MATCH (n:Node) RETURN n.name AS s")}
    start = get_incident(g, incident_id)["started"]
    times = {r["at"] for r in g.read("""
        MATCH (n) WHERE (n:Alert OR n:Change) AND n.at >= $a AND n.at <= $b RETURN n.at AS at""",
                                     {"a": start - 3 * HOUR_S, "b": start + HOUR_S})}
    clock = {(t // 60) % 1440 for t in times}
    sem = _claim_context(g, ranking, ids, services, start)

    claims = []
    for kind, text in items:
        problems = [f"{i} is not in the graph" for i in _ID.findall(text) if i not in known]
        for hh, mm in _TIME.findall(text):
            minute = int(hh) * 60 + int(mm)
            if not any(abs(minute - c) <= 1 for c in clock):
                problems.append(f"nothing in the graph happened at {hh}:{mm} UTC")
        problems += _meaning_problems(text, sem)
        named = {w for w in re.findall(r"[a-z][a-z0-9-]+", text) if w in services}
        claims.append({"kind": kind, "text": text, "ok": not problems, "problems": problems, "services": sorted(named)})

    verdict = []
    cid, svc = findings.get("change_id"), findings.get("root_cause_service")
    if cid:
        hit = g.read("MATCH (c:Change {id: $id})-[:CHANGED]->(s:Service) RETURN s.name AS s", {"id": cid})
        if not hit:
            verdict.append(f"{cid} is not a change in the graph")
        elif svc and hit[0]["s"] != svc:
            verdict.append(f"{cid} changed {hit[0]['s']}, not {svc}")
    if svc and svc not in services | hosts:
        verdict.append(f"{svc} is not a service or a node")
    top3 = [(_label(s), s["service"]) for s in ranking["suspects"][:3]]
    if svc and not any((cid and lab == cid) or (not cid and s == svc) for lab, s in top3):
        verdict.append("the blamed cause is not among the judge's top three suspects")
    ok = [c for c in claims if c["ok"]]
    return {"total": len(claims), "supported": len(ok), "claims": claims, "verdict_problems": verdict,
            "verdict_consistent": not verdict}


# --- graph algorithms as agent tools -------------------------------------------------------------

def single_points_of_failure(g: Graph, limit: int = 8) -> list[dict]:
    """Betweenness over CALLS: the services the most call paths run through. A fault there cuts the most
    traffic, so it changes severity and who needs to know. FalkorDB samples source nodes by default, so the
    sample covers every node and the seed is fixed: the same graph always gives the same ranking."""
    rows = g.read("""
        CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS'],
                               samplingSize: 10000, samplingSeed: 1}) YIELD node, score
        RETURN node.name AS service, score ORDER BY score DESC, service""")
    top = rows[0]["score"] if rows and rows[0]["score"] else 1.0
    out = []
    for i, r in enumerate(rows, 1):
        out.append({"service": r["service"], "rank": i, "betweenness": round(r["score"], 2),
                    "share": round(r["score"] / top, 3)})
    return out[:limit]


def failure_domains(g: Graph) -> list[dict]:
    """Label propagation over CALLS: groups of services that talk mostly to each other. A fault tends to stay
    inside its domain; alerts in two domains at once point to two problems or a fault on the boundary."""
    rows = g.read("""
        CALL algo.labelPropagation({nodeLabels: ['Service'], relationshipTypes: ['CALLS']}) YIELD node, communityId
        MATCH (node)-[:OWNED_BY]->(t:Team)
        RETURN communityId AS domain, collect(node.name) AS services, collect(DISTINCT t.name) AS teams
        ORDER BY size(services) DESC""")
    return [{"domain": i + 1, "services": sorted(r["services"]), "teams": sorted(r["teams"])}
            for i, r in enumerate(rows)]


def failure_paths(g: Graph, source: str, target: str, k: int = 3) -> list[dict]:
    """algo.SPpaths: the k shortest call paths from `source` down to `target`."""
    if source == target:
        return [{"path": [source], "hops": 0}]
    rows = g.read("""
        MATCH (a:Service {name: $a}), (b:Service {name: $b})
        CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: ['CALLS'], maxLen: $max, pathCount: $k})
        YIELD path, pathWeight
        RETURN [n IN nodes(path) | n.name] AS path, pathWeight AS hops
        ORDER BY hops""", {"a": source, "b": target, "k": max(1, min(int(k), 10)), "max": MAX_HOPS})
    return [{"path": r["path"], "hops": int(r["hops"])} for r in rows]


def alert_clusters(g: Graph, incident_id: str) -> dict:
    """WCC over the alerting part of the incident: the services alerting below the symptom (and the symptom),
    with the calls between them, are copied into a scratch graph and split into weakly connected components.
    Each component is a group of alerts joined by calls; separate components are separate problems."""
    inc = get_incident(g, incident_id)
    sym, start = inc["symptom"], inc["started"]
    area = [sym] + [d["service"] for d in dependencies(g, sym)]
    alerts = alerts_near(g, area, start)
    alerting = sorted({a["service"] for a in alerts} | {sym})
    edges = g.read("""
        MATCH (a:Service)-[:CALLS]->(b:Service) WHERE a.name IN $s AND b.name IN $s
        RETURN a.name AS a, b.name AS b""", {"s": alerting})
    scratch = Graph("scratch_" + uuid.uuid4().hex[:10], g.trace)
    try:
        scratch.write("UNWIND $s AS name CREATE (:Service {name: name})", {"s": alerting})
        if edges:
            scratch.write("""
                UNWIND $e AS e MATCH (a:Service {name: e.a}), (b:Service {name: e.b}) CREATE (a)-[:CALLS]->(b)""",
                          {"e": edges})
        rows = scratch.read("""
            CALL algo.WCC({nodeLabels: ['Service'], relationshipTypes: ['CALLS']}) YIELD node, componentId
            RETURN componentId, collect(node.name) AS services""")
    finally:
        scratch.drop()
    first = {}
    for a in alerts:
        first[a["service"]] = min(first.get(a["service"], a["at"]), a["at"])
    groups = []
    for r in rows:
        svcs = sorted(r["services"])
        alerting_svcs = [s for s in svcs if s in first]
        if not alerting_svcs:
            continue
        origin = min(alerting_svcs, key=lambda s: (first[s], s))
        groups.append({"services": svcs, "first_alert": first[origin], "first_to_alert": origin,
                       "has_symptom": sym in svcs})
    groups.sort(key=lambda x: (not x["has_symptom"], x["first_alert"]))
    return {"incident": incident_id, "symptom": sym, "groups": groups, "count": len(groups)}


# --- verifying a fix --------------------------------------------------------------------------------

def verify_fix(g: Graph, action_id: str) -> dict:
    """Did the approved fix work? Read only what monitoring reports: for every incident the fix covers, take the
    alerts in that incident's alert cone (the alerting services joined to its symptom through other alerting
    services) and check each one cleared after the fix. All cleared: verified. Any still firing: the fix did
    not work, so the incident goes back to open and the action is marked as not verified."""
    act = g.read("""
        MATCH (a:Action {id: $id})
        RETURN a.status AS status, a.decided_at AS decided_at, a.kind AS kind""", {"id": action_id})
    if not act:
        raise KeyError(f"no action {action_id}")
    if act[0]["status"] != "executed":
        raise ValueError(f"{action_id} is {act[0]['status']}, not executed")
    fixed_at = act[0]["decided_at"]
    covers = [r["id"] for r in g.read("MATCH (:Action {id: $id})-[:FOR]->(i:Incident) RETURN i.id AS id ORDER BY id",
                                      {"id": action_id})]
    results = []
    for inc_id in covers:
        inc = get_incident(g, inc_id)
        sym, start = inc["symptom"], inc["started"]
        area = [sym] + [d["service"] for d in dependencies(g, sym)]
        alerts = [a for a in alerts_near(g, area, start) if a["at"] <= fixed_at]
        cone = _alert_cone(g, sym, set(area), alerts)
        watched = [a for a in alerts if a["service"] in cone]
        cleared = sorted({a["service"] for a in watched if a["cleared_at"] is not None and a["cleared_at"] >= fixed_at})
        firing = sorted({a["service"] for a in watched if a["cleared_at"] is None})
        verdict = "verified" if watched and not firing else "not verified" if firing else "nothing to check"
        results.append({"incident": inc_id, "verdict": verdict, "cleared": cleared, "still_firing": firing})
    ok = bool(results) and all(r["verdict"] == "verified" for r in results)
    summary = "; ".join(
        f"{r['incident']}: " + ("all alerts cleared (" + ", ".join(r["cleared"]) + ")" if r["verdict"] == "verified"
                                else "still firing: " + ", ".join(r["still_firing"]) if r["still_firing"]
                                else "no alerts to check")
        for r in results)
    g.write("MATCH (a:Action {id: $id}) SET a.verified = $ok, a.verification = $summary",
            {"id": action_id, "ok": ok, "summary": summary})
    for r in results:
        if r["verdict"] == "verified":
            g.write("MATCH (i:Incident {id: $id}) WHERE i.status IN ['open', 'mitigated'] SET i.status = 'mitigated'",
                    {"id": r["incident"]})
        elif r["verdict"] == "not verified":
            g.write("MATCH (i:Incident {id: $id}) WHERE i.status = 'mitigated' SET i.status = 'open'", {"id": r["incident"]})
    return {"action": action_id, "verified": ok, "summary": summary, "incidents": results}


# --- who knows a service ---------------------------------------------------------------------------

def experts(g: Graph, service: str, limit: int = 5) -> list[dict]:
    """Who actually knows this service, from what they did rather than the org chart: fixes they approved for
    incidents whose root cause was on it (3 points each), changes they made to it (1 each), plus a point for
    being in the owning team. Each person comes with the evidence behind their score."""
    if not g.read("MATCH (s:Service {name: $svc}) RETURN s.name AS s", {"svc": service}):
        raise KeyError(f"no service {service}")
    fixes = g.read("""
        MATCH (p:Person)-[:APPROVED]->(a:Action)<-[:RESOLVED_BY]-(i:Incident)-[:ROOT_CAUSE]->(:Change)-[:CHANGED]->(:Service {name: $svc})
        RETURN p.handle AS handle, collect(DISTINCT i.id) AS incidents""", {"svc": service})
    made = g.read("""
        MATCH (p:Person)-[:MADE]->(c:Change)-[:CHANGED]->(:Service {name: $svc})
        RETURN p.handle AS handle, collect(DISTINCT c.id) AS changes""", {"svc": service})
    team = g.read("""
        MATCH (:Service {name: $svc})-[:OWNED_BY]->(:Team)<-[:MEMBER_OF]-(p:Person)
        RETURN p.handle AS handle, p.on_call AS on_call""", {"svc": service})
    commits = g.read("""
        MATCH (p:Person)-[c:COMMITTED]->(:Service {name: $svc})
        RETURN p.handle AS handle, c.count AS n, c.last AS last""", {"svc": service})
    names = {r["h"]: r["n"] for r in g.read("MATCH (p:Person) RETURN p.handle AS h, p.name AS n")}
    people: dict[str, dict] = {}

    def person(h):
        return people.setdefault(h, {"handle": h, "name": names.get(h, h), "score": 0, "fixed": [], "changed": [],
                                     "owner_team": False, "on_call": False, "commits": 0, "last_commit": None})
    for r in fixes:
        p = person(r["handle"]); p["fixed"] = sorted(r["incidents"]); p["score"] += 3 * len(r["incidents"])
    for r in made:
        p = person(r["handle"]); p["changed"] = sorted(r["changes"]); p["score"] += len(r["changes"])
    for r in team:
        p = person(r["handle"]); p["owner_team"] = True; p["on_call"] = bool(r["on_call"]); p["score"] += 1
    for r in commits:   # commits to the service's code: a point per 3, at most 4, so history counts but can't swamp
        p = person(r["handle"]); p["commits"] = r["n"]; p["last_commit"] = r["last"]; p["score"] += min(4, 1 + r["n"] // 3)
    return sorted(people.values(), key=lambda p: (-p["score"], p["handle"]))[:limit]


# --- full-text memory search -------------------------------------------------------------------------

def search_memory(g: Graph, query: str, limit: int = 8) -> list[dict]:
    """Full-text search (FalkorDB's full-text indexes) over past lessons, changes, alerts and incident titles.
    Any of the words may match; results come with what they belong to."""
    words = [w for w in re.findall(r"[A-Za-z0-9_]+", query) if len(w) > 2][:12]
    if not words:
        return []
    q = "|".join(words)
    rows = []
    for label, prop, back in [
        ("Lesson", "text", "MATCH (i:Incident)-[:TAUGHT]->(node) RETURN 'lesson' AS kind, i.id AS ref, node.text AS text, score"),
        ("Change", "summary", "OPTIONAL MATCH (node)-[:CHANGED]->(s:Service) RETURN 'change' AS kind, node.id AS ref, node.summary AS text, score, s.name AS service"),
        ("Alert", "signal", "OPTIONAL MATCH (node)-[:FIRED_ON]->(s:Service) RETURN 'alert' AS kind, node.id AS ref, node.signal AS text, score, s.name AS service"),
        ("Incident", "title", "RETURN 'incident' AS kind, node.id AS ref, node.title AS text, score"),
    ]:
        rows += g.read(f"""
            CALL db.idx.fulltext.queryNodes('{label}', $q) YIELD node, score
            WITH node, score
            {back}""", {"q": q})
    rows.sort(key=lambda r: -r["score"])
    return [{**r, "score": round(r["score"], 3)} for r in rows[:limit]]


# --- matching past incidents by shape ----------------------------------------------------------------

_KINDS = ["deploy", "config", "flag", "infra", "external"]
_SVC_KINDS = ["service", "datastore", "external"]


def _signature(kind: str, svc_kind: str, hops: int, sym_tier: int, sym_exposed: bool, cause_tier: int,
               risk: str | None) -> list[float]:
    """A fixed 18-number description of what an incident looked like: the kind of cause, what kind of thing
    broke, how far below the symptom, how important the symptom and the broken service are, how risky the
    change was. Two incidents with the same story have nearly the same vector, whatever the service names."""
    v = [0.0] * 18
    v[_KINDS.index(kind) if kind in _KINDS else 3] = 1.0
    v[5 + (_SVC_KINDS.index(svc_kind) if svc_kind in _SVC_KINDS else 0)] = 1.0
    v[8 + min(max(hops, 0), 3)] = 1.0
    v[12 + min(max(sym_tier, 1), 3) - 1] = 1.0
    v[15] = 1.0 if sym_exposed else 0.0
    v[16] = 1.0 if cause_tier == 1 else 0.0
    v[17] = {"high": 1.0, "medium": 0.5, "low": 0.2}.get(risk or "", 1.0 if kind in ("infra", "external") else 0.5)
    return v


def _service_info(g: Graph, names: list[str]) -> dict[str, dict]:
    return {r["name"]: r for r in g.read("""
        MATCH (s:Service) WHERE s.name IN $n
        RETURN s.name AS name, s.kind AS kind, s.tier AS tier, s.internet_exposed AS exposed""", {"n": names})}


def incident_signature(g: Graph, incident_id: str) -> list[float] | None:
    """The signature of a resolved incident, from its symptom and its recorded root cause."""
    rows = g.read("""
        MATCH (i:Incident {id: $id})-[:SYMPTOM_ON]->(sym:Service), (i)-[:ROOT_CAUSE]->(c:Change)-[:CHANGED]->(cs:Service)
        RETURN sym.name AS sym, cs.name AS cause, c.kind AS kind, c.risk AS risk LIMIT 1""", {"id": incident_id})
    if not rows:
        return None
    r = rows[0]
    info = _service_info(g, [r["sym"], r["cause"]])
    path = paths_between(g, r["sym"], r["cause"])
    hops = len(path[0]) - 1 if path else 3
    s, c = info.get(r["sym"], {}), info.get(r["cause"], {})
    return _signature(r["kind"], c.get("kind"), hops, s.get("tier") or 2, bool(s.get("exposed")), c.get("tier") or 2, r["risk"])


def store_signature(g: Graph, incident_id: str) -> bool:
    sig = incident_signature(g, incident_id)
    if sig is None:
        return False
    g.write("MATCH (i:Incident {id: $id}) SET i.signature = vecf32($v)", {"id": incident_id, "v": sig})
    return True


def similar_by_shape(g: Graph, signature: list[float], k: int = 4, exclude: str | None = None) -> list[dict]:
    """Resolved incidents whose story looks like this one (FalkorDB vector index, cosine), most similar first."""
    rows = g.read("""
        CALL db.idx.vector.queryNodes('Incident', 'signature', $k, vecf32($v)) YIELD node, score
        WHERE node.status = 'resolved' AND ($ex IS NULL OR node.id <> $ex)
        OPTIONAL MATCH (node)-[:ROOT_CAUSE]->(c:Change)-[:CHANGED]->(s:Service)
        OPTIONAL MATCH (node)-[:TAUGHT]->(l:Lesson)
        RETURN node.id AS incident, node.title AS title, 1 - score AS similarity, c.kind AS cause_kind,
               s.name AS cause_service, collect(l.text)[0] AS lesson
        ORDER BY similarity DESC""", {"k": k + 1, "v": signature, "ex": exclude})
    return [{**r, "similarity": round(r["similarity"], 3)} for r in rows[:k]]


def suspect_signature(g: Graph, incident: dict, suspect: dict) -> list[float]:
    info = _service_info(g, [incident["symptom"], suspect["service"]])
    s, c = info.get(incident["symptom"], {}), info.get(suspect["service"], {})
    kind = suspect["change"]["kind"] if suspect["change"] else "infra"
    risk = suspect["change"]["risk"] if suspect["change"] else None
    return _signature(kind, c.get("kind"), suspect["hops"], s.get("tier") or 2, bool(s.get("exposed")),
                      c.get("tier") or 2, risk)


def shape_matches(g: Graph, incident_id: str, k: int = 4) -> dict:
    """Past incidents shaped like this one's leading suspect (vector index over incident signatures)."""
    r = rank_suspects(g, incident_id)
    if not r["suspects"]:
        return {"incident": incident_id, "leader": None, "matches": []}
    lead = r["suspects"][0]
    return {"incident": incident_id, "leader": _label(lead),
            "matches": similar_by_shape(g, suspect_signature(g, r["incident"], lead), k=k, exclude=incident_id)}


# --- blast radius, per affected service ----------------------------------------------------------------

SLO_WINDOW_DAYS = 30


def journeys_hit(g: Graph, services: list[str], started: int | None = None, until: int | None = None) -> list[dict]:
    """The user journeys that need any of `services`, busiest first; for a journey with an SLO, how much of its
    30-day error budget this incident has used so far (minutes the journey was failing over the minutes it may)."""
    rows = g.read("""
        MATCH (j:Journey)-[:USES]->(s:Service) WHERE s.name IN $svcs
        WITH j, collect(DISTINCT s.name) AS hit
        MATCH (j)-[:USES]->(all:Service)
        RETURN j.name AS journey, j.slo AS slo, j.rps AS rps, j.source AS source, hit, count(DISTINCT all) AS uses
        ORDER BY j.rps DESC""", {"svcs": services})
    for r in rows:
        r["hit"] = sorted(r["hit"])
        if r["slo"] and started:
            budget = (1 - r["slo"]) * SLO_WINDOW_DAYS * 24 * 60
            burned = max(0.0, ((until or now()) - started) / 60)
            r["budget_min"] = round(budget, 1)
            r["burned_min"] = round(burned, 1)
            r["burned_share"] = round(burned / budget, 3) if budget else None
    return rows


def on_call_at(g: Graph, team: str, at: int) -> dict | None:
    """Who was on call for a team at a given time, from the shift graph (not who is on call now)."""
    rows = g.read("""
        MATCH (p:Person)-[:ON_SHIFT]->(s:OnCallShift)-[:COVERS]->(t:Team {name: $team})
        WHERE s.from <= $at AND $at < s.to
        RETURN p.handle AS handle, p.name AS name, s.from AS from, s.to AS to ORDER BY s.from DESC LIMIT 1""",
                  {"team": team, "at": at})
    return rows[0] if rows else None


def _cause_services(g: Graph, incident: dict) -> list[str]:
    """Where an incident's failure was: the root cause it was closed with, or for an open one the judge's
    current leader (a shared host stands for the services it runs)."""
    if incident["status"] == "resolved":
        rows = g.read("""MATCH (:Incident {id: $id})-[:ROOT_CAUSE]->(:Change)-[:CHANGED]->(s:Service)
                         RETURN s.name AS s""", {"id": incident["id"]})
        return [r["s"] for r in rows]
    sus = rank_suspects(g, incident["id"], limit=1)["suspects"]
    if not sus:
        return []
    return sus[0]["host"]["services"] if sus[0]["type"] == "host" else [sus[0]["service"]]


def slo_status(g: Graph) -> list[dict]:
    """Each journey with an SLO, its 30-day error budget, and what the incidents that broke it used. An incident
    counts against a journey when the journey needs the service the failure was in (not merely the service
    where it was noticed), for as long as it lasted: an upper bound, since not every request failed."""
    journeys = g.read("""
        MATCH (j:Journey)-[:USES]->(s:Service) WHERE j.slo IS NOT NULL
        RETURN j.name AS journey, j.slo AS slo, j.rps AS rps, collect(s.name) AS uses ORDER BY j.rps DESC""")
    if not journeys:
        return []
    since = now() - SLO_WINDOW_DAYS * 86400
    incs = g.read("""MATCH (i:Incident) WHERE i.started >= $since
                     RETURN i.id AS id, i.status AS status, i.started AS started, i.resolved AS resolved""", {"since": since})
    causes = {i["id"]: set(_cause_services(g, i)) for i in incs}
    out = []
    for j in journeys:
        hit = [i for i in incs if causes[i["id"]] & set(j["uses"])]
        budget = (1 - j["slo"]) * SLO_WINDOW_DAYS * 24 * 60
        used = sum(((i["resolved"] or now()) - i["started"]) / 60 for i in hit)
        out.append({"journey": j["journey"], "slo": j["slo"], "rps": j["rps"], "budget_min": round(budget, 1),
                    "used_min": round(used, 1), "remaining_share": round(max(0.0, 1 - used / budget), 3),
                    "incidents": [{"id": i["id"], "status": i["status"]} for i in sorted(hit, key=lambda x: x["started"])]})
    return out


def impact(g: Graph, service: str, incident_id: str | None = None, limit: int = 6) -> dict:
    """Who a failure of `service` reaches, one block per affected caller: the call path that carries the failure
    up to it, whether it faces the internet, who owns it and who is on call, and whether it is alerting now."""
    rows = g.read(f"""
        MATCH p = (u:Service)-[:CALLS*1..{MAX_HOPS}]->(s:Service {{name: $svc}})
        WITH u, p ORDER BY length(p)
        WITH u, collect(p)[0] AS p
        MATCH (u)-[:OWNED_BY]->(t:Team)
        OPTIONAL MATCH (oc:Person {{on_call: true}})-[:MEMBER_OF]->(t)
        RETURN u.name AS service, length(p) AS hops, [n IN nodes(p) | n.name] AS path,
               u.internet_exposed AS exposed, u.tier AS tier, t.name AS team, collect(DISTINCT oc.name)[0] AS on_call,
               size([(x:Service)-[:CALLS]->(u) | x]) AS callers
        ORDER BY exposed DESC, hops""", {"svc": service})
    alerting = set()
    started = resolved = None
    if incident_id:
        inc = get_incident(g, incident_id)
        started = inc["started"]
        resolved = (g.read("MATCH (i:Incident {id: $id}) RETURN i.resolved AS r", {"id": incident_id}) or [{}])[0].get("r")
        alerting = {a["service"] for a in alerts_near(g, [r["service"] for r in rows], inc["started"])}
        for r in rows:
            then = on_call_at(g, r["team"], started)
            r["on_call_then"] = then["name"] if then else None
    blocks = [{**r, "path": list(reversed(r["path"])), "alerting": r["service"] in alerting} for r in rows]
    return {"service": service, "affected": len(rows), "exposed": sum(1 for r in rows if r["exposed"]),
            "blocks": blocks[:limit], "journeys": journeys_hit(g, [service], started, resolved)}


def company(g: Graph) -> dict:
    """The organisation as the graph sees it: teams, what they own, who is on call, who knows what, and the
    services everything depends on."""
    teams = g.read("""
        MATCH (t:Team)
        OPTIONAL MATCH (s:Service)-[:OWNED_BY]->(t)
        OPTIONAL MATCH (p:Person)-[:MEMBER_OF]->(t)
        RETURN t.name AS team, collect(DISTINCT s.name) AS services,
               collect(DISTINCT CASE WHEN p.on_call THEN p.name END) AS on_call, count(DISTINCT p) AS people
        ORDER BY size(services) DESC""")
    for t in teams:
        t["services"] = sorted(t["services"])
        t["on_call"] = [x for x in t["on_call"] if x]
    return {"teams": teams, "chokepoints": single_points_of_failure(g, 6), "domains": failure_domains(g),
            "critical": critical_services(g, 6), "delivery": delivery_metrics(g), "slo": slo_status(g)}


def timeline(g: Graph, incident_id: str) -> dict:
    """Every alert and change in the incident's window, across all services, in time order. Alerts from real
    telemetry (the Boutique tenant) carry the z-score trace of the metric that fired."""
    inc = get_incident(g, incident_id)
    alerts = g.read("""
        MATCH (a:Alert)-[:FIRED_ON]->(s:Service)
        WHERE a.at >= $from AND a.at <= $to
        RETURN a.id AS id, s.name AS service, a.at AS at, a.signal AS signal, a.z AS z, a.series AS series,
               a.offset AS offset, a.cleared_at AS cleared_at
        ORDER BY a.at""", {"from": inc["started"] - WINDOW_MIN * 60, "to": inc["started"] + 30 * 60})
    changes = all_changes_near(g, inc["started"])
    return {"incident": incident_id, "started": inc["started"], "alerts": alerts, "changes": changes}


def endpoints(g: Graph, service: str, limit: int = 12) -> list[dict]:
    """The operations a service serves (from the traces), busiest first, each with what it calls downstream."""
    return g.read("""
        MATCH (:Service {name: $svc})-[:EXPOSES]->(o:Operation)
        OPTIONAL MATCH (o)-[c:CALLS]->(d:Operation)
        WITH o, c, d ORDER BY c.rps DESC
        RETURN o.id AS id, o.name AS op, o.rps AS rps, o.p50_ms AS p50_ms, o.p99_ms AS p99_ms, o.err AS err,
               collect(CASE WHEN d IS NULL THEN NULL ELSE {service: d.service, op: d.name, rps: c.rps, p99_ms: c.p99_ms} END)[..6] AS calls
        ORDER BY o.rps DESC LIMIT $n""", {"svc": service, "n": limit})


_WORD = re.compile(r"[a-z][a-z0-9]{3,}")
COMMON = {"with", "from", "into", "that", "this", "your", "when", "test", "tests", "docs", "bump", "main"}


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall((text or "").lower()) if w not in COMMON}


def commits_in(g: Graph, change_id: str, incident_id: str | None = None) -> dict:
    """Which commit inside a deploy most likely did it. Each commit the deploy shipped is ranked by how much
    request-path code it changed (docs, tests and dependency files don't count), and by how many words it
    shares with what the incident shows (the deploy's own summary, the alerts, new error logs). A heuristic,
    and labelled as one: it orders the review, it doesn't convict."""
    rows = g.read("""
        MATCH (ch:Change {id: $id})-[:SHIPS]->(c:Commit)
        RETURN c.sha AS sha, c.message AS message, c.files AS files, c.additions AS additions,
               c.deletions AS deletions, c.code_lines AS code_lines, c.author AS author, c.at AS at,
               ch.summary AS summary ORDER BY c.at""", {"id": change_id})
    if not rows:
        return {"change": change_id, "commits": [], "best": None}
    context = _words(rows[0]["summary"])
    if incident_id:
        inc = get_incident(g, incident_id)
        sym = inc["symptom"]
        area = [sym] + [d["service"] for d in dependencies(g, sym)]
        for a in alerts_near(g, area, inc["started"]):
            context |= _words(a["signal"])
        for lp in g.read("MATCH (l:LogPattern)-[:SEEN_IN]->(:Incident {id: $i}) RETURN l.template AS t", {"i": incident_id}):
            context |= _words(lp["t"])
    top_code = max((r["code_lines"] or 0) for r in rows) or 1
    for r in rows:
        words = _words(r["message"] + " " + " ".join(r["files"] or []))
        r["shared_words"] = sorted(words & context)
        r["code_share"] = round((r["code_lines"] or 0) / top_code, 3)
        r["overlap"] = round(len(words & context) / max(1, min(len(words), len(context))), 3)
        r["suspicion"] = round(0.6 * r["code_share"] + 0.4 * r["overlap"], 3)
        del r["summary"]
    rows.sort(key=lambda r: (-r["suspicion"], r["at"]))
    return {"change": change_id, "commits": rows, "best": rows[0]}


def delivery_metrics(g: Graph, days: int = 14, history_days: int = 90) -> list[dict]:
    """Delivery metrics per team, all from the graph: deploys per week (last `days`), change failure rate (of the
    deploys in the last `history_days`, those a resolved incident names as its root cause), time to restore
    (resolved incidents on the team's services, same window) and lead time (first commit to deploy, where
    commits are in the graph)."""
    since, long_since = now() - days * 86400, now() - history_days * 86400
    rows = g.read("""
        MATCH (t:Team)<-[:OWNED_BY]-(s:Service)
        OPTIONAL MATCH (d:Change {kind: 'deploy'})-[:CHANGED]->(s) WHERE d.at >= $long
        WITH t, collect(DISTINCT d) AS all_deploys
        WITH t, all_deploys, [d IN all_deploys WHERE d.at >= $since] AS deploys
        OPTIONAL MATCH (i:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(bad:Change {kind: 'deploy'}) WHERE bad IN all_deploys
        WITH t, deploys, size(all_deploys) AS n_long, count(DISTINCT bad) AS failed
        OPTIONAL MATCH (r:Incident {status: 'resolved'})-[:ROOT_CAUSE]->(:Change)-[:CHANGED]->(:Service)-[:OWNED_BY]->(t)
        WHERE r.started >= $long
        WITH t, deploys, n_long, failed, collect(DISTINCT r) AS restored
        UNWIND (CASE WHEN size(deploys) = 0 THEN [null] ELSE deploys END) AS d
        OPTIONAL MATCH (d)-[:SHIPS]->(c:Commit)
        WITH t, deploys, n_long, failed, restored, d, min(c.at) AS first_commit
        WITH t, size(deploys) AS n, n_long, failed, restored,
             avg(CASE WHEN first_commit IS NULL THEN null ELSE d.at - first_commit END) AS lead_s
        RETURN t.name AS team, n AS deploys, n_long AS deploys_90d, failed,
               [x IN restored | x.resolved - x.started] AS restore_s, lead_s
        ORDER BY team""", {"since": since, "long": long_since})
    out = []
    for r in rows:
        rs = [x for x in r.pop("restore_s") if x is not None]
        out.append({**r, "per_week": round(r["deploys"] * 7 / days, 1),
                    "change_failure_rate": round(r["failed"] / r["deploys_90d"], 3) if r["deploys_90d"] else None,
                    "restore_min": round(sum(rs) / len(rs) / 60, 1) if rs else None,
                    "lead_min": round(r.pop("lead_s") / 60, 1) if r.get("lead_s") is not None else None})
    return out


def releases(g: Graph, service: str, limit: int = 6) -> list[dict]:
    """The releases that changed this service, newest first: how many files and lines, and the commits whose
    messages name it."""
    return g.read("""
        MATCH (v:Version)-[c:CHANGED]->(:Service {name: $svc})
        OPTIONAL MATCH (v)-[:INCLUDES]->(m:Commit)-[:MENTIONS]->(:Service {name: $svc})
        WITH v, c, [x IN collect(m {.sha, .message, .author, .bot}) WHERE x.sha IS NOT NULL][..4] AS commits
        RETURN v.tag AS tag, v.at AS at, c.files AS files, c.code_files AS code_files, c.additions AS additions,
               c.deletions AS deletions, commits
        ORDER BY v.at DESC LIMIT $n""", {"svc": service, "n": limit})


def runtime(g: Graph, service: str) -> dict | None:
    """How a service runs: its Deployment (replicas, CPU and memory requests and limits, probe), the image it
    runs, the config it reads, and where its pods are scheduled, from the manifests (or the synthetic cluster)."""
    rows = g.read("""
        MATCH (:Service {name: $svc})-[:DEPLOYED_AS]->(d:Deployment)
        OPTIONAL MATCH (d)-[:RUNS_IMAGE]->(i:Image)
        OPTIONAL MATCH (d)-[r:READS]->(k:ConfigKey)
        WITH d, i, collect(DISTINCT {key: k.name, value: r.value}) AS keys
        OPTIONAL MATCH (d)-[:USES_CONFIG]->(cm:ConfigMap)
        OPTIONAL MATCH (d)-[:USES_SECRET]->(sec:Secret)
        WITH d, i, keys, collect(DISTINCT cm.name) AS config_maps, collect(DISTINCT sec.name) AS secrets
        OPTIONAL MATCH (d)-[:HAS_POD]->(p:Pod)-[:ON_NODE]->(n:Node)
        RETURN d.name AS deployment, d.replicas AS replicas, d.cpu_request AS cpu_request, d.cpu_limit AS cpu_limit,
               d.mem_request_mi AS mem_request_mi, d.mem_limit_mi AS mem_limit_mi, d.probe AS probe, d.source AS source,
               i.id AS image, [x IN keys WHERE x.key IS NOT NULL] AS config, config_maps, secrets,
               collect(DISTINCT {pod: p.name, node: n.name, zone: n.zone}) AS pods""", {"svc": service})
    if not rows:
        return None
    r = rows[0]
    r["pods"] = [x for x in r["pods"] if x["pod"]]
    if r["pods"]:
        nodes = sorted({x["node"] for x in r["pods"]})
        r["neighbours"] = g.read("""
            MATCH (n:Node)<-[:ON_NODE]-(:Pod)<-[:HAS_POD]-(:Deployment)<-[:DEPLOYED_AS]-(s:Service)
            WHERE n.name IN $nodes AND s.name <> $svc
            RETURN n.name AS node, collect(DISTINCT s.name) AS services ORDER BY node""", {"nodes": nodes, "svc": service})
    return r


def log_patterns(g: Graph, service: str, limit: int = 8) -> list[dict]:
    """Error log patterns this service printed during incidents, the ones seen in the most incidents first."""
    return g.read("""
        MATCH (l:LogPattern)-[:LOGGED_BY]->(:Service {name: $svc}), (l)-[s:SEEN_IN]->(i:Incident)
        WITH l.template AS template, max(l.example) AS example, count(DISTINCT i) AS incidents, sum(s.count) AS lines,
             collect(DISTINCT i.id)[..5] AS ids, max(CASE WHEN l.new THEN 1 ELSE 0 END) AS new
        RETURN template, example, incidents, lines, ids, new = 1 AS new
        ORDER BY incidents DESC, lines DESC LIMIT $n""", {"svc": service, "n": limit})


def service_card(g: Graph, service: str) -> dict:
    """Everything the graph knows about one service: owners and on-call, who actually knows it, what it calls
    and who calls it, recent changes, past incidents rooted in it, runbooks, and where it sits in the
    PageRank, betweenness and community structure."""
    own = who_owns(g, service)
    br = blast_radius(g, service)
    chokes = {r["service"]: r for r in single_points_of_failure(g, 100)}
    domain = next((d for d in failure_domains(g) if service in d.get("services", [])), None)
    recent = g.read("""
        MATCH (c:Change)-[:CHANGED]->(:Service {name: $svc})
        WHERE c.kind IN ['deploy', 'config', 'flag']
        OPTIONAL MATCH (p:Person)-[:MADE]->(c)
        RETURN c.id AS id, c.kind AS kind, c.at AS at, c.summary AS summary, c.risk AS risk, p.name AS author
        ORDER BY c.at DESC LIMIT 6""", {"svc": service})
    open_ = g.read("""
        MATCH (a:Alert)-[:FIRED_ON]->(:Service {name: $svc})
        WHERE a.cleared_at IS NULL AND a.at >= $since
        RETURN count(a) AS n""", {"svc": service, "since": now() - 2 * HOUR_S})[0]["n"]
    return {
        "service": service, "team": own["team"], "people": own["people"], "on_call": own["on_call"],
        "experts": experts(g, service), "callers": br["callers"], "exposed_via": br["exposed_entry_points"],
        "calls": dependencies(g, service, 1), "pagerank": br["pagerank"], "rank": br["criticality_rank"],
        "betweenness": chokes.get(service), "domain": domain,
        "changes": recent, "history": similar_incidents(g, service), "runbooks": runbooks(g, service),
        "alerting_now": open_, "endpoints": endpoints(g, service), "log_patterns": log_patterns(g, service),
        "runtime": runtime(g, service), "releases": releases(g, service),
        "bot_commits": (g.read("MATCH (s:Service {name: $svc}) RETURN s.bot_commits AS n, s.bots AS b", {"svc": service})
                        or [{}])[0],
    }
