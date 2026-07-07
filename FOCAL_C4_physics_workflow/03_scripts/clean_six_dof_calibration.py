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
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks, hilbert, welch


ROOT = Path(__file__).resolve().parents[1]
START_PACKAGE = ROOT / "02_starting_model" / "best_reproducible_model"
START_INPUTS = START_PACKAGE / "OpenFAST_input_files"
REFERENCE_DIR = ROOT / "00_reference" / "curated_six_dof_reference" / "processed"
RUN_ROOT = ROOT / "04_current_runs" / "six_dof_free_decay_calibration"
METRICS_DIR = ROOT / "05_metrics"
FIG_DIR = ROOT / "06_figures"
REPORT_DIR = ROOT / "07_reports"
LOG_DIR = ROOT / "08_logs"

FST = "IEA-15-240-RWT-UMaineSemi.fst"
ED = "IEA-15-240-RWT-UMaineSemi_ElastoDyn.dat"
HD = "IEA-15-240-RWT-UMaineSemi_HydroDyn.dat"
SEA = "IEA-15-240-RWT-UMaineSemi_SeaState.dat"
SHARED_RWT = ROOT / "01_baseline" / "original_baseline_model" / "IEA-15-240-RWT"
DOFS = ["Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw"]
IDX = {d: i for i, d in enumerate(DOFS)}
UNITS = {"Surge": "m", "Sway": "m", "Heave": "m", "Roll": "deg", "Pitch": "deg", "Yaw": "deg"}
CASES = {
    "Surge": "FD_SURGE",
    "Sway": "FD_SWAY",
    "Heave": "FD_HEAVE",
    "Roll": "FD_ROLL",
    "Pitch": "FD_PITCH",
    "Yaw": "FD_YAW",
}
OPENFAST_TIMEOUT_S = 300
MAX_WORKERS = 6

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


@dataclass(frozen=True)
class Candidate:
    model_id: str
    clin_diag_factors: dict[str, float]
    inertia_factors: dict[str, float] | None = None
    blin_diag_values: dict[str, float] | None = None
    bquad_diag_factors: dict[str, float] | None = None
    release_scales: dict[str, float] | None = None
    basis: str = "period-first calibration"


def ensure_dirs() -> None:
    for path in [RUN_ROOT, METRICS_DIR, FIG_DIR, REPORT_DIR, LOG_DIR, RUN_ROOT / "models"]:
        path.mkdir(parents=True, exist_ok=True)


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


def sha_manifest(src: Path, output: Path, header: list[str]) -> None:
    lines = header + [""]
    for path in sorted(p for p in src.rglob("*") if p.is_file()):
        lines.append(f"{sha256(path)}  {path.relative_to(src).as_posix()}")
    write_text(output, "\n".join(lines))


def read_matrix(path: Path, marker: str) -> np.ndarray:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    return np.array([[float(x) for x in lines[start + row].split()[:6]] for row in range(6)])


def write_matrix(path: Path, marker: str, matrix: np.ndarray) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if marker in line)
    suffix = "   " + lines[start][lines[start].find(marker) :].rstrip("\r\n")
    for row in range(6):
        lines[start + row] = " ".join(f"{matrix[row, col]:.8E}" for col in range(6)) + (suffix if row == 0 else "") + "\n"
    path.write_text("".join(lines), encoding="utf-8")


def replace_key(path: Path, key: str, value: str | float | int) -> None:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    out: list[str] = []
    found = False
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            prefix = " " * (len(line) - len(line.lstrip()))
            out.append(prefix + str(value) + "   " + line[line.find(key) :])
            found = True
        else:
            out.append(line)
    if not found:
        raise KeyError(f"{key} not found in {path}")
    path.write_text("".join(out), encoding="utf-8")


def get_scalar(path: Path, key: str) -> float:
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            return float(parts[0])
    raise KeyError(f"{key} not found in {path}")


def copy_starting_model() -> None:
    backup = RUN_ROOT / "starting_model"
    if not backup.exists():
        shutil.copytree(START_PACKAGE, backup)
    sha_manifest(
        START_PACKAGE,
        ROOT / "starting_model_sha256_manifest.txt",
        [
            "# Starting Model SHA-256 Manifest",
            "- starting_model_id: exploratory_calibrated_model",
            f"- starting_model_path: {START_PACKAGE}",
        ],
    )


def reference_path(dof: str) -> Path:
    return REFERENCE_DIR / f"{CASES[dof]}_curated_processed.csv"


def read_reference(dof: str) -> pd.DataFrame:
    return pd.read_csv(reference_path(dof))


def configure_free_decay(input_dir: Path, dof: str, release_value: float) -> None:
    setup = [
        (input_dir / FST, "TMax", 600),
        (input_dir / FST, "DT_Out", 0.05),
        (input_dir / FST, "OutFileFmt", 1),
        (input_dir / FST, "CompInflow", 0),
        (input_dir / FST, "CompAero", 0),
        (input_dir / FST, "CompServo", 0),
        (input_dir / SEA, "WaveMod", 0),
        (input_dir / SEA, "WaveHs", 0.0),
        (input_dir / SEA, "WaveTp", 1.0),
    ]
    for path, key, value in setup:
        replace_key(path, key, value)
    for item in DOFS:
        replace_key(input_dir / ED, "Ptfm" + item, release_value if item == dof else 0.0)


BASE_CLIN = read_matrix(START_INPUTS / HD, "AddCLin")
BASE_BLIN = read_matrix(START_INPUTS / HD, "AddBLin")
BASE_BQUAD = read_matrix(START_INPUTS / HD, "AddBQuad")


def create_template(candidate: Candidate) -> Path:
    model_dir = RUN_ROOT / "models" / candidate.model_id
    template = model_dir / "input_template"
    if template.exists():
        return template
    shutil.copytree(START_INPUTS, template, ignore=shutil.ignore_patterns("*.out", "*.outb", "*.sum", "*.ech"))
    clin = BASE_CLIN.copy()
    blin = BASE_BLIN.copy()
    bquad = BASE_BQUAD.copy()
    for dof, factor in candidate.clin_diag_factors.items():
        clin[IDX[dof], IDX[dof]] = BASE_CLIN[IDX[dof], IDX[dof]] * float(factor)
    for key, factor in (candidate.inertia_factors or {}).items():
        current = get_scalar(template / ED, key)
        replace_key(template / ED, key, current * float(factor))
    for dof, value in (candidate.blin_diag_values or {}).items():
        blin[IDX[dof], IDX[dof]] = float(value)
    for dof, factor in (candidate.bquad_diag_factors or {}).items():
        bquad[IDX[dof], IDX[dof]] = BASE_BQUAD[IDX[dof], IDX[dof]] * float(factor)
    write_matrix(template / HD, "AddCLin", clin)
    write_matrix(template / HD, "AddBLin", blin)
    write_matrix(template / HD, "AddBQuad", bquad)
    eig = np.linalg.eigvalsh((clin + clin.T) / 2)
    record = {
        "model_id": candidate.model_id,
        "basis": candidate.basis,
        "clin_diag_factors": candidate.clin_diag_factors,
        "inertia_factors": candidate.inertia_factors or {},
        "blin_diag_values": candidate.blin_diag_values or {},
        "bquad_diag_factors": candidate.bquad_diag_factors or {},
        "release_scales": candidate.release_scales or {},
        "minimum_AddCLin_eigenvalue": float(eig.min()),
        "stable_screen": bool(eig.min() > 0),
        "claim_boundary": "calibration-to-data / presentation-fit; not as-built",
    }
    dump_json(model_dir / "parameter_change.json", record)
    write_diff(template, model_dir / "diff.patch")
    return template


def write_diff(template: Path, output: Path) -> None:
    patches: list[str] = []
    for name in [FST, ED, HD, SEA]:
        old = (START_INPUTS / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        new = (template / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        patches.extend(difflib.unified_diff(old, new, fromfile=f"starting/{name}", tofile=f"candidate/{name}"))
    write_text(output, "".join(patches) if patches else "No physical parameter change from starting model.")


def parse_openfast_output(path: Path) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    header_idx = next(i for i, line in enumerate(lines) if line.strip().startswith("Time") and "Ptfm" in line)
    names: list[str] = []
    seen: dict[str, int] = {}
    for raw in lines[header_idx].split():
        count = seen.get(raw, 0)
        names.append(raw if count == 0 else f"{raw}_{count}")
        seen[raw] = count + 1
    return pd.read_csv(path, sep=r"\s+", skiprows=header_idx + 2, names=names)


def signal_metrics(time: np.ndarray, signal: np.ndarray) -> dict:
    tail = max(20, int(0.2 * len(signal)))
    centered = signal - float(np.nanmedian(signal[-tail:]))
    span = float(np.nanmax(centered) - np.nanmin(centered))
    dt = float(np.nanmedian(np.diff(time)))
    max_idx, _ = find_peaks(centered, prominence=max(span * 0.03, 1e-10), distance=max(1, int(2.0 / dt)))
    min_idx, _ = find_peaks(-centered, prominence=max(span * 0.03, 1e-10), distance=max(1, int(2.0 / dt)))
    periods: list[float] = []
    for indices in (max_idx, min_idx):
        if len(indices) > 1:
            periods.extend(np.diff(time[indices]).tolist())
    freq, power = welch(centered, fs=1.0 / dt, nperseg=min(len(centered), 4096))
    positive = freq > 0
    peaks = sorted([(int(i), "max") for i in max_idx] + [(int(i), "min") for i in min_idx])
    return {
        "period_s": float(np.nanmedian(periods)) if periods else math.nan,
        "dominant_frequency_hz": float(freq[positive][np.nanargmax(power[positive])]) if positive.any() else math.nan,
        "first_peaks": [{"time_s": float(time[i]), "amplitude": float(abs(centered[i])), "signed": float(centered[i]), "kind": kind} for i, kind in peaks[:8]],
    }


def error_pct(sim: float, ref: float) -> float:
    return abs(sim - ref) / max(abs(ref), 1e-12) * 100.0 if np.isfinite(sim) and np.isfinite(ref) else math.nan


def envelope_error(ref: np.ndarray, sim: np.ndarray) -> float:
    env_ref = np.abs(hilbert(ref))
    env_sim = np.abs(hilbert(sim))
    return float(np.sqrt(np.mean((env_sim - env_ref) ** 2)) / max(np.ptp(env_ref), 1e-12) * 100.0)


def compare(dof: str, reference: pd.DataFrame, simulation: pd.DataFrame, model_id: str) -> dict:
    t_ref = reference["Time"].to_numpy(float)
    y_ref = reference[dof].to_numpy(float)
    t_sim = simulation["Time"].to_numpy(float)
    y_sim = simulation["Ptfm" + dof].to_numpy(float)
    y_sim = y_sim - float(np.nanmedian(y_sim[-max(20, int(len(y_sim) * 0.2)) :]))
    end = min(float(t_ref[-1]), float(t_sim[-1]), 600.0)
    grid = t_ref[t_ref <= end]
    ref = np.interp(grid, t_ref, y_ref)
    sim = np.interp(grid, t_sim, y_sim)
    ref_m = signal_metrics(grid, ref)
    sim_m = signal_metrics(grid, sim)
    n = min(len(ref_m["first_peaks"]), len(sim_m["first_peaks"]), 5)
    n3 = min(len(ref_m["first_peaks"]), len(sim_m["first_peaks"]), 3)
    peak_error = float(np.mean([error_pct(sim_m["first_peaks"][i]["amplitude"], ref_m["first_peaks"][i]["amplitude"]) for i in range(n)])) if n else math.nan
    initial_peak_error = error_pct(sim_m["first_peaks"][0]["amplitude"], ref_m["first_peaks"][0]["amplitude"]) if n else math.nan
    first_3_peaks_error = float(np.mean([error_pct(sim_m["first_peaks"][i]["amplitude"], ref_m["first_peaks"][i]["amplitude"]) for i in range(n3)])) if n3 else math.nan
    phase_error = float(np.mean([abs(sim_m["first_peaks"][i]["time_s"] - ref_m["first_peaks"][i]["time_s"]) for i in range(n)]) / max(ref_m["period_s"], 1e-12) * 100.0) if n and np.isfinite(ref_m["period_s"]) else math.nan
    nrmse = float(np.sqrt(np.mean((sim - ref) ** 2)) / max(np.ptp(ref), 1e-12) * 100.0)
    env = envelope_error(ref, sim)
    late_start = min(120.0, float(grid[-1]) * 0.5)
    ref_late = ref[grid >= late_start]
    sim_late = sim[grid >= late_start]
    ref_energy = float(np.mean(ref_late**2) / max(np.mean(ref**2), 1e-12)) if len(ref_late) else math.nan
    sim_energy = float(np.mean(sim_late**2) / max(np.mean(sim**2), 1e-12)) if len(sim_late) else math.nan
    late_error = error_pct(sim_energy, ref_energy)
    period_error = error_pct(sim_m["period_s"], ref_m["period_s"])
    signed_period_error = (sim_m["period_s"] - ref_m["period_s"]) / max(abs(ref_m["period_s"]), 1e-12) * 100.0 if np.isfinite(sim_m["period_s"]) else math.nan
    frequency_error = error_pct(sim_m["dominant_frequency_hz"], ref_m["dominant_frequency_hz"])
    components = [period_error, frequency_error, nrmse, env, peak_error, phase_error]
    j_dof = float(sum(w * v for w, v in zip(WEIGHTS[dof], components))) if all(np.isfinite(components)) else math.nan
    shape_components = [nrmse, env, phase_error, initial_peak_error, first_3_peaks_error, late_error]
    j_shape = float(sum(w * v for w, v in zip(SHAPE_WEIGHTS[dof], shape_components))) if all(np.isfinite(shape_components)) else math.nan
    return {
        "model_id": model_id,
        "case_id": CASES[dof],
        "dof": dof,
        "unit": UNITS[dof],
        "reference_kind": "curated_late_decay" if dof == "Heave" else "curated",
        "old_heave_reference_used": False,
        "reference_period_s": ref_m["period_s"],
        "simulation_period_s": sim_m["period_s"],
        "signed_period_error_pct": signed_period_error,
        "period_error_pct": period_error,
        "frequency_error_pct": frequency_error,
        "nrmse_pct": nrmse,
        "envelope_error_pct": env,
        "peak_error_pct": peak_error,
        "phase_error_pct": phase_error,
        "initial_peak_error_pct": initial_peak_error,
        "first_3_peaks_error_pct": first_3_peaks_error,
        "late_stage_energy_ratio_error": late_error,
        "J_dof": j_dof,
        "J_shape": j_shape,
        "period_under_2pct": bool(np.isfinite(period_error) and period_error < 2.0),
        "aligned_time_s": grid,
        "reference_aligned": ref,
        "simulation_aligned": sim,
    }


def run_one(candidate: Candidate, dof: str) -> tuple[dict | None, dict]:
    template = create_template(candidate)
    run_dir = RUN_ROOT / "models" / candidate.model_id / "runs" / CASES[dof]
    input_dir = run_dir / "input_files"
    output = input_dir / Path(FST).with_suffix(".out").name
    log = run_dir / "run.log"
    reference = read_reference(dof)
    if output.exists() and log.exists() and "OpenFAST terminated normally." in log.read_text(encoding="utf-8", errors="replace"):
        return compare(dof, reference, parse_openfast_output(output), candidate.model_id), {"model_id": candidate.model_id, "case_id": CASES[dof], "status": "reused", "output": str(output)}
    if not input_dir.exists():
        shutil.copytree(template, input_dir, ignore=shutil.ignore_patterns("*.out", "*.outb", "*.sum", "*.ech"))
    shared_dst = run_dir / "IEA-15-240-RWT"
    if not shared_dst.exists() and SHARED_RWT.exists():
        shutil.copytree(SHARED_RWT, shared_dst)
    scale = (candidate.release_scales or {}).get(dof, 1.0)
    configure_free_decay(input_dir, dof, float(reference[dof].iloc[0]) * scale)
    cmd = [str(input_dir / "OpenFAST_Release.exe"), FST]
    try:
        proc = subprocess.run(cmd, cwd=input_dir, capture_output=True, text=True, errors="replace", timeout=OPENFAST_TIMEOUT_S)
        log.write_text(f"command: {cmd}\nreturncode: {proc.returncode}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}", encoding="utf-8")
    except subprocess.TimeoutExpired as exc:
        log.write_text(f"command: {cmd}\nreturncode: timeout\n\nSTDOUT\n{exc.stdout or ''}\n\nSTDERR\n{exc.stderr or ''}", encoding="utf-8")
        return None, {"model_id": candidate.model_id, "case_id": CASES[dof], "status": "timeout", "output": str(output)}
    log_text = log.read_text(encoding="utf-8", errors="replace")
    if not output.exists():
        return None, {"model_id": candidate.model_id, "case_id": CASES[dof], "status": "missing_output", "returncode": proc.returncode, "output": str(output)}
    normal = "OpenFAST terminated normally." in log_text
    if not normal:
        return None, {"model_id": candidate.model_id, "case_id": CASES[dof], "status": "failed", "returncode": proc.returncode, "output": str(output)}
    return compare(dof, reference, parse_openfast_output(output), candidate.model_id), {"model_id": candidate.model_id, "case_id": CASES[dof], "status": "normal_completion", "returncode": proc.returncode, "output": str(output)}


def flat(row: dict) -> dict:
    return {k: v for k, v in row.items() if not isinstance(v, np.ndarray)}


def run_candidate(candidate: Candidate) -> list[dict]:
    create_template(candidate)
    tasks = [(candidate, dof) for dof in DOFS]
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        pairs = list(pool.map(lambda args: run_one(*args), tasks))
    results = [r for r, _ in pairs if r is not None]
    records = [rec for _, rec in pairs]
    dump_json(RUN_ROOT / "models" / candidate.model_id / "run_manifest.json", records)
    pd.DataFrame([flat(r) for r in results]).to_csv(RUN_ROOT / "models" / candidate.model_id / "metrics.csv", index=False, encoding="utf-8-sig")
    return results


def group_score(results: list[dict]) -> dict:
    if len({r["dof"] for r in results}) != 6:
        return {"complete": False, "J_period6": math.inf, "J_total": math.inf, "J_shape_total": math.inf, "J_HRP_shape": math.inf, "full_period_pass": False}
    return {
        "complete": True,
        "J_period6": float(np.nanmax([r["period_error_pct"] for r in results])),
        "J_total": float(np.nanmean([r["J_dof"] for r in results])),
        "J_shape_total": float(np.nanmean([r["J_shape"] for r in results])),
        "J_HRP_shape": float(np.nanmean([r["J_shape"] for r in results if r["dof"] in {"Heave", "Roll", "Pitch"}])),
        "full_period_pass": bool(all(r["period_error_pct"] < 2.0 for r in results)),
        "period_pass_count": int(sum(r["period_error_pct"] < 2.0 for r in results)),
    }


def make_period_candidate(previous: list[dict], iteration: int, damping: dict[str, float] | None = None, blend: float = 1.0) -> Candidate:
    prior_factors = {}
    prev_record = RUN_ROOT / "models" / previous[0]["model_id"] / "parameter_change.json"
    if prev_record.exists():
        prior_factors = json.loads(prev_record.read_text(encoding="utf-8")).get("clin_diag_factors", {})
    factors = {d: float(prior_factors.get(d, 1.0)) for d in DOFS}
    for row in previous:
        if not np.isfinite(row["simulation_period_s"]) or not np.isfinite(row["reference_period_s"]):
            continue
        ratio = row["simulation_period_s"] / row["reference_period_s"]
        correction = ratio**2
        factors[row["dof"]] *= 1.0 + blend * (correction - 1.0)
        factors[row["dof"]] = float(np.clip(factors[row["dof"]], 0.20, 8.0))
    return Candidate(f"period_iter_{iteration:02d}_blend_{str(blend).replace('.', 'p')}", factors, blin_diag_values=damping or {}, basis=f"period inverse update from {previous[0]['model_id']}")


def period_search() -> tuple[list[dict], list[list[dict]]]:
    all_results: list[list[dict]] = []
    start = Candidate("starting_model_recheck", {}, basis="copied starting model")
    current = run_candidate(start)
    all_results.append(current)
    for iteration in range(1, 6):
        best_local: list[dict] | None = None
        for blend in [1.0, 0.75, 0.50, 1.25]:
            cand = make_period_candidate(current, iteration, blend=blend)
            results = run_candidate(cand)
            all_results.append(results)
            if best_local is None or group_score(results)["J_period6"] < group_score(best_local)["J_period6"]:
                best_local = results
        current = best_local or current
        if group_score(current)["full_period_pass"]:
            break
    return current, all_results


def damping_refine(period_best: list[dict]) -> list[list[dict]]:
    record = json.loads((RUN_ROOT / "models" / period_best[0]["model_id"] / "parameter_change.json").read_text(encoding="utf-8"))
    base_factors = {d: float(record.get("clin_diag_factors", {}).get(d, 1.0)) for d in DOFS}
    candidates: list[Candidate] = []
    for scale in [1.0, 2.0, 5.0, 10.0, 20.0]:
        values = {}
        for dof in DOFS:
            # Linear damping magnitudes are modest and diagonal-only; this keeps periods nearly unchanged.
            values[dof] = scale * (2.0e5 if dof in {"Surge", "Sway", "Heave"} else 2.0e8)
        candidates.append(Candidate(f"shape_blin_diag_{str(scale).replace('.', 'p')}", base_factors, blin_diag_values=values, basis="shape damping scan after period search"))
    bquad_sets = [
        {},
        {"Heave": 0.5, "Roll": 0.5, "Pitch": 0.5},
        {"Heave": 0.25, "Roll": 0.25, "Pitch": 0.25},
        {"Surge": 0.5, "Sway": 0.5, "Yaw": 0.5},
    ]
    for i, bq in enumerate(bquad_sets):
        candidates.append(Candidate(f"shape_bquad_set_{i}", base_factors, bquad_diag_factors=bq, basis="quadratic damping shape scan after period search"))
    return [run_candidate(c) for c in candidates]


def collect_all_results() -> list[dict]:
    rows: list[dict] = []
    for path in sorted((RUN_ROOT / "models").glob("*/metrics.csv")):
        if path.stat().st_size:
            rows.extend(pd.read_csv(path).to_dict("records"))
    return rows


def summarize_models(rows: list[dict]) -> pd.DataFrame:
    summaries = []
    for model_id, group in pd.DataFrame(rows).groupby("model_id"):
        recs = group.to_dict("records")
        summaries.append({"model_id": model_id, **group_score(recs)})
    return pd.DataFrame(summaries).sort_values(["full_period_pass", "J_period6", "J_total"], ascending=[False, True, True])


def plot_six(results: list[dict], path: Path, title: str, start_results: list[dict] | None = None) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    for ax, dof in zip(axes.flat, DOFS):
        row = next(r for r in results if r["dof"] == dof)
        ax.plot(row["aligned_time_s"], row["reference_aligned"], label="curated experiment", lw=1.15)
        if start_results is not None:
            srow = next(r for r in start_results if r["dof"] == dof)
            ax.plot(srow["aligned_time_s"], srow["simulation_aligned"], label="starting simulation", lw=0.95, alpha=0.75)
        ax.plot(row["aligned_time_s"], row["simulation_aligned"], label="OpenFAST simulation" if start_results is None else "final simulation", lw=1.05)
        ax.set_title(f"{dof}: T err {row['period_error_pct']:.2f}%, J {row['J_dof']:.2f}%, NRMSE {row['nrmse_pct']:.2f}%")
        ax.set_xlabel("Time after release (s)")
        ax.set_ylabel(UNITS[dof])
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=170)
    plt.close(fig)


def plot_one(row: dict, path: Path, zoom: float | None = None) -> None:
    t = row["aligned_time_s"]
    mask = np.ones(len(t), dtype=bool) if zoom is None else t <= min(float(t[-1]), zoom)
    plt.figure(figsize=(12, 5))
    plt.plot(t[mask], row["reference_aligned"][mask], label="curated experiment", lw=1.2)
    plt.plot(t[mask], row["simulation_aligned"][mask], label="OpenFAST simulation", lw=1.05)
    plt.title(f"{row['dof']}: period error {row['period_error_pct']:.2f}%, J {row['J_dof']:.2f}%, NRMSE {row['nrmse_pct']:.2f}%")
    plt.xlabel("Time after release (s)")
    plt.ylabel(row["unit"])
    plt.grid(alpha=0.25)
    plt.legend()
    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(path, dpi=170)
    plt.close()


def plot_objective_history(summary: pd.DataFrame) -> None:
    plt.figure(figsize=(12, 5))
    x = np.arange(len(summary))
    plt.plot(x, summary["J_period6"], marker="o", label="J_period6")
    plt.plot(x, summary["J_total"], marker="o", label="J_total")
    plt.xticks(x, summary["model_id"], rotation=60, ha="right", fontsize=8)
    plt.ylabel("%")
    plt.grid(alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIG_DIR / "objective_history.png", dpi=170)
    plt.close()


def plot_error_bar(final: list[dict]) -> None:
    plt.figure(figsize=(9, 5))
    plt.bar([r["dof"] for r in final], [r["period_error_pct"] for r in final])
    plt.axhline(2.0, color="r", linestyle="--", label="2% target")
    plt.ylabel("Period error (%)")
    plt.grid(axis="y", alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIG_DIR / "final_six_dof_error_bar.png", dpi=170)
    plt.close()


def cleanup_outputs(top_ids: set[str], final_id: str) -> None:
    rows = []
    for model_dir in sorted((RUN_ROOT / "models").glob("*")):
        keep = model_dir.name in top_ids or model_dir.name in {"starting_model_recheck", final_id}
        for path in model_dir.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".out", ".outb", ".ech", ".sum"} and not keep:
                rows.append({"path": str(path), "action": "deleted", "reason": "non-top candidate output after metrics recorded"})
                path.unlink()
    if not rows:
        rows.append({"path": "", "action": "none", "reason": "no non-top large outputs found"})
    pd.DataFrame(rows).to_csv(ROOT / "cleanup_manifest.csv", index=False, encoding="utf-8-sig")


def package_final(final: list[dict], start_results: list[dict], all_rows: list[dict], summary: pd.DataFrame) -> None:
    final_id = final[0]["model_id"]
    final_dir = ROOT / "final_candidate_model"
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.copytree(RUN_ROOT / "models" / final_id / "input_template", final_dir / "OpenFAST_input_files")
    shutil.copy2(RUN_ROOT / "models" / final_id / "parameter_change.json", final_dir / "parameter_change.json")
    shutil.copy2(RUN_ROOT / "models" / final_id / "diff.patch", ROOT / "diff.patch")
    shutil.copy2(RUN_ROOT / "models" / final_id / "diff.patch", final_dir / "diff.patch")
    pd.DataFrame([flat(r) for r in final]).to_csv(final_dir / "metrics.csv", index=False, encoding="utf-8-sig")
    sha_manifest(final_dir, ROOT / "final_model_sha256_manifest.txt", ["# Final Model SHA-256 Manifest", f"- final_model_id: {final_id}", f"- final_model_path: {final_dir}"])
    pd.DataFrame([flat(r) for r in all_rows]).to_csv(ROOT / "metrics_all_runs.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(ROOT / "objective_history.csv", index=False, encoding="utf-8-sig")
    summary.head(10).to_csv(ROOT / "top_candidates.csv", index=False, encoding="utf-8-sig")
    rejected = summary[~summary["full_period_pass"]].copy()
    rejected.to_csv(ROOT / "rejected_candidates.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([flat(r) for r in final]).to_csv(ROOT / "final_six_dof_error_table.csv", index=False, encoding="utf-8-sig")
    param_rows = []
    for path in sorted((RUN_ROOT / "models").glob("*/parameter_change.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for dof, factor in data.get("clin_diag_factors", {}).items() or {"none": 1.0}.items():
            param_rows.append({"model_id": data["model_id"], "parameter": "AddCLin_diag_factor", "dof": dof, "value": factor, "basis": data.get("basis", "")})
        for dof, value in data.get("blin_diag_values", {}).items():
            param_rows.append({"model_id": data["model_id"], "parameter": "AddBLin_diag_value", "dof": dof, "value": value, "basis": data.get("basis", "")})
        for dof, factor in data.get("bquad_diag_factors", {}).items():
            param_rows.append({"model_id": data["model_id"], "parameter": "AddBQuad_diag_factor", "dof": dof, "value": factor, "basis": data.get("basis", "")})
    pd.DataFrame(param_rows).to_csv(ROOT / "parameter_change_log.csv", index=False, encoding="utf-8-sig")
    plot_six(final, FIG_DIR / "final_six_dof_comparison.png", "Final six-DOF free-decay comparison")
    plot_six(final, FIG_DIR / "starting_vs_final_six_dof_comparison.png", "Starting vs final six-DOF comparison", start_results=start_results)
    for row in final:
        plot_one(row, FIG_DIR / f"{row['dof'].lower()}_comparison.png")
    plot_one(next(r for r in final if r["dof"] == "Heave"), FIG_DIR / "heave_zoom.png", zoom=160)
    plot_one(next(r for r in final if r["dof"] == "Roll"), FIG_DIR / "roll_zoom.png", zoom=220)
    plot_one(next(r for r in final if r["dof"] == "Pitch"), FIG_DIR / "pitch_zoom.png", zoom=220)
    plot_objective_history(summary)
    plot_error_bar(final)
    shutil.copy2(FIG_DIR / "final_six_dof_comparison.png", ROOT / "final_six_dof_comparison.png")
    top_ids = set(summary.head(3)["model_id"].tolist())
    cleanup_outputs(top_ids, final_id)
    final_score = group_score(final)
    start_score = group_score(start_results)
    rows = "\n".join(f"| {r['dof']} | {r['reference_period_s']:.3f} | {r['simulation_period_s']:.3f} | {r['period_error_pct']:.3f}% | {r['J_dof']:.3f}% | {r['nrmse_pct']:.3f}% |" for r in final)
    report = f"""
# Final Report

- starting_model_id: exploratory_calibrated_model
- starting_model_path: {START_PACKAGE}
- final_model_id: {final_id}
- final_model_path: {final_dir}
- curated_reference_metrics: {ROOT / '00_reference' / 'curated_six_dof_reference' / 'curated_reference_summary.csv'}
- known_limitations: calibration-to-data / presentation-fit only; not a FOCAL C4 as-built model.
- Heave reference: curated late-decay processed reference, old Heave reference used = false.

## Target A

- Full six-DOF period pass: {final_score['full_period_pass']}
- Starting J_period6: {start_score['J_period6']:.3f}%
- Final J_period6: {final_score['J_period6']:.3f}%
- Period pass count: {final_score['period_pass_count']}/6

| DOF | T_ref (s) | T_sim (s) | Period error | J_dof | NRMSE |
|---|---:|---:|---:|---:|---:|
{rows}

## Target B/C

- J_total: {final_score['J_total']:.3f}%
- J_shape_total: {final_score['J_shape_total']:.3f}%
- J_HRP_shape: {final_score['J_HRP_shape']:.3f}%

All required plots and manifests were generated in the clean workspace.
"""
    write_text(ROOT / "final_report.md", report)
    write_text(REPORT_DIR / "final_report.md", report)
    write_text(
        ROOT / "README_stage_summary.md",
        f"""
# README Stage Summary

1. Copied starting model to `{RUN_ROOT / 'starting_model'}` without modifying the original.
2. Rechecked the starting model against all six curated references.
3. Ran period-inverse diagonal AddCLin iterations to prioritize Target A.
4. Ran damping/shape scans after the best period candidate.
5. Selected `{final_id}` and packaged it as `final_candidate_model`.
""",
    )


def run_all() -> None:
    ensure_dirs()
    copy_starting_model()
    final_period, period_runs = period_search()
    shape_runs = damping_refine(final_period) if group_score(final_period)["full_period_pass"] else []
    all_rows = collect_all_results()
    summary = summarize_models(all_rows)
    final_id = str(summary.iloc[0]["model_id"])
    final = pd.read_csv(RUN_ROOT / "models" / final_id / "metrics.csv").to_dict("records")
    start = pd.read_csv(RUN_ROOT / "models" / "starting_model_recheck" / "metrics.csv").to_dict("records")
    # Reattach arrays for plotting from already parsed outputs.
    final_full = [run_one(Candidate(final_id, json.loads((RUN_ROOT / "models" / final_id / "parameter_change.json").read_text(encoding="utf-8")).get("clin_diag_factors", {})), r["dof"])[0] for r in final]
    start_full = [run_one(Candidate("starting_model_recheck", {}), r["dof"])[0] for r in start]
    package_final(final_full, start_full, all_rows, summary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    run_all()


if __name__ == "__main__":
    main()
