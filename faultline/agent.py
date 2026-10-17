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
        "symptom, change risk, and precedent from resolved incidents. Returns ranked suspects with factor "
        "breakdowns, paths, owners, recommended actions, and changes ruled out as off-path. Start here."),
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


def _dispatch(g: Graph) -> dict[str, Callable[..., Any]]:
    return {
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


def call_tool(tenant: str, name: str, args: dict) -> tuple[Any, list[dict], str | None]:
    """Run one tool against a fresh trace. Returns (result, cypher trace, error)."""
    trace = Trace()
    g = Graph(tenant, trace)
    fn = _dispatch(g).get(name)
    if fn is None:
        return None, [], f"unknown tool {name}"
    try:
        return fn(**args), trace.queries, None
    except (KeyError, ValueError, TypeError) as e:
        return None, trace.queries, str(e)
    except Exception as e:  # database errors (bad Cypher, timeout) go back to the model as tool errors
        return None, trace.queries, f"{type(e).__name__}: {e}"


def _step(name: str, args: dict, result: Any, queries: list[dict], error: str | None, note: str = "") -> dict:
    return {"tool": name, "input": args, "result": result, "cypher": queries, "error": error, "note": note}


# --- offline playbook ---------------------------------------------------------------------------

def _narrate(ranking: dict) -> dict:
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
    ruled = [f"{s['change']['id'] if s['change'] else s['service']} on {s['service']} scored {s['score']} "
             f"(alert chain {s['factors']['alert_chain']})" for s in sus[1:4]]
    ruled += [f"{c['id']} on {c['service']}: not on the dependency path" for c in ranking["ruled_out"][:4]]
    return {"root_cause_service": top["service"], "change_id": top["change"]["id"] if top["change"] else None,
            "confidence": ranking["confidence"], "summary": summary, "evidence": ev, "ruled_out": ruled}


def investigate_offline(tenant: str, incident_id: str, propose: bool = True) -> dict:
    steps = []

    def run(name: str, note: str, **args):
        res, q, err = call_tool(tenant, name, args)
        steps.append(_step(name, args, res, q, err, note))
        if err:
            raise RuntimeError(err)
        return res

    inc = run("get_incident", "Read the incident and the alerts on its symptom.", incident_id=incident_id)
    run("past_investigations", "Check what earlier investigations of this incident concluded.", incident_id=incident_id)
    run("dependencies", f"Walk everything {inc['symptom']} depends on.", service=inc["symptom"])
    ranking = run("rank_suspects", "Score every change and alerting dependency on that path.", incident_id=incident_id)
    top = ranking["suspects"][0] if ranking["suspects"] else None
    if top:
        run("similar_incidents", f"Look for resolved incidents caused on {top['service']}.", service=top["service"])
        run("blast_radius", f"See who else is hurt while {top['service']} is broken.", service=top["service"])
        run("who_owns", f"Find who owns {top['service']} and who is on call.", service=top["service"])
        if propose:
            ra = top["recommended_action"]
            run("propose_action", "Propose the fix. A human approves it before anything happens.",
                incident_id=incident_id, kind=ra["kind"], target=ra["target"], summary=ra["summary"],
                change_id=ra.get("change_id"), reason=f"Top suspect, score {top['score']}",
                also_for=ra.get("also_for"))
    findings = _narrate(ranking)
    return {"mode": "offline", "incident_id": incident_id, "steps": steps, "findings": findings,
            "ranking": ranking, "answer": findings["summary"]}


_ASK_ROUTES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(INC-\d+)\b", re.I), "investigate"),
    (re.compile(r"who (owns|is on.?call for|runs|knows)\s+([\w-]+)", re.I), "who_owns"),
    (re.compile(r"(what breaks|blast radius|who depends on|impact of)\b.*?\b([\w-]+)\s*(goes down|fails|\?|$)", re.I), "blast_radius"),
    (re.compile(r"(before|history|similar|past incidents?|again)\b.*?([\w-]+)?", re.I), "similar_incidents"),
    (re.compile(r"(critical|important|pagerank|single points?)", re.I), "critical_services"),
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
        else:
            svc = mentioned[-1] if mentioned else (None if route == "similar_incidents" else m.group(2))
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


def run_claude(tenant: str, prompt: str, incident_id: str | None) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": prompt}]
    steps: list[dict] = []
    findings = None
    answer = ""
    system = _system(tenant)
    for _ in range(MAX_TURNS):
        resp = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            system=system,
            tools=TOOL_SPECS,
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
        results = []
        for i, u in enumerate(uses):
            args = dict(u.input) if isinstance(u.input, dict) else {}
            res, q, err = call_tool(tenant, u.name, args)
            if u.name == "submit_findings" and not err:
                findings = args
            steps.append(_step(u.name, args, res, q, err, note=(thoughts[-1] if thoughts and i == 0 else "")))
            content = err if err else json.dumps(res, default=str)[:RESULT_CHARS]
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": content, "is_error": bool(err)})
        messages.append({"role": "user", "content": results})
        if findings:
            answer = findings.get("summary", answer)
            break
    out = {"mode": "claude", "model": MODEL, "steps": steps, "findings": findings, "answer": answer,
           "incident_id": incident_id}
    if incident_id:
        out["ranking"] = tools.rank_suspects(Graph(tenant), incident_id)
    return out


# --- entry points -------------------------------------------------------------------------------

def investigate(tenant: str, incident_id: str) -> dict:
    if claude_available():
        try:
            out = run_claude(tenant, f"Investigate {incident_id}. Find the root cause, propose a fix, then submit findings.",
                             incident_id)
        except Exception as e:  # API down or key rejected: the demo still works
            out = investigate_offline(tenant, incident_id)
            out["fallback_reason"] = f"{type(e).__name__}: {e}"[:300]
    else:
        out = investigate_offline(tenant, incident_id)
    out["investigation_id"] = tools.record_investigation(
        Graph(tenant), incident_id, out["mode"], out["ranking"], out.get("answer") or "", len(out["steps"]))
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
