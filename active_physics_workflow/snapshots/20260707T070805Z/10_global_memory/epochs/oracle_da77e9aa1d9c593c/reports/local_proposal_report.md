# Global Loop Local Proposal Pool

Generated: 2026-07-07T05:16:36+00:00

- Oracle: `oracle_da77e9aa1d9c593c`
- Parent RunCard: `RC_STEP_GLV3_MOOR_EA1005_20260707T033530Z_20260707T042227Z0000`
- Parent candidate: `GLV3_MOOR_EA1005_20260707T033530Z`
- Open DOFs: `FD_SURGE,FD_SWAY,FD_HEAVE,FD_ROLL,FD_PITCH,FD_YAW`
- Protected DOFs: ``
- Fragile DOFs: `FD_HEAVE,FD_PITCH,FD_ROLL,FD_SURGE,FD_SWAY,FD_YAW`
- Memory policy: `evidence_only_no_veto`
- Local proposals rank and explain candidates; only full 6DOF global-step can accept or reject.

| Rank | Proposal | Target | Action | Ready | Priority | Status | Intent | Warnings |
|---:|---|---|---|---|---:|---|---|---|
| 1 | `LP_330171BDD5AE` | `FD_HEAVE` | `fp_bquad_probe` | `True` | 0.398 | `similar_outcome_rejected` | FD_HEAVE dominant fp error may improve via fp_bquad_probe | none |
| 2 | `LP_156E5BFB263B` | `FD_YAW` | `fp_bquad_probe` | `True` | -0.058 | `similar_outcome_rejected` | FD_YAW dominant fp error may improve via fp_bquad_probe | none |
| 3 | `LP_71D22C5024DD` | `FD_SWAY` | `fp_bquad_probe` | `True` | -0.140 | `similar_outcome_rejected` | FD_SWAY dominant fp error may improve via fp_bquad_probe | none |
| 4 | `LP_1A727791036F` | `FD_SURGE` | `fp_bquad_probe` | `True` | -0.728 | `repeated_rejection_evidence_warning` | FD_SURGE dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
| 5 | `LP_D460BA0F215D` | `FD_ROLL` | `fp_bquad_probe` | `True` | -0.846 | `repeated_rejection_evidence_warning` | FD_ROLL dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
| 6 | `LP_134489D9588E` | `FD_PITCH` | `fp_bquad_probe` | `True` | -1.000 | `repeated_rejection_evidence_warning` | FD_PITCH dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
