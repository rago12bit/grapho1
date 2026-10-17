"""Synthetic companies. Everything here is invented; the same seed always builds the same graph.

Kestrel Pay is the main demo: 24 services, 6 teams, two weeks of deploys/config/flag changes,
alerts, eight resolved incidents (the agent's memory) and three open ones to investigate.
Harbor Freight Co is a small second tenant that exists to show isolation between graphs.
Online Boutique is real: 54 failures from the public RCAEval benchmark (see BOUTIQUE below).
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

from .db import Graph

# Fixed "now" so demos and tests are reproducible.
NOW = int(datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc).timestamp())
MIN = 60
HOUR = 3600
DAY = 86400

KESTREL = {
    "title": "Kestrel Pay",
    "teams": {
        "checkout": "Checkout",
        "payments": "Payments",
        "identity": "Identity",
        "discovery": "Discovery",
        "platform": "Platform",
        "data": "Data",
    },
    # handle, name, team, on_call
    "people": [
        ("priya", "Priya Raman", "checkout", True),
        ("tomas", "Tomás Ferreira", "checkout", False),
        ("ade", "Adebayo Okafor", "payments", True),
        ("lin", "Lin Zhao", "payments", False),
        ("marta", "Marta Kowalska", "payments", False),
        ("sam", "Sam Whitfield", "identity", True),
        ("noor", "Noor Haddad", "identity", False),
        ("kenji", "Kenji Sato", "discovery", True),
        ("elena", "Elena Rossi", "discovery", False),
        ("ravi", "Ravi Menon", "platform", True),
        ("jo", "Jo Ellison", "platform", False),
        ("fatima", "Fatima Zahra", "data", True),
        ("oscar", "Oscar Lund", "data", False),
    ],
    # name, team, kind, tier, internet_exposed
    "services": [
        ("web-frontend", "checkout", "service", 1, True),
        ("mobile-bff", "identity", "service", 1, True),
        ("api-gateway", "platform", "service", 1, True),
        ("checkout", "checkout", "service", 1, False),
        ("cart", "checkout", "service", 2, False),
        ("pricing", "checkout", "service", 2, False),
        ("payments", "payments", "service", 1, False),
        ("ledger", "payments", "service", 1, False),
        ("fraud-scoring", "payments", "service", 2, False),
        ("auth", "identity", "service", 1, False),
        ("users", "identity", "service", 1, False),
        ("notifications", "platform", "service", 3, False),
        ("search", "discovery", "service", 2, False),
        ("catalog", "discovery", "service", 2, False),
        ("inventory", "discovery", "service", 2, False),
        ("payments-db", "payments", "datastore", 1, False),
        ("ledger-db", "payments", "datastore", 1, False),
        ("users-db", "identity", "datastore", 1, False),
        ("session-redis", "identity", "datastore", 1, False),
        ("feature-store", "data", "datastore", 2, False),
        ("catalog-es", "discovery", "datastore", 2, False),
        ("events-kafka", "platform", "datastore", 2, False),
        ("stripe-api", "payments", "external", 1, False),
        ("sendgrid", "platform", "external", 3, False),
    ],
    "calls": [
        ("web-frontend", "api-gateway"), ("mobile-bff", "api-gateway"), ("mobile-bff", "auth"),
        ("api-gateway", "auth"), ("api-gateway", "checkout"), ("api-gateway", "cart"),
        ("api-gateway", "search"), ("api-gateway", "catalog"), ("api-gateway", "users"),
        ("checkout", "cart"), ("checkout", "payments"), ("checkout", "pricing"),
        ("checkout", "inventory"), ("checkout", "notifications"),
        ("cart", "pricing"), ("cart", "session-redis"),
        ("payments", "fraud-scoring"), ("payments", "ledger"), ("payments", "payments-db"),
        ("payments", "stripe-api"),
        ("fraud-scoring", "feature-store"),
        ("ledger", "ledger-db"), ("ledger", "events-kafka"),
        ("auth", "users"), ("auth", "session-redis"),
        ("users", "users-db"),
        ("search", "catalog-es"), ("catalog", "catalog-es"), ("catalog", "pricing"),
        ("inventory", "events-kafka"),
        ("notifications", "sendgrid"), ("notifications", "events-kafka"),
    ],
}

HARBOR = {
    "title": "Harbor Freight Co",
    "teams": {"ops": "Ops"},
    "people": [("dana", "Dana Brooks", "ops", True), ("li", "Li Wei", "ops", False)],
    "services": [
        ("tracking-web", "ops", "service", 1, True),
        ("shipments", "ops", "service", 1, False),
        ("shipments-db", "ops", "datastore", 1, False),
    ],
    "calls": [("tracking-web", "shipments"), ("shipments", "shipments-db")],
}


def _changes_kestrel(rng: random.Random) -> list[dict]:
    """Background noise: routine deploys on every service over two weeks, plus the planted causes."""
    people_by_team: dict[str, list[str]] = {}
    for handle, _, team, _ in KESTREL["people"]:
        people_by_team.setdefault(team, []).append(handle)
    team_of = {s[0]: s[1] for s in KESTREL["services"]}
    deployable = [s[0] for s in KESTREL["services"] if s[2] == "service"]
    out: list[dict] = []
    versions: dict[str, list[int]] = {s: [1, rng.randint(0, 9), 0] for s in deployable}
    # Keep the background release line below the planted releases so rollbacks name a real prior version.
    versions.update({"auth": [4, 1, 0], "cart": [3, 7, 0], "catalog": [5, 1, 0], "notifications": [2, 3, 0]})
    for i in range(70):
        svc = rng.choice(deployable)
        at = NOW - rng.randint(3 * HOUR, 14 * DAY)
        v = versions[svc]
        v[2] += 1
        out.append({
            "id": f"D-{1000 + i}", "kind": "deploy", "service": svc, "at": at,
            "author": rng.choice(people_by_team[team_of[svc]]),
            "summary": rng.choice([
                "dependency bumps", "logging tweaks", "refactor handlers", "add metrics",
                "copy changes", "retry tuning", "small bug fixes", "upgrade base image",
            ]),
            "version": f"{v[0]}.{v[1]}.{v[2]}", "risk": "low",
        })
    return out


def _planted_kestrel() -> tuple[list[dict], list[dict], list[dict]]:
    """Changes, alerts and incidents with known answers. Times are relative to NOW."""
    changes = [
        # INC-301 truth: a pool size change two hops below checkout.
        {"id": "C-5101", "kind": "config", "service": "payments-db", "at": NOW - 47 * MIN, "author": "lin",
         "summary": "payments-db: pgbouncer max_client_conn 400 -> 60 (cost cleanup)",
         "key": "pgbouncer.max_client_conn", "old": "400", "new": "60", "risk": "high"},
        # INC-301 red herrings: a deploy one hop away with no alerts, and one off the dependency path.
        {"id": "D-5102", "kind": "deploy", "service": "cart", "at": NOW - 52 * MIN, "author": "tomas",
         "summary": "cart: move promo banner copy to CMS", "version": "3.8.1", "risk": "low"},
        {"id": "D-5103", "kind": "deploy", "service": "catalog", "at": NOW - 41 * MIN, "author": "elena",
         "summary": "catalog: ranking feature export v7", "version": "5.2.0", "risk": "medium"},
        # INC-302 truth: auth deploy that changed token validation.
        {"id": "D-5201", "kind": "deploy", "service": "auth", "at": NOW - 26 * MIN, "author": "noor",
         "summary": "auth: switch JWT validation to rotating JWKS cache", "version": "4.2.0", "risk": "high"},
        # INC-302 weaker suspect on the path: a flag flip in users 50 minutes before.
        {"id": "F-5202", "kind": "flag", "service": "users", "at": NOW - 63 * MIN, "author": "sam",
         "summary": "users: enable profile-v2 read path for 10%", "key": "profile_v2_reads", "old": "off",
         "new": "10%", "risk": "medium"},
        # INC-303: nothing changed on search's dependency path; a deploy elsewhere is the only change.
        {"id": "D-5301", "kind": "deploy", "service": "notifications", "at": NOW - 18 * MIN, "author": "jo",
         "summary": "notifications: template cleanup", "version": "2.4.3", "risk": "low"},
    ]
    alerts = [
        {"id": "A-9001", "service": "checkout", "at": NOW - 38 * MIN, "severity": "page", "signal": "p99 latency 8.2s (SLO 1.5s)"},
        {"id": "A-9002", "service": "payments", "at": NOW - 41 * MIN, "severity": "page", "signal": "p99 latency 7.9s, 9% 5xx"},
        {"id": "A-9003", "service": "payments-db", "at": NOW - 44 * MIN, "severity": "warn", "signal": "client connections at limit (60/60), wait queue 340"},
        {"id": "A-9004", "service": "web-frontend", "at": NOW - 36 * MIN, "severity": "warn", "signal": "checkout conversion -31%"},
        {"id": "A-9101", "service": "auth", "at": NOW - 22 * MIN, "severity": "page", "signal": "401 rate 31% (baseline 2%)"},
        {"id": "A-9102", "service": "mobile-bff", "at": NOW - 20 * MIN, "severity": "page", "signal": "login failures 30%"},
        {"id": "A-9201", "service": "catalog-es", "at": NOW - 15 * MIN, "severity": "page", "signal": "cluster status RED, 2 of 6 shards unassigned"},
        {"id": "A-9202", "service": "search", "at": NOW - 13 * MIN, "severity": "page", "signal": "zero-result rate 64%"},
    ]
    incidents = [
        {"id": "INC-301", "title": "Checkout p99 latency 8s and 12% errors", "symptom": "checkout",
         "started": NOW - 38 * MIN, "status": "open", "severity": "SEV1"},
        {"id": "INC-302", "title": "30% of mobile logins failing", "symptom": "mobile-bff",
         "started": NOW - 20 * MIN, "status": "open", "severity": "SEV1"},
        {"id": "INC-303", "title": "Search returns empty results", "symptom": "search",
         "started": NOW - 13 * MIN, "status": "open", "severity": "SEV2"},
        # Opened separately by the Payments team: the same pool change is behind it and INC-301.
        {"id": "INC-304", "title": "Refund API timing out", "symptom": "payments",
         "started": NOW - 40 * MIN, "status": "open", "severity": "SEV2"},
    ]
    return changes, alerts, incidents


def _history_kestrel() -> list[dict]:
    """Resolved incidents: the agent's long-term memory. Each names its root-cause change and the fix."""
    return [
        {"id": "INC-212", "title": "Payments timeouts during evening peak", "symptom": "payments",
         "days_ago": 61, "severity": "SEV1", "cause_service": "payments-db", "cause_kind": "config",
         "cause_summary": "payments-db: pgbouncer pool_size 120 -> 40", "cause_author": "marta",
         "fix": "revert_config", "fix_summary": "Reverted pool_size to 120; latency recovered in 4 min",
         "tried": [{"kind": "rollback", "target": "payments", "by": "lin", "firing": "payments, payments-db",
                    "summary": "Rolled back payments 3.4.1 to 3.4.0; latency did not move"}],
         "lesson": "Pool/connection limits on payments-db must be load-tested against peak; checkout feels it first."},
        {"id": "INC-188", "title": "Login 401 spike after auth release", "symptom": "auth",
         "days_ago": 94, "severity": "SEV1", "cause_service": "auth", "cause_kind": "deploy",
         "cause_summary": "auth 3.9.0: new token audience check", "cause_author": "noor",
         "fix": "rollback", "fix_summary": "Rolled back auth to 3.8.4",
         "lesson": "Token validation changes need a canary on mobile-bff traffic; mobile clients cache old tokens."},
        {"id": "INC-240", "title": "Search degraded, catalog-es yellow", "symptom": "search",
         "days_ago": 33, "severity": "SEV2", "cause_service": "catalog-es", "cause_kind": "infra",
         "cause_summary": "catalog-es: node lost to host failure, shards unassigned", "cause_author": None,
         "fix": "page_owner", "fix_summary": "Platform replaced the node; shards rebalanced",
         "lesson": "catalog-es has no automatic shard reallocation; page Discovery on-call and Platform."},
        {"id": "INC-197", "title": "Cart empty for some users", "symptom": "cart",
         "days_ago": 80, "severity": "SEV2", "cause_service": "session-redis", "cause_kind": "config",
         "cause_summary": "session-redis: maxmemory-policy changed to allkeys-lru", "cause_author": "ravi",
         "fix": "revert_config", "fix_summary": "Restored volatile-lru",
         "tried": [{"kind": "rollback", "target": "cart", "by": "priya", "firing": "cart, session-redis",
                    "summary": "Rolled back cart 2.8.0 to 2.7.6; carts still emptied"}],
         "lesson": "session-redis eviction policy is load-bearing for cart and auth."},
        {"id": "INC-221", "title": "Fraud checks timing out", "symptom": "payments",
         "days_ago": 47, "severity": "SEV2", "cause_service": "fraud-scoring", "cause_kind": "deploy",
         "cause_summary": "fraud-scoring 2.7.0: new model loaded synchronously", "cause_author": "ade",
         "fix": "rollback", "fix_summary": "Rolled back to 2.6.3",
         "lesson": "Model loads must be async; payments has a 2s budget for fraud-scoring."},
        {"id": "INC-230", "title": "Ledger lag, payouts delayed", "symptom": "ledger",
         "days_ago": 40, "severity": "SEV2", "cause_service": "events-kafka", "cause_kind": "config",
         "cause_summary": "events-kafka: retention cut to 2h", "cause_author": "jo",
         "fix": "revert_config", "fix_summary": "Retention restored to 72h",
         "lesson": "Kafka retention changes need a consumer-lag review from Payments."},
        {"id": "INC-251", "title": "Order emails delayed", "symptom": "notifications",
         "days_ago": 22, "severity": "SEV3", "cause_service": "sendgrid", "cause_kind": "external",
         "cause_summary": "SendGrid regional outage", "cause_author": None,
         "fix": "page_owner", "fix_summary": "Waited out the provider outage; queued emails flushed",
         "lesson": "Provider outages show up as no internal change on the path."},
        {"id": "INC-260", "title": "Checkout errors after pricing release", "symptom": "checkout",
         "days_ago": 15, "severity": "SEV2", "cause_service": "pricing", "cause_kind": "deploy",
         "cause_summary": "pricing 2.3.0: currency rounding change", "cause_author": "tomas",
         "fix": "rollback", "fix_summary": "Rolled back pricing to 2.2.9",
         "lesson": "Pricing changes reach checkout through two paths (direct and via cart)."},
    ]


RUNBOOKS_KESTREL = [
    {"id": "RB-1", "service": "payments-db", "title": "Connection pool exhausted", "when": "config",
     "steps": ["Check pgbouncer SHOW POOLS: cl_waiting > 0 means the pool is the bottleneck",
               "Revert the last pgbouncer change (max_client_conn / pool_size)",
               "Watch payments p99 recover within 5 minutes",
               "Open a ticket to load-test the new limit before re-applying"]},
    {"id": "RB-2", "service": "auth", "title": "Bad auth release", "when": "deploy",
     "steps": ["Compare 401 rate by client: mobile clients cache tokens longest",
               "Roll back auth to the previous release",
               "Confirm mobile-bff login success is back above 98%",
               "Re-release behind a canary on mobile-bff traffic"]},
    {"id": "RB-3", "service": "catalog-es", "title": "Cluster RED, shards unassigned", "when": "infra",
     "steps": ["GET _cluster/health and _cat/shards?h=index,shard,state,unassigned.reason",
               "If a node is gone, ask Platform to replace it",
               "Reroute unassigned shards once the node is back",
               "Search recovers when the cluster is green"]},
    {"id": "RB-4", "service": "session-redis", "title": "Sessions evicted", "when": "config",
     "steps": ["Check maxmemory-policy: it must be volatile-lru",
               "Restore the policy and watch evicted_keys",
               "Tell Checkout and Identity: carts and logins depend on it"]},
    {"id": "RB-5", "service": "events-kafka", "title": "Consumer lag after retention change", "when": "config",
     "steps": ["Check consumer lag for ledger and inventory groups",
               "Restore retention to 72h", "Ask Payments to replay from the last committed offset"]},
    {"id": "RB-6", "service": "pricing", "title": "Bad pricing release", "when": "deploy",
     "steps": ["Roll back pricing", "Check checkout and cart: pricing reaches checkout by two paths"]},
]


def _write_runbooks(g: Graph, runbooks: list[dict]) -> None:
    g.write("""
        UNWIND $rbs AS r
        MATCH (s:Service {name: r.service})
        CREATE (:Runbook {id: r.id, title: r.title, when: r.when, steps: r.steps})-[:FOR_SERVICE]->(s)""",
            {"rbs": runbooks})


def _write_org(g: Graph, spec: dict) -> None:
    g.write("CREATE (:Org {title: $title})", {"title": spec["title"]})
    g.write("UNWIND $teams AS t CREATE (:Team {id: t.id, name: t.name})",
            {"teams": [{"id": k, "name": v} for k, v in spec["teams"].items()]})
    g.write("""
        UNWIND $people AS p
        MATCH (t:Team {id: p.team})
        CREATE (:Person {handle: p.handle, name: p.name, on_call: p.on_call})-[:MEMBER_OF]->(t)""",
            {"people": [{"handle": h, "name": n, "team": t, "on_call": oc} for h, n, t, oc in spec["people"]]})
    g.write("""
        UNWIND $svcs AS s
        MATCH (t:Team {id: s.team})
        CREATE (:Service {name: s.name, kind: s.kind, tier: s.tier, internet_exposed: s.exposed})-[:OWNED_BY]->(t)""",
            {"svcs": [{"name": n, "team": t, "kind": k, "tier": tier, "exposed": e}
                      for n, t, k, tier, e in spec["services"]]})
    g.write("""
        UNWIND $calls AS c
        MATCH (a:Service {name: c[0]}), (b:Service {name: c[1]})
        CREATE (a)-[:CALLS]->(b)""", {"calls": [list(c) for c in spec["calls"]]})


def _write_changes(g: Graph, changes: list[dict]) -> None:
    g.write("""
        UNWIND $changes AS c
        MATCH (s:Service {name: c.service})
        OPTIONAL MATCH (p:Person {handle: c.author})
        CREATE (ch:Change {id: c.id, kind: c.kind, at: c.at, summary: c.summary, risk: c.risk,
                           version: c.version, key: c.key, old: c.old, new: c.new})-[:CHANGED]->(s)
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:MADE]->(ch))""",
            {"changes": [{**{"version": None, "key": None, "old": None, "new": None}, **c} for c in changes]})


def _write_alerts(g: Graph, alerts: list[dict]) -> None:
    g.write("""
        UNWIND $alerts AS a
        MATCH (s:Service {name: a.service})
        CREATE (:Alert {id: a.id, at: a.at, severity: a.severity, signal: a.signal})-[:FIRED_ON]->(s)""",
            {"alerts": alerts})


def _write_incidents(g: Graph, incidents: list[dict]) -> None:
    g.write("""
        UNWIND $incs AS i
        MATCH (s:Service {name: i.symptom})
        CREATE (:Incident {id: i.id, title: i.title, started: i.started, status: i.status,
                           severity: i.severity})-[:SYMPTOM_ON]->(s)""", {"incs": incidents})


# Who approved each past fix: the on-call or senior engineer of the team that owned the broken service.
APPROVERS = {"INC-212": "marta", "INC-188": "sam", "INC-240": "kenji", "INC-197": "ravi", "INC-221": "ade",
             "INC-230": "lin", "INC-251": "jo", "INC-260": "priya"}


def _write_history(g: Graph, history: list[dict]) -> None:
    rows = []
    for h in history:
        started = h.get("started") or NOW - h["days_ago"] * DAY
        rows.append({**h, "started": started, "cause_id": f"H-{h['id']}",
                     "cause_at": h.get("cause_at") or started - 20 * MIN, "resolved": started + 50 * MIN,
                     "approver": h.get("approver") or APPROVERS.get(h["id"])})
    g.write("""
        UNWIND $rows AS h
        MATCH (sym:Service {name: h.symptom}), (cs:Service {name: h.cause_service})
        CREATE (i:Incident {id: h.id, title: h.title, started: h.started, resolved: h.resolved,
                            status: 'resolved', severity: h.severity})-[:SYMPTOM_ON]->(sym)
        CREATE (c:Change {id: h.cause_id, kind: h.cause_kind, at: h.cause_at, summary: h.cause_summary,
                          risk: 'high'})-[:CHANGED]->(cs)
        CREATE (i)-[:ROOT_CAUSE]->(c)
        CREATE (i)-[:RESOLVED_BY]->(a:Action {id: 'ACT-' + h.id, kind: h.fix, summary: h.fix_summary,
                                              status: 'executed', at: h.resolved, decided_at: h.resolved,
                                              decided_by: h.approver, verified: true,
                                              verification: h.id + ': all alerts cleared after the fix'})
        CREATE (a)-[:FOR]->(i), (a)-[:TARGETS]->(cs)
        CREATE (i)-[:TAUGHT]->(:Lesson {text: h.lesson, at: h.resolved})
        WITH h, c, a
        OPTIONAL MATCH (p:Person {handle: h.cause_author})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:MADE]->(c))
        WITH h, a
        OPTIONAL MATCH (ap:Person {handle: h.approver})
        FOREACH (_ IN CASE WHEN ap IS NULL THEN [] ELSE [1] END | CREATE (ap)-[:APPROVED]->(a))""", {"rows": rows})
    # Fixes that were tried first and did not clear the alerts: the other half of the fix track record.
    tried = [{**t, "incident": h["id"], "at": h["started"] + 25 * MIN if h.get("started") else NOW - h["days_ago"] * DAY + 25 * MIN}
             for h in history for t in h.get("tried", [])]
    if tried:
        g.write("""
            UNWIND $tried AS t
            MATCH (i:Incident {id: t.incident}), (s:Service {name: t.target})
            CREATE (a:Action {id: 'ACT-' + t.incident + '-TRY', kind: t.kind, summary: t.summary, status: 'executed',
                              at: t.at, decided_at: t.at, decided_by: t.by, verified: false,
                              verification: t.incident + ': still firing: ' + t.firing})
            CREATE (a)-[:FOR]->(i), (a)-[:TARGETS]->(s)
            WITH a, t
            OPTIONAL MATCH (p:Person {handle: t.by})
            FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:APPROVED]->(a))""", {"tried": tried})


FULLTEXT = [("Lesson", "text"), ("Change", "summary"), ("Alert", "signal"), ("Incident", "title")]
SIGNATURE_DIM = 18


def _write_indexes(g: Graph) -> None:
    for label, prop in FULLTEXT:
        g.write(f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')")
    g.write(f"CREATE VECTOR INDEX FOR (i:Incident) ON (i.signature) "
            f"OPTIONS {{dimension: {SIGNATURE_DIM}, similarityFunction: 'cosine'}}")
    for label, prop in [("Service", "name"), ("Change", "id"), ("Incident", "id"), ("Person", "handle"),
                        ("Team", "id"), ("Alert", "id"), ("Action", "id"), ("Episode", "id")]:
        g.write(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")


# --- Online Boutique: real failures from the RCAEval benchmark -----------------------------------------

BOUTIQUE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "boutique.json"

# The call graph, the alerts and every root cause come from RCAEval RE2-OB (see faultline/rcaeval.py,
# --fixture). Owners come from the project's real CODEOWNERS and who works on each service from its real commit
# history (faultline/owners.py, snapshot in fixtures/boutique_owners.json). The project has no on-call rota and no
# record of who approved the benchmark's fixes, so neither is invented: on-call is empty and approvals unknown.
BOUTIQUE_OWNERS = Path(__file__).resolve().parent / "fixtures" / "boutique_owners.json"
BOUTIQUE = {"title": "Online Boutique · RCAEval"}


def boutique_people() -> dict:
    """Teams from CODEOWNERS, people and their commit counts per service from the git history."""
    snap = json.loads(BOUTIQUE_OWNERS.read_text())
    rule = snap["codeowners"][-1]["owners"] if snap["codeowners"] else []
    team = next((o.lstrip("@").split("/")[-1] for o in rule if "/" in o), "maintainers")
    by_name: dict[str, dict] = {}
    committed, bots = [], {}
    for svc, d in snap["dirs"].items():
        for p in snap["history"][d]["people"]:
            if p["bot"]:
                bots.setdefault(svc, {})[p["login"]] = p["commits"]
                continue
            # one person can appear under their login and under a bare name (commits not linked to an account)
            key = p["name"].strip().lower()
            cur = by_name.setdefault(key, {"handle": p["login"], "name": p["name"]})
            if cur["handle"] == cur["name"] and p["login"] != p["name"]:
                cur["handle"] = p["login"]
            committed.append({"key": key, "service": svc, "count": p["commits"], "last": p["last"]})
    rows: dict[tuple, dict] = {}
    for c in committed:
        k = (by_name[c["key"]]["handle"], c["service"])
        r = rows.setdefault(k, {"handle": k[0], "service": c["service"], "count": 0, "last": ""})
        r["count"] += c["count"]
        r["last"] = max(r["last"], c["last"])
    return {"team": team, "owners_rule": rule, "people": sorted({(v["handle"], v["name"]) for v in by_name.values()}),
            "committed": list(rows.values()), "bots": bots, "source": snap["source"], "fetched": snap["fetched"]}
OPEN_REP = 3   # repetition 3 of every case is open for the agents; repetitions 1 and 2 are solved history


def boutique_cases() -> dict:
    """The fixture with every case placed on the timeline: open cases 4 h apart over the last three days, solved
    ones 10 h apart before that, so no two cases' alerts overlap. Each case gets its incident and alert ids."""
    data = json.loads(BOUTIQUE_FIXTURE.read_text())
    rng = random.Random(11)
    open_, done = [c for c in data["cases"] if c["rep"] == OPEN_REP], [c for c in data["cases"] if c["rep"] != OPEN_REP]
    rng.shuffle(open_)
    rng.shuffle(done)
    for k, c in enumerate(open_):
        c.update(id=f"INC-{401 + k}", inject=NOW - 30 * MIN - k * 4 * HOUR, status="open")
    for k, c in enumerate(done):
        c.update(id=f"INC-{101 + k}", inject=NOW - 4 * DAY - k * 10 * HOUR, status="resolved")
    for c in open_ + done:
        n = c["id"].split("-")[1]
        for i, a in enumerate(c["alerts"]):
            a.update(id=f"A-{n}{i:02d}", at=c["inject"] + a["offset"])
            if a["z"] >= 1000:   # a flat baseline makes z astronomical; past 999 the number says nothing more
                a["signal"] = a["signal"].split(" z=")[0] + " z>999"
        front = [a["at"] for a in c["alerts"] if a["service"] == "frontend"]
        c["started"] = front[0] if front else (c["alerts"][0]["at"] if c["alerts"] else c["inject"])
    return {**data, "open": open_, "done": done}


def _boutique(g: Graph) -> None:
    data = boutique_cases()
    real = boutique_people()
    team = real["team"]
    names = sorted({s for e in data["edges"] for s in e} | {a["service"] for c in data["cases"] for a in c["alerts"]})
    spec = {**BOUTIQUE, "teams": {team: team}, "people": [],
            "services": [(n, team, "datastore" if n == "redis" else "service", 1, n == "frontend") for n in names],
            "calls": data["edges"]}
    _write_org(g, spec)
    g.write("MATCH (o:Org) SET o.owners_source = $src, o.owners_fetched = $f, o.codeowners = $rule",
            {"src": real["source"], "f": real["fetched"], "rule": " ".join(real["owners_rule"])})
    g.write("UNWIND $people AS p CREATE (:Person {handle: p[0], name: p[1], on_call: false, source: 'git history'})",
            {"people": [list(p) for p in real["people"]]})
    g.write("""
        UNWIND $rows AS r
        MATCH (p:Person {handle: r.handle}), (s:Service {name: r.service})
        CREATE (p)-[:COMMITTED {count: r.count, last: r.last}]->(s)""", {"rows": real["committed"]})
    g.write("""
        UNWIND $bots AS b MATCH (s:Service {name: b.service})
        SET s.bot_commits = b.n, s.bots = b.names""",
            {"bots": [{"service": k, "n": sum(v.values()), "names": sorted(v)} for k, v in real["bots"].items()]})
    cases = data["open"] + data["done"]
    g.write("""
        UNWIND $alerts AS a
        MATCH (s:Service {name: a.service})
        CREATE (:Alert {id: a.id, at: a.at, severity: 'page', signal: a.signal, z: a.z, series: a.series,
                        offset: a.offset})-[:FIRED_ON]->(s)""",
            {"alerts": [a for c in cases for a in c["alerts"]]})

    def title(c):
        # Says only what on-call would see first. The metric that fired is left out: it can name the fault.
        return f"Storefront degraded · {len(c['alerts'])} services alerting"
    _write_incidents(g, [{"id": c["id"], "title": title(c), "symptom": "frontend", "started": c["started"],
                          "status": "open", "severity": "SEV2"} for c in data["open"]])
    history = []
    for c in data["done"]:
        rc, mine = c["root_cause"], [a for a in c["alerts"] if a["service"] == c["root_cause"]]
        after = sorted({a["service"] for a in c["alerts"] if a["service"] != rc})
        history.append({
            "id": c["id"], "title": f"{title(c)} ({c['fault']} fault)", "symptom": "frontend", "started": c["started"],
            "severity": "SEV2", "cause_service": rc, "cause_kind": "infra", "cause_at": c["inject"],
            "cause_summary": f"{rc}: {c['fault']} fault injected (RCAEval {c['case']})", "cause_author": None,
            "fix": "page_owner", "fix_summary": f"Fault removed from {rc}; metrics back to baseline",
            "approver": None,
            "lesson": (f"A {c['fault']} fault on {rc} showed first as {mine[0]['signal']}" if mine else
                       f"A {c['fault']} fault on {rc} raised no alert on {rc} itself") +
                      (f"; then {', '.join(after)} alerted." if after else ".")})
    _write_history(g, history)


def build(tenant: str, *, force: bool = False) -> dict:
    g = Graph(tenant)
    if g.exists():
        if not force:
            return {"tenant": tenant, "built": False}
        g.drop()
    _write_indexes(g)
    if tenant == "kestrel":
        rng = random.Random(7)
        _write_org(g, KESTREL)
        changes, alerts, incidents = _planted_kestrel()
        _write_changes(g, _changes_kestrel(rng) + changes)
        _write_alerts(g, alerts)
        _write_incidents(g, incidents)
        _write_history(g, _history_kestrel())
        _write_runbooks(g, RUNBOOKS_KESTREL)
        from .tools import store_signature
        for row in g.read("MATCH (i:Incident {status: 'resolved'}) RETURN i.id AS id"):
            store_signature(g, row["id"])
    elif tenant == "harbor":
        _write_org(g, HARBOR)
        _write_changes(g, [{"id": "D-1", "kind": "deploy", "service": "shipments", "at": NOW - 30 * MIN,
                            "author": "li", "summary": "shipments: new carrier adapter", "version": "1.4.0",
                            "risk": "medium"}])
        _write_alerts(g, [{"id": "A-1", "service": "shipments", "at": NOW - 25 * MIN, "severity": "page",
                           "signal": "5xx 18%"}])
        _write_incidents(g, [{"id": "INC-7", "title": "Tracking page errors", "symptom": "tracking-web",
                              "started": NOW - 24 * MIN, "status": "open", "severity": "SEV2"}])
    elif tenant == "boutique":
        _boutique(g)
        from .tools import store_signature
        for row in g.read("MATCH (i:Incident {status: 'resolved'}) RETURN i.id AS id"):
            store_signature(g, row["id"])
    else:
        raise ValueError(f"unknown tenant {tenant}")
    counts = g.read("MATCH (n) RETURN count(n) AS nodes")[0]["nodes"]
    edges = g.read("MATCH ()-[r]->() RETURN count(r) AS edges")[0]["edges"]
    return {"tenant": tenant, "built": True, "nodes": counts, "edges": edges}


TENANTS = {"kestrel": KESTREL["title"], "boutique": BOUTIQUE["title"], "harbor": HARBOR["title"]}


def build_all(force: bool = False) -> list[dict]:
    return [build(t, force=force) for t in TENANTS]


if __name__ == "__main__":
    import sys
    for r in build_all(force="--force" in sys.argv):
        print(r)
