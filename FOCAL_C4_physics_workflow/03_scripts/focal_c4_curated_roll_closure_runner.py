from __future__ import annotations

import argparse
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
from scipy.signal import find_peaks, hilbert, welch

import phase2_15mw_runner as p2


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "focal_c4_curated_roll_period_closure"
START_PACKAGE = ROOT / "focal_c4_curated_six_dof_calibration" / "curated_six_dof_calibrated_model"
START = START_PACKAGE / "OpenFAST_input_files"
CURATED_PROCESSED = (
    ROOT
    / "phase2_15mw_free_decay_diagnosis"
    / "00_reference_data"
    / "curated_six_dof_reference"
    / "processed"
)

FST = p2.FST_NAME
ED = p2.ED_NAME
HD = p2.HD_NAME
SEA = p2.SEA_NAME
DOFS = p2.DOFS
IDX = {dof: i for i, dof in enumerate(DOFS)}
LOCK_LIMITS = {"Surge": 1.0, "Sway": 1.0, "Heave": 2.0, "Pitch": 2.0, "Yaw": 1.0}
OPENFAST_TIMEOUT_S = 240

STAGES = {
    "R0": "R0_starting_model_recheck",
    "R1": "R1_roll_error_direction_check",
    "R2": "R2_roll_stiffness_refine",
    "R3": "R3_roll_inertia_refine",
    "R4": "R4_roll_coupling_refine",
    "R5": "R5_integrated_roll_period_search",
    "R6": "R6_final_roll_closed_candidate",
}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def dump_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_matrix(path: Path, marker: str) -> np.ndarray:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = next(i for i, line in enumerate(lines) if marker in line)
    return np.array([[float(v) for v in lines[start + row].split()[:6]] for row in range(6)])


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
    "PtfmRIner": get_scalar(START / ED, "PtfmRIner"),
    "PtfmCMzt": get_scalar(START / ED, "PtfmCMzt"),
}


def cases() -> list[p2.ReferenceCase]:
    return [
        p2.ReferenceCase(case.case_id, case.dof, CURATED_PROCESSED / f"{case.case_id}_curated_processed.csv", case.excitation_unit)
        for case in p2.CASES
    ]


CASES = cases()


def ensure_structure() -> None:
    for rel in [
        "00_inputs",
        "01_starting_model_backup",
        "02_objective_definition",
        "03_roll_sensitivity",
        "04_stage_runs",
        "05_metrics",
        "06_figures",
        "07_diffs",
        "08_logs",
        "09_final_report",
        "figures",
        "diffs",
        "logs",
    ]:
        (OUT / rel).mkdir(parents=True, exist_ok=True)
    for stage in STAGES.values():
        (OUT / "04_stage_runs" / stage).mkdir(parents=True, exist_ok=True)


def copy_once(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    if src.is_dir():
        shutil.copytree(src, dst)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def init() -> None:
    ensure_structure()
    copy_once(START_PACKAGE, OUT / "01_starting_model_backup" / "curated_six_dof_calibrated_model")
    for src in [
        START_PACKAGE / "metrics.csv",
        START_PACKAGE / "parameter_change.json",
        START_PACKAGE / "objective_summary.json",
        START_PACKAGE / "six_dof_comparison.png",
        START_PACKAGE / "limitations.md",
    ]:
        if src.exists():
            copy_once(src, OUT / "00_inputs" / src.name)
    lines = ["# Starting Model SHA-256 Manifest", "", f"- starting_model_path: `{START_PACKAGE}`", ""]
    for path in sorted(START_PACKAGE.rglob("*")):
        if path.is_file():
            lines.append(f"{sha256(path)}  {path.relative_to(START_PACKAGE).as_posix()}")
    write_text(OUT / "01_starting_model_backup" / "starting_model_sha256_manifest.txt", "\n".join(lines))
    write_reference_manifest()
    write_text(
        OUT / "02_objective_definition" / "roll_only_period_closure_objective.md",
        """
# Roll-Only Period Closure Objective

- reference_kind = curated
- old_heave_reference_used = false
- Primary goal: Roll period error < 2%.
- Hard locks: Surge <1%, Sway <1%, Heave <2%, Pitch <2%, Yaw <1%.
- Final acceptance: all six DOF period errors <2%.

This remains a calibration-to-data / presentation-fit model, not a FOCAL C4 as-built model.
""",
    )


def write_reference_manifest() -> None:
    rows = []
    for case in CASES:
        rows.append({"case_id": case.case_id, "dof": case.dof, "curated_processed_file": str(case.csv_path), "exists": case.csv_path.exists()})
    pd.DataFrame(rows).to_csv(OUT / "curated_reference_usage_manifest.csv", index=False, encoding="utf-8-sig")
    table = "\n".join(f"| {r['case_id']} | {r['dof']} | `{r['curated_processed_file']}` | {r['exists']} |" for r in rows)
    write_text(
        OUT / "curated_reference_usage_manifest.md",
        f"""
# Curated Reference Usage Manifest

- reference_kind = curated
- old_heave_reference_used = false
- old_heave_reference = deprecated

| Case | DOF | File | Exists |
|---|---|---|---|
{table}
""",
    )


def canonical(actions: list[dict]) -> list[dict]:
    out: dict[tuple, dict] = {}
    for action in actions:
        out[(action["parameter"], action.get("dof", ""), action.get("dof_a", ""), action.get("dof_b", ""))] = action
    return list(out.values())


def create_template(stage: str, model_id: str, actions: list[dict]) -> Path:
    model_dir = OUT / "04_stage_runs" / STAGES[stage] / model_id
    template = model_dir / "input_template"
    if template.exists():
        return template
    shutil.copytree(START, template)
    clin = BASE["AddCLin"].copy()
    for action in canonical(actions):
        param = action["parameter"]
        if param == "AddCLin_roll_factor":
            clin[IDX["Roll"], IDX["Roll"]] = BASE["AddCLin"][IDX["Roll"], IDX["Roll"]] * float(action["factor"])
        elif param == "AddCLin_offdiag_estimated":
            a, b = action["dof_a"], action["dof_b"]
            if set([a, b]) not in [set(["Roll", "Pitch"]), set(["Roll", "Heave"])]:
                raise ValueError(f"Disallowed coupling {a}-{b}")
            ia, ib = IDX[a], IDX[b]
            estimate = math.sqrt(abs(clin[ia, ia] * clin[ib, ib]))
            value = estimate * float(action["factor"])
            clin[ia, ib] = value
            clin[ib, ia] = value
        elif param == "PtfmRIner_factor":
            p2.replace_key(template / ED, "PtfmRIner", BASE["PtfmRIner"] * float(action["factor"]))
        elif param == "PtfmCMzt_offset":
            p2.replace_key(template / ED, "PtfmCMzt", BASE["PtfmCMzt"] + float(action["offset"]))
        else:
            raise ValueError(f"Disallowed parameter {param}")
    write_matrix(template / HD, "AddCLin", clin)
    eig = np.linalg.eigvalsh((clin + clin.T) / 2)
    record = {
        "model_id": model_id,
        "stage": stage,
        "actions": canonical(actions),
        "minimum_AddCLin_eigenvalue": float(eig.min()),
        "stable_screen": bool(eig.min() > 0),
        "reference_kind": "curated",
        "old_heave_reference_used": False,
        "claim_boundary": "roll-only calibration-to-data / presentation-fit; not as-built",
    }
    dump_json(model_dir / "parameter_change.json", record)
    write_diff(template, OUT / "07_diffs" / f"{model_id}.patch")
    copy_once(OUT / "07_diffs" / f"{model_id}.patch", OUT / "diffs" / f"{model_id}.patch")
    return template


def write_diff(template: Path, output: Path) -> None:
    patches = []
    for name in [FST, ED, HD, SEA]:
        old = (START / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        new = (template / name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        patches.extend(difflib.unified_diff(old, new, fromfile=f"start/{name}", tofile=f"candidate/{name}"))
    write_text(output, "".join(patches) if patches else "No physical parameter change from starting roll-closure model.")


def read_reference(case: p2.ReferenceCase) -> pd.DataFrame:
    return pd.read_csv(case.csv_path)


def configure_case(input_dir: Path, case: p2.ReferenceCase, reference: pd.DataFrame, scale: float) -> None:
    p2.configure_free_decay(input_dir, case, float(reference[case.dof].iloc[0]) * scale)


def signal_metrics(time: np.ndarray, signal: np.ndarray) -> dict:
    tail = max(20, int(0.2 * len(signal)))
    centered = signal - float(np.nanmedian(signal[-tail:]))
    dt = float(np.nanmedian(np.diff(time)))
    span = float(np.nanmax(centered) - np.nanmin(centered))
    max_idx, _ = find_peaks(centered, prominence=max(span * 0.03, 1e-10), distance=max(1, int(2.0 / dt)))
    min_idx, _ = find_peaks(-centered, prominence=max(span * 0.03, 1e-10), distance=max(1, int(2.0 / dt)))
    periods = []
    for idx in (max_idx, min_idx):
        if len(idx) > 1:
            periods.extend(np.diff(time[idx]).tolist())
    freq, power = welch(centered, fs=1.0 / dt, nperseg=min(len(centered), 4096))
    positive = freq > 0
    peaks = sorted([(int(i), "max") for i in max_idx] + [(int(i), "min") for i in min_idx])
    return {
        "period_s": float(np.nanmedian(periods)) if periods else math.nan,
        "dominant_frequency_hz": float(freq[positive][np.nanargmax(power[positive])]) if positive.any() else math.nan,
        "first_peaks": [{"time_s": float(time[i]), "amplitude": float(abs(centered[i])), "kind": kind} for i, kind in peaks[:8]],
        "peak_abs": float(np.nanmax(np.abs(centered))),
    }


def err(sim: float, ref: float) -> float:
    return abs(sim - ref) / max(abs(ref), 1e-12) * 100.0 if np.isfinite(sim) and np.isfinite(ref) else math.nan


def compare(case: p2.ReferenceCase, reference: pd.DataFrame, simulation: pd.DataFrame, model_id: str, stage: str) -> dict:
    t_ref = reference["Time"].to_numpy(float)
    y_ref = reference[case.dof].to_numpy(float)
    t_sim = simulation["Time"].to_numpy(float)
    y_sim = simulation["Ptfm" + case.dof].to_numpy(float)
    y_sim = y_sim - float(np.nanmedian(y_sim[-max(20, int(len(y_sim) * 0.2)) :]))
    end = min(float(t_ref[-1]), float(t_sim[-1]), 600.0)
    grid = t_ref[t_ref <= end]
    ref = np.interp(grid, t_ref, y_ref)
    sim = np.interp(grid, t_sim, y_sim)
    ref_m = signal_metrics(grid, ref)
    sim_m = signal_metrics(grid, sim)
    nrmse = float(np.sqrt(np.mean((sim - ref) ** 2)) / max(np.ptp(ref), 1e-12) * 100.0)
    env_ref = np.abs(hilbert(ref))
    env_sim = np.abs(hilbert(sim))
    envelope_error = float(np.sqrt(np.mean((env_sim - env_ref) ** 2)) / max(np.ptp(env_ref), 1e-12) * 100.0)
    period_error = err(sim_m["period_s"], ref_m["period_s"])
    signed_period_error = (sim_m["period_s"] - ref_m["period_s"]) / max(abs(ref_m["period_s"]), 1e-12) * 100.0
    return {
        "stage": stage,
        "model_id": model_id,
        "case_id": case.case_id,
        "dof": case.dof,
        "reference_kind": "curated",
        "old_heave_reference_used": False,
        "reference_period_s": ref_m["period_s"],
        "simulation_period_s": sim_m["period_s"],
        "signed_period_error_pct": signed_period_error,
        "period_error_pct": period_error,
        "frequency_error_pct": err(sim_m["dominant_frequency_hz"], ref_m["dominant_frequency_hz"]),
        "nrmse_pct": nrmse,
        "envelope_error_pct": envelope_error,
        "period_under_2pct": bool(np.isfinite(period_error) and period_error < 2.0),
        "lock_pass": lock_pass(case.dof, period_error),
        "aligned_time_s": grid,
        "reference_aligned": ref,
        "simulation_aligned": sim,
    }


def lock_pass(dof: str, period_error: float) -> bool:
    if dof == "Roll":
        return bool(np.isfinite(period_error) and period_error < 2.0)
    return bool(np.isfinite(period_error) and period_error < LOCK_LIMITS[dof])


def run_one(stage: str, model_id: str, actions: list[dict], case: p2.ReferenceCase, scale: float = 1.0) -> tuple[dict | None, dict]:
    template = create_template(stage, model_id, actions)
    run_dir = OUT / "04_stage_runs" / STAGES[stage] / model_id / "runs" / case.case_id
    output = run_dir / "input_files" / Path(FST).with_suffix(".out").name
    log = run_dir / "run.log"
    reference = read_reference(case)
    if output.exists() and log.exists() and "OpenFAST terminated normally." in log.read_text(encoding="utf-8", errors="replace"):
        return compare(case, reference, p2.parse_openfast_output(output), model_id, stage), {"stage": stage, "model_id": model_id, "case_id": case.case_id, "status": "normal_completion_reused", "output": str(output)}
    input_dir = run_dir / "input_files"
    if not input_dir.exists():
        shutil.copytree(template, input_dir)
    shared = run_dir / "IEA-15-240-RWT"
    if not shared.exists() and p2.SHARED_RWT.exists():
        shutil.copytree(p2.SHARED_RWT, shared)
    configure_case(input_dir, case, reference, scale)
    dump_json(run_dir / "run_configuration.json", {"stage": stage, "model_id": model_id, "case_id": case.case_id, "actions": actions, "reference_kind": "curated", "old_heave_reference_used": False})
    cmd = [str(input_dir / "OpenFAST_Release.exe"), FST]
    try:
        proc = subprocess.run(cmd, cwd=input_dir, capture_output=True, text=True, errors="replace", timeout=OPENFAST_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        log.write_text(
            f"command: {cmd}\nreturncode: timeout\n"
            f"timeout_s: {OPENFAST_TIMEOUT_S}\n\nSTDOUT\n{exc.stdout or ''}\n\nSTDERR\n{exc.stderr or ''}",
            encoding="utf-8",
        )
        return None, {
            "stage": stage,
            "model_id": model_id,
            "case_id": case.case_id,
            "status": "timeout",
            "timeout_s": OPENFAST_TIMEOUT_S,
            "output": str(output),
        }
    log.write_text(f"command: {cmd}\nreturncode: {proc.returncode}\n\nSTDOUT\n{proc.stdout}\n\nSTDERR\n{proc.stderr}", encoding="utf-8")
    normal = output.exists() and "OpenFAST terminated normally." in log.read_text(encoding="utf-8", errors="replace")
    rec = {"stage": stage, "model_id": model_id, "case_id": case.case_id, "status": "normal_completion" if normal else "failed", "returncode": proc.returncode, "output": str(output)}
    if not normal:
        return None, rec
    return compare(case, reference, p2.parse_openfast_output(output), model_id, stage), rec


def run_models(stage: str, models: list[dict], max_workers: int = 6) -> list[dict]:
    tasks = []
    for model in models:
        create_template(stage, model["model_id"], model.get("actions", []))
        for case in CASES:
            tasks.append((stage, model["model_id"], model.get("actions", []), case, model.get("roll_scale", 1.0) if case.dof == "Roll" else 1.0))
    with ThreadPoolExecutor(max_workers=min(max_workers, max(1, len(tasks)))) as pool:
        pairs = list(pool.map(lambda args: run_one(*args), tasks))
    results = [r for r, _ in pairs if r is not None]
    records = [r for _, r in pairs]
    pd.DataFrame([flat(r) for r in results]).to_csv(OUT / "05_metrics" / f"{stage}_metrics.csv", index=False, encoding="utf-8-sig")
    dump_json(OUT / "08_logs" / f"{stage}_run_manifest.json", records)
    copy_once(OUT / "08_logs" / f"{stage}_run_manifest.json", OUT / "logs" / f"{stage}_run_manifest.json")
    return results


def flat(row: dict) -> dict:
    return {k: v for k, v in row.items() if not isinstance(v, np.ndarray)}


def score(group: list[dict]) -> dict:
    return {
        "J_period6": float(np.nanmax([r["period_error_pct"] for r in group])),
        "roll_period_error_pct": float(next(r["period_error_pct"] for r in group if r["dof"] == "Roll")),
        "all_period_under_2": bool(all(r["period_error_pct"] < 2.0 for r in group)),
        "lock_pass": bool(all(r["lock_pass"] for r in group)),
        "locked_dof_pass": bool(all(r["lock_pass"] for r in group if r["dof"] != "Roll")),
    }


def model(model_id: str, actions: list[dict], roll_scale: float = 1.0) -> dict:
    return {"model_id": model_id, "actions": canonical(actions), "roll_scale": roll_scale}


def plot_six(results: list[dict], path: Path, title: str) -> None:
    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    for ax, dof in zip(axes.flat, DOFS):
        row = next(r for r in results if r["dof"] == dof)
        ax.plot(row["aligned_time_s"], row["reference_aligned"], label="Curated reference", lw=1.1)
        ax.plot(row["aligned_time_s"], row["simulation_aligned"], label="Candidate", lw=1.0)
        ax.set_title(f"{dof}: T err {row['period_error_pct']:.2f}%")
        ax.grid(alpha=0.25)
        ax.legend(fontsize=8)
    fig.suptitle(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def plot_roll(results: list[dict], path: Path) -> None:
    row = next(r for r in results if r["dof"] == "Roll")
    mask = row["aligned_time_s"] <= min(220.0, float(row["aligned_time_s"][-1]))
    plt.figure(figsize=(12, 5))
    plt.plot(row["aligned_time_s"][mask], row["reference_aligned"][mask], label="Curated Roll reference")
    plt.plot(row["aligned_time_s"][mask], row["simulation_aligned"][mask], label="Candidate Roll")
    plt.title(f"Roll period closure zoom: error {row['period_error_pct']:.3f}%")
    plt.grid(alpha=0.25)
    plt.legend()
    path.parent.mkdir(parents=True, exist_ok=True)
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()


def r0() -> list[dict]:
    init()
    results = run_models("R0", [model("R0_starting_model", [])])
    write_stage("R0", results)
    roll = next(r for r in results if r["dof"] == "Roll")
    direction = "too_short" if roll["simulation_period_s"] < roll["reference_period_s"] else "too_long"
    write_text(
        OUT / "R1_roll_error_direction_check.md",
        f"""
# R1 Roll Error Direction Check

- T_roll_ref = `{roll['reference_period_s']:.6f} s`
- T_roll_sim = `{roll['simulation_period_s']:.6f} s`
- signed_roll_period_error = `{roll['signed_period_error_pct']:.3f}%`
- direction = `{direction}`

Roll simulation period is {'shorter' if direction == 'too_short' else 'longer'} than the curated reference. Priority actions are {'decrease K_roll_roll or increase PtfmRIner' if direction == 'too_short' else 'increase K_roll_roll or decrease PtfmRIner'}.
""",
    )
    write_text(OUT / "R0_reproducibility_check.md", "# R0 Reproducibility Check\n\n" + table(results))
    return results


def table(results: list[dict]) -> str:
    lines = ["| DOF | T_ref | T_sim | Signed error | Period error | Lock pass |", "|---|---:|---:|---:|---:|---|"]
    for r in results:
        lines.append(f"| {r['dof']} | {r['reference_period_s']:.3f} | {r['simulation_period_s']:.3f} | {r['signed_period_error_pct']:.3f}% | {r['period_error_pct']:.3f}% | {r['lock_pass']} |")
    return "\n".join(lines)


def write_stage(stage: str, results: list[dict]) -> None:
    if not results:
        return
    pd.DataFrame([flat(r) for r in results]).to_csv(OUT / "05_metrics" / f"{stage}_metrics.csv", index=False, encoding="utf-8-sig")
    plot_six(results, OUT / "06_figures" / f"{stage}_six_dof_comparison.png", f"{stage} roll closure")
    copy_once(OUT / "06_figures" / f"{stage}_six_dof_comparison.png", OUT / "figures" / f"{stage}_six_dof_comparison.png")


def summarize_candidates(results: list[dict]) -> pd.DataFrame:
    rows = []
    for mid, group in pd.DataFrame([flat(r) for r in results]).groupby("model_id"):
        gs = group.to_dict("records")
        if len({r["dof"] for r in gs}) != len(DOFS):
            rows.append(
                {
                    "model_id": mid,
                    "J_period6": math.inf,
                    "roll_period_error_pct": math.inf,
                    "all_period_under_2": False,
                    "lock_pass": False,
                    "locked_dof_pass": False,
                    "complete_six_dof": False,
                }
            )
            continue
        rows.append({"model_id": mid, **score(gs), "complete_six_dof": True})
    return pd.DataFrame(rows).sort_values(["lock_pass", "all_period_under_2", "roll_period_error_pct"], ascending=[False, False, True])


def sweeps() -> list[dict]:
    r0_results = r0()
    roll = next(r for r in r0_results if r["dof"] == "Roll")
    too_short = roll["simulation_period_s"] < roll["reference_period_s"]
    k_factors = [0.98, 0.95, 0.92, 0.90, 0.85, 0.80] if too_short else [1.02, 1.05, 1.08, 1.10, 1.15, 1.20]
    i_factors = [1.02, 1.05, 1.08, 1.10, 1.15] if too_short else [0.98, 0.95, 0.92, 0.90, 0.85]
    r2 = run_models("R2", [model(f"R2_rollK_{str(f).replace('.', 'p')}", [{"parameter": "AddCLin_roll_factor", "factor": f, "basis": "roll period closure"}]) for f in k_factors])
    pd.DataFrame([flat(r) for r in r2]).to_csv(OUT / "R2_roll_stiffness_refine.csv", index=False, encoding="utf-8-sig")
    r2_summary = summarize_candidates(r2)
    write_text(OUT / "R2_best_roll_stiffness_candidate.md", f"# R2 Best Roll Stiffness Candidate\n\n{r2_summary.head(3).to_markdown(index=False)}")
    r3 = run_models("R3", [model(f"R3_rollIner_{str(f).replace('.', 'p')}", [{"parameter": "PtfmRIner_factor", "factor": f, "basis": "roll period closure"}]) for f in i_factors])
    pd.DataFrame([flat(r) for r in r3]).to_csv(OUT / "R3_roll_inertia_refine.csv", index=False, encoding="utf-8-sig")
    r3_summary = summarize_candidates(r3)
    write_text(OUT / "R3_best_roll_inertia_candidate.md", f"# R3 Best Roll Inertia Candidate\n\n{r3_summary.head(3).to_markdown(index=False)}")
    best_stage_results = r2 + r3
    best_summary = summarize_candidates(best_stage_results)
    if not bool(best_summary.iloc[0]["all_period_under_2"]):
        coupling_models = []
        for pair in [("Roll", "Pitch"), ("Roll", "Heave")]:
            for f in [-0.5, -0.25, -0.1, 0.0, 0.1, 0.25, 0.5]:
                coupling_models.append(model(f"R4_{pair[0]}_{pair[1]}_{str(f).replace('-', 'n').replace('.', 'p')}", [{"parameter": "AddCLin_offdiag_estimated", "dof_a": pair[0], "dof_b": pair[1], "factor": f, "basis": "roll coupling local scan"}]))
        r4 = run_models("R4", coupling_models)
        pd.DataFrame([flat(r) for r in r4]).to_csv(OUT / "R4_roll_coupling_refine.csv", index=False, encoding="utf-8-sig")
        r4_summary = summarize_candidates(r4)
        write_text(OUT / "R4_coupling_stability_report.md", f"# R4 Coupling Stability Report\n\n{r4_summary.head(10).to_markdown(index=False)}")
        best_stage_results.extend(r4)
    else:
        pd.DataFrame().to_csv(OUT / "R4_roll_coupling_refine.csv", index=False)
        write_text(OUT / "R4_coupling_stability_report.md", "# R4 Coupling Stability Report\n\nNot executed because R2/R3 found a passing candidate.")
    all_summary = summarize_candidates(best_stage_results)
    best_ids = all_summary.head(6)["model_id"].tolist()
    r5_results = [r for r in best_stage_results if r["model_id"] in best_ids]
    pd.DataFrame([flat(r) for r in r5_results]).to_csv(OUT / "R5_integrated_roll_period_search.csv", index=False, encoding="utf-8-sig")
    all_summary.to_csv(OUT / "R5_top_candidates.csv", index=False, encoding="utf-8-sig")
    write_text(OUT / "R5_summary.md", f"# R5 Summary\n\n{all_summary.head(10).to_markdown(index=False)}")
    return best_stage_results


def package_final(all_results: list[dict]) -> None:
    summary = summarize_candidates(all_results)
    best_id = str(summary.iloc[0]["model_id"])
    final = [r for r in all_results if r["model_id"] == best_id]
    write_stage("R6", final)
    package_created = False
    if bool(summary.iloc[0]["all_period_under_2"]):
        package = OUT / "curated_period2_closed_model"
        src = next((OUT / "04_stage_runs").rglob(f"{best_id}/input_template"))
        copy_once(src, package / "OpenFAST_input_files")
        copy_once(src.parent / "parameter_change.json", package / "parameter_change.json")
        copy_once(OUT / "07_diffs" / f"{best_id}.patch", package / "diff.patch")
        pd.DataFrame([flat(r) for r in final]).to_csv(package / "metrics.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame([{"model_id": r["model_id"], "dof": r["dof"], "nrmse_pct": r["nrmse_pct"], "envelope_error_pct": r["envelope_error_pct"]} for r in final]).to_csv(package / "shape_metrics.csv", index=False, encoding="utf-8-sig")
        plot_six(final, package / "six_dof_comparison.png", "Curated period2 closed model")
        plot_roll(final, package / "roll_zoom.png")
        dump_json(package / "objective_summary.json", score(final))
        pd.DataFrame(json.loads((src.parent / "parameter_change.json").read_text(encoding="utf-8"))["actions"]).to_csv(package / "calibration_parameter_table.csv", index=False, encoding="utf-8-sig")
        write_text(package / "limitations.md", "Calibration-to-data / presentation-fit model; not a FOCAL C4 as-built model.")
        package_created = True
    else:
        write_text(OUT / "no_roll_period_closure_reason.md", f"# No Roll Period Closure Reason\n\nClosest candidate `{best_id}` has Roll period error `{float(summary.iloc[0]['roll_period_error_pct']):.3f}%` and all-period pass `{summary.iloc[0]['all_period_under_2']}`.")
    pd.DataFrame([flat(r) for r in all_results]).to_csv(OUT / "metrics_all_runs.csv", index=False, encoding="utf-8-sig")
    summary.to_csv(OUT / "objective_history.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([flat(r) for r in final]).to_csv(OUT / "final_six_dof_error_table.csv", index=False, encoding="utf-8-sig")
    param_rows = []
    for path in sorted((OUT / "04_stage_runs").rglob("parameter_change.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for action in data.get("actions", []) or [{"parameter": "none"}]:
            param_rows.append({"model_id": data["model_id"], "stage": data["stage"], **action})
    pd.DataFrame(param_rows).to_csv(OUT / "parameter_change_log.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame([{"cleanup_action": "none", "reason": "No cleanup performed in this roll-closure pass."}]).to_csv(OUT / "cleanup_manifest.csv", index=False, encoding="utf-8-sig")
    write_text(OUT / "not_as_built_disclaimer.md", "# Not As-Built Disclaimer\n\nThis model is calibration-to-data / presentation-fit only, not a FOCAL C4 as-built model.")
    final_report(final, summary.iloc[0], package_created)


def final_report(final: list[dict], best: pd.Series, package_created: bool) -> None:
    roll = next(r for r in final if r["dof"] == "Roll")
    locked_broken = [r["dof"] for r in final if r["dof"] != "Roll" and not r["lock_pass"]]
    rows = table(final)
    text = f"""
# Final Curated Roll Period Closure Report

1. Completely used curated reference: `true`.
2. Used old Heave reference: `false`.
3. Roll period direction from starting model: `too_short` (`T_sim < T_ref`).
4. Most effective parameter/candidate: `{best['model_id']}` with Roll error `{float(best['roll_period_error_pct']):.3f}%`.
5. Achieved all six period errors <2%: `{bool(best['all_period_under_2'])}`.
6. Broke Surge/Sway/Heave/Pitch/Yaw locks: `{bool(locked_broken)}`; broken locks: `{', '.join(locked_broken) if locked_broken else 'none'}`.
7. Final model can be used as curated period2 presentation-fit candidate: `{package_created}`.
8. It is not as-built because parameters are calibrated to data without official FOCAL C4 as-built HydroDyn/WAMIT, mass/inertia, coupling, damping, and release evidence.
9. Enter shape refinement next: `{package_created}`.

## Final Candidate

- model_id: `{best['model_id']}`
- Roll T_ref: `{roll['reference_period_s']:.3f} s`
- Roll T_sim: `{roll['simulation_period_s']:.3f} s`
- Roll period error: `{roll['period_error_pct']:.3f}%`

{rows}
"""
    write_text(OUT / "final_curated_roll_period_closure_report.md", text)
    write_text(OUT / "09_final_report" / "final_curated_roll_period_closure_report.md", text)
    write_text(OUT / "R6_final_candidate_summary.md", f"# R6 Final Candidate Summary\n\n- model_id: `{best['model_id']}`\n- all_period_under_2: `{best['all_period_under_2']}`\n- roll_period_error_pct: `{float(best['roll_period_error_pct']):.3f}%`")
    write_text(OUT / "advisor_roll_period_closure_summary.md", f"# Advisor Roll Period Closure Summary\n\nClosest candidate `{best['model_id']}`; full period pass `{best['all_period_under_2']}`; Roll error `{float(best['roll_period_error_pct']):.3f}%`.")


def run_all() -> None:
    init()
    all_results = sweeps()
    package_final(all_results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=["init", "all"], default="all")
    args = parser.parse_args()
    if args.stage == "init":
        init()
    else:
        run_all()


if __name__ == "__main__":
    main()
