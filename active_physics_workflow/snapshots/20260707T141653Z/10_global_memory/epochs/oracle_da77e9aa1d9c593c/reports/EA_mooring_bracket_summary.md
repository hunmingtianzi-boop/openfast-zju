# Mooring EA Bracket Summary

Oracle: `oracle_da77e9aa1d9c593c`

Figure: `10_global_memory\epochs\oracle_da77e9aa1d9c593c\reports\figures\EA1003_EA1005_EA10055_mooring_bracket_sixdof_metrics.png`

## Global result

| Run | Candidate | Global score | Step decision | Loop verdict | Hard violations |
|---|---|---:|---|---|---:|
| EA1003 rejected lower | `GLV3_MOOR_EA1003_20260707T043810Z` | 0.715320 | REJECTED | RUNNING | 0 |
| EA1005 current best | `GLV3_MOOR_EA1005_20260707T033530Z` | 0.679273 | ACCEPTED | RUNNING | 0 |
| EA10055 rejected upper | `GLV3_MOOR_EA10055_20260707T060656Z` | 0.915552 | REJECTED | DIAGNOSTIC_FORK | 4 |

## Period error by DOF (%)

| DOF | EA1003 | EA1005 current best | EA10055 |
|---|---:|---:|---:|
| Surge | 2.171 | 1.204 | 9.042 |
| Sway | 1.288 | 0.259 | 3.826 |
| Heave | 0.588 | 0.747 | 0.588 |
| Roll | 1.726 | 1.778 | 1.792 |
| Pitch | 0.044 | 0.307 | 0.257 |
| Yaw | 0.541 | 2.292 | 1.280 |

## Interpretation

- `EA1005` remains current best: score `0.679273`.
- `EA1003` is clean but worse globally: score `0.715320`, so it is evidence only.
- `EA10055` is rejected by hard guards: Surge period reverses from `1.20%` to `9.04%`, Sway period reverses from `0.259%` to `3.83%`, and strong leak appears in `FD_SWAY->Yaw` plus `FD_YAW->Surge`.
- The bracket says the productive mooring EA region is narrow around `1.005`; further search should use smaller perturbations or switch to the local/proposal queue instead of larger EA probes.
