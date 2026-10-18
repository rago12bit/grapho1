# Scale benchmark

Output of `python -m faultline.scale --services N` on a laptop (Docker, 10 CPUs, 7.7 GB shared with everything else), FalkorDB v4.14.10. A synthetic company; see faultline/scale.py for how it is built.

## 1000 services

1,000 services · 71,005 nodes · 84,553 edges (2,792 service calls, 5,000 operations and 8,376 operation calls, 2,520 pods, 20,392 changes, 40,477 alerts, 196 incidents) · built in 0.4 s

| query | runs | p50 | p95 |
|---|---|---|---|
| dependencies, 5 hops (CALLS*1..5) | 20 | 0.4 ms | 0.5 ms |
| blast radius (who calls it) | 20 | 5.6 ms | 6.4 ms |
| changes and alerts near an incident | 20 | 2.7 ms | 2.8 ms |
| the judge (rank_suspects) | 20 | 26.5 ms | 39.3 ms |
| judge + skeptic + WCC groups | 20 | 29.4 ms | 31.6 ms |
| PageRank over CALLS | 5 | 5.6 ms | 5.6 ms |
| betweenness over CALLS | 5 | 94.8 ms | 94.9 ms |
| label propagation (failure domains) | 5 | 104.4 ms | 104.9 ms |
| SPpaths, 3 shortest (symptom to cause) | 20 | 0.2 ms | 0.2 ms |
| full-text search over changes and alerts | 20 | 6.2 ms | 6.3 ms |

Judge on the 196 planted incidents: the planted change first in 191 (97%), in the top three in 195.

## 3000 services

3,000 services · 210,849 nodes · 251,858 edges (8,479 service calls, 15,000 operations and 25,437 operation calls, 7,513 pods, 60,396 changes, 120,482 alerts, 198 incidents) · built in 1.3 s

| query | runs | p50 | p95 |
|---|---|---|---|
| dependencies, 5 hops (CALLS*1..5) | 20 | 0.4 ms | 0.5 ms |
| blast radius (who calls it) | 20 | 15.5 ms | 20.4 ms |
| changes and alerts near an incident | 20 | 7.4 ms | 7.6 ms |
| the judge (rank_suspects) | 20 | 69.0 ms | 70.6 ms |
| judge + skeptic + WCC groups | 20 | 75.2 ms | 76.5 ms |
| PageRank over CALLS | 5 | 15.4 ms | 15.5 ms |
| betweenness over CALLS | 5 | 272.1 ms | 273.0 ms |
| label propagation (failure domains) | 5 | 300.3 ms | 300.8 ms |
| SPpaths, 3 shortest (symptom to cause) | 20 | 0.2 ms | 0.2 ms |
| full-text search over changes and alerts | 20 | 6.6 ms | 7.6 ms |

Judge on the 198 planted incidents: the planted change first in 197 (99%), in the top three in 198.
