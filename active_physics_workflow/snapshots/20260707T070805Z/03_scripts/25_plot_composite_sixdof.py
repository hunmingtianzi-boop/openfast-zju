from __future__ import annotations

import argparse
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


def load_curve(candidate: str, dof: str) -> tuple[np.ndarray, np.ndarray]:
    label, chan, _unit = META[dof]
    if candidate == "REFERENCE":
        ref = metrics.read_reference(cfg.reference_dir() / f"{dof}_curated_processed.csv")
        return ref["t"], ref[label]
    runs = plot6.latest_success_runs(candidate)
    if dof not in runs:
        raise FileNotFoundError(f"missing successful run: {candidate} {dof}")
    dat = metrics.read_openfast_out(plot6.main_out(runs[dof]))
    return dat["Time"], dat[chan]


def release_norm(t: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    td, xd, *_ = metrics.decay_seg(t, x)
    scale = max(abs(float(xd[0])), 1.0e-12)
    return td, xd / scale


def parse_map(values: list[str], default: str) -> dict[str, str]:
    out = {dof: default for dof in DOFS}
    expanded: list[str] = []
    for value in values:
        expanded.extend(item.strip() for item in value.split(",") if item.strip())
    for item in expanded:
        if "=" not in item:
            raise SystemExit(f"expected DOF=CANDIDATE mapping, got {item}")
        dof, cand = item.split("=", 1)
        dof = dof.strip().upper()
        if dof not in out:
            raise SystemExit(f"unknown DOF: {dof}")
        out[dof] = cand.strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="AMP_SH_HAF15_Y15052_R8715_07041136")
    parser.add_argument("--map", action="append", default=[], help="Override as DOF=CANDIDATE")
    parser.add_argument("--label", default="composite")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--tend", type=float, default=350.0)
    parser.add_argument("--output-dir", default="", help="Directory for generated PNG. Defaults to 06_figures/diagnostics.")
    parser.add_argument("--report-dir", default="", help="Directory for generated Markdown report. Defaults to 07_reports/diagnostics.")
    args = parser.parse_args()

    mapping = parse_map(args.map, args.baseline)
    fig_dir = cfg.resolve_workspace_path(args.output_dir) if args.output_dir else cfg.workspace_root() / "06_figures" / "diagnostics"
    rep_dir = cfg.resolve_workspace_path(args.report_dir) if args.report_dir else cfg.workspace_root() / "07_reports" / "diagnostics"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(3, 2, figsize=(15, 11), constrained_layout=True)
    lines = [
        "# Composite Six-DOF Overlay",
        "",
        f"Baseline candidate: `{args.baseline}`",
        f"Composite label: `{args.label}`",
        "",
        "## DOF Mapping",
        "",
    ]
    for dof in DOFS:
        lines.append(f"- `{dof}`: `{mapping[dof]}`")

    lines.extend(["", "## Metrics", ""])
    for ax, dof in zip(axes.ravel(), DOFS):
        label, _chan, unit = META[dof]
        tr, xr = load_curve("REFERENCE", dof)
        tm, xm = load_curve(mapping[dof], dof)
        if args.raw:
            td, xd, *_ = metrics.decay_seg(tr, xr)
            tmod, xmod, *_ = metrics.decay_seg(tm, xm)
            ylabel = unit
        else:
            td, xd = release_norm(tr, xr)
            tmod, xmod = release_norm(tm, xm)
            ylabel = "normalized"
        end = min(float(td[-1]), float(tmod[-1]), args.tend)
        mr = td <= end
        mm = tmod <= end
        ax.plot(td[mr], xd[mr], color="#111827", lw=1.2, label="reference")
        ax.plot(tmod[mm], xmod[mm], color="#2563eb", lw=1.0, label=mapping[dof])
        ax.axhline(0, color="#9ca3af", lw=0.7)
        ax.set_title(f"{dof} {label}")
        ax.set_xlabel("time from release peak (s)")
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize=7)
        result_rows = [
            r for r in cfg.read_csv(cfg.registry_path("results_registry.csv"))
            if r.get("candidate_id") == mapping[dof] and r.get("dof") == dof and r.get("metric_status") == "PASS"
        ]
        if result_rows:
            r = result_rows[-1]
            lines.append(
                f"- `{dof}` `{mapping[dof]}`: period={r.get('period_error_pct')} "
                f"nrmse={r.get('nrmse')} env={r.get('envelope_error')} "
                f"LER={r.get('ler_error')} fp={r.get('fp_error')} start={r.get('start_amplitude_error')}"
            )
        else:
            lines.append(f"- `{dof}` `{mapping[dof]}`: no metric row found")

    mode = "raw" if args.raw else "release_normalized"
    fig.suptitle(f"Composite six-DOF overlay ({mode})\n{args.label}", fontsize=13)
    fig_path = fig_dir / f"{args.label}_sixdof_composite_{mode}_0706.png"
    report = rep_dir / f"{args.label}_sixdof_composite_{mode}_0706.md"
    fig.savefig(fig_path, dpi=170)
    plt.close(fig)
    lines.insert(4, f"Figure: `{fig_path}`")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Figure: {fig_path}")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
