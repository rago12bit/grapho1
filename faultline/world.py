"""The simulated production system: what really caused each alert, and what a fix actually does.

This stands in for the real world. In production you would not have this module: you would apply the fix and
watch the monitoring. Here, the true cause of every seeded alert is written down below, and applying an approved
fix clears exactly the alerts that fix really addresses, by setting `cleared_at` on those Alert nodes, the way
monitoring would report recovery.

Nothing the agent, the judge or the verifier uses reads this module. They only see the alerts in the graph.
A test checks that faultline.tools and faultline.agent never import it.
"""
from __future__ import annotations

from .db import Graph
from .seed import MIN, NOW

# alert id -> what really caused it: a change id, or "infra:<service>" for a failure with no change.
TRUE_CAUSES: dict[str, dict[str, str]] = {
    "kestrel": {
        "A-9001": "C-5101", "A-9002": "C-5101", "A-9003": "C-5101", "A-9004": "C-5101",
        "A-9101": "D-5201", "A-9102": "D-5201",
        "A-9201": "infra:catalog-es", "A-9202": "infra:catalog-es",
        "A-9301": "infra:k8s-node-4", "A-9302": "infra:k8s-node-4", "A-9303": "infra:k8s-node-4", "A-9304": "infra:k8s-node-4",
    },
    "harbor": {"A-1": "D-1"},
}


def _boutique_truth() -> dict[str, str]:
    # RCAEval's answer key: every alert in a case was caused by the fault injected on its root-cause service.
    from .seed import boutique_cases
    return {a["id"]: f"infra:{c['root_cause']}" for c in boutique_cases()["open"] for a in c["alerts"]}


def _rcaeval_truth(tenant: str) -> dict[str, str]:
    from .seed import RCAEVAL_TENANTS, TENANTS, rcaeval_cases
    if tenant not in TENANTS or tenant not in RCAEVAL_TENANTS:
        return {}
    return {a["id"]: f"infra:{c['root_cause']}" for c in rcaeval_cases(tenant)["open"] for a in c["alerts"]}


TRUE_CAUSES["boutique"] = _boutique_truth()
TRUE_CAUSES["sockshop"] = _rcaeval_truth("sockshop")
TRUE_CAUSES["trainticket"] = _rcaeval_truth("trainticket")
RECOVERY_S = 4 * MIN   # monitoring reports recovery a few minutes after a correct fix
ROLLOUT_S = 1 * MIN    # the deployer reports what is running a minute after a change is merged
FAIL_ROLLOUT: set[str] = set()   # services whose next rollout fails (tests use it: the deploy never lands)


def roll_out(g: Graph, action_id: str, at: int | None = None) -> bool:
    """The deployer: merge the action's change request and report what the service now runs, as a deploy system
    would (a (:Rollout) report). A service in FAIL_ROLLOUT keeps running what it ran before."""
    cr = g.read("""MATCH (cr:ChangeRequest)-[:FOR]->(:Action {id: $id})
                   RETURN cr.service AS svc, cr.key AS key, cr.from AS frm, cr.to AS to""", {"id": action_id})
    if not cr:
        return False
    c = cr[0]
    landed = c["svc"] not in FAIL_ROLLOUT
    g.write("""CREATE (:Rollout {service: $svc, key: $key, value: $value, at: $at, source: 'deployer'})""",
            {"svc": c["svc"], "key": c["key"], "value": c["to"] if landed else c["frm"], "at": (at or NOW) + ROLLOUT_S})
    return landed


def apply_fix(g: Graph, tenant: str, kind: str, target: str, change_id: str | None, at: int | None = None,
              action_id: str | None = None) -> list[str]:
    """Apply an approved fix to the simulated system. Returns the alert ids that recover.

    A revert, rollback or flag change fixes the alerts that change really caused, if its rollout lands. Paging an
    owner fixes an infrastructure failure on that service (the owner repairs it). Anything else changes nothing."""
    truth = TRUE_CAUSES.get(tenant, {})
    if action_id and kind != "page_owner" and not roll_out(g, action_id, at):
        return []                        # the deploy never landed, so nothing recovers
    if kind in ("rollback", "revert_config", "disable_flag"):
        fixed = [a for a, cause in truth.items() if change_id and cause == change_id]
    elif kind == "page_owner":
        fixed = [a for a, cause in truth.items() if cause == f"infra:{target}"]
    else:
        fixed = []
    if fixed:
        g.write("MATCH (a:Alert) WHERE a.id IN $ids AND a.cleared_at IS NULL SET a.cleared_at = $t",
                {"ids": fixed, "t": (at or NOW) + RECOVERY_S})
    return fixed
