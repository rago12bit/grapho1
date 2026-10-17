# Faultline

**An on-call agent that blames a change only when alerts trace an unbroken path from it up to the symptom, and says so when nothing changed at all.**

Most "what broke?" tools blame the most recent deploy near the failing service. Faultline walks a FalkorDB graph of services, owners, deploys, config changes, feature flags, alerts and past incidents, and asks a stricter question: *is there a path in the call graph from this change to the symptom where every service along the way started alerting after the change landed?* Only those changes are real suspects. It shows the path hop by hop with every Cypher query it ran, proposes a fix that waits for a human, and writes what it learned back into the graph for the next incident.

## What's different

1. **The alert chain decides, not proximity.** In INC-301 a `cart` deploy is one hop from the failing `checkout` and more recent than the real cause. Nothing on its path alerted, so it scores 0.27. A connection-pool change two hops down, on `payments-db`, has `payments-db`, `payments` and `checkout` alerting in that order above it, so it scores 0.94.
2. **"Nothing changed" is a first-class answer.** When no change on the path explains an incident, alerting dependencies with no change compete as suspects. In INC-303 the answer is "the search cluster failed first; this is infrastructure, page its owner", instead of blaming whatever deployed last.
3. **One change, many incidents.** Two teams often open separate incidents for one cause. Faultline checks whether the leading change also fully explains other open incidents (same alert-chain test), says so, and proposes one fix that covers all of them. One approval mitigates every linked incident. INC-301 (checkout) and INC-304 (refunds) are both the same pool change.
4. **Memory changes the score, not just the display.** Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson` into the graph, and the next investigation scores precedent from them. A test deletes the memory and checks that the same change then scores lower.
5. **A fault simulator checks every suspect.** For each one it asks "if this broke, which services would alert, and in what order?" and compares that with what actually alerted: "explains 3 of 3 alerting services, in the predicted order". Alerts the leader can't explain point to **independent faults**: in INC-302 the auth deploy explains 2 of the 7 alerting services under `mobile-bff`, and Faultline names the other two faults behind the rest (the pool change of INC-301/304, the search cluster of INC-303). A cause that never alerts itself (a queue, a database) still counts when everything above it did, and a change the failure climbed *past* is marked down.
6. **Four agents, one graph, and a skeptic among them.** Triage, Investigator, Skeptic and Responder never pass anything to each other in code: each writes an `Episode` to the graph and the next one starts by reading it back, so an investigation can be resumed by another process or session. The **Skeptic** tries to knock the leader over before anyone acts: what doesn't it explain, did its alerts come in the right order, did the change land before the incident, is it clearly ahead? If a rival explains more and scores within reach, the verdict is overturned. Confidence comes from the **gap** to the runner-up, not the raw score. Verdicts stay in the graph as scored `SUSPECTED_CAUSE` and `SIMILAR_TO` edges, and the Responder attaches the team's `Runbook`.
7. **Measured, not claimed.** An eval plants hundreds of random faults on the same topology and reports accuracy, the drop when each factor is switched off, and two naive baselines (see [Evaluation](#evaluation)): 96–97.5% top-1 and 100% top-3 on held-out seeds, against 64% for "blame the most recent change". When Faultline says **high** confidence it has been right every time.
8. **Every claim is checked, every number is explained, and the agent can't act alone.** Every change id, incident id, service and clock time in a verdict is looked up in the graph, and the verdict must hang together (the blamed change exists, changed the named service, and is among the judge's top suspects); claims Claude makes that the graph can't back are dropped from the evidence and shown as such. Each suspect shows its factor scores, its footprint and its path, and every step shows its Cypher. Free-form queries run through `GRAPH.RO_QUERY`, so the database itself refuses writes. The agent and the MCP server can only propose fixes; there is no approve tool.

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
| **INC-302** 30% of mobile logins failing | An `auth` deploy one hop below `mobile-bff`, 6 minutes before, with a 401 spike on `auth`. A flag flip on `users` sits on the same path but `users` never alerted. INC-188 was an `auth` token-validation deploy too. Five other services below `mobile-bff` are alerting, but not through `auth`. | `D-5201`, roll back to 4.1.7. The other alerts belong to two **independent faults**: C-5101 (INC-301, INC-304) and `catalog-es` (INC-303). |
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
(:Episode {role})-[:ABOUT]->(:Incident)   (:Episode)-[:READ]->(:Episode)   # agent handoffs
(:Incident)-[:SUSPECTED_CAUSE {rank, score, coverage}]->(:Change|:Service)
(:Incident)-[:SIMILAR_TO {score, reason}]->(:Incident)
(:Runbook {steps})-[:FOR_SERVICE]->(:Service)
```

The judge (`faultline/tools.py`, `rank_suspects`) does the multi-hop work:

1. `MATCH p = (s:Service {name: $symptom})-[:CALLS*1..5]->(d:Service)`: everything the symptom depends on, with hop counts.
2. Every deploy, config and flag change on those services in the two hours before the incident. Changes elsewhere are listed as ruled out, with the reason.
3. For each suspect, every call path from the symptom down to it. The **alert chain** is 1.0 only if every service on the path alerted after the change, deepest first. Out of order keeps 70%; a silent cause under a fully alerting path keeps 60%; a change the failure was already climbing past (something below it alerted first) is halved.
4. **Footprint** (the fault simulator): the services that call the suspect, transitively, are where its failure can show. Of the alerting services joined to the symptom through other alerting services (the symptom's *alert cone*), the share in the footprint is the factor. Everything alerting outside the leader's footprint goes to `other_faults`.
5. **Precedent**: resolved incidents whose `ROOT_CAUSE` was the same kind of change on the same service.
6. Change score = 0.20 recency + 0.05 proximity + 0.30 alert chain + 0.15 change risk + 0.15 precedent + 0.15 footprint. Alerting dependencies are scored as "nothing changed here" suspects (alert chain, earliness, precedent, proximity, footprint, and how long ago anything on or under them changed), unless a recent change on or under them explains their alert better.
7. For the leading changes, every other open incident is checked with the same alert-chain test (`also_explains`). A full chain links the incidents, and the proposed fix gets a `FOR` edge to each of them.

Other graph tools: **PageRank** over `CALLS` (`algo.pageRank`) for criticality and node size, **what-if** (`simulate_failure`: the waves in which alerts would arrive if a service failed, entry points hit, teams paged, runbooks), **blast radius**, owners and on-call, runbooks, a read-only Cypher tool (runs through `GRAPH.RO_QUERY`, so the database itself refuses writes), and the action and memory writes.

## Evaluation

```bash
docker compose run --rm app python -m faultline.eval              # 150 scenarios, seed 7, ~3 s
docker compose run --rm app python -m faultline.eval --n 400 --seed 3
```

The eval plants faults on Kestrel's real topology, one incident per scenario, spaced so their windows never overlap: **deep** (a change 1–3 hops below the symptom, alerts climbing to it), **silent** (the same, but the cause never alerts), **infra** (nothing changed; a dependency failed) and **shared** (a deep change that also breaks a second service, which opens its own incident). Every scenario carries distractors: a change close to the symptom (sometimes newer than the cause, sometimes older) with no alerts below it, a change off the path, and an old low-risk change on it. Top-1 accuracy, seed 7:

| | deep (38) | silent (38) | infra (37) | shared (37) | **all (150)** | top-3 all |
|---|---|---|---|---|---|---|
| **Faultline (judge)** | 100.0% | 84.2% | 100.0% | 100.0% | **96.0%** | 100.0% |
| **Faultline (judge + skeptic)** | 100.0% | 89.5% | 100.0% | 100.0% | **97.3%** | 100.0% |
| no recency | 100.0% | 44.7% | 100.0% | 100.0% | **86.0%** | 100.0% |
| no proximity | 100.0% | 86.8% | 100.0% | 100.0% | **96.7%** | 100.0% |
| no alert_chain | 89.5% | 84.2% | 100.0% | 86.5% | **90.0%** | 100.0% |
| no risk | 100.0% | 68.4% | 100.0% | 100.0% | **92.0%** | 100.0% |
| no precedent | 100.0% | 84.2% | 100.0% | 100.0% | **96.0%** | 100.0% |
| no footprint | 100.0% | 73.7% | 100.0% | 100.0% | **93.3%** | 100.0% |
| nearest recent change | 7.9% | 2.6% | 0.0% | 24.3% | **8.7%** | 75.3% |
| most recent change | 81.6% | 89.5% | 0.0% | 86.5% | **64.7%** | 75.3% |

When it says… **high** confidence (60 cases): right 100.0%; **medium** confidence (56 cases): right 96.4%; **low** confidence (34 cases): right 94.1%.

Linked incidents (shared scenarios): precision 1.0, recall 1.0.

Learning: the same 150 faults planted again after the first round was resolved: top-1 96.0% → 97.3%. Without the precedent factor the second round scores 96.0%.

Held-out seeds (400 scenarios each), judge → judge + skeptic: 95.5% → 96.0% (seed 3), 95.8% → 96.8% (seed 11), 96.8% → 97.5% (seed 42); top-3 100% on all three; linked-incident precision and recall 1.0. Confidence on the held-out seeds: **high** was right 100% of the time, **medium** 97.6–99.4%, **low** 85.4–89.2%.

How to read it honestly:
- The scenario generator is mine, so this measures the judge against the failure patterns it was designed for, not against real outages. The weights were adjusted while looking at seed 7; the held-out seeds are the fairer number.
- Risk labels are drawn at random for causes and distractors alike, so in this eval risk is noise and switching it off can help some kinds. In real change logs risky changes do break things more often.
- Precedent does nothing on a first sighting (memory is empty). Its effect is the learning row: the same faults again after round one was resolved.
- Silent causes are the weak spot (84–90% with the skeptic): with no alert of their own, a recent change one level up can still look better.
- Confidence is conservative: "medium" and "low" verdicts are right more often than the labels suggest. It is still ordered correctly (high > medium > low), which is what an on-call engineer needs to know when to double-check.

## Agent, memory and actions

- **Four agents.** *Triage* reads the incident, its dependencies, recent changes and alerts. *Investigator* reads triage's handoff from the graph, runs the judge and the fault simulator, and checks precedent. *Skeptic* reads the investigator's handoff and challenges the leader (deterministic checks, up to two rounds, may overturn). *Responder* reads the skeptic's verdict, finds the owner and the runbook, and proposes the fix. Each writes an `Episode` that the next reads back (`(:Episode)-[:READ]->(:Episode)`); nothing is passed in memory.
- **Claude** (`claude-opus-5-5`, adaptive thinking) plays each role with only that role's tools. The **offline playbook** runs the same roles and tools in a fixed order, so the demo works without a key. Both return the same steps, labelled by agent, with the Cypher behind each.
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
faultline/agent.py       the three agents (Claude or offline playbook) + question router
faultline/eval.py        planted-fault accuracy eval with ablations, baselines and a learning run
faultline/mcp_server.py  MCP server over the same tools
faultline/api.py         FastAPI + static UI
web/                     UI (Cytoscape.js)
tests/                   pytest against a real FalkorDB, on separate test graphs
```

## Limits

- The company, its changes and its alerts are synthetic. A real deployment would ingest them from CI/CD, config management, the feature-flag service and the alerting system.
- The clock is frozen at 14:00 UTC on 17 Oct 2026, so every run tells the same story.
- Scoring weights are hand-set and explained in the UI, not learned. The eval shows what each one contributes.
- The fault simulator models the call graph only. It does not know fault types (CPU, memory, network) or traffic volume, which would sharpen the predicted footprint.
- The Claude-driven agents have not been run against the live API in this repo's tests; the offline playbook is what the tests and the eval exercise.
