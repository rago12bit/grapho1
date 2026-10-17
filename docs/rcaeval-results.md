# RCAEval: Faultline against RCAEval's baselines

Output of `python -m faultline.compare` (AC@1 / Avg@5). Faultline v2; RE2 Train Ticket was held out until v2 was frozen. See the README, "Public data", for how each number was produced.

AC@1 / Avg@5 per dataset

| method | RE2-OB (90) | RE2-SS (90) | RE2-TT (90) | RE3-OB (30) | RE3-SS (30) | RE3-TT (30) | All 360 (360) |
|---|---|---|---|---|---|---|---|
| **Faultline (all)** | 0.77 / 0.92 | 0.83 / 0.88 | 0.72 / 0.78 | 0.97 / 0.99 | 0.33 / 0.62 | 0.83 / 0.94 | 0.76 / 0.86 |
| **Faultline (metrics)** | 0.84 / 0.92 | 0.84 / 0.89 | 0.70 / 0.75 | 0.97 / 0.99 | 0.67 / 0.72 | 0.83 / 0.94 | 0.80 / 0.86 |
| BARO | 0.14 / 0.74 | 0.07 / 0.76 | 0.67 / 0.80 | 0.00 / 0.77 | 0.00 / 0.74 | 0.77 / 0.95 | 0.28 / 0.78 |
| Biggest anomaly (metrics) | 0.02 / 0.70 | 0.00 / 0.74 | 0.27 / 0.48 | 0.03 / 0.75 | 0.00 / 0.53 | 0.03 / 0.47 | 0.08 / 0.62 |
| CIRCA | 0.60 / 0.82 | 0.79 / 0.92 | – | 0.83 / 0.96 | 0.67 / 0.89 | – | 0.71 / 0.89 (on 240) |
| Earliest alert (metrics) | 0.80 / 0.94 | 0.59 / 0.89 | 0.16 / 0.49 | 0.77 / 0.95 | 0.43 / 0.64 | 0.23 / 0.42 | 0.51 / 0.75 |
| NSigma | 0.79 / 0.91 | 0.86 / 0.94 | 0.76 / 0.83 | 0.83 / 0.89 | 0.57 / 0.83 | 0.80 / 0.96 | 0.78 / 0.90 |
| PC-RandomWalk | 0.03 / 0.39 | 0.18 / 0.49 | – | 0.10 / 0.49 | 0.10 / 0.49 | – | 0.10 / 0.45 (on 240) |

RE2 Train Ticket, averaged over fault types, against RCAEval's published Table 6

| method | AC@1 ours | AC@1 published | Avg@5 ours | Avg@5 published |
|---|---|---|---|---|
| Faultline (all) | 0.72 | – | 0.78 | – |
| Faultline (metrics) | 0.70 | – | 0.75 | – |
| BARO | 0.67 | 0.67 | 0.80 | 0.8 |
| Biggest anomaly (metrics) | 0.27 | – | 0.48 | – |
| Earliest alert (metrics) | 0.16 | – | 0.49 | – |
| NSigma | 0.76 | – | 0.83 | – |
| MicroRank (not run here) | – | 0.16 | – | 0.31 |
| TraceRCA (not run here) | – | 0.66 | – | 0.77 |
| CausalRCA (not run here) | – | 0.22 | – | 0.43 |
| MicroCause (not run here) | – | 0.1 | – | 0.2 |
| RCD (not run here) | – | 0.09 | – | 0.13 |
| Multi-source BARO (not run here) | – | 0.69 | – | 0.81 |
| Multi-source CIRCA (not run here) | – | 0.06 | – | 0.13 |
| PDiagnose (not run here) | – | 0.48 | – | 0.67 |
| Multi-source RCD (not run here) | – | 0.1 | – | 0.54 |
