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

DOF_CHAN = {
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


def candidate_parent(candidate_id: str) -> str:
    for row in read_csv_rows(cfgmod.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row.get("parent_candidate_id", "")
    return ""


def latest_success(candidate_id: str, dof: str) -> dict[str, str] | None:
    rows = [
        r for r in read_csv_rows(cfgmod.registry_path("run_registry.csv"))
        if r.get("candidate_id") == candidate_id and r.get("dof") == dof and r.get("status") == "SUCCESS"
    ]
    if not rows:
        return None
    return sorted(rows, key=lambda r: r.get("end_time", ""))[-1]


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


def result_for_run(run_id: str) -> dict[str, str]:
    return next((r for r in read_csv_rows(cfgmod.registry_path("results_registry.csv")) if r.get("run_id") == run_id), {})


def label_for(candidate_id: str, baseline: str) -> str:
    if candidate_id == baseline:
        return "baseline"
    if candidate_id.startswith("BQIC_"):
        return candidate_id.replace("BQIC_", "")
    return candidate_id[-24:]


def release_scale(x: np.ndarray) -> float:
    if len(x) == 0:
        return 1.0
    scale = float(max(abs(x[0]), 1.0e-12))
    return scale


def plot(baseline: str, candidates: list[str], dof: str, title: str | None, raw_overlay: bool = False) -> dict[str, str]:
    label, chan, unit = DOF_CHAN[dof]
    requested = [c for c in candidates if c and c != baseline]
    accepted = [baseline]
    skipped: list[tuple[str, str]] = []
    for cid in requested:
        parent = candidate_parent(cid)
        if parent != baseline:
            skipped.append((cid, parent))
        else:
            accepted.append(cid)

    rows: list[tuple[str, str, dict[str, str], dict[str, str], np.ndarray, np.ndarray]] = []
    for cid in accepted:
        run = latest_success(cid, dof)
        if not run:
            skipped.append((cid, "missing-success-run"))
            continue
        res = result_for_run(run["run_id"])
        data = metrics.read_openfast_out(main_out(run))
        t, x = rel_decay(data["Time"], data[chan])
        mask = t <= 260.0
        rows.append((label_for(cid, baseline), cid, run, res, t[mask], x[mask]))

    if not rows:
        raise SystemExit("no comparable successful runs")

    ref = metrics.read_reference(cfgmod.reference_dir() / f"{dof}_curated_processed.csv")
    tr, xr = rel_decay(ref["t"], ref[label])
    ref_mask = tr <= 260.0

    fig = plt.figure(figsize=(15, 9), constrained_layout=True)
    gs = fig.add_gridspec(2, 2, height_ratios=[2.1, 1.0])
    ax0 = fig.add_subplot(gs[0, :])
    ax1 = fig.add_subplot(gs[1, 0])
    ax2 = fig.add_subplot(gs[1, 1])

    ref_scale = release_scale(xr[ref_mask])
    ref_y = xr[ref_mask] if raw_overlay else xr[ref_mask] / ref_scale
    ax0.plot(tr[ref_mask], ref_y, color="black", lw=2.0, label="reference")
    colors = ["#2563eb", "#dc2626", "#ea580c", "#059669", "#7c3aed", "#64748b"]
    release_amplitudes = [ref_scale]
    for idx, (short, _cid, _run, _res, t, x) in enumerate(rows):
        scale = release_scale(x)
        release_amplitudes.append(scale)
        y = x if raw_overlay else x / scale
        ax0.plot(t, y, color=colors[idx % len(colors)], lw=2.0 if idx == 0 else 1.3, alpha=0.9, label=short)
    ax0.axhline(0, color="#94a3b8", lw=0.8)
    ax0.set_title(title or f"{dof} objective sweep, same-parent comparison")
    ax0.set_xlabel("time from release peak (s)")
    ax0.set_ylabel(f"{chan} ({unit})" if raw_overlay else f"{chan} / release amplitude")
    ax0.grid(True, alpha=0.25)
    ax0.legend(ncol=3, fontsize=8)

    names = [r[0] for r in rows]
    xpos = np.arange(len(names))
    width = 0.2
    period = [float(r[3].get("period_error_pct") or "nan") for r in rows]
    env = [float(r[3].get("envelope_error") or "nan") for r in rows]
    ler = [float(r[3].get("ler_error") or "nan") for r in rows]
    fp = [float(r[3].get("fp_error") or "nan") for r in rows]
    zeta = [float(r[3].get("zeta") or "nan") for r in rows]
    refz = [float(r[3].get("reference_zeta") or "nan") for r in rows]

    ax1.bar(xpos - 1.5 * width, period, width, label="period err %", color="#2563eb")
    ax1.bar(xpos - 0.5 * width, env, width, label="env", color="#dc2626")
    ax1.bar(xpos + 0.5 * width, ler, width, label="LER", color="#059669")
    ax1.bar(xpos + 1.5 * width, fp, width, label="fp", color="#ea580c")
    ax1.axhline(3.0, color="#2563eb", ls="--", lw=1.0, alpha=0.7)
    ax1.set_xticks(xpos)
    ax1.set_xticklabels(names, rotation=25, ha="right")
    ax1.set_title("Gate metrics, lower is better")
    ax1.grid(True, axis="y", alpha=0.25)
    ax1.legend(fontsize=8)

    ax2.bar(xpos - 0.18, zeta, width=0.36, color="#7c3aed", alpha=0.8, label="candidate zeta")
    ax2.axhline(refz[0], color="black", lw=1.5, label="reference zeta")
    ax2b = ax2.twinx()
    ax2b.bar(xpos + 0.18, release_amplitudes[1:], width=0.36, color="#64748b", alpha=0.45, label="release amp")
    ax2b.set_ylabel(f"release amplitude ({unit})")
    ax2.set_xticks(xpos)
    ax2.set_xticklabels(names, rotation=25, ha="right")
    ax2.set_title("Damping ratio and raw release amplitude")
    ax2.grid(True, axis="y", alpha=0.25)
    h1, l1 = ax2.get_legend_handles_labels()
    h2, l2 = ax2b.get_legend_handles_labels()
    ax2.legend(h1 + h2, l1 + l2, fontsize=8, loc="best")

    fig_dir = cfgmod.workspace_root() / "06_figures" / "evidence_workflow"
    rep_dir = cfgmod.workspace_root() / "07_reports" / "evidence_workflow"
    fig_dir.mkdir(parents=True, exist_ok=True)
    rep_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{dof.lower()}_same_parent_objective_sweep_{baseline[-10:]}"
    fig_path = fig_dir / f"{stem}.png"
    report_path = rep_dir / f"{stem}.md"
    fig.savefig(fig_path, dpi=170)
    plt.close(fig)

    lines = [
        "# Same-Parent Objective Sweep Plot",
        "",
        f"Baseline: `{baseline}`",
        f"DOF: `{dof}`",
        f"Figure: `{fig_path}`",
        "",
        "## Included",
        "",
    ]
    for short, cid, run, res, _t, _x in rows:
        lines.append(
            f"- `{short}` `{cid}` run=`{run['run_id']}` "
            f"period={res.get('period_error_pct')} env={res.get('envelope_error')} "
            f"LER={res.get('ler_error')} fp={res.get('fp_error')} zeta={res.get('zeta')} "
            f"release_amp={release_amplitudes[idx + 1]:.8g}"
        )
    lines.extend([
        "",
        "## Plot Mode",
        "",
        f"- Overlay mode: `{'raw amplitude' if raw_overlay else 'normalized by each curve release amplitude'}`",
        f"- Reference release amplitude: `{ref_scale:.8g}`",
    ])
    lines.extend(["", "## Skipped", ""])
    if not skipped:
        lines.append("None.")
    else:
        for cid, parent in skipped:
            lines.append(f"- `{cid}` skipped because parent/status is `{parent}`")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"figure": str(fig_path), "report": str(report_path), "included": str(len(rows)), "skipped": str(len(skipped))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Plot same-parent objective sweep candidates.")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidates", required=True, help="Comma-separated candidate ids to compare against baseline.")
    parser.add_argument("--dof", default="FD_SURGE", choices=sorted(DOF_CHAN))
    parser.add_argument("--title")
    parser.add_argument("--raw-overlay", action="store_true", help="Plot raw displacement amplitudes instead of release-amplitude-normalized curves.")
    args = parser.parse_args()
    out = plot(args.baseline, [c.strip() for c in args.candidates.split(",") if c.strip()], args.dof, args.title, raw_overlay=args.raw_overlay)
    print(f"Figure: {out['figure']}")
    print(f"Report: {out['report']}")
    print(f"Included: {out['included']} Skipped: {out['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
