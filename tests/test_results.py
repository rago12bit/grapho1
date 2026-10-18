"""The Results page reads every number from the file that publishes it, and the fix proofs it runs live hold."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from faultline import results, site
from faultline.db import list_tenants

ROOT = Path(__file__).resolve().parents[1]


def test_the_rcaeval_tables_are_read_as_published():
    d = results.load()
    r = d["rcaeval"]
    assert r["datasets"][-1] == "All 360" and sum(r["sizes"][:-1]) == r["sizes"][-1] == 360
    me = next(m for m in r["methods"] if m["name"] == "Hypocenter (metrics)")
    doc = (ROOT / "docs" / "rcaeval-results.md").read_text()
    row = next(line for line in doc.splitlines() if line.startswith("| **Hypocenter (metrics)**"))
    assert me["cells"][-1] == [float(x) for x in re.findall(r"\d\.\d+", row.split("|")[-2])]
    circa = next(m for m in r["methods"] if m["name"] == "CIRCA")
    assert circa["note"] == "240" and circa["cells"][2] == [None, None]          # not run on Train Ticket
    baro = next(x for x in r["re2_tt_published"] if x["name"] == "BARO")
    assert baro["ac1_ours"] == baro["ac1_published"]                            # the harness reproduces the paper


def test_scale_planted_and_learning_come_from_their_files():
    d = results.load()
    big = d["scale"][-1]
    assert big["services"] == 3000 and big["nodes"] > 200_000
    judge = next(q for q in big["queries"] if q["query"].startswith("the judge"))
    assert judge["p50_ms"] == 69.0 and big["planted"] == {"incidents": 198, "first": 197, "top3": 198}
    assert [s["seed"] for s in d["held_out_seeds"]] == [3, 11, 42]
    assert d["synthetic"]["kinds"][:5] == ["deep", "silent", "infra", "shared", "host"]
    assert [c["label"] for c in d["confidence"]] == ["high", "medium", "low"]
    assert {l["company"] for l in d["learned"]} == {"Online Boutique", "Sock Shop", "Train Ticket"}
    assert d["held_out_tt"][0]["name"].startswith("Hypocenter v3") and d["held_out_tt"][0]["ac1"] == 0.71


def test_every_planted_fix_holds_on_a_copy_and_the_decoy_clears_nothing(fresh):
    copies = {t for t in list_tenants() if t.startswith("proof_")}
    rows = site.prove_fixes(fresh)
    assert all(r["holds"] for r in rows) and len(rows) == 5
    decoy = next(r for r in rows if r.get("decoy"))
    assert decoy["verdict"] == "clears nothing" and decoy["predicted_clear"] == []
    real = [r for r in rows if not r.get("decoy")]
    assert all(r["verdict"] == "resolves" and r["predicted_clear"] for r in real)
    inc302 = next(r for r in rows if r["incidents"] == ["INC-302"])
    assert inc302["truly_caused"] == inc302["predicted_clear"] == ["A-9101", "A-9102"]
    assert {t for t in list_tenants() if t.startswith("proof_")} == copies


def test_the_results_routes(fresh):
    with TestClient(__import__("faultline.api", fromlist=["app"]).app) as c:
        d = c.get("/api/results").json()
        assert d["proof"]["tests"] > 0 and d["experiments"] == len(site.EXPERIMENTS)
        assert len(c.post("/api/results/proofs/run").json()) == 5
        assert "/static/js/results.js" in c.get("/results").text


def test_a_real_fix_that_predicts_nothing_does_not_hold(fresh, monkeypatch):
    from faultline import proof
    monkeypatch.setattr(proof, "preview_fix", lambda *a, **k: {"verdict": "clears nothing", "predicted_clear": [], "summary": ""})
    rows = site.prove_fixes(fresh)
    assert [r["holds"] for r in rows] == [False, False, False, False, True]    # only the decoy should predict nothing
