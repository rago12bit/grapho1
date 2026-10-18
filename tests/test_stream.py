"""Investigations streamed as they happen (server-sent events)."""
import json

from fastapi.testclient import TestClient

from faultline import ingest


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_every_step_streams_before_the_result_and_matches_it(fresh):
    with _client() as c:
        r = c.get("/api/kestrel/incidents/INC-301/investigate/stream")
    assert r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    steps = [d for k, d in ev if k == "step"]
    kind, done = ev[-1]
    assert kind == "done" and [k for k, _ in ev[:-1]] == ["step"] * len(steps)
    assert [s["tool"] for s in steps] == [s["tool"] for s in done["steps"]]
    assert steps[0]["role"] == "triage" and any(s["role"] == "skeptic" for s in steps) and done["reasoning_written"]


def test_two_streams_at_once_never_mix_their_steps(fresh):
    """Each stream's sink lives in its own worker's context, so concurrent runs can't see each other's steps."""
    from concurrent.futures import ThreadPoolExecutor
    with _client() as c, ThreadPoolExecutor(2) as pool:
        texts = list(pool.map(lambda inc: c.get(f"/api/kestrel/incidents/{inc}/investigate/stream?pace_ms=20").text,
                              ["INC-301", "INC-303"]))
    for inc, text in zip(["INC-301", "INC-303"], texts):
        steps = [d for k, d in _events(text) if k == "step"]
        ids = {d["input"]["incident_id"] for d in steps if "incident_id" in d["input"]}
        assert ids == {inc}, (inc, ids)


def test_a_busy_incident_streams_an_error_not_a_hang(fresh):
    with ingest.exclusive("kestrel", "INC-301"):
        with _client() as c:
            ev = _events(c.get("/api/kestrel/incidents/INC-301/investigate/stream").text)
    assert ev == [("error", ev[0][1])] and "INC-301" in ev[0][1]["detail"]
