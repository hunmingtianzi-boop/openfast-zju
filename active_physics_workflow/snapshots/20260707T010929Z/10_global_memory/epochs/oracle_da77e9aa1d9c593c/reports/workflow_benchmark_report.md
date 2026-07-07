# Global Loop Workflow Benchmark

Generated: 2026-07-06T06:15:27+00:00

## 结论

- Benchmark 类型: `snapshot`
- Snapshot id: `WB_oracle_da77e9aa1d9c593c_20260706T061527Z0000`
- Workflow benchmark score: 66.33
- Confidence: 0.775
- Status: `DIAGNOSTIC_REVIEW_REQUIRED`
- 主问题: STOP_AUTO_MATERIALIZE
- 调度建议: `RUN_DIAGNOSTIC_REVIEW`
- 建议原因: proposal pool is diagnostic-only while active status is EXPLORATORY; require diagnostic review before materialization

## 子分

| Component | Score |
|---|---:|
| `oracle_path_readiness` | 15.000 |
| `evidence_inventory` | 15.000 |
| `active_search_efficiency` | 7.342 |
| `historical_evidence_quality` | 6.405 |
| `coupling_memory_utility` | 9.583 |
| `proposal_scheduler_readiness` | 8.000 |
| `artifact_report_hygiene` | 5.000 |

## Active Loop Decisions

- Active stages: `baseline_suite,final_validate,global_step`
- Active decision count: 4
- Active status: `EXPLORATORY`
- Active score: 7.342
- Active confidence: 1.000
- Accepted: 0
- Rejected: 0
- Diagnostic: 0
- Plateau count: 4

## Historical Evidence

- Historical cards: 227
- Complete historical cards: 22
- Diagnostic rate: 0.295
- Decision counts: `{'REJECTED': 15, 'EVIDENCE_ONLY': 212}`

## Memory Diagnostics

- Memory utility score: 9.583
- Group coverage: 1.000
- Average confidence: 0.250
- Consistency: 0.250
- Conflict count: 9
- Empty group count: 2
- Ambiguous groups: `mooring,diagonal_hydro`

## Proposal Readiness

- Status: `DIAGNOSTIC_ONLY`
- Score: 8.000
- Proposal count: 6
- Top proposal: `LP_74D87CB67FEC`
- Reason: active data insufficient for trend score, but current proposal pool is diagnostic-only; stop auto materialize.
