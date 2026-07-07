# EA1005 vs EA1003 6DOF comparison

- Current best: `GLV3_MOOR_EA1005_20260707T033530Z` score `0.679273`
- Tested bracket: `GLV3_MOOR_EA1003_20260707T043810Z` score `0.715320`
- Step decision: `REJECTED` - global score did not improve 0.6793 -> 0.7153
- Plot: `10_global_memory/epochs/oracle_da77e9aa1d9c593c/reports/figures/EA1005_vs_EA1003_sixdof_metrics.png`
- Delta heatmap: `10_global_memory/epochs/oracle_da77e9aa1d9c593c/reports/figures/EA1003_minus_EA1005_metric_delta_heatmap.png`

| DOF | score old | score new | period old | period new | env old | env new | LER old | LER new | fp old | fp new |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FD_SURGE | 0.722 | 0.920 | 1.204 | 2.171 | 0.405 | 0.403 | 0.055 | 0.288 | 0.324 | 0.322 |
| FD_SWAY | 0.560 | 0.698 | 0.259 | 1.288 | 0.325 | 0.358 | 0.403 | 0.444 | 0.196 | 0.172 |
| FD_HEAVE | 1.098 | 1.098 | 0.747 | 0.588 | 0.498 | 0.498 | 0.318 | 0.365 | 0.596 | 0.596 |
| FD_ROLL | 0.604 | 0.670 | 1.778 | 1.726 | 0.184 | 0.183 | 0.121 | 0.297 | 0.246 | 0.244 |
| FD_PITCH | 0.450 | 0.420 | 0.307 | 0.044 | 0.131 | 0.132 | 0.470 | 0.456 | 0.156 | 0.164 |
| FD_YAW | 0.642 | 0.487 | 2.292 | 0.541 | 0.257 | 0.259 | 0.126 | 0.136 | 0.185 | 0.220 |

## Interpretation

- EA1003 improves Yaw period margin, but loses global score because Surge/Sway/Roll LER and Sway period/env regress relative to EA1005.
- The rejected run is still useful: it brackets the mooring EA mechanism and suggests EA1005 is closer to the productive region than EA1003.
- Runner efficiency issue observed: serial six-DOF execution makes one candidate dominated by long Surge/Sway/Yaw wall time; parallel DOF execution would improve throughput without changing verdict authority.
