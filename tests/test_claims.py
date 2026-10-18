"""Every number the README tags agrees with the file (or the graph) that measured it."""
from faultline import claims


def test_every_tagged_number_matches_its_source(fresh):
    rows = claims.check(live=True)
    assert rows and all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]


def test_a_drifted_number_or_an_unknown_key_is_caught():
    text = claims.README.read_text()
    key, said = claims.tags(text)[0]
    wrong = text.replace(f"<!-- claim:{key} -->{said}", f"<!-- claim:{key} -->9.99", 1)
    wrong += "\n<!-- claim:no.such.key -->1<!-- /claim -->\n"
    bad = {r["key"]: r["why"] for r in claims.check(text=wrong) if r["ok"] is False}
    assert bad == {key: "drifted", "no.such.key": "no such claim key"}
