"""Public post-mortems as precedent. The real list has no licence and is never committed, so these tests use
entries written here."""
from pathlib import Path

from fastapi.testclient import TestClient

from faultline import agent, public_postmortems as P
from faultline.db import Graph

SAMPLE = """# A list
## Config Errors

[Acme](https://example.com/acme). A connection pool limit was lowered in a config push and every client queued for connections until the limit was raised again.

[Initech](https://example.com/initech). A feature flag was flipped for all users at once.

## Hardware/Power Failures

[Globex](https://example.com/globex). A host's disk filled up and every container scheduled on that node failed together.

## Other lists of postmortems

[Somewhere](https://example.com/list). Not an outage.
"""


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_the_list_is_parsed_by_failure_class():
    e = P.parse(SAMPLE)
    assert [(x["company"], x["class"]) for x in e] == [("Acme", "Config Errors"), ("Initech", "Config Errors"),
                                                        ("Globex", "Hardware/Power Failures")]
    assert e[0]["url"] == "https://example.com/acme"


def test_an_incident_is_matched_by_class_and_shared_words(fresh):
    P.write(P.parse(SAMPLE))
    m = P.similar(agent.judged(fresh, "INC-301"))         # a pgbouncer connection-limit config change
    assert m["class"] == "Config Errors" and m["matches"][0]["company"] == "Acme"
    assert {"connections", "limit"} <= set(m["matches"][0]["shared"])
    host = P.similar(agent.judged(fresh, "INC-305"))      # a failing node
    assert host["class"] == "Hardware/Power Failures" and host["matches"][0]["company"] == "Globex"
    with _client() as c:
        assert c.get("/api/kestrel/incidents/INC-301/public").json()["matches"][0]["url"] == "https://example.com/acme"


def test_without_the_list_nothing_is_claimed(fresh):
    Graph(P.KNOWLEDGE).drop()
    assert P.similar(agent.judged(fresh, "INC-301")) == {"available": False, "matches": [], "source": P.REPO}
    assert agent.preview("kestrel", "INC-301")["ranking"]["public"]["available"] is False


def test_none_of_the_list_ships_with_the_repo():
    root = Path(__file__).resolve().parent.parent
    for f in (root / "faultline" / "fixtures").iterdir():
        assert "danluu" not in f.read_text(errors="ignore"), f.name
