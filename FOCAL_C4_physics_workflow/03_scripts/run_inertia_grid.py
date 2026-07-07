from __future__ import annotations

import itertools

import pandas as pd

import clean_six_dof_calibration as c


def main() -> None:
    c.RUN_ROOT = c.ROOT / "04_current_runs" / "six_dof_free_decay_calibration_2"
    c.ensure_dirs()
    base_clin = {
        "Surge": 0.9553631042827027,
        "Sway": 0.9357481969040711,
        "Heave": 0.9647898344484139,
        "Roll": 1.0,
        "Pitch": 1.0,
        "Yaw": 1.011472559706516,
    }
    r_factors = [1.05, 1.10, 1.15, 1.20, 1.30, 1.45]
    p_factors = [1.25, 1.35, 1.45, 1.50, 1.55, 1.65, 1.80]
    out = c.RUN_ROOT / "inertia_grid_roll_pitch.csv"
    rows = pd.read_csv(out).to_dict("records") if out.exists() and out.stat().st_size > 0 else []
    done = {(float(r["PtfmRIner_factor"]), float(r["PtfmPIner_factor"])) for r in rows}
    for rf, pf in itertools.product(r_factors, p_factors):
        if (rf, pf) in done:
            continue
        model_id = f"inertia_R{str(rf).replace('.', 'p')}_P{str(pf).replace('.', 'p')}"
        cand = c.Candidate(
            model_id,
            base_clin,
            inertia_factors={"PtfmRIner": rf, "PtfmPIner": pf},
            basis="roll/pitch inertia period closure with horizontal/heave/yaw locks",
        )
        c.create_template(cand)
        row = {"model_id": model_id, "PtfmRIner_factor": rf, "PtfmPIner_factor": pf}
        for dof in ["Roll", "Pitch"]:
            result, rec = c.run_one(cand, dof)
            row[f"{dof}_status"] = rec.get("status")
            if result is not None:
                row[f"{dof}_period_error_pct"] = result["period_error_pct"]
                row[f"{dof}_signed_period_error_pct"] = result["signed_period_error_pct"]
                row[f"{dof}_simulation_period_s"] = result["simulation_period_s"]
                row[f"{dof}_J_dof"] = result["J_dof"]
        rows.append(row)
        pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")
        print(row, flush=True)
    df = pd.DataFrame(rows)
    print(df.sort_values(["Roll_period_error_pct", "Pitch_period_error_pct"]).head(20).to_string(index=False))


if __name__ == "__main__":
    main()
