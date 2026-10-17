"""Bring your own incident: an uploaded file becomes its own tenant graph, and the judge reads it at once.

Two formats:
  JSON   Faultline's own incident format (web/examples/incident.json): services, calls (or spans to derive them
         from), owners, changes, alerts and the incident. Anything with deploys in it gets the full change-based
         judge.
  ZIP    one case in RCAEval's layout: inject_time.txt plus metrics and traces as parquet or CSV. The call graph
         comes from the spans and the alerts from the same spike detector the benchmark uses. A trace export has
         no deploys in it, so every answer is a "nothing changed" infrastructure suspect.

Uploads are untrusted: sizes and counts are capped, names validated, and a zip is read member by member (only the
expected file names, no paths, checked uncompressed sizes). At most MAX_TENANTS uploads are kept; the oldest
goes first.
"""
from __future__ import annotations

import io
import json
import re
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

from . import seed, tools
from .db import Graph, list_tenants
from .ingest import _ts

PREFIX = "up_"
MAX_TENANTS = 8
MAX_JSON_BYTES = 2_000_000
MAX_ZIP_BYTES = 40_000_000
MAX_UNZIPPED = 400_000_000
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}")
ZIP_MEMBERS = {"inject_time.txt", "metrics.parquet", "metrics.csv", "traces.parquet", "traces.csv"}
LIMITS = {"services": 300, "calls": 3000, "changes": 2000, "alerts": 5000, "spans": 200_000, "people": 300}

_busy = threading.Lock()


class Busy(Exception):
    pass


def is_upload(tenant: str) -> bool:
    return tenant.startswith(PREFIX) and tenant in list_tenants()


def uploaded() -> list[dict]:
    out = []
    for t in list_tenants():
        if not t.startswith(PREFIX):
            continue
        org = Graph(t).read("MATCH (o:Org) RETURN o.title AS title, o.created AS created, o.format AS format")
        if org:
            out.append({"id": t, **org[0]})
    return sorted(out, key=lambda o: -(o["created"] or 0))


def _evict() -> None:
    for o in uploaded()[MAX_TENANTS - 1:]:
        Graph(o["id"]).drop()


def _name(v, what: str) -> str:
    s = str(v or "").strip()
    if not NAME.fullmatch(s):
        raise ValueError(f"bad {what} name {s[:40]!r}: letters, digits and _ . : - only, up to 80")
    return s


def _cap(spec: dict, key: str) -> list:
    v = spec.get(key) or []
    if not isinstance(v, list):
        raise ValueError(f"{key} must be a list")
    if len(v) > LIMITS[key]:
        raise ValueError(f"too many {key}: {len(v)} (limit {LIMITS[key]})")
    return v


def from_json(spec: dict) -> dict:
    """Validate Faultline's incident JSON into the shapes the seed writers take."""
    if not isinstance(spec, dict):
        raise ValueError("the JSON must be an object")
    raw = _cap(spec, "services")
    if not raw:
        raise ValueError("services is empty")
    services = []
    for s in raw:
        s = {"name": s} if isinstance(s, str) else s
        services.append({"name": _name(s.get("name"), "service"), "team": _name(s.get("team") or "unowned", "team"),
                         "kind": s.get("kind") if s.get("kind") in ("service", "datastore", "external") else "service",
                         "tier": int(s.get("tier") or 2), "exposed": bool(s.get("exposed"))})
    names = {s["name"] for s in services}
    if len(names) != len(services):
        raise ValueError("service names must be unique")
    calls = [(_name(a, "service"), _name(b, "service")) for a, b in _cap(spec, "calls")]
    spans = _cap(spec, "spans")
    if spans:   # derive CALLS from parent/child spans, like the RCAEval runner
        by_id = {str(x.get("span_id")): x.get("service") for x in spans}
        calls += [(by_id[str(x["parent_id"])], x["service"]) for x in spans
                  if x.get("parent_id") and str(x["parent_id"]) in by_id and by_id[str(x["parent_id"])] != x.get("service")]
    calls = sorted(set(calls))
    bad = [c for c in calls if c[0] not in names or c[1] not in names]
    if bad:
        raise ValueError(f"calls name unknown services: {bad[:3]}")
    teams = sorted({s["team"] for s in services})
    people = []
    for p in _cap(spec, "people"):
        team = _name(p.get("team"), "team")
        if team not in teams:
            raise ValueError(f"person {p.get('name')} is in unknown team {team}")
        people.append((_name(p.get("handle"), "person"), str(p.get("name") or p.get("handle"))[:80], team,
                       bool(p.get("on_call"))))
    changes = []
    counters = {"deploy": 0, "config": 0, "flag": 0}
    for c in _cap(spec, "changes"):
        kind = c.get("kind")
        if kind not in counters:
            raise ValueError("a change's kind must be deploy, config or flag")
        if c.get("service") not in names:
            raise ValueError(f"change on unknown service {c.get('service')}")
        counters[kind] += 1
        cid = c.get("id") or f"{kind[0].upper()}-{100 + counters[kind] + len(changes)}"
        if not re.fullmatch(r"[CDF]-\d{1,8}", str(cid)):
            raise ValueError(f"change id {cid} must look like D-101, C-102 or F-103")
        changes.append({"id": cid, "kind": kind, "service": c["service"], "at": _ts(c["at"]),
                        "summary": str(c.get("summary") or f"{kind} on {c['service']}")[:300],
                        "author": c.get("author"), "version": c.get("version"), "key": c.get("key"),
                        "old": c.get("old"), "new": c.get("new"),
                        "risk": c.get("risk") if c.get("risk") in ("low", "medium", "high") else "medium"})
    alerts = []
    for i, a in enumerate(_cap(spec, "alerts")):
        if a.get("service") not in names:
            raise ValueError(f"alert on unknown service {a.get('service')}")
        alerts.append({"id": f"A-{i + 1}", "service": a["service"], "at": _ts(a["at"]),
                       "severity": a.get("severity") or "page", "signal": str(a.get("signal") or "alert")[:200]})
    alerts.sort(key=lambda a: a["at"])
    inc = spec.get("incident") or {}
    sym = inc.get("symptom")
    if sym not in names:
        raise ValueError("incident.symptom must be one of the services")
    first = [a["at"] for a in alerts if a["service"] == sym] or [a["at"] for a in alerts]
    started = _ts(inc["started"]) if inc.get("started") else (first[0] if first else None)
    if started is None:
        raise ValueError("incident.started is needed when there are no alerts")
    return {"title": str(spec.get("title") or "Uploaded incident")[:80], "services": services, "calls": calls,
            "teams": teams, "people": people, "changes": changes, "alerts": alerts,
            "incident": {"id": "INC-1", "title": str(inc.get("title") or f"{sym} degraded")[:120], "symptom": sym,
                         "started": started, "status": "open", "severity": inc.get("severity") or "SEV2"}}


def from_zip(data: bytes, filename: str) -> dict:
    """An RCAEval-layout case: the same steps as the benchmark runner, on the uploaded files."""
    from . import rcaeval
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ValueError("not a zip file")
    members = [m for m in zf.infolist() if not m.is_dir()]
    if len(members) > 20:
        raise ValueError("too many files in the zip")
    wanted = {}
    for m in members:
        base = m.filename.rsplit("/", 1)[-1]
        if m.filename.startswith("/") or ".." in m.filename.split("/") or base not in ZIP_MEMBERS:
            continue
        wanted[base] = m
    if sum(m.file_size for m in wanted.values()) > MAX_UNZIPPED:
        raise ValueError("the zip unpacks to more than 400 MB")
    if "inject_time.txt" not in wanted or not ({"metrics.parquet", "metrics.csv"} & set(wanted)) \
            or not ({"traces.parquet", "traces.csv"} & set(wanted)):
        raise ValueError("expected inject_time.txt, metrics.(parquet|csv) and traces.(parquet|csv)")
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for base, m in wanted.items():
            (d / base).write_bytes(zf.read(m))
        case = rcaeval.load_case(d)
    services = sorted({s for e in case["edges"] for s in e} | {a["service"] for a in case["alerts"]})
    if not services:
        raise ValueError("no services found in the traces or metrics")
    alerting = [a["service"] for a in case["alerts"]]
    callers = {b for _, b in case["edges"]}
    sym = rcaeval.SYMPTOM if rcaeval.SYMPTOM in services else next(
        (s for s in alerting if s not in callers), alerting[0] if alerting else services[0])
    alerts = [{"id": f"A-{i + 1}", "service": a["service"], "at": a["at"], "severity": "page", "signal": a["signal"]}
              for i, a in enumerate(case["alerts"])]
    first = [a["at"] for a in alerts if a["service"] == sym] or [a["at"] for a in alerts] or [case["inject"]]
    return {"title": re.sub(r"\.zip$", "", filename)[:80] or "Uploaded RCAEval case",
            "services": [{"name": s, "team": "uploaded", "kind": "datastore" if s in ("redis", "mysql", "postgres", "mongodb") else "service",
                          "tier": 1, "exposed": s == sym} for s in services],
            "calls": case["edges"], "teams": ["uploaded"], "people": [], "changes": [], "alerts": alerts,
            "incident": {"id": "INC-1", "title": f"{sym} degraded · {len(alerts)} services alerting", "symptom": sym,
                         "started": first[0], "status": "open", "severity": "SEV2"}}


def build(spec: dict, fmt: str) -> dict:
    tenant = PREFIX + uuid.uuid4().hex[:8]
    g = Graph(tenant)
    seed._write_indexes(g)
    seed._write_org(g, {"title": spec["title"], "teams": {t: t for t in spec["teams"]}, "people": spec["people"],
                        "services": [(s["name"], s["team"], s["kind"], s["tier"], s["exposed"]) for s in spec["services"]],
                        "calls": spec["calls"]})
    g.write("MATCH (o:Org) SET o.created = $t, o.format = $f", {"t": int(time.time()), "f": fmt})
    if spec["changes"]:
        seed._write_changes(g, spec["changes"])
    if spec["alerts"]:
        seed._write_alerts(g, spec["alerts"])
    seed._write_incidents(g, [spec["incident"]])
    return {"tenant": tenant, "incident": spec["incident"]["id"]}


def handle(data: bytes, filename: str, content_type: str) -> dict:
    """Parse, build the tenant, let the judge read it. One upload at a time."""
    if not _busy.acquire(blocking=False):
        raise Busy("another upload is being processed")
    try:
        is_zip = data[:4] == b"PK\x03\x04" or filename.lower().endswith(".zip")
        if is_zip:
            if len(data) > MAX_ZIP_BYTES:
                raise ValueError("zip larger than 40 MB")
            spec, fmt = from_zip(data, filename), "rcaeval zip"
        else:
            if len(data) > MAX_JSON_BYTES:
                raise ValueError("JSON larger than 2 MB")
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValueError(f"not valid JSON: {e}")
            spec, fmt = from_json(parsed), "faultline json"
        _evict()
        out = build(spec, fmt)
        from .agent import judged
        r = judged(Graph(out["tenant"]), out["incident"])
        top = r["suspects"][0] if r["suspects"] else None
        return {**out, "title": spec["title"], "format": fmt, "services": len(spec["services"]),
                "calls": len(spec["calls"]), "changes": len(spec["changes"]), "alerts": len(spec["alerts"]),
                "leader": tools._label(top) if top else None, "confidence": r["confidence"],
                "no_changes": not spec["changes"]}
    finally:
        _busy.release()
