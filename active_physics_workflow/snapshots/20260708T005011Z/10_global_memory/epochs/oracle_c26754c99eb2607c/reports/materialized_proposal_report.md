# Materialized Local Proposal

Generated: 2026-07-07T14:43:17+00:00

- Proposal: `LP_330171BDD5AE`
- Candidate: `LP_330171BDD5AE_HEAVE_20260707T144315Z0000`
- Parent candidate: `GLV3_MOOR_EA1005_20260707T033530Z`
- Target DOF: `FD_HEAVE`
- Action: `fp_bquad_probe`
- Parameter group: `diagonal_hydro`
- Intent: FD_HEAVE dominant fp error may improve via fp_bquad_probe
- Expected observation: FD_HEAVE diagonal BQuad x 1.02; global-step must confirm full 6DOF predicates and score
- Falsification condition: FD_HEAVE fp component does not improve, or any hard predicate/new protected-DOF regression appears in full 6DOF validation
- Memory policy: `evidence_only_no_veto`
- Harness warnings: `none`
- OpenFAST was not run by materialization.
- Next: `python 03_scripts/20_global_calibration_loop.py --global-step --candidate LP_330171BDD5AE_HEAVE_20260707T144315Z0000 --run-live`
