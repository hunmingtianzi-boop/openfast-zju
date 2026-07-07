# Global Loop Project Scorecard

Generated: 2026-07-06T02:30:01+00:00

## 结论

- 分数类型: `snapshot`
- Snapshot id: `PS_oracle_da77e9aa1d9c593c_20260706T023000Z0000`
- 状态: `CALIBRATING`
- 项目总分: 61.288
- 原始总分: 61.288
- 物理拟合分: 57.158
- workflow 健康分: 66.335
- 解释: 项目流程可继续推进，但还有 6 个 DOF 未 closed；当前 worst DOF 是 FD_SWAY，主导残差是 fp。
- 最大阻塞: final_validation
- 下一步: `review_non_materializable_proposal` - active data insufficient for trend score, but current proposal pool is diagnostic-only; stop auto materialize.

## Active Gates

| Gate | Cap | Reason |
|---|---:|---|
| `final_validation` | 90.0 | final validation has not passed |

## 6DOF 拟合

- closed DOF: 0/6
- worst DOF: `FD_SWAY` quality=57.817 dominant=`fp`
- closure gap: `FD_SURGE,FD_SWAY,FD_HEAVE,FD_ROLL,FD_PITCH,FD_YAW`

| DOF | Closed | Quality | Dominant | Period | Env | LER | fp | zeta |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| `FD_SURGE` | `False` | 66.624 | `fp` | 82.174 | 49.235 | 79.384 | 28.420 | 68.357 |
| `FD_SWAY` | `False` | 57.817 | `fp` | 53.508 | 55.322 | 58.696 | 40.072 | 95.124 |
| `FD_HEAVE` | `False` | 60.324 | `fp` | 80.532 | 44.538 | 68.263 | 16.772 | 56.738 |
| `FD_ROLL` | `False` | 69.699 | `fp` | 64.927 | 68.742 | 94.207 | 32.830 | 76.646 |
| `FD_PITCH` | `False` | 72.481 | `fp` | 90.991 | 74.810 | 45.531 | 43.816 | 84.436 |
| `FD_YAW` | `False` | 63.866 | `fp` | 80.251 | 59.657 | 39.807 | 37.311 | 91.710 |

## Workflow 子分

| Component | Score |
|---|---:|
| `oracle_path_readiness` | 15.000 |
| `evidence_inventory` | 15.000 |
| `active_search_efficiency` | 7.350 |
| `historical_evidence_quality` | 6.405 |
| `coupling_memory_utility` | 9.580 |
| `proposal_scheduler_readiness` | 8.000 |
| `artifact_report_hygiene` | 5.000 |

## 趋势与 Memory

- trend score: 7.350
- trend window: 3
- best improvement ratio: 0.000
- acceptance rate: 0.000
- diagnostic rate: 0.000
- plateau count: 3
- memory reliability score: 9.580
- memory conflicts: 9
- diagnostic evidence rate: 0.265

## Final Validation

- final_validated: `False`
- artifact_retained: `True`
- reason: not all six DOFs are closed
