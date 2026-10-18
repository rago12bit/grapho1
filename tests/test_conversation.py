"""Conversation history kept in the graph: each question and answer is a Turn in a Session, linked to the turn
before and to what it was about, and read back so a follow-up knows what "it" refers to."""
from fastapi.testclient import TestClient

from faultline import agent, tools
from faultline.db import Graph

S = "sess-test-0001"


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_a_follow_up_knows_what_it_refers_to(fresh):
    first = agent.ask("kestrel", "what breaks if ledger-db goes down?", S)
    assert first["turn"] == 0 and first["subject"] == "ledger-db"
    again = agent.ask("kestrel", "who owns it?", S)
    assert again["resolved"] == {"it": "ledger-db"}
    assert again["answer"].startswith("ledger-db is owned by Payments")
    # without the conversation, the same words name nothing
    assert "resolved" not in agent.ask("kestrel", "who owns it?")


def test_turns_are_nodes_linked_in_order_and_to_what_they_were_about(fresh):
    for q in ("what about INC-301", "who owns it", "single points of failure"):
        agent.ask("kestrel", q, S)
    turns = tools.conversation(fresh, S)
    assert [t["n"] for t in turns] == [0, 1, 2]
    assert turns[0]["subject"] == "payments-db"                 # an incident's subject is the service at fault
    assert {a["name"] for a in turns[0]["about"]} >= {"INC-301", "C-5101", "payments-db"}
    assert turns[1]["about"] == [{"name": "payments-db", "kind": "Service"}]   # "Payments" the team is not payments
    links = fresh.read("MATCH (a:Turn)-[:FOLLOWS]->(b:Turn) RETURN a.n AS a, b.n AS b ORDER BY a")
    assert links == [{"a": 1, "b": 0}, {"a": 2, "b": 1}]


def test_nothing_is_kept_without_a_session_and_sessions_stay_in_their_company(fresh):
    agent.ask("kestrel", "who owns payments")
    assert fresh.read("MATCH (t:Turn) RETURN count(t) AS n")[0]["n"] == 0
    agent.ask("kestrel", "who owns payments", S)
    assert tools.conversation(Graph("harbor"), S) == []


def test_routes(fresh):
    with _client() as c:
        a = c.post("/api/kestrel/ask", json={"question": "what breaks if ledger-db goes down?", "session": S}).json()
        b = c.post("/api/kestrel/ask", json={"question": "who knows it", "session": S}).json()
        assert a["turn"] == 0 and b["resolved"] == {"it": "ledger-db"}
        h = c.get(f"/api/kestrel/sessions/{S}").json()
        assert [t["question"] for t in h["turns"]] == ["what breaks if ledger-db goes down?", "who knows it"]
        assert c.post("/api/kestrel/ask", json={"question": "who owns it", "session": "Bad Id!"}).status_code == 422
