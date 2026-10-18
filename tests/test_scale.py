"""The scale benchmark, small enough for the suite: the same build and checks as at 1,000+ services."""
from faultline import scale, tools
from faultline.db import Graph


def _in_a_fresh_process(hash_seed: str) -> str:
    """Python randomises string hashing per process, so set order can differ between runs: build in a child
    process with its own hash seed and report what was built."""
    import os
    import subprocess
    import sys
    code = ("from faultline import scale; r = scale.run(services=80, incidents=20, reps=1); "
            "print(r['nodes'], r['edges'], r['incidents'], r['judge_top1'])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         env={**os.environ, "PYTHONHASHSEED": hash_seed})
    return out.stdout.strip()


def test_a_generated_company_is_built_the_same_every_time_and_the_judge_finds_its_faults():
    assert _in_a_fresh_process("1") == _in_a_fresh_process("2")
    a = scale.run(services=80, incidents=20, reps=2)
    assert a["services"] >= 75 and a["operations"] == 5 * a["services"] and a["incidents"] >= 15
    assert a["judge_top1"] >= 0.8 * a["incidents"] and a["judge_top3"] >= 0.9 * a["incidents"]
    assert set(a["timings"]) >= {"the judge (rank_suspects)", "betweenness over CALLS"}
    assert not Graph(scale.TENANT).exists()           # the benchmark cleans up after itself


def test_betweenness_is_exact_on_small_graphs_and_sampled_on_big_ones(fresh, monkeypatch):
    exact = tools.single_points_of_failure(fresh, 3)
    assert all(r["exact"] for r in exact)
    monkeypatch.setattr(tools, "BETWEENNESS_SAMPLE", 5)
    sampled = tools.single_points_of_failure(fresh, 3)
    assert sampled and not any(r["exact"] for r in sampled)
    assert [r["betweenness"] for r in sampled] != [r["betweenness"] for r in exact]   # five sources, not all of them
    assert sampled == tools.single_points_of_failure(fresh, 3)   # fixed seed: the same sample every time
