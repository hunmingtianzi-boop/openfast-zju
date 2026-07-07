# Free Decay Comparison

- Figure: `D:\OpenFast\FOCAL_C4_physics_workflow\06_figures\free_decay_reference_imported_period_tuned.png`
- Source audit: `D:\OpenFast\FOCAL_C4_physics_workflow\06_figures\free_decay_reference_imported_period_tuned_sources.csv`
- Curves: curated processed reference vs original imported package vs period-tuned package.
- Processing: same `decay_seg` and OpenFAST channel mapping as `03_scripts/05_evaluate_metrics.py`; traces are equilibrium-centered and preserve the workflow sign convention.

| DOF | Original period error | Period-tuned error | Original NRMSE | Tuned NRMSE |
|---|---:|---:|---:|---:|
| Surge | 11.256% | 2.011% | 0.1910 | 0.1892 |
| Sway | 0.336% | 0.336% | 0.1868 | 0.1868 |
| Heave | 4.733% | 0.630% | 0.1135 | 0.1154 |
| Roll | 1.919% | 1.919% | 0.0390 | 0.0390 |
| Pitch | 3.950% | 0.161% | 0.2205 | 0.2278 |
| Yaw | 1.770% | 1.770% | 0.1906 | 0.1906 |
