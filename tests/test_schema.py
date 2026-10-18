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
