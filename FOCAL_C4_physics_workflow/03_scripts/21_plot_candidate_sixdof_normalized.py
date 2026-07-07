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


cfgmod = _load_script("00_config_loader.py")
metrics = _load_script("05_evaluate_metrics.py")

DOFS = ["FD_SURGE", "FD_SWAY", "FD_HEAVE", "FD_ROLL", "FD_PITCH", "FD_YAW"]
DOF_LABEL = {
    "FD_SURGE": ("Surge", "PtfmSurge", "m"),
    "FD_SWAY": ("Sway", "PtfmSway", "m"),
    "FD_HEAVE": ("Heave", "PtfmHeave", "m"),
    "FD_ROLL": ("Roll", "PtfmRoll", "deg"),
    "FD_PITCH": ("Pitch", "PtfmPitch", "deg"),
    "FD_YAW": ("Yaw", "PtfmYaw", "deg"),
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_success_runs(candidate: str) -> dict[str, dict[str, str]]:
    rows = [
        r for r in read_csv_rows(cfgmod.registry_path("run_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("status") == "SUCCESS"
    ]
    latest: dict[str, dict[str, str]] = {}
    for row in rows:
        dof = row.get("dof", "")
        if dof and (dof not in latest or row.get("end_time", "") > latest[dof].get("end_time", "")):
            latest[dof] = row
    return latest


def main_out(row: dict[str, str]) -> Path:
    fst = cfgmod.resolve_workspace_path(row["fst_file"])
    direct = fst.with_suffix(".out")
    if direct.exists():
        return direct
    found: list[Path] = []
    for key in ["input_dir", "output_dir"]:
        root = cfgmod.resolve_workspace_path(row[key])
        if root.exists():
            found.extend(
                p for p in root.rglob("*.out")
                if p.stem == fst.stem and not p.name.endswith(".MD.out") and "Hydro" not in p.parts
            )
    if not found:
        raise FileNotFoundError(f"main .out not found for {row['run_id']}")
    return sorted(found, key=lambda p: p.stat().st_size, reverse=True)[0]


def rel_decay(t: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t2, x2, *_ = metrics.decay_seg(t, x)
    return t2, x2


def release_scale(x: np.ndarray) -> float:
    if len(x) == 0:
        return 1.0
    return float(max(abs(x[0]), 1.0e-12))


def plot_candidate(candidate: str, raw_overlay: bool = False) -> dict[str, str]:
    runs = latest_success_runs(candidate)
    result_rows = {r["run_id"]: r for r in read_csv_rows(cfgmod.registry_path("results_registry.csv"))}
    fig_dir = cfgmod.workspace_root() / "06_figures" / "evidence_workflow"
    rep_dir = cfgmod.workspace_root() / "07_reports" / "evidence_workflow"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    summary: list[str] = []
    amp_rows: list[tuple[str, float, float]] = []

    for ax, dof in zip(axes.ravel(), DOFS):
        label, chan, unit = DOF_LABEL[dof]
        row = runs.get(dof)
        if not row:
            ax.set_title(f"{dof}: missing")
            ax.axis("off")
            summary.append(f"- `{dof}`: missing successful run")
            continue
        ref = metrics.read_reference(cfgmod.reference_dir() / f"{dof}_curated_processed.csv")
        model = metrics.read_openfast_out(main_out(row))
        tr, xr = rel_decay(ref["t"], ref[label])
        tm, xm = rel_decay(model["Time"], model[chan])
        tend = min(float(tr[-1]), float(tm[-1]), 350.0)
        mr = tr <= tend
        mm = tm <= tend
        ref_amp = release_scale(xr[mr])
        model_amp = release_scale(xm[mm])
        yr = xr[mr] if raw_overlay else xr[mr] / ref_amp
        ym = xm[mm] if raw_overlay else xm[mm] / model_amp
        ax.plot(tr[mr], yr, color="#111827", lw=1.4, label=f"reference amp={ref_amp:.3g}")
        ax.plot(tm[mm], ym, color="#2563eb", lw=1.1, alpha=0.9, label=f"model amp={model_amp:.3g}")
        ax.axhline(0, color="#9ca3af", lw=0.7)
        ax.set_title(f"{dof} {label}")
        ax.set_xlabel("time from release peak (s)")
        ax.set_ylabel(unit if raw_overlay else "normalized displacement")
        ax.grid(True, alpha=0.25)
        ax.legend(loc="best", fontsize=7)
        met = result_rows.get(row["run_id"], {})
        amp_rows.append((dof, ref_amp, model_amp))
        summary.append(
            f"- `{dof}`: run=`{row['run_id']}` period={met.get('period_error_pct', 'NA')} "
            f"env={met.get('envelope_error', 'NA')} LER={met.get('ler_error', 'NA')} "
            f"fp={met.get('fp_error', 'NA')} zeta={met.get('zeta', 'NA')} "
            f"ref_amp={ref_amp:.8g} model_amp={model_amp:.8g}"
        )

    mode = "raw" if raw_overlay else "release_normalized"
    fig.suptitle(f"Six-DOF free-decay overlay ({mode})\n{candidate}", fontsize=13)
    png = fig_dir / f"{candidate}_sixdof_{mode}_overlay.png"
    fig.savefig(png, dpi=170)
    plt.close(fig)

    report = rep_dir / f"{candidate}_sixdof_{mode}_overlay.md"
    report.write_text(
        "\n".join([
            "# Six-DOF Normalized Overlay Report",
            "",
            f"Candidate: `{candidate}`",
            f"Figure: `{png}`",
            f"Overlay mode: `{'raw displacement' if raw_overlay else 'normalized by each curve release amplitude'}`",
            "",
            "## Metric And Release Summary",
            "",
            *summary,
            "",
        ]),
        encoding="utf-8",
    )
    return {"figure": str(png), "report": str(report)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--raw-overlay", action="store_true")
    args = parser.parse_args()
    out = plot_candidate(args.candidate, raw_overlay=args.raw_overlay)
    print(f"Figure: {out['figure']}")
    print(f"Report: {out['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
