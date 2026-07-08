# EA1005 vs Yaw BQuad +0.5% Early-Stop Summary

- Current best: `GLV3_MOOR_EA1005_20260707T033530Z`, score `0.6792725733966515`
- Candidate: `GLV3_YAW_BQ1005_EA1005_20260707T140240Z`, partial score `1.1973037933381994`
- RunCard: `RC_STEP_GLV3_YAW_BQ1005_EA1005_20260707T140240Z_20260707T141338Z0000`
- Decision: `EVIDENCE_ONLY`; loop verdict: `DIAGNOSTIC_FORK`
- Completed DOFs: `FD_SURGE`; skipped DOFs: `FD_SWAY`, `FD_HEAVE`, `FD_ROLL`, `FD_PITCH`, `FD_YAW`

## Hard Guard
- `missing_dof:FD_HEAVE`
- `missing_dof:FD_PITCH`
- `missing_dof:FD_ROLL`
- `missing_dof:FD_SWAY`
- `missing_dof:FD_YAW`
- `period_reverse_worse:FD_SURGE:abs 1.2->4.53 signed 1.2->4.53`

## Observed Surge Delta
| metric | current best | candidate | delta |
|---|---:|---:|---:|
| Score | 0.721802 | 1.1973 | +0.475501 |
| Period % | 1.2039 | 4.52963 | +3.32574 |
| Env | 0.405493 | 0.403784 | -0.00170876 |
| LER | 0.0550531 | 0.345058 | +0.290004 |
| fp | 0.3243 | 0.291099 | -0.0332019 |

Figure: `D:\OpenFast\FOCAL_C4_workflow_hub\01_active_physics_workflow\10_global_memory\epochs\oracle_c26754c99eb2607c\reports\figures\EA1005_vs_YAW_BQ1005_20260707_earlystop_sixdof_guard.png`
