# Faultline

**An on-call agent that blames a change only when alerts trace an unbroken path from it up to the symptom, and says so when nothing changed at all.**

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), entering **Tracks 01, 02 and 03**. Run all of it with one command (`docker compose up -d --build`, then http://localhost:8090), or put it on a public URL with [deploy/DEPLOY.md](deploy/DEPLOY.md).

![Faultline's case file for INC-301: the failure path drawn from checkout down to payments-db in red, the blast radius in dashed amber, the verdict and its evidence on the right](docs/img/case-file.png)

<sub>INC-301 opened: the answer is on screen at once. Red is the failure path the alerts climbed (payments-db → payments → checkout), dashed amber the callers it reaches. The `cart` deploy one hop away is newer and closer, but nothing under it alerted, so it scores 0.27 against 0.94.</sub>

| Track | What Faultline does |
|---|---|
| **01 · Agents that act on connected data** | An alert-chain judge over `CALLS*1..5`, a fault simulator per suspect, and **WCC, SPpaths, betweenness, label propagation and PageRank used during the investigation**. Every claim is checked against the graph. Fixes wait for a human, then are **verified from the alerts clearing**. **Monitoring and CI push in live** (Alertmanager webhook, change events): Faultline opens the incident and investigates it by itself. An MCP server exposes the same tools. |
| **02 · Agent memory and coordination** | Four agents hand off **only through `Episode` nodes** (resumable by another process). Lessons and precedent change the score; past incidents are matched by shape on a vector index and by full text. A **fix track record** (what worked, what didn't, counted from verified outcomes) shapes what the responder says. A load test **in the app** shows isolation and consistency under 50 parallel investigations. |
| **03 · Company brain** | Ownership and on-call, **experts ranked by the fixes they approved, the changes they made and their commits** (real CODEOWNERS and git history for Online Boutique), runbooks per service, lessons searchable by full text, a postmortem export for any incident, and a card per service with the queries behind it. |

**What's real.** Online Boutique, Sock Shop and Train Ticket are real: call graphs, metrics, logs, traces, endpoints and root causes from all 360 RCAEval RE2/RE3 failures, plus Online Boutique's owners and contributors from its CODEOWNERS and git history. Kestrel Pay is a synthetic company built to show every kind of answer. Nobody's on-call rota or fix approvals are public, so the Boutique tenant has none rather than invented ones.

Most "what broke?" tools blame the most recent deploy near the failing service. Faultline walks a FalkorDB graph of services, owners, deploys, config changes, feature flags, alerts and past incidents, and asks a stricter question: *is there a path in the call graph from this change to the symptom where every service along the way started alerting after the change landed?* Only those changes are real suspects. It shows the path hop by hop with every Cypher query it ran, proposes a fix that waits for a human, and writes what it learned back into the graph for the next incident.

## What's different

1. **The alert chain decides, not proximity.** In INC-301 a `cart` deploy is one hop from the failing `checkout` and more recent than the real cause. Nothing on its path alerted, so it scores 0.27. A connection-pool change two hops down, on `payments-db`, has `payments-db`, `payments` and `checkout` alerting in that order above it, so it scores 0.94.
2. **"Nothing changed" is a first-class answer.** When no change on the path explains an incident, alerting dependencies with no change compete as suspects. In INC-303 the answer is "the search cluster failed first; this is infrastructure, page its owner", instead of blaming whatever deployed last.
3. **One change, many incidents.** Two teams often open separate incidents for one cause. Faultline checks whether the leading change also fully explains other open incidents (same alert-chain test), says so, and proposes one fix that covers all of them. One approval mitigates every linked incident. INC-301 (checkout) and INC-304 (refunds) are both the same pool change.
4. **Memory changes the score, not just the display.** Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson` into the graph, and the next investigation scores precedent from them. A test deletes the memory and checks that the same change then scores lower.
5. **A fault simulator checks every suspect.** For each one it asks "if this broke, which services would alert, and in what order?" and compares that with what actually alerted: "explains 3 of 3 alerting services, in the predicted order". Alerts the leader can't explain point to **independent faults**: in INC-302 the auth deploy explains 2 of the 7 alerting services under `mobile-bff`, and Faultline names the other two faults behind the rest (the pool change of INC-301/304, the search cluster of INC-303). A cause that never alerts itself (a queue, a database) still counts when everything above it did, and a change the failure climbed *past* is marked down.
6. **Four agents, one graph, and a skeptic among them.** Triage, Investigator, Skeptic and Responder never pass anything to each other in code: each writes an `Episode` to the graph and the next one starts by reading it back, so an investigation can be resumed by another process or session. The **Skeptic** tries to knock the leader over before anyone acts: what doesn't it explain, did its alerts come in the right order, did the change land before the incident, is it clearly ahead? If a rival explains more and scores within reach, the verdict is overturned. Confidence comes from the **gap** to the runner-up, not the raw score. Verdicts stay in the graph as scored `SUSPECTED_CAUSE` and `SIMILAR_TO` edges, and the Responder attaches the team's `Runbook`.
7. **Measured, not claimed.** An eval plants hundreds of random faults on the same topology and reports accuracy, the drop when each factor is switched off, and two naive baselines (see [Evaluation](#evaluation)): 94.5–96.8% top-1 and 99.8–100% top-3 on held-out seeds, against 50% for "blame the most recent change". When Faultline says **high** confidence it has been right 99.2–100% of the time. On **real telemetry** it runs on every RCAEval RE2 and RE3 case (360 failures in three systems) against RCAEval's own baseline code, run here on the same cases: 0.70 first on the 90 held-out Train Ticket cases, ahead of BARO (0.67) and behind NSigma (0.76), with a worse top five than both (see [Public data](#public-data-every-rcaeval-case-against-the-published-baselines)). All 360 failures are loaded into the app as three more companies.
8. **Every claim is checked, every number is explained, and the agent can't act alone.** Every change id, incident id, service and clock time in a verdict is looked up in the graph, and what the claim *says* must match it ("C-5101 changed auth" fails: it changed payments-db; "D-5102 caused it" fails: it was ruled out; "payments alerted before payments-db" fails: the times say otherwise). The verdict must also hang together (the blamed change exists, changed the named service, and is among the judge's top suspects); claims Claude makes that the graph can't back are dropped from the evidence and shown as such. Each suspect shows its factor scores, its footprint and its path, and every step shows its Cypher. Free-form queries run through `GRAPH.RO_QUERY`, so the database itself refuses writes. The agent and the MCP server can only propose fixes; there is no approve tool.
9. **The reasoning is a subgraph you can query.** Every suspect becomes a `Hypothesis` (standing, overturned or rejected) with `Evidence` for and against it, and each piece of evidence `CITES` the alerts, changes and past incidents it rests on. The skeptic's checks run against *every* hypothesis, so a rejected one says why it lost ("leaves auth unexplained; 0.33 behind D-5201"). For the hypothesis left standing, the alerts are linked into a **causal chain**: `(:Alert)-[:CAUSED_BY {lag_s, confidence}]->(:Alert)…->(:Change)`. The topology decides which links are possible and the alert timing picks among them, naming silent hops ("via api") and ending at the change, or at the service that failed on its own. One query answers "why did this alert fire?": `MATCH p = (:Alert {id: 'A-9102'})-[:CAUSED_BY*]->(root) RETURN p` gives A-9102 on mobile-bff ← A-9101 on auth ← D-5201.
10. **It knows where things run.** Every service has its `Deployment` (replicas, CPU and memory limits, image and tag, the config keys, ConfigMaps and Secrets it reads), from the projects' real Kubernetes manifests for Online Boutique, Sock Shop and Train Ticket (secret values never kept), and pods scheduled onto `Node`s in `Zone`s for Kestrel. Two things follow. When two services that don't call each other fail on their own within minutes, and share one node, the judge names the **node** (INC-305: `catalog` and `users` on `k8s-node-4`; without placement it reports two independent faults). And a CPU or memory alert cites the limit the manifest sets ("checkoutservice is capped at 0.2 CPU"). A config change points at the `ConfigKey` it sets and every deployment that reads it.
11. **From deploy to commit.** Every Kestrel deploy `SHIPS` the commits behind it, and the investigator asks which one to read first: the commit that changed the most request-path code (docs, tests and dependency files don't count) and shares the most words with what the incident shows. For D-5201 that is "validate tokens against the rotating JWKS cache", not the runbook or the tests, and the argument cites the `Commit`. Online Boutique's last eight real releases are in its graph (`Version`-[:INCLUDES]->`Commit`, files and lines changed per service, the manifests' images `BUILT_FROM` v0.10.7), and the Company tab shows each team's deploys per week, change failure rate, time to restore and lead time, computed from the graph.
12. **It learns from its own outcomes, and from you.** On any hypothesis a person can say **not the cause** (it drops out of that incident at once, with their reason in the argument) or **confirm** it; both are kept as `Feedback` nodes. Each real company's judge is also refitted on its own solved incidents: every silent-failure suspect of every solved incident is a training row (its factors plus a **track record**: how often that service turned out to be the cause when it alerted, never counting the incident being scored), labelled by the cause the incident was closed with, people's verdicts included. A logistic regression is fitted and used only if, cross-validated over the solved incidents alone, it names the cause at least as often as the hand-set weights; the open incidents stay a clean test:

    | open incidents (not used to fit or decide) | hand weights | learned | Brier, hand → learned |
    |---|---|---|---|
    | Online Boutique (18) | 15 | 16 | 0.144 → 0.079 |
    | Sock Shop (27) | 21 | 22 | 0.105 → 0.082 |
    | Train Ticket (26) | 21 | 21 | 0.096 → 0.075 |

    Small gains in top-1 and clearly better calibration: when the learned model says 0.9 it is right about 0.9 of the time (the Memory tab shows the calibration bins). Services that alert in every incident and are rarely the cause (Online Boutique's `redis`) get a low track record and stop crowding the top. One cost found and not hidden: on Train Ticket the skeptic, whose takeover margin was set for hand scores, overturns four learned leaders and brings 21 down to 19. The RCAEval numbers above are the judge without any history (each case is its own graph); this is what history adds.
13. **Impact in customers' terms, and who was on call then.** `Journey` nodes say what a customer does and which services it needs. Train Ticket's 14 and Online Boutique's 11 come from the traces (each request entering the system and everything its calls reach; a client span that names an uninstrumented service, like `hipstershop.CartService/GetCart`, adds it and its datastore), with no SLO invented. Kestrel's six carry SLOs, so a case file says "checkout: 38 of 43.2 minutes of its 30-day error budget gone", and the Company tab keeps a 30-day budget per journey that charges an incident only to journeys needing the service the failure was *in*, not where it was noticed. A weekly on-call rota (`OnCallShift`) answers who was on call when it started, not just now.
14. **Watch it work, and replay what happened.** Running the agents streams each tool call to the case file as it happens (server-sent events, `GET …/investigate/stream`): which agent, what it did, how many queries and milliseconds in FalkorDB. The offline playbook finishes in well under a second, so the page asks the server to space steps out (`pace_ms`, at most 500); it waits between real steps, nothing is replayed. Under the graph a scrubber replays the incident: drag or press play and changes land (dashed), services start alerting and each causal hop appears with its lag, minute by minute.
15. **Other companies' outages as precedent, without taking their words.** `python -m faultline.public_postmortems --fetch` reads [Dan Luu's list of public post-mortems](https://github.com/danluu/post-mortems) (about 240 write-ups, each a link to the company's own report) into a local `knowledge` graph, `(:PublicPostmortem)-[:IN_CLASS]->(:FailureClass)`. A case file then lists the public outages that look like its leading explanation: same failure class (a config change → config errors, a failing node → hardware), at least two shared words, with the link and the words that matched. The list has no licence, so nothing from it is in this repository; the importer only fetches it on your machine, and a test checks the fixtures stay clean. Precedent only, never scored.
16. **Graph algorithms do real work in the investigation:** WCC splits the alerts into separate problems, SPpaths traces the failure path, betweenness flags chokepoints on it, label propagation names the failure domains and teams, PageRank ranks criticality. See [the table](#falkordb-graph-algorithms-used-during-the-investigation).

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18 2026).
Tracks: **01 Agents That Act on Connected Data** (main), **02 Agent Memory and Coordination**, **03 Company Brain**.

Kestrel Pay and Harbor Freight are synthetic. **Online Boutique, Sock Shop and Train Ticket** are real: every failure in the public RCAEval benchmark's RE2 and RE3 suites (call graphs, metrics, logs, operation-level traces, root causes), with Online Boutique's owners and contributors from its own CODEOWNERS and git history. Nothing outside the graph is ever changed.

## Run it

```bash
docker compose up -d --build
```

- UI: http://localhost:8090
- FalkorDB browser: http://localhost:3003 (graph `faultline_kestrel`)
- Tests: `docker compose run --rm app pytest -q`

The graphs are built on first start. **Reset** in the header rebuilds them. **Guided demo** (or http://localhost:8090/?demo=1) plays the whole story on the live app with captions.

### The app

- **Answer first.** Opening an incident shows the case file at once: the verdict, confidence, how many of its claims were checked against the graph, and the one-paragraph story. It is the judge and the skeptic reading the graph directly (about 70 read-only queries, ~20 ms); nothing is written. **Run the four agents** re-derives it step by step, records the handoffs in the graph and proposes the fix. Their steps sit folded under *Agent handoffs*, each with its Cypher.
- **Case file sections:** root cause (path, change, spread, what it explains, fix, owners, score), **blast radius** (one block per service the failure reaches: the path to it, internet-facing or not, owning team and on-call, alerting now or quiet), proposed fixes with approve and verify, a **timeline** of every alert and change in the window (real metric traces as sparklines on Online Boutique), the skeptic's checks, alert groups (WCC), other faults, memory and precedent, evidence, and every suspect with its factor bars.
- **Focused graph.** With a case open, the canvas shows only what the case touches (the symptom, the top suspects' paths, everything alerting, the callers the cause reaches) and lays it out again; *Whole system* puts everything back. *replay traversal* walks the failure path again.
- **Risk board** (every incident, open first by severity, with the suspect the judge leads with and its confidence), **Memory** (solved incidents, lessons, full-text search over them) and **Company** (teams and on-call, chokepoints by betweenness, failure domains by label propagation, and a card per service: owners, who actually knows it, callers, recent changes, past incidents, runbooks, and the queries behind the card). Click any node on the graph to open its card.
- **Command bar** (ctrl k): an incident id opens it; anything else is a question for the graph, answered with the tool calls and queries behind it.
- The header shows FalkorDB's live node and edge counts, the test count and the two eval numbers, and turns red with a banner if the database goes away. Light and dark themes follow the OS; the ◐ button overrides it.
- **Export postmortem** on any case file: Markdown built only from the graph (summary, root cause with author and lag, how we know, a timeline, impact, the fix and its verification, linked incidents, the lesson, similar past incidents, and the Cypher behind it). A draft until the incident is resolved.

![Real RCAEval telemetry in the Boutique tenant: each alert's metric as a sparkline, the leader highlighted](docs/img/real-data.png)

### Live input: monitoring and CI push, Faultline acts

```bash
demo/fire.sh            # CI posts a fraud-scoring deploy, Alertmanager posts three alerts climbing to checkout
demo/fire.sh resolve    # the same alerts, resolved: an approved fix is verified by real recovery
```

- `POST /api/{tenant}/changes`: a deploy, config or flag change from CI/CD (`{kind, service, summary, at, author, version, ...}`).
- `POST /api/{tenant}/alerts/alertmanager`: Alertmanager's webhook body (Grafana alerting sends the same). Alerts are idempotent on their fingerprint; unknown services are reported back, not fatal; bodies over 1 MB are refused.
- **Opening rule** (`faultline/ingest.py`): a paging alert on a tier-1 or internet-facing service opens an incident unless an unresolved one already covers that service (it is the symptom or below it) within an hour. The symptom is the paging service with the most new alerts below it. The four agents investigate at once, and the response names the cause: the `fire.sh` run opens INC-501 and blames `D-6001` with high confidence, and the responder adds that rolling back `fraud-scoring` worked the one time it was tried (INC-221).
- An approved fix on a live incident is not simulated: it waits for monitoring, and the resolved alerts verify it. One investigation per incident at a time (a second gets 409).

### Bring your own incident

On the start panel, or `POST /api/upload`: a JSON file in Faultline's format ([web/examples/incident.json](web/examples/incident.json): services, calls or spans, people, changes, alerts, the incident), or one case zipped in RCAEval's layout (`inject_time.txt`, metrics and traces as parquet or CSV, read with the same detector as the benchmark). Each upload becomes its own graph (`up_…`) and the judge reads it at once; the example's answer is `C-102`, a connection limit cut two hops below the storefront. Uploads are capped (2 MB JSON, 40 MB zip, 400 MB unzipped), names and counts validated, zip members read by name only (no paths), one at a time, and only the 8 newest are kept. A trace export has no deploys in it, so its answers are always "nothing changed" infrastructure suspects.

To let Claude drive the investigation instead of the offline playbook, put a key in `.env` before starting:

```bash
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
docker compose up -d
```

Without a key, a fixed playbook calls the same graph tools in the same order, so the demo always works. The header shows which mode is running.

**Demo video:** `demo/` regenerates it from the running app. Put one voiceover MP3 per scene of `demo/narration.json` in `data/demo/audio/` (with a `durations.txt` of `<scene> <seconds>` lines), then run `node demo/record.mjs data/demo/audio data/demo/rec` (needs `playwright-core@1.64.1`) to record the real UI in time with the voice, and `demo/mix.py` in any container with ffmpeg to lay in the audio, burn in captions and write `data/demo/faultline-demo.mp4`.

## Five incidents, five kinds of answer

| Incident | What the graph shows | Verdict |
|---|---|---|
| **INC-301** Checkout p99 8s | A pgbouncer limit cut from 400 to 60 on `payments-db`, two hops below `checkout`. `payments` and `payments-db` both alerted after it. A deploy to `cart` was closer and more recent, but nothing on its path alerted. INC-212 (61 days ago) was the same kind of change on the same database. | `C-5101`, revert the config. High confidence. |
| **INC-302** 30% of mobile logins failing | An `auth` deploy one hop below `mobile-bff`, 6 minutes before, with a 401 spike on `auth`. A flag flip on `users` sits on the same path but `users` never alerted. INC-188 was an `auth` token-validation deploy too. Five other services below `mobile-bff` are alerting, but not through `auth`. | `D-5201`, roll back to 4.1.7. The other alerts belong to two **independent faults**: C-5101 (INC-301, INC-304) and `catalog-es` (INC-303). |
| **INC-303** Search returns nothing | Nothing changed on `search`'s path. `catalog-es` went RED two minutes before `search` alerted. INC-240 was a node failure on the same cluster. | **No change caused this.** Page Discovery's on-call for `catalog-es`. |
| **INC-304** Refund API timing out | Opened separately by the Payments team. The same pool change sits one hop below `payments`, with `payments-db` alerting. | **Same root cause as INC-301.** One revert, proposed once, covers both. |
| **INC-305** Product and account pages failing | Nothing changed. `catalog` and `users` both failed on their own, a minute apart; neither calls the other, and the only thing they share is `k8s-node-4` (zone-b). | **A failing host**: cordon and drain `k8s-node-4`, page Platform. The causal chain ends at the `Node`. Without placement in the graph the same alerts read as two independent faults. |

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
(:Incident)-[:HAS_HYPOTHESIS]->(:Hypothesis {rank, score, status})-[:BLAMES]->(:Change|:Service)
(:Hypothesis)-[:SUPPORTED_BY|CONTRADICTED_BY]->(:Evidence {kind, text, weight})-[:CITES]->(:Alert|:Change|:Incident|:Service)
(:Alert)-[:CAUSED_BY {incident, lag_s, confidence, direct, via}]->(:Alert|:Change|:Service)   # the causal chain
(:Service)-[:EXPOSES]->(:Operation {rps, p50_ms, p99_ms, err})-[:CALLS {rps, p50_ms, p99_ms, err}]->(:Operation)
(:Alert {strength})-[:ON_OPERATION {p90_before_ms, p90_after_ms, ratio}]->(:Operation)   # the endpoint that slowed
(:LogPattern {template, new})-[:LOGGED_BY]->(:Service)   (:LogPattern)-[:SEEN_IN {count}]->(:Incident)
(:Service)-[:DEPLOYED_AS]->(:Deployment {replicas, cpu_limit, mem_limit_mi, probe})-[:RUNS_IMAGE]->(:Image {repo, tag})
(:Deployment)-[:READS {value}]->(:ConfigKey)<-[:SETS]-(:Change)   (:Deployment)-[:USES_CONFIG|USES_SECRET]->(:ConfigMap|:Secret)
(:Deployment)-[:HAS_POD]->(:Pod)-[:ON_NODE]->(:Node)-[:IN_ZONE]->(:Zone)   (:Node)-[:OWNED_BY]->(:Team)
(:Change {kind: 'deploy'})-[:SHIPS]->(:Commit {sha, message, code_lines})-[:TOUCHED]->(:Service)   (:Person)-[:AUTHORED]->(:Commit)
(:Journey {slo, rps})-[:USES]->(:Service)   (:Journey)-[:STARTS_AT]->(:Operation)
(:Person)-[:ON_SHIFT]->(:OnCallShift {from, to})-[:COVERS]->(:Team)
(:Feedback {verdict, by, note})-[:ABOUT]->(:Incident)   (:Model {kind, weights, enabled, evaluation})   # learned from outcomes
(:Version {tag})-[:INCLUDES]->(:Commit)-[:MENTIONS]->(:Service)   (:Version)-[:CHANGED {files, additions}]->(:Service)   (:Image)-[:BUILT_FROM]->(:Version)
```

The judge (`faultline/tools.py`, `rank_suspects`) does the multi-hop work:

1. `MATCH p = (s:Service {name: $symptom})-[:CALLS*1..5]->(d:Service)`: everything the symptom depends on, with hop counts.
2. Every deploy, config and flag change on those services in the two hours before the incident. Changes elsewhere are listed as ruled out, with the reason.
3. For each suspect, every call path from the symptom down to it. The **alert chain** is 1.0 only if every service on the path alerted after the change, deepest first. Out of order keeps 70%; a silent cause under a fully alerting path keeps 60%; a change the failure was already climbing past (something below it alerted first) is halved.
4. **Footprint** (the fault simulator): the services that call the suspect, transitively, are where its failure can show. Of the alerting services joined to the symptom through other alerting services (the symptom's *alert cone*), the share in the footprint is the factor. Everything alerting outside the leader's footprint goes to `other_faults`.
5. **Precedent**: resolved incidents whose `ROOT_CAUSE` was the same kind of change on the same service.
6. Change score = 0.20 recency + 0.05 proximity + 0.30 alert chain + 0.15 change risk + 0.15 precedent + 0.15 footprint. Alerting dependencies are scored as "nothing changed here" suspects (alert chain, earliness, precedent, proximity, footprint, and how long ago anything on or under them changed), unless a recent change on or under them explains their alert better.
7. For the leading changes, every other open incident is checked with the same alert-chain test (`also_explains`). A full chain links the incidents, and the proposed fix gets a `FOR` edge to each of them.

### FalkorDB graph algorithms, used during the investigation

| Algorithm | Tool | Where the agents use it |
|---|---|---|
| `algo.WCC` | `alert_clusters` | **Investigator** copies the alerting services under the symptom (and the calls between them) into a scratch graph and splits them into weakly connected components. Each component is a separate problem: INC-302's alerts form exactly three groups, one per cause. The **Skeptic** fails the verdict if any group is explained by neither the leader nor an independent fault. The scratch graph is always dropped. |
| `algo.SPpaths` | `failure_paths` | **Investigator** lists the k shortest call paths from the symptom down to the leading suspect. |
| `algo.betweenness` | `single_points_of_failure` | **Responder** checks whether the failure path runs through the services most call paths depend on ("runs through payments, #1 by betweenness"). FalkorDB samples betweenness by default; Faultline samples every node with a fixed seed so the ranking is repeatable. |
| `algo.labelPropagation` | `failure_domains` | **Responder** names the failure domains (groups of services that mostly talk to each other) and their teams that the alerts reach. |
| `algo.pageRank` | `critical_services` | Criticality, node size in the UI, blast radius. |

Other graph tools: **what-if** (`simulate_failure`: the waves in which alerts would arrive if a service failed, entry points hit, teams paged, runbooks), **blast radius**, owners and on-call, runbooks, a read-only Cypher tool (runs through `GRAPH.RO_QUERY`, so the database itself refuses writes), and the action and memory writes. Every algorithm is also available to Claude (per role) and over MCP.

## Evaluation

```bash
docker compose run --rm app python -m faultline.eval              # 150 scenarios, seed 7, ~3 s
docker compose run --rm app python -m faultline.eval --n 400 --seed 3
```

The eval plants faults on Kestrel's real topology, one incident per scenario, spaced so their windows never overlap: **deep** (a change 1–3 hops below the symptom, alerts climbing to it), **silent** (the same, but the cause never alerts), **infra** (nothing changed; a dependency failed), **shared** (a deep change that also breaks a second service, which opens its own incident) and **host** (nothing changed; a node degrades and two services it runs, which share no other node and don't call each other, fail together). Every scenario carries distractors: a change close to the symptom (sometimes newer than the cause, sometimes older) with no alerts below it, a change off the path, and an old low-risk change on it. Top-1 accuracy, seed 7:

| | deep (30) | silent (30) | infra (30) | shared (30) | host (30) | **all (150)** | top-3 all |
|---|---|---|---|---|---|---|---|
| **Faultline (judge)** | 100.0% | 73.3% | 100.0% | 100.0% | 93.3% | **93.3%** | 98.7% |
| **Faultline (judge + skeptic)** | 100.0% | 73.3% | 100.0% | 100.0% | 93.3% | **93.3%** | 98.7% |
| no recency | 100.0% | 50.0% | 100.0% | 100.0% | 93.3% | **88.7%** | 98.7% |
| no proximity | 100.0% | 73.3% | 100.0% | 100.0% | 93.3% | **93.3%** | 98.7% |
| no alert_chain | 83.3% | 70.0% | 100.0% | 96.7% | 93.3% | **88.7%** | 98.7% |
| no risk | 100.0% | 66.7% | 100.0% | 100.0% | 93.3% | **92.0%** | 98.7% |
| no precedent | 100.0% | 73.3% | 100.0% | 100.0% | 93.3% | **93.3%** | 98.7% |
| no footprint | 100.0% | 63.3% | 100.0% | 100.0% | 93.3% | **91.3%** | 98.7% |
| nearest recent change | 20.0% | 3.3% | 0.0% | 30.0% | 0.0% | **10.7%** | 60.0% |
| most recent change | 93.3% | 80.0% | 0.0% | 76.7% | 0.0% | **50.0%** | 60.0% |

When it says… **high** confidence (42 cases): right 100.0%; **medium** confidence (87 cases): right 95.4%; **low** confidence (21 cases): right 71.4%.

Linked incidents (shared scenarios): precision 1.0, recall 1.0.

Learning: the same 150 faults planted again after the first round was resolved: top-1 93.3% → 97.3%. Without the precedent factor the second round scores 95.3%.

Held-out seeds (400 scenarios each), judge → judge + skeptic: 94.2% → 94.5% (seed 3), 96.2% → 96.8% (seed 11), 96.2% → 96.5% (seed 42); top-3 99.8–100%; host faults 98.8–100%; linked-incident precision and recall 1.0. Confidence on the held-out seeds: **high** was right 99.2–100% of the time, **medium** 98.0–99.1%, **low** 74.6–83.8%. (Before the host kind existed the same seeds scored 95.8–97.5% on four kinds; adding a fifth reshuffles which faults each seed draws, and the silent ones, the weak spot, came out harder.)

Without host suspects (`_host_suspects` returning nothing) every host scenario is wrong by construction: the judge blames one of the two services and reports the other as an independent fault, which is what Faultline did before the runtime layer existed. The host kind is planted exactly as the rule expects, so its score says the mechanism works, not how often real nodes fail this cleanly.

How to read it honestly:
- The scenario generator is mine, so this measures the judge against the failure patterns it was designed for, not against real outages. The weights were adjusted while looking at seed 7; the held-out seeds are the fairer number.
- Risk labels are drawn at random for causes and distractors alike, so in this eval risk is noise and switching it off can help some kinds. In real change logs risky changes do break things more often.
- Precedent does nothing on a first sighting (memory is empty). Its effect is the learning row: the same faults again after round one was resolved.
- Silent causes are the weak spot (72–84% with the skeptic on the held-out seeds): with no alert of their own, a recent change one level up can still look better.
- Confidence is conservative: "medium" and "low" verdicts are right more often than the labels suggest. It is still ordered correctly (high > medium > low), which is what an on-call engineer needs to know when to double-check.

## Public data: every RCAEval case, against the published baselines

The synthetic eval measures the judge against the patterns it was built for. On real telemetry Faultline runs on **all of** [RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval)'s multi-source and code-level suites (MIT, [paper](https://arxiv.org/abs/2412.17015)): **360 recorded failures** in three systems, Online Boutique (12 services), Sock Shop (15) and Train Ticket (68). RE2 (270 cases) injects CPU, memory, disk, delay, packet loss and socket faults; RE3 (90 cases) plants bugs in the services' code. Every case comes with metrics, logs, traces where the system has them, and the true root-cause service.

It is scored the way RCAEval scores its own baselines, against **RCAEval's own implementations of those baselines, run here on the same cases** (`baselines/`, their package in its own image, never in Faultline's). Every ranking, Faultline's included, goes through RCAEval's service mapping and is scored with AC@1 and Avg@5 (the mean of AC@1…AC@5).

```bash
docker compose run --rm app python -c "from faultline.rcaeval import download_suite; download_suite(['re2', 're3'])"  # ~3.3 GB
docker compose run --rm app python -m faultline.rcaeval --suites re2,re3 --extract        # signals.json per case
docker compose run --rm app python -m faultline.rcaeval --suites re2,re3 --json data/rcaeval/faultline_metrics.json
docker build -t faultline-baselines baselines && docker run --rm -e OMP_NUM_THREADS=1 -v "$PWD/data:/data" \
    -v "$PWD/baselines:/b:ro" faultline-baselines python /b/run_baselines.py --methods baro,nsigma,circa,pc_randomwalk
docker compose run --rm app python -m faultline.compare
```

**The harness reproduces the paper.** Our run of RCAEval's BARO on RE2 Train Ticket gives AC@1 0.47 / 0.93 / 1.0 / 0.60 / 0.47 / 0.53 per fault type and Avg@5 0.80, exactly the paper's Table 6. So the baselines below are run the way their authors run them.

**How the judge was developed, and what is held out.** Settings for every system were committed before any result on Sock Shop, Train Ticket or RE3 was seen (v1, commit `6d9d5a1`). v1 did badly away from Online Boutique: 155 of the 270 cases outside RE2 Train Ticket first, against 165 for "blame whatever alerted first" and 0.72 AC@1 for RCAEval's NSigma. Looking at those 270 cases, and only those, gave three changes (v2, commit `b8e907a`, frozen before the held-out run):

1. **The symptom service can be the cause.** RE3 plants code faults in Sock Shop's `front-end` itself; v1 never considered the entry point a suspect.
2. **Online Boutique's call graph = traces + its release manifests.** Spans miss calls into uninstrumented services (`adservice`, `shippingservice`, `cartservice` from frontend and checkout); the manifests' `*_ADDR` variables name them. Sock Shop's RE2/RE3 cases have no traces at all, so its graph comes from the project's code the same way (`faultline/rcaeval.py`). Train Ticket's comes from 1M+ spans per case, its untraced gateway, and each service's own database.
3. **Anomaly strength.** v1 used *when* each service alerted and nothing about *how much*. With the true graph, services that alert in every case (`redis`, `adservice`) competed with the cause. Now each silent-failure suspect's score blends in how many standard deviations its metrics moved (the n-sigma reading NSigma makes), relative to the strongest alert in the area. Weight 0.5, chosen on the 270 dev cases (0: 143/270 first, 0.5: 222, 1.0 i.e. strength alone on the graph's candidates: 220).

**RE2 Train Ticket was never looked at until v2 was frozen.** It is the held-out test, and the dataset the paper publishes numbers for:

| RE2 Train Ticket (90 held-out cases) | AC@1 | Avg@5 |
|---|---|---|
| **Faultline v2 (judge + skeptic)** | **0.70** | **0.75** |
| Faultline v1 (the pre-registered settings) | 0.29 | 0.58 |
| BARO (RCAEval's code, run here = published) | 0.67 | 0.80 |
| NSigma (RCAEval's code, run here) | 0.76 | 0.83 |
| TraceRCA (published) | 0.66 | 0.77 |
| Multi-source BARO (published) | 0.69 | 0.81 |
| PDiagnose (published) | 0.48 | 0.67 |
| CIRCA (published) | 0.32 | 0.46 |
| earliest alert | 0.16 | 0.49 |

**What that says.** On held-out data Faultline ranks first about as often as the best published methods (ahead of BARO and TraceRCA, behind NSigma), and its top five are worse than theirs (Avg@5 0.75 against 0.80–0.83). It is not the most accurate root-cause ranker on this benchmark, and this README does not claim it is. Where it differs is what comes with the answer: the path, the evidence for and against every suspect, the causal chain stored in the graph, the endpoint that slowed and the log lines that appeared, the owner, runbook and fix, and claims checked against the graph.

Every case (v2; 270 of the 360 were used to develop it, so read them as seen; CIRCA and PC-RandomWalk take 5–10 minutes per Train Ticket case here and were run on the other two systems):

| method | RE2-OB (90) | RE2-SS (90) | RE2-TT (90) | RE3-OB (30) | RE3-SS (30) | RE3-TT (30) | all |
|---|---|---|---|---|---|---|---|
| **Faultline v2, metrics** | 0.84 / 0.92 | 0.84 / 0.89 | 0.70 / 0.75 | 0.97 / 0.99 | 0.67 / 0.72 | 0.83 / 0.94 | **0.79 / 0.86** (360) |
| Faultline v2, + logs and traces as alerts | 0.77 / 0.92 | 0.83 / 0.88 | 0.72 / 0.78 | 0.97 / 0.99 | 0.33 / 0.62 | 0.83 / 0.94 | 0.76 / 0.86 (360) |
| NSigma | 0.79 / 0.91 | 0.86 / 0.94 | 0.76 / 0.83 | 0.83 / 0.89 | 0.57 / 0.83 | 0.80 / 0.96 | 0.78 / 0.90 (360) |
| CIRCA | 0.60 / 0.82 | 0.79 / 0.92 | – | 0.83 / 0.96 | 0.67 / 0.89 | – | 0.71 / 0.89 (240) |
| BARO | 0.14 / 0.74 | 0.07 / 0.76 | 0.67 / 0.80 | 0.00 / 0.77 | 0.00 / 0.74 | 0.77 / 0.95 | 0.28 / 0.78 (360) |
| PC-RandomWalk | 0.03 / 0.39 | 0.18 / 0.49 | – | 0.10 / 0.49 | 0.10 / 0.49 | – | 0.10 / 0.45 (240) |
| earliest alert | 0.80 / 0.94 | 0.59 / 0.89 | 0.16 / 0.49 | 0.77 / 0.95 | 0.43 / 0.64 | 0.23 / 0.42 | 0.51 / 0.75 (360) |

AC@1 / Avg@5. Full output with the paper's numbers alongside: [docs/rcaeval-results.md](docs/rcaeval-results.md).

**Logs and traces.** Every case's logs become templates (`faultline/telemetry.py`: ids, numbers, hex, ips and quoted strings masked, so 171k lines are ~500 patterns), and its spans an operation-level call graph with traffic and p50/p99 latency per call. Used as *alerts* (a service alerts when its error lines or span latency rise and stay up), they made the judge worse in v1 (133/270 against 155) and mixed in v2 (above), so the headline uses metric alerts, and logs and traces are used as **evidence**: each hypothesis cites the endpoint that slowed most (`(:Alert)-[:ON_OPERATION]->(:Operation)`) and the error log patterns that appeared with the incident (`(:LogPattern)-[:SEEN_IN]->(:Incident)`).

**Limits of the comparison.** Trace methods (MicroRank, TraceRCA) were not run here: RCAEval's `main.py` feeds every method metrics only, the span preparation behind their published numbers isn't in the repo, and our attempt did not reproduce them, so they are quoted as published. ε-Diagnosis needs Salesforce PyRCA and RCD Python 3.8; neither was set up. Faultline is given no injection time, but its detector, like every baseline, uses it to split normal from faulty data.

### The same failures, in the app: Online Boutique

`python -m faultline.rcaeval --fixture` boils all 54 cases down to a 130 KB fixture (`faultline/fixtures/boutique.json`): the call graph seen in the traces, and for each case the detector's alerts (time from the injection, peak z-score, and a 5-second z-score trace from 60 s before the fault to 4 minutes after) and RCAEval's answer key. No raw metrics or traces are kept. The seed turns it into a tenant:

- **36 solved incidents** (repetitions 1 and 2): `ROOT_CAUSE` on the true service, a verified fix (approver unknown: none is published), a lesson written from what the detector saw, and a shape vector each. This is the memory.
- **18 open incidents** (repetition 3), placed four hours apart so no two cases' alerts overlap. Their titles say only how many services alerted, never the metric that fired (a "socket" alarm on the frontend would give away a socket fault).
- **The answer key** goes to `world.py` only, so approving a fix is verified against what RCAEval says caused the case: paging the wrong team leaves the alerts firing.
- Owners come from the project's CODEOWNERS (one maintainers team for everything) and who knows each service from the last 200 commits to its `src/` directory, merged by name, bots kept apart (`python -m faultline.owners --fetch`, snapshot committed so the build stays offline).

With that memory and the weights learned from it, the judge + skeptic leads with the right service on **16 of the 18** open cases (16 in the top three; with the hand-set weights 15, and v1 on traced edges alone 15 with 17 in the top three). A test pins the number.

### Sock Shop and Train Ticket, in the app

The same treatment for the other two systems, every RE2 and RE3 case each (`python -m faultline.rcaeval` → `export_system`, fixtures in `faultline/fixtures/`): 120 incidents per company, repetition 3 of every fault open for the agents (27 and 26) and the rest solved history. Train Ticket's graph is **3,990 nodes and 5,265 edges**: 68 services, 160 operations with 203 operation-level calls carrying traffic and latency, 3,072 alerts, 397 hot-endpoint links and 287 error log patterns. Service cards list each service's endpoints (busiest first, with what each calls downstream) and the error logs it printed in past incidents. On the open cases, with weights learned from each company's solved incidents, the judge + skeptic leads with the right service on **21 of 27** (Sock Shop, 24 in the top three) and **19 of 26** (Train Ticket, 23 in the top three; the judge alone 21). Nobody's owners or on-call are public for these two, so each has a single owning team and no people.

## Agent, memory and actions

- **Four agents.** *Triage* reads the incident, its dependencies, recent changes and alerts. *Investigator* reads triage's handoff from the graph, runs the judge and the fault simulator, and checks precedent. *Skeptic* reads the investigator's handoff and challenges the leader (deterministic checks, up to two rounds, may overturn). *Responder* reads the skeptic's verdict, finds the owner and the runbook, and proposes the fix. Each writes an `Episode` that the next reads back (`(:Episode)-[:READ]->(:Episode)`); nothing is passed in memory.
- **The argument is written to the graph.** The Skeptic calls `record_argument` (every hypothesis, its evidence for and against, and the causal chain), and the Responder starts by following the symptom's alert down its `CAUSED_BY` links with `why`. Both are also MCP tools, and the case file shows the argument and the chain before anything is written (the preview is read-only).
- **Claude** (`claude-opus-5-5`, adaptive thinking) plays each role with only that role's tools. The **offline playbook** runs the same roles and tools in a fixed order, so the demo works without a key. Both return the same steps, labelled by agent, with the Cypher behind each.
- **Fix track record** (procedural memory): for each kind of fix on each service, how often it worked and how often it didn't, counted at read time from executed `Action`s and whether `verify_fix` saw the alerts clear, never kept as counters. The Memory tab lists it; the responder cites it ("revert_config on payments-db worked 1 of 1 time before (INC-212)") and warns when a fix has failed there before. Kestrel's history includes two first attempts that didn't work (rolling back `payments` during INC-212, `cart` during INC-197); a failed fix is never recorded as what resolved an incident.
- **Load test in the app**: Memory tab → 10 / 25 / 50 investigations, 8 at a time, on a `GRAPH.COPY` of the tenant (deleted afterwards). It shows investigations per second, p50/p95, and the two checks that matter: **isolation** (no run read another run's handoffs) and **consistency** (every run reached the same verdict as a run on its own). One at a time server-wide.
- **Memory**: every investigation is stored as an `Investigation` node. The next run on the same incident starts by reading it. Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson`, which feed the precedent factor of the next investigation. The Memory tab shows what the agent has learned.
- **Actions**: the agent can only propose (`rollback`, `revert_config`, `disable_flag`, `page_owner`). A person approves in the UI. Approval writes a remediation change to the graph and marks the incident mitigated. It is a simulation: no real system is touched. Re-running an investigation never stacks duplicate proposals.
- **Memory you can search and match**: every resolved incident gets an 18-number *signature* of its story (kind of cause, kind of thing that broke, depth below the symptom, tiers, risk) stored in a FalkorDB **vector index**, so `shape_matches` finds past incidents that looked the same on other services (INC-212's pool change matches INC-230's Kafka retention change at 0.91). Shape matches are shown as evidence but not scored: crediting them helped look-alike decoys as much as real causes in the eval. **Full-text indexes** over lessons, changes, alerts and incident titles back `search_memory` ("pgbouncer pool connections" finds INC-212's lesson, its pool change, today's C-5101 and the payments-db alert). `experts(service)` ranks people by what they did: fixes they approved for incidents caused there, changes they made, owning team.
- **Concurrency** (`python -m faultline.bench`): copies the demo graph with `GRAPH.COPY` and runs many investigations at once. 40 runs, 8 at a time: about 80 runs a second, no handoff ever read from another run, every run complete, every verdict identical to a single run. Queries retry with backoff when FalkorDB reports it is busy; errors in a query are never retried.
- **Verification**: approving a fix is not the end. The fix is applied to a simulated system (`faultline/world.py`, which stands in for production and is the only place the true cause of each alert is written down), and monitoring reports which alerts recover. Faultline then verifies from the graph alone: every alert in each covered incident's alert cone must have cleared after the fix. If any is still firing, the fix is marked as not working and the incident reopens. Approving the pool revert clears INC-301 and INC-304 and nothing else; approving a rollback of the unrelated `cart` deploy leaves checkout, payments and payments-db firing and reopens INC-301. A test checks that no agent tool or MCP tool imports the simulator's answer key.
- **Tenants**: one FalkorDB graph per company (`faultline_kestrel`, `faultline_boutique`, `faultline_harbor`, and `faultline_up_…` per upload). Isolation comes from separate graphs, not a property filter, and a test proves it.

## MCP

The same tools are available to any MCP client:

```bash
docker compose exec -T app python -m faultline.mcp_server          # stdio
```

Claude Code: `claude mcp add faultline -- docker compose -f /path/to/faultline/docker-compose.yml exec -T app python -m faultline.mcp_server`

On a public server (`docker-compose.public.yml`) the same tools are served over streamable HTTP at `https://<host>/mcp`, read-only: `propose_action` is not registered there.

There is deliberately no approve tool. An agent can propose a fix; only a person can run it.

## Layout

```
faultline/db.py          FalkorDB access, per-tenant graphs, query trace, read-only guard
faultline/seed.py        the synthetic companies (deterministic)
faultline/tools.py       graph tools, the judge, actions, memory
faultline/public_postmortems.py  imports the public post-mortem list locally (never committed); matches incidents to it
faultline/learn.py       weights fitted on a company's solved incidents and people's verdicts; cross-validated, calibrated
faultline/delivery.py    releases, commits and the deploy→commit links (fixtures/boutique_releases.json: real)
faultline/manifests.py   the three systems' real Kubernetes manifests, boiled down (fixtures/manifests.json)
faultline/reasoning.py   the argument as a subgraph: hypotheses, evidence, citations, the CAUSED_BY chain
faultline/agent.py       the four agents (Claude or offline playbook), the case preview + question router
faultline/eval.py        planted-fault accuracy eval with ablations, baselines and a learning run
faultline/rcaeval.py     RCAEval runner for all three systems: topology, alerts, strength, AC@k and Avg@5
faultline/telemetry.py   logs → templates and error-log alerts; spans → operation graph and span alerts
faultline/compare.py     Faultline against RCAEval's baselines on the same cases
baselines/               RCAEval's own baseline code in its own image, and the runner that mirrors its main.py
faultline/bench.py       concurrency benchmark: many investigations at once on a graph copy, isolation checked
faultline/world.py       the simulated system: true causes, and which alerts a fix clears (agents never read it)
faultline/mcp_server.py  MCP server over the same tools
faultline/api.py         FastAPI: case file, board, company, service cards, memory search + the static UI
faultline/fixtures/      boutique.json / sockshop.json / trainticket.json: RCAEval cases boiled down (MIT, see rcaeval.py);
                         boutique_owners.json: CODEOWNERS + per-service commit history (owners.py --fetch)
faultline/ingest.py      live input: CI change events, the Alertmanager webhook, the incident opening rule
faultline/upload.py      bring your own incident: JSON or an RCAEval-layout zip, validated, as a new tenant
faultline/postmortem.py  Markdown postmortem from the graph
faultline/owners.py      fetches Online Boutique's real owners and commit history from GitHub
deploy/                  public deployment: Caddy (HTTPS) + docker-compose.public.yml, see DEPLOY.md
demo/fire.sh             plays a live incident in through the webhooks
web/index.html           the page: header, three tabs, command bar, graph, case file
web/js/                  ES modules, no build step: util, parts (shared HTML), graph (Cytoscape: whole
                         system / this case), casefile, board, memory, company, ask (command bar), tour, main
tests/                   pytest against a real FalkorDB, on separate test graphs
```

## Limits

- Kestrel's changes and alerts are synthetic. Online Boutique's are real but recorded; its owners and contributors are real, but it has no on-call rota or approval history, so none is shown. The RCAEval runner shows the path for real data (graph from traces, alerts from metrics), but there is no live ingestion: a real deployment would also read deploys, config and flags from CI/CD and the flag service.
- The anomaly detector is deliberately simple (a sustained robust z-score). It raised an alert on the true root-cause service in all 360 RCAEval cases, but it has no change-point or slow-leak detection.
- The clock is frozen at 14:00 UTC on 17 Oct 2026, so every run tells the same story.
- Scoring weights are hand-set and explained in the UI, not learned. The eval shows what each one contributes.
- The fault simulator models the call graph only. It does not know fault types (CPU, memory, network) or traffic volume, which would sharpen the predicted footprint.
- The Claude-driven agents have not been run against the live API in this repo's tests; the offline playbook is what the tests and the eval exercise.
