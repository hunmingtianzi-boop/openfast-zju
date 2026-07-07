# Global Loop Workflow Benchmark

Generated: 2026-07-07T05:11:09+00:00

## 结论

- Benchmark 类型: `snapshot`
- Snapshot id: `WB_oracle_da77e9aa1d9c593c_20260707T051109Z0000`
- Workflow benchmark score: 78.619
- Confidence: 0.776
- Status: `RUNNING`
- 主问题: CONTINUE_CURRENT_CAMPAIGN
- 调度建议: `RUN_ACTIVE_VALIDATION_BATCH`
- 建议原因: recent active attempts are producing accepted or improving evidence

## 子分

| Component | Score |
|---|---:|
| `oracle_path_readiness` | 15.000 |
| `evidence_inventory` | 15.000 |
| `active_search_efficiency` | 13.304 |
| `historical_evidence_quality` | 6.405 |
| `coupling_memory_utility` | 8.910 |
| `proposal_scheduler_readiness` | 15.000 |
| `artifact_report_hygiene` | 5.000 |

## Active Loop Decisions

- Active stages: `baseline_suite,final_validate,global_step`
- Active decision count: 9
- Active status: `PRODUCTIVE`
- Active score: 13.304
- Active confidence: 1.000
- Accepted: 1
- Rejected: 3
- Diagnostic: 3
- Plateau count: 0

## Historical Evidence

- Historical cards: 227
- Complete historical cards: 22
- Diagnostic rate: 0.295
- Decision counts: `{'REJECTED': 15, 'EVIDENCE_ONLY': 212}`

## Memory Diagnostics

- Memory utility score: 8.910
- Group coverage: 1.000
- Average confidence: 0.255
- Consistency: 0.083
- Conflict count: 11
- Empty group count: 2
- Ambiguous groups: `mooring,diagonal_hydro`

## Proposal Readiness

- Status: `MATERIALIZABLE`
- Score: 15.000
- Proposal count: 6
- Top proposal: `LP_330171BDD5AE`
- Reason: top proposal can be materialized but must still pass global-step full 6DOF validation
