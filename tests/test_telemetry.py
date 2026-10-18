"""Logs and traces as signals (faultline/telemetry.py) and the topology of each RCAEval system, on small inputs
built here, so they run without the 3 GB benchmark."""
import numpy as np
import pandas as pd

from faultline import rcaeval, telemetry as T


def test_templates_mask_what_varies_and_keep_the_status_class():
    a = T.template("GetCartAsync called with userId=77cc67a9-3828-410c-a117-d90c9628cef1")
    b = T.template("GetCartAsync called with userId=65a9523a-d63d-4a00-01fb-8410aaaa0000")
    assert a == b == "GetCartAsync called with userId=<id>"
    assert T.template("POST /orders 500 12.3 ms - -") == "POST /orders 5xx <n> ms - -"
    assert T.template("GET /catalogue 200 6.1 ms - -") == "GET /catalogue 2xx <n> ms - -"
    assert T.template("dial tcp 10.0.3.7:8080: connect: connection refused") == "dial tcp <ip>: connect: connection refused"


def test_error_lines_are_errors_and_card_numbers_are_not():
    hit = lambda m: bool(T.ERROR.search(m))  # noqa: E731
    assert hit("POST /orders 500 12 ms") and hit("connection refused") and hit("java.lang.NullPointerException")
    assert hit("java.lang.IllegalStateException: pool exhausted") and hit("ERROR c.f.Foo : boom")
    assert not hit("GET /catalogue 200 6 ms") and not hit("ts=1 method=List err=null took=2ms")
    assert not hit("terrorist_count=0")
    assert not hit('PaymentService#Charge invoked with request {"credit_card_number":"4432-8015-6152-0454"}')


def _logs(inject: int, errors_after: int) -> pd.DataFrame:
    rows = []
    for t in range(inject - 600, inject + 600):
        rows.append({"timestamp": t, "container_name": "cart", "message": f"served request id={t}"})
        if t % 60 == 0:
            rows.append({"timestamp": t, "container_name": "cart", "message": "upstream timeout talking to redis"})
        if t >= inject + 40 and t % max(1, 60 // errors_after) == 0:
            rows.append({"timestamp": t, "container_name": "cart", "message": f"redis: connection refused ({t})"})
    return pd.DataFrame(rows)


def test_error_logs_alert_only_when_they_rise_and_stay_up():
    inject = 10_000
    df = T.prepare_logs(_logs(inject, errors_after=30), lambda s: s)
    al = T.log_alerts(df, inject)
    assert [a["service"] for a in al] == ["cart"] and inject + 30 <= al[0]["at"] <= inject + 60
    # the timeout line was there all along: not new; the refused line is
    pats = T.log_patterns(df, inject)
    assert pats[0]["new"] and "connection refused" in pats[0]["template"]
    assert not any("timeout" in p["template"] for p in pats)
    quiet = T.prepare_logs(_logs(inject, errors_after=0) if False else _logs(inject, 1)[lambda d: ~d.message.str.contains("refused")], lambda s: s)
    assert T.log_alerts(quiet, inject) == []


def _spans(inject: int, slow_from: int | None) -> pd.DataFrame:
    rows, k = [], 0
    for t in range(inject - 300, inject + 300):
        for _ in range(2):
            k += 1
            slow = slow_from is not None and t >= slow_from
            rows.append({"spanID": f"f{k}", "parentSpanID": None, "serviceName": "frontend", "operationName": "GET /cart",
                         "startTime": t * 10**6, "duration": (90_000 if slow else 9_000), "statusCode": 0})
            rows.append({"spanID": f"c{k}", "parentSpanID": f"f{k}", "serviceName": "cart", "operationName": "GetCart",
                         "startTime": t * 10**6 + 10, "duration": (80_000 if slow else 2_000) + (k % 7) * 50, "statusCode": 0})
            rows.append({"spanID": f"x{k}", "parentSpanID": f"c{k}", "serviceName": "cart",
                         "operationName": "opentelemetry.proto.collector.trace.v1.TraceService/Export",
                         "startTime": t * 10**6 + 20, "duration": 500_000, "statusCode": 0})
    return pd.DataFrame(rows)


def test_operations_and_span_alerts_from_traces():
    inject = 1_705_000_000
    df = T.prepare_traces(_spans(inject, slow_from=inject + 20), lambda s: s)
    assert not df.op.str.contains("TraceService").any()          # the exporter's own spans are dropped
    ops = T.operations(df, inject)
    edge = next(e for e in ops["edges"] if e["from_service"] == "frontend")
    assert (edge["to_service"], edge["to_op"]) == ("cart", "GetCart") and edge["rps"] >= 1.9 and edge["p99_ms"] < 3
    al, hot = T.trace_alerts(df, inject)
    assert {a["service"] for a in al} == {"cart", "frontend"}
    assert hot["cart"]["op"] == "GetCart" and hot["cart"]["ratio"] > 10
    calm = T.prepare_traces(_spans(inject, slow_from=None), lambda s: s)
    assert T.trace_alerts(calm, inject)[0] == []


def test_merge_keeps_the_earliest_onset_and_every_source():
    m = [{"service": "cart", "at": 120, "z": 9, "signal": "cpu z=9", "metric": "cart_cpu"}]
    lg = [{"service": "cart", "at": 90, "z": 7, "signal": "error logs z=7", "source": "logs", "metric": "cart_error-logs"},
          {"service": "redis", "at": 60, "z": 6, "signal": "error logs z=6", "source": "logs", "metric": "redis_error-logs"}]
    out = T.merge_alerts(m, lg, [])
    assert [(a["service"], a["at"]) for a in out] == [("redis", 60), ("cart", 90)]
    assert out[1]["sources"] == ["metrics: cpu z=9", "logs: error logs z=7"]


def test_sock_shop_topology_comes_from_its_code_and_train_ticket_from_spans():
    ss = rcaeval.topology("ss", None, {"front-end", "orders", "payment", "carts", "carts-db", "user", "shipping"})
    assert ("orders", "payment") in ss and ("front-end", "carts") in ss and ("carts", "carts-db") in ss
    assert all(a in {"front-end", "orders", "payment", "carts", "carts-db", "user", "shipping"} for a, _ in ss)
    spans = pd.DataFrame(
        [{"spanID": f"r{i}", "parentSpanID": None, "serviceName": "ts-travel-service"} for i in range(6)]
        + [{"spanID": f"c{i}", "parentSpanID": f"r{i}", "serviceName": "ts-route-service"} for i in range(6)])
    tt = rcaeval.topology("tt", spans, {"ts-travel-service", "ts-route-service", "ts-route-mongo", "ts-ui-dashboard"})
    assert ("ts-ui-dashboard", "ts-travel-service") in tt            # the untraced gateway calls every trace root
    assert ("ts-travel-service", "ts-route-service") in tt
    assert ("ts-route-service", "ts-route-mongo") in tt               # each service's own database


def test_scores_follow_rcaeval():
    rows = [{"suite": "RE2", "system": "ss", "alerted": True, "ac": {m: [False, True, True, True, True] for m in rcaeval.METHODS}},
            {"suite": "RE2", "system": "ss", "alerted": True, "ac": {m: [True] * 5 for m in rcaeval.METHODS}}]
    s = rcaeval.summarize(rows)["RE2-SS"]["judge"]
    assert s == {"ac1": 1, "ac3": 2, "avg5": 0.9}
    assert np.isclose((0.8 + 1.0) / 2, 0.9)


def test_the_span_profile_tells_a_slow_service_from_one_that_waits():
    """frontend calls cart; after the fault cart's own work is 20x slower and frontend only waits for it."""
    inject = 1_705_000_000
    df = T.prepare_traces(_spans(inject, slow_from=inject), lambda s: s)
    p = T.span_profile(df, inject)
    assert p["cart"]["self_ratio"] > 10 and p["cart"]["own_share"] > 0.9
    assert p["frontend"]["child_ratio"] > 10 and p["frontend"]["self_ratio"] < 2 and p["frontend"]["own_share"] < 0.3


def test_the_ranking_goes_on_past_the_judges_suspects():
    from faultline import tools
    sus = [{"type": "host", "service": "node-1", "host": {"services": ["b", "a"]}},
           {"type": "signal", "service": "c"}]
    alerts = [{"service": "d", "at": 0, "strength": 2.0}, {"service": "e", "at": 1, "strength": 9.0},   # stronger, not earlier
              {"service": "a", "at": 2, "strength": 1.0}]
    assert tools.service_order(sus, alerts, {"q1": 3.0, "q2": 7.0, "e": 9.0}) == ["b", "a", "c", "e", "d", "q2", "q1"]
