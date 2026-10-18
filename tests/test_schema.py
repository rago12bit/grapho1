"""docs/graph-model.md is generated from the graphs themselves; this regenerates it and fails if it has drifted."""
import re

from faultline import schema


def test_the_published_graph_model_is_what_the_seed_and_agents_build():
    want = schema.DOC.read_text().rstrip("\n")
    got = schema.build().rstrip("\n")
    assert got == want, "docs/graph-model.md is stale: run python -m faultline.schema (see its docstring)"


def _table(section: str) -> list[list[str]]:
    import re
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    body = text.split(f"## {section}\n", 1)[1].split("\n## ", 1)[0]
    return [[c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))] for line in body.splitlines()
            if line.startswith("| ") and not line.startswith("|---")][1:]


def _func(ref: str):
    import importlib
    mod, name = ref.strip("`").split(".")
    return getattr(importlib.import_module(f"faultline.{mod}"), name)


def test_the_query_catalogue_is_what_the_code_runs():
    import inspect
    import re
    rows = _table("Query catalogue")
    assert len(rows) >= 12
    for question, cypher, where in rows:
        src = re.sub(r"\s+", " ", inspect.getsource(_func(where)))
        for token in re.findall(r"algo\.\w+|db\.idx\.\w+\.queryNodes|CAUSED_BY\*1\.\.12|CALLS\*1\.\.|relDirection: 'incoming'|GRAPH\.COPY", cypher):
            assert token in src or token == "GRAPH.COPY" and "GRAPH.COPY" in inspect.getsource(inspect.getmodule(_func(where))), (question, token)


def test_the_feature_map_points_at_real_code():
    for feature, _, where in _table("FalkorDB feature map"):
        for ref in re.findall(r"`((?:tools|reasoning|db)\.(?!py`)\w+)`", where):
            assert callable(_func(ref)), (feature, ref)


def test_the_charts_are_drawn_from_the_current_results():
    from faultline import charts
    for name, svg in charts.build().items():
        assert (charts.OUT / name).read_text() == svg, f"docs/media/charts/{name} is stale: run python -m faultline.charts"


def test_every_readme_link_and_image_resolves():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (root / "README.md").read_text()
    slug = lambda h: re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")  # noqa: E731
    heads = {slug(h) for h in re.findall(r"^#{1,4} (.+)$", text, re.M)}
    links = {a or b for a, b in re.findall(r"\]\(#([^)]+)\)|href=\"#([^\"]+)\"", text)}
    assert links and not links - heads, sorted(links - heads)
    files = set(re.findall(r"\]\(((?:docs|demo|scripts|faultline|web|deploy)/[^)#\s]+)\)", text))
    files |= set(re.findall(r"(?:src|srcset)=\"((?:docs|web)/[^\"]+)\"", text))
    missing = sorted(f for f in files if not (root / f).exists())
    assert files and not missing, missing
