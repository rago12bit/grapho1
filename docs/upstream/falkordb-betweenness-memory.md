# Draft issue for FalkorDB/FalkorDB (not posted yet)

**Title:** `algo.betweenness` memory grows with every node in the graph, not just the `nodeLabels` subgraph (OOM on v4.14.10)

**Version:** `falkordb/falkordb:v4.14.10` (Docker). Not checked on newer releases.

**What happens**

`algo.betweenness` restricted with `nodeLabels: ['Service']` to 3,000 nodes needs memory that grows with the
*total* number of nodes in the graph times `samplingSize`, even when the other nodes have no relationships at all.
With 150,000 unrelated nodes in the same graph and `samplingSize: 3000` (exact betweenness over the 3,000), the
server is OOM-killed under a 6 GB limit.

We hit it in a scale benchmark: 3,000 `Service` nodes with 8,479 `CALLS` relationships inside a 211,000-node
graph (operations, pods, changes, alerts). Exact betweenness over the services ran FalkorDB out of memory.

**Reproduce** (`scripts/betweenness-memory-repro.sh <unrelated_nodes> <samplingSize>`): a fresh container with
`-m 6g`, 3,000 `:Service` nodes, 8,500 random `:CALLS` between them, N unrelated `:Filler` nodes, then

```cypher
CALL algo.betweenness({nodeLabels: ['Service'], relationshipTypes: ['CALLS'], samplingSize: $k, samplingSeed: 1})
YIELD node, score RETURN count(node)
```

| unrelated nodes | samplingSize | used_memory_peak | result |
|---|---|---|---|
| 0 | 1,000 | 103 MB | 3,000 rows, 0.2 s |
| 0 | 3,000 | 334 MB | 3,000 rows, 0.6 s |
| 50,000 | 500 | 493 MB | 3,000 rows, 0.2 s |
| 50,000 | 1,000 | 972 MB | 3,000 rows, 0.4 s |
| 50,000 | 3,000 | 2,800 MB | "Query timed out" |
| 150,000 | 3,000 | – | container OOM-killed |

Peak memory is roughly (all nodes in the graph) × samplingSize × ~19 bytes, so the working matrices look sized by
the graph's node-id range rather than by the nodes the `nodeLabels` filter selects.

**Expected**

Memory proportional to the selected subgraph (here 3,000 nodes), so exact betweenness over a small labelled
subgraph works inside a large graph.

**Workaround we use**

Sample: `samplingSize: 500, samplingSeed: 1` above 500 nodes (approximate, but repeatable). Copying the subgraph
into a scratch graph first would also avoid it.
