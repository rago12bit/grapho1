# Faultline

**An on-call agent that blames a change only when alerts trace an unbroken path from it up to the symptom, and says so when nothing changed at all.**

Most "what broke?" tools blame the most recent deploy near the failing service. Faultline walks a FalkorDB graph of services, owners, deploys, config changes, feature flags, alerts and past incidents, and asks a stricter question: *is there a path in the call graph from this change to the symptom where every service along the way started alerting after the change landed?* Only those changes are real suspects. It shows the path hop by hop with every Cypher query it ran, proposes a fix that waits for a human, and writes what it learned back into the graph for the next incident.

## What's different

1. **The alert chain decides, not proximity.** In INC-301 a `cart` deploy is one hop from the failing `checkout` and more recent than the real cause. Nothing on its path alerted, so it scores 0.28. A connection-pool change two hops down, on `payments-db`, has `payments` and `payments-db` alerting in sequence above it, so it scores 0.90.
2. **"Nothing changed" is a first-class answer.** When no change on the path explains an incident, alerting dependencies with no change compete as suspects. In INC-303 the answer is "the search cluster failed first; this is infrastructure, page its owner", instead of blaming whatever deployed last.
3. **One change, many incidents.** Two teams often open separate incidents for one cause. Faultline checks whether the leading change also fully explains other open incidents (same alert-chain test), says so, and proposes one fix that covers all of them. One approval mitigates every linked incident. INC-301 (checkout) and INC-304 (refunds) are both the same pool change.
4. **Memory changes the score, not just the display.** Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson` into the graph, and the next investigation scores precedent from them. A test deletes the memory and checks that the same change then scores lower.
5. **Every number is explained, and the agent can't act alone.** Each suspect shows its five factor scores and its path, and every step shows its Cypher. Free-form queries run through `GRAPH.RO_QUERY`, so the database itself refuses writes. The agent and the MCP server can only propose fixes; there is no approve tool.

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18 2026).
Tracks: **01 Agents That Act on Connected Data** (main), **02 Agent Memory and Coordination**.

All company data is synthetic. Nothing outside the graph is ever changed.

## Run it

```bash
docker compose up -d --build
```

- UI: http://localhost:8090
- FalkorDB browser: http://localhost:3003 (graph `faultline_kestrel`)
- Tests: `docker compose run --rm app pytest -q`

The graphs are built on first start. **Reset demo** in the header rebuilds them.

To let Claude drive the investigation instead of the offline playbook, put a key in `.env` before starting:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
docker compose up -d
```

Without a key, a fixed playbook calls the same graph tools in the same order, so the demo always works. The header shows which mode is running.

## Four incidents, four kinds of answer

| Incident | What the graph shows | Verdict |
|---|---|---|
| **INC-301** Checkout p99 8s | A pgbouncer limit cut from 400 to 60 on `payments-db`, two hops below `checkout`. `payments` and `payments-db` both alerted after it. A deploy to `cart` was closer and more recent, but nothing on its path alerted. INC-212 (61 days ago) was the same kind of change on the same database. | `C-5101`, revert the config. High confidence. |
| **INC-302** 30% of mobile logins failing | An `auth` deploy one hop below `mobile-bff`, 6 minutes before, with a 401 spike on `auth`. A flag flip on `users` sits on the same path but `users` never alerted. INC-188 was an `auth` token-validation deploy too. | `D-5201`, roll back to 4.1.7. High confidence. |
| **INC-303** Search returns nothing | Nothing changed on `search`'s path. `catalog-es` went RED two minutes before `search` alerted. INC-240 was a node failure on the same cluster. | **No change caused this.** Page Discovery's on-call for `catalog-es`. |
| **INC-304** Refund API timing out | Opened separately by the Payments team. The same pool change sits one hop below `payments`, with `payments-db` alerting. | **Same root cause as INC-301.** One revert, proposed once, covers both. |

"No change on the path" is an answer, not a failure: in a graph, the absence of a path is evidence.

## How the graph is used

```
(:Person)-[:MEMBER_OF]->(:Team)<-[:OWNED_BY]-(:Service)-[:CALLS]->(:Service)
(:Person)-[:MADE]->(:Change {deploy|config|flag})-[:CHANGED]->(:Service)
(:Alert)-[:FIRED_ON]->(:Service)
(:Incident)-[:SYMPTOM_ON]->(:Service)
(:Incident)-[:ROOT_CAUSE]->(:Change)   (:Incident)-[:RESOLVED_BY]->(:Action)   (:Incident)-[:TAUGHT]->(:Lesson)
(:Action)-[:FOR]->(:Incident)   # one action can cover several incidents
(:Action)-[:TARGETS]->(:Service)   (:Action)-[:REVERTS]->(:Change)
(:Investigation)-[:INVESTIGATED]->(:Incident)   (:Investigation)-[:BLAMED]->(:Change)
```

The judge (`faultline/tools.py`, `rank_suspects`) does the multi-hop work:

1. `MATCH p = (s:Service {name: $symptom})-[:CALLS*1..5]->(d:Service)`: everything the symptom depends on, with hop counts.
2. Every deploy, config and flag change on those services in the two hours before the incident. Changes elsewhere are listed as ruled out, with the reason.
3. For each suspect, every call path from the symptom down to it. The **alert chain** is 1.0 only if every service on the path alerted after the change. This is what separates the real cause from a closer but unrelated deploy.
4. **Precedent**: resolved incidents whose `ROOT_CAUSE` was the same kind of change on the same service.
5. Score = 0.25 recency + 0.10 proximity + 0.35 alert chain + 0.15 change risk + 0.15 precedent. Alerting dependencies with no change are scored too, so infrastructure failures can win.
6. For the leading changes, every other open incident is checked with the same alert-chain test (`also_explains`). A full chain links the incidents, and the proposed fix gets a `FOR` edge to each of them.

Other graph tools: **PageRank** over `CALLS` (`algo.pageRank`) for criticality and node size, **blast radius** (every transitive caller and the internet-facing entry points), owners and on-call, a read-only Cypher tool (runs through `GRAPH.RO_QUERY`, so the database itself refuses writes), and the action and memory writes.

## Agent, memory and actions

- **Claude** (`claude-opus-5-5`, adaptive thinking) picks which of 14 tools to call, starting from `rank_suspects` and then checking it, and finishes with `submit_findings`. The **offline playbook** runs the same tools in a fixed order. Both return the same steps, so the UI shows exactly what was called and which Cypher ran.
- **Memory**: every investigation is stored as an `Investigation` node. The next run on the same incident starts by reading it. Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson`, which feed the precedent factor of the next investigation. The Memory tab shows what the agent has learned.
- **Actions**: the agent can only propose (`rollback`, `revert_config`, `disable_flag`, `page_owner`). A person approves in the UI. Approval writes a remediation change to the graph and marks the incident mitigated. It is a simulation: no real system is touched. Re-running an investigation never stacks duplicate proposals.
- **Tenants**: one FalkorDB graph per company (`faultline_kestrel`, `faultline_harbor`). Isolation comes from separate graphs, not a property filter, and a test proves it.

## MCP

The same tools are available to any MCP client:

```bash
docker compose exec -T app python -m faultline.mcp_server          # stdio
```

Claude Code: `claude mcp add faultline -- docker compose -f /path/to/faultline/docker-compose.yml exec -T app python -m faultline.mcp_server`

There is deliberately no approve tool. An agent can propose a fix; only a person can run it.

## Layout

```
faultline/db.py          FalkorDB access, per-tenant graphs, query trace, read-only guard
faultline/seed.py        the synthetic companies (deterministic)
faultline/tools.py       graph tools, the judge, actions, memory
faultline/agent.py       Claude tool loop + offline playbook + question router
faultline/mcp_server.py  MCP server over the same tools
faultline/api.py         FastAPI + static UI
web/                     UI (Cytoscape.js)
tests/                   pytest against a real FalkorDB, on separate test graphs
```

## Limits

- The company, its changes and its alerts are synthetic. A real deployment would ingest them from CI/CD, config management, the feature-flag service and the alerting system.
- The clock is frozen at 14:00 UTC on 17 Oct 2026, so every run tells the same story.
- Scoring weights are hand-set and explained in the UI, not learned.
