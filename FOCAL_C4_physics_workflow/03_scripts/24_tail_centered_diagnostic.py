from __future__ import annotations

import argparse
import csv
import importlib.util
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


cfg = _load_script("00_config_loader.py")
metrics = _load_script("05_evaluate_metrics.py")
plot6 = _load_script("21_plot_candidate_sixdof_normalized.py")

DOFS = ["FD_SURGE", "FD_SWAY", "FD_HEAVE", "FD_ROLL", "FD_PITCH", "FD_YAW"]
META = {
    "FD_SURGE": ("Surge", "PtfmSurge", "m"),
    "FD_SWAY": ("Sway", "PtfmSway", "m"),
    "FD_HEAVE": ("Heave", "PtfmHeave", "m"),
    "FD_ROLL": ("Roll", "PtfmRoll", "deg"),
    "FD_PITCH": ("Pitch", "PtfmPitch", "deg"),
    "FD_YAW": ("Yaw", "PtfmYaw", "deg"),
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_run(candidate: str, dof: str) -> dict[str, str]:
    found = [
        r for r in rows(cfg.registry_path("run_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("dof") == dof and r.get("status") == "SUCCESS"
    ]
    if not found:
        raise FileNotFoundError(f"missing successful run: {candidate} {dof}")
    return sorted(found, key=lambda r: r.get("end_time", ""))[-1]


def curve(candidate: str, dof: str) -> tuple[np.ndarray, np.ndarray]:
    label, chan, _ = META[dof]
    if candidate == "REFERENCE":
        ref = metrics.read_reference(cfg.reference_dir() / f"{dof}_curated_processed.csv")
        return ref["t"], ref[label]
    run = latest_run(candidate, dof)
    dat = metrics.read_openfast_out(plot6.main_out(run))
    return dat["Time"], dat[chan]


def tail_center(t: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    n = max(20, int(len(x) * 0.15))
    eq = float(np.median(x[-n:]))
    return t, x - eq, eq


def release_segment(t: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    td, xd, *_rest, eq = metrics.decay_seg(t, x)
    scale = max(abs(float(xd[0])), 1.0e-12)
    return td, xd / scale, eq


def rmse_on_grid(t_ref: np.ndarray, y_ref: np.ndarray, t_mod: np.ndarray, y_mod: np.ndarray, tend: float) -> float:
    end = min(float(t_ref[-1]), float(t_mod[-1]), tend)
    start = max(float(t_ref[0]), float(t_mod[0]))
    dt = max(float(np.median(np.diff(t_ref))), float(np.median(np.diff(t_mod))), 0.05)
    grid = np.arange(start, end + 0.5 * dt, dt)
    yr = np.interp(grid, t_ref, y_ref)
    ym = np.interp(grid, t_mod, y_mod)
    span = max(float(np.max(yr) - np.min(yr)), 1.0e-12)
    return float(np.sqrt(np.mean((ym - yr) ** 2)) / span)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="AMP_SH_HAF15_Y15052_R8715_07041136")
    parser.add_argument("--tend", type=float, default=350.0)
    args = parser.parse_args()

    fig_dir = cfg.workspace_root() / "06_figures" / "diagnostics"
    rep_dir = cfg.workspace_root() / "07_reports" / "diagnostics"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(6, 3, figsize=(17, 20), constrained_layout=True)
    table: list[dict[str, object]] = []

    for row_i, dof in enumerate(DOFS):
        label, _chan, unit = META[dof]
        tr, xr = curve("REFERENCE", dof)
        try:
            tm, xm = curve(args.candidate, dof)
        except FileNotFoundError:
            for col_i in range(3):
                ax = axes[row_i, col_i]
                ax.set_title(f"{dof} missing for {args.candidate}")
                ax.axis("off")
            continue
        trc, xrc, eqr = tail_center(tr, xr)
        tmc, xmc, eqm = tail_center(tm, xm)
        trn, xrn, _ = release_segment(tr, xr)
        tmn, xmn, _ = release_segment(tm, xm)

        raw_rmse = rmse_on_grid(tr, xr, tm, xm, args.tend)
        tail_rmse = rmse_on_grid(trc, xrc, tmc, xmc, args.tend)
        norm_rmse = rmse_on_grid(trn, xrn, tmn, xmn, args.tend)
        table.append({
            "dof": dof,
            "ref_tail": eqr,
            "model_tail": eqm,
            "tail_delta": eqm - eqr,
            "raw_rmse_span": raw_rmse,
            "tail_centered_rmse_span": tail_rmse,
            "release_norm_rmse_span": norm_rmse,
        })

        panels = [
            ("raw", tr, xr, tm, xm, unit),
            ("tail-centered", trc, xrc, tmc, xmc, unit),
            ("release-normalized", trn, xrn, tmn, xmn, "normalized"),
        ]
        for col_i, (title, rt, rx, mt, mx, ylabel) in enumerate(panels):
            ax = axes[row_i, col_i]
            mr = rt <= min(args.tend, float(rt[-1]))
            mm = mt <= min(args.tend, float(mt[-1]))
            ax.plot(rt[mr], rx[mr], color="#111827", lw=1.1, label="reference")
            ax.plot(mt[mm], mx[mm], color="#2563eb", lw=1.0, label=args.candidate)
            ax.axhline(0, color="#9ca3af", lw=0.7)
            ax.set_title(f"{dof} {title}")
            ax.set_xlabel("time (s)")
            ax.set_ylabel(ylabel)
            ax.grid(True, alpha=0.25)
            if row_i == 0:
                ax.legend(fontsize=7)

    fig.suptitle(f"Tail-centered diagnostic: {args.candidate}", fontsize=14)
    fig_path = fig_dir / f"{args.candidate}_tail_centered_diagnostic_0706.png"
    fig.savefig(fig_path, dpi=160)
    plt.close(fig)

    if not table:
        raise SystemExit(f"no successful DOF runs found for {args.candidate}")
    headers = list(table[0])
    report = rep_dir / f"{args.candidate}_tail_centered_diagnostic_0706.md"
    lines = [
        "# Tail-Centered Diagnostic",
        "",
        f"Candidate: `{args.candidate}`",
        f"Figure: `{fig_path}`",
        "",
        "|" + "|".join(headers) + "|",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for item in table:
        vals = []
        for h in headers:
            v = item[h]
            vals.append(f"{v:.6g}" if isinstance(v, float) else str(v))
        lines.append("|" + "|".join(vals) + "|")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Figure: {fig_path}")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
