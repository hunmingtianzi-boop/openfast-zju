# Objective Review Report

Generated: 2026-07-06T06:30:30+00:00

- Candidate: SURGE_AF0_N190K_IC178_T600_0706
- Baseline: AMP_SH_HAF15_Y15052_R8715_07041136
- Candidate parent: AMP_SH_HAF15_Y15052_R8715_07041136
- Lineage status: same_parent
- Target DOFs: FD_SURGE
- Decision: HOLD_OBJECTIVE_NOT_IMPROVED
- Reason: regressed/mixed: FD_SURGE

## Target Comparisons

### FD_SURGE: REGRESSED
- `envelope_error`: baseline=0.32766471, candidate=0.32111639, delta=-0.00654832
- `fp_error`: baseline=0.13034279, candidate=0.076445965, delta=-0.053896825
- `ler_error`: baseline=0.63369893, candidate=0.004736511, delta=-0.62896242
- `period_error_pct`: baseline=1.8489757, candidate=5.7179264, delta=+3.8689507
- `zeta_abs_error`: baseline=0.005165912, candidate=0.003408994, delta=-0.001756918

## Routing

- Do not package as final recommendation from this branch.
- Loop back through physics-agent/free-decay diagnosis for the target DOF.
- For `FD_SURGE` or `FD_YAW`, prefer MoorDyn equilibrium/tension and low-frequency drift review before AddCLin forcing.
