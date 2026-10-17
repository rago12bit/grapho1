"""MCP server: the same graph tools for any MCP client (Claude Code, Claude Desktop, Cursor, ...).

    docker compose exec -T app python -m faultline.mcp_server            # stdio
    docker compose exec app python -m faultline.mcp_server --http        # streamable HTTP on :8091

Approving actions is deliberately missing: an agent can propose a fix, only a person in the UI can run it.
"""
from __future__ import annotations

import json
import os
import sys

from mcp.server.mcpserver import MCPServer

from . import agent

TENANT = os.environ.get("FAULTLINE_TENANT", "kestrel")

server = MCPServer(
    name="faultline",
    instructions=(
        "Outage investigation over a FalkorDB graph of services, owners, changes, alerts and past incidents. "
        "Start an incident with rank_suspects. Actions you propose wait for human approval."),
)


def _call(name: str, **args) -> str:
    res, queries, err = agent.call_tool(TENANT, name, {k: v for k, v in args.items() if v is not None})
    if err:
        raise ValueError(err)
    return json.dumps({"result": res, "cypher": [q["cypher"] for q in queries]}, default=str)


@server.tool(description="Open incidents (or all with status=null).")
def list_incidents(status: str | None = "open") -> str:
    from .db import Graph
    from . import tools
    return json.dumps(tools.list_incidents(Graph(TENANT), status), default=str)


@server.tool(description="Incident details and alerts on its symptom service.")
def get_incident(incident_id: str) -> str:
    return _call("get_incident", incident_id=incident_id)


@server.tool(description="Ranked root-cause suspects for an incident with factor breakdowns, paths, owners and a recommended action.")
def rank_suspects(incident_id: str) -> str:
    return _call("rank_suspects", incident_id=incident_id)


@server.tool(description="Services a service depends on, with hop counts.")
def dependencies(service: str, max_hops: int = 5) -> str:
    return _call("dependencies", service=service, max_hops=max_hops)


@server.tool(description="Call paths from one service down to another.")
def paths_between(source: str, target: str) -> str:
    return _call("paths_between", source=source, target=target)


@server.tool(description="Resolved incidents caused on a service: cause, fix and lesson.")
def similar_incidents(service: str, kind: str | None = None) -> str:
    return _call("similar_incidents", service=service, kind=kind)


@server.tool(description="Owning team and on-call person for a service.")
def who_owns(service: str) -> str:
    return _call("who_owns", service=service)


@server.tool(description="Everything that calls a service, internet-facing entry points, and its PageRank rank.")
def blast_radius(service: str) -> str:
    return _call("blast_radius", service=service)


@server.tool(description="What-if before you ship: if a service failed, the waves in which alerts would arrive, entry points hit, teams paged.")
def simulate_failure(service: str) -> str:
    return _call("simulate_failure", service=service)


@server.tool(description="The team's runbooks for a service.")
def runbooks(service: str) -> str:
    return _call("runbooks", service=service)


@server.tool(description="Services ranked by PageRank over the call graph.")
def critical_services(limit: int = 10) -> str:
    return _call("critical_services", limit=limit)


@server.tool(description="Read-only Cypher against the graph (200-row cap, 3 s timeout).")
def run_cypher(query: str) -> str:
    return _call("run_cypher", query=query)


@server.tool(description=(
    "Propose a fix (rollback, revert_config, disable_flag, page_owner). A human approves it in the Faultline UI. "
    "also_for: other open incidents the same change explains, so one approval mitigates all of them."))
def propose_action(incident_id: str, kind: str, target: str, summary: str, change_id: str | None = None,
                   reason: str = "", also_for: list[str] | None = None) -> str:
    return _call("propose_action", incident_id=incident_id, kind=kind, target=target, summary=summary,
                 change_id=change_id, reason=reason, also_for=also_for)


if __name__ == "__main__":
    if "--http" in sys.argv:
        server.run(transport="streamable-http", host="0.0.0.0", port=8091)
    else:
        server.run()
