# Calibration Harness

Generated: 2026-07-07T05:16:36+00:00

## Summary

- Oracle: `oracle_da77e9aa1d9c593c`
- Window status: `PRODUCTIVE`
- Supervisor recommendation: `CONTINUE_CURRENT_CAMPAIGN`
- Stop auto materialize: `False`
- Reason: recent active attempts are producing accepted or improving evidence

## Active Attempts

- Active attempts in window: 9
- Valid active attempts: 9
- Window limit: 20
- Accepted: 1
- Rejected: 3
- Evidence only: 5
- Diagnostic: 3
- Plateau count: 0
- Mean information gain: 0.789

## Proposal State

- Proposal status: `MATERIALIZABLE`
- Proposal count: 6
- Top proposal: `LP_330171BDD5AE`
- Materializable proposals: `LP_330171BDD5AE,LP_156E5BFB263B,LP_71D22C5024DD,LP_1A727791036F,LP_D460BA0F215D,LP_134489D9588E`
- Reason: top proposal can be materialized but must still pass global-step full 6DOF validation

## Repeated Failure Signatures

| Signature | Count |
|---|---:|
| none | 0 |

## Campaigns

| Campaign | Status | Confidence | Attempts | Support | Refute | Hypothesis |
|---|---|---:|---:|---:|---:|---|
| `CAMP_DIAGONAL_HYDRO_FD_ROLL_SIGNED_IC_BQUAD_FP_DFF6A6F7` | `ACTIVE` | 0.750 | 5 | 0 | 0 | inferred:diagonal_hydro/FD_ROLL/signed_ic_bquad/fp from HEAVE095_ROLL090_CMP_0612144710 |
| `CAMP_DIAGONAL_HYDRO_FD_HEAVE_HYDRODYN_EDIT_FP_2C7D4CA5` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_HEAVE/hydrodyn_edit/fp from LP_1091C22C41B7_HEAVE_20260706T135310Z0000 |
| `CAMP_DIAGONAL_HYDRO_FD_SWAY_HYDRODYN_EDIT_FP_43879956` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_SWAY/hydrodyn_edit/fp from LP_2BFEA3960C79_SWAY_20260707T011700Z0000 |
| `CAMP_DIAGONAL_HYDRO_FD_YAW_HYDRODYN_EDIT_LER_26804F02` | `ACTIVE` | 0.150 | 1 | 0 | 1 | inferred:diagonal_hydro/FD_YAW/hydrodyn_edit/ler from LP_9E877C00D37E_YAW_20260707T020651Z0000 |
| `CAMP_MOORING_GLOBAL_MOORING_EA_PROBE_FP_C3A1E4CA` | `SUPPORTED` | 0.200 | 1 | 1 | 0 | Small MoorDyn EA increase may reduce long FD_SWAY period without exceeding Surge/Yaw period guards. |
