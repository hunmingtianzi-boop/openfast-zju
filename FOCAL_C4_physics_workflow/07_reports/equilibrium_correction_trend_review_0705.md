# Equilibrium Correction Trend Review

## FD_YAW
| candidate | period | start | nrmse | shape | zerr | env | LER | fp | major regressions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AMP_SH_HAF15_Y15052_R8715_07041136 | 0.522812 | 0.195165 | 0.0229033 | 0.387715 | 0.00306874 | 0.261304 | 0.93315 | 0.192758 | - |
| YAW_EQIC83_0704231051 | 0.0977851 | 0.173184 | 0.0188541 | 0.261764 | 0.0135582 | 0.231217 | 3.43384 | 0.205199 | zeta_abs_error,ler_error |
| YAW_EQIC87_0704223546 | 2.77684 | 0.144061 | 0.0326355 | 0.922506 | 0.0133617 | 0.237405 | 0.124877 | 0.131035 | period_error_pct,zeta_abs_error |
| YAW_EQIC87_CL105_0704224441 | 2.47734 | 0.139428 | 0.0278382 | 0.793922 | 0.0146282 | 0.244075 | 0.683932 | 0.0983395 | period_error_pct,zeta_abs_error |
| YAW_EQIC87_CL105_BL100_0704230212 | 1.86432 | 0.132902 | 0.022014 | 0.475456 | 0.0110969 | 0.23043 | 3.50527 | 0.119831 | period_error_pct,zeta_abs_error,ler_error |
| YAW_EQIC87_CL105_BQ2X_0704225331 | 1.35753 | 0.139727 | 0.043954 | 0.849619 | 0.0186104 | 0.381917 | 2.10815 | 0.0914859 | period_error_pct,zeta_abs_error,envelope_error,ler_error |
| YAW_EQIC_0704222630 | 6.99002 | 0.0113984 | 0.0608155 | 1.48312 | 0.0147715 | 0.208081 | 1.32398 | 0.0163906 | period_error_pct,zeta_abs_error,ler_error |

## FD_SURGE
| candidate | period | start | nrmse | shape | zerr | env | LER | fp | major regressions |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| AMP_SH_HAF15_Y15052_R8715_07041136 | 1.84898 | 0.175461 | 0.0302486 | 0.590018 | 0.00516591 | 0.327665 | 0.633699 | 0.130343 | - |
| SURGE_EQIC90_0704231943 | 7.60583 | 0.0557563 | 0.0242493 | 0.548108 | 0.0120679 | 0.274047 | 0.676856 | 0.0703002 | period_error_pct,zeta_abs_error |
| AMP_SH_H054_SWTTD_SG1019_07040711 | 0.799803 | 0.107317 | 0.0258561 | 0.541013 | 0.0097256 | 0.315019 | 0.659153 | 0.0662728 | zeta_abs_error |
| BQIC_SURGE_0704123214_F098_I072 | 1.76894 | 0.13622 | 0.0317966 | 0.750102 | 0.0094481 | 0.301109 | 1.80917 | 0.100135 | zeta_abs_error,ler_error |

## Yaw local threshold estimate
- zeta major limit from formal: 0.00506874
- observed points: [(0.8048346, 0.003068741, 0.19275803, 0.93314977, 'AMP_SH_HAF15_Y15052_R8715_07041136'), (0.82681561, 0.013558246, 0.20519907, 3.4338368, 'YAW_EQIC83_0704231051'), (0.85593865, 0.013361689999999999, 0.13103487, 0.12487726, 'YAW_EQIC87_0704223546')]
- Linearized safe start_abs_ratio before zeta major: 0.809026