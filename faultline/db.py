"""FalkorDB access. One graph per tenant; every query is recorded so answers can show their Cypher."""
from __future__ import annotations

import os
import random
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import redis
from falkordb import FalkorDB
from falkordb import Graph as FalkorGraph
from redis import exceptions as rx

GRAPH_PREFIX = os.environ.get("FAULTLINE_GRAPH_PREFIX", "hypocenter_")
ROW_CAP = 200
QUERY_TIMEOUT_MS = 3000

_WRITE_WORDS = re.compile(r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|LOAD\s+CSV|CALL\s+db\.)", re.I)

_client: FalkorDB | None = None

RETRIES = 4
_TRANSIENT = ("max pending queries", "busy", "loading", "try again")
retry_count = 0   # how many retries happened in this process (the benchmark reports it)


def _transient(e: Exception) -> bool:
    if isinstance(e, rx.AuthenticationError):       # a reader whose user went away (FalkorDB restarted): not transient
        return False
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


# --- one read-only database user per tenant ------------------------------------------------------------------
# Requests for a tenant (the API, the MCP server) read through that tenant's own FalkorDB ACL user, which may run
# GRAPH.RO_QUERY on that tenant's graph key and nothing else. A write, or a read of another tenant's graph, is
# refused by the database whatever the query says. The users are made at first use with the shared connection's
# (admin) user, each process adds a random password of its own, and nothing is written to disk. Scratch graphs
# (graph copies, evals, benchmarks) and every write keep using the shared connection. FAULTLINE_TENANT_ACL=0 turns
# it off; a server that won't let us create users falls back to the shared connection and /api/engine says so.
ACL_ON = os.environ.get("FAULTLINE_TENANT_ACL", "1") != "0"
READER_RULES = ("resetkeys", "resetchannels", "-@all", "+graph.ro_query", "+ping")
_readers: dict[str, tuple[redis.Redis, str, str]] = {}   # graph name -> (connection, password, user)
_acl_lock = threading.Lock()
acl_state: dict[str, Any] = {"enabled": False, "users": [], "error": None}


def reader_user(tenant: str) -> str:
    return f"{GRAPH_PREFIX}reader_{tenant}"


def _set_reader(tenant: str, password: str) -> None:
    client().connection.execute_command("ACL", "SETUSER", reader_user(tenant), "on", f">{password}",
                                        *READER_RULES[:1], f"~{graph_name(tenant)}", *READER_RULES[1:])


def _key(tenant: str) -> str:
    return graph_name(tenant)        # by graph, not tenant: a scratch prefix is a different graph and user


def ensure_reader(tenant: str) -> bool:
    """Make (once per process) the tenant's read-only user and a connection that logs in as it. False when ACLs
    are off or the server refused to create users; reads then use the shared connection."""
    if not ACL_ON:
        return False
    with _acl_lock:
        if _key(tenant) in _readers:
            return True
        if acl_state["error"]:
            return False
        password = secrets.token_urlsafe(24)
        try:
            _set_reader(tenant, password)
        except Exception as e:  # e.g. a managed FalkorDB without ACL rights: say so and carry on
            acl_state["error"] = f"{type(e).__name__}: {e}"[:200]
            return False
        # a plain connection: the FalkorDB client's constructor asks the server for INFO, which this user can't
        _readers[_key(tenant)] = (redis.Redis(host=os.environ.get("FALKOR_HOST", "localhost"),
                                              port=int(os.environ.get("FALKOR_PORT", "6381")),
                                              username=reader_user(tenant), password=password), password, reader_user(tenant))
        acl_state.update(enabled=True, users=sorted(r[2] for r in _readers.values()))
        return True


def drop_readers() -> None:
    """Delete the reader users this process made (scratch runs clean up after themselves)."""
    with _acl_lock:
        for k, (_, _, user) in list(_readers.items()):
            client().connection.execute_command("ACL", "DELUSER", user)
            del _readers[k]
        acl_state.update(enabled=False, users=[])


def reader(tenant: str) -> redis.Redis | None:
    """The tenant's read-only connection, if ensure_reader made one."""
    r = _readers.get(_key(tenant))
    return r[0] if r else None


def reader_graph(tenant: str, graph_tenant: str | None = None) -> FalkorGraph:
    """A graph handle on the tenant's read-only connection (another tenant's graph, to show it is refused)."""
    return FalkorGraph(reader(tenant), graph_name(graph_tenant or tenant))


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
    """A tenant's graph. read() uses GRAPH.RO_QUERY, so the database itself refuses writes there; for a tenant
    served to requests it also logs in as the tenant's read-only user, which can't reach any other graph."""

    def __init__(self, tenant: str, trace: Trace | None = None):
        self.tenant = tenant
        self.g = client().select_graph(graph_name(tenant))
        self.ro = reader_graph(tenant) if reader(tenant) else self.g   # the tenant's own read-only user, if made
        self.trace = trace if trace is not None else Trace()

    def _ro_query(self, cypher: str, params: dict):
        try:
            return self.ro.ro_query(cypher, params, timeout=QUERY_TIMEOUT_MS)
        except rx.AuthenticationError:
            # The reader's user is gone (FalkorDB restarted, users live in memory): make it again, same password.
            _set_reader(self.tenant, _readers[_key(self.tenant)][1])
            return self.ro.ro_query(cypher, params, timeout=QUERY_TIMEOUT_MS)

    def read(self, cypher: str, params: dict | None = None) -> list[dict[str, Any]]:
        t = time.perf_counter()
        res = _with_retry(lambda: self._ro_query(cypher, params or {}))
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
