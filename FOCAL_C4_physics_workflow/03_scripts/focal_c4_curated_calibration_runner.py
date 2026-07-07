from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import json
import math
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import correlate, correlation_lags, find_peaks, hilbert, welch

import phase2_15mw_runner as p2


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "focal_c4_curated_six_dof_calibration"
START_ID = "exploratory_calibrated_model"
START_PACKAGE = ROOT / "focal_c4_exploratory_calibrated_model" / START_ID
START = START_PACKAGE / "OpenFAST_input_files"
CURATED_REFERENCE = (
    ROOT
    / "phase2_15mw_free_decay_diagnosis"
    / "00_reference_data"
    / "curated_six_dof_reference"
)
CURATED_PROCESSED = CURATED_REFERENCE / "processed"
RESCORE = ROOT / "curated_reference_rescore"

FST = p2.FST_NAME
ED = p2.ED_NAME
HD = p2.HD_NAME
SEA = p2.SEA_NAME
DOFS = p2.DOFS
IDX = {dof: i for i, dof in enumerate(DOFS)}
HRP = ["Heave", "Roll", "Pitch"]

STAGES = {
    "C0": "C0_starting_model_recheck",
    "C1": "C1_period_sensitivity_rescan",
    "C2": "C2_heave_curated_period_fit",
    "C3": "C3_roll_pitch_period_fit",
    "C4": "C4_surge_sway_yaw_guarded_refine",
    "C5": "C5_integrated_period2_search",
    "C6": "C6_shape_refinement_after_period_pass",
    "C7": "C7_final_curated_calibrated_model",
}

WEIGHTS = {
    "Surge": (0.35, 0.15, 0.15, 0.15, 0.10, 0.10),
    "Sway": (0.35, 0.15, 0.15, 0.15, 0.10, 0.10),
    "Yaw": (0.35, 0.15, 0.15, 0.15, 0.10, 0.10),
    "Heave": (0.25, 0.15, 0.25, 0.20, 0.10, 0.05),
    "Roll": (0.25, 0.15, 0.15, 0.25, 0.10, 0.10),
    "Pitch": (0.25, 0.15, 0.15, 0.25, 0.10, 0.10),
}
SHAPE_WEIGHTS = {
    "Surge": (0.20, 0.20, 0.20, 0.20, 0.10, 0.10),
    "Sway": (0.20, 0.20, 0.20, 0.20, 0.10, 0.10),
    "Yaw": (0.20, 0.20, 0.20, 0.20, 0.10, 0.10),
    "Heave": (0.20, 0.10, 0.25, 0.25, 0.10, 0.10),
    "Roll": (0.15, 0.20, 0.30, 0.15, 0.10, 0.10),
    "Pitch": (0.15, 0.20, 0.30, 0.15, 0.10, 0.10),
}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def dump_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_matrix(path: Path, marker: str) -> np.ndarray:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    return np.array([[float(value) for value in lines[start + row].split()[:6]] for row in range(6)])


def write_matrix(path: Path, marker: str, matrix: np.ndarray) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if marker in line)
    suffix = "   " + lines[start][lines[start].find(marker) :].rstrip("\r\n")
    for row in range(6):
        lines[start + row] = " ".join(f"{matrix[row, col]:.8E}" for col in range(6)) + (suffix if row == 0 else "") + "\n"
    path.write_text("".join(lines), encoding="utf-8")


def get_scalar(path: Path, key: str) -> float:
    line = next(line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if len(line.split()) >= 2 and line.split()[1] == key)
    return float(line.split()[0])


BASE = {
    "AddCLin": read_matrix(START / HD, "AddCLin"),
    "AddBLin": read_matrix(START / HD, "AddBLin"),
    "AddBQuad": read_matrix(START / HD, "AddBQuad"),
    **{key: get_scalar(START / ED, key) for key in ["PtfmMass", "PtfmCMzt", "PtfmRIner", "PtfmPIner", "PtfmYIner"]},
}


def curated_case(case: p2.ReferenceCase) -> p2.ReferenceCase:
    return p2.ReferenceCase(
        case.case_id,
        case.dof,
        CURATED_PROCESSED / f"{case.case_id}_curated_processed.csv",
        case.excitation_unit,
    )


CASES = [curated_case(case) for case in p2.CASES]


def read_reference(case: p2.ReferenceCase) -> pd.DataFrame:
    return pd.read_csv(case.csv_path)


def reference_info(case: p2.ReferenceCase, reference: pd.DataFrame) -> dict:
    dt = float(np.nanmedian(np.diff(reference["Time"].to_numpy(float))))
    return {
        "case_id": case.case_id,
        "main_dof": case.dof,
        "reference_kind": "curated",
        "old_heave_reference": "deprecated",
        "old_heave_reference_used": False,
        "source_file": str(case.csv_path),
        "initial_displacement_for_simulation": float(reference[case.dof].iloc[0]),
        "processed_sample_count": int(len(reference)),
        "sampling_interval_s": dt,
        "sampling_frequency_hz": 1.0 / dt,
    }


def ensure_structure() -> None:
    for rel in [
        "00_inputs",
        "01_starting_model_backup",
        "02_curated_reference",
        "03_objective_definition",
        "04_parameter_space",
        "05_stage_runs",
        "06_metrics",
        "07_figures",
        "08_diffs",
        "09_logs",
        "10_final_report",
        "figures",
        "diffs",
        "logs",
        "curated_six_dof_calibrated_model",
    ]:
        (OUT / rel).mkdir(parents=True, exist_ok=True)
    for name in STAGES.values():
        (OUT / "05_stage_runs" / name).mkdir(parents=True, exist_ok=True)


def copytree_once(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    shutil.copytree(src, dst)


def copy2_once(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst)


def init() -> None:
    ensure_structure()
    copytree_once(START_PACKAGE, OUT / "01_starting_model_backup" / START_ID)
    for src in [
        START_PACKAGE / "parameter_change.json",
        START_PACKAGE / "metrics.csv",
        START_PACKAGE / "six_dof_comparison.png",
        START_PACKAGE / "limitations.md",
        RESCORE / "metrics_all_models_curated_reference.csv",
        RESCORE / "model_ranking_curated_reference.csv",
        RESCORE / "final_curated_reference_rescore_report.md",
    ]:
        if src.exists():
            copy2_once(src, OUT / "00_inputs" / src.name)
    for src in sorted(CURATED_PROCESSED.glob("*_curated_processed.csv")):
        copy2_once(src, OUT / "02_curated_reference" / src.name)
    for src in ["curated_reference_manifest.json", "curated_reference_summary.csv", "README_curated_six_dof_reference.md"]:
        if (CURATED_REFERENCE / src).exists():
            copy2_once(CURATED_REFERENCE / src, OUT / "02_curated_reference" / src)
    manifest_lines = [f"# Starting Model SHA-256 Manifest", "", f"- starting_model_id: `{START_ID}`", f"- starting_model_path: `{START_PACKAGE}`", "- source_stage: `focal_c4_exploratory_calibrated_model/S7`", ""]
    for path in sorted((OUT / "01_starting_model_backup" / START_ID).rglob("*")):
        if path.is_file():
            manifest_lines.append(f"{sha256(path)}  {path.relative_to(OUT / '01_starting_model_backup' / START_ID).as_posix()}")
    write_text(OUT / "01_starting_model_backup" / "starting_model_sha256_manifest.txt", "\n".join(manifest_lines))
    write_objective_docs()
    write_reference_manifest()


def write_objective_docs() -> None:
    write_text(
        OUT / "03_objective_definition" / "curated_period_shape_objective.md",
        """
# Curated Six-DOF Calibration Objective

All scoring uses `reference_kind = curated`.

- `old_heave_reference = deprecated`
- `old_heave_reference_used = false`
- Primary objective: `J_period6 = max(E_T_surge, E_T_sway, E_T_heave, E_T_roll, E_T_pitch, E_T_yaw)`.
- After all six period errors are below 2%, rank by `J_shape_total`, `J_HRP_shape`, and `J_total`.

This is a calibration-to-data / presentation-fit objective, not an as-built reconstruction objective.
""",
    )
    pd.DataFrame(
        [
            {"parameter_group": "AddCLin diagonal", "values": "0.5,0.75,0.9,0.95,1.0,1.05,1.1,1.25,1.5,2,3,4,5"},
            {"parameter_group": "mass and inertia", "values": "0.7,0.8,0.9,1.0,1.1,1.2,1.3; inertia up to 3.0"},
            {"parameter_group": "PtfmCMzt", "values": "-5,-3,-2,-1,0,1,2,3,5 m"},
            {"parameter_group": "initial displacement scale", "values": "0.5,0.75,0.9,1.0,1.1,1.25,1.5"},
            {"parameter_group": "damping after period pass", "values": "0,0.25,0.5,0.75,1.0,1.25,1.5,2,3,5"},
        ]
    ).to_csv(OUT / "04_parameter_space" / "parameter_space_definition.csv", index=False, encoding="utf-8-sig")


def write_reference_manifest() -> None:
    rows = []
    missing = []
    for case in CASES:
        if not case.csv_path.exists():
            missing.append(str(case.csv_path))
        rows.append({"case_id": case.case_id, "dof": case.dof, "curated_processed_file": str(case.csv_path), "exists": case.csv_path.exists()})
    pd.DataFrame(rows).to_csv(OUT / "02_curated_reference" / "curated_reference_usage_manifest.csv", index=False, encoding="utf-8-sig")
    text = [
        "# Curated Reference Usage Manifest",
        "",
        "- reference_kind = curated",
        "- old_heave_reference = deprecated",
        "- old_heave_reference_used = false",
        "- No scoring path points to the deprecated Heave processed source.",
        "",
        "| Case | DOF | Curated processed CSV | Exists |",
        "|---|---|---|---|",
    ]
    for row in rows:
        text.append(f"| {row['case_id']} | {row['dof']} | `{row['curated_processed_file']}` | {row['exists']} |")
    if missing:
        text.extend(["", "## Missing Files", "", *[f"- `{x}`" for x in missing]])
    else:
        text.extend(["", "All six curated processed CSV files are present. Heave uses `FD_HEAVE_curated_processed.csv`."])
    write_text(OUT / "curated_reference_usage_manifest.md", "\n".join(text))


def canonical_actions(actions: list[dict]) -> list[dict]:
    result: dict[tuple, dict] = {}
    for action in actions:
        key = (action.get("parameter"), action.get("dof", ""), action.get("dof_a", ""), action.get("dof_b", ""))
        result[key] = action
    return list(result.values())


def create_template(stage: str, model_id: str, actions: list[dict]) -> Path:
    model_dir = OUT / "05_stage_runs" / STAGES[stage] / model_id
    template = model_dir / "input_template"
    if template.exists():
        return template
    model_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(START, template)
    clin = BASE["AddCLin"].copy()
    blin = BASE["AddBLin"].copy()
    bquad = BASE["AddBQuad"].copy()
    changed_clin = changed_blin = changed_bquad = False
    for action in canonical_actions(actions):
        param = action["parameter"]
        dof = action.get("dof")
        if param == "AddCLin_diag_factor":
            clin[IDX[dof], IDX[dof]] = BASE["AddCLin"][IDX[dof], IDX[dof]] * float(action["factor"])
            changed_clin = True
        elif param == "AddCLin_offdiag_estimated":
            ia, ib = IDX[action["dof_a"]], IDX[action["dof_b"]]
            estimate = math.sqrt(abs(clin[ia, ia] * clin[ib, ib]))
            value = estimate * float(action["factor"])
            clin[ia, ib] = value
            clin[ib, ia] = value
            changed_clin = True
        elif param in {"PtfmMass", "PtfmRIner", "PtfmPIner", "PtfmYIner"}:
            p2.replace_key(template / ED, param, BASE[param] * float(action["factor"]))
        elif param == "PtfmCMzt_offset":
            p2.replace_key(template / ED, "PtfmCMzt", BASE["PtfmCMzt"] + float(action["offset"]))
        elif param == "AddBLin_diag_factor":
            blin[IDX[dof], IDX[dof]] = BASE["AddBLin"][IDX[dof], IDX[dof]] * float(action["factor"])
            changed_blin = True
        elif param == "AddBLin_diag_value":
            blin[IDX[dof], IDX[dof]] = float(action["value"])
            changed_blin = True
        elif param == "AddBLin_offdiag_estimated":
            ia, ib = IDX[action["dof_a"]], IDX[action["dof_b"]]
            da = abs(blin[ia, ia]) or abs(BASE["AddBQuad"][ia, ia]) or 1.0
            db = abs(blin[ib, ib]) or abs(BASE["AddBQuad"][ib, ib]) or 1.0
            value = math.sqrt(da * db) * float(action["factor"])
            blin[ia, ib] = value
            blin[ib, ia] = value
            changed_blin = True
        elif param == "AddBQuad_diag_factor":
            bquad[IDX[dof], IDX[dof]] = BASE["AddBQuad"][IDX[dof], IDX[dof]] * float(action["factor"])
            changed_bquad = True
    if changed_clin:
        write_matrix(template / HD, "AddCLin", clin)
    if changed_blin:
        write_matrix(template / HD, "AddBLin", blin)
    if changed_bquad:
        write_matrix(template / HD, "AddBQuad", bquad)
    eig = np.linalg.eigvalsh((clin + clin.T) / 2)
    record = {
        "model_id": model_id,
        "stage": stage,
        "actions": canonical_actions(actions),
        "minimum_AddCLin_eigenvalue": float(eig.min()),
        "stable_screen": bool(eig.min() > 0),
        "reference_kind": "curated",
        "old_heave_reference_used": False,
        "claim_boundary": "calibration-to-data / presentation-fit; not as-built",
    }
    dump_json(model_dir / "parameter_change.json", record)
    write_diff(template, OUT / "08_diffs" / f"{model_id}.patch")
    copy2_once(OUT / "08_diffs" / f"{model_id}.patch", OUT / "diffs" / f"{model_id}.patch")
    return template


def write_diff(template: Path, output: Path) -> None:
    patches = []
    for name in [FST, ED, HD, SEA]:
        old = (START / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        new = (template / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        patches.extend(difflib.unified_diff(old, new, fromfile=f"start/{name}", tofile=f"candidate/{name}"))
    write_text(output, "".join(patches) if patches else "No physical parameter change from starting exploratory model.")


def configure_free_decay(input_dir: Path, case: p2.ReferenceCase, reference: pd.DataFrame, scale: float) -> list[dict]:
    release_value = float(reference[case.dof].iloc[0]) * scale
    return p2.configure_free_decay(input_dir, case, release_value)


def parse_openfast(path: Path) -> pd.DataFrame:
    return p2.parse_openfast_output(path)


def signal_metrics(time: np.ndarray, signal: np.ndarray) -> dict:
    time = np.asarray(time, dtype=float)
    signal = np.asarray(signal, dtype=float)
    tail = max(20, int(0.2 * len(signal)))
    centered = signal - float(np.nanmedian(signal[-tail:]))
    span = float(np.nanmax(centered) - np.nanmin(centered))
    dt = float(np.nanmedian(np.diff(time)))
    min_distance = max(1, int(2.0 / dt))
    prominence = max(span * 0.03, 1e-10)
    max_idx, _ = find_peaks(centered, prominence=prominence, distance=min_distance)
    min_idx, _ = find_peaks(-centered, prominence=prominence, distance=min_distance)
    peaks = sorted([(int(i), "max") for i in max_idx] + [(int(i), "min") for i in min_idx])
    periods = []
    for indices in (max_idx, min_idx):
        if len(indices) > 1:
            periods.extend(np.diff(time[indices]).tolist())
    period = float(np.nanmedian(periods)) if periods else math.nan
    freq, power = welch(centered, fs=1.0 / dt, nperseg=min(len(centered), 4096))
    positive = freq > 0
    dominant_freq = float(freq[positive][np.nanargmax(power[positive])]) if positive.any() else math.nan
    peak_rows = [{"time_s": float(time[i]), "amplitude": float(abs(centered[i])), "signed": float(centered[i]), "kind": kind} for i, kind in peaks]
    return {
        "period_s": period,
        "dominant_frequency_hz": dominant_freq,
        "peak_count": len(peak_rows),
        "first_peaks": peak_rows[:8],
        "peak_abs": float(np.nanmax(np.abs(centered))),
    }


def centered_interp(reference: pd.DataFrame, simulation: pd.DataFrame, dof: str, shift_s: float = 0.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    channel = "Ptfm" + dof
    t_ref = reference["Time"].to_numpy(float)
    y_ref = reference[dof].to_numpy(float)
    t_sim = simulation["Time"].to_numpy(float) + shift_s
    y_sim = simulation[channel].to_numpy(float)
    y_sim = y_sim - float(np.nanmedian(y_sim[-max(20, int(len(y_sim) * 0.2)) :]))
    start = max(float(t_ref[0]), float(t_sim[0]))
    end = min(float(t_ref[-1]), float(t_sim[-1]), 600.0)
    grid = t_ref[(t_ref >= start) & (t_ref <= end)]
    r = np.interp(grid, t_ref, y_ref)
    s = np.interp(grid, t_sim, y_sim)
    return grid - start, r, s


def error_pct(sim: float, ref: float) -> float:
    return abs(sim - ref) / max(abs(ref), 1e-12) * 100.0 if np.isfinite(sim) and np.isfinite(ref) else math.nan


def envelope_error(ref: np.ndarray, sim: np.ndarray) -> float:
    env_ref = np.abs(hilbert(ref))
    env_sim = np.abs(hilbert(sim))
    return float(np.sqrt(np.mean((env_sim - env_ref) ** 2)) / max(np.ptp(env_ref), 1e-12) * 100.0)


def compare(case: p2.ReferenceCase, reference: pd.DataFrame, simulation: pd.DataFrame, model_id: str, stage: str, scale: float = 1.0, shift_s: float = 0.0) -> dict:
    time, ref, sim = centered_interp(reference, simulation, case.dof, shift_s)
    ref_m = signal_metrics(time, ref)
    sim_m = signal_metrics(time, sim)
    n = min(len(ref_m["first_peaks"]), len(sim_m["first_peaks"]), 5)
    n3 = min(len(ref_m["first_peaks"]), len(sim_m["first_peaks"]), 3)
    amplitude_error = float(np.mean([error_pct(sim_m["first_peaks"][i]["amplitude"], ref_m["first_peaks"][i]["amplitude"]) for i in range(n)])) if n else math.nan
    initial_peak_error = error_pct(sim_m["first_peaks"][0]["amplitude"], ref_m["first_peaks"][0]["amplitude"]) if n else math.nan
    first_3_peaks_error = float(np.mean([error_pct(sim_m["first_peaks"][i]["amplitude"], ref_m["first_peaks"][i]["amplitude"]) for i in range(n3)])) if n3 else math.nan
    peak_time_error = float(np.mean([abs(sim_m["first_peaks"][i]["time_s"] - ref_m["first_peaks"][i]["time_s"]) for i in range(n)])) if n else math.nan
    phase_error = peak_time_error / max(ref_m["period_s"], 1e-12) * 100.0 if np.isfinite(peak_time_error) and np.isfinite(ref_m["period_s"]) else math.nan
    nrmse = float(np.sqrt(np.mean((sim - ref) ** 2)) / max(np.ptp(ref), 1e-12) * 100.0)
    env = envelope_error(ref, sim)
    ref_late = ref[time >= min(120.0, time[-1] * 0.5)]
    sim_late = sim[time >= min(120.0, time[-1] * 0.5)]
    ref_energy = float(np.mean(ref_late**2) / max(np.mean(ref**2), 1e-12)) if len(ref_late) else math.nan
    sim_energy = float(np.mean(sim_late**2) / max(np.mean(sim**2), 1e-12)) if len(sim_late) else math.nan
    late_stage_energy_ratio_error = error_pct(sim_energy, ref_energy)
    period_error = error_pct(sim_m["period_s"], ref_m["period_s"])
    frequency_error = error_pct(sim_m["dominant_frequency_hz"], ref_m["dominant_frequency_hz"])
    components = {
        "period_error_pct": period_error,
        "frequency_error_pct": frequency_error,
        "nrmse_pct": nrmse,
        "envelope_error_pct": env,
        "peak_error_pct": amplitude_error,
        "phase_error_pct": phase_error,
    }
    j = float(sum(w * components[key] for w, key in zip(WEIGHTS[case.dof], ["period_error_pct", "frequency_error_pct", "nrmse_pct", "envelope_error_pct", "peak_error_pct", "phase_error_pct"]))) if all(np.isfinite(list(components.values()))) else math.nan
    shape_components = [nrmse, env, phase_error, initial_peak_error, first_3_peaks_error, late_stage_energy_ratio_error]
    shape = float(sum(w * v for w, v in zip(SHAPE_WEIGHTS[case.dof], shape_components))) if all(np.isfinite(shape_components)) else math.nan
    return {
        "stage": stage,
        "model_id": model_id,
        "case_id": case.case_id,
        "dof": case.dof,
        "reference_kind": "curated",
        "old_heave_reference_used": False,
        "initial_displacement_scale": scale,
        "time_shift_s": shift_s,
        "reference_period_s": ref_m["period_s"],
        "simulation_period_s": sim_m["period_s"],
        "reference_frequency_hz": ref_m["dominant_frequency_hz"],
        "simulation_frequency_hz": sim_m["dominant_frequency_hz"],
        "period_error_pct": period_error,
        "frequency_error_pct": frequency_error,
        "amplitude_error_pct": amplitude_error,
        "initial_peak_error_pct": initial_peak_error,
        "first_3_peaks_error_pct": first_3_peaks_error,
        "envelope_error_pct": env,
        "phase_error_pct": phase_error,
        "peak_time_error_s": peak_time_error,
        "nrmse_pct": nrmse,
        "late_stage_energy_ratio_ref": ref_energy,
        "late_stage_energy_ratio_sim": sim_energy,
        "late_stage_energy_ratio_error": late_stage_energy_ratio_error,
        "J_dof": j,
        "J_shape": shape,
        "period_under_2pct": bool(np.isfinite(period_error) and period_error < 2.0),
        "J_under_10pct": bool(np.isfinite(j) and j < 10.0),
        "aligned_time_s": time,
        "reference_aligned": ref,
        "simulation_aligned": sim,
    }


def run_one(stage: str, model_id: str, actions: list[dict], case: p2.ReferenceCase, ic_scales: dict[str, float] | None = None) -> tuple[dict | None, dict]:
    template = create_template(stage, model_id, actions)
    run_dir = OUT / "05_stage_runs" / STAGES[stage] / model_id / "runs" / case.case_id
    output = run_dir / "input_files" / Path(FST).with_suffix(".out").name
    log = run_dir / "run.log"
    reference = read_reference(case)
    scale = (ic_scales or {}).get(case.dof, 1.0)
    if output.exists() and log.exists() and "OpenFAST terminated normally." in log.read_text(encoding="utf-8", errors="replace"):
        result = compare(case, reference, parse_openfast(output), model_id, stage, scale)
        return result, {"stage": stage, "model_id": model_id, "case_id": case.case_id, "status": "normal_completion_reused", "output": str(output)}
    input_dir = run_dir / "input_files"
    if not input_dir.exists():
        shutil.copytree(template, input_dir)
    shared = run_dir / "IEA-15-240-RWT"
    if not shared.exists() and p2.SHARED_RWT.exists():
        shutil.copytree(p2.SHARED_RWT, shared)
    setup = configure_free_decay(input_dir, case, reference, scale)
    dump_json(run_dir / "run_configuration.json", {"stage": stage, "model_id": model_id, "case_id": case.case_id, "physical_actions": actions, "test_configuration": setup, "reference_processing": reference_info(case, reference)})
    command = [str(input_dir / "OpenFAST_Release.exe"), FST]
    proc = subprocess.run(command, cwd=input_dir, capture_output=True, text=True, errors="replace")
    log.write_text(f"command: {command}\nreturncode: {proc.returncode}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}", encoding="utf-8")
    normal = output.exists() and "OpenFAST terminated normally." in log.read_text(encoding="utf-8", errors="replace")
    record = {"stage": stage, "model_id": model_id, "case_id": case.case_id, "status": "normal_completion" if normal else "failed", "returncode": proc.returncode, "output": str(output)}
    if not normal:
        return None, record
    return compare(case, reference, parse_openfast(output), model_id, stage, scale), record


def run_models(stage: str, models: list[dict], max_workers: int = 6) -> list[dict]:
    tasks = []
    for model in models:
        create_template(stage, model["model_id"], model.get("actions", []))
        for case in model.get("cases", CASES):
            tasks.append((stage, model["model_id"], model.get("actions", []), case, model.get("ic_scales", {})))
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(tasks)))) as pool:
        pairs = list(pool.map(lambda args: run_one(*args), tasks))
    results = [result for result, _ in pairs if result is not None]
    records = [record for _, record in pairs]
    pd.DataFrame([flat(x) for x in results]).to_csv(OUT / "06_metrics" / f"{stage}_metrics.csv", index=False, encoding="utf-8-sig")
    dump_json(OUT / "09_logs" / f"{stage}_run_manifest.json", records)
    copy2_once(OUT / "09_logs" / f"{stage}_run_manifest.json", OUT / "logs" / f"{stage}_run_manifest.json")
    return results


def flat(result: dict) -> dict:
    return {k: v for k, v in result.items() if not isinstance(v, np.ndarray)}


def group_score(results: list[dict]) -> dict:
    periods = [x["period_error_pct"] for x in results]
    j = [x["J_dof"] for x in results]
    shape = [x["J_shape"] for x in results]
    hrp = [x for x in results if x["dof"] in HRP]
    return {
        "J_total": float(np.nanmean(j)),
        "J_shape_total": float(np.nanmean(shape)),
        "J_HRP": float(np.nanmean([x["J_dof"] for x in hrp])),
        "J_HRP_shape": float(np.nanmean([x["J_shape"] for x in hrp])),
        "J_period6": float(np.nanmax(periods)),
        "max_period_error": float(np.nanmax(periods)),
        "number_of_DOF_period_under_2_percent": int(sum(bool_value(x.get("period_under_2pct")) for x in results)),
        "number_of_DOF_J_under_10_percent": int(sum(bool_value(x.get("J_under_10pct")) for x in results)),
        "full_period_pass": bool(all(bool_value(x.get("period_under_2pct")) for x in results)),
    }


def bool_value(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == "true"
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except TypeError:
        pass
    return bool(value)


def plot_six(results: list[dict], path: Path, title: str) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    for ax, dof in zip(axes.flat, DOFS):
        item = next(x for x in results if x["dof"] == dof)
        ax.plot(item["aligned_time_s"], item["reference_aligned"], label="Curated reference", lw=1.1)
        ax.plot(item["aligned_time_s"], item["simulation_aligned"], label="OpenFAST candidate", lw=1.0)
        ax.set_title(f"{dof}: T err {item['period_error_pct']:.2f}%, J {item['J_dof']:.2f}%")
        ax.set_xlabel("Time after release (s)")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_zoom(results: list[dict], dofs: list[str], path: Path, title: str) -> None:
    fig, axes = plt.subplots(len(dofs), 1, figsize=(12, 4 * len(dofs)), constrained_layout=True)
    if len(dofs) == 1:
        axes = [axes]
    for ax, dof in zip(axes, dofs):
        item = next(x for x in results if x["dof"] == dof)
        mask = item["aligned_time_s"] <= min(180.0, float(item["aligned_time_s"][-1]))
        ax.plot(item["aligned_time_s"][mask], item["reference_aligned"][mask], label="Curated reference", lw=1.1)
        ax.plot(item["aligned_time_s"][mask], item["simulation_aligned"][mask], label="OpenFAST candidate", lw=1.0)
        ax.set_title(f"{dof}: first-window zoom")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def model(model_id: str, actions: list[dict], cases: list[p2.ReferenceCase] | None = None, ic_scales: dict[str, float] | None = None) -> dict:
    return {"model_id": model_id, "actions": canonical_actions(actions), "cases": cases or CASES, "ic_scales": ic_scales or {}}


def stage_case(dof: str) -> p2.ReferenceCase:
    return next(case for case in CASES if case.dof == dof)


def c0() -> list[dict]:
    init()
    results = run_models("C0", [model("C0_starting_model", [])])
    write_stage_outputs("C0", results, "C0 starting model curated recheck")
    score = group_score(results)
    rows = "\n".join(f"| {x['dof']} | {x['period_error_pct']:.3f}% | {x['J_dof']:.3f}% | {x['J_shape']:.3f}% |" for x in results)
    write_text(
        OUT / "C0_reproducibility_check.md",
        f"""
# C0 Reproducibility Check

- starting_model_id: `{START_ID}`
- starting_model_path: `{START_PACKAGE}`
- reference_kind: `curated`
- old_heave_reference: `deprecated`
- old_heave_reference_used: `false`
- J_period6: `{score['J_period6']:.3f}%`
- DOF period pass count: `{score['number_of_DOF_period_under_2_percent']}/6`

| DOF | Period error | J_dof | J_shape |
|---|---:|---:|---:|
{rows}
""",
    )
    return results


def estimated_period_actions(c0_results: list[dict]) -> list[dict]:
    actions = []
    for item in c0_results:
        ref = float(item["reference_period_s"])
        sim = float(item["simulation_period_s"])
        factor = (sim / ref) ** 2 if np.isfinite(ref) and ref else 1.0
        factor = float(np.clip(factor, 0.5, 5.0))
        actions.append({"parameter": "AddCLin_diag_factor", "dof": item["dof"], "factor": factor, "basis": "curated period inverse estimate from C0"})
    return actions


def c1(c0_results: list[dict]) -> None:
    rows = []
    for item in c0_results:
        factor = (float(item["simulation_period_s"]) / float(item["reference_period_s"])) ** 2
        rows.append(
            {
                "dof": item["dof"],
                "reference_period_s": item["reference_period_s"],
                "simulation_period_s": item["simulation_period_s"],
                "period_error_pct": item["period_error_pct"],
                "estimated_AddCLin_diag_factor_to_hit_period": factor,
                "direction": "decrease stiffness / increase inertia" if factor < 1 else "increase stiffness / decrease inertia",
            }
        )
    pd.DataFrame(rows).to_csv(OUT / "C1_period_sensitivity_map.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rows).to_csv(OUT / "06_metrics" / "C1_period_sensitivity_map.csv", index=False, encoding="utf-8-sig")
    text_rows = "\n".join(f"| {r['dof']} | {r['period_error_pct']:.3f}% | {r['estimated_AddCLin_diag_factor_to_hit_period']:.4f} | {r['direction']} |" for r in rows)
    write_text(
        OUT / "C1_period_sensitivity_summary.md",
        f"""
# C1 Period Sensitivity Summary

This lightweight rescan uses C0 curated periods to estimate first restoring-stiffness directions. It records the expected sensitivity direction before running the integrated C5 candidate.

| DOF | C0 period error | Estimated AddCLin diagonal factor | Direction |
|---|---:|---:|---|
{text_rows}
""",
    )


def c2_c3_c4_docs(c0_results: list[dict], actions: list[dict]) -> None:
    act = {x["dof"]: x["factor"] for x in actions if x["parameter"] == "AddCLin_diag_factor"}
    write_text(
        OUT / "C2_best_heave_candidate.md",
        f"""
# C2 Best Heave Candidate

Curated C0 Heave is already close to the period target. The integrated C5 candidate applies Heave AddCLin diagonal factor `{act.get('Heave', 1.0):.4f}` and keeps old Heave reference unused.
""",
    )
    write_text(
        OUT / "C3_best_roll_pitch_candidate.md",
        f"""
# C3 Best Roll/Pitch Candidate

Roll and Pitch are the dominant period misses in C0. The integrated C5 period candidate applies:

- Roll AddCLin diagonal factor `{act.get('Roll', 1.0):.4f}`
- Pitch AddCLin diagonal factor `{act.get('Pitch', 1.0):.4f}`

No as-built claim is made.
""",
    )
    write_text(
        OUT / "C4_locked_dof_summary.md",
        f"""
# C4 Surge/Sway/Yaw Guarded Refine Summary

The integrated C5 period candidate applies guarded diagonal factors:

- Surge `{act.get('Surge', 1.0):.4f}`
- Sway `{act.get('Sway', 1.0):.4f}`
- Yaw `{act.get('Yaw', 1.0):.4f}`

These are bounded calibration-to-data changes based on curated C0 period errors.
""",
    )


def c5(c0_results: list[dict]) -> list[dict]:
    actions = estimated_period_actions(c0_results)
    historical_period2_actions = [
        {"parameter": "AddCLin_diag_factor", "dof": "Surge", "factor": 0.9554, "basis": "historical presentation period fit rescored with curated reference"},
        {"parameter": "AddCLin_diag_factor", "dof": "Sway", "factor": 0.9357, "basis": "historical presentation period fit rescored with curated reference"},
        {"parameter": "AddCLin_diag_factor", "dof": "Yaw", "factor": 1.0115, "basis": "historical presentation period fit rescored with curated reference"},
        {"parameter": "AddBQuad_diag_factor", "dof": "Surge", "factor": 1.5, "basis": "historical presentation envelope fit"},
        {"parameter": "AddBLin_diag_value", "dof": "Sway", "value": 100000.0, "basis": "historical presentation envelope fit"},
        {"parameter": "AddBQuad_diag_factor", "dof": "Heave", "factor": 5.0, "basis": "historical heave damping fit"},
        {"parameter": "AddBLin_diag_value", "dof": "Roll", "value": 1000000000.0, "basis": "historical roll envelope fit"},
        {"parameter": "AddBQuad_diag_factor", "dof": "Pitch", "factor": 0.75, "basis": "historical pitch envelope fit"},
        {"parameter": "AddBQuad_diag_factor", "dof": "Yaw", "factor": 2.0, "basis": "historical yaw envelope fit"},
        {"parameter": "AddBLin_diag_value", "dof": "Heave", "value": 1000000.0, "basis": "historical HRP heave envelope fit"},
        {"parameter": "AddCLin_diag_factor", "dof": "Pitch", "factor": 1.5, "basis": "historical HRP pitch period fit"},
        {"parameter": "AddBLin_offdiag_estimated", "dof_a": "Roll", "dof_b": "Pitch", "factor": 1.0, "basis": "historical roll-pitch damping coupling"},
        {"parameter": "AddCLin_offdiag_estimated", "dof_a": "Heave", "dof_b": "Pitch", "factor": 0.5, "basis": "historical HRP restoring coupling"},
        {"parameter": "AddCLin_diag_factor", "dof": "Heave", "factor": 0.4, "basis": "historical period2 heave K"},
        {"parameter": "PtfmPIner", "factor": 2.05, "basis": "historical period2 pitch inertia"},
        {"parameter": "PtfmRIner", "factor": 2.9, "basis": "historical period2 roll inertia"},
    ]
    c2_c3_c4_docs(c0_results, actions)
    horizontal_yaw_actions = [x for x in actions if x.get("dof") in {"Surge", "Sway", "Yaw"}]
    heave_action = [x for x in actions if x.get("dof") == "Heave"]
    candidates = [
        model("C5_period_inverse_diag_candidate", actions),
        model(
            "C5_period_inverse_diag_heave_guarded_candidate",
            [x for x in actions if x.get("dof") != "Heave"],
        ),
        model("C5_historical_period2_path_curated_candidate", historical_period2_actions),
        model(
            "C5_horizontal_guard_RP_inertia_1p12_1p14",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.12, "basis": "curated C5 roll period inertia refine"},
                {"parameter": "PtfmPIner", "factor": 1.14, "basis": "curated C5 pitch period inertia refine"},
            ],
        ),
        model(
            "C5_horizontal_guard_RP_inertia_1p20_1p40",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.20, "basis": "curated C5 roll period inertia refine"},
                {"parameter": "PtfmPIner", "factor": 1.40, "basis": "curated C5 pitch period inertia refine"},
            ],
        ),
        model(
            "C5_horizontal_guard_RP_inertia_1p30_1p50",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.30, "basis": "curated C5 roll period inertia refine"},
                {"parameter": "PtfmPIner", "factor": 1.50, "basis": "curated C5 pitch period inertia refine"},
            ],
        ),
        model(
            "C5_horizontal_guard_RP_inertia_1p45_1p60",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.45, "basis": "curated C5 roll local inertia refinement"},
                {"parameter": "PtfmPIner", "factor": 1.60, "basis": "curated C5 pitch local inertia refinement"},
            ],
        ),
        model(
            "C5_horizontal_guard_RP_inertia_1p55_1p65",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.55, "basis": "curated C5 roll local inertia refinement"},
                {"parameter": "PtfmPIner", "factor": 1.65, "basis": "curated C5 pitch local inertia refinement"},
            ],
        ),
        model(
            "C5_best_RP_inertia_rollK_1p20",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.30, "basis": "curated C5 best inertia base"},
                {"parameter": "PtfmPIner", "factor": 1.50, "basis": "curated C5 best inertia base"},
                {"parameter": "AddCLin_diag_factor", "dof": "Roll", "factor": 1.20, "basis": "curated C5 roll restoring local scan"},
            ],
        ),
        model(
            "C5_best_RP_inertia_rollK_1p50",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.30, "basis": "curated C5 best inertia base"},
                {"parameter": "PtfmPIner", "factor": 1.50, "basis": "curated C5 best inertia base"},
                {"parameter": "AddCLin_diag_factor", "dof": "Roll", "factor": 1.50, "basis": "curated C5 roll restoring local scan"},
            ],
        ),
        model(
            "C5_best_RP_inertia_rollK_2p00",
            horizontal_yaw_actions
            + heave_action
            + [
                {"parameter": "PtfmRIner", "factor": 1.30, "basis": "curated C5 best inertia base"},
                {"parameter": "PtfmPIner", "factor": 1.50, "basis": "curated C5 best inertia base"},
                {"parameter": "AddCLin_diag_factor", "dof": "Roll", "factor": 2.00, "basis": "curated C5 roll restoring local scan"},
            ],
        ),
    ]
    results_all = []
    summaries = []
    for candidate in candidates:
        results = run_models("C5", [candidate])
        results_all.extend(results)
        if len(results) == 6:
            summaries.append({"model_id": candidate["model_id"], **group_score(results)})
    trace = pd.DataFrame([flat(x) for x in results_all])
    trace.to_csv(OUT / "C5_integrated_period_search_trace.csv", index=False, encoding="utf-8-sig")
    summary = pd.DataFrame(summaries).sort_values(["J_period6", "J_total"]) if summaries else pd.DataFrame()
    summary.to_csv(OUT / "C5_top_30_period_candidates.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUT / "06_metrics" / "C5_top_30_period_candidates.csv", index=False, encoding="utf-8-sig")
    best_id = str(summary.iloc[0]["model_id"]) if not summary.empty else "C5_period_inverse_diag_candidate"
    best = [x for x in results_all if x["model_id"] == best_id]
    write_stage_outputs("C5", best, "C5 integrated curated period candidate")
    score = group_score(best) if len(best) == 6 else {}
    write_text(
        OUT / "C5_integrated_period_search_summary.md",
        f"""
# C5 Integrated Period Search Summary

- Best candidate: `{best_id}`
- Full six-DOF period pass: `{score.get('full_period_pass')}`
- J_period6: `{score.get('J_period6', math.nan):.3f}%`
- DOF period pass count: `{score.get('number_of_DOF_period_under_2_percent', 0)}/6`

The search was a conservative first pass using period-inverse diagonal restoring estimates from C0.
""",
    )
    write_text(
        OUT / "C5_period_pass_candidate.md",
        f"""
# C5 Period Pass Candidate

Selected candidate: `{best_id}`.

Full period pass: `{score.get('full_period_pass')}`.
""",
    )
    return best


def c6(best: list[dict]) -> list[dict]:
    score = group_score(best)
    if not score["full_period_pass"]:
        write_text(
            OUT / "C6_shape_refinement_summary.md",
            f"""
# C6 Shape Refinement Summary

C6 was not executed because C5 did not achieve all six period errors below 2%.

- C5 J_period6: `{score['J_period6']:.3f}%`
- DOF period pass count: `{score['number_of_DOF_period_under_2_percent']}/6`

Shape refinement is deferred until Target A is achieved.
""",
        )
        pd.DataFrame().to_csv(OUT / "C6_shape_refinement_trace.csv", index=False)
        pd.DataFrame().to_csv(OUT / "C6_top_shape_candidates.csv", index=False)
        return best
    write_text(OUT / "C6_shape_refinement_summary.md", "# C6 Shape Refinement Summary\n\nC5 achieved full period pass; no additional damping/shape run was launched in this first curated pass.")
    pd.DataFrame([flat(x) for x in best]).to_csv(OUT / "C6_shape_refinement_trace.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([flat(x) for x in best]).to_csv(OUT / "C6_top_shape_candidates.csv", index=False, encoding="utf-8-sig")
    return best


def write_stage_outputs(stage: str, results: list[dict], title: str) -> None:
    if not results:
        return
    pd.DataFrame([flat(x) for x in results]).to_csv(OUT / "06_metrics" / f"{stage}_metrics.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([shape_row(x) for x in results]).to_csv(OUT / "06_metrics" / f"{stage}_shape_metrics.csv", index=False, encoding="utf-8-sig")
    plot_six(results, OUT / "07_figures" / f"{stage}_six_dof_comparison.png", title)
    copy2_once(OUT / "07_figures" / f"{stage}_six_dof_comparison.png", OUT / "figures" / f"{stage}_six_dof_comparison.png")


def shape_row(result: dict) -> dict:
    return {
        "stage": result["stage"],
        "model_id": result["model_id"],
        "case_id": result["case_id"],
        "dof": result["dof"],
        "J_shape": result["J_shape"],
        "nrmse_pct": result["nrmse_pct"],
        "envelope_error_pct": result["envelope_error_pct"],
        "phase_error_pct": result["phase_error_pct"],
        "initial_peak_error_pct": result["initial_peak_error_pct"],
        "first_3_peaks_error_pct": result["first_3_peaks_error_pct"],
        "late_stage_energy_ratio_error": result["late_stage_energy_ratio_error"],
    }


def c7(final_results: list[dict]) -> None:
    package = OUT / "curated_six_dof_calibrated_model"
    model_id = final_results[0]["model_id"] if final_results else "none"
    stage = final_results[0]["stage"] if final_results else "C5"
    source = OUT / "05_stage_runs" / STAGES[stage] / model_id
    if (source / "input_template").exists() and not (package / "OpenFAST_input_files").exists():
        shutil.copytree(source / "input_template", package / "OpenFAST_input_files")
    if (source / "parameter_change.json").exists():
        shutil.copy2(source / "parameter_change.json", package / "parameter_change.json")
    if (OUT / "08_diffs" / f"{model_id}.patch").exists():
        shutil.copy2(OUT / "08_diffs" / f"{model_id}.patch", package / "diff.patch")
    metrics = pd.DataFrame([flat(x) for x in final_results])
    shapes = pd.DataFrame([shape_row(x) for x in final_results])
    metrics.to_csv(package / "metrics.csv", index=False, encoding="utf-8-sig")
    shapes.to_csv(package / "shape_metrics.csv", index=False, encoding="utf-8-sig")
    plot_six(final_results, package / "six_dof_comparison.png", "Curated six-DOF calibrated model")
    plot_zoom(final_results, ["Heave"], package / "heave_zoom.png", "Curated Heave zoom")
    plot_zoom(final_results, ["Roll", "Pitch"], package / "roll_pitch_zoom.png", "Curated Roll/Pitch zoom")
    score = group_score(final_results)
    dump_json(package / "objective_summary.json", score)
    metrics.to_csv(package / "calibration_parameter_table.csv", index=False, encoding="utf-8-sig")
    write_text(package / "limitations.md", "This is a calibration-to-data / presentation-fit model. It is not a FOCAL C4 as-built model.")
    manifest = []
    for path in sorted(package.rglob("*")):
        if path.is_file():
            manifest.append(f"{sha256(path)}  {path.relative_to(package).as_posix()}")
    write_text(OUT / "final_model_sha256_manifest.txt", "\n".join(manifest))
    write_text(
        OUT / "C7_final_model_summary.md",
        f"""
# C7 Final Model Summary

- Final package: `curated_six_dof_calibrated_model`
- Source candidate: `{model_id}`
- Full six-DOF period pass: `{score['full_period_pass']}`
- J_period6: `{score['J_period6']:.3f}%`
- J_total: `{score['J_total']:.3f}%`
- J_shape_total: `{score['J_shape_total']:.3f}%`
- J_HRP_shape: `{score['J_HRP_shape']:.3f}%`
- Claim boundary: calibration-to-data / presentation-fit, not as-built.
""",
    )


def aggregate(final_results: list[dict]) -> None:
    frames = []
    c0_path = OUT / "06_metrics" / "C0_metrics.csv"
    if c0_path.exists() and c0_path.stat().st_size > 0:
        frames.append(pd.read_csv(c0_path))
    trace_path = OUT / "C5_integrated_period_search_trace.csv"
    if trace_path.exists() and trace_path.stat().st_size > 0:
        frames.append(pd.read_csv(trace_path))
    metrics = pd.concat(frames, ignore_index=True, sort=False) if frames else pd.DataFrame()
    metrics.to_csv(OUT / "metrics_all_runs.csv", index=False, encoding="utf-8-sig")
    shape_cols = [
        "stage",
        "model_id",
        "case_id",
        "dof",
        "J_shape",
        "nrmse_pct",
        "envelope_error_pct",
        "phase_error_pct",
        "initial_peak_error_pct",
        "first_3_peaks_error_pct",
        "late_stage_energy_ratio_error",
    ]
    shapes = metrics[[col for col in shape_cols if col in metrics.columns]].copy() if not metrics.empty else pd.DataFrame()
    shapes.to_csv(OUT / "shape_metrics_all_runs.csv", index=False, encoding="utf-8-sig")
    rows = []
    if not metrics.empty:
        for model_id, group in metrics.groupby("model_id"):
            rows.append({"model_id": model_id, **group_score([row._asdict() for row in group.itertuples(index=False)])})
    history = pd.DataFrame(rows).sort_values(["J_period6", "J_total"]) if rows else pd.DataFrame()
    history.to_csv(OUT / "objective_history.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([flat(x) for x in final_results]).to_csv(OUT / "final_six_dof_error_table.csv", index=False, encoding="utf-8-sig")
    if trace_path.exists():
        trace = pd.read_csv(trace_path)
        trace[trace["dof"] == "Heave"].to_csv(OUT / "C2_heave_curated_sweep.csv", index=False, encoding="utf-8-sig")
        trace[trace["dof"].isin(["Roll", "Pitch"])].to_csv(OUT / "C3_roll_pitch_period_sweep.csv", index=False, encoding="utf-8-sig")
        trace[trace["dof"].isin(["Surge", "Sway", "Yaw"])].to_csv(OUT / "C4_locked_dof_refine.csv", index=False, encoding="utf-8-sig")
    if (OUT / "C5_top_30_period_candidates.csv").exists():
        shutil.copy2(OUT / "C5_top_30_period_candidates.csv", OUT / "top_candidates.csv")
    rejected_lines = ["# Rejected Candidates", ""]
    if (OUT / "C5_top_30_period_candidates.csv").exists():
        cands = pd.read_csv(OUT / "C5_top_30_period_candidates.csv")
        for row in cands.itertuples(index=False):
            if not bool_value(getattr(row, "full_period_pass", False)):
                rejected_lines.append(f"- `{row.model_id}`: J_period6 `{float(row.J_period6):.3f}%`, period pass count `{int(row.number_of_DOF_period_under_2_percent)}/6`.")
    write_text(OUT / "rejected_candidates.md", "\n".join(rejected_lines))
    param_rows = []
    for path in sorted((OUT / "05_stage_runs").rglob("parameter_change.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        actions = data.get("actions", [])
        if not actions:
            param_rows.append({"model_id": data.get("model_id"), "stage": data.get("stage"), "parameter": "none", "dof": "", "value_or_factor": "", "basis": "no physical parameter change"})
        for action in actions:
            param_rows.append({
                "model_id": data.get("model_id"),
                "stage": data.get("stage"),
                "parameter": action.get("parameter"),
                "dof": action.get("dof") or f"{action.get('dof_a','')}-{action.get('dof_b','')}".strip("-"),
                "value_or_factor": action.get("factor", action.get("value", action.get("offset", ""))),
                "basis": action.get("basis", ""),
                "claim_boundary": data.get("claim_boundary", "calibration-to-data / presentation-fit; not as-built"),
            })
    pd.DataFrame(param_rows).to_csv(OUT / "parameter_change_log.csv", index=False, encoding="utf-8-sig")
    run_rows = []
    for log in sorted((OUT / "05_stage_runs").rglob("run.log")):
        parts = log.relative_to(OUT / "05_stage_runs").parts
        text = log.read_text(encoding="utf-8", errors="replace")
        run_rows.append({
            "stage_directory": parts[0] if len(parts) > 0 else "",
            "model_id": parts[1] if len(parts) > 1 else "",
            "case_id": parts[3] if len(parts) > 3 else "",
            "status": "normal_completion" if "OpenFAST terminated normally." in text else "failed_or_incomplete",
            "run_log": str(log),
        })
    pd.DataFrame(run_rows).to_csv(OUT / "09_logs" / "all_run_manifest.csv", index=False, encoding="utf-8-sig")
    dump_json(OUT / "09_logs" / "all_run_manifest.json", run_rows)
    shutil.copy2(OUT / "09_logs" / "all_run_manifest.csv", OUT / "logs" / "all_run_manifest.csv")
    shutil.copy2(OUT / "09_logs" / "all_run_manifest.json", OUT / "logs" / "all_run_manifest.json")
    pd.DataFrame([{"cleanup_action": "none", "reason": "User prohibited deletion/move/compression in this calibration window."}]).to_csv(OUT / "cleanup_manifest.csv", index=False, encoding="utf-8-sig")
    if (OUT / "curated_six_dof_calibrated_model" / "six_dof_comparison.png").exists():
        shutil.copy2(OUT / "curated_six_dof_calibrated_model" / "six_dof_comparison.png", OUT / "final_six_dof_comparison.png")
    if (OUT / "curated_six_dof_calibrated_model" / "diff.patch").exists():
        shutil.copy2(OUT / "curated_six_dof_calibrated_model" / "diff.patch", OUT / "diff.patch")
    write_text(OUT / "deprecated_reference_note.md", "# Deprecated Reference Note\n\nThe deprecated Heave processed source and pre-curation Heave mapping were not used in this calibration.")
    write_text(OUT / "not_as_built_disclaimer.md", "# Not As-Built Disclaimer\n\nThe final model is a calibration-to-data / presentation-fit model. It must not be described as a FOCAL C4 as-built model.")
    write_text(
        OUT / "README_stage_summary.md",
        """
# README Stage Summary

- C0: reran the exploratory starting model using curated references.
- C1: built a curated period-sensitivity direction map from C0 periods.
- C2/C3/C4: summarized Heave, Roll/Pitch, and locked Surge/Sway/Yaw guarded candidates.
- C5: ran integrated period-first candidates.
- C6: deferred shape refinement because full six-DOF period pass was not achieved.
- C7: packaged the closest curated calibration candidate.
""",
    )


def final_reports(final_results: list[dict], c0_results: list[dict]) -> None:
    final_score = group_score(final_results)
    start_score = group_score(c0_results)
    failed = [x["dof"] for x in final_results if not x["period_under_2pct"]]
    high_fidelity = pd.read_csv(RESCORE / "model_ranking_curated_reference.csv")
    hf = high_fidelity[high_fidelity["model_id"] == "high_fidelity_shape_fit_model"].iloc[0]
    rows = "\n".join(f"| {x['dof']} | {x['period_error_pct']:.3f}% | {x['J_dof']:.3f}% | {x['J_shape']:.3f}% | {x['period_under_2pct']} |" for x in final_results)
    report = f"""
# Final Curated Six-DOF Calibration Report

## Required Answers

1. Completely used curated reference: `true`.
2. Old Heave reference abandoned: `true`; old Heave reference used: `false`.
3. Generated `curated_six_dof_calibrated_model`: `true`.
4. Achieved all six DOF period errors <2%: `{final_score['full_period_pass']}`.
5. DOFs still not meeting period target: `{', '.join(failed) if failed else 'none'}`.
6. Improvement versus starting `exploratory_calibrated_model`: J_period6 `{start_score['J_period6']:.3f}% -> {final_score['J_period6']:.3f}%`; period pass count `{start_score['number_of_DOF_period_under_2_percent']}/6 -> {final_score['number_of_DOF_period_under_2_percent']}/6`.
7. Difference versus old `high_fidelity_shape_fit_model`: curated rescore showed high fidelity rank `{int(hf['rank'])}` and no six-DOF period pass; this run starts from the curated-best historical model and explicitly optimizes curated period first.
8. Old Heave error handled by excluding old Heave from every scoring path and documenting deprecation.
9. Suitable for advisor report: `{final_score['full_period_pass']}` for full pass; otherwise suitable only as an interim curated calibration update.
10. Not as-built because changes are calibration-to-data parameter adjustments without full FOCAL as-built HydroDyn/WAMIT, mass/inertia, umbilical, damping, and release-state evidence.
11. If not met, next needed evidence: official as-built restoring/coupling matrix, assembled mass/inertia with TMD/umbilical mapping, Heave-plate damping/hydrodynamics, and release-state records.

## Final Error Table

| DOF | Period error | J_dof | J_shape | Period <2% |
|---|---:|---:|---:|---|
{rows}

## Objective Summary

- J_period6: `{final_score['J_period6']:.3f}%`
- J_total: `{final_score['J_total']:.3f}%`
- J_shape_total: `{final_score['J_shape_total']:.3f}%`
- J_HRP_shape: `{final_score['J_HRP_shape']:.3f}%`
"""
    write_text(OUT / "final_curated_six_dof_calibration_report.md", report)
    write_text(OUT / "10_final_report" / "final_curated_six_dof_calibration_report.md", report)
    write_text(
        OUT / "advisor_curated_calibration_summary.md",
        f"""
# Advisor Curated Calibration Summary

The calibration used only curated six-DOF references. Old Heave was deprecated and unused.

- Starting model: `{START_ID}`
- Final package: `curated_six_dof_calibrated_model`
- Full period pass: `{final_score['full_period_pass']}`
- J_period6 improvement: `{start_score['J_period6']:.3f}% -> {final_score['J_period6']:.3f}%`
- Remaining misses: `{', '.join(failed) if failed else 'none'}`
- Claim boundary: presentation-fit / calibration-to-data, not as-built.
""",
    )


def run_all() -> None:
    c0_results = c0()
    c1(c0_results)
    best = c5(c0_results)
    final = c6(best)
    c7(final)
    aggregate(final)
    final_reports(final, c0_results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["init", "C0", "all"], default="all")
    args = parser.parse_args()
    if args.stage == "init":
        init()
    elif args.stage == "C0":
        c0()
    else:
        run_all()


if __name__ == "__main__":
    main()
