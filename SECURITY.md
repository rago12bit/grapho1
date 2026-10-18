# Security

## Reporting a vulnerability

Please report it privately through GitHub: the **Security** tab of this repository → **Report a vulnerability**.
Don't open a public issue for it. (Maintainers: switch on private vulnerability reporting under Settings → Code security.) You'll get an answer within a few days; a fix goes to `main` with a note in
[CHANGELOG.md](CHANGELOG.md).

Supported: `main` and the latest tag. This is a hackathon project run locally or as a single-server demo; there
is no hosted service holding anyone's data.

## What the design already guards

Each item below is enforced in code, and most are among the 28 breakages `make mutants` checks a test catches
([docs/mutants.txt](docs/mutants.txt)).

| Risk | Guard | Where |
|---|---|---|
| An agent changes production | Agents and MCP clients can only **propose** a fix. There is no approve tool; a person approves in the UI, and the fix is applied to a simulated system | `tools.propose_action`, `mcp_server.py` |
| A query writes through the read path | Every read goes through `GRAPH.RO_QUERY`, so FalkorDB itself refuses writes; the free-form Cypher tool also rejects write keywords and runs with a timeout | `db.Graph.read`, `db.assert_read_only` |
| One company reads another's data | One FalkorDB graph per tenant; tenant ids are validated (`[a-z][a-z0-9_]{0,31}`), so a name like `../kestrel` is refused | `db.graph_name` |
| Strangers feed fake monitoring data | With `FAULTLINE_PUBLIC=1` the alert and change webhooks need `X-Hypocenter-Token` (compared in constant time) | `api.public_guard` |
| Abuse of the routes that do work | Per-client rate limits on investigate, ask, upload, experiments, reset and the rest in public mode | `api.LIMITS` |
| The public MCP endpoint acts | In public mode the HTTP MCP endpoint is read-only: `propose_action` isn't registered | `mcp_server.py` |
| The judge cheats | The simulator that knows the true causes (`world.py`) is never imported by the judge or the agents; a test checks the imports | `tests/test_case_file.py` |
| A sandbox leaks | Fix proofs, Break-it experiments and the load test run on `GRAPH.COPY`s deleted in a `finally` | `proof.py`, `site.py`, `bench.py` |
| A model's claim is trusted | Every id, service and time in a verdict is looked up in the graph; claims it can't back are dropped from the evidence | `tools.verify_claims` |
| Uploaded files | Incident uploads are validated JSON or an RCAEval-layout zip, size-limited and rate-limited, and become a separate tenant | `upload.py` |

## Data

- Only public, synthetic or project-written data is loaded (rule 6 of the hackathon).
- Secret values in the projects' Kubernetes manifests are never kept: only the key names.
- The public post-mortem list has no licence, so it is fetched onto your machine and never committed; a test
  checks the fixtures stay clean.
- `ANTHROPIC_API_KEY` is read from the environment and never written anywhere; `make live-run` uses scratch
  graphs it deletes afterwards.

## Running the public demo

See [deploy/DEPLOY.md](deploy/DEPLOY.md): Caddy for HTTPS, FalkorDB not exposed, `FAULTLINE_PUBLIC=1`, and a
random `FAULTLINE_INGEST_TOKEN` for the webhooks.
