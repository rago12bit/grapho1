"""The delivery layer: versions, the commits each one shipped, and the files and services each commit touched.

    docker compose run --rm app python -m faultline.delivery --fetch     # GitHub API, unauthenticated, ~10 requests

Online Boutique's is real: its release tags and, between each pair, the commits and files GitHub's compare API
returns (snapshot in fixtures/boutique_releases.json; the image tags in its manifests are these releases).
Kestrel's deploys get synthetic commits (seed.kestrel_commits) with one planted culprit each.

    (:Version {tag, at})-[:INCLUDES]->(:Commit {sha, message, author, at, additions, deletions})-[:TOUCHED {files}]->(:Service)
    (:Change {kind: 'deploy'})-[:SHIPS]->(:Commit)       (:Image)-[:BUILT_FROM]->(:Version)
    (:Person)-[:AUTHORED]->(:Commit)
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path

from .owners import DIRS, REPO, _get, _is_bot

SNAPSHOT = Path(__file__).resolve().parent / "fixtures" / "boutique_releases.json"
RELEASES = 8          # the last eight releases
NOT_CODE = re.compile(r"(^|/)(docs?|tests?|__tests__|testdata)/|\.md$|README|_test\.(go|py)$|Test\.(java|cs)$|"
                      r"(^|/)(requirements[^/]*\.(txt|in)|go\.(mod|sum)|package(-lock)?\.json|build\.gradle|"
                      r".*\.csproj|Dockerfile)$", re.I)


def service_of(path: str) -> str | None:
    for svc, d in DIRS.items():
        if svc != "redis" and path.startswith(d + "/"):
            return svc
    return None


def fetch() -> dict:
    tags = [t["name"] for t in _get(f"https://api.github.com/repos/{REPO}/tags?per_page=30")
            if re.fullmatch(r"v\d+\.\d+\.\d+", t["name"])]
    tags = sorted(tags, key=lambda t: tuple(int(x) for x in t[1:].split(".")))[-(RELEASES + 1):]
    releases = []
    for prev, tag in zip(tags, tags[1:]):
        cmp_ = _get(f"https://api.github.com/repos/{REPO}/compare/{prev}...{tag}")
        commits = []
        for c in cmp_["commits"]:
            login = (c.get("author") or {}).get("login") or c["commit"]["author"]["name"]
            commits.append({"sha": c["sha"][:12], "message": c["commit"]["message"].split("\n")[0][:200],
                            "author": login, "name": c["commit"]["author"]["name"], "at": c["commit"]["author"]["date"],
                            "bot": _is_bot(login, c["commit"]["author"]["name"])})
        files = [{"path": f["filename"], "additions": f.get("additions", 0), "deletions": f.get("deletions", 0)}
                 for f in cmp_.get("files") or []]
        date = commits[-1]["at"] if commits else None
        releases.append({"tag": tag, "previous": prev, "at": date, "commits": commits, "files": files})
        print(tag, len(commits), "commits", len(files), "files")
        time.sleep(0.5)
    snap = {"source": f"https://github.com/{REPO}", "fetched": time.strftime("%Y-%m-%d"), "releases": releases}
    SNAPSHOT.write_text(json.dumps(snap, indent=1))
    return snap


def load() -> dict:
    return json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {"releases": []}


def _ts(iso: str | None) -> int | None:
    if not iso:
        return None
    from datetime import datetime
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def write_boutique(g) -> dict:
    """Online Boutique's releases into its graph: Versions, the commits each shipped (with the person, when they
    are in the graph, and the services a commit's message names), the files each release changed per service,
    and the image the manifests run built from that release."""
    snap = load()
    services = {r["s"] for r in g.read("MATCH (s:Service) RETURN s.name AS s")}
    vers, commits, per_svc = [], [], []
    for rel in snap["releases"]:
        vers.append({"tag": rel["tag"], "at": _ts(rel["at"]), "previous": rel["previous"], "commits": len(rel["commits"])})
        for c in rel["commits"]:
            named = sorted({svc for svc in services if svc != "redis" and re.search(rf"\b{re.escape(svc)}\b|src/{re.escape(svc)}\b", c["message"])})
            commits.append({**c, "tag": rel["tag"], "at": _ts(c["at"]), "services": named})
        agg: dict[str, dict] = {}
        for f in rel["files"]:
            svc = service_of(f["path"])
            if svc in services:
                a = agg.setdefault(svc, {"files": 0, "code_files": 0, "additions": 0, "deletions": 0})
                a["files"] += 1
                a["code_files"] += not NOT_CODE.search(f["path"])
                a["additions"] += f["additions"]
                a["deletions"] += f["deletions"]
        per_svc += [{"tag": rel["tag"], "service": k, **v} for k, v in agg.items()]
    if not vers:
        return {"versions": 0}
    g.write("CREATE INDEX FOR (c:Commit) ON (c.sha)")
    g.write("UNWIND $v AS v CREATE (:Version {tag: v.tag, at: v.at, previous: v.previous, commits: v.commits})", {"v": vers})
    g.write("""
        UNWIND $c AS c MATCH (v:Version {tag: c.tag})
        CREATE (v)-[:INCLUDES]->(x:Commit {sha: c.sha, message: c.message, at: c.at, bot: c.bot, author: c.author})
        WITH x, c
        OPTIONAL MATCH (p:Person {handle: c.author})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:AUTHORED]->(x))
        WITH x, c
        UNWIND c.services AS svc MATCH (s:Service {name: svc})
        CREATE (x)-[:MENTIONS]->(s)""", {"c": commits})
    g.write("""
        UNWIND $r AS r MATCH (v:Version {tag: r.tag}), (s:Service {name: r.service})
        CREATE (v)-[:CHANGED {files: r.files, code_files: r.code_files, additions: r.additions, deletions: r.deletions}]->(s)""",
            {"r": per_svc})
    g.write("MATCH (i:Image), (v:Version) WHERE i.tag = v.tag CREATE (i)-[:BUILT_FROM]->(v)")
    return {"versions": len(vers), "commits": len(commits), "service_changes": len(per_svc)}


def write_commits(g, commits: list[dict]) -> None:
    """Synthetic deploys' commits: (Change)-[:SHIPS]->(Commit)-[:TOUCHED {files}]->(Service), (Person)-[:AUTHORED]->."""
    g.write("CREATE INDEX FOR (c:Commit) ON (c.sha)")
    g.write("""
        UNWIND $c AS c MATCH (ch:Change {id: c.change}), (s:Service {name: c.service})
        CREATE (ch)-[:SHIPS]->(x:Commit {sha: c.sha, message: c.message, at: c.at, author: c.author, bot: false,
                                         additions: c.additions, deletions: c.deletions, files: c.files,
                                         code_lines: c.code_lines})-[:TOUCHED {files: size(c.files)}]->(s)
        WITH x, c
        OPTIONAL MATCH (p:Person {handle: c.author})
        FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | CREATE (p)-[:AUTHORED]->(x))""", {"c": commits})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    if ap.parse_args().fetch:
        fetch()
