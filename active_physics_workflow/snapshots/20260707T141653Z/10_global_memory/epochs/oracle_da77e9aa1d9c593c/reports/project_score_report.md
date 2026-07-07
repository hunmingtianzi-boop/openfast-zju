# Global Loop Project Scorecard

Generated: 2026-07-07T11:22:27+00:00

## 结论

- 分数类型: `snapshot`
- Snapshot id: `PS_oracle_da77e9aa1d9c593c_20260707T112224Z0000`
- 状态: `CALIBRATING`
- 项目总分: 63.875
- 原始总分: 63.875
- 物理拟合分: 58.337
- workflow 健康分: 70.644
- 解释: 项目流程可继续推进，但还有 6 个 DOF 未 closed；当前 worst DOF 是 FD_HEAVE，主导残差是 fp。
- 最大阻塞: final_validation
- 下一步: `review_non_materializable_proposal` - 1 similar proposal(s) were already rejected by full 6DOF validation

## Active Gates

| Gate | Cap | Reason |
|---|---:|---|
| `final_validation` | 90.0 | final validation has not passed |

## 6DOF 拟合

- closed DOF: 0/6
- worst DOF: `FD_HEAVE` quality=58.751 dominant=`fp`
- closure gap: `FD_SURGE,FD_SWAY,FD_HEAVE,FD_ROLL,FD_PITCH,FD_YAW`

| DOF | Closed | Quality | Dominant | Period | Env | LER | fp | zeta |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| `FD_SURGE` | `False` | 65.481 | `fp` | 71.362 | 49.659 | 90.081 | 27.009 | 73.723 |
| `FD_SWAY` | `False` | 70.714 | `fp` | 92.054 | 55.169 | 55.346 | 37.936 | 98.398 |
| `FD_HEAVE` | `False` | 58.751 | `fp` | 80.074 | 44.553 | 61.159 | 16.768 | 56.788 |
| `FD_ROLL` | `False` | 66.256 | `fp` | 62.781 | 68.482 | 80.485 | 32.828 | 77.825 |
| `FD_PITCH` | `False` | 73.975 | `fp` | 90.713 | 75.339 | 51.524 | 43.438 | 87.421 |
| `FD_YAW` | `False` | 64.137 | `fp` | 56.694 | 60.927 | 79.897 | 39.322 | 91.504 |

## Workflow 子分

| Component | Score |
|---|---:|
| `oracle_path_readiness` | 15.000 |
| `evidence_inventory` | 15.000 |
| `active_search_efficiency` | 12.332 |
| `historical_evidence_quality` | 6.405 |
| `coupling_memory_utility` | 8.907 |
| `proposal_scheduler_readiness` | 8.000 |
| `artifact_report_hygiene` | 5.000 |

## 趋势与 Memory

- trend score: 12.332
- trend window: 12
- best improvement ratio: 0.060
- acceptance rate: 0.143
- diagnostic rate: 0.417
- plateau count: 3
- memory reliability score: 8.907
- memory conflicts: 11
- diagnostic evidence rate: 0.277

## Final Validation

- final_validated: `False`
- artifact_retained: `True`
- reason: not all six DOFs are closed
