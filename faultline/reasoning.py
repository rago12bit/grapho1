"""The judge's argument, as a subgraph.

Every suspect the judge scored becomes a Hypothesis. Each one is backed or undercut by Evidence nodes that cite
the graph objects they rest on (alerts, changes, past incidents, services). The skeptic's checks run against
every hypothesis, not only the leader, so a rejected suspect says *why* it lost. Then, for the hypothesis left
standing, the alerts are linked into a causal chain: each alert points at the alert below it that set it off,
down to the change (or the silent failure) where it started.

    (Incident)-[:HAS_HYPOTHESIS]->(Hypothesis {rank, score, status})-[:BLAMES]->(Change | Service)
    (Hypothesis)-[:SUPPORTED_BY | CONTRADICTED_BY]->(Evidence {kind, text, weight})
        -[:CITES]->(Alert | Change | Incident | Service | Operation | LogPattern)
    (Alert)-[:CAUSED_BY {incident, confidence, lag_s, direct, via}]->(Alert | Change | Service)

`build` is pure (plus read-only queries), so the case file can show the argument before anything is written;
`write` stores it when the agents run, and `argument` / `why` read it back with Cypher.
"""
from __future__ import annotations

from .db import Graph
from . import tools

LAG_TIGHT_S = 300      # an alert within 5 minutes of the one below it is a tight link


def _cite_alerts(alerts: list[dict]) -> list[dict]:
    return [{"label": "Alert", "id": a["id"]} for a in alerts if a.get("id")]


def _subject(s: dict) -> dict:
    if s["change"]:
        return {"label": "Change", "id": s["change"]["id"]}
    return {"label": "Node", "id": s["service"]} if s["type"] == "host" else {"label": "Service", "id": s["service"]}


def _evidence(s: dict, ranking: dict, others: list[dict], groups: list[dict] | None, leader: dict | None) -> tuple[list, list]:
    """Supporting and contradicting evidence for one suspect: the skeptic's checks, plus the score factors the
    checks don't cover. Each piece cites what it rests on."""
    by_service: dict[str, list[dict]] = {}
    for a in ranking.get("alerts") or []:
        by_service.setdefault(a["service"], []).append(a)
    sup, con = [], []

    def add(ok: bool, kind: str, text: str, cites: list[dict] | None = None, weight: float | None = None):
        (sup if ok else con).append({"kind": kind, "text": text, "weight": weight, "cites": cites or []})

    checks, _ = tools._challenges(s, ranking, others, groups)
    fp = s["footprint"]
    for c in checks:
        if (c["check"] == "clearly ahead of the runner-up" and s is not leader) or c["check"].startswith("the change landed"):
            continue   # the gap to the leader and the change's timing are stated below, once
        cites: list[dict] = []
        if c["check"].startswith("explains every alert"):
            cites = _cite_alerts([by_service[x][0] for x in (fp["explained"] if c["passed"] else fp["unexplained"])
                                  if x in by_service])
        elif c["check"].startswith("alerts arrive"):
            cites = _cite_alerts([a for a in s["evidence_alerts"]])
        elif c["check"].startswith("alerts trace"):
            cites = _cite_alerts(s["evidence_alerts"])
        add(c["passed"], "check", f"{c['check']}: {c['detail']}", cites)

    f, w = s["factors"], ranking["weights"]["change" if s["change"] else s["type"] if s["type"] == "host" else "signal"]
    if s["type"] == "host":
        h = s["host"]
        add(True, "host", f"{' and '.join(h['services'])} run on {h['node']} ({h['zone']}) and share nothing else: "
                          f"neither calls the other", [_subject(s)] + [{"label": "Service", "id": x} for x in h["services"]],
            round(w["co_location"] * f["co_location"], 3))
        add(h["spread_min"] <= 5, "timing", f"they started failing {h['spread_min']:g} min apart", _cite_alerts(s["evidence_alerts"]),
            round(w["simultaneity"] * f["simultaneity"], 3))
    contrib = lambda k: round(w[k] * f[k], 3)  # noqa: E731
    if s["change"]:
        ch = s["change"]
        if s["minutes_before"] < 0:
            add(False, "timing", f"{ch['id']} landed {-s['minutes_before']:g} min after the incident started", [_subject(s)], None)
        else:
            add(f["recency"] >= 0.3, "timing", f"{ch['id']} landed {s['minutes_before']:g} min before the incident"
                + ("" if f["recency"] >= 0.3 else ", too long before to be the trigger"), [_subject(s)], contrib("recency"))
        if f["risk"] >= 0.5:
            add(True, "risk", f"{ch['kind']} marked {ch.get('risk') or 'medium'} risk", [_subject(s)], contrib("risk"))
    else:
        add(f["earliness"] >= 1.0, "timing",
            f"{s['service']} alerted {'before' if f['earliness'] >= 1.0 else 'after'} the symptom did",
            _cite_alerts([by_service[s["service"]][0]] if s["service"] in by_service else []), contrib("earliness"))
        if f["unchanged"] < 0.5:
            add(False, "change", f"a recent change on or below {s['service']} explains its alert better", [], None)
    if s.get("from_below"):
        add(False, "order", f"something below {s['service']} was alerting first: the failure started deeper", [], None)
    tel = ranking.get("telemetry") or {}
    lim = tel.get("limits", {}).get(s["service"])
    sig = next((a["signal"] for a in ranking.get("alerts") or [] if a["service"] == s["service"]), "") or ""
    if lim and (sig.startswith("cpu") and lim.get("cpu_limit") or sig.startswith("mem") and lim.get("mem_limit_mi")):
        what = f"{lim['cpu_limit']:g} CPU" if sig.startswith("cpu") else f"{lim['mem_limit_mi']:g} MiB of memory"
        add(True, "limits", f"{s['service']} is capped at {what} in its manifest ({lim['replicas']} replica"
                            f"{'' if lim['replicas'] == 1 else 's'}), and its {sig.split(' ')[0]} is what alerted",
            [{"label": "Deployment", "id": lim["deployment"]}])
    for h in tel.get("hot_ops", {}).get(s["service"], [])[:1]:
        add(True, "endpoint", f"{h['op']} on {s['service']} slowed at onset: p90 {h['before']:g} → {h['after']:g} ms ({h['ratio']:g}x)",
            [{"label": "Operation", "id": h["id"]}] + _cite_alerts([{"id": h["alert"]}]))
    for lp in tel.get("logs", {}).get(s["service"], [])[:2]:
        add(True, "logs", f"{'new ' if lp['new'] else ''}error log on {s['service']} ×{lp['count']}: {lp['template'][:90]}",
            [{"label": "LogPattern", "id": lp["id"]}])
    if s["precedents"]:
        n = len(s["precedents"])
        add(True, "precedent", f"{n} past incident{'' if n == 1 else 's'} rooted on {s['service']}",
            [{"label": "Incident", "id": p["incident"]} for p in s["precedents"][:3]], contrib("precedent"))
    if leader is not None and s is not leader:
        add(False, "rank", f"{round(leader['score'] - s['score'], 3)} behind {tools._label(leader)}",
            [_subject(leader)], None)
    return sup, con


def _status(s: dict, review: dict | None, leader: dict) -> str:
    if s is leader:
        return "standing"
    tried = {r["leader"] for r in (review or {}).get("rounds") or []}
    return "overturned" if tools._label(s) in tried else "rejected"


def hypotheses(ranking: dict, limit: int = 5) -> list[dict]:
    sus = ranking.get("suspects") or []
    if not sus:
        return []
    leader, review, groups = sus[0], ranking.get("skeptic"), ranking.get("alert_groups")
    out = []
    for i, s in enumerate(sus[:limit]):
        others = [o for o in sus if o is not s]
        sup, con = _evidence(s, ranking, others, groups, leader)
        inc = ranking["incident"]["id"]
        out.append({"id": f"HYP-{inc}-{i + 1}", "rank": i + 1, "label": tools._label(s), "service": s["service"],
                    "type": s["type"], "score": s["score"], "status": _status(s, review, leader),
                    "blames": _subject(s), "supports": sup, "contradicts": con})
    return out


def _first(alerts: list[dict], service: str, after: int) -> dict | None:
    return min((a for a in alerts if a["service"] == service and a["at"] >= after and a.get("id")),
               key=lambda a: a["at"], default=None)


def _chain(root: dict, explained: list[str], alerts: list[dict], down: dict[str, set[str]], confidence: float) -> list[dict]:
    """Link each explained alert to the alert below it that most plausibly set it off.

    Topology decides which links are possible (a failure travels from callee to caller); timing picks among them
    and sets the confidence. The root's alert points at the change, or at the root service itself when nothing
    changed there."""
    after = root["change"]["at"] if root["change"] else root.get("first_alert", 0) - 1
    svc = root["service"]
    first = {x: _first(alerts, x, after) for x in set(explained) | {svc}}
    first = {k: v for k, v in first.items() if v}
    links = []
    root_alert = first.get(svc)
    if root_alert:
        if root["change"]:
            links.append({"from": root_alert["id"], "from_service": svc, "to": root["change"]["id"], "to_label": "Change",
                          "to_service": svc, "lag_s": root_alert["at"] - root["change"]["at"], "direct": True,
                          "confidence": round(confidence, 3), "via": []})
        else:
            links.append({"from": root_alert["id"], "from_service": svc, "to": svc, "to_label": "Service",
                          "to_service": svc, "lag_s": 0, "direct": True, "confidence": round(confidence, 3), "via": []})
    # distance from each service down to the root, through CALLS edges
    dist, todo = {svc: 0}, [svc]
    up: dict[str, set[str]] = {}
    for a, bs in down.items():
        for b in bs:
            up.setdefault(b, set()).add(a)
    while todo:
        cur = todo.pop(0)
        for caller in up.get(cur, ()):
            if caller not in dist:
                dist[caller] = dist[cur] + 1
                todo.append(caller)
    # What a link can point at: an alerting service's first alert or, for a root that never alerted (databases
    # and queues often don't), the change itself or the silent service.
    anchor = {k: (v["id"], "Alert", v["at"]) for k, v in first.items()}
    if svc not in anchor:
        anchor[svc] = (root["change"]["id"], "Change", root["change"]["at"]) if root["change"] else (svc, "Service", after)
    for x in sorted(first, key=lambda k: (first[k]["at"], k)):
        if x == svc or x not in dist:
            continue
        mine = first[x]
        direct = [c for c in down.get(x, ()) if c in anchor and dist.get(c, 99) < dist[x]]
        cands = [(c, True) for c in direct]
        via: list[str] = []
        if not cands:
            # no alerting callee: walk down through silent services to the nearest alerting one
            cur, seen = x, {x}
            while True:
                nxt = sorted((c for c in down.get(cur, ()) if dist.get(c, 99) < dist[cur] and c not in seen),
                             key=lambda c: dist[c])
                if not nxt:
                    break
                cur = nxt[0]
                seen.add(cur)
                if cur in anchor:
                    cands = [(cur, False)]
                    break
                via.append(cur)
        if not cands:
            continue
        in_order = [(c, d) for c, d in cands if anchor[c][2] <= mine["at"]]
        pick, d = min(in_order or cands, key=lambda cd: (dist[cd[0]], anchor[cd[0]][2], cd[0]))
        to, to_label, at = anchor[pick]
        lag = mine["at"] - at
        conf = 0.95 if 0 <= lag <= LAG_TIGHT_S else 0.75 if lag >= 0 else 0.4
        if not d:
            conf *= 0.8
        links.append({"from": mine["id"], "from_service": x, "to": to, "to_label": to_label,
                      "to_service": pick, "lag_s": lag, "direct": d, "confidence": round(conf * confidence, 3),
                      "via": via if not d else []})
    return links


def causal_chain(g: Graph, ranking: dict) -> dict:
    """The causal chain for the hypothesis left standing, plus one for each independent fault the judge found."""
    sus = ranking.get("suspects") or []
    if not sus:
        return {"links": [], "roots": []}
    alerts = ranking.get("alerts") or []
    lead = sus[0]
    services = set(lead["footprint"]["explained"]) | {lead["service"]}
    others = []
    for o in ranking.get("other_faults") or []:
        match = next((s for s in sus if s["service"] == o["service"] and (s["change"] or {}).get("id") == (o["change"] or {}).get("id")), None)
        if match:
            others.append((match, o["explains"]))
            services |= set(o["explains"]) | {match["service"]}
    rows = g.read("""
        MATCH (a:Service)-[:CALLS]->(b:Service) WHERE a.name IN $s AND b.name IN $s
        RETURN a.name AS a, b.name AS b""", {"s": sorted(services | set(ranking.get("alert_cone") or []))})
    # silent services between two alerting ones still carry the failure: include the leader's path
    down: dict[str, set[str]] = {}
    for r in rows:
        down.setdefault(r["a"], set()).add(r["b"])
    path = lead.get("path") or []
    for a, b in zip(path, path[1:]):
        down.setdefault(a, set()).add(b)
    conf = ranking.get("confidence_p") or 0.5
    if lead["type"] == "host":
        # each service the host runs failed on its own: its first alert points at the node, and what each set off
        # chains up from there
        links, seen = [], set()
        for svc in lead["host"]["services"]:
            first = min((a["at"] for a in alerts if a["service"] == svc), default=0)
            for l in _chain({"service": svc, "change": None, "first_alert": first}, lead["footprint"]["explained"], alerts, down, conf):
                if l["from"] in seen:
                    continue
                seen.add(l["from"])
                if l["to_label"] == "Service" and l["to"] == svc:
                    l = {**l, "to": lead["service"], "to_label": "Node", "to_service": lead["service"]}
                links.append(l)
        return {"links": links, "roots": [{"label": tools._label(lead), "service": lead["service"], "links": len(links)}]}
    links = _chain(lead, lead["footprint"]["explained"], alerts, down, conf)
    roots = [{"label": tools._label(lead), "service": lead["service"], "links": len(links)}]
    for s, explains in others:
        extra = _chain(s, explains, alerts, down, conf * 0.8)
        known = {l["from"] for l in links}
        extra = [l for l in extra if l["from"] not in known]
        links += extra
        roots.append({"label": tools._label(s), "service": s["service"], "links": len(extra)})
    return {"links": links, "roots": roots}


def telemetry(g: Graph, ranking: dict) -> dict:
    """What the traces and logs say about this incident's alerting services, where the tenant has them: the
    endpoint that slowed most on each, and error log patterns that appeared with the incident."""
    ids = [a["id"] for a in ranking.get("alerts") or [] if a.get("id")]
    hot: dict[str, list[dict]] = {}
    for r in g.read("""
            MATCH (a:Alert)-[r:ON_OPERATION]->(o:Operation) WHERE a.id IN $ids
            RETURN a.id AS alert, o.service AS service, o.name AS op, o.id AS id, r.p90_before_ms AS before,
                   r.p90_after_ms AS after, r.ratio AS ratio ORDER BY r.ratio DESC""", {"ids": ids}):
        hot.setdefault(r["service"], []).append(r)
    logs: dict[str, list[dict]] = {}
    for r in g.read("""
            MATCH (l:LogPattern)-[s:SEEN_IN]->(:Incident {id: $inc}), (l)-[:LOGGED_BY]->(svc:Service)
            RETURN svc.name AS service, l.id AS id, l.template AS template, l.new AS new, s.count AS count,
                   s.first_at AS first_at ORDER BY l.new DESC, s.count DESC""", {"inc": ranking["incident"]["id"]}):
        logs.setdefault(r["service"], []).append(r)
    svcs = sorted({a["service"] for a in ranking.get("alerts") or []})
    limits = {r["service"]: r for r in g.read("""
        MATCH (s:Service)-[:DEPLOYED_AS]->(d:Deployment) WHERE s.name IN $s
        RETURN s.name AS service, d.name AS deployment, d.replicas AS replicas, d.cpu_limit AS cpu_limit,
               d.mem_limit_mi AS mem_limit_mi""", {"s": svcs})}
    return {"hot_ops": hot, "logs": logs, "limits": limits}


def build(g: Graph, ranking: dict) -> dict:
    ranking["telemetry"] = telemetry(g, ranking)
    return {"hypotheses": hypotheses(ranking), "chain": causal_chain(g, ranking), "telemetry": ranking["telemetry"]}


def write(g: Graph, incident_id: str, reasoning: dict) -> dict:
    """Replace this incident's stored argument and causal chain with `reasoning`."""
    g.write("""
        MATCH (:Incident {id: $inc})-[:HAS_HYPOTHESIS]->(h:Hypothesis)
        OPTIONAL MATCH (h)-[:SUPPORTED_BY|CONTRADICTED_BY]->(e:Evidence)
        DETACH DELETE h, e""", {"inc": incident_id})
    g.write("MATCH ()-[r:CAUSED_BY {incident: $inc}]->() DELETE r", {"inc": incident_id})
    hyps, ev = [], []
    for h in reasoning["hypotheses"]:
        hyps.append({k: h[k] for k in ("id", "rank", "label", "service", "type", "score", "status")}
                    | {"blames_label": h["blames"]["label"], "blames_id": h["blames"]["id"]})
        for pol, items in (("SUPPORTED_BY", h["supports"]), ("CONTRADICTED_BY", h["contradicts"])):
            for j, e in enumerate(items):
                ev.append({"h": h["id"], "id": f"{h['id']}-{pol[0]}{j + 1}", "rel": pol, "kind": e["kind"],
                           "text": e["text"], "weight": e["weight"], "cites": e["cites"]})
    g.write("""
        MATCH (i:Incident {id: $inc})
        UNWIND $hyps AS h
        CREATE (i)-[:HAS_HYPOTHESIS]->(x:Hypothesis {id: h.id, rank: h.rank, label: h.label, service: h.service,
                                                     type: h.type, score: h.score, status: h.status})
        WITH x, h
        OPTIONAL MATCH (c:Change {id: h.blames_id}) WHERE h.blames_label = 'Change'
        OPTIONAL MATCH (s:Service {name: h.blames_id}) WHERE h.blames_label = 'Service'
        OPTIONAL MATCH (n:Node {name: h.blames_id}) WHERE h.blames_label = 'Node'
        WITH x, coalesce(c, s, n) AS t WHERE t IS NOT NULL
        CREATE (x)-[:BLAMES]->(t)""", {"inc": incident_id, "hyps": hyps})
    for rel in ("SUPPORTED_BY", "CONTRADICTED_BY"):
        rows = [e for e in ev if e["rel"] == rel]
        if rows:
            g.write(f"""
                UNWIND $ev AS e MATCH (h:Hypothesis {{id: e.h}})
                CREATE (h)-[:{rel}]->(:Evidence {{id: e.id, kind: e.kind, text: e.text, weight: e.weight}})""", {"ev": rows})
    cites = [{"e": e["id"], **c} for e in ev for c in e["cites"]]
    for label, key in (("Alert", "id"), ("Change", "id"), ("Incident", "id"), ("Service", "name"), ("Operation", "id"),
                       ("LogPattern", "id"), ("Node", "name"), ("Deployment", "name")):
        rows = [c for c in cites if c["label"] == label]
        if rows:
            g.write(f"""
                UNWIND $c AS c MATCH (e:Evidence {{id: c.e}}), (t:{label} {{{key}: c.id}})
                MERGE (e)-[:CITES]->(t)""", {"c": rows})
    links = reasoning["chain"]["links"]
    for label, key in (("Alert", "id"), ("Change", "id"), ("Service", "name"), ("Node", "name")):
        rows = [l for l in links if l["to_label"] == label]
        if rows:
            g.write(f"""
                UNWIND $l AS l MATCH (a:Alert {{id: l.from}}), (t:{label} {{{key}: l.to}})
                CREATE (a)-[:CAUSED_BY {{incident: $inc, confidence: l.confidence, lag_s: l.lag_s, direct: l.direct,
                                          via: l.via}}]->(t)""", {"l": rows, "inc": incident_id})
    return {"hypotheses": len(hyps), "evidence": len(ev), "citations": len(cites), "causal_links": len(links)}


def argument(g: Graph, incident_id: str) -> list[dict]:
    """The stored argument for an incident, read back from the graph: each hypothesis with its evidence."""
    rows = g.read("""
        MATCH (:Incident {id: $inc})-[:HAS_HYPOTHESIS]->(h:Hypothesis)
        OPTIONAL MATCH (h)-[r:SUPPORTED_BY|CONTRADICTED_BY]->(e:Evidence)
        OPTIONAL MATCH (e)-[:CITES]->(t)
        WITH h, r, e, collect(coalesce(t.id, t.name)) AS cites
        ORDER BY e.id
        RETURN h.id AS id, h.rank AS rank, h.label AS label, h.status AS status, h.score AS score,
               collect(CASE WHEN e IS NULL THEN NULL ELSE {polarity: type(r), kind: e.kind, text: e.text, cites: cites} END) AS evidence
        ORDER BY rank""", {"inc": incident_id})
    for r in rows:
        r["supports"] = [e for e in r["evidence"] if e and e["polarity"] == "SUPPORTED_BY"]
        r["contradicts"] = [e for e in r["evidence"] if e and e["polarity"] == "CONTRADICTED_BY"]
        del r["evidence"]
    return rows


def why(g: Graph, alert_id: str) -> dict:
    """Follow an alert's CAUSED_BY links down to where it started."""
    rows = g.read("""
        MATCH p = (a:Alert {id: $id})-[:CAUSED_BY*1..12]->(root)
        WHERE NOT (root)-[:CAUSED_BY]->()
        WITH p ORDER BY length(p) DESC LIMIT 1
        UNWIND range(0, length(p) - 1) AS k
        WITH nodes(p)[k] AS x, relationships(p)[k] AS r, nodes(p)[k + 1] AS y
        OPTIONAL MATCH (x)-[:FIRED_ON]->(xs:Service)
        RETURN x.id AS alert, xs.name AS service, x.signal AS signal, x.at AS at, r.lag_s AS lag_s,
               r.confidence AS confidence, r.direct AS direct, r.via AS via,
               labels(y)[0] AS to_label, coalesce(y.id, y.name) AS to, y.summary AS to_summary""", {"id": alert_id})
    if not rows:
        return {"alert": alert_id, "chain": [], "root": None}
    last = rows[-1]
    return {"alert": alert_id, "chain": rows, "root": {"label": last["to_label"], "id": last["to"], "summary": last["to_summary"]}}
