"""The Claude path, end to end, without a key: the real Anthropic SDK builds and sends every request, but a fake
transport answers with scripted model turns. This checks what the live run depends on: the request each role
sends, that only that role's tools reach the model, that tool calls run against the graph, that the four agents
hand off through it, and that claims the graph can't back are dropped from the findings."""
import json

import anthropic
import httpx2 as httpx   # the HTTP client the Anthropic SDK is built on

from faultline import agent, db, live_run

ROLE_OF = [("submit_findings", "responder"), ("challenge_leader", "skeptic"), ("rank_suspects", "investigator"),
           ("get_incident", "triage")]
SCRIPT = {
    "triage": [("get_incident", {"incident_id": "INC-302"}),
               ("write_handoff", {"incident_id": "INC-302", "summary": "mobile-bff failing; auth and users changed"})],
    "investigator": [("read_handoff", {"incident_id": "INC-302", "role": "triage"}),
                     ("propose_action", {"incident_id": "INC-302"}),               # not this role's tool
                     ("rank_suspects", {"incident_id": "INC-302"}),
                     ("spread_tree", {"incident_id": "INC-302"}),
                     ("write_handoff", {"incident_id": "INC-302", "summary": "D-5201 leads"})],
    "skeptic": [("challenge_leader", {"incident_id": "INC-302"}),
                ("write_handoff", {"incident_id": "INC-302", "summary": "D-5201 upheld"})],
    "responder": [("submit_findings", {
        "incident_id": "INC-302", "root_cause_service": "auth", "change_id": "D-5201", "confidence": "high",
        "summary": "D-5201 broke token validation on auth; mobile-bff logins failed above it.",
        "evidence": ["D-5201 changed auth", "C-5101 changed auth"]})],
}


def _transport(seen: list[dict]):
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        names = {t["name"] for t in body["tools"]}
        role = next(r for tool, r in ROLE_OF if tool in names)
        turn = sum(1 for m in body["messages"] if m["role"] == "user") - 1
        seen.append({"role": role, "turn": turn, "body": body, "headers": dict(request.headers), "url": str(request.url)})
        name, args = SCRIPT[role][turn]
        return httpx.Response(200, json={
            "id": f"msg_{role}_{turn}", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": f"{role} calls {name}"},
                        {"type": "tool_use", "id": f"tu_{role}_{turn}", "name": name, "input": args}],
            "stop_reason": "tool_use", "stop_sequence": None,
            "usage": {"input_tokens": 1000, "output_tokens": 100, "cache_read_input_tokens": 500}})
    return httpx.MockTransport(handle)


def test_four_claude_agents_run_through_the_real_sdk(fresh, monkeypatch):
    seen: list[dict] = []
    client = anthropic.Anthropic(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=_transport(seen)))
    monkeypatch.setattr(agent, "_client", lambda: client)
    monkeypatch.setattr(agent, "claude_available", lambda: True)

    out = agent.investigate("kestrel", "INC-302")
    assert out["mode"] == "claude" and "fallback_reason" not in out

    # every request: the model, adaptive thinking, the beta, and only that role's tools
    assert [s["role"] for s in seen] == ["triage"] * 2 + ["investigator"] * 5 + ["skeptic"] * 2 + ["responder"]
    for s in seen:
        assert s["body"]["model"] == agent.MODEL == "claude-opus-5-5"
        assert s["body"]["thinking"]["type"] == "adaptive"
        assert "server-side-fallback-2026-07-01" in s["headers"]["anthropic-beta"]
        assert {t["name"] for t in s["body"]["tools"]} == set(agent.ROLE_TOOLS[s["role"]])
    assert out["usage"] == {"input_tokens": 10000, "output_tokens": 1000, "cache_read_input_tokens": 5000, "requests": 10}

    steps = {(st["role"], st["tool"]): st for st in out["steps"]}
    # the investigator read triage's handoff back from the graph, not from memory
    assert "mobile-bff failing" in json.dumps(steps[("investigator", "read_handoff")]["result"])
    # a tool outside the role is refused, and the model is told so
    assert "not available to the investigator agent" in steps[("investigator", "propose_action")]["error"]
    sent = next(s for s in seen if s["role"] == "investigator" and s["turn"] == 2)["body"]["messages"][-1]["content"][0]
    assert sent["is_error"] is True
    # tool results reach the model: the judge's ranking names D-5201
    after_rank = next(s for s in seen if s["role"] == "investigator" and s["turn"] == 3)["body"]["messages"][-1]["content"][0]
    assert "D-5201" in after_rank["content"]
    assert steps[("investigator", "spread_tree")]["result"]["count"] == 3

    # the false claim (C-5101 changed payments-db) is dropped; the true one stays
    assert out["findings"]["change_id"] == "D-5201"
    assert out["findings"]["evidence"] == ["D-5201 changed auth"]
    bad = next(c for c in out["claim_check"]["claims"] if c["text"] == "C-5101 changed auth")
    assert not bad["ok"] and bad["problems"] == ["C-5101 changed payments-db, not auth"]


def test_an_api_failure_falls_back_to_the_offline_agents(fresh, monkeypatch):
    def broken(request):
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}})
    client = anthropic.Anthropic(api_key="bad", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(broken)))
    monkeypatch.setattr(agent, "_client", lambda: client)
    monkeypatch.setattr(agent, "claude_available", lambda: True)
    out = agent.investigate("kestrel", "INC-302")
    assert out["mode"] == "offline" and out["fallback_reason"].startswith("AuthenticationError")
    assert out["ranking"]["suspects"][0]["change"]["id"] == "D-5201"


def test_make_live_run_writes_the_transcript(monkeypatch, tmp_path):
    seen: list[dict] = []
    client = anthropic.Anthropic(api_key="test-key", max_retries=0, http_client=httpx.Client(transport=_transport(seen)))
    monkeypatch.setattr(agent, "_client", lambda: client)
    monkeypatch.setattr(agent, "claude_available", lambda: True)
    monkeypatch.setattr(db, "GRAPH_PREFIX", "livecheck_t_")
    monkeypatch.setattr(live_run, "OUT", tmp_path)
    monkeypatch.setattr("sys.argv", ["live_run", "INC-302"])
    assert live_run.main() == 0
    text = (tmp_path / "live-run-INC-302.md").read_text()
    assert "four agents played by claude-opus-5-5" in text and "10 model requests" in text
    assert "| investigator | `rank_suspects` |" in text and "error: propose_action is not available" in text
    assert "The deterministic judge leads with **D-5201**: they agree." in text
    assert "✗ C-5101 changed auth (C-5101 changed payments-db, not auth)" in text
    assert not [n for n in db.client().list_graphs() if n.startswith("livecheck_t_")]     # scratch graphs dropped


def test_live_run_needs_a_key_and_a_scratch_prefix(monkeypatch):
    monkeypatch.setattr("sys.argv", ["live_run"])
    monkeypatch.setattr(agent, "claude_available", lambda: False)
    assert live_run.main() == 2
    monkeypatch.setattr(agent, "claude_available", lambda: True)
    assert live_run.main() == 2          # the test prefix is not livecheck_: it won't touch other graphs
