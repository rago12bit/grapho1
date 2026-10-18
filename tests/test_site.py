"""The landing page at /, the incident room at /app, and the landing page's numbers kept equal to the eval output."""
import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
LANDING = (ROOT / "web" / "landing.html").read_text()
RESULTS = (ROOT / "docs" / "rcaeval-results.md").read_text()


def _client():
    return TestClient(__import__("faultline.api", fromlist=["app"]).app)


def _table(title_row: str) -> dict:
    """method -> list of cells, from the markdown table whose header starts with `title_row`."""
    lines = RESULTS[RESULTS.index(title_row):].splitlines()
    out = {}
    for ln in lines[2:]:
        if not ln.startswith("|"):
            break
        cells = [c.strip() for c in ln.strip("|").split("|")]
        out[cells[0].strip("*")] = cells[1:]
    return out


ALL = {m: tuple(float(x) for x in re.findall(r"\d\.\d+", c[-1])[:2]) for m, c in _table("| method | RE2-OB").items()}
TT = _table("| method | AC@1 ours")


def _tt(method: str) -> tuple[float, float]:
    ac1_ours, ac1_pub, avg_ours, avg_pub = TT[method]
    return float(ac1_ours if ac1_ours != "–" else ac1_pub), float(avg_ours if avg_ours != "–" else avg_pub)


PAGES = {"/": "landing.js", "/app": "main.js", "/oncall": "oncall.js", "/explore": "explore.js", "/break": "break.js", "/replay": "replay.js"}


def test_every_page_is_served_with_its_own_script_and_nothing_missing():
    with _client() as c:
        for path, script in PAGES.items():
            r = c.get(path)
            assert r.status_code == 200, path
            assert f"/static/js/{script}" in r.text, path
            for ref in re.findall(r'(?:href|src)="/static/([^"]+)"', r.text):
                assert (ROOT / "web" / ref).is_file(), (path, ref)
    for js in (ROOT / "web" / "js").glob("*.js"):              # every module a page imports exists
        for ref in re.findall(r'from "\./([^"]+)"|import\("\./([^"]+)"\)', js.read_text()):
            assert (ROOT / "web" / "js" / (ref[0] or ref[1])).is_file(), (js.name, ref)


def test_the_landing_bars_are_the_eval_numbers():
    bars = [json.loads(b.replace("&amp;", "&")) for b in re.findall(r"data-bars='([^']+)'", LANDING)]
    names = {"Faultline": "Faultline (metrics)", "NSigma": "NSigma", "CIRCA · 240 cases": "CIRCA",
             "Earliest alert": "Earliest alert (metrics)", "BARO": "BARO"}
    for name, v, *_ in bars[0]:
        assert v == ALL[names[name]][0], name
    tt = {"Faultline": "Faultline (metrics)", "NSigma": "NSigma", "BARO": "BARO", "TraceRCA · published": "TraceRCA (not run here)"}
    for name, ac1, _, avg5 in bars[1]:
        assert (ac1, avg5) == _tt(tt[name]), name


def test_the_headline_numbers_match_the_eval():
    ac1 = ALL["Faultline (metrics)"][0]
    assert f'data-count="{ac1:.2f}" data-dec="2">{ac1:.2f}</p>' in LANDING
    assert f"NSigma {ALL['NSigma'][0]:.2f}" in LANDING and f"BARO {ALL['BARO'][0]:.2f}" in LANDING
    from faultline.api import PROOF
    hit, n = (int(x) for x in PROOF["real"].split("/"))
    assert n == 360 and round(hit / n, 2) == ac1          # the header badge in the app says the same thing
