"""Real owners for the Online Boutique tenant, from the project itself.

    python -m faultline.owners --fetch      # GitHub API, unauthenticated (about 25 requests); writes the snapshot

Ownership comes from GoogleCloudPlatform/microservices-demo's CODEOWNERS; who actually works on each service comes
from the commit history of its `src/<service>` directory (the last 200 commits each, bots kept apart). The result
is committed as faultline/fixtures/boutique_owners.json so the demo builds offline and the same way every time.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path

REPO = "GoogleCloudPlatform/microservices-demo"
SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "boutique_owners.json"
# Graph service name -> directory in the repo. redis is the cart's datastore: it has no source of its own, so it
# is credited to the cart service's directory.
DIRS = {"frontend": "src/frontend", "checkoutservice": "src/checkoutservice", "paymentservice": "src/paymentservice",
        "emailservice": "src/emailservice", "currencyservice": "src/currencyservice",
        "shippingservice": "src/shippingservice", "productcatalogservice": "src/productcatalogservice",
        "recommendationservice": "src/recommendationservice", "adservice": "src/adservice",
        "cartservice": "src/cartservice", "redis": "src/cartservice"}


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "faultline"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()) if "api.github.com" in url else r.read().decode()


def _is_bot(login: str, name: str) -> bool:
    return login.endswith("[bot]") or "bot" in login.lower() or name.lower().endswith("bot")


def fetch() -> dict:
    codeowners = _get(f"https://raw.githubusercontent.com/{REPO}/main/.github/CODEOWNERS")
    rules = []
    for line in codeowners.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            pattern, *owners = line.split()
            rules.append({"pattern": pattern, "owners": owners})
    per_dir: dict[str, dict] = {}
    for d in sorted(set(DIRS.values())):
        commits = []
        for page in (1, 2):
            batch = _get(f"https://api.github.com/repos/{REPO}/commits?path={d}&per_page=100&page={page}")
            commits += batch
            if len(batch) < 100:
                break
            time.sleep(0.5)
        people: dict[str, dict] = {}
        for c in commits:
            login = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
            name = c["commit"]["author"]["name"]
            p = people.setdefault(login, {"login": login, "name": name, "commits": 0, "last": None,
                                          "bot": _is_bot(login, name)})
            p["commits"] += 1
            p["last"] = max(p["last"] or "", c["commit"]["author"]["date"])
        per_dir[d] = {"commits_read": len(commits),
                      "people": sorted(people.values(), key=lambda p: (-p["commits"], p["login"]))}
        print(d, len(commits), "commits,", sum(not p["bot"] for p in people.values()), "people")
    return {"source": f"https://github.com/{REPO}", "fetched": time.strftime("%Y-%m-%d"),
            "codeowners": rules, "dirs": DIRS, "history": per_dir}


def owners_of(snapshot: dict, path: str) -> list[str]:
    """CODEOWNERS semantics: the last matching rule wins."""
    hit: list[str] = []
    for r in snapshot["codeowners"]:
        p = r["pattern"].strip("/")
        if p == "*" or path.startswith(p.rstrip("*").rstrip("/")):
            hit = r["owners"]
    return hit


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    if ap.parse_args().fetch:
        SNAPSHOT.write_text(json.dumps(fetch(), indent=1))
        print("wrote", SNAPSHOT)


if __name__ == "__main__":
    main()
