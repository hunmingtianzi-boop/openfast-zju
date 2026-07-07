from __future__ import annotations

import argparse
import csv
import difflib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, welch


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "phase2_15mw_free_decay_diagnosis"
MODEL_ROOT = ROOT / "01_IEA15MW_标准参考模型" / "OpenFAST"
MODEL_DIR = MODEL_ROOT / "IEA-15-240-RWT-UMaineSemi"
SHARED_RWT = MODEL_ROOT / "IEA-15-240-RWT"
EXE = MODEL_DIR / "OpenFAST_Release.exe"
FST_NAME = "IEA-15-240-RWT-UMaineSemi.fst"
ED_NAME = "IEA-15-240-RWT-UMaineSemi_ElastoDyn.dat"
HD_NAME = "IEA-15-240-RWT-UMaineSemi_HydroDyn.dat"
SEA_NAME = "IEA-15-240-RWT-UMaineSemi_SeaState.dat"
DOFS = ["Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw"]
OUT_CHANNELS = {d: "Ptfm" + d for d in DOFS}


@dataclass(frozen=True)
class ReferenceCase:
    case_id: str
    dof: str
    csv_path: Path
    excitation_unit: str


CASES = [
    ReferenceCase(
        "FD_SURGE",
        "Surge",
        ROOT / "数据" / "fd01_Surge" / "order_70a745fa0ea84dfcab796718f"
        / "focal.campaign4.m02d01t00.fd01.e00w00.r01.z1.a1.csv",
        "m",
    ),
    ReferenceCase(
        "FD_SWAY",
        "Sway",
        ROOT / "数据" / "fd05_Sway" / "focal.campaign4.m02d01t00.fd05.e00w00.r01.z1.a1.csv",
        "m",
    ),
    ReferenceCase(
        "FD_HEAVE",
        "Heave",
        ROOT / "数据" / "fd04_Heave" / "focal.campaign4.m02d01t00.fd04.e00w00.r01.z1.a1.csv",
        "m",
    ),
    ReferenceCase(
        "FD_ROLL",
        "Roll",
        ROOT / "数据" / "fd06_Roll" / "focal.campaign4.m02d01t00.fd06.e00w00.r03.z1.a1.csv",
        "deg",
    ),
    ReferenceCase(
        "FD_PITCH",
        "Pitch",
        ROOT / "数据" / "fd02_Pitch" / "focal.campaign4.m02d01t00.fd02.e00w00.r01.z1.a1.csv",
        "deg",
    ),
    ReferenceCase(
        "FD_YAW",
        "Yaw",
        ROOT / "数据" / "fd03_Yaw" / "order_d3abb791b1294301abbc88db3"
        / "focal.campaign4.m02d01t00.fd03.e00w00.r01.z1.a1.csv",
        "deg",
    ),
]


def json_dump(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8")


def read_reference(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, skiprows=[1])


def prepare_reference(case: ReferenceCase) -> tuple[pd.DataFrame, dict]:
    df = read_reference(case.csv_path)
    x = df[case.dof].to_numpy(dtype=float)
    tail = max(100, int(0.2 * len(x)))
    equilibrium = float(np.nanmedian(x[-tail:]))
    centered = x - equilibrium
    release_idx = int(np.nanargmax(np.abs(centered)))
    processed = df.loc[release_idx:, ["Time"] + DOFS].copy()
    processed["Time"] = processed["Time"] - float(processed["Time"].iloc[0])
    for dof in DOFS:
        dof_tail = max(100, int(0.2 * len(processed)))
        processed[dof] = processed[dof] - float(np.nanmedian(processed[dof].iloc[-dof_tail:]))
    dt = float(np.nanmedian(np.diff(df["Time"].to_numpy(dtype=float))))
    info = {
        "case_id": case.case_id,
        "main_dof": case.dof,
        "source_file": str(case.csv_path),
        "source_class": "experiment",
        "column_units": {"Time": "s", "Surge": "m", "Sway": "m", "Heave": "m", "Roll": "deg", "Pitch": "deg", "Yaw": "deg"},
        "sample_count_raw": int(len(df)),
        "sampling_interval_s": dt,
        "sampling_frequency_hz": 1.0 / dt,
        "release_detection": "maximum absolute displacement relative to tail median in the excited DOF",
        "release_time_s_raw": float(df["Time"].iloc[release_idx]),
        "tail_equilibrium_main_dof": equilibrium,
        "initial_displacement_for_simulation": float(processed[case.dof].iloc[0]),
        "processed_sample_count": int(len(processed)),
        "processing": ["select post-release segment", "shift release time to 0 s", "subtract per-channel tail median"],
        "filtering": "none",
        "missing_values": bool(df[["Time"] + DOFS].isna().any().any()),
    }
    return processed, info


def replace_key(path: Path, key: str, value: str | float | int) -> None:
    text = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    found = False
    result: list[str] = []
    for line in text:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            prefix_len = len(line) - len(line.lstrip())
            tail = line[line.find(key):]
            result.append(" " * prefix_len + str(value) + "   " + tail)
            found = True
        else:
            result.append(line)
    if not found:
        raise KeyError(f"Parameter {key} not found in {path}")
    path.write_text("".join(result), encoding="utf-8")


def patch_matrix_value(path: Path, marker: str, row: int, col: int, factor: float) -> tuple[float, float]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if marker in line)
    idx = start + row - 1
    tokens = lines[idx].split()
    old = float(tokens[col - 1])
    new = old * factor
    tokens[col - 1] = f"{new:.8E}"
    suffix = ""
    if marker in lines[idx]:
        suffix = "   " + lines[idx][lines[idx].find(marker):].rstrip("\r\n")
    lines[idx] = " ".join(tokens[:6]) + suffix + "\n"
    path.write_text("".join(lines), encoding="utf-8")
    return old, new


def patch_moordyn_ea(path: Path, factor: float) -> tuple[float, float]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    for i, line in enumerate(lines):
        tokens = line.split()
        if tokens and tokens[0] == "main" and len(tokens) >= 4:
            old = float(tokens[3])
            new = old * factor
            tokens[3] = f"{new:.8E}"
            lines[i] = "  ".join(tokens) + "\n"
            path.write_text("".join(lines), encoding="utf-8")
            return old, new
    raise KeyError(f"MoorDyn main line type not found in {path}")


def copy_case_input(run_dir: Path) -> Path:
    input_dir = run_dir / "input_files"
    if input_dir.exists():
        return input_dir
    shutil.copytree(MODEL_DIR, input_dir, ignore=shutil.ignore_patterns("*.out", "*.outb", "*.sum", "*.ech", "*.log"))
    shutil.copytree(SHARED_RWT, run_dir / "IEA-15-240-RWT")
    return input_dir


def configure_free_decay(input_dir: Path, case: ReferenceCase, release_value: float) -> list[dict]:
    changes = []
    fst = input_dir / FST_NAME
    ed = input_dir / ED_NAME
    sea = input_dir / SEA_NAME
    setup = [
        (fst, "TMax", 600),
        (fst, "DT_Out", 0.05),
        (fst, "OutFileFmt", 1),
        (fst, "CompInflow", 0),
        (fst, "CompAero", 0),
        (fst, "CompServo", 0),
        (sea, "WaveMod", 0),
        (sea, "WaveHs", 0.0),
        (sea, "WaveTp", 1.0),
    ]
    for path, key, val in setup:
        replace_key(path, key, val)
        changes.append({"classification": "test_configuration", "file": path.name, "parameter": key, "new_value": val})
    for dof in DOFS:
        val = release_value if dof == case.dof else 0.0
        replace_key(ed, "Ptfm" + dof, val)
        changes.append({"classification": "initial_condition", "file": ed.name, "parameter": "Ptfm" + dof, "new_value": val})
    return changes


def parse_openfast_output(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.strip().startswith("Time") and "Ptfm" in line)
    names = []
    occurrences: dict[str, int] = {}
    for raw_name in lines[header_idx].split():
        count = occurrences.get(raw_name, 0)
        names.append(raw_name if count == 0 else f"{raw_name}_{count}")
        occurrences[raw_name] = count + 1
    return pd.read_csv(path, sep=r"\s+", skiprows=header_idx + 2, names=names)


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
    decrements = []
    for indices in (max_idx, min_idx):
        amps = np.abs(centered[indices])
        if len(amps) > 1:
            ratios = amps[:-1] / np.maximum(amps[1:], 1e-12)
            decrements.extend(np.log(ratios[ratios > 1]).tolist())
    delta = float(np.nanmedian(decrements)) if decrements else math.nan
    zeta = delta / math.sqrt((2 * math.pi) ** 2 + delta**2) if np.isfinite(delta) else math.nan
    freq, power = welch(centered, fs=1.0 / dt, nperseg=min(len(centered), 4096))
    positive = freq > 0
    dominant_freq = float(freq[positive][np.nanargmax(power[positive])]) if positive.any() else math.nan
    peak_rows = [
        {"time_s": float(time[i]), "amplitude": float(abs(centered[i])), "kind": kind}
        for i, kind in peaks
    ]
    return {
        "period_s": period,
        "damping_ratio": zeta,
        "dominant_frequency_hz": dominant_freq,
        "peak_count": len(peak_rows),
        "first_peaks": peak_rows[:8],
        "rms": float(np.sqrt(np.nanmean(centered**2))),
        "peak_abs": float(np.nanmax(np.abs(centered))),
    }


def error_pct(sim: float, ref: float) -> float:
    return abs(sim - ref) / max(abs(ref), 1e-12) * 100.0 if np.isfinite(sim) and np.isfinite(ref) else math.nan


def compare_signals(case: ReferenceCase, ref: pd.DataFrame, sim: pd.DataFrame, run_id: str) -> dict:
    channel = OUT_CHANNELS[case.dof]
    t_ref = ref["Time"].to_numpy(dtype=float)
    y_ref = ref[case.dof].to_numpy(dtype=float)
    t_sim = sim["Time"].to_numpy(dtype=float)
    y_sim = sim[channel].to_numpy(dtype=float)
    y_sim = y_sim - float(np.nanmedian(y_sim[-max(20, int(len(y_sim) * 0.2)):]))
    duration = min(float(t_ref[-1]), float(t_sim[-1]), 600.0)
    grid = t_ref[t_ref <= duration]
    r = y_ref[: len(grid)]
    s = np.interp(grid, t_sim, y_sim)
    ref_m = signal_metrics(grid, r)
    sim_m = signal_metrics(grid, s)
    n = min(len(ref_m["first_peaks"]), len(sim_m["first_peaks"]), 5)
    amp_e = (
        float(np.mean([error_pct(sim_m["first_peaks"][i]["amplitude"], ref_m["first_peaks"][i]["amplitude"]) for i in range(n)]))
        if n else math.nan
    )
    phase_e = (
        float(np.mean([abs(sim_m["first_peaks"][i]["time_s"] - ref_m["first_peaks"][i]["time_s"]) for i in range(n)])
              / max(ref_m["period_s"], 1e-12) * 100.0)
        if n and np.isfinite(ref_m["period_s"]) else math.nan
    )
    rmse = float(np.sqrt(np.nanmean((s - r) ** 2)))
    nrmse = rmse / max(float(np.nanmax(r) - np.nanmin(r)), 1e-12) * 100.0
    errors = {
        "period_error_pct": error_pct(sim_m["period_s"], ref_m["period_s"]),
        "damping_error_pct": error_pct(sim_m["damping_ratio"], ref_m["damping_ratio"]),
        "amplitude_error_pct": amp_e,
        "phase_error_pct_of_period": phase_e,
        "frequency_error_pct": error_pct(sim_m["dominant_frequency_hz"], ref_m["dominant_frequency_hz"]),
        "rmse": rmse,
        "nrmse_pct": nrmse,
    }
    weights = {
        "Surge": (0.45, 0.20, 0.15, 0.10, 0.10),
        "Sway": (0.45, 0.20, 0.15, 0.10, 0.10),
        "Heave": (0.35, 0.25, 0.20, 0.10, 0.10),
        "Roll": (0.30, 0.40, 0.15, 0.05, 0.10),
        "Pitch": (0.30, 0.40, 0.15, 0.05, 0.10),
        "Yaw": (0.35, 0.25, 0.20, 0.10, 0.10),
    }[case.dof]
    components = [errors[k] for k in ["period_error_pct", "damping_error_pct", "amplitude_error_pct", "phase_error_pct_of_period", "frequency_error_pct"]]
    objective = float(sum(w * e for w, e in zip(weights, components))) if all(np.isfinite(components)) else math.nan
    return {
        "run_id": run_id,
        "case_id": case.case_id,
        "dof": case.dof,
        "reference_metrics": ref_m,
        "simulation_metrics": sim_m,
        **errors,
        "objective_pct": objective,
        "error_i_definition": "weighted objective percentage from period, damping, first-five-peak amplitude, peak-time phase, and Welch dominant frequency errors",
        "pass_lt_2pct": bool(np.isfinite(objective) and objective < 2.0),
        "aligned_time_s": grid,
        "reference_aligned": r,
        "simulation_aligned": s,
    }


def run_openfast(input_dir: Path, log_path: Path) -> tuple[int, Path]:
    command = [str(input_dir / "OpenFAST_Release.exe"), FST_NAME]
    started = datetime.now().isoformat(timespec="seconds")
    proc = subprocess.run(command, cwd=input_dir, capture_output=True, text=True, errors="replace")
    log_path.write_text(
        f"started_at: {started}\ncommand: {command}\nreturncode: {proc.returncode}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}",
        encoding="utf-8",
    )
    return proc.returncode, input_dir / Path(FST_NAME).with_suffix(".out").name


def write_diff(original_dir: Path, input_dir: Path, out_path: Path) -> None:
    patches = []
    for name in [FST_NAME, ED_NAME, HD_NAME, SEA_NAME]:
        old = (original_dir / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        new = (input_dir / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        patches.extend(difflib.unified_diff(old, new, fromfile=f"original/{name}", tofile=f"input_files/{name}"))
    out_path.write_text("".join(patches), encoding="utf-8")


def ensure_structure() -> None:
    for rel in [
        "00_reference_data/raw", "00_reference_data/processed",
        "01_baseline_model_backup", "02_baseline_run", "03_experiment_runs/A_surge_mooring_or_mass",
        "03_experiment_runs/B_pitch_damping_or_tmd", "03_experiment_runs/C_time_alignment_or_initial_condition",
        "03_experiment_runs/D_yaw_mooring_geometry", "03_experiment_runs/other_hypotheses",
        "04_metrics", "05_figures/controlled_experiment_figures", "06_logs", "07_final_report",
    ]:
        (OUT / rel).mkdir(parents=True, exist_ok=True)


def baseline() -> None:
    ensure_structure()
    backup = OUT / "01_baseline_model_backup" / "original_openfast_model"
    if not backup.exists():
        shutil.copytree(MODEL_ROOT, backup, ignore=shutil.ignore_patterns("*.out", "*.outb", "*.sum", "*.ech", "*.log"))
    reference_summary = []
    processing_log = []
    results = []
    run_log = []
    for case in CASES:
        processed, info = prepare_reference(case)
        raw_dst = OUT / "00_reference_data" / "raw" / case.csv_path.name
        if not raw_dst.exists():
            shutil.copy2(case.csv_path, raw_dst)
        processed_path = OUT / "00_reference_data" / "processed" / f"{case.case_id}_processed.csv"
        processed.to_csv(processed_path, index=False)
        reference_summary.append(info)
        processing_log.append({"case_id": case.case_id, "source": str(case.csv_path), "derived_output": str(processed_path), "operations": info["processing"], "filtering": "none", "source_modified": False})
        run_dir = OUT / "02_baseline_run" / case.case_id
        input_dir = copy_case_input(run_dir)
        changes = configure_free_decay(input_dir, case, info["initial_displacement_for_simulation"])
        write_diff(MODEL_DIR, input_dir, run_dir / "input_diff.patch")
        json_dump(run_dir / "parameter_change.json", {"run_id": f"BASELINE_{case.case_id}", "physical_parameter_changes": [], "test_configuration_changes": changes, "note": "No baseline physical model coefficient modified."})
        returncode, output = run_openfast(input_dir, run_dir / "openfast_run.log")
        output_dir = run_dir / "output_files"
        output_dir.mkdir(exist_ok=True)
        if output.exists():
            shutil.copy2(output, output_dir / output.name)
        status = "ok" if returncode == 0 and output.exists() else "failed"
        run_log.append({"run_id": f"BASELINE_{case.case_id}", "status": status, "returncode": returncode, "output": str(output_dir / output.name)})
        if status == "ok":
            sim = parse_openfast_output(output)
            result = compare_signals(case, processed, sim, f"BASELINE_{case.case_id}")
            pd.DataFrame({k: v for k, v in result.items() if not isinstance(v, (dict, np.ndarray))}, index=[0]).to_csv(run_dir / "metrics.csv", index=False)
            json_dump(run_dir / "decision.json", {"run_id": f"BASELINE_{case.case_id}", "decision": "baseline_observation", "objective_pct": result["objective_pct"], "pass_lt_2pct": result["pass_lt_2pct"]})
            results.append(result)
    json_dump(OUT / "00_reference_data" / "reference_data_summary.json", reference_summary)
    json_dump(OUT / "06_logs" / "data_processing_log.json", processing_log)
    json_dump(OUT / "06_logs" / "baseline_run_manifest.json", run_log)
    pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, (dict, np.ndarray))} for r in results]).to_csv(OUT / "04_metrics" / "metrics_baseline.csv", index=False)
    pd.DataFrame([{k: v for k, v in r.items() if not isinstance(v, (dict, np.ndarray))} for r in results]).to_csv(OUT / "02_baseline_run" / "baseline_metrics.csv", index=False)
    plot_comparison(results, OUT / "05_figures" / "baseline_vs_reference_6dof.png", "Baseline vs FOCAL Reference")
    (OUT / "02_baseline_run" / "baseline_run_log.txt").write_text(json.dumps(run_log, indent=2, ensure_ascii=False), encoding="utf-8")
    write_initial_logs(reference_summary, run_log)


def plot_comparison(results: list[dict], path: Path, title: str) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    for ax, dof, result in zip(axes.flat, DOFS, sorted(results, key=lambda r: DOFS.index(r["dof"]))):
        ax.plot(result["aligned_time_s"], result["reference_aligned"], label="Experiment", lw=1.1)
        ax.plot(result["aligned_time_s"], result["simulation_aligned"], label="OpenFAST", lw=1.0)
        ax.set_title(f"{dof}: J={result['objective_pct']:.2f}%")
        ax.set_xlabel("Time after release (s)")
        ax.set_ylabel("m" if dof in {"Surge", "Sway", "Heave"} else "deg")
        ax.grid(alpha=0.25)
        ax.legend()
    fig.suptitle(title)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def write_initial_logs(reference_summary: list[dict], run_log: list[dict]) -> None:
    model_info = {
        "model_source": str(MODEL_DIR),
        "model_family": "IEA-15-240-RWT-UMaineSemi",
        "openfast_executable": str(EXE),
        "main_fst": FST_NAME,
        "baseline_backup": str(OUT / "01_baseline_model_backup" / "original_openfast_model"),
        "physical_model_parameters_preserved_in_baseline": True,
        "baseline_test_configuration": {
            "free_decay_condition": "still water, no aero, no servo, MoorDyn retained",
            "simulation_time_s": 600,
            "output_time_step_s": 0.05,
        },
        "known_parameters": {
            "water_depth_m": 200,
            "platform_mass_kg": 1.7838e7,
            "platform_roll_inertia_kg_m2": 1.2507e10,
            "platform_pitch_inertia_kg_m2": 1.2507e10,
            "platform_yaw_inertia_kg_m2": 2.3667e10,
            "mooring_module": "MoorDyn",
            "mooring_unstretched_length_m": 850.0,
            "mooring_line_EA_N": 3.27e9,
        },
        "uncertainties": [
            "The provided workspace does not contain the FOCAL laboratory configuration report.",
            "FOCAL-specific umbilical and TMD representation is unknown in this baseline.",
            "Release detection is derived from measured displacement peaks and must be reviewed.",
        ],
        "external_context_consulted": "D:/OpenFast/IEA15MW_Full_Package_With_Experimental_Data/03_OpenFAST_自由衰减标定平台 metadata identifies FOCAL Campaign 4 as 1:70 and states supplied series are full-scale converted; it is not used as simulation evidence.",
    }
    json_dump(OUT / "06_logs" / "model_provenance.json", model_info)
    quality_lines = [
        "# Data Quality Report", "",
        "The six selected FOCAL CSV files provide Time plus all six platform DOF channels and explicit units.",
        "Each main-DOF series is processed by selecting the detected release peak, shifting that time to zero, and subtracting a tail median; no filter is applied.",
        "",
        "| Case | Main DOF | Sampling Hz | Release time (raw s) | Initial displacement | Missing values |",
        "|---|---|---:|---:|---:|---|",
    ]
    for item in reference_summary:
        quality_lines.append(
            f"| {item['case_id']} | {item['main_dof']} | {item['sampling_frequency_hz']:.4f} | "
            f"{item['release_time_s_raw']:.3f} | {item['initial_displacement_for_simulation']:.5g} | {item['missing_values']} |"
        )
    quality_lines.extend(["", "## Limitations", "", "- A single representative high-sampling run is used for each release DOF in this bounded pass.", "- Experimental TMD, umbilical, exact release apparatus, and model-to-test configuration correspondence remain to be verified by human review."])
    (OUT / "00_reference_data" / "data_quality_report.md").write_text("\n".join(quality_lines), encoding="utf-8")
    metrics_definition = """# Metrics Definition

For each DOF, a representative release test is compared with a dedicated OpenFAST free-decay run.

- `period_error_pct`: relative error of median same-sign peak spacing.
- `damping_error_pct`: relative error of median logarithmic-decrement damping ratio.
- `amplitude_error_pct`: mean relative error of up to the first five absolute detected peaks.
- `phase_error_pct_of_period`: mean peak-time offset divided by reference period.
- `frequency_error_pct`: relative error of the dominant Welch spectral peak.
- `nrmse_pct`: time-domain RMSE normalized by the reference peak-to-peak range.
- `objective_pct` (`error_i`): DOF-weighted sum of period, damping, amplitude, phase, and frequency errors from the task specification.

No filtering is applied. The processed reference data are derived copies; raw experimental files are not changed.
"""
    (OUT / "04_metrics" / "metrics_definition.md").write_text(metrics_definition, encoding="utf-8")
    pd.DataFrame([x for x in run_log if x["status"] != "ok"]).to_csv(OUT / "06_logs" / "failed_runs_log.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["baseline"])
    args = parser.parse_args()
    if args.action == "baseline":
        baseline()


if __name__ == "__main__":
    main()
