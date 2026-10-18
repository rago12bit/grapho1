"""Accuracy eval. Plants random faults on Kestrel's real topology and checks whether the judge names them.

    docker compose run --rm app python -m faultline.eval            # 150 scenarios, ~30 s
    docker compose run --rm app python -m faultline.eval --n 400 --seed 3

Each scenario is one incident with a known cause, spaced a day apart on the same graph so windows never
overlap. Scenario kinds:
  deep    a deploy/config/flag change 1-3 hops below the symptom; alerts climb from it to the symptom
  silent  the same, but the cause never alerts itself (queues and databases often don't)
  infra   nothing changed; a dependency fails and the alerts climb from it
  shared  a deep change that also breaks a second service, which opens its own incident
  host    nothing changed; a node degrades, and two services it runs (neither calling the other, sharing no other
          node) fail together, their alerts climbing to the symptom along two branches
Every scenario also gets distractors: a more recent change closer to the symptom (sometimes on the symptom
itself) with no alerts below it, a change off the dependency path, and an older low-risk change on it.

Reported: top-1 / top-3 accuracy per kind, the same with each scoring factor switched off, two naive
baselines, linked-incident precision/recall, and a learning run: every phase-1 incident is resolved with its
true cause, then the same faults are planted again with fresh distractors.
"""
from __future__ import annotations

import argparse
import json
import random
import time
from collections import defaultdict
from pathlib import Path

from . import tools
from .db import Graph
from .seed import (DAY, HOUR, KESTREL, KESTREL_NODES, MIN, NOW, RUNBOOKS_KESTREL, _write_indexes, _write_org,
                   _write_runbooks, _write_runtime, kestrel_runtime)

TENANT = "evalbench"
KINDS = ("deep", "silent", "infra", "shared", "host")
RISKS = ["high", "high", "medium", "medium", "low"]


class World:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.kind = {s[0]: s[2] for s in KESTREL["services"]}
        self.down: dict[str, list[str]] = defaultdict(list)
        self.up: dict[str, list[str]] = defaultdict(list)
        for a, b in KESTREL["calls"]:
            self.down[a].append(b)
            self.up[b].append(a)
        self.n = 0
        self.runtime = kestrel_runtime()
        self.nodes = {d["service"]: {p["node"] for p in d["pods"]} for d in self.runtime}

    def reaches(self, a: str, b: str) -> bool:
        return any(p[-1] == b for p in self.paths_down(a, 6))

    def paths_down(self, start: str, max_len: int = 3) -> list[list[str]]:
        out, stack = [], [[start]]
        while stack:
            p = stack.pop()
            if len(p) > 1:
                out.append(p)
            if len(p) <= max_len:
                stack += [p + [n] for n in self.down[p[-1]] if n not in p]
        return out

    def uid(self, prefix: str) -> str:
        self.n += 1
        return f"{prefix}-{self.n}"


def _distractors(w: World, sym: str, exclude: set[str], t_ref: int, start: int) -> list[dict]:
    rng = w.rng
    near = rng.choice([x for x in [sym] + w.down[sym] if x not in exclude] or [sym])
    out = [{"id": w.uid("C"), "kind": rng.choice(["deploy", "flag"]), "service": near,
            "at": rng.randint(t_ref - 40 * MIN, start - MIN), "author": None,
            "summary": "distractor: close to the symptom", "risk": rng.choice(RISKS)}]
    off = rng.choice(sorted(set(w.kind) - {sym, *(n for p in w.paths_down(sym, 5) for n in p)} or {"sendgrid"}))
    out.append({"id": w.uid("C"), "kind": "deploy", "service": off, "at": start - rng.randint(2, 30) * MIN,
                "author": None, "summary": "distractor: off the path", "risk": "high"})
    old = rng.choice(w.down[sym])
    out.append({"id": w.uid("C"), "kind": "deploy", "service": old, "at": start - rng.randint(70, 110) * MIN,
                "author": None, "summary": "distractor: old and low risk", "risk": "low"})
    return out


def _plant_host(w: World, day: int, cause: tuple[str, str] | None = None) -> dict:
    """A degrading node: two services it runs fail within minutes of each other and nothing changed."""
    rng = w.rng
    base = NOW - day * DAY
    deployable = sorted(s for s, k in w.kind.items() if k == "service")
    options = []
    for sym in deployable:
        below = sorted({n for p in w.paths_down(sym, 5) for n in p} - {sym})
        for i, a in enumerate(below):
            for b in below[i + 1:]:
                shared = w.nodes.get(a, set()) & w.nodes.get(b, set())
                if len(shared) == 1 and not w.reaches(a, b) and not w.reaches(b, a):
                    node = next(iter(shared))
                    if not cause or cause[0] == node:
                        options.append((sym, a, b, node))
    sym, a, b, node = rng.choice(options)
    t0 = base - rng.randint(4, 45) * MIN
    times = {a: t0, b: t0 + rng.randint(0, 2) * MIN}
    for leaf in (a, b):
        path = min((p for p in w.paths_down(sym, 5) if p[-1] == leaf), key=len)
        t = times[leaf]
        for svc in reversed(path[:-1]):
            t += rng.randint(1, 3) * MIN
            times[svc] = min(times.get(svc, t), t)
    alerts = [{"id": w.uid("A"), "service": svc, "at": t, "severity": "page", "signal": "synthetic"}
              for svc, t in sorted(times.items(), key=lambda x: x[1])]
    start = times[sym]
    inc = {"id": w.uid("INC"), "title": f"{sym} failing", "symptom": sym, "started": start, "status": "open",
           "severity": "SEV2"}
    return {"kind": "host", "incident": inc["id"], "linked": None, "truth": {"service": node, "change": None, "host": True},
            "symptom": sym, "changes": _distractors(w, sym, {a, b}, t0, start), "alerts": alerts, "incidents": [inc],
            "path": [sym, a]}


def _plant(w: World, kind: str, day: int, cause: tuple[str, str] | None = None) -> dict:
    """One incident with a known cause. `cause` (service, change kind) replays a fault for the learning run."""
    if kind == "host":
        return _plant_host(w, day, cause)
    rng = w.rng
    base = NOW - day * DAY
    deployable = {s for s, k in w.kind.items() if k == "service"}
    for _ in range(200):
        sym = rng.choice(sorted(deployable))
        paths = [p for p in w.paths_down(sym) if (kind != "infra" or w.kind[p[-1]] != "service")]
        if cause:
            paths = [p for p in paths if p[-1] == cause[0]]
        if kind == "silent":
            paths = [p for p in paths if len(p) >= 3]
        if kind == "shared":
            paths = [p for p in paths if any(u not in p for u in w.up[p[-1]] if u in deployable)]
        if paths:
            break
    else:
        raise RuntimeError("could not place scenario")
    path = rng.choice(paths)
    culprit = path[-1]
    change_kind = cause[1] if cause else rng.choice(["deploy", "config", "flag"])
    lag = rng.randint(4, 45) * MIN
    t_change = base - lag
    changes, alerts, incidents = [], [], []

    # Alerts climb from the culprit to the symptom, each a few minutes after the one below.
    t = t_change + rng.randint(1, 4) * MIN
    culprit_alert = t
    for i, svc in enumerate(reversed(path)):
        if not (kind == "silent" and i == 0):
            alerts.append({"id": w.uid("A"), "service": svc, "at": t, "severity": "page", "signal": "synthetic"})
        t += rng.randint(1, 4) * MIN
    start = t - MIN
    truth = {"service": culprit, "change": None}
    if kind != "infra":
        cid = w.uid("C")
        changes.append({"id": cid, "kind": change_kind, "service": culprit, "at": t_change, "author": None,
                        "summary": f"planted {change_kind} on {culprit}", "risk": rng.choice(RISKS)})
        truth["change"] = cid
    incidents.append({"id": w.uid("INC"), "title": f"{sym} failing", "symptom": sym, "started": start,
                      "status": "open", "severity": "SEV2"})

    linked = None
    if kind == "shared":
        # A second caller of the culprit breaks too and gets its own incident.
        other = rng.choice([u for u in w.up[culprit] if u not in path and w.kind[u] == "service"])
        t2 = culprit_alert + rng.randint(1, 4) * MIN
        alerts.append({"id": w.uid("A"), "service": other, "at": t2, "severity": "page", "signal": "synthetic"})
        linked = w.uid("INC")
        incidents.append({"id": linked, "title": f"{other} failing", "symptom": other, "started": t2 + MIN,
                          "status": "open", "severity": "SEV3"})

    # Distractors.
    # A change close to the symptom, sometimes newer than the cause and sometimes older, never alerting below.
    near = rng.choice([x for x in [sym] + w.down[sym] if x != culprit])
    changes.append({"id": w.uid("C"), "kind": rng.choice(["deploy", "flag"]), "service": near,
                    "at": rng.randint(t_change - 40 * MIN, start - MIN), "author": None,
                    "summary": "distractor: close to the symptom", "risk": rng.choice(RISKS)})
    off = rng.choice(sorted(set(w.kind) - {sym, *(n for p in w.paths_down(sym, 5) for n in p)} or {"sendgrid"}))
    changes.append({"id": w.uid("C"), "kind": "deploy", "service": off, "at": start - rng.randint(2, 30) * MIN,
                    "author": None, "summary": "distractor: off the path", "risk": "high"})
    old = rng.choice(w.down[sym])
    changes.append({"id": w.uid("C"), "kind": "deploy", "service": old, "at": start - rng.randint(70, 110) * MIN,
                    "author": None, "summary": "distractor: old and low risk", "risk": "low"})
    return {"kind": kind, "incident": incidents[0]["id"], "linked": linked, "truth": truth, "symptom": sym,
            "changes": changes, "alerts": alerts, "incidents": incidents, "path": path}


def _write(g: Graph, scenarios: list[dict]) -> None:
    norm = lambda c: {**{"version": None, "key": None, "old": None, "new": None}, **c}  # noqa: E731
    from .seed import _write_alerts, _write_changes, _write_incidents
    _write_changes(g, [norm(c) for s in scenarios for c in s["changes"]])
    _write_alerts(g, [a for s in scenarios for a in s["alerts"]])
    _write_incidents(g, [i for s in scenarios for i in s["incidents"]])


def _hit(s: dict, truth: dict) -> bool:
    if truth["change"]:
        return (s["change"] or {}).get("id") == truth["change"]
    if truth.get("host"):
        return s["type"] == "host" and s["service"] == truth["service"]
    return s["type"] == "signal" and s["service"] == truth["service"]


def _rescore(suspects: list[dict], drop: str | None) -> list[dict]:
    def score(s):
        w = tools.WEIGHTS if s["type"] == "change" else tools.HOST_WEIGHTS if s["type"] == "host" else tools.SIGNAL_WEIGHTS
        return sum(v * s["factors"].get(k, 0) for k, v in w.items() if k != drop)
    return sorted(suspects, key=score, reverse=True)


def _nearest_recent(r: dict) -> list[dict]:
    """Baseline: blame the change with the fewest hops to the symptom, most recent first. No alerts, no memory."""
    ch = [s for s in r["suspects"] if s["type"] == "change"]
    return sorted(ch, key=lambda s: (s["hops"], s["minutes_before"]))


def _most_recent(r: dict) -> list[dict]:
    """Baseline: blame the most recent change on the dependency path."""
    return sorted([s for s in r["suspects"] if s["type"] == "change"], key=lambda s: s["minutes_before"])


def _evaluate(g: Graph, scenarios: list[dict]) -> dict:
    variants = ["full", "full + skeptic"] + [f"no {k}" for k in tools.WEIGHTS] + ["nearest recent change", "most recent change"]
    calib = defaultdict(list)
    top1 = {v: defaultdict(list) for v in variants}
    top3 = {v: defaultdict(list) for v in variants}
    link_tp = link_fp = link_fn = 0
    for sc in scenarios:
        r = tools.rank_suspects(g, sc["incident"], limit=50)
        review = tools.skeptic_review(r)
        orders = {"full": r["suspects"], "full + skeptic": review["suspects"],
                  "nearest recent change": _nearest_recent(r), "most recent change": _most_recent(r)}
        calib[review["confidence"]].append(bool(review["suspects"]) and _hit(review["suspects"][0], sc["truth"]))
        for k in tools.WEIGHTS:
            orders[f"no {k}"] = _rescore(r["suspects"], k)
        for v, order in orders.items():
            for bucket in (sc["kind"], "all"):
                top1[v][bucket].append(bool(order) and _hit(order[0], sc["truth"]))
                top3[v][bucket].append(any(_hit(s, sc["truth"]) for s in order[:3]))
        got = {o["id"] for o in (r["suspects"][0].get("also_explains") or [])} if r["suspects"] else set()
        want = {sc["linked"]} if sc["linked"] else set()
        if r["suspects"] and _hit(r["suspects"][0], sc["truth"]):
            link_tp += len(got & want)
            link_fp += len(got - want)
            link_fn += len(want - got)
        else:
            link_fn += len(want)
    pct = lambda xs: round(100 * sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    return {
        "top1": {v: {b: pct(x) for b, x in d.items()} for v, d in top1.items()},
        "top3": {v: {b: pct(x) for b, x in d.items()} for v, d in top3.items()},
        "counts": {b: len(x) for b, x in top1["full"].items()},
        "calibration": {k: {"n": len(v), "correct": pct(v)} for k, v in sorted(calib.items())},
        "links": {"precision": round(link_tp / (link_tp + link_fp), 3) if link_tp + link_fp else None,
                  "recall": round(link_tp / (link_tp + link_fn), 3) if link_tp + link_fn else None},
    }


def run(n: int = 150, seed: int = 7, learning: bool = True) -> dict:
    t0 = time.time()
    rng = random.Random(seed)
    w = World(rng)
    g = Graph(TENANT)
    g.drop()
    _write_indexes(g)
    _write_org(g, KESTREL)
    _write_runbooks(g, RUNBOOKS_KESTREL)
    _write_runtime(g, w.runtime, "synthetic", KESTREL_NODES, "platform")
    kinds = [KINDS[i % len(KINDS)] for i in range(n)]
    rng.shuffle(kinds)
    phase1 = [_plant(w, k, 2 + 2 * i) for i, k in enumerate(kinds)]
    _write(g, phase1)
    res = {"n": n, "seed": seed, "phase1": _evaluate(g, phase1)}

    if learning:
        # Resolve every phase-1 incident with its true cause, then plant the same faults again.
        for sc in phase1:
            tools.resolve_incident(g, sc["incident"], sc["truth"]["change"], "eval", cause_service=sc["truth"]["service"])
            if sc["linked"]:
                tools.resolve_incident(g, sc["linked"], sc["truth"]["change"], "eval", cause_service=sc["truth"]["service"])
        replays = []
        for i, sc in enumerate(phase1):
            kind = sc["kind"]
            ck = next((c["kind"] for c in sc["changes"] if c["id"] == sc["truth"]["change"]), None)
            replays.append(_plant(w, kind, 2 + 2 * (n + i) + 1, cause=(sc["truth"]["service"], ck)))
        _write(g, replays)
        res["phase2"] = _evaluate(g, replays)
    res["seconds"] = round(time.time() - t0, 1)
    g.drop()
    return res


def table(res: dict) -> str:
    p1 = res["phase1"]
    kinds = [k for k in KINDS if k in p1["counts"]]
    head = "| | " + " | ".join(f"{k} ({p1['counts'][k]})" for k in kinds) + f" | **all ({p1['counts']['all']})** | top-3 all |"
    lines = [head, "|---" * (len(kinds) + 3) + "|"]
    for v, row in p1["top1"].items():
        name = {"full": "**Faultline (judge)**", "full + skeptic": "**Faultline (judge + skeptic)**"}.get(v, v)
        lines.append(f"| {name} | " + " | ".join(f"{row.get(k)}%" for k in kinds)
                     + f" | **{row['all']}%** | {p1['top3'][v]['all']}% |")
    out = "\n".join(lines)
    cal = p1["calibration"]
    out += "\n\nWhen it says… " + "; ".join(f"**{k}** confidence ({cal[k]['n']} cases): right {cal[k]['correct']}%"
                                           for k in ("high", "medium", "low") if k in cal) + "."
    out += (f"\n\nLinked incidents (shared scenarios): precision {p1['links']['precision']}, "
            f"recall {p1['links']['recall']}.")
    if "phase2" in res:
        a, b = p1["top1"]["full"]["all"], res["phase2"]["top1"]["full"]["all"]
        out += (f"\n\nLearning: the same {res['n']} faults planted again after the first round was resolved: "
                f"top-1 {a}% → {b}%. Without the precedent factor the second round scores "
                f"{res['phase2']['top1']['no precedent']['all']}%.")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args()
    res = run(a.n, a.seed)
    print(table(res))
    print(f"\n{res['n']} scenarios, seed {res['seed']}, {res['seconds']} s")
    if a.json:
        a.json.write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
