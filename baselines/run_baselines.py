"""Run RCAEval's own baseline implementations on the same cases Hypocenter is scored on.

Runs inside the faultline-baselines image (see Dockerfile), never in Hypocenter's. It calls the methods from the
RCAEval package (MIT) unchanged and prepares each case the way RCAEval's main.py does: metrics only, latency-50
dropped, forward-filled, the last 10 minutes before the injection and the first 10 after (--length 20), the
injection time given to the method, and the SLI chosen by the same rule. Service ranks come from metric names
the same way (`name.split("_")[0].replace("-db", "")`, duplicates dropped), and every method, Hypocenter included,
is scored with that same mapping.

    docker build -t faultline-baselines baselines
    docker run --rm -v "$PWD/data:/data" -v "$PWD/baselines:/b:ro" faultline-baselines \
        python /b/run_baselines.py --methods baro,nsigma,circa,e_diagnosis --suites re2,re3

Writes /data/rcaeval/baselines/<method>.json: per case the top-10 service ranking and the run time.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import time
import warnings
from argparse import Namespace
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

DATA = Path("/data/rcaeval")
OUT = DATA / "baselines"
LENGTH_MIN = 20
TRACE_METHODS = {"microrank", "tracerca"}


def services(ranks: list[str]) -> list[str]:
    out: list[str] = []
    for x in ranks:
        s = str(x).split("_")[0].replace("-db", "")
        if s not in out:
            out.append(s)
    return out


def metric_input(case_dir: Path, truth: str) -> tuple[pd.DataFrame, int, str | None]:
    data = pd.read_parquet(case_dir / "metrics.parquet")
    if "time.1" in data:
        data = data.drop(columns=["time.1"])
    data = data.loc[:, ~data.columns.str.endswith("_latency-50")]
    data = data.replace([np.inf, -np.inf], np.nan).ffill().fillna(0)
    inject = int((case_dir / "inject_time.txt").read_text().strip().split(".")[0])
    half = LENGTH_MIN * 60 // 2
    data = pd.concat([data[data["time"] < inject].tail(half), data[data["time"] >= inject].head(half)], ignore_index=True)
    data = data.rename(columns={c: c.replace("_latency-90", "_latency") for c in data.columns if c.endswith("_latency-90")})
    system = case_dir.name.split("_")[0][3:5]
    default = {"tt": "ts-ui-dashboard_latency"}.get(system, "frontend_latency")
    sli = f"{truth}_latency" if f"{truth}_latency" in data else default
    return data, inject, sli


class Timeout(Exception):
    pass


def _alarm(*_):
    raise Timeout()


def run(method: str, cases: list[str], idx: pd.DataFrame, limit_s: int) -> dict:
    import RCAEval.e2e as e2e
    fn = getattr(e2e, method)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{method}.json"
    done = json.loads(path.read_text()) if path.exists() else {}
    for case in cases:
        if case in done:
            continue
        d = DATA / case
        truth = str(idx.loc[case, "root_cause_service"])
        t0 = time.time()
        signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(limit_s)
        try:
            if method in TRACE_METHODS:
                if not (d / "traces.parquet").exists():
                    continue
                spans = pd.read_parquet(d / "traces.parquet")
                inject = int((d / "inject_time.txt").read_text().strip().split(".")[0])
                out = fn(spans, inject * 1_000_000, dataset=case[:5])
            else:
                data, inject, sli = metric_input(d, truth)
                out = fn(data, inject, dataset=f"{case[:3]}-{case[3:5]}", anomalies=None, dk_select_useful=False, sli=sli,
                         verbose=False, n_iter=len(data.columns) - 1, args=Namespace(root_path=os.getcwd(), data_path=str(d)))
            ranks = services(out.get("ranks") or [])[:10]
            done[case] = {"ranks": ranks, "seconds": round(time.time() - t0, 2)}
        except Timeout:
            done[case] = {"error": f"timeout after {limit_s}s"}
        except Exception as e:  # a method failing on a case counts as a miss, and is reported
            done[case] = {"error": f"{type(e).__name__}: {e}"[:300]}
        finally:
            signal.alarm(0)
        ok = "error" not in done[case]
        print(f"{method:12} {case:48} {'top=' + ','.join(done[case]['ranks'][:3]) if ok else done[case]['error'][:80]}", flush=True)
        path.write_text(json.dumps(done, indent=0))
    return done


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--methods", default="baro,nsigma,circa,e_diagnosis")
    ap.add_argument("--suites", default="re2,re3")
    ap.add_argument("--systems", default="ob,ss,tt")
    ap.add_argument("--limit", type=int, default=600, help="seconds per case before a method is stopped")
    a = ap.parse_args()
    idx = pd.read_parquet(DATA / "cases.parquet").set_index("case")
    cases = sorted(c for c in idx.index if c[:3] in a.suites.split(",") and c[3:5] in a.systems.split(",")
                   and (DATA / c / "metrics.parquet").exists())
    for m in a.methods.split(","):
        run(m, cases, idx, a.limit)


if __name__ == "__main__":
    main()
