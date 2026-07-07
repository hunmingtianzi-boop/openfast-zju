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

DOF_META = {
    "FD_SURGE": ("Surge", "PtfmSurge", "m"),
    "FD_SWAY": ("Sway", "PtfmSway", "m"),
    "FD_HEAVE": ("Heave", "PtfmHeave", "m"),
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def result_row(candidate: str, dof: str) -> dict[str, str]:
    rows = [
        r for r in read_rows(cfg.registry_path("results_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("dof") == dof
    ]
    if not rows:
        return {}
    return rows[-1]


def run_row(candidate: str, dof: str) -> dict[str, str]:
    rows = [
        r for r in read_rows(cfg.registry_path("run_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("dof") == dof and r.get("status") == "SUCCESS"
    ]
    if not rows:
        raise FileNotFoundError(f"No successful run for {candidate} {dof}")
    return sorted(rows, key=lambda r: r.get("end_time", ""))[-1]


def load_curve(candidate: str, dof: str) -> tuple[np.ndarray, np.ndarray]:
    label, chan, _ = DOF_META[dof]
    if candidate == "REFERENCE":
        ref = metrics.read_reference(cfg.reference_dir() / f"{dof}_curated_processed.csv")
        t, x, *_ = metrics.decay_seg(ref["t"], ref[label])
        return t, x
    row = run_row(candidate, dof)
    out = plot6.main_out(row)
    model = metrics.read_openfast_out(out)
    t, x, *_ = metrics.decay_seg(model["Time"], model[chan])
    return t, x


def uniform_pair(t_ref: np.ndarray, x_ref: np.ndarray, t_mod: np.ndarray, x_mod: np.ndarray, tend: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    start = max(float(t_ref[0]), float(t_mod[0]))
    end = min(float(t_ref[-1]), float(t_mod[-1]), tend)
    dt_ref = float(np.median(np.diff(t_ref)))
    dt_mod = float(np.median(np.diff(t_mod)))
    dt = max(dt_ref, dt_mod, 0.05)
    grid = np.arange(start, end + 0.5 * dt, dt)
    return grid, np.interp(grid, t_ref, x_ref), np.interp(grid, t_mod, x_mod)


def norm_release(x: np.ndarray) -> np.ndarray:
    scale = max(abs(float(x[0])), 1.0e-12)
    return x / scale


def best_lag_seconds(y_ref: np.ndarray, y_mod: np.ndarray, dt: float, max_lag_s: float) -> tuple[float, float, float]:
    max_lag = max(1, int(round(max_lag_s / dt)))
    best = (0.0, float("inf"), 0.0)
    for lag in range(-max_lag, max_lag + 1):
        if lag < 0:
            a = y_ref[-lag:]
            b = y_mod[: len(a)]
        elif lag > 0:
            a = y_ref[:-lag]
            b = y_mod[lag:]
        else:
            a = y_ref
            b = y_mod
        if len(a) < 20:
            continue
        err = float(np.sqrt(np.mean((a - b) ** 2)))
        corr = float(np.corrcoef(a, b)[0, 1]) if np.std(a) > 0 and np.std(b) > 0 else 0.0
        if err < best[1]:
            best = (lag * dt, err, corr)
    return best


def tail_mean(x: np.ndarray, fraction: float = 0.15) -> float:
    n = max(20, int(len(x) * fraction))
    return float(np.mean(x[-n:]))


def zero_crossings(t: np.ndarray, x: np.ndarray, limit: int = 6) -> list[float]:
    out: list[float] = []
    for i in range(1, len(x)):
        if x[i - 1] == 0 or x[i] == 0 or np.sign(x[i - 1]) == np.sign(x[i]):
            continue
        frac = abs(x[i - 1]) / (abs(x[i - 1]) + abs(x[i]))
        out.append(float(t[i - 1] + frac * (t[i] - t[i - 1])))
        if len(out) >= limit:
            break
    return out


def diagnose(dof: str, baseline: str, candidates: list[str], tend: float) -> tuple[list[dict[str, object]], Path]:
    t_ref, x_ref = load_curve("REFERENCE", dof)
    all_ids = [baseline, *candidates]
    fig_dir = cfg.workspace_root() / "06_figures" / "diagnostics"
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(len(all_ids), 2, figsize=(13, 3.6 * len(all_ids)), constrained_layout=True)
    if len(all_ids) == 1:
        axes = np.array([axes])
    rows: list[dict[str, object]] = []
    ref_tail = tail_mean(x_ref)
    for idx, cand in enumerate(all_ids):
        t_mod, x_mod = load_curve(cand, dof)
        grid, ref_i, mod_i = uniform_pair(t_ref, x_ref, t_mod, x_mod, tend)
        ref_n = norm_release(ref_i)
        mod_n = norm_release(mod_i)
        dt = float(np.median(np.diff(grid)))
        zero_rmse = float(np.sqrt(np.mean((ref_n - mod_n) ** 2)))
        lag, lag_rmse, lag_corr = best_lag_seconds(ref_n, mod_n, dt, max_lag_s=30.0)
        z_ref = zero_crossings(grid, ref_n)
        z_mod = zero_crossings(grid, mod_n)
        z_delta = float(np.mean([m - r for r, m in zip(z_ref, z_mod)])) if z_ref and z_mod else float("nan")
        met = result_row(cand, dof)
        mod_tail = tail_mean(x_mod)
        rows.append({
            "dof": dof,
            "candidate": cand,
            "period_error_pct": met.get("period_error_pct", ""),
            "zeta_abs_error": abs(float(met.get("zeta", "nan")) - float(met.get("zeta_ref", "nan"))) if met else float("nan"),
            "nrmse": met.get("nrmse", ""),
            "shape_error": met.get("shape_error", ""),
            "envelope_error": met.get("envelope_error", ""),
            "ler_error": met.get("ler_error", ""),
            "fp_error": met.get("fp_error", ""),
            "tail_mean": mod_tail,
            "tail_delta_vs_ref": mod_tail - ref_tail,
            "zero_lag_norm_rmse": zero_rmse,
            "best_lag_s": lag,
            "best_lag_norm_rmse": lag_rmse,
            "best_lag_corr": lag_corr,
            "mean_zero_crossing_delta_s": z_delta,
        })
        ax0, ax1 = axes[idx]
        ax0.plot(grid, ref_i, color="#111827", lw=1.2, label="reference")
        ax0.plot(grid, mod_i, color="#2563eb", lw=1.0, label=cand)
        ax0.axhline(0, color="#9ca3af", lw=0.7)
        ax0.set_title(f"{dof} raw: {cand}")
        ax0.set_xlabel("time from release peak (s)")
        ax0.grid(True, alpha=0.25)
        ax0.legend(fontsize=7)
        ax1.plot(grid, ref_n, color="#111827", lw=1.2, label="reference")
        ax1.plot(grid, mod_n, color="#2563eb", lw=1.0, label=cand)
        if abs(lag) > 0:
            ax1.plot(grid + lag, mod_n, color="#dc2626", lw=0.9, alpha=0.8, label=f"model shifted {lag:+.1f}s")
        ax1.axhline(0, color="#9ca3af", lw=0.7)
        ax1.set_title(f"normalized phase: zero RMSE {zero_rmse:.3f}, best {lag_rmse:.3f}")
        ax1.set_xlabel("time from release peak (s)")
        ax1.grid(True, alpha=0.25)
        ax1.legend(fontsize=7)
    fig.suptitle(f"{dof} phase and LER diagnostic", fontsize=13)
    fig_path = fig_dir / f"{dof.lower()}_phase_ler_diagnostic_0706.png"
    fig.savefig(fig_path, dpi=170)
    plt.close(fig)
    return rows, fig_path


def write_report(rows: list[dict[str, object]], figures: list[Path]) -> Path:
    rep_dir = cfg.workspace_root() / "07_reports" / "diagnostics"
    rep_dir.mkdir(parents=True, exist_ok=True)
    out = rep_dir / "phase_ler_diagnostic_0706.md"
    headers = [
        "dof", "candidate", "period_error_pct", "zeta_abs_error", "nrmse", "shape_error",
        "envelope_error", "ler_error", "fp_error", "tail_delta_vs_ref",
        "zero_lag_norm_rmse", "best_lag_s", "best_lag_norm_rmse", "best_lag_corr",
        "mean_zero_crossing_delta_s",
    ]
    lines = [
        "# Phase/LER Diagnostic",
        "",
        "This report compares the formal baseline with local diagnostic branches using the existing registry outputs only.",
        "",
        "## Figures",
        "",
        *[f"- `{p}`" for p in figures],
        "",
        "## Metrics",
        "",
        "|" + "|".join(headers) + "|",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        vals = []
        for h in headers:
            v = row[h]
            if isinstance(v, float):
                vals.append(f"{v:.6g}")
            else:
                vals.append(str(v))
        lines.append("|" + "|".join(vals) + "|")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="AMP_SH_HAF15_Y15052_R8715_07041136")
    parser.add_argument("--tend", type=float, default=350.0)
    args = parser.parse_args()
    plan = {
        "FD_SURGE": [
            "SURGE_AF0_N50K_FULL_0705",
            "SURGE_IC2032_FULL_0706",
            "SURGE_IC2032_AF0_N50K_FULL_0706",
            "SURGE_UL1002_FULL_0705230510",
            "SURGE_AF0_N50K_UL1002_FULL_0706",
            "SURGE_AF0_N180K_IC177_T600_0706",
            "SURGE_AF0_N190K_IC178_T600_0706",
            "SURGE_AF0_N210K_IC180_T600_0706",
            "SURGE_AF0_N180K_IC177_UL1001_T600_0706",
        ],
        "FD_SWAY": ["SWAY_AF0_N50K_FULL_0706", "SWAY_AF0_N50K_BQ103X_FULL_0706", "SWAY_AF0_N50K_BQ125X_FULL_0706"],
        "FD_HEAVE": [
            "HEAVE_AF0_N16M_FULL_0706",
            "HEAVE_BL50_0704212109",
            "HEAVE_AF0_N20M_IC83_FULL_0706",
            "HEAVE_AF0_N20M_IC83_BL100_FULL_0706",
            "HEAVE_AF0_N20M_IC83_BL100_BQ095_T600_0706",
            "HEAVE_AF0_N20M_IC83_BL100_BQ095_FULL_0706",
        ],
    }
    all_rows: list[dict[str, object]] = []
    figures: list[Path] = []
    for dof, cands in plan.items():
        rows, fig = diagnose(dof, args.baseline, cands, args.tend)
        all_rows.extend(rows)
        figures.append(fig)
    report = write_report(all_rows, figures)
    print(f"Report: {report}")
    for fig in figures:
        print(f"Figure: {fig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
