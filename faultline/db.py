"""FalkorDB access. One graph per tenant; every query is recorded so answers can show their Cypher."""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any

from falkordb import FalkorDB
from redis import exceptions as rx

GRAPH_PREFIX = os.environ.get("FAULTLINE_GRAPH_PREFIX", "faultline_")
ROW_CAP = 200
QUERY_TIMEOUT_MS = 3000

_WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|CALL\s+db\.)", re.I)

_client: FalkorDB | None = None

RETRIES = 4
_TRANSIENT = ("max pending queries", "busy", "loading", "try again")
retry_count = 0   # how many retries happened in this process (the benchmark reports it)


def _transient(e: Exception) -> bool:
    if isinstance(e, (rx.BusyLoadingError, rx.ConnectionError, rx.TimeoutError)):
        return True
    return isinstance(e, rx.ResponseError) and any(t in str(e).lower() for t in _TRANSIENT)


def _with_retry(fn):
    """Retry a query when FalkorDB is busy or the connection drops, with jittered exponential backoff.
    Errors in the query itself are never retried."""
    global retry_count
    for attempt in range(RETRIES + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == RETRIES or not _transient(e):
                raise
            retry_count += 1
            time.sleep(0.05 * 2 ** attempt + random.random() * 0.05)


def client() -> FalkorDB:
    global _client
    if _client is None:
        _client = FalkorDB(
            host=os.environ.get("FALKOR_HOST", "localhost"),
            port=int(os.environ.get("FALKOR_PORT", "6381")),
        )
    return _client


def graph_name(tenant: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", tenant):
        raise ValueError(f"bad tenant id: {tenant!r}")
    return GRAPH_PREFIX + tenant


def list_tenants() -> list[str]:
    return sorted(g[len(GRAPH_PREFIX):] for g in client().list_graphs() if g.startswith(GRAPH_PREFIX))


@dataclass
class Trace:
    """Every Cypher statement a tool ran, with its parameters, row count and time."""
    queries: list[dict[str, Any]] = field(default_factory=list)

    def add(self, cypher: str, params: dict | None, rows: int, ms: float) -> None:
        self.queries.append({"cypher": _tidy(cypher), "params": params or {}, "rows": rows, "ms": round(ms, 2)})


def _tidy(cypher: str) -> str:
    lines = [ln.rstrip() for ln in cypher.strip("\n").splitlines()]
    indent = min((len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()), default=0)
    return "\n".join(ln[indent:] for ln in lines)


def _rows(result) -> list[dict[str, Any]]:
    cols = [c[1] if isinstance(c, (list, tuple)) else c for c in result.header]
    cols = [c.decode() if isinstance(c, bytes) else c for c in cols]
    return [dict(zip(cols, row)) for row in result.result_set[:ROW_CAP]]


class Graph:
    """A tenant's graph. read() uses GRAPH.RO_QUERY, so the database itself refuses writes there."""

    def __init__(self, tenant: str, trace: Trace | None = None):
        self.tenant = tenant
        self.g = client().select_graph(graph_name(tenant))
        self.trace = trace if trace is not None else Trace()

    def read(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        t = time.perf_counter()
        res = _with_retry(lambda: self.g.ro_query(cypher, params or {}, timeout=QUERY_TIMEOUT_MS))
        rows = _rows(res)
        self.trace.add(cypher, params, len(rows), (time.perf_counter() - t) * 1000)
        return rows

    def write(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        t = time.perf_counter()
        res = _with_retry(lambda: self.g.query(cypher, params or {}, timeout=QUERY_TIMEOUT_MS))
        rows = _rows(res) if res.header else []
        self.trace.add(cypher, params, len(rows), (time.perf_counter() - t) * 1000)
        return rows

    def exists(self) -> bool:
        return graph_name(self.tenant) in client().list_graphs()

    def drop(self) -> None:
        if self.exists():
            self.g.delete()


def assert_read_only(cypher: str) -> None:
    """Belt and braces for the free-form Cypher tool; RO_QUERY is the real guard."""
    if _WRITE_WORDS.search(cypher):
        raise ValueError("only read queries are allowed")
