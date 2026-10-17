# Faultline

[![tests](https://github.com/rago12bit/grapho1/actions/workflows/ci.yml/badge.svg)](https://github.com/rago12bit/grapho1/actions/workflows/ci.yml)

**An on-call agent that blames a change only when alerts trace an unbroken path from it up to the symptom, and says so when nothing changed at all.**

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB), entering **Tracks 01, 02 and 03**. Run all of it with one command (`docker compose up -d --build`, then http://localhost:8090), or put it on a public URL with [deploy/DEPLOY.md](deploy/DEPLOY.md).

![Faultline's case file for INC-301: the failure path drawn from checkout down to payments-db in red, the blast radius in dashed amber, the verdict and its evidence on the right](docs/img/case-file.png)

<sub>INC-301 opened: the answer is on screen at once. Red is the failure path the alerts climbed (payments-db → payments → checkout), dashed amber the callers it reaches. The `cart` deploy one hop away is newer and closer, but nothing under it alerted, so it scores 0.27 against 0.94.</sub>

| Track | What Faultline does |
|---|---|
| **01 · Agents that act on connected data** | An alert-chain judge over `CALLS*1..5`, a fault simulator per suspect, and **WCC, SPpaths, betweenness, label propagation and PageRank used during the investigation**. Every claim is checked against the graph. Fixes wait for a human, then are **verified from the alerts clearing**. **Monitoring and CI push in live** (Alertmanager webhook, change events): Faultline opens the incident and investigates it by itself. An MCP server exposes the same tools. |
| **02 · Agent memory and coordination** | Four agents hand off **only through `Episode` nodes** (resumable by another process). Lessons and precedent change the score; past incidents are matched by shape on a vector index and by full text. A **fix track record** (what worked, what didn't, counted from verified outcomes) shapes what the responder says. A load test **in the app** shows isolation and consistency under 50 parallel investigations. |
| **03 · Company brain** | Ownership and on-call, **experts ranked by the fixes they approved, the changes they made and their commits** (real CODEOWNERS and git history for Online Boutique), runbooks per service, lessons searchable by full text, a postmortem export for any incident, and a card per service with the queries behind it. |

**What's real.** Online Boutique's call graph, telemetry, root causes, owners and contributors are real (RCAEval RE2-OB; the project's CODEOWNERS and git history). Kestrel Pay is a synthetic company built to show every kind of answer. Nobody's on-call rota or fix approvals are public, so the Boutique tenant has none rather than invented ones.

Most "what broke?" tools blame the most recent deploy near the failing service. Faultline walks a FalkorDB graph of services, owners, deploys, config changes, feature flags, alerts and past incidents, and asks a stricter question: *is there a path in the call graph from this change to the symptom where every service along the way started alerting after the change landed?* Only those changes are real suspects. It shows the path hop by hop with every Cypher query it ran, proposes a fix that waits for a human, and writes what it learned back into the graph for the next incident.

## What's different

1. **The alert chain decides, not proximity.** In INC-301 a `cart` deploy is one hop from the failing `checkout` and more recent than the real cause. Nothing on its path alerted, so it scores 0.27. A connection-pool change two hops down, on `payments-db`, has `payments-db`, `payments` and `checkout` alerting in that order above it, so it scores 0.94.
2. **"Nothing changed" is a first-class answer.** When no change on the path explains an incident, alerting dependencies with no change compete as suspects. In INC-303 the answer is "the search cluster failed first; this is infrastructure, page its owner", instead of blaming whatever deployed last.
3. **One change, many incidents.** Two teams often open separate incidents for one cause. Faultline checks whether the leading change also fully explains other open incidents (same alert-chain test), says so, and proposes one fix that covers all of them. One approval mitigates every linked incident. INC-301 (checkout) and INC-304 (refunds) are both the same pool change.
4. **Memory changes the score, not just the display.** Resolving an incident writes `ROOT_CAUSE`, `RESOLVED_BY` and a `Lesson` into the graph, and the next investigation scores precedent from them. A test deletes the memory and checks that the same change then scores lower.
5. **A fault simulator checks every suspect.** For each one it asks "if this broke, which services would alert, and in what order?" and compares that with what actually alerted: "explains 3 of 3 alerting services, in the predicted order". Alerts the leader can't explain point to **independent faults**: in INC-302 the auth deploy explains 2 of the 7 alerting services under `mobile-bff`, and Faultline names the other two faults behind the rest (the pool change of INC-301/304, the search cluster of INC-303). A cause that never alerts itself (a queue, a database) still counts when everything above it did, and a change the failure climbed *past* is marked down.
6. **Four agents, one graph, and a skeptic among them.** Triage, Investigator, Skeptic and Responder never pass anything to each other in code: each writes an `Episode` to the graph and the next one starts by reading it back, so an investigation can be resumed by another process or session. The **Skeptic** tries to knock the leader over before anyone acts: what doesn't it explain, did its alerts come in the right order, did the change land before the incident, is it clearly ahead? If a rival explains more and scores within reach, the verdict is overturned. Confidence comes from the **gap** to the runner-up, not the raw score. Verdicts stay in the graph as scored `SUSPECTED_CAUSE` and `SIMILAR_TO` edges, and the Responder attaches the team's `Runbook`.
7. **Measured, not claimed.** An eval plants hundreds of random faults on the same topology and reports accuracy, the drop when each factor is switched off, and two naive baselines (see [Evaluation](#evaluation)): 95.8–97.5% top-1 and 100% top-3 on held-out seeds, against 64% for "blame the most recent change". When Faultline says **high** confidence it has been right every time. On **real telemetry** from a public benchmark (RCAEval, 36 unseen cases) it names the right service first 92% of the time (33/36) and in its top three 94%, against 86% first for the best simple baseline (see [Public data](#public-data-rcaeval)). Those 54 real failures are also loaded into the app as a third company, so you can open them.
8. **Every claim is checked, every number is explained, and the agent can't act alone.** Every change id, incident id, service and clock time in a verdict is looked up in the graph, and what the claim *says* must match it ("C-5101 changed auth" fails: it changed payments-db; "D-5102 caused it" fails: it was ruled out; "payments alerted before payments-db" fails: the times say otherwise). The verdict must also hang together (the blamed change exists, changed the named service, and is among the judge's top suspects); claims Claude makes that the graph can't back are dropped from the evidence and shown as such. Each suspect shows its factor scores, its footprint and its path, and every step shows its Cypher. Free-form queries run through `GRAPH.RO_QUERY`, so the database itself refuses writes. The agent and the MCP server can only propose fixes; there is no approve tool.
9. **Graph algorithms do real work in the investigation:** WCC splits the alerts into separate problems, SPpaths traces the failure path, betweenness flags chokepoints on it, label propagation names the failure domains and teams, PageRank ranks criticality. See [the table](#falkordb-graph-algorithms-used-during-the-investigation).

Built for **Graph Hacks: Context for AI Agents** (WeMakeDevs × FalkorDB, Oct 15–18 2026).
Tracks: **01 Agents That Act on Connected Data** (main), **02 Agent Memory and Coordination**, **03 Company Brain**.

Kestrel Pay and Harbor Freight are synthetic. **Online Boutique** is real: 54 failures from the public RCAEval benchmark (call graph, metrics, root causes), with owners and contributors from the project's own CODEOWNERS and git history. Nothing outside the graph is ever changed.

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

Held-out seeds (400 scenarios each), judge → judge + skeptic: 95.2% → 95.8% (seed 3), 95.8% → 96.8% (seed 11), 96.8% → 97.5% (seed 42); top-3 100% on all three; linked-incident precision and recall 1.0. Confidence on the held-out seeds: **high** was right 100% of the time, **medium** 97.6–99.4%, **low** 84.1–89.2%.

How to read it honestly:
- The scenario generator is mine, so this measures the judge against the failure patterns it was designed for, not against real outages. The weights were adjusted while looking at seed 7; the held-out seeds are the fairer number.
- Risk labels are drawn at random for causes and distractors alike, so in this eval risk is noise and switching it off can help some kinds. In real change logs risky changes do break things more often.
- Precedent does nothing on a first sighting (memory is empty). Its effect is the learning row: the same faults again after round one was resolved.
- Silent causes are the weak spot (84–90% with the skeptic): with no alert of their own, a recent change one level up can still look better.
- Confidence is conservative: "medium" and "low" verdicts are right more often than the labels suggest. It is still ordered correctly (high > medium > low), which is what an on-call engineer needs to know when to double-check.

## Public data: RCAEval

The synthetic eval measures the judge against the patterns it was built for. To check it on real telemetry, Faultline also runs on [RCAEval](https://huggingface.co/datasets/phamquiluan/RCAEval) (MIT, [paper](https://huggingface.co/papers/2412.17015)): recorded faults in Google's Online Boutique (RE2-OB) with real metrics, traces and the true root-cause service.

```bash
docker compose run --rm app python -m faultline.rcaeval --download            # 18 cases, ~180 MB, into data/rcaeval
docker compose run --rm app python -m faultline.rcaeval                       # repetition 1
docker compose run --rm app python -m faultline.rcaeval --download --rep 2    # and --rep 3 for the held-out cases
```

For each case (`faultline/rcaeval.py`):
- **Graph from traces:** a `Service` per service, `CALLS` from each parent span's service to its child span's service (at least 5 calls).
- **Alerts from metrics:** simple spike detection on CPU, memory, disk I/O, sockets, p50/p90 latency and errors. A robust z-score against the normal period before the fault was injected must stay above 5 for 30 seconds; one `Alert` per service at its earliest onset. These settings were fixed before looking at any result.
- **Incident** on `frontend`, the user-facing entry point. Then the judge ranks suspects and its first answer is compared with the case's root cause.

The 18 cases per repetition are every fault type (CPU, memory, disk, delay, packet loss, socket exhaustion) on three different services, chosen by a fixed rotation, not by hand.

| | top-1 | top-3 |
|---|---|---|
| Judge as it was, repetition 1 | 11/18 | 13/18 |
| Judge after the knock-on fix, repetition 1 (seen while fixing) | 13/18 | 15/18 |
| Judge after the knock-on fix, repetitions 2 + 3 (36 unseen cases) | 31/36 (86%) | 34/36 (94%) |
| **Judge with the tie rule, repetitions 2 + 3** | **33/36 (92%)** | **34/36 (94%)** |
| Judge + skeptic with the tie rule, repetitions 2 + 3 | 32/36 (89%) | 34/36 (94%) |
| Judge with the tie rule, repetition 1 | 13/18 | 16/18 |
| Baseline: earliest alert, repetitions 2 + 3 | 31/36 (86%) | 35/36 (97%) |
| Baseline: biggest anomaly, repetitions 2 + 3 | 1/36 | 28/36 |

**The fix.** The first run showed a real bug: the judge only considered the *deepest* alerting services, so when CPU stress hit `checkoutservice` and `currencyservice` below it slowed down 90 seconds later, checkout was dropped and currency blamed. Now a service is set aside only if something below it alerted *before* it did. Later alerts below are knock-on effects. That was the only change made after seeing RCAEval results, and it did not change the synthetic eval. Repetitions 2 and 3 were downloaded and scored only after it.

**The tie rule, and how it was found.** Loading the cases into the app showed one (repetition 3, `recommendationservice` packet loss) where two suspects scored exactly the same and the winner was whichever row FalkorDB returned first, so the answer could change between builds. Ties now go to whatever started first (a change's time, a silent failure's first alert), then by name. It was added to make the judge deterministic, but it was found on a held-out case, so treat the 33/36 as partly seen; 31/36 is the number from before it. It changed no synthetic seed except one case in 400 on seed 3 (96.0% → 95.8%).

**What this says.** On real faults Faultline is about as accurate as the strongest simple baseline (blame whatever alerted first): a little better first (33 vs 31 of 36), a little worse in the top three (34 vs 35). That fits the data: these faults are resource stress with no deploy or config change and one fault per case, so alert timing carries almost all of the signal, and Faultline's change-based factors and linked-incident logic have nothing to work with. What Faultline adds on top is the explanation (path, footprint, order, skeptic checks), the owner, runbook and fix, and claims checked against the graph. The skeptic cost one case here, and on Online Boutique (below) it overturns one correct answer and lowers its confidence on six. The detector found an alert on the true root-cause service in all 54 cases, so the misses are the judge's, not the detector's.

### The same failures, in the app: Online Boutique

`python -m faultline.rcaeval --fixture` boils all 54 cases down to a 130 KB fixture (`faultline/fixtures/boutique.json`): the call graph seen in the traces, and for each case the detector's alerts (time from the injection, peak z-score, and a 5-second z-score trace from 60 s before the fault to 4 minutes after) and RCAEval's answer key. No raw metrics or traces are kept. The seed turns it into a tenant:

- **36 solved incidents** (repetitions 1 and 2): `ROOT_CAUSE` on the true service, a verified fix (approver unknown: none is published), a lesson written from what the detector saw, and a shape vector each. This is the memory.
- **18 open incidents** (repetition 3), placed four hours apart so no two cases' alerts overlap. Their titles say only how many services alerted, never the metric that fired (a "socket" alarm on the frontend would give away a socket fault).
- **The answer key** goes to `world.py` only, so approving a fix is verified against what RCAEval says caused the case: paging the wrong team leaves the alerts firing.
- Owners come from the project's CODEOWNERS (one maintainers team for everything) and who knows each service from the last 200 commits to its `src/` directory, merged by name, bots kept apart (`python -m faultline.owners --fetch`, snapshot committed so the build stays offline).

With that memory, the judge + skeptic leads with the right service on **15 of the 18** open cases (17 in the top three). A test pins the number.

## Agent, memory and actions

- **Four agents.** *Triage* reads the incident, its dependencies, recent changes and alerts. *Investigator* reads triage's handoff from the graph, runs the judge and the fault simulator, and checks precedent. *Skeptic* reads the investigator's handoff and challenges the leader (deterministic checks, up to two rounds, may overturn). *Responder* reads the skeptic's verdict, finds the owner and the runbook, and proposes the fix. Each writes an `Episode` that the next reads back (`(:Episode)-[:READ]->(:Episode)`); nothing is passed in memory.
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
faultline/agent.py       the four agents (Claude or offline playbook), the case preview + question router
faultline/eval.py        planted-fault accuracy eval with ablations, baselines and a learning run
faultline/rcaeval.py     RCAEval public-benchmark runner: graph from traces, alerts from metrics, score
faultline/bench.py       concurrency benchmark: many investigations at once on a graph copy, isolation checked
faultline/world.py       the simulated system: true causes, and which alerts a fix clears (agents never read it)
.github/workflows/ci.yml tests + a 60-scenario eval against a real FalkorDB on every push
faultline/mcp_server.py  MCP server over the same tools
faultline/api.py         FastAPI: case file, board, company, service cards, memory search + the static UI
faultline/fixtures/      boutique.json: the 54 RCAEval cases, boiled down (MIT, see rcaeval.py);
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
- The anomaly detector is deliberately simple (a sustained robust z-score). It found the true root cause's anomaly in all 54 RCAEval cases, but it has no change-point or slow-leak detection.
- The clock is frozen at 14:00 UTC on 17 Oct 2026, so every run tells the same story.
- Scoring weights are hand-set and explained in the UI, not learned. The eval shows what each one contributes.
- The fault simulator models the call graph only. It does not know fault types (CPU, memory, network) or traffic volume, which would sharpen the predicted footprint.
- The Claude-driven agents have not been run against the live API in this repo's tests; the offline playbook is what the tests and the eval exercise.
