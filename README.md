# OpenFAST ZJU FOCAL C4 Calibration Snapshot

This repository contains a GitHub-sized snapshot of the current FOCAL C4 physics workflow results.

The active local workflow was developed at:

`D:\OpenFast\FOCAL_C4_physics_workflow`

Only reproducible workflow assets and analysis outputs are included here. Large OpenFAST run directories, executable binaries, and full model packages are intentionally excluded because the local workflow currently exceeds 25 GB and contains files that are not suitable for normal GitHub storage.

## Included

- `FOCAL_C4_physics_workflow/config.yaml`
- `FOCAL_C4_physics_workflow/00_reference/`
  - curated processed six-DOF reference data
- `FOCAL_C4_physics_workflow/03_scripts/`
  - gates, candidate registry, run executor, metric evaluator, plotting, and physics-agent scripts
- `FOCAL_C4_physics_workflow/05_registry/`
  - candidate, run, results, decision, and summary registries
- `FOCAL_C4_physics_workflow/06_figures/`
  - six-DOF free-decay overlays and diagnostics
- `FOCAL_C4_physics_workflow/07_reports/`
  - generated workflow reports
- `FOCAL_C4_physics_workflow/08_logs/`
  - workflow logs
- `FOCAL_C4_physics_workflow/09_fixtures/`
  - regression fixtures

## Excluded

The following local directories were not uploaded:

- `02_starting_model/`
- `04_current_runs/`
- `99_loop_packages/`
- OpenFAST executables and binary simulation outputs
- large raw or intermediate `.out`, `.outb`, `.exe`, `.dll`, `.zip`, and archive files

These are excluded to avoid committing multi-GB data and GitHub-incompatible large files.

## Current Calibration Notes

Current formal baseline candidate:

`AMP_SH_HAF15_Y15052_R8715_07041136`

Current best diagnostic combined candidate:

`COMBO_FORMALBASE_SURGE_BQ080_HEAVE_BQ095_NOPTFMMASS_DOFIC_T600_0706`

This combined candidate improves Surge, Heave, and Pitch metrics relative to the formal baseline, but it is not yet a final recommended full six-DOF package because Sway/Yaw LER regressions remain.

Key figure:

`FOCAL_C4_physics_workflow/06_figures/evidence_workflow/COMBO_FORMALBASE_SURGE_BQ080_HEAVE_BQ095_NOPTFMMASS_DOFIC_T600_0706_sixdof_release_normalized_overlay.png`

The latest isolation test completed:

`FORMALBASE_SURGEONLY_BQ080_DOFIC_T600_0706`

It shows that the Surge-only edit can hurt Sway period and Yaw LER, so it should remain diagnostic until the cross-DOF regression is resolved.

## Metric Snapshot

| Candidate | DOF | Period err % | NRMSE | Envelope | LER | Status |
|---|---:|---:|---:|---:|---:|---|
| formal baseline | Surge | 1.8489757 | 0.030248578 | 0.32766471 | 0.63369893 | PASS |
| combined diagnostic | Surge | 1.463389 | 0.026892476 | 0.27711431 | 0.056242269 | PASS |
| formal baseline | Heave | 0.81703053 | 0.023015447 | 0.35974372 | 0.2257355 | PASS |
| combined diagnostic | Heave | 0.83556537 | 0.021026889 | 0.33072316 | 0.11174908 | PASS |
| formal baseline | Pitch | 0.44945173 | 0.014083946 | 0.13842681 | 0.85497007 | PASS |
| combined diagnostic | Pitch | 0.19137596 | 0.013301788 | 0.1312298 | 0.53925882 | PASS |
| formal baseline | Yaw | 0.52281173 | 0.02290328 | 0.26130353 | 0.93314977 | PASS |
| combined diagnostic | Yaw | 0.70708443 | 0.026098279 | 0.24289752 | 3.508498 | PASS |

