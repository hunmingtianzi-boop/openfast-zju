# Global Loop Diagnostic Fork Queue

Generated: 2026-07-07T04:26:38+00:00

- Oracle: `oracle_da77e9aa1d9c593c`
- Epoch: `oracle_da77e9aa1d9c593c`
- Items: 70
- Pending: 70
- Active/current actionable: 3
- Source categories: `{"active": 3, "historical_import": 67}`
- Review types: `{"leak_diagnostic": 68, "period_reverse_diagnostic": 2}`

## Active / Current Review Queue

These are the items to inspect first. Full historical evidence remains in `diagnostic_queue.json`.

| Item | Priority | Source | Candidate | Run | Type | DOFs | Reasons |
|---|---:|---|---|---|---|---|---|
| `DQ_8F5A51B75612` | 84.0 | `active` | `LP_9E877C00D37E_YAW_20260707T020651Z0000` | `RC_STEP_LP_9E877C00D37E_YAW_20260707T020651Z0000_20260707T025134Z0000` | `leak_diagnostic` | `FD_SURGE,FD_SWAY,FD_YAW` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SURGE:abs 0.651->7.89 signed 0.651->7.89; period_reverse_worse:FD_YAW:abs 0.738->6.83 signed 0.738->6.83; strong_leak:FD_SWAY->Yaw:0.505>ref0.117 |
| `DQ_F72F72C6D73B` | 72.0 | `active` | `LP_1091C22C41B7_HEAVE_20260706T135310Z0000` | `RC_STEP_LP_1091C22C41B7_HEAVE_20260706T135310Z0000_20260706T143712Z0000` | `period_reverse_diagnostic` | `FD_SWAY` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SWAY:abs 2.61->6.6 signed 2.61->6.6 |
| `DQ_B5D7A411FF70` | 72.0 | `active` | `LP_2BFEA3960C79_SWAY_20260707T011700Z0000` | `RC_STEP_LP_2BFEA3960C79_SWAY_20260707T011700Z0000_20260707T020357Z0000` | `period_reverse_diagnostic` | `FD_SWAY` | loop_verdict:DIAGNOSTIC_FORK:diagnostic hard constraint requires review; period_reverse_worse:FD_SWAY:abs 2.61->6.31 signed 2.61->6.31 |

## Historical Evidence Summary

Historical items are kept for memory/relation evidence but are not all listed here.
- active: 3
- historical_import: 67
