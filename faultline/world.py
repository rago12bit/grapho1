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
    },
    "harbor": {"A-1": "D-1"},
}
RECOVERY_S = 4 * MIN   # monitoring reports recovery a few minutes after a correct fix


def apply_fix(g: Graph, tenant: str, kind: str, target: str, change_id: str | None, at: int | None = None) -> list[str]:
    """Apply an approved fix to the simulated system. Returns the alert ids that recover.

    A revert, rollback or flag change fixes the alerts that change really caused. Paging an owner fixes an
    infrastructure failure on that service (the owner repairs it). Anything else changes nothing."""
    truth = TRUE_CAUSES.get(tenant, {})
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
