# Calibration Harness

Generated: 2026-07-07T13:27:06+00:00

## Summary

- Oracle: `oracle_da77e9aa1d9c593c`
- Window status: `PRODUCTIVE`
- Supervisor recommendation: `STOP_AUTO_MATERIALIZE`
- Stop auto materialize: `True`
- Reason: proposal pool is diagnostic-only while active status is PRODUCTIVE; require diagnostic review before materialization

## Active Attempts

- Active attempts in window: 13
- Valid active attempts: 13
- Window limit: 20
- Accepted: 1
- Rejected: 7
- Evidence only: 5
- Diagnostic: 6
- Plateau count: 4
- Mean information gain: 0.873

## Proposal State

- Proposal status: `DIAGNOSTIC_ONLY`
- Proposal count: 6
- Top proposal: `LP_330171BDD5AE`
- Materializable proposals: ``
- Reason: 1 similar proposal(s) were already rejected by full 6DOF validation

## Repeated Failure Signatures

| Signature | Count |
|---|---:|
| none | 0 |

## Campaigns

| Campaign | Status | Confidence | Attempts | Support | Refute | Hypothesis |
|---|---|---:|---:|---:|---:|---|
| `CAMP_DIAGONAL_HYDRO_FD_ROLL_SIGNED_IC_BQUAD_FP_DFF6A6F7` | `ACTIVE` | 0.750 | 5 | 0 | 0 | inferred:diagonal_hydro/FD_ROLL/signed_ic_bquad/fp from HEAVE095_ROLL090_CMP_0612144710 |
| `CAMP_MOORING_GLOBAL_MOORING_EA_PROBE_FP_C3A1E4CA` | `AMBIGUOUS` | 0.800 | 4 | 1 | 3 | Small MoorDyn EA increase may reduce long FD_SWAY period without exceeding Surge/Yaw period guards. |
| `CAMP_DIAGONAL_HYDRO_FD_HEAVE_HYDRODYN_EDIT_FP_2C7D4CA5` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_HEAVE/hydrodyn_edit/fp from LP_1091C22C41B7_HEAVE_20260706T135310Z0000 |
| `CAMP_DIAGONAL_HYDRO_FD_SWAY_HYDRODYN_EDIT_FP_43879956` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_SWAY/hydrodyn_edit/fp from LP_2BFEA3960C79_SWAY_20260707T011700Z0000 |
| `CAMP_DIAGONAL_HYDRO_FD_YAW_HYDRODYN_EDIT_LER_26804F02` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_YAW/hydrodyn_edit/ler from LP_9E877C00D37E_YAW_20260707T020651Z0000 |
| `CAMP_MOORING_FD_HEAVE_HEAVE_ADDF0_PROBE_FP_0D3D131D` | `ACTIVE` | 0.200 | 1 | 0 | 1 | Small Heave AddF0=-0.25e6 on accepted EA1005 may reduce FD_HEAVE fp/envelope without crossing Heave->Roll leak guard. |
