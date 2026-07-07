# Surge/Yaw Gate Repair Final Update

- Previous candidate: `AMP_SH_06121500`
- New candidate: `AMP_SH_SY_06172058`
- Figure: `D:\OpenFast\FOCAL_C4_physics_workflow\06_figures\surge_yaw_gate_repair_final_update.png`

## Summary

### AMP_SH_06121500
- decision: HOLD
- reason: start amplitude gate failed: FD_SURGE,FD_YAW
- j_total: 0.20408834
- j_shape_total: 3.6241239
- start_amplitude_error_total: 1.0949335
- score_total: 0.47782172

### AMP_SH_SY_06172058
- decision: PACKAGE_READY
- reason: six DOF period and start amplitude gates passed
- j_total: 0.18551563
- j_shape_total: 3.2695357
- start_amplitude_error_total: 0.91841037
- score_total: 0.41511823

## DOF deltas

- FD_SURGE: start 0.335 -> 0.175; period 1.293% -> 1.849%; nrmse 0.0507 -> 0.0302
- FD_SWAY: start 0.008 -> 0.008; period 2.632% -> 2.632%; nrmse 0.0472 -> 0.0472
- FD_HEAVE: start 0.190 -> 0.190; period 0.717% -> 0.717%; nrmse 0.0238 -> 0.0238
- FD_ROLL: start 0.192 -> 0.192; period 1.917% -> 1.917%; nrmse 0.0458 -> 0.0458
- FD_PITCH: start 0.162 -> 0.162; period 0.449% -> 0.449%; nrmse 0.0141 -> 0.0141
- FD_YAW: start 0.207 -> 0.190; period 0.141% -> 1.140%; nrmse 0.0225 -> 0.0244
