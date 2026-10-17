import os

# Tests build their own graphs so they never touch the demo data the UI is showing.
os.environ.setdefault("FAULTLINE_GRAPH_PREFIX", "faultlinetest_")
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

import pytest  # noqa: E402

from faultline import seed  # noqa: E402
from faultline.db import Graph  # noqa: E402


@pytest.fixture()
def fresh():
    """Rebuild the two synthetic tenant graphs from the seed before the test (Boutique tests build their own)."""
    seed.build("kestrel", force=True)
    seed.build("harbor", force=True)
    return Graph("kestrel")
