"""Faultline on public data: RCAEval RE2-OB (Online Boutique), real metrics and traces with a known root cause.

    docker compose run --rm app python -m faultline.rcaeval --download     # ~190 MB, 18 cases, into data/rcaeval
    docker compose run --rm app python -m faultline.rcaeval                # build each case's graph, run the judge

Dataset: https://huggingface.co/datasets/phamquiluan/RCAEval (MIT), paper arXiv:2412.17015.

For every case:
  graph   one Service per service seen in the traces or metrics; CALLS from a parent span's service to its
          child span's service (seen at least MIN_CALLS times). redis gets cartservice -> redis, the one
          dependency the traces don't show (it is not instrumented).
  alerts  simple spike detection on each service's metrics (cpu, mem, diskio, socket, latency p50/p90, error):
          robust z-score against the normal period before the injection, alert when it stays above Z_ALERT
          for SUSTAIN_S seconds. One Alert per service at its earliest onset.
  incident on `frontend`, the user-facing entry point, opened at frontend's first alert (or the first alert
          anywhere if frontend never alerts).
Then the unchanged judge ranks suspects and we compare its top answer with the case's root_cause_service.

The detector settings below were fixed before looking at any result. The injection time is used only to split
"normal" from "after", which is how RCAEval's own baselines are run.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

from . import tools
from .db import Graph
from .seed import BOUTIQUE_MANIFEST_EDGES, _write_alerts, _write_incidents, _write_indexes

DATA = Path(__file__).resolve().parent.parent / "data" / "rcaeval"
HF = "https://huggingface.co/datasets/phamquiluan/RCAEval/resolve/main"
FAULTS = ["cpu", "mem", "disk", "delay", "loss", "socket"]
SERVICES = ["checkoutservice", "currencyservice", "emailservice", "productcatalogservice", "recommendationservice"]

# Detector settings, fixed up front.
Z_ALERT = 5.0          # robust z-score that counts as anomalous
SUSTAIN_S = 30         # ...for at least this many consecutive seconds
METRICS = ("cpu", "mem", "diskio", "socket", "latency-50", "latency-90", "error")
MIN_CALLS = 5          # parent->child span pairs needed before we draw a CALLS edge
SYMPTOM = "frontend"


def selection(rep: int = 1) -> list[str]:
    """18 cases: every fault type on three different services (rotating), one repetition. Not hand-picked."""
    out = []
    for i, f in enumerate(FAULTS):
        for k in range(3):
            out.append(f"re2ob_{SERVICES[(i + k) % len(SERVICES)]}_{f}_{rep}")
    return out


def download(cases: list[str]) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    if not (DATA / "cases.parquet").exists():
        urllib.request.urlretrieve(f"{HF}/cases.parquet", DATA / "cases.parquet")
    for c in cases:
        (DATA / c).mkdir(exist_ok=True)
        for f in ("inject_time.txt", "metrics.parquet", "traces.parquet"):
            dest = DATA / c / f
            if not dest.exists() or dest.stat().st_size == 0:
                urllib.request.urlretrieve(f"{HF}/{c}/{f}", dest)
        print("downloaded", c)


def download_suite(prefixes: list[str]) -> None:
    """Every case whose name starts with one of `prefixes` (e.g. re2ss, re3tt), with whatever files RCAEval has
    for it (metrics, and logs and traces where the suite recorded them). Resumable: finished files are skipped."""
    DATA.mkdir(parents=True, exist_ok=True)
    if not (DATA / "cases.parquet").exists():
        urllib.request.urlretrieve(f"{HF}/cases.parquet", DATA / "cases.parquet")
    idx = pd.read_parquet(DATA / "cases.parquet")
    api = HF.replace("/datasets/", "/api/datasets/").replace("/resolve/main", "/tree/main")
    for case in sorted(c for c in idx.case if c.startswith(tuple(prefixes))):
        (DATA / case).mkdir(exist_ok=True)
        with urllib.request.urlopen(f"{api}/{case}") as r:
            files = [(f["path"].split("/", 1)[1], f.get("size") or 0) for f in json.load(r) if f["type"] == "file"]
        for name, size in files:
            dest = DATA / case / name
            if dest.exists() and dest.stat().st_size == size:
                continue
            tmp = dest.with_suffix(dest.suffix + ".part")
            urllib.request.urlretrieve(f"{HF}/{case}/{name}", tmp)
            tmp.rename(dest)
        print("downloaded", case, ", ".join(n for n, _ in files), flush=True)


def _norm(name: str) -> str:
    return "frontend" if name in ("frontendservice", "frontend-external") else name


def call_edges(traces: pd.DataFrame) -> list[tuple[str, str]]:
    """Service-level CALLS edges from span parent/child links."""
    spans = traces[["spanID", "parentSpanID", "serviceName"]].copy()
    spans["serviceName"] = spans["serviceName"].map(_norm)
    joined = spans.merge(spans, left_on="parentSpanID", right_on="spanID", suffixes=("_child", "_parent"))
    pairs = joined[joined.serviceName_child != joined.serviceName_parent]
    counts = pairs.groupby(["serviceName_parent", "serviceName_child"]).size()
    edges = [(a, b) for (a, b), n in counts.items() if n >= MIN_CALLS]
    return sorted(set(edges) | {("cartservice", "redis")})


def detect(metrics: pd.DataFrame, inject: int, norm=None, skip: frozenset = frozenset()) -> list[dict]:
    """Earliest sustained anomaly per service. Returns alerts sorted by onset."""
    norm = norm or _norm
    normal = metrics[metrics.time < inject]
    after = metrics[metrics.time >= inject]
    best: dict[str, dict] = {}
    for col in metrics.columns:
        if col == "time" or "_" not in col:
            continue
        svc, kind = col.rsplit("_", 1)
        if kind not in METRICS:
            continue
        svc = norm(svc)
        if svc in skip:
            continue
        base = normal[col].astype(float).to_numpy()
        med = np.nanmedian(base)
        mad = np.nanmedian(np.abs(base - med)) * 1.4826
        scale = max(mad, 1e-6, abs(med) * 0.01)       # flat baselines: a 1% floor stops division blow-ups
        z = (after[col].astype(float).to_numpy() - med) / scale
        hot = np.nan_to_num(z, nan=0.0) > Z_ALERT
        run, onset = 0, None
        for i, h in enumerate(hot):
            run = run + 1 if h else 0
            if run >= SUSTAIN_S:
                onset = int(after.time.iloc[i - SUSTAIN_S + 1])
                break
        if onset is None:
            continue
        peak = float(np.nanmax(z))
        if svc not in best or onset < best[svc]["at"]:
            best[svc] = {"service": svc, "at": onset, "signal": f"{kind} z={peak:.0f}", "z": peak, "metric": col}
    return sorted(best.values(), key=lambda a: a["at"])


def _table(d: Path, stem: str, columns: list[str] | None = None) -> pd.DataFrame:
    if (d / f"{stem}.parquet").exists():
        return pd.read_parquet(d / f"{stem}.parquet", columns=columns)
    return pd.read_csv(d / f"{stem}.csv", usecols=columns)


def load_case(d: Path) -> dict:
    """One RCAEval-layout case directory (inject_time.txt, metrics and traces as parquet or CSV) boiled down to
    what the graph needs: call edges from the spans and the detector's alerts."""
    inject = int((d / "inject_time.txt").read_text().strip().split(".")[0])
    metrics = _table(d, "metrics")
    traces = _table(d, "traces", ["spanID", "parentSpanID", "serviceName"])
    return {"inject": inject, "edges": call_edges(traces), "alerts": detect(metrics, inject)}


def build(case: str) -> dict:
    """Load one case into its own tenant graph. Returns what the judge needs plus the answer key."""
    d = DATA / case
    c = load_case(d)
    inject, edges, alerts = c["inject"], c["edges"], c["alerts"]
    services = sorted({s for e in edges for s in e} | {a["service"] for a in alerts} | {SYMPTOM})

    g = Graph(f"rca_{abs(hash(case)) % 10**8}")
    g.drop()
    _write_indexes(g)
    g.write("CREATE (:Org {title: $t}), (:Team {id: 'ob', name: 'Online Boutique'})", {"t": case})
    g.write("""
        UNWIND $svcs AS s MATCH (t:Team {id: 'ob'})
        CREATE (:Service {name: s, kind: CASE WHEN s = 'redis' THEN 'datastore' ELSE 'service' END, tier: 1,
                          internet_exposed: s = 'frontend'})-[:OWNED_BY]->(t)""", {"svcs": services})
    g.write("""
        UNWIND $edges AS e MATCH (a:Service {name: e[0]}), (b:Service {name: e[1]})
        CREATE (a)-[:CALLS]->(b)""", {"edges": [list(e) for e in edges]})
    _write_alerts(g, [{"id": f"A-{i}", "service": a["service"], "at": a["at"], "severity": "page",
                       "signal": a["signal"]} for i, a in enumerate(alerts)])
    front = next((a["at"] for a in alerts if a["service"] == SYMPTOM), None)
    started = front if front is not None else (alerts[0]["at"] if alerts else inject)
    _write_incidents(g, [{"id": "INC-1", "title": case, "symptom": SYMPTOM, "started": started,
                          "status": "open", "severity": "SEV2"}])
    return {"graph": g, "inject": inject, "alerts": alerts, "edges": edges}


def run(cases: list[str]) -> dict:
    idx = pd.read_parquet(DATA / "cases.parquet").set_index("case")
    rows = []
    for case in cases:
        truth = idx.loc[case, "root_cause_service"]
        b = build(case)
        g = b["graph"]
        r = tools.rank_suspects(g, "INC-1", limit=50)
        review = tools.skeptic_review(r)
        names = lambda order: [s["service"] for s in order]  # noqa: E731
        judge, skeptic = names(r["suspects"]), names(review["suspects"])
        earliest = [a["service"] for a in b["alerts"] if a["service"] != SYMPTOM]
        loudest = [a["service"] for a in sorted(b["alerts"], key=lambda a: -a["z"]) if a["service"] != SYMPTOM]
        rows.append({
            "case": case, "fault": idx.loc[case, "fault"], "truth": truth,
            "judge": judge[:3], "skeptic": skeptic[:3], "confidence": review["confidence"],
            "alerting": [a["service"] for a in b["alerts"]], "truth_alerted": truth in {a["service"] for a in b["alerts"]},
            "hit": {"judge": judge[:1] == [truth], "judge+skeptic": skeptic[:1] == [truth],
                    "earliest alert": earliest[:1] == [truth], "biggest anomaly": loudest[:1] == [truth]},
            "hit3": {"judge": truth in judge[:3], "judge+skeptic": truth in skeptic[:3],
                     "earliest alert": truth in earliest[:3], "biggest anomaly": truth in loudest[:3]},
        })
        g.drop()
        print(f"{case:42} truth={truth:22} judge={','.join(judge[:3]) or '-':50} {'OK' if rows[-1]['hit']['judge+skeptic'] else '--'}")
    n = len(rows)
    methods = ["judge+skeptic", "judge", "earliest alert", "biggest anomaly"]
    return {"n": n, "rows": rows,
            "top1": {m: sum(r["hit"][m] for r in rows) for m in methods},
            "top3": {m: sum(r["hit3"][m] for r in rows) for m in methods},
            "detected": sum(r["truth_alerted"] for r in rows),
            "settings": {"Z_ALERT": Z_ALERT, "SUSTAIN_S": SUSTAIN_S, "METRICS": METRICS, "MIN_CALLS": MIN_CALLS}}


# --- every RCAEval system: Online Boutique, Sock Shop, Train Ticket; RE2 (multi-source) and RE3 (code-level) ---

# Sock Shop's RE2/RE3 cases have no traces, so its call graph comes from the project's own code: front-end's
# api/endpoints.js (catalogue, carts, orders, user) and its session store (session-db), orders' service URIs
# (payment, shipping) and the carts/user links it follows to build an order, shipping and queue-master's
# spring.rabbitmq.host, and each service's own database from deploy/kubernetes/complete-demo.yaml.
# github.com/microservices-demo (Apache-2.0).
SOCKSHOP_EDGES = [
    ("front-end", "catalogue"), ("front-end", "carts"), ("front-end", "orders"), ("front-end", "user"),
    ("front-end", "session-db"), ("catalogue", "catalogue-db"), ("carts", "carts-db"), ("orders", "orders-db"),
    ("orders", "payment"), ("orders", "shipping"), ("orders", "carts"), ("orders", "user"), ("user", "user-db"),
    ("shipping", "rabbitmq"), ("queue-master", "rabbitmq"),
]
SYSTEMS = {
    "ob": {"title": "Online Boutique", "entry": "frontend", "skip": frozenset(), "datastores": {"redis"}},
    "ss": {"title": "Sock Shop", "entry": "front-end", "skip": frozenset({"rabbitmq-exporter"}),
           "datastores": {"carts-db", "catalogue-db", "orders-db", "user-db", "session-db", "rabbitmq"}},
    # Train Ticket's gateway (ts-ui-dashboard, nginx) is not traced: it calls every service whose spans start a trace.
    # Every ts-X-service keeps its own ts-X-mongo / ts-X-mysql, as in its deployment.
    "tt": {"title": "Train Ticket", "entry": "ts-ui-dashboard", "skip": frozenset(), "datastores": set()},
}
# v1: the settings committed before any result on these systems (6d9d5a1). v2: two structural fixes found by
# looking at v1's misses on everything except RE2 Train Ticket, which stayed unseen as the held-out test: the
# manifest edges above, and the judge letting the symptom service itself be the cause (tools.rank_suspects).
VERSION = 2
SIGNALS = ("metrics", "all")      # metrics only (the detector above, unchanged), or metrics + logs + traces


def system_of(case: str) -> str:
    return case.split("_", 1)[0][3:5]


def suite_of(case: str) -> str:
    return case.split("_", 1)[0][:3].upper()


def norm_for(system: str):
    return _norm if system == "ob" else (lambda s: s)


def topology(system: str, spans: pd.DataFrame | None, services: set[str]) -> list[tuple[str, str]]:
    """Service CALLS edges for one case."""
    if system == "ob":
        return call_edges(spans)
    if system == "ss":
        return sorted(e for e in SOCKSHOP_EDGES if e[0] in services | {"front-end"} and e[1] in services)
    edges: set[tuple[str, str]] = set()
    if spans is not None:
        sp = spans[["spanID", "parentSpanID", "serviceName"]]
        j = sp.merge(sp, left_on="parentSpanID", right_on="spanID", suffixes=("_c", "_p"), how="left")
        cross = j[j.serviceName_p.notna() & (j.serviceName_c != j.serviceName_p)]
        n = cross.groupby(["serviceName_p", "serviceName_c"]).size()
        edges |= {(a, b) for (a, b), k in n.items() if k >= MIN_CALLS}
        roots = j[j.serviceName_p.isna()].serviceName_c.value_counts()
        edges |= {(SYSTEMS["tt"]["entry"], svc) for svc, k in roots.items() if k >= MIN_CALLS}
    for svc in services:
        if svc.endswith("-service"):
            stem = svc[: -len("-service")]
            for db in (f"{stem}-mongo", f"{stem}-mysql"):
                if db in services:
                    edges.add((svc, db))
    return sorted(edges)


def extract(case: str, refresh: bool = False) -> dict:
    """Everything the judge needs from one case directory, cached next to it as signals.json: the topology,
    alerts from each source, the endpoint that slowed most per service, new error log patterns, operations."""
    from . import telemetry as T
    d = DATA / case
    cache = d / "signals.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    system = system_of(case)
    cfg, norm = SYSTEMS[system], norm_for(system)
    inject = int((d / "inject_time.txt").read_text().strip().split(".")[0])
    metrics = _table(d, "metrics")
    m_alerts = detect(metrics, inject, norm, cfg["skip"])
    services = {norm(c.rsplit("_", 1)[0]) for c in metrics.columns if c != "time" and "_" in c} - cfg["skip"]
    spans = None
    t_alerts, hot, ops = [], {}, {"ops": [], "edges": []}
    if (d / "traces.parquet").exists():
        raw = pd.read_parquet(d / "traces.parquet")
        spans = raw[["spanID", "parentSpanID", "serviceName"]].assign(serviceName=raw.serviceName.map(norm))
        tr = T.prepare_traces(raw, norm, cfg["skip"])
        t_alerts, hot = T.trace_alerts(tr, inject)
        ops = T.operations(tr, inject)
        services |= set(tr.service.unique())
    l_alerts, patterns = [], []
    if (d / "logs.parquet").exists():
        lg = T.prepare_logs(pd.read_parquet(d / "logs.parquet"), norm, cfg["skip"])
        l_alerts, patterns = T.log_alerts(lg, inject), T.log_patterns(lg, inject)
    out = {"case": case, "system": system, "inject": inject, "services": sorted(services),
           "edges": [list(e) for e in topology(system, spans, services)],
           "alerts": {"metrics": m_alerts, "logs": l_alerts, "traces": t_alerts},
           "hot_ops": hot, "patterns": patterns, "ops": ops}
    cache.write_text(json.dumps(out, default=float))
    return out


def alerts_for(sig: dict, signals: str) -> list[dict]:
    from .telemetry import merge_alerts
    if signals == "metrics":
        return sorted(sig["alerts"]["metrics"], key=lambda a: a["at"])
    return merge_alerts(sig["alerts"]["metrics"], sig["alerts"]["logs"], sig["alerts"]["traces"])


STRENGTH_WINDOW_S = 600   # 10 minutes either side of the injection, the window RCAEval runs its baselines on


def strengths(metrics: pd.DataFrame, inject: int, norm=None, skip: frozenset = frozenset()) -> dict[str, float]:
    """How far each service's metrics moved after the fault: for every metric (latency-50 left out, as RCAEval
    does), the largest |x - mean| / std over the 10 minutes after, against the 10 minutes before; a service
    takes its largest. The same n-sigma reading RCAEval's NSigma baseline makes."""
    norm = norm or _norm
    before = metrics[(metrics.time < inject) & (metrics.time >= inject - STRENGTH_WINDOW_S)]
    after = metrics[(metrics.time >= inject) & (metrics.time < inject + STRENGTH_WINDOW_S)]
    out: dict[str, float] = {}
    for col in metrics.columns:
        if col == "time" or "_" not in col or col.endswith("_latency-50"):
            continue
        svc = norm(col.rsplit("_", 1)[0])
        if svc in skip:
            continue
        b = before[col].astype(float).replace([np.inf, -np.inf], np.nan).ffill().fillna(0).to_numpy()
        a = after[col].astype(float).replace([np.inf, -np.inf], np.nan).ffill().fillna(0).to_numpy()
        if not len(a) or not len(b):
            continue
        sd = float(np.std(b))
        if sd < 1e-9:
            continue                      # a constant metric says nothing (RCAEval drops these too)
        z = float(np.max(np.abs(a - np.mean(b))) / sd)
        out[svc] = max(out.get(svc, 0.0), z)
    return out


def build_case(case: str, signals: str = "metrics") -> dict:
    """One case of any system as its own graph, ready for the unchanged judge."""
    sig = extract(case)
    cfg = SYSTEMS[sig["system"]]
    entry = cfg["entry"]
    alerts = alerts_for(sig, signals)
    edges = [tuple(e) for e in sig["edges"]]
    if sig["system"] == "ob" and VERSION >= 2:
        present = set(sig["services"]) | {"redis"}
        edges = sorted(set(edges) | {e for e in BOUTIQUE_MANIFEST_EDGES if e[0] in present and e[1] in present})
    services = sorted({s for e in edges for s in e} | {a["service"] for a in alerts} | {entry})
    g = Graph(f"rca_{abs(hash(case + signals)) % 10**8}")
    g.drop()
    _write_indexes(g)
    g.write("CREATE (:Org {title: $t}), (:Team {id: 'sys', name: $n})", {"t": case, "n": cfg["title"]})
    stores = cfg["datastores"]
    g.write("""
        UNWIND $svcs AS s MATCH (t:Team {id: 'sys'})
        CREATE (:Service {name: s, kind: CASE WHEN s IN $stores OR s ENDS WITH '-mongo' OR s ENDS WITH '-mysql'
                                            THEN 'datastore' ELSE 'service' END, tier: 1, internet_exposed: s = $entry})-[:OWNED_BY]->(t)""",
            {"svcs": services, "stores": sorted(stores), "entry": entry})
    g.write("""
        UNWIND $edges AS e MATCH (a:Service {name: e[0]}), (b:Service {name: e[1]})
        CREATE (a)-[:CALLS]->(b)""", {"edges": [list(e) for e in edges]})
    power = {}
    if VERSION >= 2:
        power = strengths(_table(DATA / case, "metrics"), sig["inject"], norm_for(sig["system"]), cfg["skip"])
    _write_alerts(g, [{"id": f"A-{i}", "service": a["service"], "at": a["at"], "severity": "page", "signal": a["signal"],
                       "strength": power.get(a["service"])} for i, a in enumerate(alerts)])
    front = next((a["at"] for a in alerts if a["service"] == entry), None)
    started = front if front is not None else (alerts[0]["at"] if alerts else sig["inject"])
    _write_incidents(g, [{"id": "INC-1", "title": case, "symptom": entry, "started": started, "status": "open", "severity": "SEV2"}])
    return {"graph": g, "alerts": alerts, "signals": sig, "entry": entry}


def all_cases(suites=("re2", "re3"), systems=("ob", "ss", "tt")) -> list[str]:
    idx = pd.read_parquet(DATA / "cases.parquet")
    return sorted(c for c in idx.case if c[:3] in suites and system_of(c) in systems and (DATA / c / "metrics.parquet").exists())


METHODS = ["judge+skeptic", "judge", "earliest alert", "biggest anomaly"]


def _ac(order: list[str], truth: str, k: int) -> bool:
    return truth in order[:k]


def run_cases(cases: list[str], signals: str = "metrics", verbose: bool = True) -> dict:
    """Every case through the judge, scored like RCAEval scores its baselines: AC@1, AC@3 and Avg@5 (the mean of
    AC@1..AC@5)."""
    idx = pd.read_parquet(DATA / "cases.parquet").set_index("case")
    rows = []
    for case in cases:
        truth = str(idx.loc[case, "root_cause_service"])
        b = build_case(case, signals)
        g, entry = b["graph"], b["entry"]
        r = tools.rank_suspects(g, "INC-1", limit=50)
        review = tools.skeptic_review(r)
        names = lambda order: list(dict.fromkeys(s["service"] for s in order))  # noqa: E731
        orders = {
            "judge+skeptic": names(review["suspects"]), "judge": names(r["suspects"]),
            "earliest alert": [a["service"] for a in b["alerts"] if a["service"] != entry],
            "biggest anomaly": [a["service"] for a in sorted(b["alerts"], key=lambda a: -a["z"]) if a["service"] != entry],
        }
        g.drop()
        rows.append({"case": case, "suite": suite_of(case), "system": system_of(case), "fault": str(idx.loc[case, "fault"]),
                     "truth": truth, "top": {m: o[:5] for m, o in orders.items()},
                     "alerted": truth in {a["service"] for a in b["alerts"]},
                     "ac": {m: [_ac(o, truth, k) for k in range(1, 6)] for m, o in orders.items()}})
        if verbose:
            print(f"{case:48} truth={truth:24} judge={','.join(orders['judge+skeptic'][:3]) or '-':60} "
                  f"{'OK' if rows[-1]['ac']['judge+skeptic'][0] else '--'}", flush=True)
    return {"signals": signals, "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict]) -> dict:
    out: dict = {}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(f"{r['suite']}-{r['system'].upper()}", []).append(r)
    groups["all"] = rows
    for name, rs in groups.items():
        n = len(rs)
        out[name] = {"n": n, "alerted": sum(r["alerted"] for r in rs), **{m: {
            "ac1": sum(r["ac"][m][0] for r in rs), "ac3": sum(r["ac"][m][2] for r in rs),
            "avg5": round(sum(sum(r["ac"][m]) / 5 for r in rs) / n, 3) if n else 0.0} for m in METHODS}}
    return out


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "boutique.json"
SERIES = (-60, 240, 5)   # seconds around the injection, step: the z-score trace kept for each alert


def export_fixture(reps=(1, 2, 3)) -> dict:
    """Everything the Boutique tenant needs from RCAEval, small enough to ship in the repo: the union of the
    traced call graph over every case, and per case the detector's alerts (time relative to the injection, plus
    a z-score trace of the metric that fired) and RCAEval's answer key. No raw metrics or traces are kept."""
    idx = pd.read_parquet(DATA / "cases.parquet").set_index("case")
    edges: set[tuple[str, str]] = set()
    cases = []
    for rep in reps:
        for case in selection(rep):
            d = DATA / case
            inject = int((d / "inject_time.txt").read_text().strip())
            metrics = pd.read_parquet(d / "metrics.parquet")
            traces = pd.read_parquet(d / "traces.parquet", columns=["spanID", "parentSpanID", "serviceName"])
            edges |= set(call_edges(traces))
            normal = metrics[metrics.time < inject]
            alerts = []
            for a in detect(metrics, inject):
                base = normal[a["metric"]].astype(float).to_numpy()
                med = np.nanmedian(base)
                scale = max(np.nanmedian(np.abs(base - med)) * 1.4826, 1e-6, abs(med) * 0.01)
                col = metrics.set_index("time")[a["metric"]].astype(float)
                lo, hi, step = SERIES
                pts = col.reindex(range(inject + lo, inject + hi + 1), method="nearest").to_numpy()[::step]
                z = np.clip(np.nan_to_num((pts - med) / scale), -50, 999)
                alerts.append({"service": a["service"], "offset": a["at"] - inject, "signal": a["signal"],
                               "z": round(a["z"], 1), "series": [round(float(x), 1) for x in z]})
            cases.append({"case": case, "rep": rep, "fault": str(idx.loc[case, "fault"]),
                          "root_cause": str(idx.loc[case, "root_cause_service"]), "alerts": alerts})
            print("exported", case, len(alerts), "alerts")
    out = {"source": "RCAEval RE2-OB (Online Boutique), https://huggingface.co/datasets/phamquiluan/RCAEval, MIT",
           "detector": {"Z_ALERT": Z_ALERT, "SUSTAIN_S": SUSTAIN_S, "METRICS": list(METRICS), "MIN_CALLS": MIN_CALLS},
           "series": {"from_s": SERIES[0], "to_s": SERIES[1], "step_s": SERIES[2]},
           "edges": sorted(list(e) for e in edges), "cases": cases}
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps(out, separators=(",", ":")))
    return out


def _series(metrics: pd.DataFrame, inject: int, col: str) -> list[float] | None:
    """The z-score trace of one metric around the injection, the way the detector saw it."""
    if col not in metrics:
        return None
    normal = metrics[metrics.time < inject]
    base = normal[col].astype(float).to_numpy()
    med = np.nanmedian(base)
    scale = max(np.nanmedian(np.abs(base - med)) * 1.4826, 1e-6, abs(med) * 0.01)
    lo, hi, step = SERIES
    pts = metrics.set_index("time")[col].astype(float).reindex(range(inject + lo, inject + hi + 1), method="nearest").to_numpy()[::step]
    return [round(float(x), 1) for x in np.clip(np.nan_to_num((pts - med) / scale), -50, 999)]


def _median_rows(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    """One row per key across cases: the median of each number."""
    by: dict[tuple, list[dict]] = {}
    for r in rows:
        by.setdefault(tuple(r[k] for k in key), []).append(r)
    out = []
    for k, rs in sorted(by.items()):
        row = dict(zip(key, k))
        for f in ("calls", "rps", "p50_ms", "p99_ms", "err"):
            row[f] = round(float(np.median([r[f] for r in rs])), 2)
        row["cases"] = len(rs)
        out.append(row)
    return out


def layers(cases: list[str]) -> dict:
    """What the traces and logs add to a tenant: the operation graph (median over every case's normal period),
    and per case the endpoint that slowed most on each service and the new error log patterns."""
    ops, op_edges, per_case = [], [], {}
    for case in cases:
        sig = extract(case)
        ops += sig["ops"]["ops"]
        op_edges += sig["ops"]["edges"]
        per_case[case] = {"hot_ops": {s: h for s, h in sig["hot_ops"].items() if h["ratio"] >= 2},
                          "patterns": [{**{k: v for k, v in pt.items() if k != "first_at"},
                                        "offset": pt["first_at"] - sig["inject"]} for pt in sig["patterns"]]}
    return {"ops": _median_rows(ops, ("service", "op")),
            "op_edges": _median_rows(op_edges, ("from_service", "from_op", "to_service", "to_op")), "per_case": per_case}


def add_layers(path: Path = FIXTURE) -> dict:
    """Add the operation graph, hot endpoints and log patterns to an existing fixture, alerts untouched."""
    data = json.loads(path.read_text())
    lay = layers([c["case"] for c in data["cases"]])
    data["ops"], data["op_edges"] = lay["ops"], lay["op_edges"]
    for c in data["cases"]:
        c.update(lay["per_case"][c["case"]])
        inject = int((DATA / c["case"] / "inject_time.txt").read_text().strip().split(".")[0])
        power = strengths(pd.read_parquet(DATA / c["case"] / "metrics.parquet"), inject)
        for a in c["alerts"]:
            a["strength"] = round(power[a["service"]], 2) if a["service"] in power else None
    path.write_text(json.dumps(data, separators=(",", ":")))
    return data


SYSTEM_FIXTURE = {"ss": "sockshop.json", "tt": "trainticket.json"}


def export_system(system: str, signals: str = "metrics") -> dict:
    """A whole system (every RE2 and RE3 case) as a fixture for its tenant, built from the cached signals."""
    idx = pd.read_parquet(DATA / "cases.parquet").set_index("case")
    names = all_cases(("re2", "re3"), (system,))
    edges: set[tuple[str, str]] = set()
    cases = []
    for case in names:
        sig = extract(case)
        edges |= {tuple(e) for e in sig["edges"]}
        metrics = pd.read_parquet(DATA / case / "metrics.parquet")
        power = strengths(metrics, sig["inject"], norm_for(system), SYSTEMS[system]["skip"])
        alerts = []
        for a in alerts_for(sig, signals):
            alerts.append({"service": a["service"], "offset": a["at"] - sig["inject"], "signal": a["signal"],
                           "strength": round(power[a["service"]], 2) if a["service"] in power else None,
                           "z": round(min(a["z"], 1e6), 1), "series": _series(metrics, sig["inject"], a["metric"]),
                           "sources": a.get("sources") or [f"metrics: {a['signal']}"]})
        cases.append({"case": case, "suite": suite_of(case), "rep": int(case.rsplit("_", 1)[1]),
                      "fault": str(idx.loc[case, "fault"]), "fault_description": str(idx.loc[case, "fault_description"]),
                      "root_cause": str(idx.loc[case, "root_cause_service"]), "alerts": alerts})
        print("exported", case, len(alerts), "alerts", flush=True)
    lay = layers(names)
    for c in cases:
        c.update(lay["per_case"][c["case"]])
    cfg = SYSTEMS[system]
    out = {"source": f"RCAEval RE2/RE3-{system.upper()} ({cfg['title']}), https://huggingface.co/datasets/phamquiluan/RCAEval, MIT",
           "system": system, "title": cfg["title"], "entry": cfg["entry"], "signals": signals,
           "datastores": sorted(cfg["datastores"] | {s for e in edges for s in e if s.endswith(("-mongo", "-mysql"))}),
           "detector": {"Z_ALERT": Z_ALERT, "SUSTAIN_S": SUSTAIN_S, "METRICS": list(METRICS), "MIN_CALLS": MIN_CALLS},
           "series": {"from_s": SERIES[0], "to_s": SERIES[1], "step_s": SERIES[2]},
           "edges": sorted(list(e) for e in edges), "ops": lay["ops"], "op_edges": lay["op_edges"], "cases": cases}
    path = FIXTURE.parent / SYSTEM_FIXTURE[system]
    path.write_text(json.dumps(out, separators=(",", ":")))
    return out


def table(res: dict) -> str:
    n = res["n"]
    lines = ["| method | top-1 | top-3 |", "|---|---|---|"]
    for m in res["top1"]:
        name = {"judge+skeptic": "**Faultline (judge + skeptic)**", "judge": "Faultline (judge only)"}.get(m, m)
        lines.append(f"| {name} | {res['top1'][m]}/{n} | {res['top3'][m]}/{n} |")
    by: dict[str, list[bool]] = {}
    for r in res["rows"]:
        by.setdefault(r["fault"], []).append(r["hit"]["judge+skeptic"])
    lines.append("")
    lines.append("By fault (judge + skeptic, top-1): " + ", ".join(f"{f} {sum(v)}/{len(v)}" for f, v in by.items()) + ".")
    lines.append(f"The detector raised an alert on the true root-cause service in {res['detected']}/{n} cases.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--rep", type=int, default=1, help="which repetition of each fault/service pair (1-3)")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--fixture", action="store_true", help="write faultline/fixtures/boutique.json from all 54 cases")
    ap.add_argument("--suites", default=None, help="run every downloaded case of these suites, e.g. re2,re3")
    ap.add_argument("--systems", default="ob,ss,tt")
    ap.add_argument("--signals", choices=SIGNALS, default="metrics")
    ap.add_argument("--extract", action="store_true", help="only extract and cache signals.json per case")
    ap.add_argument("--shard", default="0/1", help="i/n: this process takes every n-th case starting at i")
    a = ap.parse_args()
    if a.suites:
        i, n = (int(x) for x in a.shard.split("/"))
        cases = all_cases(tuple(a.suites.split(",")), tuple(a.systems.split(",")))[i::n]
        if a.extract:
            for c in cases:
                t = time.time()
                sig = extract(c, refresh=True)
                print(f"{c:48} {len(sig['edges']):4} edges  alerts m/l/t {len(sig['alerts']['metrics'])}/"
                      f"{len(sig['alerts']['logs'])}/{len(sig['alerts']['traces'])}  {time.time() - t:.1f}s", flush=True)
            return
        res = run_cases(cases, a.signals)
        print(json.dumps(res["summary"], indent=1))
        if a.json:
            a.json.write_text(json.dumps(res, indent=1, default=str))
        return
    if a.fixture:
        out = export_fixture()
        print(len(out["cases"]), "cases,", len(out["edges"]), "edges ->", FIXTURE)
        return
    cases = selection(a.rep)
    if a.download:
        download(cases)
        return
    t0 = time.time()
    res = run(cases)
    print()
    print(table(res))
    print(f"\n{res['n']} RCAEval RE2-OB cases (repetition {a.rep}), {time.time() - t0:.0f} s")
    if a.json:
        a.json.write_text(json.dumps(res, indent=2, default=str))


if __name__ == "__main__":
    main()
