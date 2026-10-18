"""Learning from outcomes: people's verdicts on suspects, the track record of each service, and weights fitted on
a company's own solved incidents."""
import pytest
from fastapi.testclient import TestClient

from faultline import agent, learn, reasoning, seed, tools
from faultline.db import Graph


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def test_a_person_ruling_a_suspect_out_takes_effect_at_once(fresh):
    assert agent.judged(fresh, "INC-302")["suspects"][0]["change"]["id"] == "D-5201"
    with _client() as c:
        r = c.post("/api/kestrel/incidents/INC-302/feedback",
                   json={"target": "D-5201", "verdict": "not_cause", "who": "sam", "note": "rolled back already, still failing"})
        assert r.status_code == 200
    r = agent.judged(fresh, "INC-302")
    assert r["suspects"][0]["change"]["id"] != "D-5201"
    ruled = next(s for s in r["suspects"] if (s["change"] or {}).get("id") == "D-5201")
    assert ruled["score"] == 0 and ruled["feedback"]["by"] == "sam"
    hyp = next(h for h in reasoning.build(fresh, r)["hypotheses"] if h["label"] == "D-5201")
    assert any(e["kind"] == "feedback" and "ruled out by sam" in e["text"] for e in hyp["contradicts"])
    # changing your mind replaces the verdict instead of stacking a second one
    tools.give_feedback(fresh, "INC-302", "D-5201", "confirmed", "sam")
    assert fresh.read("MATCH (f:Feedback {target: 'D-5201'}) RETURN f.verdict AS v") == [{"v": "confirmed"}]
    assert agent.judged(fresh, "INC-302")["suspects"][0]["change"]["id"] == "D-5201"


def test_the_track_record_never_counts_the_incident_being_scored():
    hist = [{"id": "I1", "cause": "db", "alerting": ["db", "api"]},
            {"id": "I2", "cause": "cache", "alerting": ["db", "cache"]}]
    assert tools.track_record(hist, "db") == round(2 / 4, 3)            # caused 1 of 2, smoothed
    assert tools.track_record(hist, "db", exclude="I1") == round(1 / 3, 3)
    assert tools.track_record(hist, "api", exclude="I1") == 0.5          # no history left: no opinion


@pytest.fixture(scope="module")
def tt():
    seed.build("trainticket", force=True)        # trains as part of the build
    return Graph("trainticket")


def test_weights_are_learned_from_history_and_checked_on_the_open_incidents(tt):
    m = learn.summary(tt)
    cv, hand, lrn = m["evaluation"]["cross_validation"], m["evaluation"]["hand"], m["evaluation"]["learned"]
    assert m["enabled"] and cv["learned_top1"] >= cv["hand_top1"]      # the decision uses the history only
    assert lrn["top1"] >= hand["top1"] and lrn["brier"] < hand["brier"]  # the open incidents are the test
    assert m["weights"]["strength"] > 0 and m["weights"]["track"] > 0
    r = agent.judged(tt, seed.rcaeval_cases("trainticket")["open"][0]["id"])
    assert any("track" in s["factors"] for s in r["suspects"] if s["type"] == "signal")


def test_training_rows_come_only_from_solved_incidents_and_verdicts(tt):
    resolved = {r["id"] for r in tt.read("MATCH (i:Incident {status: 'resolved'}) RETURN i.id AS id")}
    open_ = seed.rcaeval_cases("trainticket")["open"]
    rows = learn.training_rows(tt)
    assert {r[3] for r in rows} <= resolved
    case = open_[0]
    top = tools.rank_suspects(tt, case["id"])["suspects"][0]
    tools.give_feedback(tt, case["id"], tools._label(top), "not_cause", "t")
    fb = [r for r in learn.training_rows(tt) if r[3] == case["id"]]
    assert len(fb) == 1 and fb[0][1] == 0 and fb[0][2] == top["service"]
    tt.write("MATCH (f:Feedback) DETACH DELETE f")


def test_too_little_history_means_no_model(fresh, tt, monkeypatch):
    out = learn.train(fresh, "kestrel")                 # eight solved incidents, none a silent failure
    assert not out["trained"] and learn.summary(fresh) is None
    before = learn.summary(tt)["weights"]
    monkeypatch.setattr(learn, "MIN_ROWS", 10**6)       # Train Ticket's 1,000 rows, held to an impossible bar
    out = learn.train(tt, "trainticket")
    assert not out["trained"] and "needs" in out["reason"] and learn.summary(tt)["weights"] == before
