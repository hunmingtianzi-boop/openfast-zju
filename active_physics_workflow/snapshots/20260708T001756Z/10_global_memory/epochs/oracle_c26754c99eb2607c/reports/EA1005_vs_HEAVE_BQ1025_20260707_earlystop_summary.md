# Heave BQuad +2.5% Early-Stop Summary

- Current best: `GLV3_MOOR_EA1005_20260707T033530Z` score `0.6792725733966515`
- Candidate: `LP_330171BDD5AE_HEAVE_20260707T144315Z0000` partial score `2.0823245154723455`
- Step decision: `EVIDENCE_ONLY`
- Loop verdict: `DIAGNOSTIC_FORK`
- Hard violations: `missing_dof:FD_HEAVE; missing_dof:FD_PITCH; missing_dof:FD_ROLL; missing_dof:FD_YAW; period_reverse_worse:FD_SWAY:abs 0.259->6.6 signed 0.259->6.6`

| DOF | Best score | Candidate score | Score delta | Period % best -> cand | LER best -> cand | Status |
|---|---:|---:|---:|---|---|---|
| FD_SURGE | 0.7218 | 2.8878 | +2.1660 | 1.2 -> 1.54 | 0.0551 -> 5.52 | observed |
| FD_SWAY | 0.5600 | 1.2768 | +0.7169 | 0.259 -> 6.6 | 0.403 -> 0.281 | observed |
| FD_HEAVE | 1.0977 |  |  |  |  | skipped by early-stop |
| FD_ROLL | 0.6042 |  |  |  |  | skipped by early-stop |
| FD_PITCH | 0.4504 |  |  |  |  | skipped by early-stop |
| FD_YAW | 0.6416 |  |  |  |  | skipped by early-stop |

- Figure: `10_global_memory/epochs/oracle_c26754c99eb2607c/reports/figures/EA1005_vs_HEAVE_BQ1025_20260707_earlystop_sixdof_guard.png`
