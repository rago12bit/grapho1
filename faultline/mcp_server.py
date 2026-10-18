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

from . import agent, db

TENANT = os.environ.get("FAULTLINE_TENANT", "kestrel")
db.ensure_reader(TENANT)     # every read this server makes goes through the tenant's read-only database user

server = MCPServer(
    name="hypocenter",
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


@server.tool(description="The same incident judged with the search capped at 1, 2, ... 5 hops below the symptom: what a one-hop look blames, and the depth from which the verdict stops changing.")
def depth_sweep(incident_id: str) -> str:
    return _call("depth_sweep", incident_id=incident_id)


@server.tool(description="How an incident's failure spread: MSF over the alerting services, each call weighted by the gap between their first alerts. One tree per problem, read from the first service to alert.")
def spread_tree(incident_id: str) -> str:
    return _call("spread_tree", incident_id=incident_id)


@server.tool(description="Every service that calls this one, directly or transitively, with hop counts (SSpaths walking CALLS backwards).")
def callers(service: str) -> str:
    return _call("callers", service=service)


@server.tool(description="What people said in chat about these services, changes or incidents around an incident. Context, never proof.")
def chat_about(incident_id: str, names: list[str]) -> str:
    return _call("chat_about", incident_id=incident_id, names=names)


@server.tool(description="Where a change came from: who made it, the chat discussion that led to it, its commits, what was said after, and any revert.")
def decision_trail(change_id: str) -> str:
    return _call("decision_trail", change_id=change_id)


@server.tool(description="Look a name up anywhere in the graph, with near service names. Nothing found means it isn't modelled, not that it is safe.")
def find_named(name: str) -> str:
    return _call("find_named", name=name)


@server.tool(description="Skeptic review of the leading suspect: what it doesn't explain, order, timing, margin. May overturn it. Returns the verdict, each check and a calibrated confidence.")
def challenge_leader(incident_id: str) -> str:
    return _call("challenge_leader", incident_id=incident_id)


@server.tool(description="The evidence gate: eight named checks the leader must pass before it is called proven (in the graph, on a call path, there first, alerts climb the path, explains every alert, no rival survives, every claim checked, nothing failing off the graph), each with the Cypher that decided it. Verdict: confirmed, not_confident or cannot_prove. No path is never treated as safe.")
def evidence_gate(incident_id: str) -> str:
    return _call("evidence_gate", incident_id=incident_id)


@server.tool(description="Before a change ships: if it broke its service, who would feel it (callers, entry points, journeys, teams paged); open incidents it would land in; what changes like it did there before; changes already stacked on it; who should review. Returns ship, review or hold with each reason. kind: deploy, config or flag. Read-only.")
def preflight_change(service: str, kind: str = "deploy", summary: str = "") -> str:
    return _call("preflight_change", service=service, kind=kind, summary=summary)


@server.tool(description="The stored argument for an incident: every hypothesis (standing / overturned / rejected) with the evidence for and against it and what each piece cites.")
def argument(incident_id: str) -> str:
    return _call("argument", incident_id=incident_id)


@server.tool(description="Follow an alert's CAUSED_BY links down to where it started, with each link's lag and confidence.")
def why(alert_id: str) -> str:
    return _call("why", alert_id=alert_id)


@server.tool(description="The commits a deploy shipped, ranked: which one to look at first.")
def commits_in(change_id: str, incident_id: str | None = None) -> str:
    return _call("commits_in", change_id=change_id, incident_id=incident_id)


@server.tool(description="Per team: deploys per week, change failure rate, time to restore, lead time.")
def delivery_metrics() -> str:
    return _call("delivery_metrics")


@server.tool(description="User journeys (and SLOs) that need any of these services.")
def journeys_hit(services: list[str]) -> str:
    return _call("journeys_hit", services=services)


@server.tool(description="Each journey's SLO and the 30-day error budget its incidents used.")
def slo_status() -> str:
    return _call("slo_status")


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


@server.tool(description="WCC over the alerting services under an incident's symptom: each connected group of alerts is a separate problem.")
def alert_clusters(incident_id: str) -> str:
    return _call("alert_clusters", incident_id=incident_id)


@server.tool(description="algo.SPpaths: the k shortest call paths from one service down to another.")
def failure_paths(source: str, target: str, k: int = 3) -> str:
    return _call("failure_paths", source=source, target=target, k=k)


@server.tool(description="Betweenness over CALLS: the services the most call paths run through.")
def single_points_of_failure(limit: int = 8) -> str:
    return _call("single_points_of_failure", limit=limit)


@server.tool(description="Label propagation over CALLS: groups of services that mostly talk to each other, with their teams.")
def failure_domains() -> str:
    return _call("failure_domains")


@server.tool(description="Who actually knows a service: fixes they approved there, changes they made, owning team.")
def experts(service: str) -> str:
    return _call("experts", service=service)


@server.tool(description="Full-text search over past lessons, changes, alerts and incident titles.")
def search_memory(query: str) -> str:
    return _call("search_memory", query=query)


@server.tool(description="Resolved incidents shaped like this incident's leading suspect (vector index).")
def shape_matches(incident_id: str) -> str:
    return _call("shape_matches", incident_id=incident_id)


@server.tool(description="Services ranked by PageRank over the call graph.")
def critical_services(limit: int = 10) -> str:
    return _call("critical_services", limit=limit)


@server.tool(description="Read-only Cypher against the graph (200-row cap, 3 s timeout).")
def run_cypher(query: str) -> str:
    return _call("run_cypher", query=query)


def propose_action(incident_id: str, kind: str, target: str, summary: str, change_id: str | None = None,
                   reason: str = "", also_for: list[str] | None = None) -> str:
    return _call("propose_action", incident_id=incident_id, kind=kind, target=target, summary=summary,
                 change_id=change_id, reason=reason, also_for=also_for)


# On a public server (FAULTLINE_PUBLIC=1) the MCP endpoint is read-only: the one tool that writes isn't registered.
if os.environ.get("FAULTLINE_PUBLIC") != "1":
    server.tool(description=(
        "Propose a fix (rollback, revert_config, disable_flag, page_owner). A human approves it in the Hypocenter UI. "
        "also_for: other open incidents the same change explains, so one approval mitigates all of them."))(propose_action)


if __name__ == "__main__":
    if "--http" in sys.argv:
        server.run(transport="streamable-http", host="0.0.0.0", port=8091)
    else:
        server.run()
