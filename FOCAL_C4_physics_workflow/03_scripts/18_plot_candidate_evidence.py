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
    roots = [cfgmod.resolve_workspace_path(row["input_dir"]), cfgmod.resolve_workspace_path(row["output_dir"])]
    found: list[Path] = []
    for root in roots:
        if root.exists():
            found.extend(
                p for p in root.rglob("*.out")
                if p.stem == fst.stem and not p.name.endswith(".MD.out") and "Hydro" not in p.parts
            )
    if not found:
        raise FileNotFoundError(f"main .out not found for {row['run_id']}")
    return sorted(found, key=lambda p: p.stat().st_size, reverse=True)[0]


def rel_decay(t: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    t2, x2, _e, _i0, _i1, _eq = metrics.decay_seg(t, x)
    return t2, x2


def ref_path(dof: str) -> Path:
    return cfgmod.reference_dir() / f"{dof}_curated_processed.csv"


def plot_candidate(candidate: str) -> dict[str, str]:
    runs = latest_success_runs(candidate)
    fig_dir = cfgmod.workspace_root() / "06_figures" / "evidence_workflow"
    rep_dir = cfgmod.workspace_root() / "07_reports" / "evidence_workflow"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(3, 2, figsize=(15, 10.5), constrained_layout=True)
    axes_flat = axes.ravel()
    summary: list[str] = []
    result_rows = {
        r["run_id"]: r for r in read_csv_rows(cfgmod.registry_path("results_registry.csv"))
    }

    for ax, dof in zip(axes_flat, DOFS):
        label, chan, unit = DOF_LABEL[dof]
        row = runs.get(dof)
        if not row:
            ax.set_title(f"{dof}: missing")
            ax.axis("off")
            summary.append(f"- `{dof}`: missing successful run")
            continue
        ref = metrics.read_reference(ref_path(dof))
        model = metrics.read_openfast_out(main_out(row))
        tr, xr = rel_decay(ref["t"], ref[label])
        tm, xm = rel_decay(model["Time"], model[chan])
        tend = min(float(tr[-1]), float(tm[-1]), 350.0)
        mr = tr <= tend
        mm = tm <= tend
        ax.plot(tr[mr], xr[mr], color="#111827", lw=1.3, label="reference")
        ax.plot(tm[mm], xm[mm], color="#2563eb", lw=1.0, alpha=0.9, label="model")
        ax.axhline(0, color="#9ca3af", lw=0.7)
        ax.set_title(f"{dof} {label}")
        ax.set_xlabel("time from release peak (s)")
        ax.set_ylabel(unit)
        ax.grid(True, alpha=0.25)
        met = result_rows.get(row["run_id"], {})
        summary.append(
            f"- `{dof}`: {row['status']} / metrics={met.get('metric_status', 'NA')} / "
            f"period_err={met.get('period_error_pct', 'NA')} / env={met.get('envelope_error', 'NA')} / "
            f"LER={met.get('ler_error', 'NA')} / fp={met.get('fp_error', 'NA')}"
        )
    axes_flat[0].legend(loc="best")
    fig.suptitle(f"Six-DOF free-decay evidence overlay\n{candidate}", fontsize=13)
    png = fig_dir / f"{candidate}_sixdof_overlay.png"
    fig.savefig(png, dpi=170)
    plt.close(fig)

    report = rep_dir / f"{candidate}_plot_report.md"
    report.write_text(
        "\n".join([
            "# Candidate Evidence Plot Report",
            "",
            f"Candidate: `{candidate}`",
            f"Figure: `{png}`",
            "",
            "## Metric Summary",
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
    args = parser.parse_args()
    out = plot_candidate(args.candidate)
    print(f"Figure: {out['figure']}")
    print(f"Report: {out['report']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
