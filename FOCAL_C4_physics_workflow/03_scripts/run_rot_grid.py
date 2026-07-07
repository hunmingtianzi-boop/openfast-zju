from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd

import clean_six_dof_calibration as c


def main() -> None:
    c.RUN_ROOT = c.ROOT / "04_current_runs" / "six_dof_free_decay_calibration_2"
    c.ensure_dirs()
    base = {
        "Surge": 0.9553631042827027,
        "Sway": 0.9357481969040711,
        "Heave": 0.9647898344484139,
        "Yaw": 1.011472559706516,
    }
    roll_factors = [1.0, 1.1, 1.25, 1.5, 1.8, 2.2, 2.8, 3.5]
    pitch_factors = [1.0, 1.1, 1.25, 1.5, 1.8, 2.2, 2.8, 3.5, 4.5]
    rows = []
    for rf, pf in itertools.product(roll_factors, pitch_factors):
        factors = {**base, "Roll": rf, "Pitch": pf}
        model_id = f"rot_grid_R{str(rf).replace('.', 'p')}_P{str(pf).replace('.', 'p')}"
        candidate = c.Candidate(model_id, factors, basis="manual roll/pitch period grid")
        c.create_template(candidate)
        result_rows = []
        for dof in ["Roll", "Pitch"]:
            result, _ = c.run_one(candidate, dof)
            if result is not None:
                result_rows.append(result)
        if len(result_rows) == 2:
            row = {
                "model_id": model_id,
                "Roll_factor": rf,
                "Pitch_factor": pf,
            }
            for result in result_rows:
                row[f"{result['dof']}_period_error_pct"] = result["period_error_pct"]
                row[f"{result['dof']}_signed_period_error_pct"] = result["signed_period_error_pct"]
                row[f"{result['dof']}_J_dof"] = result["J_dof"]
            rows.append(row)
            print(row, flush=True)
    output = c.RUN_ROOT / "rot_grid_roll_pitch.csv"
    pd.DataFrame(rows).to_csv(output, index=False, encoding="utf-8-sig")
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
