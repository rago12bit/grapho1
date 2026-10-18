"""Learning the judge's weights from outcomes.

    docker compose run --rm app python -m faultline.learn --tenant trainticket    # fit, compare, store

The hand-set weights in tools.py were chosen by reasoning and by the synthetic eval. Each company's own solved
incidents are better evidence: for every solved incident the judge is re-run as if it were open, each
silent-failure suspect becomes a row (its factors, plus its track record: how often it was the cause when it
alerted, counted without this incident), labelled by whether it was the root cause the incident was closed with.
People's verdicts on open incidents ("not the cause", "confirmed") are rows too. A logistic regression with an
L2 penalty is fitted on those rows (numpy, no other dependency).

The model is stored as a (:Model) node and the judge only uses it if it is enabled, which `train` decides from the
history alone: two-fold cross-validation over the solved incidents, learned against hand weights. The open
incidents are then a clean test, scored against the benchmark's answer key (repetition 3 of every RCAEval case;
the history is repetitions 1 and 2). Calibration (does a stated probability of 0.8 come true 80% of the time?) is
reported for both.
"""
from __future__ import annotations

import argparse
import json
import math

import numpy as np

from . import seed, tools
from .db import Graph

FEATURES = tools.LEARNED_FEATURES
L2 = 1.0
MIN_ROWS = 40


def _rows(g: Graph, incident_id: str, truth: str | None, history: list[dict], feedback: dict | None = None) -> list[tuple]:
    """One row per silent-failure suspect, scored with the hand weights (so factors are what they'd be without a
    model) plus the track record."""
    out = []
    r = tools.rank_suspects(g, incident_id, limit=50)
    for s in r["suspects"]:
        if s["type"] != "signal":
            continue
        f = dict(s["factors"])
        f.setdefault("strength", 0.0)
        f["track"] = tools.track_record(history, s["service"], exclude=incident_id)
        if feedback is not None:
            said = feedback.get(tools._label(s))
            if not said:
                continue
            y = 1 if said["verdict"] == "confirmed" else 0
        else:
            y = int(s["service"] == truth)
        out.append(([f[k] for k in FEATURES], y, s["service"], incident_id, s["score"]))
    return out


def _without_model(g: Graph):
    """Training and the hand-weight baseline must see the judge as it is without a model."""
    class _Off:
        def __enter__(self):
            self.rows = g.read("MATCH (m:Model {kind: 'signal'}) RETURN m.enabled AS on")
            g.write("MATCH (m:Model {kind: 'signal'}) SET m.enabled = false")
        def __exit__(self, *a):
            if self.rows:
                g.write("MATCH (m:Model {kind: 'signal'}) SET m.enabled = $on", {"on": self.rows[0]["on"]})
    return _Off()


def training_rows(g: Graph) -> list[tuple]:
    history = tools.outcome_history(g)
    rows = []
    with _without_model(g):
        for h in history:
            rows += _rows(g, h["id"], h["cause"], history)
        for inc in g.read("MATCH (:Feedback)-[:ABOUT]->(i:Incident) RETURN DISTINCT i.id AS id"):
            rows += _rows(g, inc["id"], None, history, tools.feedback_on(g, inc["id"]))
    return rows


def fit(X: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 4000, lr: float = 0.5) -> tuple[np.ndarray, float]:
    """Logistic regression by gradient descent; positives are rare (one per incident), so they are weighted up."""
    w, b = np.zeros(X.shape[1]), 0.0
    pos = max(1, int(y.sum()))
    sw = np.where(y == 1, (len(y) - pos) / pos, 1.0)
    sw = sw / sw.mean()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        err = (p - y) * sw
        w -= lr * (X.T @ err / len(y) + l2 * w / len(y))
        b -= lr * err.mean()
    return w, b


def _answer_key(tenant: str) -> dict[str, str]:
    data = seed.boutique_cases() if tenant == "boutique" else seed.rcaeval_cases(tenant)
    return {c["id"]: c["root_cause"] for c in data["open"]}


def evaluate(g: Graph, tenant: str, model: dict) -> dict:
    """Top-1 on the open incidents with the hand weights and with the learned ones, and calibration of each:
    how often the leader's stated probability came true, in bins."""
    truth = _answer_key(tenant)
    history = tools.outcome_history(g)
    res = {"hand": [], "learned": []}
    with _without_model(g):
        for inc, t in truth.items():
            r = tools.rank_suspects(g, inc, limit=50)
            sus = r["suspects"]
            res["hand"].append((sus[0]["service"] == t if sus else False, r["confidence_p"]))
            sig = []
            for s in sus:
                if s["type"] != "signal":
                    continue
                f = dict(s["factors"])
                f.setdefault("strength", 0.0)
                f["track"] = tools.track_record(history, s["service"], exclude=inc)
                p = tools.learned_score(model, f)
                sig.append((p, s["service"]))
            sig.sort(reverse=True)
            res["learned"].append((bool(sig) and sig[0][1] == t, sig[0][0] if sig else 0.0))
    out = {}
    for k, v in res.items():
        hits = [h for h, _ in v]
        probs = [p for _, p in v]
        bins = []
        for lo, hi in ((0, .5), (.5, .7), (.7, .85), (.85, 1.01)):
            idx = [i for i, p in enumerate(probs) if lo <= p < hi]
            if idx:
                bins.append({"stated": f"{lo:.2f}–{min(hi, 1):.2f}", "n": len(idx),
                             "came_true": round(sum(hits[i] for i in idx) / len(idx), 2),
                             "mean_stated": round(sum(probs[i] for i in idx) / len(idx), 2)})
        out[k] = {"top1": sum(hits), "n": len(hits),
                  "brier": round(sum((p - h) ** 2 for h, p in zip(hits, probs)) / len(hits), 3) if hits else None,
                  "calibration": bins}
    return out


def cross_validate(rows: list[tuple], folds: int = 2) -> dict:
    """Top-1 over the solved incidents, each fold scored by a model fitted on the others, against the hand
    weights' own ranking of the same suspects. Uses only the history, so deciding on it leaves the open
    incidents untouched as a test."""
    incidents = sorted({r[3] for r in rows})
    hand = learned = n = 0
    for f in range(folds):
        test = set(incidents[f::folds])
        tr = [r for r in rows if r[3] not in test]
        w, b = fit(np.array([r[0] for r in tr], dtype=float), np.array([r[1] for r in tr], dtype=float))
        for inc in test:
            rs = [r for r in rows if r[3] == inc]
            if not any(r[1] for r in rs):
                continue           # the cause wasn't a silent-failure suspect: neither ranking can name it
            n += 1
            hand += max(rs, key=lambda r: r[4])[1]
            learned += max(rs, key=lambda r: float(np.dot(w, r[0]) + b))[1]
    return {"incidents": n, "hand_top1": hand, "learned_top1": learned}


def train(g: Graph, tenant: str | None = None) -> dict:
    """Fit on the solved incidents (and feedback) and store the model. It is enabled only if, in cross-validation
    over the solved incidents, it names the cause at least as often as the hand weights. The open incidents are
    then a clean test (scored when an answer key exists for this company)."""
    rows = training_rows(g)
    if len(rows) < MIN_ROWS or sum(r[1] for r in rows) < 5:
        return {"trained": False, "rows": len(rows), "reason": f"needs {MIN_ROWS} rows and 5 known causes"}
    X = np.array([r[0] for r in rows], dtype=float)
    y = np.array([r[1] for r in rows], dtype=float)
    cv = cross_validate(rows)
    w, b = fit(X, y)
    model = {"w": {k: round(float(v), 4) for k, v in zip(FEATURES, w)}, "b": round(float(b), 4), "n": len(rows)}
    enabled = cv["learned_top1"] >= cv["hand_top1"]
    ev = evaluate(g, tenant, model) if tenant in ("boutique", *seed.RCAEVAL_TENANTS) else None
    ev = {**(ev or {}), "cross_validation": cv}
    g.write("MATCH (m:Model {kind: 'signal'}) DELETE m")
    g.write("""CREATE (:Model {kind: 'signal', weights: $w, bias: $b, n: $n, positives: $pos, enabled: $on,
                               evaluation: $ev, features: $f})""",
            {"w": json.dumps(model["w"]), "b": model["b"], "n": len(rows), "pos": int(y.sum()), "on": enabled,
             "ev": json.dumps(ev), "f": FEATURES})
    return {"trained": True, "enabled": enabled, "model": model, "positives": int(y.sum()), "evaluation": ev}


def summary(g: Graph) -> dict | None:
    rows = g.read("""MATCH (m:Model {kind: 'signal'})
                     RETURN m.weights AS w, m.bias AS b, m.n AS n, m.positives AS pos, m.enabled AS on, m.evaluation AS ev""")
    if not rows:
        return None
    r = rows[0]
    return {"weights": json.loads(r["w"]), "bias": r["b"], "rows": r["n"], "positives": r["pos"], "enabled": r["on"],
            "evaluation": json.loads(r["ev"]) if r["ev"] else None,
            "hand_weights": {**tools.SIGNAL_WEIGHTS, "strength": tools.STRENGTH_W},
            "feedback": g.read("""MATCH (f:Feedback)-[:ABOUT]->(i:Incident)
                                  RETURN i.id AS incident, f.target AS target, f.verdict AS verdict, f.by AS by, f.note AS note
                                  ORDER BY f.at DESC LIMIT 10""")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    a = ap.parse_args()
    out = train(Graph(a.tenant), a.tenant)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
