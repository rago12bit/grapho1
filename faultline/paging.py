"""Paging in the graph: page the owner of what broke, acknowledge, escalate when nobody answers, and standing
approvals that let a repeat fault be fixed without waking anyone.

    (:Page {level, status})-[:FOR]->(:Incident), (:Page)-[:TO]->(:Person), (:Page)-[:ESCALATED_TO]->(:Page)
    (:StandingApproval {kind, service, requires, uses_left, expires})-[:COVERS]->(:Service)
    (:Action)-[:APPROVED_UNDER]->(:StandingApproval), (:StandingDecision {fired, reasons})-[:ON]->(:Action)

Who is paged comes from the graph: level 1 is the on-call person of the team that owns the service the judge
blames (not the team that noticed), level 2 the next person on that team's rota, level 3 whoever knows the service
best by what they did. A page nobody acknowledges within ESCALATE_S goes to the next level. Every transition is a
conditional write, so two people acknowledging, or a person and the escalation timer, can't both win.
"""
from __future__ import annotations

import json
import os
import time
import uuid

from . import tools
from .db import Graph

ESCALATE_S = int(os.environ.get("FAULTLINE_ESCALATE_S", "300"))   # unacknowledged this long, it escalates
KINDS = ("rollback", "revert_config", "disable_flag", "page_owner")
MAX_USES, MAX_DAYS = 10, 90


def _now_wall() -> float:
    return time.time()


def _blamed(g: Graph, incident_id: str) -> tuple[str, str | None, dict | None]:
    """The service the judge blames, its owning team, and the leading suspect."""
    r = tools.rank_suspects(g, incident_id, limit=1)
    top = r["suspects"][0] if r["suspects"] else None
    svc = (top["path"][-1] if top["type"] == "host" else top["service"]) if top else r["incident"]["symptom"]
    try:
        team = tools.who_owns(g, svc)["team"]
    except KeyError:
        team = None
    return svc, team, top


def _chain(g: Graph, incident_id: str) -> list[dict]:
    """Who to page, in order: the owning team's on-call, the next person on its rota, then who knows the service."""
    svc, team, _ = _blamed(g, incident_id)
    at = tools.now()
    out: list[dict] = []

    def add(p: dict | None, why: str) -> None:
        if p and p.get("handle") and p["handle"] not in {x["handle"] for x in out}:
            out.append({"handle": p["handle"], "name": p.get("name") or p["handle"], "why": why})
    if team:
        add(tools.on_call_at(g, team, at), f"on call for {team}, which owns {svc}")
        shifts = g.read("""
            MATCH (p:Person)-[:ON_SHIFT]->(s:OnCallShift)-[:COVERS]->(:Team {name: $team}) WHERE s.from > $at
            RETURN p.handle AS handle, p.name AS name ORDER BY s.from LIMIT 3""", {"team": team, "at": at})
        for s in shifts:
            add(s, f"next on {team}'s rota")
        mates = g.read("""MATCH (p:Person)-[:MEMBER_OF]->(:Team {name: $team}) RETURN p.handle AS handle, p.name AS name
                          ORDER BY p.handle""", {"team": team})
        if len(out) < 2:
            for m in mates:
                add(m, f"on {team}")
                if len(out) >= 2:
                    break
    try:
        for e in tools.experts(g, svc, limit=5):
            if e["fixed"] or e["changed"]:
                add(e, f"knows {svc}: " + ", ".join(filter(None, [f"fixed {', '.join(e['fixed'])}" if e["fixed"] else "",
                                                                   f"made {', '.join(e['changed'][:2])}" if e["changed"] else ""])))
    except KeyError:
        pass
    return out[:3]


def _rows(g: Graph, incident_id: str | None = None) -> list[dict]:
    rows = g.read("""
        MATCH (pg:Page)-[:FOR]->(i:Incident) WHERE $inc IS NULL OR i.id = $inc
        OPTIONAL MATCH (pg)-[:TO]->(p:Person)
        RETURN pg.id AS id, i.id AS incident, pg.level AS level, pg.status AS status, pg.at AS at, pg.wall_at AS wall_at,
               pg.why AS why, pg.acked_by AS acked_by, pg.acked_wall AS acked_wall, pg.note AS note, p.handle AS handle,
               p.name AS name
        ORDER BY i.id, pg.level""", {"inc": incident_id})
    for r in rows:
        r["escalates_in"] = max(0, round(r["wall_at"] + ESCALATE_S - _now_wall())) if r["status"] == "paged" else None
    return rows


def pages(g: Graph) -> list[dict]:
    """Every incident's page chain, oldest level first, with how long until an unanswered page escalates."""
    out: dict[str, list[dict]] = {}
    for r in _rows(g):
        out.setdefault(r["incident"], []).append(r)
    return [{"incident": k, "chain": v, "current": v[-1]} for k, v in out.items()]


def _create(g: Graph, incident_id: str, level: int, person: dict | None, status: str, why: str,
            prev: str | None = None, note: str = "") -> dict:
    pid = "PG-" + uuid.uuid4().hex[:8].upper()
    g.write("""
        MATCH (i:Incident {id: $inc})
        CREATE (pg:Page {id: $id, level: $level, status: $status, why: $why, note: $note, at: $at, wall_at: $wall})-[:FOR]->(i)
        WITH pg
        OPTIONAL MATCH (p:Person {handle: $handle})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (pg)-[:TO]->(p))
        WITH pg
        OPTIONAL MATCH (prev:Page {id: $prev})
        FOREACH (_ IN CASE WHEN prev IS NULL THEN [] ELSE [1] END | CREATE (prev)-[:ESCALATED_TO]->(pg))""",
            {"inc": incident_id, "id": pid, "level": level, "status": status, "why": why, "note": note,
             "at": tools.clock(g, [incident_id]), "wall": _now_wall(), "handle": (person or {}).get("handle"), "prev": prev})
    return next(r for r in _rows(g, incident_id) if r["id"] == pid)


def page(g: Graph, incident_id: str) -> dict:
    """Page the first person in the chain. Paging an incident that already has a live page returns that page."""
    tools.get_incident(g, incident_id)
    rows = _rows(g, incident_id)
    live = [r for r in rows if r["status"] in ("paged", "acked", "notice")]
    if live:      # already paged, or fixed under a standing approval (a notice: nobody needs waking)
        return {**live[-1], "already_paged": True}
    chain = _chain(g, incident_id)
    if not chain:
        return _create(g, incident_id, 1, None, "unrouted", "nobody in the graph owns or knows the blamed service")
    return _create(g, incident_id, 1, chain[0], "paged", chain[0]["why"])


def acknowledge(g: Graph, page_id: str, who: str) -> dict:
    """Someone takes it. Only a page still waiting can be acknowledged, and only once."""
    won = g.write("""
        MATCH (pg:Page {id: $id}) WHERE pg.status = 'paged'
        SET pg.status = 'acked', pg.acked_by = $who, pg.acked_wall = $wall
        WITH pg
        OPTIONAL MATCH (p:Person {handle: $who})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | MERGE (p)-[:ACKED]->(pg))
        RETURN pg.id AS id""", {"id": page_id, "who": who[:60], "wall": _now_wall()})
    if not won:
        st = g.read("MATCH (pg:Page {id: $id}) RETURN pg.status AS s", {"id": page_id})
        if not st:
            raise KeyError(f"no page {page_id}")
        raise ValueError(f"{page_id} is {st[0]['s']}, not waiting for an acknowledgement")
    return next(r for r in _rows(g) if r["id"] == page_id)


def escalate(g: Graph, page_id: str, reason: str = "not acknowledged") -> dict:
    """Hand an unanswered page to the next person in the chain; when the chain runs out, say so."""
    row = g.read("""MATCH (pg:Page {id: $id})-[:FOR]->(i:Incident) RETURN i.id AS inc, pg.level AS level""", {"id": page_id})
    if not row:
        raise KeyError(f"no page {page_id}")
    inc, level = row[0]["inc"], row[0]["level"]
    won = g.write("""MATCH (pg:Page {id: $id}) WHERE pg.status = 'paged' SET pg.status = 'escalated', pg.note = $why
                     RETURN pg.id AS id""", {"id": page_id, "why": reason})
    if not won:
        raise ValueError(f"{page_id} is no longer waiting, so it can't escalate")
    chain = _chain(g, inc)
    paged = {r["handle"] for r in _rows(g, inc)}
    nxt = next((p for p in chain if p["handle"] not in paged), None)
    if nxt is None:
        return _create(g, inc, level + 1, None, "exhausted", "everyone in the chain was paged and nobody answered", page_id)
    return _create(g, inc, level + 1, nxt, "paged", nxt["why"], page_id, note=reason)


def escalate_overdue(g: Graph) -> list[dict]:
    """Escalate every page left unacknowledged past ESCALATE_S. Run on a timer (the on-call page polls it)."""
    out = []
    for r in _rows(g):
        if r["status"] == "paged" and _now_wall() - r["wall_at"] >= ESCALATE_S:
            try:
                out.append(escalate(g, r["id"], f"no acknowledgement in {ESCALATE_S // 60} min"))
            except ValueError:      # someone acknowledged it in the meantime
                pass
    return out


# --- standing approvals ------------------------------------------------------------------------------------

def grant_standing(g: Graph, kind: str, service: str, by: str, uses: int = 3, days: int = 30) -> dict:
    """A person agrees in advance: this kind of fix on this service may be approved without paging anyone, at
    most `uses` times in the next `days`, and only when the evidence gate confirms the cause and the fix was proved
    on a graph copy to clear it. Outside those terms it does nothing and says why."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    if not 1 <= uses <= MAX_USES or not 1 <= days <= MAX_DAYS:
        raise ValueError(f"uses 1 to {MAX_USES}, days 1 to {MAX_DAYS}")
    if not g.read("MATCH (s:Service {name: $s}) RETURN s.name AS s", {"s": service}):
        raise KeyError(f"no service {service}")
    sid = "SA-" + uuid.uuid4().hex[:6].upper()
    at = tools.now()
    g.write("""
        MATCH (s:Service {name: $svc})
        CREATE (sa:StandingApproval {id: $id, kind: $kind, service: $svc, requires: 'gate confirmed and fix proved',
                                     uses_left: $uses, uses: $uses, at: $at, expires: $exp, by: $by, active: true})-[:COVERS]->(s)
        WITH sa
        OPTIONAL MATCH (p:Person {handle: $by})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:GRANTED]->(sa))""",
            {"svc": service, "id": sid, "kind": kind, "uses": uses, "at": at, "exp": at + days * 86400, "by": by[:60]})
    return next(x for x in standing(g) if x["id"] == sid)


def revoke_standing(g: Graph, sid: str, by: str) -> dict:
    won = g.write("""MATCH (sa:StandingApproval {id: $id}) WHERE sa.active SET sa.active = false, sa.revoked_by = $by
                     RETURN sa.id AS id""", {"id": sid, "by": by[:60]})
    if not won:
        raise KeyError(f"no active standing approval {sid}")
    return {"id": sid, "active": False}


def standing(g: Graph) -> list[dict]:
    """Every standing approval with each time it was considered: fired, or not and why."""
    rows = g.read("""
        MATCH (sa:StandingApproval)
        OPTIONAL MATCH (d:StandingDecision)-[:UNDER]->(sa)
        WITH sa, d ORDER BY d.wall_at DESC
        RETURN sa.id AS id, sa.kind AS kind, sa.service AS service, sa.uses AS uses, sa.uses_left AS uses_left,
               sa.expires AS expires, sa.by AS by, sa.active AS active, sa.requires AS requires,
               collect(CASE WHEN d IS NULL THEN null ELSE {action: d.action, incident: d.incident, fired: d.fired,
                       reasons: d.reasons} END) AS decisions
        ORDER BY sa.id""")
    for r in rows:
        r["decisions"] = [dict(x, reasons=json.loads(x["reasons"] or "[]")) for x in r["decisions"] if x]
    return rows


def try_standing(g: Graph, action_id: str, gate: dict) -> dict:
    """If a standing approval covers this proposed action, check its terms and approve under it, or record why
    not. Returns {"fired": bool, "approval": id or None, "reasons": [...]}; no approval covering it: fired False
    and no reasons."""
    act = g.read("""
        MATCH (a:Action {id: $id})-[:TARGETS]->(s), (a)-[:FOR]->(i:Incident)
        RETURN a.status AS status, a.kind AS kind, s.name AS target, a.change_id AS cid, a.preview_verdict AS proof,
               collect(i.id) AS incidents""", {"id": action_id})
    if not act:
        raise KeyError(f"no action {action_id}")
    a = act[0]
    cover = g.read("""MATCH (sa:StandingApproval {kind: $kind, service: $svc}) WHERE sa.active
                      RETURN sa.id AS id, sa.uses_left AS left, sa.expires AS exp ORDER BY sa.id""",
                   {"kind": a["kind"], "svc": a["target"]})
    if not cover or a["status"] != "proposed":
        return {"fired": False, "approval": None, "reasons": []}
    sa = cover[0]
    lead_change = next((x["change"] for x in gate.get("sweep", []) if x["status"] == "leader"), None)
    reasons = []
    if gate.get("verdict") != "confirmed":
        reasons.append(f"the evidence gate says {gate.get('verdict_label', gate.get('verdict', 'nothing')).lower()}, not confirmed")
    if a["cid"] and lead_change != a["cid"]:
        reasons.append(f"the fix reverts {a['cid']}, but the gate's leader is {gate.get('leader')}")
    if a["proof"] != "resolves":
        reasons.append(f"the fix was not proved to clear the incident on a graph copy ({a['proof'] or 'no proof'})")
    if sa["left"] <= 0:
        reasons.append("no uses left")
    if sa["exp"] < tools.now():
        reasons.append("it has expired")
    fired = False
    if not reasons:
        # take one use atomically: two investigations racing for the last use can't both have it
        fired = bool(g.write("""MATCH (sa:StandingApproval {id: $id}) WHERE sa.active AND sa.uses_left > 0
                                SET sa.uses_left = sa.uses_left - 1 RETURN sa.id AS id""", {"id": sa["id"]}))
        if not fired:
            reasons.append("its last use was just taken")
    if fired:
        tools.decide_action(g, action_id, True, f"standing approval {sa['id']}")
        g.write("MATCH (a:Action {id: $a}), (sa:StandingApproval {id: $s}) CREATE (a)-[:APPROVED_UNDER]->(sa)",
                {"a": action_id, "s": sa["id"]})
        for inc in a["incidents"]:     # nobody is woken: the owner gets a notice, not a page
            svc, team, _ = _blamed(g, inc)
            who = tools.on_call_at(g, team, tools.now()) if team else None
            _create(g, inc, 0, who, "notice", f"fixed under standing approval {sa['id']} ({a['kind']} on {a['target']}); "
                                              f"for information, no acknowledgement needed")
    g.write("""
        MATCH (a:Action {id: $a}), (sa:StandingApproval {id: $s})
        CREATE (d:StandingDecision {action: $a, incident: $inc, fired: $fired, reasons: $why, wall_at: $wall})-[:UNDER]->(sa),
               (d)-[:ON]->(a)""",
            {"a": action_id, "s": sa["id"], "inc": ", ".join(a["incidents"]), "fired": fired, "why": json.dumps(reasons),
             "wall": _now_wall()})
    return {"fired": fired, "approval": sa["id"], "reasons": reasons}
