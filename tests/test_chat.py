"""Chat in the company graph: Slack-shaped channels and messages, who posted them, threads, what they mention,
and the decision trail of a change. Chat is context only: the verdict never reads it."""
import json

from fastapi.testclient import TestClient

from faultline import agent, chat, tools


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_the_chat_is_in_the_graph_with_threads_and_mentions(fresh):
    n = fresh.read("MATCH (m:Message)-[:IN_CHANNEL]->(c:Channel) RETURN count(m) AS m, count(DISTINCT c) AS c")[0]
    assert (n["m"], n["c"]) == (13, 5)
    assert fresh.read("MATCH (:Message)-[:REPLY_TO]->(:Message) RETURN count(*) AS n")[0]["n"] == 4
    # posters are the company's people, not new ones
    assert fresh.read("MATCH (p:Person) WHERE NOT (p)-[:MEMBER_OF]->() RETURN count(p) AS n")[0]["n"] == 0
    about = {r["m"]: r["x"] for r in fresh.read("""
        MATCH (m:Message)-[:MENTIONS]->(x) RETURN m.text AS m, collect(coalesce(x.name, x.id)) AS x""")}
    marta = next(v for k, v in about.items() if k.startswith("Careful: INC-212"))
    assert set(marta) == {"payments-db", "INC-212"}
    node = next(v for k, v in about.items() if k.startswith("k8s-node-4"))
    assert set(node) == {"k8s-node-4", "catalog", "users"}
    # "Checkout p99 …" names the incident's symptom in words, but "Checkout" the team is not the checkout service
    assert "checkout" not in next(v for k, v in about.items() if k.startswith("Checkout p99"))


def test_a_change_can_be_traced_to_the_conversation_that_decided_it(fresh):
    t = tools.decision_trail(fresh, "C-5101")
    assert t["by"] == "lin" and t["service"] == "payments-db"
    assert t["people_in_discussion"] == ["lin", "ravi", "marta"]
    texts = [m["text"] for m in t["discussion"]]
    assert texts[0].startswith("Cost review follow-up") and any("INC-212" in x for x in texts)   # the warning, a reply
    assert [m["by"] for m in t["said_after"]] == ["ade"]
    answer = agent.ask_offline("kestrel", "why was C-5101 made?")["answer"]
    assert "Ravi Menon in #payments-eng at 12:44" in answer and "Careful: INC-212" in answer


def test_the_agents_read_the_chat_but_the_verdict_never_does(fresh):
    before = tools.rank_suspects(fresh, "INC-302")
    r = agent.investigate_offline("kestrel", "INC-302", propose=False)
    steps = {(s["role"], s["tool"]) for s in r["steps"]}
    assert ("investigator", "chat_about") in steps and ("responder", "decision_trail") in steps
    assert [m["by"] for m in r["ranking"]["chat"]] == ["noor", "sam", "noor"]
    fresh.write("MATCH (m:Message) DETACH DELETE m")
    after = tools.rank_suspects(fresh, "INC-302")
    assert [(tools._label(s), s["score"]) for s in after["suspects"]] == [(tools._label(s), s["score"]) for s in before["suspects"]]


def test_memory_search_reaches_chat(fresh):
    hits = tools.search_memory(fresh, "pgbouncer pool connections")
    assert ("chat", "#payments-eng") in {(h["kind"], h["ref"]) for h in hits}


def test_a_slack_export_folder_loads(fresh, tmp_path):
    (tmp_path / "channels.json").write_text(json.dumps([{"name": "ops"}]))
    (tmp_path / "users.json").write_text(json.dumps([{"id": "U1", "name": "kenji"}, {"id": "U2", "name": "newhire"}]))
    (tmp_path / "ops").mkdir()
    (tmp_path / "ops" / "2026-10-17.json").write_text(json.dumps([
        {"type": "message", "user": "U1", "ts": "1792245000.000100", "text": "catalog-es is red again, see INC-303"},
        {"type": "message", "user": "U2", "ts": "1792245060.000200", "text": "on it", "thread_ts": "1792245000.000100"},
        {"type": "message", "subtype": "channel_join", "user": "U2", "ts": "1792245001.000100", "text": "joined"}]))
    export = chat.read_slack_export(tmp_path)
    assert [len(c["messages"]) for c in export["channels"]] == [2]          # the join notice is skipped
    out = chat.load(fresh, export)
    assert out == {"channels": 1, "messages": 2, "mentions": 2, "source": f"slack export: {tmp_path.name}"}
    assert chat.load(fresh, export)["messages"] == 2                       # loading again changes nothing
    assert fresh.read("MATCH (m:Message)-[:IN_CHANNEL]->(:Channel {name: 'ops'}) RETURN count(m) AS n")[0]["n"] == 2
    replied = fresh.read("""MATCH (:Message)-[:REPLY_TO]->(m:Message)-[:IN_CHANNEL]->(:Channel {name: 'ops'})
                            MATCH (m)-[:MENTIONS]->(x) RETURN coalesce(x.name, x.id) AS x""")
    assert sorted(r["x"] for r in replied) == ["INC-303", "catalog-es"]
    assert fresh.read("MATCH (p:Person {handle: 'kenji'}) RETURN count(p) AS n")[0]["n"] == 1   # an existing person


def test_routes(fresh):
    with _client() as c:
        assert c.get("/api/kestrel/changes/C-5101/trail").json()["by"] == "lin"
        assert c.get("/api/kestrel/changes/C-9999/trail").status_code == 404
