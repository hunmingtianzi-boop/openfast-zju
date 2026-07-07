# Calibration Harness

Generated: 2026-07-06T13:53:10+00:00

## Summary

- Oracle: `oracle_da77e9aa1d9c593c`
- Window status: `EXPLORATORY`
- Supervisor recommendation: `LIMITED_PROBE`
- Stop auto materialize: `False`
- Reason: active attempts are exploratory; keep probe count small until campaign support improves

## Active Attempts

- Active attempts in window: 5
- Valid active attempts: 5
- Window limit: 20
- Accepted: 0
- Rejected: 0
- Evidence only: 5
- Diagnostic: 0
- Plateau count: 5
- Mean information gain: 0.410

## Proposal State

- Proposal status: `MATERIALIZABLE`
- Proposal count: 6
- Top proposal: `LP_1091C22C41B7`
- Materializable proposals: `LP_1091C22C41B7,LP_2BFEA3960C79,LP_9E877C00D37E,LP_A299E8730E68,LP_445E3BB8A219,LP_F18C487648C7`
- Reason: top proposal can be materialized but must still pass global-step full 6DOF validation

## Repeated Failure Signatures

| Signature | Count |
|---|---:|
| none | 0 |

## Campaigns

| Campaign | Status | Confidence | Attempts | Support | Refute | Hypothesis |
|---|---|---:|---:|---:|---:|---|
| `CAMP_DIAGONAL_HYDRO_FD_HEAVE_UNKNOWN_FP_7F57418B` | `ACTIVE` | 0.450 | 3 | 0 | 0 | inferred:diagonal_hydro/FD_HEAVE/unknown/fp from HEAVE095_ROLL090_CMP_0612144710 |
| `CAMP_DIAGONAL_HYDRO_FD_ROLL_SIGNED_IC_BQUAD_FP_DFF6A6F7` | `ACTIVE` | 0.300 | 2 | 0 | 0 | inferred:diagonal_hydro/FD_ROLL/signed_ic_bquad/fp from HEAVE095_ROLL090_CMP_0612144710 |
