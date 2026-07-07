# Surge/Sway/Heave Refinement Update

- Baseline candidate: `AMP_SH_SY_R965_06171637`
- Accepted candidate: `AMP_SH_SY_R965_H058_07031325`
- Figure: `D:\OpenFast\FOCAL_C4_physics_workflow\06_figures\surge_sway_heave_refinement_update.png`

## Accepted Change

- Accepted: `FD_HEAVE` reference-signed IC scale `0.58`.
- Rejected: Sway BQuad 1.03/1.05/1.10/1.20 due to period or LER/NRMSE regression.
- Rejected: Surge BQuad 0.8/0.9/1.1/1.2 due to period or LER regression.

## Candidate Summary

### AMP_SH_SY_R965_06171637
- decision: PACKAGE_READY
- reason: six DOF period and start amplitude gates passed
- j_total: 0.18455768
- j_shape_total: 3.2763783
- start_amplitude_error_total: 0.91907928
- score_total: 0.4143275

### AMP_SH_SY_R965_H058_07031325
- decision: PACKAGE_READY
- reason: six DOF period and start amplitude gates passed
- j_total: 0.18426412
- j_shape_total: 3.2765803
- start_amplitude_error_total: 0.89904718
- score_total: 0.40902591

## DOF Metrics

- FD_SURGE: period 1.849% -> 1.849%; start 0.175 -> 0.175; nrmse 0.03025 -> 0.03025; env 0.328 -> 0.328; LER 0.634 -> 0.634; fp 0.130 -> 0.130
- FD_SWAY: period 2.632% -> 2.632%; start 0.008 -> 0.008; nrmse 0.04722 -> 0.04722; env 0.345 -> 0.345; LER 0.121 -> 0.121; fp 0.033 -> 0.033
- FD_HEAVE: period 0.717% -> 0.734%; start 0.190 -> 0.170; nrmse 0.02381 -> 0.02351; env 0.375 -> 0.371; LER 0.068 -> 0.107; fp 0.228 -> 0.207
