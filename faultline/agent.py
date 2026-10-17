"""The investigator. With an Anthropic API key, Claude chooses which graph tools to call; without one,
a fixed playbook calls the same tools in order. Both return the same shape, step by step, with the
Cypher behind every step, so the UI can't tell them apart except by the `mode` label."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from . import tools
from .db import Graph, Trace

MODEL = "claude-opus-5-5"
MAX_TURNS = 14
RESULT_CHARS = 12000


def _hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M UTC")


# --- tool registry ------------------------------------------------------------------------------

def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S = {"type": "string"}
TOOL_SPECS: list[dict] = [
    {"name": "get_incident", "description": "Incident details: symptom service, start time, owning team, alerts on the symptom.",
     "input_schema": _obj({"incident_id": S}, ["incident_id"])},
    {"name": "rank_suspects", "description": (
        "Deterministic judge. Scores every deploy/config/flag change on the symptom's dependency path (and alerting "
        "dependencies with no change) by recency, hop distance, whether alerts trace an unbroken path back to the "
        "symptom in the right order, change risk, and precedent from resolved incidents. Each suspect carries a "
        "footprint: which alerting services it would explain (coverage) and whether they alerted in the predicted "
        "order. Also returns other_faults (independent faults behind the alerts the leader can't explain), "
        "also_explains (other open incidents with the same cause), owners, recommended actions, and changes ruled "
        "out as off-path."),
     "input_schema": _obj({"incident_id": S}, ["incident_id"])},
    {"name": "dependencies", "description": "Services a service calls, directly or transitively, with hop counts.",
     "input_schema": _obj({"service": S, "max_hops": {"type": "integer", "minimum": 1, "maximum": 5}}, ["service"])},
    {"name": "changes_near", "description": "Deploys, config and flag changes on the given services in the 2 hours before an incident started.",
     "input_schema": _obj({"incident_id": S, "services": {"type": "array", "items": S}}, ["incident_id", "services"])},
    {"name": "alerts_near", "description": "Alerts on the given services around an incident's start.",
     "input_schema": _obj({"incident_id": S, "services": {"type": "array", "items": S}}, ["incident_id", "services"])},
    {"name": "paths_between", "description": "Every call path from one service down to another (up to 5 hops).",
     "input_schema": _obj({"source": S, "target": S}, ["source", "target"])},
    {"name": "similar_incidents", "description": "Resolved incidents whose root cause was on this service: cause, fix, lesson learned. Optional kind: deploy, config, flag, infra, external.",
     "input_schema": _obj({"service": S, "kind": S}, ["service"])},
    {"name": "who_owns", "description": "Owning team, members and who is on call for a service.",
     "input_schema": _obj({"service": S}, ["service"])},
    {"name": "blast_radius", "description": "Every service that calls this one (who is hurt if it fails), internet-exposed entry points, and its PageRank criticality.",
     "input_schema": _obj({"service": S}, ["service"])},
    {"name": "critical_services", "description": "Services ranked by PageRank over the call graph.",
     "input_schema": _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}, [])},
    {"name": "past_investigations", "description": "Earlier investigations of this incident and what they concluded.",
     "input_schema": _obj({"incident_id": S}, ["incident_id"])},
    {"name": "run_cypher", "description": (
        "Read-only Cypher against the tenant graph for questions the other tools don't cover. Labels: Service, Team, "
        "Person, Change, Alert, Incident, Action, Lesson, Investigation. Relationships: CALLS, OWNED_BY, MEMBER_OF, "
        "CHANGED, MADE, FIRED_ON, SYMPTOM_ON, ROOT_CAUSE, RESOLVED_BY, TAUGHT. Times are epoch seconds. Max 200 rows."),
     "input_schema": _obj({"query": S}, ["query"])},
    {"name": "propose_action", "description": (
        "Propose a remediation. It is NOT executed: a human approves or rejects it in the UI. kind is one of "
        "rollback, revert_config, disable_flag, page_owner. Include change_id when it reverts a change. If the "
        "change also explains other open incidents (rank_suspects lists them in also_explains), pass their ids in "
        "also_for so one approval mitigates all of them."),
     "input_schema": _obj({"incident_id": S, "kind": {"type": "string", "enum": ["rollback", "revert_config", "disable_flag", "page_owner"]},
                           "target": S, "summary": S, "change_id": S, "reason": S,
                           "also_for": {"type": "array", "items": S}},
                          ["incident_id", "kind", "target", "summary"])},
    {"name": "simulate_failure", "description": "What-if: if this service failed, the waves in which alerts would arrive, internet-facing entry points hit, teams paged, and its runbooks.",
     "input_schema": _obj({"service": S}, ["service"])},
    {"name": "alert_clusters", "description": "WCC (weakly connected components) over the alerting services under the symptom: each connected group of alerts is a separate problem. Use it to count independent faults.",
     "input_schema": _obj({"incident_id": S}, ["incident_id"])},
    {"name": "failure_paths", "description": "algo.SPpaths: the k shortest call paths from one service down to another.",
     "input_schema": _obj({"source": S, "target": S, "k": {"type": "integer", "minimum": 1, "maximum": 10}}, ["source", "target"])},
    {"name": "single_points_of_failure", "description": "Betweenness centrality over CALLS: the services the most call paths run through.",
     "input_schema": _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 30}}, [])},
    {"name": "failure_domains", "description": "Label propagation over CALLS: groups of services that mostly talk to each other, with their teams.",
     "input_schema": _obj({}, [])},
    {"name": "runbooks", "description": "The team's runbooks for a service: when each applies and its steps.",
     "input_schema": _obj({"service": S}, ["service"])},
    {"name": "read_handoff", "description": "Read the previous agent's handoff for this incident from the graph. role: triage, investigator or skeptic.",
     "input_schema": _obj({"incident_id": S, "role": {"type": "string", "enum": ["triage", "investigator", "skeptic"]}}, ["incident_id", "role"])},
    {"name": "challenge_leader", "description": (
        "Skeptic review of the judge's leader: does it explain every alert joined to the symptom, did alerts come in "
        "the predicted order, did the change land before the incident, does the alert chain reach the symptom, is it "
        "clearly ahead? If another suspect explains more and scores within 0.15, it takes over. Returns the verdict "
        "(upheld / upheld with doubts / overturned), each check, the final leader and a calibrated confidence."),
     "input_schema": _obj({"incident_id": S}, ["incident_id"])},
    {"name": "write_handoff", "description": "Write your handoff to the graph for the next agent: a short summary plus the facts it needs. Call once, last.",
     "input_schema": _obj({"incident_id": S, "summary": S, "data": {"type": "object"}}, ["incident_id", "summary"])},
    {"name": "submit_findings", "description": "Finish an incident investigation. Call exactly once, last.",
     "input_schema": _obj({
         "incident_id": S,
         "root_cause_service": S,
         "change_id": {"type": ["string", "null"], "description": "The change you blame, or null if no change explains it."},
         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "summary": {"type": "string", "description": "2-4 sentences an on-call engineer can act on."},
         "evidence": {"type": "array", "items": S, "description": "Short facts from the graph that support the conclusion."},
         "ruled_out": {"type": "array", "items": S, "description": "Suspects you rejected and why."},
     }, ["incident_id", "root_cause_service", "change_id", "confidence", "summary", "evidence"])},
]


def _start(g: Graph, incident_id: str) -> int:
    return tools.get_incident(g, incident_id)["started"]


def _dispatch(g: Graph, run_id: str | None = None, role: str | None = None) -> dict[str, Callable[..., Any]]:
    return {
        "simulate_failure": lambda service: tools.simulate_failure(g, service),
        "challenge_leader": lambda incident_id: tools.challenge_leader(g, incident_id),
        "alert_clusters": lambda incident_id: tools.alert_clusters(g, incident_id),
        "failure_paths": lambda source, target, k=3: tools.failure_paths(g, source, target, k),
        "single_points_of_failure": lambda limit=8: tools.single_points_of_failure(g, limit),
        "failure_domains": lambda: tools.failure_domains(g),
        "runbooks": lambda service: tools.runbooks(g, service),
        "read_handoff": lambda incident_id, role: tools.read_handoff(g, incident_id, role, run_id),
        "write_handoff": lambda incident_id, summary, data=None:
            tools.write_handoff(g, incident_id, role or "investigator", summary, data, run_id),
        "get_incident": lambda incident_id: tools.get_incident(g, incident_id),
        "rank_suspects": lambda incident_id: tools.rank_suspects(g, incident_id),
        "dependencies": lambda service, max_hops=tools.MAX_HOPS: tools.dependencies(g, service, max_hops),
        "changes_near": lambda incident_id, services: tools.changes_near(g, services, _start(g, incident_id)),
        "alerts_near": lambda incident_id, services: tools.alerts_near(g, services, _start(g, incident_id)),
        "paths_between": lambda source, target: tools.paths_between(g, source, target),
        "similar_incidents": lambda service, kind=None: tools.similar_incidents(g, service, kind or None),
        "who_owns": lambda service: tools.who_owns(g, service),
        "blast_radius": lambda service: tools.blast_radius(g, service),
        "critical_services": lambda limit=10: tools.critical_services(g, limit),
        "past_investigations": lambda incident_id: tools.past_investigations(g, incident_id),
        "run_cypher": lambda query: tools.run_cypher(g, query),
        "propose_action": lambda incident_id, kind, target, summary, change_id=None, reason="", also_for=None:
            tools.propose_action(g, incident_id, kind, target, summary, change_id, reason, also_for),
        "submit_findings": lambda **kw: {"accepted": True},
    }


def call_tool(tenant: str, name: str, args: dict, run_id: str | None = None,
              role: str | None = None) -> tuple[Any, list[dict], str | None]:
    """Run one tool against a fresh trace. Returns (result, cypher trace, error)."""
    trace = Trace()
    g = Graph(tenant, trace)
    fn = _dispatch(g, run_id, role).get(name)
    if fn is None:
        return None, [], f"unknown tool {name}"
    try:
        return fn(**args), trace.queries, None
    except (KeyError, ValueError, TypeError) as e:
        return None, trace.queries, str(e)
    except Exception as e:  # database errors (bad Cypher, timeout) go back to the model as tool errors
        return None, trace.queries, f"{type(e).__name__}: {e}"


def _step(name: str, args: dict, result: Any, queries: list[dict], error: str | None, note: str = "",
          role: str | None = None) -> dict:
    return {"tool": name, "input": args, "result": result, "cypher": queries, "error": error, "note": note,
            "role": role}


# --- offline playbook ---------------------------------------------------------------------------

def _narrate(ranking: dict, books: list[dict] | None = None) -> dict:
    inc = ranking["incident"]
    sus = ranking["suspects"]
    if not sus:
        return {"root_cause_service": None, "change_id": None, "confidence": "low",
                "summary": f"Nothing on {inc['symptom']}'s dependency path changed or alerted in the last two hours.",
                "evidence": [], "ruled_out": [c["id"] + ": " + c["reason"] for c in ranking["ruled_out"]]}
    top = sus[0]
    path = " → ".join(top["path"])
    ev = [f"Dependency path {path} ({top['hops']} hop{'s' if top['hops'] != 1 else ''})"]
    ev += [f"{a['service']} alerted at {_hhmm(a['at'])}: {a['signal']}" for a in top["evidence_alerts"]]
    ev += [f"Precedent {p['incident']}: {p['cause']} → {p['fix_summary']}" for p in top["precedents"][:2]]
    ev += [f"Same change also explains {o['id']} ({o['title']}): alerts trace {' → '.join(o['path'])}"
           for o in top.get("also_explains", [])]
    fp = top["footprint"]
    seq = " → ".join(f"{x['service']} {_hhmm(x['at'])}" for x in fp["sequence"])
    ev.append(f"Footprint: explains {len(fp['explained'])} of {fp['observed']} alerting services"
              + (f", in the predicted order ({seq})" if fp["in_order"] and seq else f" ({seq})" if seq else ""))
    for o in ranking.get("other_faults", []):
        who = (o["change"] or {}).get("id") or f"{o['service']} (no change)"
        inc_ids = ", ".join(i["id"] for i in o["incidents"])
        ev.append(f"Independent fault: {who} explains {', '.join(o['explains'])}" + (f", already {inc_ids}" if inc_ids else ""))
    groups = ranking.get("alert_groups") or []
    if groups:
        if len(groups) == 1:
            ev.append("All alerts form one connected group (WCC): " + ", ".join(groups[0]["services"]))
        else:
            ev.append(f"Alerts form {len(groups)} separate connected groups (WCC), so {len(groups)} separate problems: "
                      + "; ".join("{" + ", ".join(g_["services"]) + "}" for g_ in groups))
    choke = {c["service"]: c for c in ranking.get("chokepoints") or []}
    on_path = [choke[s_] for s_ in top["path"] if s_ in choke and choke[s_]["betweenness"] > 0]
    if on_path:
        ev.append("The failure path runs through " + ", ".join(
            f"{c['service']} (#{c['rank']} by betweenness)" for c in sorted(on_path, key=lambda c: c["rank"])[:3]))
    doms = [d for d in ranking.get("domains") or [] if set(d["services"]) & set(fp["explained"])]
    if doms:
        ev.append(f"Failure domains reached (label propagation): " + "; ".join(
            f"{', '.join(d['teams'])} ({len(set(d['services']) & set(fp['explained']))} alerting)" for d in doms))
    for b in (books or [])[:1]:
        ev.append(f"Runbook {b['id']} “{b['title']}”: " + "; ".join(b["steps"][:3]))
    if top["type"] == "change":
        c = top["change"]
        ev.insert(0, f"{c['id']} by {c['author']} at {_hhmm(c['at'])}, {top['minutes_before']:g} min before the incident: {c['summary']}")
        summary = (f"{c['summary']} ({c['id']}) is the most likely cause of {inc['id']}. It landed "
                   f"{top['minutes_before']:g} minutes before {inc['symptom']} degraded, and alerts follow the path {path}. "
                   f"{top['recommended_action']['summary']}; {top['owner']['on_call']['name'] if top['owner'].get('on_call') else top['owner']['team']} "
                   f"({top['owner']['team']}) is on call.")
        if top.get("also_explains"):
            ids = ", ".join(o["id"] for o in top["also_explains"])
            summary += f" The same change also explains {ids}: one fix should clear {'both' if len(top['also_explains']) == 1 else 'all of them'}."
    else:
        summary = (f"No deploy, config or flag change on {inc['symptom']}'s dependency path explains {inc['id']}. "
                   f"{top['service']} started alerting first, {top['minutes_before']:g} minutes before the incident, and the "
                   f"alerts follow the path {path}. Treat it as an infrastructure failure on {top['service']}: page "
                   f"{top['owner']['on_call']['name'] if top['owner'].get('on_call') else top['owner']['team']} ({top['owner']['team']}).")
    if ranking.get("other_faults"):
        parts = [f"{(o['change'] or {}).get('id') or o['service']}"
                 + (f" ({', '.join(i['id'] for i in o['incidents'])})" if o["incidents"] else "")
                 for o in ranking["other_faults"]]
        summary += (f" It explains only {len(fp['explained'])} of the {fp['observed']} alerting services below "
                    f"{inc['symptom']}; the rest come from independent faults: {', '.join(parts)}.")
    ruled = [f"{s['change']['id'] if s['change'] else s['service']} on {s['service']} scored {s['score']} "
             f"(alert chain {s['factors']['alert_chain']})" for s in sus[1:4]]
    ruled += [f"{c['id']} on {c['service']}: not on the dependency path" for c in ranking["ruled_out"][:4]]
    return {"root_cause_service": top["service"], "change_id": top["change"]["id"] if top["change"] else None,
            "confidence": ranking["confidence"], "summary": summary, "evidence": ev, "ruled_out": ruled}


def investigate_offline(tenant: str, incident_id: str, propose: bool = True, run_id: str | None = None) -> dict:
    """Three agents in sequence. None passes anything to the next in code: each writes a handoff Episode to
    the graph and the next one starts by reading it back."""
    steps: list[dict] = []
    run_id = run_id or tools.new_run_id()

    def run(agent: str, name: str, note: str, **args):
        res, q, err = call_tool(tenant, name, args, run_id, agent)
        steps.append(_step(name, args, res, q, err, note, agent))
        if err:
            raise RuntimeError(err)
        return res

    # Triage: what is broken, what sits under it, what changed and what is alerting.
    inc = run("triage", "get_incident", "Read the incident and the alerts on its symptom.", incident_id=incident_id)
    run("triage", "past_investigations", "Check what earlier investigations of this incident concluded.", incident_id=incident_id)
    deps = run("triage", "dependencies", f"Walk everything {inc['symptom']} depends on.", service=inc["symptom"])
    area = [inc["symptom"]] + [d["service"] for d in deps]
    changes = run("triage", "changes_near", "List deploys, config and flag changes on that path in the last two hours.",
                  incident_id=incident_id, services=area)
    alerts = run("triage", "alerts_near", "List what is alerting on that path.", incident_id=incident_id, services=area)
    alerting = sorted({a["service"] for a in alerts})
    run("triage", "write_handoff", "Hand off to the investigator through the graph.", incident_id=incident_id,
        summary=f"{inc['symptom']} is failing. {len(deps)} services below it, {len(changes)} recent changes, "
                f"{len(alerting)} alerting: {', '.join(alerting)}.",
        data={"symptom": inc["symptom"], "changes": [c["id"] for c in changes], "alerting": alerting})

    # Investigator: score the suspects, simulate each one's footprint, check precedent.
    run("investigator", "read_handoff", "Pick up triage's handoff from the graph.", incident_id=incident_id, role="triage")
    ranking = run("investigator", "rank_suspects",
                  "Score every suspect and simulate its footprint: what it would break, and in what order.",
                  incident_id=incident_id)
    top = ranking["suspects"][0] if ranking["suspects"] else None
    groups = run("investigator", "alert_clusters",
                 "Split the alerts into connected groups (WCC): each separate group is a separate problem.",
                 incident_id=incident_id)["groups"]
    if top:
        run("investigator", "failure_paths", f"Shortest call paths from {inc['symptom']} down to {top['service']} (SPpaths).",
            source=inc["symptom"], target=top["service"])
        run("investigator", "similar_incidents", f"Look for resolved incidents caused on {top['service']}.", service=top["service"])
        run("investigator", "simulate_failure", f"Predict how a failure of {top['service']} spreads.", service=top["service"])
    run("investigator", "write_handoff", "Hand off to the responder through the graph.", incident_id=incident_id,
        summary=(f"Leading suspect {(top['change'] or {}).get('id') or top['service']} on {top['service']} "
                 f"(score {top['score']}, {ranking['confidence']} confidence), explains "
                 f"{len(top['footprint']['explained'])} of {top['footprint']['observed']} alerting services.")
        if top else "No suspect found.",
        data={"service": top["service"] if top else None, "change_id": (top["change"] or {}).get("id") if top else None,
              "confidence": ranking["confidence"], "action": top["recommended_action"] if top else None,
              "other_faults": ranking["other_faults"]})

    # Skeptic: try to knock the leader over before anyone acts on it.
    run("skeptic", "read_handoff", "Pick up the investigator's handoff from the graph.", incident_id=incident_id, role="investigator")
    challenged = run("skeptic", "challenge_leader",
                     "What doesn't the leader explain? Wrong order, too late, too close to the runner-up?",
                     incident_id=incident_id)
    ranking = tools.apply_review(ranking, tools.skeptic_review(ranking, groups))
    ranking["alert_groups"] = groups
    top = ranking["suspects"][0] if ranking["suspects"] else None
    sk = challenged["skeptic"]
    run("skeptic", "write_handoff", "Hand the reviewed verdict to the responder through the graph.", incident_id=incident_id,
        summary=f"Verdict {sk['verdict']}: leader {sk['leader']}, {sk['failed_checks']} failed check(s), "
                f"confidence {challenged['confidence']} ({challenged['confidence_p']}).",
        data={"leader": sk["leader"], "verdict": sk["verdict"], "confidence": challenged["confidence"],
              "action": top["recommended_action"] if top else None})

    # Responder: who to page, which runbook applies, and the fix to propose.
    handoff = run("responder", "read_handoff", "Pick up the skeptic's reviewed verdict from the graph.",
                  incident_id=incident_id, role="skeptic")
    act = (handoff or {}).get("data", {}).get("action")
    if act:
        run("responder", "who_owns", f"Find who owns {act['target']} and who is on call.", service=act["target"])
        ranking["chokepoints"] = run("responder", "single_points_of_failure",
                                     "Which services on the failure path carry the most traffic (betweenness)?", limit=24)
        ranking["domains"] = run("responder", "failure_domains",
                                 "Which failure domains and teams the alerts reach (label propagation).")
        run("responder", "runbooks", f"Find the runbook for {act['target']}.", service=act["target"])
        if propose:
            run("responder", "propose_action", "Propose the fix. A human approves it before anything happens.",
                incident_id=incident_id, kind=act["kind"], target=act["target"], summary=act["summary"],
                change_id=act.get("change_id"), reason=f"Top suspect, score {top['score']}",
                also_for=act.get("also_for"))
    findings = _narrate(ranking, tools.runbooks(Graph(tenant), top["service"]) if top else [])
    run("responder", "write_handoff", "Record the outcome in the graph.", incident_id=incident_id,
        summary=findings["summary"], data={"change_id": findings["change_id"]})
    return {"mode": "offline", "incident_id": incident_id, "run_id": run_id, "steps": steps, "findings": findings,
            "ranking": ranking, "answer": findings["summary"]}


_ASK_ROUTES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(INC-\d+)\b", re.I), "investigate"),
    (re.compile(r"who (owns|is on.?call for|runs|knows)\s+([\w-]+)", re.I), "who_owns"),
    (re.compile(r"(what breaks|what if|simulate|before (i|we) ship)\b.*?\b([\w-]+)\s*(goes down|fails|\?|$)", re.I), "simulate_failure"),
    (re.compile(r"(blast radius|who depends on|impact of)\b.*?\b([\w-]+)\s*(goes down|fails|\?|$)", re.I), "blast_radius"),
    (re.compile(r"(runbook|how do (i|we) fix)\b.*?([\w-]+)?", re.I), "runbooks"),
    (re.compile(r"(before|history|similar|past incidents?|again)\b.*?([\w-]+)?", re.I), "similar_incidents"),
    (re.compile(r"(single points? of failure|chokepoints?|bottlenecks?|betweenness)", re.I), "single_points_of_failure"),
    (re.compile(r"(failure domains?|communit(y|ies)|clusters?|label propagation)", re.I), "failure_domains"),
    (re.compile(r"(critical|important|pagerank)", re.I), "critical_services"),
]


def ask_offline(tenant: str, question: str) -> dict:
    services = {r["service"] for r in Graph(tenant).read("MATCH (s:Service) RETURN s.name AS service")}
    mentioned = [w for w in re.findall(r"[\w-]+", question.lower()) if w in services]
    for pat, route in _ASK_ROUTES:
        m = pat.search(question)
        if not m:
            continue
        if route == "investigate":
            return investigate_offline(tenant, m.group(1).upper(), propose=False)
        if route == "critical_services":
            args = {"limit": 8}
            res, q, err = call_tool(tenant, route, args)
            answer = "Most critical by PageRank: " + ", ".join(f"{r['service']} ({r['score']})" for r in res or [])
        elif route == "single_points_of_failure":
            args = {"limit": 6}
            res, q, err = call_tool(tenant, route, args)
            answer = "Most call paths run through (betweenness): " + ", ".join(
                f"{r['service']} ({r['betweenness']})" for r in res or [] if r["betweenness"] > 0)
        elif route == "failure_domains":
            args = {}
            res, q, err = call_tool(tenant, route, args)
            answer = " ".join(f"Domain {d['domain']} ({', '.join(d['teams'])}): {', '.join(d['services'])}." for d in res or [])
        else:
            svc = mentioned[-1] if mentioned else (None if route in ("similar_incidents", "runbooks", "simulate_failure") else m.group(2))
            if not svc:
                continue
            args = {"service": svc}
            res, q, err = call_tool(tenant, route, args)
            answer = _summarize(route, svc, res, err)
        return {"mode": "offline", "steps": [_step(route, args, res, q, err)], "answer": answer, "findings": None}
    return {"mode": "offline", "steps": [], "findings": None, "answer": (
        "Offline mode understands: an incident id (\"investigate INC-301\"), \"who owns payments\", "
        "\"what breaks if ledger-db goes down\", \"has payments-db failed before\", \"most critical services\". "
        "Set ANTHROPIC_API_KEY for free-form questions.")}


def _summarize(route: str, svc: str, res: Any, err: str | None) -> str:
    if err:
        return err
    if route == "who_owns":
        oc = res.get("on_call")
        return f"{svc} is owned by {res['team']}. On call: {oc['name'] if oc else 'nobody listed'}. Members: " + \
            ", ".join(p["name"] for p in res["people"]) + "."
    if route == "blast_radius":
        return (f"If {svc} fails, {len(res['callers'])} services are affected: "
                + ", ".join(f"{c['service']} ({c['hops']} hop{'s' if c['hops'] != 1 else ''})" for c in res["callers"])
                + f". Internet-facing: {', '.join(res['exposed_entry_points']) or 'none'}. PageRank rank #{res['criticality_rank']}.")
    if route == "simulate_failure":
        waves = "; then ".join(", ".join(w["services"]) for w in res["waves"])
        teams = ", ".join(f"{t['team']} ({', '.join(t['on_call']) or 'no on-call'})" for t in res["teams_paged"])
        return (f"If {svc} fails, alerts arrive in waves: {waves}. {res['affected']} services affected; internet-facing: "
                f"{', '.join(res['internet_exposed']) or 'none'}. Teams paged: {teams}."
                + (f" Runbook: {res['runbooks'][0]['title']}." if res["runbooks"] else ""))
    if route == "runbooks":
        if not res:
            return f"No runbook for {svc} yet."
        return " ".join(f"{r['id']} {r['title']} (for {r['applies_to']} problems): " + "; ".join(r["steps"]) + "." for r in res)
    if route == "similar_incidents":
        if not res:
            return f"No resolved incident was caused on {svc}."
        return " ".join(f"{r['incident']} ({r['title']}): {r['cause']}. Fix: {r['fix_summary']}. Lesson: {r['lesson']}" for r in res)
    return json.dumps(res, default=str)[:2000]


# --- Claude -------------------------------------------------------------------------------------

def claude_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _system(tenant: str) -> str:
    title = Graph(tenant).read("MATCH (o:Org) RETURN o.title AS t")[0]["t"]
    return f"""You are Faultline, the on-call investigator for {title}. Its services, call graph, owners, deploys, \
config and flag changes, alerts and past incidents live in a FalkorDB graph, and your tools query it.

How to investigate an incident:
- Start with rank_suspects; it does the multi-hop scoring. Then check its leading suspects yourself: confirm the \
alert chain with alerts_near or paths_between, read precedents with similar_incidents, and look for anything the \
judge could have missed with run_cypher.
- A change counts only if it sits on the symptom's dependency path. If nothing on the path changed, say so and \
look for an alerting dependency instead: "no change found" is a valid answer.
- If the leading change also explains other open incidents (also_explains), say so: they share one root cause.
- Propose one remediation with propose_action, with also_for listing those incidents. It waits for a human; \
never say it has been done.
- Finish with submit_findings. Every evidence item must come from a tool result. Use the change ids, service \
names and times exactly as the graph gives them.

For a plain question (not an incident), answer in a few sentences from tool results, naming the services and \
people involved. The clock is frozen at {_hhmm(tools.now())} on 17 Oct 2026; times in the graph are epoch seconds."""


ROLE_TOOLS = {
    "triage": ["get_incident", "past_investigations", "dependencies", "changes_near", "alerts_near", "write_handoff"],
    "investigator": ["read_handoff", "rank_suspects", "alert_clusters", "failure_paths", "alerts_near", "paths_between",
                     "similar_incidents", "simulate_failure", "run_cypher", "write_handoff"],
    "skeptic": ["read_handoff", "challenge_leader", "alert_clusters", "alerts_near", "paths_between", "write_handoff"],
    "responder": ["read_handoff", "who_owns", "runbooks", "blast_radius", "single_points_of_failure", "failure_domains",
                  "propose_action", "submit_findings"],
}
ROLE_BRIEF = {
    "triage": "You are the TRIAGE agent. Establish what is failing, everything it depends on, what changed on that "
              "path in the last two hours and what is alerting. Do not pick a cause. Finish with write_handoff: a "
              "two-sentence summary plus data {symptom, changes, alerting}.",
    "investigator": "You are the INVESTIGATOR agent. Start with read_handoff(role='triage'), then rank_suspects. Check "
                    "the leader yourself: its footprint (does it explain the alerting services, in order?), its "
                    "precedents, and other_faults for alerts it cannot explain. Finish with write_handoff: summary "
                    "plus data {service, change_id, confidence, action, other_faults}, where action is the leader's "
                    "recommended_action.",
    "skeptic": "You are the SKEPTIC agent. Start with read_handoff(role='investigator'), then challenge_leader. Your job is to "
               "find what the leader does not explain. Check any failed check yourself with alerts_near or paths_between. "
               "Finish with write_handoff: summary plus data {leader, verdict, confidence, action}, where action is the "
               "final leader's recommended_action.",
    "responder": "You are the RESPONDER agent. Start with read_handoff(role='skeptic'). Find the owner and on-call, "
                 "read the runbook, then propose_action exactly once (with also_for when the cause also explains other "
                 "open incidents). Finish with submit_findings.",
}
ASK_TOOLS = ["get_incident", "rank_suspects", "dependencies", "paths_between", "failure_paths", "similar_incidents",
             "who_owns", "blast_radius", "simulate_failure", "critical_services", "single_points_of_failure",
             "failure_domains", "alert_clusters", "runbooks", "run_cypher"]


def run_claude(tenant: str, prompt: str, incident_id: str | None, role: str | None = None,
               run_id: str | None = None) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    names = ROLE_TOOLS[role] if role else ASK_TOOLS
    specs = [t for t in TOOL_SPECS if t["name"] in names]
    system = _system(tenant) + (f"\n\n{ROLE_BRIEF[role]}" if role else "")
    messages: list[dict] = [{"role": "user", "content": prompt}]
    steps: list[dict] = []
    findings = None
    answer = ""
    done_tool = {"triage": "write_handoff", "investigator": "write_handoff", "skeptic": "write_handoff",
                 "responder": "submit_findings"}.get(role)
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=specs,
            messages=messages,
            thinking={"type": "adaptive", "display": "summarized"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            cache_control={"type": "ephemeral"},
        )
        if resp.stop_reason == "refusal":
            answer = "The model declined this request."
            break
        messages.append({"role": "assistant", "content": resp.content})
        thoughts = [b.thinking for b in resp.content if b.type == "thinking" and b.thinking]
        texts = [b.text for b in resp.content if b.type == "text"]
        uses = [b for b in resp.content if b.type == "tool_use"]
        if texts:
            answer = "\n".join(texts)
        if resp.stop_reason != "tool_use" or not uses:
            break
        results, finished = [], False
        for i, u in enumerate(uses):
            args = dict(u.input) if isinstance(u.input, dict) else {}
            if u.name not in names:
                res, q, err = None, [], f"{u.name} is not available to the {role or 'question'} agent"
            else:
                res, q, err = call_tool(tenant, u.name, args, run_id, role)
            if u.name == "submit_findings" and not err:
                findings = args
            finished = finished or (u.name == done_tool and not err)
            steps.append(_step(u.name, args, res, q, err, note=(thoughts[-1] if thoughts and i == 0 else ""), role=role))
            content = err if err else json.dumps(res, default=str)[:RESULT_CHARS]
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": bool(err)})
        messages.append({"role": "user", "content": results})
        if finished:
            if findings:
                answer = findings.get("summary", answer)
            break
    out = {"mode": "claude", "model": MODEL, "steps": steps, "findings": findings, "answer": answer,
           "incident_id": incident_id}
    if incident_id and not role:
        out["ranking"] = tools.rank_suspects(Graph(tenant), incident_id)
    return out


def investigate_claude(tenant: str, incident_id: str, run_id: str) -> dict:
    """Three Claude agents with separate tools; the only channel between them is the graph."""
    steps: list[dict] = []
    out: dict = {}
    for role in tools.ROLES:
        out = run_claude(tenant, f"Incident {incident_id}.", incident_id, role=role, run_id=run_id)
        steps += out["steps"]
    ranking = tools.rank_suspects(Graph(tenant), incident_id)
    ranking = tools.apply_review(ranking, tools.skeptic_review(ranking))
    findings = out.get("findings") or _narrate(ranking)
    return {"mode": "claude", "model": MODEL, "incident_id": incident_id, "run_id": run_id, "steps": steps,
            "findings": findings, "answer": findings.get("summary", ""), "ranking": ranking}


# --- entry points -------------------------------------------------------------------------------

def investigate(tenant: str, incident_id: str) -> dict:
    run_id = tools.new_run_id()
    if claude_available():
        try:
            out = investigate_claude(tenant, incident_id, run_id)
        except Exception as e:  # API down or key rejected: the demo still works
            out = investigate_offline(tenant, incident_id, run_id=tools.new_run_id())
            out["fallback_reason"] = f"{type(e).__name__}: {e}"[:300]
    else:
        out = investigate_offline(tenant, incident_id, run_id=run_id)
    g = Graph(tenant)
    out["claim_check"] = check = tools.verify_claims(g, incident_id, out["findings"], out["ranking"])
    if out["mode"] == "claude":
        # Claims the graph can't back are dropped from the evidence, not shown as fact.
        bad = {c["text"] for c in check["claims"] if c["kind"] == "evidence" and not c["ok"]}
        out["findings"]["evidence"] = [e for e in out["findings"].get("evidence") or [] if e not in bad]
    tools.record_suspicions(g, incident_id, out["ranking"])
    out["investigation_id"] = tools.record_investigation(
        g, incident_id, out["mode"], out["ranking"], out.get("answer") or "", len(out["steps"]))
    return out


def ask(tenant: str, question: str) -> dict:
    if claude_available():
        try:
            m = re.search(r"\b(INC-\d+)\b", question, re.I)
            return run_claude(tenant, question, m.group(1).upper() if m else None)
        except Exception as e:
            out = ask_offline(tenant, question)
            out["fallback_reason"] = f"{type(e).__name__}: {e}"[:300]
            return out
    return ask_offline(tenant, question)
