"""Live input: changes pushed by CI/CD and alerts pushed by monitoring.

Nothing is polled. A deploy pipeline (or a config/flag service) posts each change as it lands; Alertmanager, or
Grafana alerting in the same format, posts alerts as they fire and resolve. Hypocenter writes both into the graph,
opens an incident when the opening rule says so, and has the agents investigate it straight away. Resolved alerts
set `cleared_at`, which is exactly what `verify_fix` reads, so a fix is verified by real recovery.
"""
from __future__ import annotations

import hashlib
import re
import threading
from datetime import datetime

from . import tools
from .db import Graph

SEVERITIES = {"page", "critical", "warning", "info"}
# Opening rule: a paging alert on a tier-1 or internet-facing service, with no unresolved incident whose
# symptom already covers it (the service is the symptom or sits below it) within this many minutes.
ATTACH_WINDOW_MIN = 60


def _ts(v) -> int:
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip().replace("Z", "+00:00")
    return int(datetime.fromisoformat(s).timestamp())


def _next_id(g: Graph, label: str, prefix: str, floor: int) -> str:
    rows = g.read(f"MATCH (n:{label}) WHERE n.id STARTS WITH $p RETURN n.id AS id", {"p": prefix})
    nums = [int(m.group(1)) for r in rows if (m := re.fullmatch(re.escape(prefix) + r"(\d+)", r["id"] or ""))]
    return f"{prefix}{max([floor - 1, *nums]) + 1}"


def _services(g: Graph) -> dict[str, dict]:
    return {r["name"]: r for r in g.read(
        "MATCH (s:Service) RETURN s.name AS name, s.tier AS tier, s.internet_exposed AS exposed")}


# --- changes from CI/CD ---------------------------------------------------------------------------------

def record_change(g: Graph, kind: str, service: str, summary: str, at, author: str | None = None,
                  version: str | None = None, key: str | None = None, old: str | None = None,
                  new: str | None = None, risk: str = "medium", change_id: str | None = None) -> dict:
    if kind not in ("deploy", "config", "flag"):
        raise ValueError("kind must be deploy, config or flag")
    if service not in _services(g):
        raise KeyError(f"no service {service}")
    prefix = {"deploy": "D-", "config": "C-", "flag": "F-"}[kind]
    cid = change_id or _next_id(g, "Change", prefix, 6001)
    if not re.fullmatch(r"[CDF]-\d{1,8}", cid):
        raise ValueError("change id must look like D-6001")
    if g.read("MATCH (c:Change {id: $id}) RETURN c.id AS id", {"id": cid}):
        return {"id": cid, "created": False}
    g.write("""
        MATCH (s:Service {name: $svc})
        OPTIONAL MATCH (p:Person {handle: $author})
        CREATE (c:Change {id: $id, kind: $kind, at: $at, summary: $summary, risk: $risk, version: $version,
                          key: $key, old: $old, new: $new, source: 'ci'})-[:CHANGED]->(s)
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:MADE]->(c))""",
            {"svc": service, "author": author, "id": cid, "kind": kind, "at": _ts(at), "summary": summary,
             "risk": risk if risk in ("low", "medium", "high") else "medium", "version": version, "key": key,
             "old": old, "new": new})
    return {"id": cid, "created": True}


# --- alerts from Alertmanager / Grafana ---------------------------------------------------------------------

def _alert_id(a: dict) -> str:
    # Alertmanager's fingerprint identifies the alert across firing and resolved posts; without one, the labels
    # and start time do. Hashed to 8 characters so it reads like the other ids (A-1F3C09B2).
    key = a.get("fingerprint") or repr(sorted((a.get("labels") or {}).items())) + str(a.get("startsAt"))
    return "A-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper()


def _service_of(labels: dict) -> str | None:
    for k in ("service", "app", "job", "service_name"):
        if labels.get(k):
            return str(labels[k])
    return None


def _covering_incident(g: Graph, svc: str, at: int) -> dict | None:
    """An unresolved incident this alert belongs to: its symptom is the service, or the service is below it."""
    for inc in tools.list_incidents(g, None):
        if inc["status"] == "resolved" or abs(inc["started"] - at) > ATTACH_WINDOW_MIN * 60:
            continue
        below = {d["service"] for d in tools.dependencies(g, inc["symptom"])}
        if svc == inc["symptom"] or svc in below:
            return inc
    return None


def ingest_alertmanager(g: Graph, payload: dict) -> dict:
    """Write each alert (idempotent on its fingerprint), clear resolved ones, then apply the opening rule.
    Returns what changed and which incidents were opened, so one curl shows the whole story."""
    svcs = _services(g)
    created, updated, ignored, firing = [], [], [], []
    for a in payload.get("alerts", []):
        labels = a.get("labels") or {}
        svc = _service_of(labels)
        if svc not in svcs:
            ignored.append({"alert": labels.get("alertname"), "service": svc, "reason": "not a service in this graph"})
            continue
        aid = _alert_id(a)
        sev = str(labels.get("severity", "page")).lower()
        sev = "page" if sev in ("critical", "page") else sev if sev in SEVERITIES else "warning"
        signal = (a.get("annotations") or {}).get("summary") or labels.get("alertname") or "alert"
        at = _ts(a.get("startsAt"))
        ends = a.get("endsAt")
        resolved = a.get("status") == "resolved" or payload.get("status") == "resolved" and a.get("status") != "firing"
        cleared = _ts(ends) if resolved and ends and not str(ends).startswith("0001") else None
        existed = g.read("MATCH (x:Alert {id: $id}) RETURN x.id AS id", {"id": aid})
        g.write("""
            MATCH (s:Service {name: $svc})
            MERGE (x:Alert {id: $id})
            ON CREATE SET x.at = $at, x.severity = $sev, x.signal = $signal, x.source = 'alertmanager'
            SET x.cleared_at = CASE WHEN $cleared IS NULL THEN x.cleared_at ELSE $cleared END
            MERGE (x)-[:FIRED_ON]->(s)""",
                {"svc": svc, "id": aid, "at": at, "sev": sev, "signal": str(signal)[:200], "cleared": cleared})
        (updated if existed else created).append(aid)
        if not resolved and not existed and sev == "page":
            firing.append({"id": aid, "service": svc, "at": at, "signal": str(signal)[:200]})

    opened, attached = [], []
    for a in firing:
        inc = _covering_incident(g, a["service"], a["at"])
        if inc:
            attached.append({"alert": a["id"], "incident": inc["id"]})
    new = [a for a in firing if not any(x["alert"] == a["id"] for x in attached)]
    if new:
        alerting = {a["service"] for a in new}
        # the symptom is the paging service highest up: the one with the most of the new alerts below it
        def above(svc):
            below = {d["service"] for d in tools.dependencies(g, svc)}
            return len(below & alerting)
        cands = [a for a in new if svcs[a["service"]]["tier"] == 1 or svcs[a["service"]]["exposed"]]
        if cands:
            sym = max(cands, key=lambda a: (above(a["service"]), a["at"]))
            iid = _next_id(g, "Incident", "INC-", 501)
            exposed = svcs[sym["service"]]["exposed"]
            g.write("""
                MATCH (s:Service {name: $svc})
                CREATE (:Incident {id: $id, title: $title, started: $at, status: 'open', severity: $sev,
                                   source: 'alertmanager'})-[:SYMPTOM_ON]->(s)""",
                    {"svc": sym["service"], "id": iid, "title": f"{sym['service']}: {sym['signal']}"[:120],
                     "at": sym["at"], "sev": "SEV1" if exposed else "SEV2"})
            opened.append(iid)
            attached += [{"alert": a["id"], "incident": iid} for a in new]

    # a resolved alert can be the recovery a fix was waiting for
    reverified = []
    if any(True for a in payload.get("alerts", []) if a.get("status") == "resolved"):
        for r in g.read("""
                MATCH (a:Action {status: 'executed'})-[:FOR]->(i:Incident)
                WHERE i.status <> 'resolved' AND (a.verified IS NULL OR a.verified = false)
                RETURN DISTINCT a.id AS id"""):
            reverified.append(tools.verify_fix(g, r["id"]))
    return {"created": created, "updated": updated, "ignored": ignored, "opened": opened,
            "attached": attached, "reverified": reverified}


# --- one investigation per incident at a time -------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()


class Busy(Exception):
    pass


def exclusive(tenant: str, incident_id: str):
    """Context manager: refuse a second investigation of the same incident while one runs."""
    with _guard:
        lock = _locks.setdefault(f"{tenant}/{incident_id}", threading.Lock())
    if not lock.acquire(blocking=False):
        raise Busy(f"{incident_id} is already being investigated")

    class _Held:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            lock.release()
    return _Held()
