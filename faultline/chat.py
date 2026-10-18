"""Chat in the company graph: channels, messages, who posted them, threads, and what each message is about.

    (:Message {at, text})-[:IN_CHANNEL]->(:Channel)   (:Message)-[:POSTED_BY]->(:Person)
    (:Message)-[:REPLY_TO]->(:Message)                (:Message)-[:MENTIONS]->(:Service|:Change|:Incident|:Node)

It reads Slack's export format: a folder with `channels.json`, `users.json` and one folder per channel of
`YYYY-MM-DD.json` message lists. Kestrel Pay's chat (fixtures/kestrel_chat.json) is synthetic, written for the
demo company in the same shape, and is marked as such on every channel. To load a real export:

    docker compose run --rm -v /path/to/export:/export app python -m faultline.chat --tenant kestrel /export

Chat is context for people and for the agents (who decided a change, what was said while it broke). It is
never scored: the judge's verdict does not read it.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .db import Graph

FIXTURE = Path(__file__).parent / "fixtures" / "kestrel_chat.json"
_ID = re.compile(r"\b(INC-\d+|[CDF]-\d+)\b")


def read_slack_export(folder: Path) -> dict:
    """A Slack export folder as {source, channels: [{name, messages: [{user, ts, text, thread_ts?}]}]}."""
    users = {u["id"]: (u.get("name") or u["id"]) for u in json.loads((folder / "users.json").read_text())} \
        if (folder / "users.json").exists() else {}
    out = {"source": f"slack export: {folder.name}", "channels": []}
    for c in json.loads((folder / "channels.json").read_text()):
        msgs = []
        for day in sorted((folder / c["name"]).glob("*.json")):
            for m in json.loads(day.read_text()):
                if m.get("type", "message") != "message" or not m.get("text") or m.get("subtype"):
                    continue
                msgs.append({"user": users.get(m.get("user"), m.get("user") or "unknown"), "ts": m["ts"],
                             "text": m["text"], **({"thread_ts": m["thread_ts"]} if m.get("thread_ts") not in (None, m["ts"]) else {})})
        out["channels"].append({"name": c["name"], "messages": msgs})
    return out


def _mentions(text: str, names: dict[str, str]) -> list[str]:
    """Names the graph has, as written (service names are lowercase; "Payments" the team is not payments)."""
    found = [w for w in re.findall(r"[A-Za-z0-9][\w.-]*[A-Za-z0-9]|[A-Za-z0-9]", text) if w in names]
    found += _ID.findall(text)
    return list(dict.fromkeys(f for f in found if f in names))


def load(g: Graph, export: dict) -> dict:
    """Write an export into the graph. Messages are keyed by channel and timestamp, so loading twice is harmless."""
    names = {r["n"]: r["k"] for r in g.read("""
        MATCH (x) WHERE x:Service OR x:Node OR x:Change OR x:Incident
        RETURN coalesce(x.name, x.id) AS n, labels(x)[0] AS k""")}
    rows = []
    for c in export["channels"]:
        for m in c["messages"]:
            rows.append({"id": f"{c['name']}:{m['ts']}", "channel": c["name"], "user": m["user"], "text": m["text"],
                         "at": int(float(m["ts"])), "reply_to": f"{c['name']}:{m['thread_ts']}" if m.get("thread_ts") else None,
                         "about": _mentions(m["text"], names)})
    g.write("""
        UNWIND $rows AS r
        MERGE (c:Channel {name: r.channel}) ON CREATE SET c.source = $source
        MERGE (p:Person {handle: r.user})
        MERGE (m:Message {id: r.id})
        SET m.text = r.text, m.at = r.at
        MERGE (m)-[:IN_CHANNEL]->(c)
        MERGE (m)-[:POSTED_BY]->(p)""", {"rows": rows, "source": export["source"]})
    g.write("""
        UNWIND $rows AS r
        MATCH (m:Message {id: r.id}), (parent:Message {id: r.reply_to})
        MERGE (m)-[:REPLY_TO]->(parent)""", {"rows": [r for r in rows if r["reply_to"]]})
    g.write("""
        UNWIND $rows AS r
        MATCH (m:Message {id: r.id})
        UNWIND r.about AS name
        MATCH (x) WHERE (x:Service OR x:Node) AND x.name = name OR (x:Change OR x:Incident) AND x.id = name
        MERGE (m)-[:MENTIONS]->(x)""", {"rows": rows})
    return {"channels": len(export["channels"]), "messages": len(rows),
            "mentions": sum(len(r["about"]) for r in rows), "source": export["source"]}


def load_fixture(g: Graph) -> dict:
    return load(g, json.loads(FIXTURE.read_text()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--tenant", default="kestrel")
    a = ap.parse_args()
    print(load(Graph(a.tenant), read_slack_export(a.folder)))


if __name__ == "__main__":
    main()
