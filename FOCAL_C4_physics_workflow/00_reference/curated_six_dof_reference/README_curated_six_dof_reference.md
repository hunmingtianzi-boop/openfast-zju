# Curated Six-DOF Free-Decay Reference Dataset

This folder reorganizes the six experimental reference records for follow-up OpenFAST comparison. Five DOFs keep the original accepted processed windows. Heave replaces the original bad cut with the audited late coherent free-decay window.

## Files

- `raw/`: copied original representative CSV files.
- `processed/`: curated processed reference CSV files, one per DOF.
- `curated_reference_manifest.json`: source, processing, and metric metadata.
- `curated_reference_summary.csv`: compact table for scripts/reports.

## Summary

| Case | DOF | Status | Initial value | Median period (s) | Extrema count |
|---|---|---|---:|---:|---:|
| FD_SURGE | Surge | original_cut_accepted | -23.354 | 80.410 | 17 |
| FD_SWAY | Sway | original_cut_accepted | -15.027 | 79.310 | 23 |
| FD_HEAVE | Heave | recut_required_and_applied | -8.2962 | 20.830 | 27 |
| FD_ROLL | Roll | original_cut_accepted | 11.972 | 31.000 | 34 |
| FD_PITCH | Pitch | original_cut_accepted | -9.0286 | 30.460 | 31 |
| FD_YAW | Yaw | original_cut_accepted | -17.89 | 50.700 | 24 |

## Important Note

Previous Heave metrics based on `FD_HEAVE_processed.csv` should not be used for final conclusions. Use `FD_HEAVE_curated_processed.csv` from this folder instead.
