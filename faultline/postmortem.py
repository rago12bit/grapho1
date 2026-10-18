"""A postmortem for an incident, as Markdown, built only from the graph.

Every line comes from a node or edge Hypocenter wrote while handling the incident (the incident, its root cause,
alerts, the judge's ranking, the fix and its verification, the lesson), so it pastes into a wiki, an issue or a
chat and still says only what the graph can back. The appendix lists the Cypher behind it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import tools
from .db import Graph


def _t(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ts else "?"


def _hm(ts: int | None) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M") if ts else "?"


def _dur(a: int | None, b: int | None) -> str:
    if not a or not b:
        return "?"
    m = round((b - a) / 60)
    return f"{m} min" if m < 120 else f"{m // 60} h {m % 60} min"


def postmortem(g: Graph, incident_id: str) -> dict:
    inc = tools.get_incident(g, incident_id)
    row = g.read("""
        MATCH (i:Incident {id: $id})
        OPTIONAL MATCH (i)-[:ROOT_CAUSE]->(c:Change)-[:CHANGED]->(cs:Service)
        OPTIONAL MATCH (p:Person)-[:MADE]->(c)
        OPTIONAL MATCH (i)-[:TAUGHT]->(l:Lesson)
        RETURN i.resolved AS resolved, c.id AS cause_id, c.kind AS cause_kind, c.summary AS cause_summary,
               c.at AS cause_at, cs.name AS cause_service, p.name AS author, collect(l.text) AS lessons""",
                 {"id": incident_id})[0]
    last = (tools.past_investigations(g, incident_id) or [None])[0]
    ranking = tools.rank_suspects(g, incident_id)
    groups = tools.alert_clusters(g, incident_id)["groups"]
    ranking = tools.apply_review(ranking, tools.skeptic_review(ranking, groups))
    top = ranking["suspects"][0] if ranking["suspects"] else None
    second = ranking["suspects"][1] if len(ranking["suspects"]) > 1 else None
    tl = tools.timeline(g, incident_id)
    acts = tools.list_actions(g, incident_id)
    similar = g.read("""
        MATCH (:Incident {id: $id})-[r:SIMILAR_TO]->(p:Incident)
        RETURN p.id AS id, p.title AS title, r.score AS score, r.reason AS reason ORDER BY r.score DESC""",
                     {"id": incident_id})
    cause_svc = row["cause_service"] or (top["service"] if top else None)
    impact = tools.impact(g, cause_svc, incident_id) if cause_svc else None
    final = inc["status"] == "resolved"

    out: list[str] = []
    w = out.append
    w(f"# {incident_id}: {inc['title']}")
    w("")
    w(f"{'Postmortem' if final else '**Draft postmortem** (the incident is not resolved yet; the cause below is the judge’s current lead)'} · "
      f"{inc['severity']} · status **{inc['status']}** · started {_t(inc['started'])}"
      + (f" · resolved {_t(row['resolved'])} ({_dur(inc['started'], row['resolved'])})" if row["resolved"] else ""))
    w(f"Symptom on `{inc['symptom']}` ({inc['team']}).")
    w("")

    w("## Summary")
    w("")
    w(last["summary"] if last and last.get("summary") else
      "No agent run is recorded for this incident; the sections below are the judge reading the graph.")
    w("")

    w("## Root cause")
    w("")
    if row["cause_id"]:
        w(f"- **{row['cause_id']}** ({row['cause_kind']}) on `{row['cause_service']}`: {row['cause_summary']}")
        if row["cause_at"]:
            w(f"- Landed {_t(row['cause_at'])}, {round((inc['started'] - row['cause_at']) / 60)} min before the incident"
              + (f", made by {row['author']}" if row["author"] else ""))
    elif top:
        what = f"**{top['change']['id']}** ({top['change']['kind']}) on `{top['service']}`: {top['change']['summary']}" \
            if top["change"] else f"no change: `{top['service']}` failed first (infrastructure)"
        w(f"- Not confirmed yet. Leading suspect: {what}")
    else:
        w("- No cause found.")
    w("")

    if top:
        w("## How we know")
        w("")
        w(f"- Failure path, symptom to cause: {' → '.join(f'`{s}`' for s in top['path'])}")
        seq = top["footprint"]["sequence"]
        if seq:
            w(f"- Alerts arrived {'in' if top['footprint']['in_order'] else 'out of'} the predicted order: "
              + " → ".join(f"`{x['service']}` {_hm(x['at'])}" for x in seq))
        w(f"- Explains {len(top['footprint']['explained'])} of {top['footprint']['observed']} alerting services")
        if second:
            w(f"- Runner-up: {tools._label(second)} at {second['score']:.2f} against {top['score']:.2f}; "
              f"confidence **{ranking['confidence']}**" + (f" ({round(100 * ranking['confidence_p'])}%)" if ranking.get("confidence_p") is not None else ""))
        if ranking.get("skeptic"):
            sk = ranking["skeptic"]
            w(f"- Skeptic: {sk['verdict']}; " + "; ".join(
                f"{'✓' if c['passed'] else '✗'} {c['check']}" for c in sk["rounds"][-1]["checks"]))
        if len(groups) > 1:
            w(f"- Alerts split into {len(groups)} separate groups (WCC): "
              + "; ".join(", ".join(gr["services"]) for gr in groups))
        w("")

    w("## Timeline")
    w("")
    events = [(c["at"], f"{c['kind']} **{c['id']}** on `{c['service']}`: {c['summary']}") for c in tl["changes"]]
    events += [(a["at"], f"alert on `{a['service']}`: {a['signal']}"
                + (f" (cleared {_hm(a['cleared_at'])})" if a.get("cleared_at") else "")) for a in tl["alerts"]]
    events.append((inc["started"], f"**incident opened** on `{inc['symptom']}`"))
    for a in acts:
        if a["status"] != "proposed" and a.get("at"):
            events.append((a["at"], f"fix {a['id']} ({a['kind']} on `{a['target']}`) {a['status']} by {a['decided_by'] or '?'}"
                           + ("" if a.get("verified") is None else " · verified" if a["verified"] else " · **did not work**")))
    if row["resolved"]:
        events.append((row["resolved"], "**resolved**"))
    w("| time (UTC) | event |")
    w("|---|---|")
    for at, text in sorted(events, key=lambda e: e[0] or 0):
        w(f"| {_hm(at)} | {text.replace('|', '/')} |")
    w("")

    if impact and impact["blocks"]:
        w("## Impact")
        w("")
        w(f"A failure on `{impact['service']}` reaches {impact['affected']} service(s), {impact['exposed']} of them internet-facing.")
        w("")
        for b in impact["blocks"]:
            w(f"- `{b['service']}`{' (internet-facing)' if b['exposed'] else ''}{', alerting' if b['alerting'] else ''}: "
              f"via {' → '.join(b['path'])}; owners {b['team']}" + (f", on call {b['on_call']}" if b["on_call"] else ""))
        w("")

    w("## Fix")
    w("")
    done = [a for a in acts if a["status"] != "proposed"]
    if not done:
        pending = [a for a in acts if a["status"] == "proposed"]
        w(f"- Proposed, waiting for approval: {pending[0]['summary']}" if pending else "- No fix recorded.")
    for a in done:
        w(f"- {a['id']} · {a['kind']} on `{a['target']}` · {a['status']} by {a['decided_by'] or '?'}: {a['summary']}")
        if a.get("verification"):
            w(f"  - {'Verified' if a.get('verified') else 'Not verified'}: {a['verification']}")
        if len(a.get("covers") or []) > 1:
            w(f"  - Covered {', '.join(a['covers'])} with one approval")
    w("")

    if row["lessons"]:
        w("## Lesson")
        w("")
        for l in row["lessons"]:
            w(f"> {l}")
        w("")

    if similar:
        w("## Similar past incidents")
        w("")
        for s in similar:
            w(f"- {s['id']} {s['title']} (score {s['score']:.2f}): {s['reason']}")
        w("")

    w("## Appendix: the graph queries behind this postmortem")
    w("")
    seen = set()
    for q in g.trace.queries:
        text = " ".join(q["cypher"].split())
        if text in seen:
            continue
        seen.add(text)
        w(f"- `{text[:220]}{'…' if len(text) > 220 else ''}` ({q['rows']} rows, {q['ms']} ms)")
    w("")
    w(f"_Generated by Hypocenter from graph `{g.tenant}`._")
    return {"markdown": "\n".join(out), "final": final}
