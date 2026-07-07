from __future__ import annotations

import argparse
import itertools
from pathlib import Path

import pandas as pd

import clean_six_dof_calibration as c


RUN_ROOT = c.ROOT / "04_current_runs" / "six_dof_free_decay_calibration_2"
FINE_CSV = RUN_ROOT / "fine_inertia_grid_roll_pitch.csv"
RESULTS_CSV = c.ROOT / "fine_inertia_grid_results.csv"
FAILED_CSV = c.ROOT / "timeout_or_failed_cases.csv"
BEST_MD = c.ROOT / "current_best_roll_pitch_candidate.md"
REPORT_MD = c.ROOT / "fine_grid_recovery_report.md"


BASE_CLIN = {
    "Surge": 0.9553631042827027,
    "Sway": 0.9357481969040711,
    "Heave": 0.9647898344484139,
    "Roll": 1.0,
    "Pitch": 1.0,
    "Yaw": 1.011472559706516,
}
R_FACTORS = [1.22, 1.24, 1.26, 1.28, 1.30, 1.32]
P_FACTORS = [1.46, 1.48, 1.50, 1.52, 1.54]


def load_rows() -> list[dict]:
    if FINE_CSV.exists() and FINE_CSV.stat().st_size > 0:
        return pd.read_csv(FINE_CSV).to_dict("records")
    return []


def row_complete(row: dict) -> bool:
    return str(row.get("Roll_status", "")) in {"normal_completion", "reused"} and str(row.get("Pitch_status", "")) in {"normal_completion", "reused"}


def write_incremental(rows: list[dict]) -> None:
    FINE_CSV.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(FINE_CSV, index=False, encoding="utf-8-sig")
    df.to_csv(RESULTS_CSV, index=False, encoding="utf-8-sig")
    if df.empty:
        pd.DataFrame(columns=["model_id", "case_id", "status"]).to_csv(FAILED_CSV, index=False, encoding="utf-8-sig")
        return
    failed_rows = []
    for record in df.to_dict("records"):
        for dof in ["Roll", "Pitch"]:
            status = str(record.get(f"{dof}_status", ""))
            if status not in {"normal_completion", "reused"}:
                failed_rows.append(
                    {
                        "model_id": record.get("model_id", ""),
                        "case_id": c.CASES[dof],
                        "dof": dof,
                        "status": status or "not_run",
                        "PtfmRIner_factor": record.get("PtfmRIner_factor", ""),
                        "PtfmPIner_factor": record.get("PtfmPIner_factor", ""),
                    }
                )
    pd.DataFrame(failed_rows, columns=["model_id", "case_id", "dof", "status", "PtfmRIner_factor", "PtfmPIner_factor"]).to_csv(
        FAILED_CSV, index=False, encoding="utf-8-sig"
    )


def write_best_and_report(rows: list[dict], mode: str, attempted: int) -> None:
    df = pd.DataFrame(rows)
    if not df.empty and {"Roll_period_error_pct", "Pitch_period_error_pct"}.issubset(df.columns):
        ranked = df.dropna(subset=["Roll_period_error_pct", "Pitch_period_error_pct"]).copy()
        if not ranked.empty:
            ranked["max_rot_period_error_pct"] = ranked[["Roll_period_error_pct", "Pitch_period_error_pct"]].max(axis=1)
            best = ranked.sort_values(["max_rot_period_error_pct", "Roll_period_error_pct", "Pitch_period_error_pct"]).iloc[0]
            best_text = f"""
# Current Best Roll/Pitch Candidate

- model_id: `{best['model_id']}`
- PtfmRIner_factor: `{best['PtfmRIner_factor']}`
- PtfmPIner_factor: `{best['PtfmPIner_factor']}`
- Roll period error: `{float(best['Roll_period_error_pct']):.3f}%`
- Pitch period error: `{float(best['Pitch_period_error_pct']):.3f}%`
- Max Roll/Pitch period error: `{float(best['max_rot_period_error_pct']):.3f}%`
- Roll/Pitch both under 2%: `{bool(best['max_rot_period_error_pct'] < 2.0)}`
"""
            BEST_MD.write_text(best_text.strip() + "\n", encoding="utf-8")
    residual_csv = c.ROOT / "08_logs" / "fine_grid_residual_processes_before_termination.csv"
    completed = int(sum(row_complete(row) for row in rows))
    total = len(R_FACTORS) * len(P_FACTORS)
    failed_count = 0
    if FAILED_CSV.exists() and FAILED_CSV.stat().st_size > 0:
        failed_count = len(pd.read_csv(FAILED_CSV))
    report = f"""
# Fine Grid Recovery Report

- mode: `{mode}`
- attempted_candidates_this_run: `{attempted}`
- fine_grid_total_candidates: `{total}`
- completed_candidate_rows_recorded: `{completed}`
- incremental_source_csv: `{FINE_CSV}`
- exported_results_csv: `{RESULTS_CSV}`
- timeout_or_failed_cases_csv: `{FAILED_CSV}`
- timeout_or_failed_case_count: `{failed_count}`
- residual_process_log: `{residual_csv}`
- OpenFAST timeout per subprocess: `300 s`
- old Heave reference used: `false`
- scope: Roll/Pitch fine inertia recovery only; no expanded search.
"""
    REPORT_MD.write_text(report.strip() + "\n", encoding="utf-8")


def pending_pairs(rows: list[dict]) -> list[tuple[float, float]]:
    complete = {(float(row["PtfmRIner_factor"]), float(row["PtfmPIner_factor"])) for row in rows if row_complete(row)}
    return [(rf, pf) for rf, pf in itertools.product(R_FACTORS, P_FACTORS) if (rf, pf) not in complete]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-candidates", type=int, default=None)
    parser.add_argument("--mode", default="run")
    args = parser.parse_args()

    c.RUN_ROOT = RUN_ROOT
    c.ensure_dirs()
    rows = load_rows()
    pending = pending_pairs(rows)
    if args.max_candidates is not None:
        pending = pending[: args.max_candidates]

    attempted = 0
    for rf, pf in pending:
        attempted += 1
        model_id = f"fine_inertia_R{str(rf).replace('.', 'p')}_P{str(pf).replace('.', 'p')}"
        row = next((item for item in rows if float(item.get("PtfmRIner_factor", -1)) == rf and float(item.get("PtfmPIner_factor", -1)) == pf), None)
        if row is None:
            row = {"model_id": model_id, "PtfmRIner_factor": rf, "PtfmPIner_factor": pf}
            rows.append(row)
        candidate = c.Candidate(
            model_id,
            BASE_CLIN,
            inertia_factors={"PtfmRIner": rf, "PtfmPIner": pf},
            basis="fine roll/pitch inertia period closure",
        )
        c.create_template(candidate)
        for dof in ["Roll", "Pitch"]:
            if str(row.get(f"{dof}_status", "")) in {"normal_completion", "reused"}:
                continue
            print(f"starting case_id={c.CASES[dof]} RIner_scale={rf} PIner_scale={pf}", flush=True)
            try:
                result, rec = c.run_one(candidate, dof)
            except Exception as exc:  # keep recovery moving and preserve the failure.
                result, rec = None, {"status": "exception", "error": repr(exc), "case_id": c.CASES[dof]}
            row[f"{dof}_status"] = rec.get("status", "unknown")
            if result is not None:
                row[f"{dof}_period_error_pct"] = result["period_error_pct"]
                row[f"{dof}_signed_period_error_pct"] = result["signed_period_error_pct"]
                row[f"{dof}_simulation_period_s"] = result["simulation_period_s"]
                row[f"{dof}_J_dof"] = result["J_dof"]
            else:
                row[f"{dof}_error"] = rec.get("error", "")
            write_incremental(rows)
            print(f"completed case_id={c.CASES[dof]} status={row[f'{dof}_status']}", flush=True)
    write_incremental(rows)
    write_best_and_report(rows, args.mode, attempted)


if __name__ == "__main__":
    main()
