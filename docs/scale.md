# Scale benchmark

Output of `python -m faultline.scale --services N` on a laptop (Docker, 10 CPUs, 7.7 GB shared with everything else), FalkorDB v4.14.10. A synthetic company; see faultline/scale.py for how it is built.

## 1000 services

1,000 services · 71,005 nodes · 84,553 edges (2,792 service calls, 5,000 operations and 8,376 operation calls, 2,520 pods, 20,392 changes, 40,477 alerts, 196 incidents) · built in 0.5 s

| query | runs | p50 | p95 |
|---|---|---|---|
| dependencies, 5 hops (algo.BFS) | 20 | 0.7 ms | 1.0 ms |
| blast radius (algo.SSpaths, incoming) | 20 | 5.9 ms | 6.6 ms |
| changes and alerts near an incident | 20 | 2.7 ms | 2.8 ms |
| the judge (rank_suspects) | 20 | 28.5 ms | 42.2 ms |
| judge + skeptic + WCC groups | 20 | 31.3 ms | 33.7 ms |
| PageRank over CALLS | 5 | 5.6 ms | 5.7 ms |
| betweenness over CALLS | 5 | 106.5 ms | 110.0 ms |
| label propagation (failure domains) | 5 | 105.3 ms | 106.6 ms |
| SPpaths, 3 shortest (symptom to cause) | 20 | 0.1 ms | 0.2 ms |
| full-text search over changes and alerts | 20 | 6.2 ms | 6.5 ms |

Judge on the 196 planted incidents: the planted change first in 191 (97%), in the top three in 195.

## 3000 services

3,000 services · 210,849 nodes · 251,858 edges (8,479 service calls, 15,000 operations and 25,437 operation calls, 7,513 pods, 60,396 changes, 120,482 alerts, 198 incidents) · built in 1.4 s

| query | runs | p50 | p95 |
|---|---|---|---|
| dependencies, 5 hops (algo.BFS) | 20 | 1.0 ms | 2.4 ms |
| blast radius (algo.SSpaths, incoming) | 20 | 16.1 ms | 22.1 ms |
| changes and alerts near an incident | 20 | 7.4 ms | 7.7 ms |
| the judge (rank_suspects) | 20 | 73.1 ms | 75.7 ms |
| judge + skeptic + WCC groups | 20 | 78.1 ms | 81.6 ms |
| PageRank over CALLS | 5 | 15.7 ms | 16.1 ms |
| betweenness over CALLS | 5 | 393.2 ms | 477.8 ms |
| label propagation (failure domains) | 5 | 310.8 ms | 315.0 ms |
| SPpaths, 3 shortest (symptom to cause) | 20 | 0.2 ms | 0.3 ms |
| full-text search over changes and alerts | 20 | 6.6 ms | 9.0 ms |

Judge on the 198 planted incidents: the planted change first in 197 (99%), in the top three in 198.
