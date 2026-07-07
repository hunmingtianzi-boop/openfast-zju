# Global Loop Memory Report

Generated: 2026-07-07T05:16:44+00:00

- RunCards: 236
- Artifact entries: 236
- TransitionCards: 229
- Relation entries: 408
- Proposal history entries: 9
- Known artifact bytes: 17527510921

## Evidence Harness Policy
- Memory role: evidence compression, ranking context, and audit trail.
- Memory cannot modify TruthSnapshot, veto legal candidates, or update current_best.
- Candidate acceptance remains full 6DOF global-step with hard predicates before score.

## Artifact Retention
- baseline: 6
- current_best: 1
- diagnostic_fork: 64
- final_validation: 5
- summary_only: 160

## Relation Status
- fragile: 114
- observed: 202
- refuted: 92

## Supported Relations
- none

## Fragile Relations
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_HEAVE.score effect=0.728 confidence=1.00 support=19 refute=20
- `signed_ic_bquad` diagonal_hydro FD_SWAY -> FD_SWAY.ler_error effect=0.479 confidence=1.00 support=5 refute=8
- `signed_ic_bquad` diagonal_hydro FD_SWAY -> FD_SWAY.score effect=0.3243 confidence=1.00 support=5 refute=8
- `mooring_probe` mooring FD_ROLL -> FD_SURGE.ler_error effect=0.3221 confidence=1.00 support=7 refute=7
- `signed_ic_bquad` diagonal_hydro FD_YAW -> FD_YAW.ler_error effect=-0.3095 confidence=1.00 support=4 refute=3
- `signed_ic_bquad` diagonal_hydro FD_YAW -> FD_YAW.period_error_pct effect=-0.2392 confidence=1.00 support=4 refute=3
- `signed_ic_bquad` diagonal_hydro FD_YAW -> FD_YAW.score effect=-0.1458 confidence=1.00 support=4 refute=3
- `signed_ic_bquad` diagonal_hydro FD_SWAY -> FD_SWAY.zeta_error_ratio effect=0.1083 confidence=1.00 support=6 refute=7
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_HEAVE.zeta_error_ratio effect=0.09679 confidence=1.00 support=16 refute=23
- `signed_ic_bquad` diagonal_hydro FD_HEAVE -> FD_HEAVE.zeta_error_ratio effect=-0.09128 confidence=1.00 support=10 refute=1

## Refuted Relations
- `mooring_probe` mooring FD_SURGE -> FD_SURGE.period_error_pct effect=5.114 confidence=1.00 support=3 refute=9
- `mooring_probe` mooring FD_SURGE -> FD_SURGE.ler_error effect=4.218 confidence=1.00 support=3 refute=9
- `mooring_probe` mooring FD_ROLL -> FD_SURGE.period_error_pct effect=3.892 confidence=1.00 support=2 refute=12
- `signed_ic_bquad` diagonal_hydro FD_SURGE -> FD_SURGE.period_error_pct effect=3.623 confidence=1.00 support=5 refute=30
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_SURGE.period_error_pct effect=2.326 confidence=1.00 support=6 refute=32
- `mooring_probe` mooring FD_SURGE -> FD_SURGE.score effect=2.251 confidence=1.00 support=3 refute=9
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_HEAVE.ler_error effect=1.811 confidence=1.00 support=9 refute=30
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_SWAY.period_error_pct effect=1.036 confidence=1.00 support=9 refute=31
- `signed_ic_bquad` diagonal_hydro FD_ROLL -> FD_YAW.period_error_pct effect=0.6541 confidence=1.00 support=1 refute=18
- `signed_ic_bquad` diagonal_hydro FD_SURGE -> FD_SURGE.score effect=0.6463 confidence=1.00 support=6 refute=29

## Evidence Balance
- Support links: 673
- Refute links: 1601
- Mixed support/refute relations: 113
- Relations without support: 208
- Relations without refute: 87

## Proposal Outcomes
- proposed: 6
- rejected: 3

## Candidate Intent Coverage
- ProposalHistory entries: 9
- Entries with CandidateIntent: 9
- Coverage: 100.0%
