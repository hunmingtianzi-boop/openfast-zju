# Evidence Workflow Report

Generated: 2026-06-17T16:24:28+00:00
Candidate: `AMP_SH_S_BASEYAW_06171610`
Overall: `blocked`

## Gates

### Data gate | pass=True
Project curated reference data gate.

### Model gate | pass=True
Project model/path audit gate.

### Mainline free-decay runner | pass=True
Skipped; using existing run registry.

### Run registry gate | pass=True
At least one project run exists for the requested scope.

### Shadow scout gate | pass=True
Non-authoritative project shadow scouting completed.

### Objective review gate | pass=False
unchanged: FD_YAW

## Candidate Run Summary

- `FD_HEAVE`: SUCCESS (RUN_0001_FD_HEAVE_AMP_SH_S_BASEYAW_06171610)
- `FD_PITCH`: SUCCESS (RUN_0001_FD_PITCH_AMP_SH_S_BASEYAW_06171610)
- `FD_ROLL`: SUCCESS (RUN_0001_FD_ROLL_AMP_SH_S_BASEYAW_06171610)
- `FD_SURGE`: SUCCESS (RUN_0001_FD_SURGE_AMP_SH_S_BASEYAW_06171610)
- `FD_SWAY`: SUCCESS (RUN_0001_FD_SWAY_AMP_SH_S_BASEYAW_06171610)
- `FD_YAW`: SUCCESS (RUN_0001_FD_YAW_AMP_SH_S_BASEYAW_06171610)

## Shadow Alarms

- `AMP_SH_S_BASEYAW_06171610` `FD_SURGE`: PtfmSurge is low-frequency/drift dominated (low_freq_fraction=0.8054328248489152, peak_period_s=75.00416666667304, drift_ratio=0.161).
  Next: Use the physics-agent mooring path and inspect equilibrium drift plus tension channels if available.
- `AMP_SH_S_BASEYAW_06171610` `FD_YAW`: PtfmYaw is low-frequency/drift dominated (low_freq_fraction=0.6636121075041076, peak_period_s=50.003571428582795, drift_ratio=0.053).
  Next: Use the physics-agent mooring path and inspect equilibrium drift plus tension channels if available.

## Follow-Up Actions

- `FOLLOWUP-FD_SURGE-MOORING`: Use physics-agent MoorDyn EA/UnstrLen path and inspect equilibrium drift/tension before any AddCLin path.
- `FOLLOWUP-FD_YAW-MOORING`: Use physics-agent MoorDyn EA/UnstrLen path and inspect equilibrium drift/tension before any AddCLin path.

## Objective Review

- Decision: `HOLD_OBJECTIVE_NOT_IMPROVED`
- Target DOFs: `FD_YAW`
- Reason: unchanged: FD_YAW

## Boundary

Shadow scouting is non-authoritative. Final candidate recommendation remains controlled by the project candidate selector, metrics gates, consistency checks, and manual package decision.
