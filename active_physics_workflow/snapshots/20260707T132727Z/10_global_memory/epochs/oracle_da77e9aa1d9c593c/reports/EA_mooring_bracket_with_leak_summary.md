# Mooring EA Bracket With Leak Guard

Oracle: `oracle_da77e9aa1d9c593c`

Figure: `10_global_memory\epochs\oracle_da77e9aa1d9c593c\reports\figures\EA1003_EA10045R_EA1005_EA10055_mooring_bracket_sixdof_metrics.png`

## Global result

| Run | Candidate | Global score | Step decision | Loop verdict | Hard violations |
|---|---|---:|---|---|---:|
| EA1003 | `GLV3_MOOR_EA1003_20260707T043810Z` | 0.715320 | REJECTED | RUNNING | 0 |
| EA10045R | `GLV3_MOOR_EA10045R_20260707T090815Z` | 0.977567 | REJECTED | DIAGNOSTIC_FORK | 1 |
| EA1005 best | `GLV3_MOOR_EA1005_20260707T033530Z` | 0.679273 | ACCEPTED | RUNNING | 0 |
| EA10055 | `GLV3_MOOR_EA10055_20260707T060656Z` | 0.915552 | REJECTED | DIAGNOSTIC_FORK | 4 |

## Period error by DOF (%)

| DOF | EA1003 | EA10045R | EA1005 current best | EA10055 |
|---|---:|---:|---:|---:|
| Surge | 2.171 | 0.618 | 1.204 | 9.042 |
| Sway | 1.288 | 2.983 | 0.259 | 3.826 |
| Heave | 0.588 | 0.575 | 0.747 | 0.588 |
| Roll | 1.726 | 1.896 | 1.778 | 1.792 |
| Pitch | 0.044 | 0.597 | 0.307 | 0.257 |
| Yaw | 0.541 | 1.600 | 2.292 | 1.280 |

## Hard violations

| Run | Violations |
|---|---|
| EA1003 | none |
| EA10045R | strong_leak:FD_SWAY->Yaw:0.524>ref0.117 |
| EA1005 best | none |
| EA10055 | period_reverse_worse:FD_SURGE:abs 1.2->9.04 signed 1.2->9.04<br>period_reverse_worse:FD_SWAY:abs 0.259->3.83 signed 0.259->3.83<br>strong_leak:FD_SWAY->Yaw:0.506>ref0.117<br>strong_leak:FD_YAW->Surge:0.587>ref0.167 |

## Interpretation

- `EA1005` remains the current best accepted candidate with score `0.679273`.
- `EA10045R` is not accepted: its periods are mostly acceptable, but it creates strong `FD_SWAY->Yaw` leak and scores `0.977567`.
- `EA10055` is worse: it triggers period reverse on Surge/Sway and strong leak.
- `EA1003` is clean but does not improve the global score.
- The mooring EA search is now well bracketed; further progress likely needs either leak-targeted mechanism diagnosis or a different parameter group, not more broad EA probing.
