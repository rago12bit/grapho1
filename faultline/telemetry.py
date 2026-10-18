"""Signals from logs and traces, beyond the metrics: what RCAEval's RE2/RE3 suites record for each case.

    logs    every line is reduced to a template (ids, numbers, hex, ips, quoted strings and JSON masked), so
            "GetCartAsync called with userId=77cc…" and "…userId=65a9…" are one pattern. A service alerts when
            its error lines (error / exception / refused / timeout / 5xx …) rise above their normal rate and stay
            there; a template never seen before the fault, that is an error and repeats, is kept as a LogPattern.
    traces  every span becomes an Operation (service + endpoint), and parent→child spans become operation-level
            CALLS with traffic, p50/p99 latency and error rate over the normal period. A service alerts when the
            p90 latency or error rate of the spans it serves rises and stays up. For each alerting service, the
            operation whose latency rose most is named, so a verdict can say which endpoint broke.

Same robust z-score and sustain rule as the metric detector (rcaeval.Z_ALERT, SUSTAIN_S), fixed before any
result on Sock Shop, Train Ticket or the code-level (RE3) cases was seen.
"""
from __future__ import annotations

import re
from typing import Callable

import numpy as np
import pandas as pd

Z_ALERT = 5.0
SUSTAIN_S = 30
LOG_BUCKET_S = 10           # logs are sparse: count error lines per 10 s
TRACE_BUCKET_S = 5          # spans: p90 latency and error share per 5 s
MIN_PATTERN_HITS = 3        # a new error template must repeat this often after the fault to count
MIN_OP_CALLS = 5            # operation edges seen fewer times than this in the normal period are dropped

_MASKS = [
    (re.compile(r"\{.*\}"), "{…}"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ][\d:.,]+Z?\b"), "<ts>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{12,}\b", re.I), "<hex>"),
    (re.compile(r"\[[^\]]*,[^\]]*\]"), "[…]"),
    (re.compile(r"\"[^\"]*\"|'[^']*'"), "\"…\""),
    (re.compile(r"(?<![\w<])-?\d+(?:\.\d+)?(?:ms|s|µs|us|ns|%|b|kb|mb)?\b", re.I), "<n>"),
    (re.compile(r"\s+"), " "),
]
ERROR = re.compile(r"(?:\w*(?:error|exception)\b|\berr=(?!null)|\b(?:fail(?:ed|ure|s)?|refused|timeout|timed out|unavailable|unreachable|"
                   r"panic|fatal|severe|denied|reset by peer|broken pipe|deadline))|"
                   r"^(?:GET|POST|PUT|DELETE|PATCH) \S+ 5\d\d\b|\bstatus(?:Code)?[=: ]+5\d\d\b", re.I)
_HTTP = re.compile(r"^((?:GET|POST|PUT|DELETE|PATCH) \S+ )([1-5]\d\d)\b")
NOISE_OP = re.compile(r"opentelemetry|TraceService/Export|grpc\.health|/health|healthz|/metrics$", re.I)


def template(msg: str) -> str:
    s = _HTTP.sub(lambda m: m.group(1) + "§" + m.group(2)[0] + "xx", str(msg or "")[:400])   # keep the status class
    for rx, rep in _MASKS:
        s = rx.sub(rep, s)
    return s.replace("§", "").strip()[:160]


def _robust(base: np.ndarray) -> tuple[float, float]:
    base = base[~np.isnan(base)]
    if not len(base):
        return 0.0, 1.0
    med = float(np.median(base))
    mad = float(np.median(np.abs(base - med))) * 1.4826
    return med, max(mad, 1e-6, abs(med) * 0.01)


def _sustained(times: np.ndarray, z: np.ndarray, need: int) -> tuple[int | None, float]:
    run = 0
    for i, h in enumerate(np.nan_to_num(z, nan=0.0) > Z_ALERT):
        run = run + 1 if h else 0
        if run >= need:
            return int(times[i - need + 1]), float(np.nanmax(z))
    return None, 0.0


# --- logs ----------------------------------------------------------------------------------------

def prepare_logs(logs: pd.DataFrame, norm: Callable[[str], str], skip: set[str] = frozenset()) -> pd.DataFrame:
    df = logs.rename(columns={"container_name": "service"})[["timestamp", "service", "message"]].dropna(subset=["message"]).copy()
    df["service"] = df["service"].map(norm)
    df = df[~df["service"].isin(skip)]
    df["timestamp"] = df["timestamp"].astype("int64")
    if df["timestamp"].max() > 10**11:          # milliseconds
        df["timestamp"] //= 1000
    df["template"] = df["message"].map(template)
    df["error"] = df["message"].astype(str).str.contains(ERROR)
    return df


def log_alerts(df: pd.DataFrame, inject: int) -> list[dict]:
    """Earliest sustained rise in error lines per service."""
    out = []
    if df.empty:
        return out
    lo, hi = int(df.timestamp.min()), int(df.timestamp.max())
    edges = np.arange(lo - lo % LOG_BUCKET_S, hi + LOG_BUCKET_S, LOG_BUCKET_S)
    need = max(1, SUSTAIN_S // LOG_BUCKET_S)
    for svc, part in df[df.error].groupby("service"):
        counts, _ = np.histogram(part.timestamp, bins=edges)
        t = edges[:-1]
        med, scale = _robust(counts[t < inject].astype(float))
        z = (counts[t >= inject] - med) / scale
        onset, peak = _sustained(t[t >= inject], z, need)
        if onset is not None:
            out.append({"service": svc, "at": onset, "z": peak, "source": "logs", "metric": f"{svc}_error-logs",
                        "signal": f"error logs z={peak:.0f}"})
    return out


def log_patterns(df: pd.DataFrame, inject: int, limit: int = 12) -> list[dict]:
    """Error templates that appear only after the fault (or at 5x their normal rate), most frequent first."""
    if df.empty:
        return []
    before, after = df[df.timestamp < inject], df[df.timestamp >= inject]
    span_b = max(1, inject - int(df.timestamp.min()))
    span_a = max(1, int(df.timestamp.max()) - inject)
    nb = before.groupby(["service", "template"]).size()
    out = []
    for (svc, tpl), part in after[after.error].groupby(["service", "template"]):
        n_after, n_before = len(part), int(nb.get((svc, tpl), 0))
        if n_after < MIN_PATTERN_HITS:
            continue
        rate_a, rate_b = n_after / span_a, n_before / span_b
        if n_before and rate_a < 5 * rate_b:
            continue
        out.append({"service": svc, "template": tpl, "after": n_after, "before": n_before, "first_at": int(part.timestamp.min()),
                    "new": n_before == 0, "example": str(part.message.iloc[0])[:240]})
    return sorted(out, key=lambda p: (-p["new"], -p["after"], p["first_at"]))[:limit]


# --- traces --------------------------------------------------------------------------------------

def prepare_traces(traces: pd.DataFrame, norm: Callable[[str], str], skip: set[str] = frozenset()) -> pd.DataFrame:
    cols = [c for c in ("spanID", "parentSpanID", "serviceName", "operationName", "startTime", "duration", "statusCode") if c in traces.columns]
    df = traces[cols].copy()
    df["service"] = df["serviceName"].map(norm)
    df = df[~df["service"].isin(skip)]
    st = df["startTime"].astype("int64")
    df["ts"] = (st // 10**6 if st.max() > 10**14 else st // 10**3 if st.max() > 10**11 else st).astype("int64")
    dur = df["duration"].astype(float)
    df["ms"] = dur / 1000.0 if dur.median() > 50 else dur      # microseconds in every RCAEval suite
    code = pd.to_numeric(df.get("statusCode"), errors="coerce").fillna(0)
    df["err"] = ((code >= 1) & (code < 100)) | (code >= 500)    # gRPC non-OK, or HTTP 5xx
    df["op"] = df["operationName"].astype(str).str.slice(0, 120)
    return df[~df["op"].str.contains(NOISE_OP)]       # the telemetry pipeline's own spans and health checks


def _served(df: pd.DataFrame) -> pd.DataFrame:
    """Spans a service serves: roots, and spans whose parent ran in another service."""
    parent = df[["spanID", "service"]].rename(columns={"spanID": "parentSpanID", "service": "parent_service"})
    j = df.merge(parent, on="parentSpanID", how="left")
    return j[j.parent_service.isna() | (j.parent_service != j.service)]


def operations(df: pd.DataFrame, inject: int) -> dict:
    """Operation nodes and operation-level CALLS over the normal period, with traffic and latency."""
    normal = df[df.ts < inject]
    secs = max(1, int(normal.ts.max() - normal.ts.min())) if len(normal) else 1
    ops = (normal.groupby(["service", "op"]).ms.agg(calls="size", p50=lambda x: x.quantile(.5), p99=lambda x: x.quantile(.99))
           .reset_index())
    errs = normal.groupby(["service", "op"]).err.mean().rename("err").reset_index()
    ops = ops.merge(errs, on=["service", "op"])
    pairs = normal.merge(normal[["spanID", "service", "op"]].rename(columns={"spanID": "parentSpanID", "service": "ps", "op": "pop"}),
                         on="parentSpanID")
    pairs = pairs[(pairs.ps != pairs.service) | (pairs.pop != pairs.op)]
    e = (pairs.groupby(["ps", "pop", "service", "op"]).ms.agg(calls="size", p50=lambda x: x.quantile(.5), p99=lambda x: x.quantile(.99))
         .reset_index())
    e = e.merge(pairs.groupby(["ps", "pop", "service", "op"]).err.mean().rename("err").reset_index(), on=["ps", "pop", "service", "op"])
    e = e[e.calls >= MIN_OP_CALLS]
    r = lambda v: round(float(v), 2)  # noqa: E731
    return {
        "ops": [{"service": x.service, "op": x.op, "calls": int(x.calls), "rps": r(x.calls / secs), "p50_ms": r(x.p50),
                 "p99_ms": r(x.p99), "err": r(x.err)} for x in ops.itertuples()],
        "edges": [{"from_service": x.ps, "from_op": x.pop, "to_service": x.service, "to_op": x.op, "calls": int(x.calls),
                   "rps": r(x.calls / secs), "p50_ms": r(x.p50), "p99_ms": r(x.p99), "err": r(x.err)} for x in e.itertuples()],
    }


def trace_alerts(df: pd.DataFrame, inject: int) -> tuple[list[dict], dict[str, dict]]:
    """Earliest sustained rise in p90 latency or error share of the spans each service serves, and for each
    service the operation whose latency rose most after the fault."""
    served = _served(df)
    served = served.assign(b=served.ts - served.ts % TRACE_BUCKET_S)
    need = max(1, SUSTAIN_S // TRACE_BUCKET_S)
    alerts, hot = [], {}
    for svc, part in served.groupby("service"):
        g = part.groupby("b").agg(p90=("ms", lambda x: x.quantile(.9)), err=("err", "mean"))
        t = g.index.to_numpy()
        best = None
        for kind, col in (("latency-p90", "p90"), ("error-rate", "err")):
            v = g[col].to_numpy(dtype=float)
            med, scale = _robust(v[t < inject])
            z = (v[t >= inject] - med) / scale
            onset, peak = _sustained(t[t >= inject], z, need)
            if onset is not None and (best is None or onset < best["at"]):
                best = {"service": svc, "at": onset, "z": peak, "source": "traces", "metric": f"{svc}_span-{kind}",
                        "signal": f"span {kind} z={peak:.0f}"}
        if best:
            alerts.append(best)
        before = part[part.ts < inject].groupby("op").ms.quantile(.9)
        late = part[part.ts >= inject]
        busy = late.op.value_counts()
        after = late[late.op.isin(busy[busy >= MIN_OP_CALLS].index)].groupby("op").ms.quantile(.9)
        ratio = (after / before.clip(lower=0.01)).dropna()
        if len(ratio):
            op = ratio.idxmax()
            hot[svc] = {"op": op, "p90_before_ms": round(float(before[op]), 2), "p90_after_ms": round(float(after[op]), 2),
                        "ratio": round(float(ratio[op]), 1)}
    return alerts, hot


def merge_alerts(*sources: list[dict]) -> list[dict]:
    """One alert per service at its earliest onset across sources; the other sources are kept as corroboration."""
    best: dict[str, dict] = {}
    seen: dict[str, list[str]] = {}
    for src in sources:
        for a in src:
            seen.setdefault(a["service"], []).append(f"{a.get('source', 'metrics')}: {a['signal']}")
            if a["service"] not in best or a["at"] < best[a["service"]]["at"]:
                best[a["service"]] = dict(a)
    for svc, a in best.items():
        a["sources"] = seen[svc]
    return sorted(best.values(), key=lambda a: (a["at"], a["service"]))


PROFILE_WINDOW_S = 600


def span_profile(df: pd.DataFrame, inject: int) -> dict[str, dict]:
    """Where each service's time went, before and after the fault: its own time (a span's duration minus the time
    its direct children took) and its waiting time (the children's), p90 over the 10 minutes after against the 10
    before. A service whose own time grew is doing the slow work; one that only waits longer is a victim of
    something below it. `own_share` is the share of the slowdown that is its own (0.5 when nothing slowed)."""
    d = df[(df.ts >= inject - PROFILE_WINDOW_S) & (df.ts < inject + PROFILE_WINDOW_S)]
    kids = d.groupby("parentSpanID").ms.sum().rename("child_ms")
    d = d.join(kids, on="spanID")
    d = d.assign(child_ms=d.child_ms.fillna(0.0))
    d = d.assign(self_ms=(d.ms - d.child_ms).clip(lower=0.0), after=d.ts >= inject)
    out: dict[str, dict] = {}
    for svc, part in d.groupby("service"):
        b, a = part[~part.after], part[part.after]
        if len(b) < 20 or len(a) < 20:
            continue
        r = lambda col: float(a[col].quantile(.9) / max(b[col].quantile(.9), 0.01))  # noqa: E731
        self_r, child_r = r("self_ms"), (r("child_ms") if part.child_ms.max() > 0 else 1.0)
        up_self, up_child = max(self_r - 1, 0.0), max(child_r - 1, 0.0)
        share = up_self / (up_self + up_child) if up_self + up_child > 0.05 else 0.5
        out[svc] = {"self_ratio": round(self_r, 3), "child_ratio": round(child_r, 3), "own_share": round(share, 3),
                    "spans": int(len(part))}
    return out
