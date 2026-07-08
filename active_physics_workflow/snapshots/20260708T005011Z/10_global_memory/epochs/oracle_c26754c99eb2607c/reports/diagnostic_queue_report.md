# Global Loop Diagnostic Fork Queue

Generated: 2026-07-08T00:47:26+00:00

- Oracle: `oracle_c26754c99eb2607c`
- Epoch: `oracle_c26754c99eb2607c`
- Items: 82
- Pending: 82
- Active/current actionable: 3
- Source categories: `{"active": 3, "historical_import": 79}`
- Review types: `{"leak_diagnostic": 79, "period_reverse_diagnostic": 3}`

## Active / Current Review Queue

These are the items to inspect first. Full historical evidence remains in `diagnostic_queue.json`.

| Item | Priority | Source | Candidate | Run | Type | DOFs | Reasons |
|---|---:|---|---|---|---|---|---|
| `DQ_5A39BE5B2B3D` | 72.0 | `active` | `GLV3_YAW_BQ1005_EA1005_20260707T140240Z` | `RC_STEP_GLV3_YAW_BQ1005_EA1005_20260707T140240Z_20260707T141338Z0000` | `period_reverse_diagnostic` | `FD_SURGE` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SURGE:abs 1.2->4.53 signed 1.2->4.53 |
| `DQ_9B9B42DB59EB` | 72.0 | `active` | `LP_330171BDD5AE_HEAVE_20260707T144315Z0000` | `RC_STEP_LP_330171BDD5AE_HEAVE_20260707T144315Z0000_20260707T151010Z0000` | `period_reverse_diagnostic` | `FD_SWAY` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SWAY:abs 0.259->6.6 signed 0.259->6.6 |
| `DQ_62FE575B528A` | 72.0 | `active` | `GLV3_AMP_SH_EA1005_20260708T002409Z` | `RC_STEP_GLV3_AMP_SH_EA1005_20260708T002409Z_20260708T004641Z0000` | `period_reverse_diagnostic` | `FD_SWAY` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SWAY:abs 0.259->7.02 signed 0.259->7.02 |

## Historical Evidence Summary

Historical items are kept for memory/relation evidence but are not all listed here.
- active: 3
- historical_import: 79
