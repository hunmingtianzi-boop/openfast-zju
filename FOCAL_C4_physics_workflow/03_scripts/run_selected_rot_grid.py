from __future__ import annotations

from pathlib import Path

import pandas as pd

import clean_six_dof_calibration as c


def write_rows(rows: list[dict]) -> None:
    output = c.RUN_ROOT / "selected_rot_grid_roll_pitch.csv"
    pd.DataFrame(rows).to_csv(output, index=False, encoding="utf-8-sig")


def main() -> None:
    c.RUN_ROOT = c.ROOT / "04_current_runs" / "six_dof_free_decay_calibration_2"
    c.ensure_dirs()
    base = {
        "Surge": 0.9553631042827027,
        "Sway": 0.9357481969040711,
        "Heave": 0.9647898344484139,
        "Yaw": 1.011472559706516,
    }
    pairs = [
        (1.1, 1.1),
        (1.25, 1.1),
        (1.5, 1.1),
        (1.8, 1.1),
        (2.2, 1.1),
        (1.25, 1.25),
        (1.25, 1.35),
        (1.25, 1.5),
        (1.5, 1.25),
        (1.5, 1.35),
        (1.5, 1.5),
        (1.8, 1.25),
        (1.8, 1.35),
        (1.8, 1.5),
        (2.2, 1.25),
        (2.2, 1.35),
        (2.2, 1.5),
        (2.8, 1.25),
        (2.8, 1.35),
        (2.8, 1.5),
    ]
    rows: list[dict] = []
    prior = c.RUN_ROOT / "selected_rot_grid_roll_pitch.csv"
    if prior.exists() and prior.stat().st_size > 0:
        rows = pd.read_csv(prior).to_dict("records")
    done = {(float(r["Roll_factor"]), float(r["Pitch_factor"])) for r in rows}
    for rf, pf in pairs:
        if (rf, pf) in done:
            continue
        factors = {**base, "Roll": rf, "Pitch": pf}
        model_id = f"sel_rot_R{str(rf).replace('.', 'p')}_P{str(pf).replace('.', 'p')}"
        candidate = c.Candidate(model_id, factors, basis="selected roll/pitch period grid")
        c.create_template(candidate)
        row = {"model_id": model_id, "Roll_factor": rf, "Pitch_factor": pf}
        for dof in ["Roll", "Pitch"]:
            result, rec = c.run_one(candidate, dof)
            row[f"{dof}_status"] = rec.get("status")
            if result is not None:
                row[f"{dof}_period_error_pct"] = result["period_error_pct"]
                row[f"{dof}_signed_period_error_pct"] = result["signed_period_error_pct"]
                row[f"{dof}_simulation_period_s"] = result["simulation_period_s"]
                row[f"{dof}_J_dof"] = result["J_dof"]
        rows.append(row)
        write_rows(rows)
        print(row, flush=True)
    df = pd.DataFrame(rows)
    print(df.sort_values(["Roll_period_error_pct", "Pitch_period_error_pct"]).head(20).to_string(index=False))


if __name__ == "__main__":
    main()
