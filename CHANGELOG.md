# Changelog

Built during Graph Hacks (WeMakeDevs × FalkorDB, Oct 15–18 2026). The first commit is from Oct 17.

## v1.0.0 · 2026-10-18 · the submission

**The judge and its proof**
- Alert-chain judge over the call graph: a change is scored by whether alerts climb the path to the symptom in
  order, plus recency, risk, precedent and the fault simulator's footprint. "Nothing changed" and "a failing node"
  are answers too.
- Skeptic agent, confidence from the gap to the runner-up, and every claim in a verdict checked against the graph
  for what it says, not only that its ids exist.
- The argument as a subgraph: `Hypothesis`, `Evidence`, `CITES`, and the alerts linked by `CAUSED_BY`.
- Each fix proved on a `GRAPH.COPY` before a person approves it, then verified from which alerts cleared.
- Depth 1 against full depth (`depth_sweep`) and "not in the graph" answers (`find_named`).

**FalkorDB**
- All eight algorithms v4.14.10 ships do work in an investigation: BFS (dependencies), SSpaths (callers and
  footprints), SPpaths (failure paths), WCC (separate problems), MSF (how each spread), betweenness
  (chokepoints), label propagation (failure domains), PageRank (criticality).
- Vector and full-text indexes for memory, `GRAPH.RO_QUERY` for every read, one graph per company,
  `GRAPH.COPY` sandboxes for proofs, experiments and the load test.
- Found and reproduced a memory limit in exact betweenness (`scripts/betweenness-memory-repro.sh`).

**Memory, company and agents**
- Four agents handing off only through `Episode` nodes; investigations streamed live and replayable.
- Learning from outcomes and people's verdicts, per company, used only when cross-validation says so.
- Runtime and config from real Kubernetes manifests, deploys to commits, real Online Boutique releases,
  journeys and SLO budgets, the on-call rota, public post-mortems as precedent (fetched locally, never committed).
- Alertmanager and CI webhooks, uploads as new tenants, an MCP server (read-only on a public server).
- Conversations kept in the graph (`Session`, `Turn`): a follow-up knows what "it" refers to.
- Chat in the graph from a Slack export (Kestrel's is synthetic): channels, threads, mentions, and the decision
  trail of a change, from the discussion that led to it to its revert. Context for the agents, never scored.

**Evidence**
- All 360 RCAEval RE2/RE3 failures against RCAEval's own baseline code; Train Ticket held out until the method
  was frozen (v1 623d340, v2 8de51f4, v3 0b4ef76). 0.81 AC@1 over all 360; 0.71 held out, behind NSigma (0.76).
- Planted-fault eval with ablations and baselines: 94.5–96.8% top-1 on held-out seeds.
- Scale: 3,000 services, 211k nodes, a verdict in 73 ms.
- `make judge-check` (passes on a fresh clone), `make mutants` (25 of 25 caught), 198 tests.
- The Claude path tested end to end through the real Anthropic SDK with a scripted transport; `make live-run` for
  the real thing (needs a key).

**Site**
- Landing page, incident room, On-call, Explore, Break it (seven experiments on graph copies), Replay, Results.

**Renamed** from Faultline to Hypocenter on Oct 18: another entry had the same name.
