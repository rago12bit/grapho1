# RCAEval: Hypocenter against RCAEval's baselines

Output of `make eval` (`python -m faultline.compare`): AC@1 / Avg@5. See the README, "Public data", for how each number was produced.

AC@1 / Avg@5 per dataset

| method | RE2-OB (90) | RE2-SS (90) | RE2-TT (90) | RE3-OB (30) | RE3-SS (30) | RE3-TT (30) | All 360 (360) |
|---|---|---|---|---|---|---|---|
| **Hypocenter (all)** | 0.69 / 0.89 | 0.83 / 0.89 | 0.74 / 0.78 | 1.00 / 1.00 | 0.33 / 0.66 | 0.90 / 0.96 | 0.75 / 0.86 |
| **Hypocenter (metrics)** | 0.83 / 0.92 | 0.84 / 0.90 | 0.71 / 0.75 | 1.00 / 1.00 | 0.67 / 0.76 | 0.90 / 0.97 | 0.81 / 0.87 |
| BARO | 0.14 / 0.74 | 0.07 / 0.76 | 0.67 / 0.80 | 0.00 / 0.77 | 0.00 / 0.74 | 0.77 / 0.95 | 0.28 / 0.78 |
| Biggest anomaly (metrics) | 0.02 / 0.70 | 0.00 / 0.74 | 0.27 / 0.48 | 0.03 / 0.75 | 0.00 / 0.53 | 0.03 / 0.47 | 0.08 / 0.62 |
| CIRCA | 0.60 / 0.82 | 0.79 / 0.92 | – | 0.83 / 0.96 | 0.67 / 0.89 | – | 0.71 / 0.89 (on 240) |
| Earliest alert (metrics) | 0.80 / 0.94 | 0.59 / 0.89 | 0.16 / 0.49 | 0.77 / 0.95 | 0.43 / 0.64 | 0.23 / 0.42 | 0.51 / 0.75 |
| NSigma | 0.79 / 0.91 | 0.86 / 0.94 | 0.76 / 0.83 | 0.83 / 0.89 | 0.57 / 0.83 | 0.80 / 0.96 | 0.78 / 0.90 |
| PC-RandomWalk | 0.03 / 0.39 | 0.18 / 0.49 | – | 0.10 / 0.49 | 0.10 / 0.49 | – | 0.10 / 0.45 (on 240) |

RE2 Train Ticket, averaged over fault types, against RCAEval's published Table 6

| method | AC@1 ours | AC@1 published | Avg@5 ours | Avg@5 published |
|---|---|---|---|---|
| Hypocenter (all) | 0.74 | – | 0.78 | – |
| Hypocenter (metrics) | 0.71 | – | 0.75 | – |
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

AC@1 with 95% Wilson intervals

| set | method | right first | AC@1 | 95% interval |
|---|---|---|---|---|
| All 360 | Hypocenter (all) | 271 of 360 | 0.75 | 0.71–0.79 |
| All 360 | Hypocenter (metrics) | 292 of 360 | 0.81 | 0.77–0.85 |
| All 360 | BARO | 102 of 360 | 0.28 | 0.24–0.33 |
| All 360 | Biggest anomaly (metrics) | 28 of 360 | 0.08 | 0.05–0.11 |
| All 360 | Earliest alert (metrics) | 182 of 360 | 0.51 | 0.45–0.56 |
| All 360 | NSigma | 282 of 360 | 0.78 | 0.74–0.82 |
| RE2-TT | Hypocenter (all) | 67 of 90 | 0.74 | 0.65–0.82 |
| RE2-TT | Hypocenter (metrics) | 64 of 90 | 0.71 | 0.61–0.79 |
| RE2-TT | BARO | 60 of 90 | 0.67 | 0.56–0.76 |
| RE2-TT | Biggest anomaly (metrics) | 24 of 90 | 0.27 | 0.19–0.37 |
| RE2-TT | Earliest alert (metrics) | 14 of 90 | 0.16 | 0.09–0.24 |
| RE2-TT | NSigma | 68 of 90 | 0.76 | 0.66–0.83 |

Hypocenter (metrics) on all 360: right first at each confidence the skeptic gave

| confidence | cases | right first | wrong first | right, 95% interval |
|---|---|---|---|---|
| high | 12 | 12 | 0 | 0.76–1.00 |
| medium | 239 | 229 | 10 | 0.92–0.98 |
| low | 109 | 51 | 58 | 0.38–0.56 |
| high or medium | 251 | 241 | 10 | 0.93–0.98 |
