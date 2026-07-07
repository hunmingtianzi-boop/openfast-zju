# Global Loop Diagnostic Fork Queue

Generated: 2026-07-06T14:43:52+00:00

- Oracle: `oracle_da77e9aa1d9c593c`
- Epoch: `oracle_da77e9aa1d9c593c`
- Items: 68
- Pending: 68
- Active/current actionable: 1
- Source categories: `{"active": 1, "historical_import": 67}`
- Review types: `{"leak_diagnostic": 67, "period_reverse_diagnostic": 1}`

## Active / Current Review Queue

These are the items to inspect first. Full historical evidence remains in `diagnostic_queue.json`.

| Item | Priority | Source | Candidate | Run | Type | DOFs | Reasons |
|---|---:|---|---|---|---|---|---|
| `DQ_F72F72C6D73B` | 72.0 | `active` | `LP_1091C22C41B7_HEAVE_20260706T135310Z0000` | `RC_STEP_LP_1091C22C41B7_HEAVE_20260706T135310Z0000_20260706T143712Z0000` | `period_reverse_diagnostic` | `FD_SWAY` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SWAY:abs 2.61->6.6 signed 2.61->6.6 |

## Historical Evidence Summary

Historical items are kept for memory/relation evidence but are not all listed here.
- active: 1
- historical_import: 67
