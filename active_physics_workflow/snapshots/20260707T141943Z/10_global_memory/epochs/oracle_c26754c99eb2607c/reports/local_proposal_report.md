# Global Loop Local Proposal Pool

Generated: 2026-07-07T14:01:10+00:00

- Oracle: `oracle_c26754c99eb2607c`
- Parent RunCard: `RC_GLV3_MOOR_EA1005_20260707T033530Z`
- Parent candidate: `GLV3_MOOR_EA1005_20260707T033530Z`
- Open DOFs: `FD_SURGE,FD_SWAY,FD_HEAVE,FD_ROLL,FD_PITCH,FD_YAW`
- Protected DOFs: ``
- Fragile DOFs: `FD_HEAVE,FD_PITCH,FD_ROLL,FD_SURGE,FD_SWAY,FD_YAW`
- Memory policy: `evidence_only_no_veto`
- Local proposals rank and explain candidates; only full 6DOF global-step can accept or reject.

| Rank | Proposal | Target | Action | Ready | Priority | Status | Intent | Warnings |
|---:|---|---|---|---|---:|---|---|---|
| 1 | `LP_330171BDD5AE` | `FD_HEAVE` | `fp_bquad_probe` | `True` | 1.148 | `proposed` | FD_HEAVE dominant fp error may improve via fp_bquad_probe | none |
| 2 | `LP_1A727791036F` | `FD_SURGE` | `fp_bquad_probe` | `True` | 0.772 | `proposed` | FD_SURGE dominant fp error may improve via fp_bquad_probe | none |
| 3 | `LP_D460BA0F215D` | `FD_ROLL` | `fp_bquad_probe` | `True` | 0.654 | `proposed` | FD_ROLL dominant fp error may improve via fp_bquad_probe | none |
| 4 | `LP_71D22C5024DD` | `FD_SWAY` | `fp_bquad_probe` | `True` | 0.610 | `proposed` | FD_SWAY dominant fp error may improve via fp_bquad_probe | none |
| 5 | `LP_134489D9588E` | `FD_PITCH` | `fp_bquad_probe` | `True` | 0.500 | `proposed` | FD_PITCH dominant fp error may improve via fp_bquad_probe | none |
| 6 | `LP_156E5BFB263B` | `FD_YAW` | `diagnostic_review` | `False` | -1.558 | `similar_outcome_rejected_diagnostic_only` | FD_YAW dominant fp error may improve via fp_bquad_probe | 1 similar proposal(s) were already rejected by full 6DOF validation |
