"""Before a change ships: what breaks if it goes wrong, and should it go out now.

    preflight(g, "auth", "deploy", "switch JWT validation to a rotating JWKS cache")

The same graph the judge reads after an outage, read before one. If the change broke its service, who would feel it
(every caller, the internet-facing entry points, the user journeys and their SLOs, the teams that would be paged);
how central the service is; whether an open incident is running through it right now; what changes like this one
did to it before; what has already changed on it in the last two hours; and who should look at it. The answer is
ship, review or hold, with every reason and what it rests on. Read-only: nothing is written.

From CI, before merge (exit 1 on hold):

    scripts/preflight.sh auth deploy "switch JWT validation to a rotating JWKS cache"
"""
from __future__ import annotations

from . import tools
from .db import Graph

KINDS = ("deploy", "config", "flag")
FIX_FOR = {"deploy": "rollback", "config": "revert_config", "flag": "disable_flag"}
CENTRAL_TOP = 5          # a PageRank rank this high, with internet-facing callers, wants a second pair of eyes
CHOKEPOINT_TOP = 5       # so does a top betweenness score: the most call paths run through it
STACK_MIN = tools.WINDOW_MIN   # changes on the same service this recently make a failure hard to attribute
LEVELS = {"hold": 2, "review": 1, "info": 0}


def _ago(ts: int) -> str:
    d = max(0, tools.now() - ts)
    return f"{round(d / 60)} min ago" if d < 2 * 3600 else f"{round(d / 3600)} h ago" if d < 2 * 86400 else f"{round(d / 86400)} d ago"


def preflight(g: Graph, service: str, kind: str = "deploy", summary: str = "") -> dict:
    """What happens if this change to `service` goes wrong, and whether it should ship now (see the module doc)."""
    if kind not in KINDS:
        raise ValueError(f"kind is one of {', '.join(KINDS)}")
    found = g.read("""
        MATCH (s:Service {name: $svc})
        RETURN s.name AS name, s.kind AS kind, s.tier AS tier, s.internet_exposed AS exposed""", {"svc": service})
    if not found:
        raise KeyError(f"no service {service} in the graph: it isn't modelled, so nothing here can say a change to it is safe")
    at = tools.now()
    reasons: list[dict] = []

    def say(level: str, text: str, cites: list[str] | None = None) -> None:
        reasons.append({"level": level, "text": text, "cites": cites or []})

    # If it breaks: every caller, in the waves the alerts would arrive in, and who'd be paged.
    sim = tools.simulate_failure(g, service)
    reach = [s for w in sim["waves"] for s in w["services"]]
    exposed = sim["internet_exposed"] + ([service] if found[0]["exposed"] else [])
    journeys = tools.journeys_hit(g, [service])
    rank = tools.critical_services(g, limit=1000)
    pr = next((r for r in rank if r["service"] == service), None)
    choke = tools.single_points_of_failure(g, limit=CHOKEPOINT_TOP)
    choke_rank = next((i + 1 for i, r in enumerate(choke) if r["service"] == service), None)

    # Right now: open incidents this service sits under. It matters most when the judge blames it, or when it is one
    # of the services failing in the incident: a change there lands inside a live failure.
    open_incs = []
    for inc in tools.list_incidents(g, "open"):
        below = {inc["symptom"]} | {d["service"] for d in tools.dependencies(g, inc["symptom"])}
        if service not in below:
            continue
        r = tools.rank_suspects(g, inc["id"], limit=1)
        top = r["suspects"][0] if r["suspects"] else None
        open_incs.append({"id": inc["id"], "title": inc["title"], "symptom": inc["symptom"], "severity": inc["severity"],
                          "leader": tools._label(top) if top else None,
                          "blames_this_service": top is not None and top["service"] == service,
                          "failing_in_it": service in r["alert_cone"]})
    quiet = []
    for o in open_incs:
        if o["blames_this_service"]:
            say("hold", f"{o['id']} is open and its leading suspect is {o['leader']} on {service}. Fix or roll that back "
                        f"before shipping another change on top of it.", [o["id"], o["leader"]])
        elif o["failing_in_it"]:
            say("hold", f"{service} is alerting as part of {o['id']} ({o['title']}). A change now would land inside a live "
                        f"failure and blur what the alerts mean.", [o["id"]])
        else:
            quiet.append(o["id"])
    if quiet:
        say("info", f"{service} is quiet but sits under {len(quiet)} open incident{'s' if len(quiet) > 1 else ''} "
                    f"({', '.join(quiet)}): if this change breaks it, it adds to them.", quiet)

    # Before: what changes like this one did to this service, and what was learned.
    history = tools.similar_incidents(g, service)
    same = [h for h in history if h["cause_kind"] == kind]
    for h in same[:3]:
        say("review", f"{h['incident']}, {_ago(h['started'])}: a {kind} on {service} caused it ({h['cause']})."
                      + (f" Lesson: {h['lesson']}" if h.get("lesson") else ""), [h["incident"]])
    if history and not same:
        say("info", f"{len(history)} past incident{'s' if len(history) > 1 else ''} started on {service}, none from a {kind}.",
            [h["incident"] for h in history[:3]])
    record = tools.fix_track_record(g, service, FIX_FOR[kind])
    rec = record[0] if record else None
    if rec and rec["failed"]:
        say("review", f"Undoing a {kind} here ({FIX_FOR[kind]}) did not clear the alerts {rec['failed']} of "
                      f"{rec['worked'] + rec['failed']} times: have a second way back ready.",
            [h["action"] for h in rec["history"] if h["verified"] is False])

    # Stacked: what has already changed on this service in the last two hours.
    recent = g.read("""
        MATCH (c:Change)-[:CHANGED]->(:Service {name: $svc})
        WHERE c.at >= $from AND c.at <= $at AND c.kind IN ['deploy', 'config', 'flag']
        OPTIONAL MATCH (p:Person)-[:MADE]->(c)
        RETURN c.id AS id, c.kind AS kind, c.at AS at, c.summary AS summary, p.handle AS author
        ORDER BY c.at DESC""", {"svc": service, "from": at - STACK_MIN * 60, "at": at})
    if recent:
        say("review", f"{len(recent)} change{'s' if len(recent) > 1 else ''} already landed on {service} in the last "
                      f"{STACK_MIN // 60} hours ({', '.join(c['id'] for c in recent)}). If something breaks, the judge "
                      f"will have to tell them apart.", [c["id"] for c in recent])

    # Where it sits: how far a failure would spread and how central the service is.
    if pr and pr["rank"] <= CENTRAL_TOP and exposed:
        say("review", f"{service} is #{pr['rank']} of {len(rank)} by PageRank and reaches {len(exposed)} internet-facing "
                      f"entry point{'s' if len(exposed) > 1 else ''} ({', '.join(sorted(exposed))}).", sorted(exposed))
    if choke_rank:
        say("review", f"{service} is #{choke_rank} by betweenness: more call paths run through it than almost anywhere else.")
    if reach:
        say("info", f"If it broke, {len(reach)} service{'s' if len(reach) > 1 else ''} above it would feel it, in "
                    f"{len(sim['waves'])} wave{'s' if len(sim['waves']) > 1 else ''}"
                    + (f", and {len(journeys)} user journey{'s' if len(journeys) > 1 else ''} would fail" if journeys else "") + ".")
    else:
        say("info", f"Nothing calls {service}: a failure there stays there.")

    # Memory: what the graph remembers about changes worded like this one.
    hits = [h for h in tools.search_memory(g, summary) if h["kind"] in ("lesson", "incident")][:2] if summary else []
    for h in hits:
        say("info", f"Memory: {h['ref']} {h['kind']}: {h['text']}", [h["ref"]])

    owner = tools.who_owns(g, service)
    experts = tools.experts(g, service)
    reviewers = [e for e in experts if e["fixed"] or e["changed"]][:2] or experts[:1]
    decision = max(reasons, key=lambda r: LEVELS[r["level"]])["level"] if reasons else "info"
    decision = {"hold": "hold", "review": "review", "info": "ship"}[decision]
    holds = [r for r in reasons if r["level"] == "hold"]
    n_review = sum(r["level"] == "review" for r in reasons)
    headline = {
        "hold": f"{holds[0]['text'].split('. ')[0]}." if holds else "An open incident runs through it.",
        "review": f"Ship with a reviewer: {n_review} thing{'s' if n_review > 1 else ''} to look at first.",
        "ship": f"Nothing in the graph argues against a {kind} on {service} now.",
    }[decision]
    return {
        "service": service, "kind": kind, "summary": summary, "at": at, "decision": decision, "headline": headline,
        "reasons": sorted(reasons, key=lambda r: -LEVELS[r["level"]]),
        "blast": {"affected": len(reach), "waves": sim["waves"], "internet_exposed": sorted(exposed),
                  "journeys": [{k: j[k] for k in ("journey", "rps", "slo", "uses")} for j in journeys],
                  "teams_paged": sim["teams_paged"]},
        "centrality": {"pagerank_rank": pr["rank"] if pr else None, "services": len(rank), "betweenness_rank": choke_rank},
        "open_incidents": open_incs, "history": history[:5], "recent_changes": recent,
        "rollback_record": rec, "runbooks": sim["runbooks"],
        "owner": {"team": owner["team"], "on_call": owner["on_call"]},
        "reviewers": [{"name": e["name"], "handle": e["handle"], "fixed": e["fixed"], "changed": e["changed"]} for e in reviewers],
    }
