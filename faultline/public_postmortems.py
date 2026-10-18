"""Public post-mortems as precedent: "this looks like the time X happened to company Y".

    docker compose run --rm app python -m faultline.public_postmortems --fetch

Source: Dan Luu's list of public post-mortems (github.com/danluu/post-mortems), about 250 write-ups from real
companies, each a link to the company's own report with a one-paragraph summary, grouped into failure classes
(config errors, hardware and power, conflicts, time, database, uncategorized).

The list has no licence, so none of it ships with Hypocenter: this module only fetches it, on your machine, into a
graph of its own (`knowledge`, shared by every company and untouched by "Reset"), and every entry keeps the link
to the original report. Nothing is committed to the repository.

    (:PublicPostmortem {company, url, text})-[:IN_CLASS]->(:FailureClass {name})

An incident is matched by failure class (from the kind of cause the judge leads with) and by a full-text search
of the summaries with the words of the incident's evidence. Matches are shown as evidence, never scored.
"""
from __future__ import annotations

import argparse
import re
import urllib.request

from .db import Graph

SOURCE = "https://raw.githubusercontent.com/danluu/post-mortems/master/README.md"
REPO = "https://github.com/danluu/post-mortems"
KNOWLEDGE = "knowledge"
CLASSES = ("Config Errors", "Hardware/Power Failures", "Conflicts", "Time", "Database", "Uncategorized")
_ENTRY = re.compile(r"^\[([^\]]+)\]\((\S+?)\)\.?\s*(.+)$")


def parse(markdown: str) -> list[dict]:
    """Entries of the form `[Company](url). Summary` under the failure-class headings."""
    out, cls = [], None
    for line in markdown.splitlines():
        if line.startswith("## "):
            name = line[3:].strip()
            cls = name if name in CLASSES else None
            continue
        m = _ENTRY.match(line.strip())
        if cls and m:
            out.append({"company": m.group(1).strip(), "url": m.group(2), "text": m.group(3).strip()[:1200], "class": cls})
    return out


def write(entries: list[dict], g: Graph | None = None) -> dict:
    g = g or Graph(KNOWLEDGE)
    g.drop()
    g.write("CALL db.idx.fulltext.createNodeIndex('PublicPostmortem', 'text', 'company')")
    g.write("UNWIND $c AS c CREATE (:FailureClass {name: c})", {"c": list(CLASSES)})
    g.write("""
        UNWIND $e AS e MATCH (c:FailureClass {name: e.class})
        CREATE (:PublicPostmortem {company: e.company, url: e.url, text: e.text, source: $src})-[:IN_CLASS]->(c)""",
            {"e": entries, "src": REPO})
    return {"entries": len(entries), "classes": len({e["class"] for e in entries})}


def fetch() -> dict:
    with urllib.request.urlopen(SOURCE, timeout=30) as r:
        return write(parse(r.read().decode()))


def available() -> bool:
    g = Graph(KNOWLEDGE)
    return g.exists() and bool(g.read("MATCH (p:PublicPostmortem) RETURN count(p) AS n")[0]["n"])


# The judge's kind of cause -> the list's failure class
KIND_CLASS = {"config": "Config Errors", "flag": "Config Errors", "host": "Hardware/Power Failures"}
DATASTORE_CLASS = "Database"
MIN_SHARED = 2
_STOP = {"with", "from", "after", "that", "this", "were", "into", "then", "when", "which", "their", "about", "service",
         "services", "alerted", "alerting", "baseline", "latency", "change"}


def query_words(text: str, limit: int = 8) -> list[str]:
    seen: list[str] = []
    for w in re.findall(r"[a-zA-Z][a-zA-Z]{3,}", text or ""):
        w = w.lower()
        if w not in _STOP and w not in seen:
            seen.append(w)
    return seen[:limit]


def similar(ranking: dict, limit: int = 4) -> dict:
    """Public post-mortems like this incident's leading explanation."""
    if not available():
        return {"available": False, "matches": [], "source": REPO}
    sus = ranking.get("suspects") or []
    if not sus:
        return {"available": True, "matches": [], "source": REPO}
    top = sus[0]
    kind = "host" if top["type"] == "host" else (top["change"] or {}).get("kind")
    cls = KIND_CLASS.get(kind or "")
    if not cls and top["type"] == "signal" and re.search(r"(-db|db|redis|mongo|mysql|postgres|kafka|es)$", top["service"]):
        cls = DATASTORE_CLASS
    text = " ".join([(top["change"] or {}).get("summary") or "", (top["change"] or {}).get("key") or ""]
                    + [a["signal"] for a in top.get("evidence_alerts") or []])
    words = query_words(text)
    g = Graph(KNOWLEDGE)
    rows = []
    if words:
        rows = g.read("""
            CALL db.idx.fulltext.queryNodes('PublicPostmortem', $q) YIELD node, score
            MATCH (node)-[:IN_CLASS]->(c:FailureClass)
            RETURN node.company AS company, node.url AS url, node.text AS text, c.name AS class, score
            ORDER BY score DESC LIMIT 60""", {"q": " | ".join(words)})
    for r in rows:
        r["same_class"] = r["class"] == cls
        r["shared"] = sorted(set(query_words(r["text"], 400)) & set(words))
    # one shared word is chance, unless the failure class matches too; two is a story that rhymes
    rows = [r for r in rows if len(r["shared"]) >= MIN_SHARED or (r["same_class"] and r["shared"])]
    rows.sort(key=lambda r: (not r["same_class"], -len(r["shared"]), -r["score"]))
    if cls and len(rows) < limit:
        seen = {r["url"] for r in rows}
        rows += [{**r, "same_class": True, "shared": []} for r in g.read("""
            MATCH (p:PublicPostmortem)-[:IN_CLASS]->(:FailureClass {name: $c})
            RETURN p.company AS company, p.url AS url, p.text AS text, $c AS class, 0.0 AS score""", {"c": cls})
            if r["url"] not in seen][: limit - len(rows)]
    picked, companies = [], set()
    for r in rows:                      # one per company, so the list shows different stories
        if r["company"] not in companies:
            companies.add(r["company"])
            picked.append(r)
    return {"available": True, "class": cls, "words": words, "source": REPO,
            "matches": [{**r, "text": r["text"][:300]} for r in picked[:limit]]}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    if ap.parse_args().fetch:
        print(fetch())
