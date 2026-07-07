# Global Loop Local Proposal Pool

Generated: 2026-07-07T09:03:31+00:00

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
| 1 | `LP_330171BDD5AE` | `FD_HEAVE` | `diagnostic_review` | `False` | -1.102 | `similar_outcome_rejected_diagnostic_only` | FD_HEAVE dominant fp error may improve via fp_bquad_probe | 1 similar proposal(s) were already rejected by full 6DOF validation |
| 2 | `LP_1A727791036F` | `FD_SURGE` | `diagnostic_review` | `False` | -1.228 | `repeated_rejection_evidence_warning_diagnostic_only` | FD_SURGE dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
| 3 | `LP_D460BA0F215D` | `FD_ROLL` | `diagnostic_review` | `False` | -1.346 | `repeated_rejection_evidence_warning_diagnostic_only` | FD_ROLL dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
| 4 | `LP_134489D9588E` | `FD_PITCH` | `diagnostic_review` | `False` | -1.500 | `repeated_rejection_evidence_warning_diagnostic_only` | FD_PITCH dominant fp error may improve via fp_bquad_probe | diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review |
| 5 | `LP_156E5BFB263B` | `FD_YAW` | `diagnostic_review` | `False` | -1.558 | `similar_outcome_rejected_diagnostic_only` | FD_YAW dominant fp error may improve via fp_bquad_probe | 1 similar proposal(s) were already rejected by full 6DOF validation |
| 6 | `LP_71D22C5024DD` | `FD_SWAY` | `diagnostic_review` | `False` | -1.640 | `similar_outcome_rejected_diagnostic_only` | FD_SWAY dominant fp error may improve via fp_bquad_probe | 1 similar proposal(s) were already rejected by full 6DOF validation |
