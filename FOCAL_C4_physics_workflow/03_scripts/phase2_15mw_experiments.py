from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import phase2_15mw_runner as core


MOORDYN_NAME = "IEA-15-240-RWT-UMaineSemi_MoorDyn.dat"


@dataclass(frozen=True)
class Trial:
    run_id: str
    category: str
    case_id: str
    factor: float
    modification: str
    hypothesis: str
    observation: str
    expected_response: str
    expected_metric: str
    expected_direction: str
    confidence: str


TRIALS = [
    Trial(
        "A1_mooring_EA_plus10",
        "A_surge_mooring_or_mass",
        "FD_SURGE",
        1.10,
        "moordyn_ea",
        "The equivalent horizontal mooring stiffness may be too low for the represented test condition.",
        "Surge simulated period is longer than the experimental period.",
        "Surge period should decrease.",
        "period_s",
        "decrease",
        "low",
    ),
    Trial(
        "A2_mooring_EA_plus20",
        "A_surge_mooring_or_mass",
        "FD_SURGE",
        1.20,
        "moordyn_ea",
        "The equivalent horizontal mooring stiffness may be too low for the represented test condition.",
        "Surge simulated period is longer than the experimental period.",
        "Surge period should decrease.",
        "period_s",
        "decrease",
        "low",
    ),
    Trial(
        "B1_pitch_AddBQuad_minus10",
        "B_pitch_damping_or_tmd",
        "FD_PITCH",
        0.90,
        "pitch_addbquad",
        "The pitch quadratic hydrodynamic damping represented in the model may exceed the test-equivalent damping.",
        "Pitch simulated damping ratio is much larger than the experimental damping ratio.",
        "Pitch damping ratio should decrease.",
        "damping_ratio",
        "decrease",
        "medium",
    ),
    Trial(
        "B2_pitch_AddBQuad_minus20",
        "B_pitch_damping_or_tmd",
        "FD_PITCH",
        0.80,
        "pitch_addbquad",
        "The pitch quadratic hydrodynamic damping represented in the model may exceed the test-equivalent damping.",
        "Pitch simulated damping ratio is much larger than the experimental damping ratio.",
        "Pitch damping ratio should decrease.",
        "damping_ratio",
        "decrease",
        "medium",
    ),
    Trial(
        "D1_yaw_AddBQuad_minus10",
        "D_yaw_mooring_geometry",
        "FD_YAW",
        0.90,
        "yaw_addbquad",
        "The yaw quadratic damping may be too high, while the much longer period also indicates unresolved restoring-geometry mismatch.",
        "Yaw simulated period and damping ratio are both larger than experimental values.",
        "Yaw damping ratio should decrease; period is not expected to be corrected by this test.",
        "damping_ratio",
        "decrease",
        "low",
    ),
    Trial(
        "D2_yaw_AddBQuad_minus20",
        "D_yaw_mooring_geometry",
        "FD_YAW",
        0.80,
        "yaw_addbquad",
        "The yaw quadratic damping may be too high, while the much longer period also indicates unresolved restoring-geometry mismatch.",
        "Yaw simulated period and damping ratio are both larger than experimental values.",
        "Yaw damping ratio should decrease; period is not expected to be corrected by this test.",
        "damping_ratio",
        "decrease",
        "low",
    ),
]


def clean_row(result: dict) -> dict:
    ref = result["reference_metrics"]
    sim = result["simulation_metrics"]
    return {
        "run_id": result["run_id"],
        "case_id": result["case_id"],
        "dof": result["dof"],
        "reference_period_s": ref["period_s"],
        "simulation_period_s": sim["period_s"],
        "reference_damping_ratio": ref["damping_ratio"],
        "simulation_damping_ratio": sim["damping_ratio"],
        "reference_frequency_hz": ref["dominant_frequency_hz"],
        "simulation_frequency_hz": sim["dominant_frequency_hz"],
        "period_error_pct": result["period_error_pct"],
        "damping_error_pct": result["damping_error_pct"],
        "amplitude_error_pct": result["amplitude_error_pct"],
        "phase_error_pct_of_period": result["phase_error_pct_of_period"],
        "frequency_error_pct": result["frequency_error_pct"],
        "nrmse_pct": result["nrmse_pct"],
        "objective_pct": result["objective_pct"],
        "pass_lt_2pct": result["pass_lt_2pct"],
    }


def case_for_id(case_id: str) -> core.ReferenceCase:
    return next(case for case in core.CASES if case.case_id == case_id)


def baseline_results() -> dict[str, dict]:
    results = {}
    for case in core.CASES:
        ref, _ = core.prepare_reference(case)
        output = core.OUT / "02_baseline_run" / case.case_id / "input_files" / core.FST_NAME.replace(".fst", ".out")
        results[case.case_id] = core.compare_signals(case, ref, core.parse_openfast_output(output), f"BASELINE_{case.case_id}")
    return results


def apply_trial_parameter(input_dir: Path, trial: Trial) -> dict:
    if trial.modification == "moordyn_ea":
        path = input_dir / MOORDYN_NAME
        old, new = core.patch_moordyn_ea(path, trial.factor)
        parameter = "LineType main EA"
    elif trial.modification == "pitch_addbquad":
        path = input_dir / core.HD_NAME
        old, new = core.patch_matrix_value(path, "AddBQuad", 5, 5, trial.factor)
        parameter = "AddBQuad[5,5] pitch rotational quadratic damping"
    elif trial.modification == "yaw_addbquad":
        path = input_dir / core.HD_NAME
        old, new = core.patch_matrix_value(path, "AddBQuad", 6, 6, trial.factor)
        parameter = "AddBQuad[6,6] yaw rotational quadratic damping"
    else:
        raise ValueError(trial.modification)
    return {
        "modified_file": path.name,
        "modified_parameter": parameter,
        "baseline_value": old,
        "new_value": new,
        "modification_ratio_pct": (trial.factor - 1.0) * 100.0,
    }


def response_direction(trial: Trial, baseline: dict, result: dict) -> bool:
    old = baseline["simulation_metrics"][trial.expected_metric]
    new = result["simulation_metrics"][trial.expected_metric]
    return new < old if trial.expected_direction == "decrease" else new > old


def plot_target(result: dict, baseline: dict, path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(12, 4.5), constrained_layout=True)
    ax.plot(result["aligned_time_s"], result["reference_aligned"], label="Experiment", lw=1.2)
    ax.plot(baseline["aligned_time_s"], baseline["simulation_aligned"], label="Baseline", lw=1.0)
    ax.plot(result["aligned_time_s"], result["simulation_aligned"], label="Trial", lw=1.0)
    ax.set_title(title)
    ax.set_xlabel("Time after release (s)")
    ax.set_ylabel("m" if result["dof"] in {"Surge", "Sway", "Heave"} else "deg")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def run_trials() -> None:
    base = baseline_results()
    plan = []
    change_rows = []
    decisions = []
    results = []
    failed = []
    for trial in TRIALS:
        case = case_for_id(trial.case_id)
        reference, info = core.prepare_reference(case)
        run_dir = core.OUT / "03_experiment_runs" / trial.category / trial.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        input_dir = core.copy_case_input(run_dir)
        setup_changes = core.configure_free_decay(input_dir, case, info["initial_displacement_for_simulation"])
        parameter_path = run_dir / "parameter_change.json"
        if parameter_path.exists():
            stored_change = json.loads(parameter_path.read_text(encoding="utf-8"))
            physical_change = stored_change["physical_parameter_changes"][0]
        else:
            physical_change = apply_trial_parameter(input_dir, trial)
        plan_item = {
            "run_id": trial.run_id,
            "category": trial.category,
            "observation": trial.observation,
            "hypothesis": trial.hypothesis,
            "controlled_experiment": f"Apply factor {trial.factor:.2f} to {physical_change['modified_parameter']}.",
            **physical_change,
            "fixed_conditions": [
                "all unlisted physical model parameters",
                "initial condition derived by the baseline processing method",
                "still-water free-decay switches",
                "simulation length and output time step",
            ],
            "expected_response": trial.expected_response,
            "stop_condition": "Stop after bounded directional tests; do not escalate without configuration evidence.",
            "rollback_condition": "OpenFAST failure, instability, opposite response, or physical plausibility concern.",
            "confidence": trial.confidence,
        }
        plan.append(plan_item)
        core.write_diff(core.MODEL_DIR, input_dir, run_dir / "input_diff.patch")
        if not parameter_path.exists():
            core.json_dump(
                parameter_path,
                {"run_id": trial.run_id, "physical_parameter_changes": [physical_change], "test_configuration_changes": setup_changes},
            )
        returncode, output = core.run_openfast(input_dir, run_dir / "openfast_run.log")
        output_dir = run_dir / "output_files"
        output_dir.mkdir(exist_ok=True)
        if output.exists():
            shutil.copy2(output, output_dir / output.name)
        if returncode != 0 or not output.exists():
            failure = {"run_id": trial.run_id, "status": "failed", "returncode": returncode}
            failed.append(failure)
            core.json_dump(run_dir / "decision.json", {**failure, "decision": "rollback"})
            continue
        result = core.compare_signals(case, reference, core.parse_openfast_output(output), trial.run_id)
        results.append(result)
        target_improved = result["objective_pct"] < base[trial.case_id]["objective_pct"]
        direction_ok = response_direction(trial, base[trial.case_id], result)
        if target_improved and direction_ok:
            decision = "support_hypothesis"
            reason = "Target-DOF objective improved and the measured response moved in the predicted direction."
        elif not direction_ok or not target_improved:
            decision = "reject_hypothesis"
            reason = "Target-DOF objective did not improve or the response did not follow the predicted direction."
        else:
            decision = "inconclusive"
            reason = "The bounded perturbation produced insufficient separation."
        decision_item = {
            "run_id": trial.run_id,
            "decision": decision,
            "reason": reason,
            "baseline_target_objective_pct": base[trial.case_id]["objective_pct"],
            "trial_target_objective_pct": result["objective_pct"],
            "objective_change_pct_points": result["objective_pct"] - base[trial.case_id]["objective_pct"],
            "predicted_direction_observed": direction_ok,
            "cross_dof_effect": "not quantified in this target-only diagnostic run",
            "overfitting_risk": "high if adopted without multi-DOF reruns and experimental-configuration confirmation",
            "recommend_accept_parameter": False,
        }
        decisions.append(decision_item)
        row = clean_row(result)
        row.update({"category": trial.category, **physical_change, "decision": decision})
        change_rows.append(row)
        pd.DataFrame([row]).to_csv(run_dir / "metrics.csv", index=False)
        figures = run_dir / "figures"
        figures.mkdir(exist_ok=True)
        plot_target(result, base[trial.case_id], figures / f"{trial.run_id}.png", trial.run_id)
        plot_target(
            result,
            base[trial.case_id],
            core.OUT / "05_figures" / "controlled_experiment_figures" / f"{trial.run_id}.png",
            trial.run_id,
        )
        core.json_dump(run_dir / "decision.json", decision_item)
    c_audit(base)
    core.json_dump(core.OUT / "06_logs" / "experiment_plan.json", plan + c_plan())
    core.json_dump(core.OUT / "06_logs" / "decision_log.json", decisions + c_decision())
    pd.DataFrame(change_rows).to_csv(core.OUT / "06_logs" / "parameter_change_log.csv", index=False)
    if failed:
        previous = pd.read_csv(core.OUT / "06_logs" / "failed_runs_log.csv") if (core.OUT / "06_logs" / "failed_runs_log.csv").stat().st_size else pd.DataFrame()
        pd.concat([previous, pd.DataFrame(failed)], ignore_index=True).to_csv(core.OUT / "06_logs" / "failed_runs_log.csv", index=False)
    all_rows = [clean_row(result) | {"category": "baseline"} for result in base.values()]
    all_rows += [clean_row(result) | {"category": next(t.category for t in TRIALS if t.run_id == result["run_id"])} for result in results]
    pd.DataFrame(all_rows).to_csv(core.OUT / "04_metrics" / "metrics_all_runs.csv", index=False)
    history = [{"run_id": row["run_id"], "dof": row["dof"], "objective_pct": row["objective_pct"], "category": row["category"]} for row in all_rows]
    pd.DataFrame(history).to_csv(core.OUT / "04_metrics" / "objective_history.csv", index=False)


def c_plan() -> list[dict]:
    return [{
        "run_id": "C1_release_peak_tail_median_audit",
        "category": "C_time_alignment_or_initial_condition",
        "observation": "Measured records contain pre-release portions and nonzero equilibrium offsets.",
        "hypothesis": "A transparent release-time and equilibrium convention is required for a valid comparison.",
        "controlled_experiment": "Document raw versus processed reference traces; no physical parameter is modified.",
        "modified_file": "derived reference copies only",
        "modified_parameter": "release origin and tail-median coordinate origin",
        "expected_response": "Comparison begins at the measured release and offsets are explicit.",
        "confidence": "high",
    }]


def c_decision() -> list[dict]:
    return [{
        "run_id": "C1_release_peak_tail_median_audit",
        "decision": "inconclusive",
        "reason": "Alignment is necessary and documented, but it cannot explain the large physical-response discrepancies alone.",
        "recommend_accept_parameter": False,
    }]


def c_audit(base: dict[str, dict]) -> None:
    path = core.OUT / "03_experiment_runs" / "C_time_alignment_or_initial_condition" / "C1_release_peak_tail_median_audit"
    path.mkdir(parents=True, exist_ok=True)
    (path / "input_files").mkdir(exist_ok=True)
    (path / "output_files").mkdir(exist_ok=True)
    (path / "figures").mkdir(exist_ok=True)
    case = case_for_id("FD_SURGE")
    raw = core.read_reference(case.csv_path)
    processed, info = core.prepare_reference(case)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), constrained_layout=True)
    axes[0].plot(raw["Time"], raw["Surge"], lw=1)
    axes[0].axvline(info["release_time_s_raw"], color="tab:red", ls="--", label="Detected release")
    axes[0].set_title("Raw Surge record and detected release")
    axes[0].legend()
    axes[1].plot(processed["Time"], processed["Surge"], label="Processed reference", lw=1)
    axes[1].plot(base["FD_SURGE"]["aligned_time_s"], base["FD_SURGE"]["simulation_aligned"], label="Baseline", lw=1)
    axes[1].set_title("Post-release aligned comparison")
    axes[1].legend()
    for ax in axes:
        ax.grid(alpha=0.25)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Surge (m)")
    fig.savefig(core.OUT / "05_figures" / "time_alignment_before_after.png", dpi=160)
    fig.savefig(path / "figures" / "alignment_audit.png", dpi=160)
    plt.close(fig)
    (path / "input_files" / "README.md").write_text("No OpenFAST input file is modified in this data-processing audit.\n", encoding="utf-8")
    (path / "output_files" / "README.md").write_text("No OpenFAST solver output is generated in this data-processing audit.\n", encoding="utf-8")
    (path / "openfast_run.log").write_text("No OpenFAST execution: C1 is a documented data-processing and initial-condition audit only.\n", encoding="utf-8")
    pd.DataFrame([{
        "run_id": "C1_release_peak_tail_median_audit",
        "dof": "Surge",
        "release_time_s_raw": info["release_time_s_raw"],
        "initial_displacement_for_simulation": info["initial_displacement_for_simulation"],
        "physical_parameter_modified": False,
        "objective_claim": "none",
    }]).to_csv(path / "metrics.csv", index=False)
    core.json_dump(path / "parameter_change.json", {"run_id": "C1_release_peak_tail_median_audit", "physical_parameter_changes": [], "data_processing_operations": info["processing"]})
    core.json_dump(path / "decision.json", c_decision()[0])


def diagnostic_figures(base: dict[str, dict]) -> None:
    for result in base.values():
        fig, ax = plt.subplots(figsize=(11, 4), constrained_layout=True)
        ax.plot(result["aligned_time_s"], result["reference_aligned"], label="Experiment", lw=1.1)
        ax.plot(result["aligned_time_s"], result["simulation_aligned"], label="Baseline", lw=1.0)
        ax.set_title(f"{result['dof']} baseline diagnostic: J={result['objective_pct']:.2f}%")
        ax.set_xlabel("Time after release (s)")
        ax.set_ylabel("m" if result["dof"] in {"Surge", "Sway", "Heave"} else "deg")
        ax.grid(alpha=0.25)
        ax.legend()
        fig.savefig(core.OUT / "05_figures" / "controlled_experiment_figures" / f"{result['dof']}_baseline_diagnostic.png", dpi=160)
        plt.close(fig)


def finalize() -> None:
    base = baseline_results()
    diagnostic_figures(base)
    final_rows = [clean_row(r) | {"final_parameter_source": "unaltered physical baseline retained"} for r in base.values()]
    final_df = pd.DataFrame(final_rows)
    baseline_df = pd.DataFrame([clean_row(r) for r in base.values()])
    baseline_df.to_csv(core.OUT / "04_metrics" / "metrics_baseline.csv", index=False)
    baseline_df.to_csv(core.OUT / "02_baseline_run" / "baseline_metrics.csv", index=False)
    final_df.to_csv(core.OUT / "04_metrics" / "final_6dof_error_table.csv", index=False)
    baseline_df.to_csv(core.OUT / "04_metrics" / "baseline_error_table.csv", index=False)
    core.plot_comparison(list(base.values()), core.OUT / "05_figures" / "final_vs_reference_6dof.png", "Final retained baseline vs FOCAL Reference")
    history = pd.read_csv(core.OUT / "04_metrics" / "objective_history.csv")
    fig, ax = plt.subplots(figsize=(12, 5), constrained_layout=True)
    for dof, group in history.groupby("dof"):
        ax.plot(group["run_id"], group["objective_pct"], marker="o", label=dof)
    ax.set_ylabel("Target-DOF objective J (%)")
    ax.set_xlabel("Run")
    ax.tick_params(axis="x", rotation=35)
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(core.OUT / "05_figures" / "objective_history.png", dpi=160)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(10, 4.5), constrained_layout=True)
    x = np.arange(len(final_df))
    vals = final_df["objective_pct"].to_numpy()
    ax.bar(x - 0.18, vals, width=0.36, label="Baseline")
    ax.bar(x + 0.18, vals, width=0.36, label="Final retained")
    ax.axhline(2.0, color="tab:red", ls="--", label="2% pass limit")
    ax.set_xticks(x, final_df["dof"])
    ax.set_ylabel("J (%)")
    ax.set_title("Six-DOF error: no diagnostic perturbation adopted")
    ax.legend()
    ax.grid(axis="y", alpha=0.25)
    fig.savefig(core.OUT / "05_figures" / "error_reduction_6dof.png", dpi=160)
    plt.close(fig)
    write_reports(final_df, history)


def write_reports(final_df: pd.DataFrame, history: pd.DataFrame) -> None:
    decisions = json.loads((core.OUT / "06_logs" / "decision_log.json").read_text(encoding="utf-8"))
    supported = [x for x in decisions if x["decision"] == "support_hypothesis"]
    decision_df = pd.DataFrame(decisions)
    decision_df.to_csv(core.OUT / "04_metrics" / "decision_table.csv", index=False)
    pd.DataFrame([{"hypothesis": x.get("run_id"), "classification": x["decision"], "reason": x["reason"]} for x in decisions]).to_csv(core.OUT / "04_metrics" / "hypothesis_summary_table.csv", index=False)
    pd.DataFrame([{"parameter_set": "baseline physical parameters", "recommended": True, "reason": "Diagnostic perturbations were not validated as a combined six-DOF parameter set."}]).to_csv(core.OUT / "04_metrics" / "recommended_parameter_set.csv", index=False)
    pd.DataFrame([
        {"risk": "Experimental configuration correspondence", "review_priority": "high", "action": "Confirm TMD, umbilical, mooring geometry, water depth, and scaling."},
        {"risk": "Trial transfer across DOFs", "review_priority": "high", "action": "Run all six DOFs for any candidate parameter set before adoption."},
        {"risk": "Release and initial-condition representation", "review_priority": "medium", "action": "Review release detection and equilibrium convention against laboratory protocol."},
    ]).to_csv(core.OUT / "04_metrics" / "human_review_risk_table.csv", index=False)
    core.json_dump(core.OUT / "06_logs" / "hypothesis_log.json", {
        "candidate_hypotheses": [
            {"id": "A", "statement": "Surge/Sway/Yaw restoring mismatch may involve mooring equivalent stiffness or test geometry.", "tested_parameter": "MoorDyn main EA", "status": "bounded diagnostic only"},
            {"id": "B", "statement": "Pitch/Roll damping mismatch may involve hydrodynamic quadratic damping, TMD, or controller/test state.", "tested_parameter": "HydroDyn AddBQuad[5,5]", "status": "bounded diagnostic only"},
            {"id": "C", "statement": "Comparison depends on documented release/equilibrium alignment.", "tested_parameter": "no physical parameter", "status": "documented; not a physical cause conclusion"},
            {"id": "D", "statement": "Yaw has both damping and restoring/geometry mismatch.", "tested_parameter": "HydroDyn AddBQuad[6,6]", "status": "bounded damping diagnostic only"},
        ],
        "decisions": decisions,
    })
    (core.OUT / "06_logs" / "hypothesis_selection_reasoning.md").write_text(
        """# Hypothesis Selection Reasoning

- A was selected because Surge, Sway, and Yaw simulated periods are substantially longer than measured values, making a bounded MoorDyn restoring-direction check informative. MoorDyn `EA` was used only as a sensitivity probe because the actual test-equivalent mooring properties are unconfirmed.
- B was selected because Pitch and Roll simulated damping ratios greatly exceed measured damping. A narrow Pitch `AddBQuad[5,5]` perturbation tests direction without changing inertia, TMD, or controller assumptions.
- C was selected because release origin and equilibrium offset are necessary comparison conventions. It modifies only derived copies and records no improved physical model claim.
- D was selected because Yaw has both excess period and damping; the damping-only probe separates whether changing `AddBQuad[6,6]` can improve the damping component without pretending to cure restoring mismatch.

The trial set is diagnostic rather than optimizing: no broad search, no simultaneous physical changes, and no adoption without six-DOF regression and human confirmation of the laboratory configuration.
""",
        encoding="utf-8",
    )
    failed_path = core.OUT / "06_logs" / "failed_runs_log.csv"
    if not failed_path.exists() or failed_path.stat().st_size <= 2:
        pd.DataFrame(columns=["run_id", "status", "returncode", "reason"]).to_csv(failed_path, index=False)
    baseline_table = final_df[["dof", "reference_period_s", "simulation_period_s", "reference_damping_ratio", "simulation_damping_ratio", "objective_pct", "pass_lt_2pct"]].to_markdown(index=False, floatfmt=".4f")
    trial_table = history.to_markdown(index=False, floatfmt=".3f")
    report = f"""# Phase 2 15 MW Free-Decay Diagnosis Report

## Executive Summary

Real OpenFAST runs were executed for six FOCAL Campaign 4 free-decay records using the IEA-15-240-RWT-UMaineSemi model. The baseline physical coefficients were preserved; only free-decay boundary-condition switches and measured release initial conditions were configured.

The six-DOF `<2%` criterion is **not achieved**. Final status is **Level 2: diagnostic loop completed, fail for acceptance**. Bounded single-factor diagnostic runs were performed, but no perturbed parameter set is adopted because the deviations are large, coupled, and the laboratory configuration correspondence is not yet confirmed.

## Model and Data Provenance

- Source model: `{core.MODEL_DIR}`
- Solver: `{core.EXE}`
- Model: IEA-15-240-RWT-UMaineSemi, water depth 200 m, MoorDyn retained.
- Reference data: six provided FOCAL Campaign 4 CSV records; translations are m and rotations are deg.
- Processing: release at maximum absolute primary-DOF displacement relative to tail median; post-release time shifted to zero; per-channel tail median removed; no filtering.
- Free-decay setup: still water, aerodynamic and servo modules disabled, 600 s duration, 0.05 s output interval.
- Uncertainty requiring review: test-scale conversion provenance, TMD/umbilical representation, exact mooring/release configuration, and model-to-test coordinate equivalence.

## Baseline Comparison

![Baseline comparison](../05_figures/baseline_vs_reference_6dof.png)

{baseline_table}

Major observations: simulated Surge, Sway, and Yaw periods are much longer than the records; Pitch and Roll damping are much higher than measured; Heave damping and amplitude response are grossly inconsistent. These deviations are too broad to treat as a small calibration offset.

## Candidate Diagnosis

- A: Mooring restoring or test-geometry mismatch can affect Surge/Sway/Yaw periods. `EA` perturbations are directional probes, not accepted calibration.
- B: Excess Pitch/Roll decay can reflect HydroDyn damping, TMD, controller/test-state, or inertia mismatch. Only Pitch quadratic damping was probed.
- C: Release time and equilibrium convention must be explicit. Alignment is audited without changing raw data.
- D: Yaw contains damping and restoring/geometry discrepancies; reducing yaw quadratic damping cannot resolve a period mismatch alone.

## Controlled Experiments

{trial_table}

Each physical run changes one coefficient only and stores its input diff, solver log, metric table, plot, and decision in `03_experiment_runs/`. Decisions are hypothesis-direction evidence only; no cross-DOF parameter adoption is inferred from a target-only run.

## Final Result

No diagnostic perturbation is accepted as a final physical parameter change. The final comparison therefore retains the baseline physical parameter set.

![Final comparison](../05_figures/final_vs_reference_6dof.png)

All six DOFs fail `J_i < 2%`; the minimum baseline/final value is Sway at `{final_df["objective_pct"].min():.2f}%`. Level 5 is not reached.

## Supported / Rejected / Inconclusive Hypotheses

- Supported directional trials: {", ".join(x["run_id"] for x in supported) if supported else "none"}.
- Other trial classifications and numeric basis are in `../06_logs/decision_log.json` and `../04_metrics/decision_table.csv`.
- C1 remains inconclusive as a physical cause: transparent alignment is necessary but cannot cure the response disagreement.

## Overfitting and Physical Plausibility Check

The risk of overfitting is high if individual-DOF improvements are adopted without test-configuration confirmation and six-DOF reruns. `EA` and quadratic damping perturbations remain diagnostic only; the model mass, inertia, depth, geometry, and raw measurements were not altered. No recommendation is made to accept a calibrated parameter set.

## Human Review Checklist

Review the original model backup, input diffs, raw-to-processed log, test-scale/full-scale mapping, TMD and umbilical status, mooring geometry/pretension, free-decay initial state, and all run logs before any further parameter proposal.

## Recommended Next Experiments

First obtain or confirm the FOCAL test configuration and full-scale conversion, including TMD/umbilical and mooring/restoring specifications. Then reproduce static equilibrium/restoring tests and rerun all six decays for a single justified candidate configuration before considering a damping refinement.
"""
    (core.OUT / "07_final_report" / "phase2_diagnosis_report.md").write_text(report, encoding="utf-8")
    provenance = """# Model Provenance and Diff

The original OpenFAST tree is copied under `../01_baseline_model_backup/original_openfast_model/`.

Baseline runs alter only the run-copy test configuration: still water, aerodynamic and controller modules disabled, 600 s duration, 0.05 s output, and the measured primary-DOF release displacement. No source physical coefficient is overwritten.

Physical diagnostic diffs are stored per run in `../03_experiment_runs/*/*/input_diff.patch`. They consist of MoorDyn `EA` factors of +10% and +20%, HydroDyn pitch `AddBQuad[5,5]` factors of -10% and -20%, and HydroDyn yaw `AddBQuad[6,6]` factors of -10% and -20%. None is adopted as final.
"""
    (core.OUT / "07_final_report" / "model_provenance_and_diff.md").write_text(provenance, encoding="utf-8")
    checklist = """# Final Human Review Checklist

- [ ] Confirm the original model backup is complete and source files were not overwritten.
- [ ] Confirm raw FOCAL CSV files match the supplied records and only derived copies were processed.
- [ ] Verify experimental scale conversion, coordinate signs, TMD, umbilical, controller state, water depth, and mooring geometry.
- [ ] Inspect every `input_diff.patch`, `openfast_run.log`, `metrics.csv`, and `decision.json`.
- [ ] Do not accept a parameter change based on a single targeted DOF trial.
- [ ] Re-run six DOFs for any human-approved candidate configuration.
- [ ] Confirm explicitly that the present result fails the six-DOF `<2%` criterion.
"""
    (core.OUT / "07_final_report" / "final_human_review_checklist.md").write_text(checklist, encoding="utf-8")
    next_exp = """# Recommended Next Experiments

1. Recover the laboratory configuration record: TMD/umbilical state, line properties, pretension, fairlead/anchor coordinates, mass/inertia and scaling convention.
2. Validate hydrostatic and mooring restoring curves or static offsets before further free-decay tuning.
3. Build one human-approved configuration candidate and run all six DOF decays, retaining the same transparent preprocessing.
4. Only after restoring and configuration checks, conduct narrow damping sweeps with six-DOF regression checks.
"""
    (core.OUT / "07_final_report" / "recommended_next_experiments.md").write_text(next_exp, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["trials", "finalize"])
    args = parser.parse_args()
    if args.action == "trials":
        run_trials()
    else:
        finalize()


if __name__ == "__main__":
    main()
