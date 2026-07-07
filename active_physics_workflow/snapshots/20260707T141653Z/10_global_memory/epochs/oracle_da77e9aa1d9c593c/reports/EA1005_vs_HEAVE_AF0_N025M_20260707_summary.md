# EA1005 vs HEAVE AddF0 -0.25M Comparison

- Current best candidate: `GLV3_MOOR_EA1005_20260707T033530Z`
- Tested candidate: `GLV3_HEAVE_AF0_N025M_EA1005_20260707T113721Z`
- Step decision: `REJECTED`
- Loop verdict: `DIAGNOSTIC_FORK`
- Global score: `0.679273` -> `1.039797` (`+0.360524`)
- Hard guard: `period_reverse_worse:FD_SWAY:abs 0.259->10.1 signed 0.259->10.1`
- Figure: `10_global_memory/epochs/oracle_da77e9aa1d9c593c/reports/figures/EA1005_vs_HEAVE_AF0_N025M_20260707_sixdof_compare.png`

| DOF | best score | new score | delta | best period % | new period % | best fp | new fp | best LER | new LER |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| FD_SURGE | 0.7218 | 1.2920 | +0.5702 | 1.204 | 3.822 | 0.324 | 0.303 | 0.055 | 0.724 |
| FD_SWAY | 0.5600 | 1.7440 | +1.1840 | 0.259 | 10.096 | 0.196 | 0.188 | 0.403 | 0.396 |
| FD_HEAVE | 1.0977 | 1.0151 | -0.0826 | 0.747 | 0.563 | 0.596 | 0.587 | 0.318 | 0.186 |
| FD_ROLL | 0.6042 | 0.6625 | +0.0584 | 1.778 | 1.592 | 0.246 | 0.251 | 0.121 | 0.301 |
| FD_PITCH | 0.4504 | 0.5622 | +0.1118 | 0.307 | 0.623 | 0.156 | 0.157 | 0.470 | 0.638 |
| FD_YAW | 0.6416 | 0.9629 | +0.3213 | 2.292 | 0.015 | 0.185 | 0.191 | 0.126 | 1.607 |

## Efficiency note

This candidate was already non-promotable after FD_SWAY because the period reverse guard fired. With the new runner early-stop guard, equivalent future candidates can stop after the offending DOF and still enter memory as diagnostic evidence.
