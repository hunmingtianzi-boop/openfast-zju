from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()
ROOT = cfgmod.workspace_root()
REPORT_DIR = ROOT / "07_reports" / "shadow_scout"
LOG_DIR = ROOT / "08_logs" / "shadow_scout"
REGISTRY = ROOT / "05_registry" / "shadow_experiment_registry.jsonl"
LATEST_STATE = LOG_DIR / "latest_shadow_scout_state.json"
LATEST_REPORT = REPORT_DIR / "latest_shadow_scout_report.md"

PTFM_CHANS = ["PtfmSurge", "PtfmSway", "PtfmHeave", "PtfmRoll", "PtfmPitch", "PtfmYaw"]
DOF_PRIMARY = {
    "FD_SURGE": "PtfmSurge",
    "FD_SWAY": "PtfmSway",
    "FD_HEAVE": "PtfmHeave",
    "FD_ROLL": "PtfmRoll",
    "FD_PITCH": "PtfmPitch",
    "FD_YAW": "PtfmYaw",
}
PAIRS = {
    "roll_pitch": ("PtfmRoll", "PtfmPitch"),
    "sway_pitch": ("PtfmSway", "PtfmPitch"),
    "surge_yaw": ("PtfmSurge", "PtfmYaw"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_out(path: Path) -> dict[str, np.ndarray]:
    rows: list[list[float]] = []
    header: list[str] | None = None
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            parts = line.strip().split()
            if not parts:
                continue
            if header is None:
                if parts[0] == "Time":
                    header = parts
                continue
            if parts[0].startswith("("):
                continue
            try:
                rows.append([float(x) for x in parts[: len(header)]])
            except ValueError:
                continue
    if header is None or not rows:
        raise ValueError(f"no OpenFAST table found: {path}")
    arr = np.asarray(rows, float)
    return {name: arr[:, i] for i, name in enumerate(header)}


def channel_stats(t: np.ndarray, x: np.ndarray) -> dict[str, float | None]:
    x = np.asarray(x, float)
    mean = float(np.mean(x))
    y = x - mean
    rms = float(np.sqrt(np.mean(y * y)))
    peak = float(np.max(np.abs(y)))
    dt = float(np.median(np.diff(t))) if len(t) > 1 else math.nan
    if len(y) > 8 and math.isfinite(dt) and dt > 0:
        freq = np.fft.rfftfreq(len(y), dt)
        spec = np.abs(np.fft.rfft(y)) ** 2
        if len(spec) > 1:
            k = int(np.argmax(spec[1:]) + 1)
            fpk = float(freq[k])
            period = float(1.0 / fpk) if fpk > 0 else None
            low = float(np.sum(spec[(freq > 0) & (freq < 0.02)]) / max(np.sum(spec[1:]), 1e-30))
        else:
            period, low = None, None
    else:
        period, low = None, None
    return {
        "mean": mean,
        "rms_detrended": rms,
        "peak_abs_detrended": peak,
        "psd_peak_period_s": period,
        "low_freq_energy_fraction": low,
    }


def pair_metrics(t: np.ndarray, a: np.ndarray, b: np.ndarray) -> dict[str, float | None]:
    x = np.asarray(a, float) - float(np.mean(a))
    y = np.asarray(b, float) - float(np.mean(b))
    xr = float(np.sqrt(np.mean(x * x)))
    yr = float(np.sqrt(np.mean(y * y)))
    if xr <= 1e-12 or yr <= 1e-12:
        return {"secondary_primary_rms": None, "band_coupling": None, "phase_deg": None}
    X = np.fft.rfft(x)
    Y = np.fft.rfft(y)
    pxx = np.abs(X) ** 2
    pyy = np.abs(Y) ** 2
    pxy = X * np.conj(Y)
    coh = np.abs(pxy) ** 2 / np.maximum(pxx * pyy, 1e-30)
    k = int(np.argmax(pxx[1:]) + 1) if len(pxx) > 1 else 0
    phase = float(np.degrees(np.angle(pxy[k]))) if k else None
    return {
        "secondary_primary_rms": float(yr / xr),
        "band_coupling": float(coh[k]) if k else None,
        "phase_deg": phase,
    }


def latest_successful_runs(candidate_id: str | None = None) -> list[dict[str, str]]:
    rows = [r for r in read_csv(cfgmod.registry_path("run_registry.csv")) if r.get("status") == "SUCCESS"]
    if candidate_id:
        rows = [r for r in rows if r.get("candidate_id") == candidate_id]
    latest: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (row.get("candidate_id", ""), row.get("dof", ""))
        if key not in latest or row.get("end_time", "") > latest[key].get("end_time", ""):
            latest[key] = row
    return sorted(latest.values(), key=lambda r: (r.get("candidate_id", ""), r.get("dof", "")))


def analyze_run(row: dict[str, str]) -> dict[str, Any]:
    out_path = find_main_out(row)
    data = read_out(out_path)
    t = data["Time"]
    platform = {c: channel_stats(t, data[c]) for c in PTFM_CHANS if c in data}
    pairs = {name: pair_metrics(t, data[a], data[b]) for name, (a, b) in PAIRS.items() if a in data and b in data}
    primary = DOF_PRIMARY.get(row.get("dof", ""), max(platform, key=lambda c: platform[c]["rms_detrended"]))
    analysis = {
        "run_id": row["run_id"],
        "candidate_id": row["candidate_id"],
        "dof": row["dof"],
        "source_out": str(out_path),
        "primary_channel": primary,
        "platform": platform,
        "pairs": pairs,
        "findings": [],
    }
    analysis["findings"] = build_findings(analysis)
    return analysis


def find_main_out(row: dict[str, str]) -> Path:
    fst = cfgmod.resolve_workspace_path(row["fst_file"])
    candidates = [
        fst.with_suffix(".out"),
        cfgmod.resolve_workspace_path(row["input_dir"]) / (fst.stem + ".out"),
        cfgmod.resolve_workspace_path(row["output_dir"]) / (fst.stem + ".out"),
    ]
    for path in candidates:
        if path.exists() and not path.name.endswith(".MD.out"):
            return path
    roots = [cfgmod.resolve_workspace_path(row["output_dir"]), cfgmod.resolve_workspace_path(row["input_dir"])]
    found: list[Path] = []
    for root in roots:
        if root.exists():
            found.extend(
                p for p in root.rglob("*.out")
                if not p.name.endswith(".MD.out") and "Hydro" not in p.parts and p.stem == fst.stem
            )
    if not found:
        for root in roots:
            if root.exists():
                found.extend(
                    p for p in root.rglob("*.out")
                    if not p.name.endswith(".MD.out") and "Hydro" not in p.parts
                )
    if not found:
        raise FileNotFoundError(f"no main OpenFAST .out for run {row.get('run_id')}")
    return sorted(found, key=lambda p: p.stat().st_size, reverse=True)[0]


def build_findings(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    primary = analysis["primary_channel"]
    pairs = analysis["pairs"]

    def rec(dof: str, observation: str, hypothesis: str, next_action: str, verdict: str = "shadow_alarm"):
        return {
            "created_at": utc_now(),
            "run_id": analysis["run_id"],
            "candidate_id": analysis["candidate_id"],
            "dof": dof,
            "observation": observation,
            "hypothesis": hypothesis,
            "changed_knob": "none",
            "expected": "scouting only; promote only through free-decay or causal falsification",
            "actual": observation,
            "verdict": verdict,
            "evidence_level": "scouting_only",
            "artifacts": [analysis["source_out"]],
            "next_action": next_action,
        }

    rp = pairs.get("roll_pitch", {})
    if primary == "PtfmRoll" and (rp.get("secondary_primary_rms") or 0.0) >= 0.35 and (rp.get("band_coupling") or 0.0) >= 0.35:
        out.append(rec(
            "FD_ROLL",
            f"Roll run leaks Pitch with RMS ratio {rp['secondary_primary_rms']:.3f} and coherence {rp['band_coupling']:.3f}.",
            "Possible Roll/Pitch RNA-offset or conservative-coupling path; not a direct damping target.",
            "Run or inspect Roll RNA on/off and dose-response falsification before promotion.",
        ))

    sp = pairs.get("sway_pitch", {})
    if primary == "PtfmSway" and (sp.get("secondary_primary_rms") or 0.0) >= 0.35 and (sp.get("band_coupling") or 0.0) >= 0.35:
        out.append(rec(
            "FD_SWAY",
            f"Sway run leaks Pitch with RMS ratio {sp['secondary_primary_rms']:.3f} and coherence {sp['band_coupling']:.3f}.",
            "Possible Sway/Pitch cross-group leakage; do not treat as pure Sway damping.",
            "Fork to RNA/geometric coupling diagnosis before any scalar calibration.",
        ))

    if primary in {"PtfmSurge", "PtfmYaw"}:
        st = analysis["platform"].get(primary, {})
        low = st.get("low_freq_energy_fraction")
        peak_period = st.get("psd_peak_period_s")
        mean = abs(st.get("mean") or 0.0)
        amp = st.get("peak_abs_detrended") or 0.0
        drift_ratio = mean / amp if amp > 1e-12 else 0.0
        if (low is not None and low >= 0.65) or drift_ratio >= 0.25:
            out.append(rec(
                analysis["dof"],
                f"{primary} is low-frequency/drift dominated (low_freq_fraction={low}, peak_period_s={peak_period}, drift_ratio={drift_ratio:.3f}).",
                "Prioritize MoorDyn stiffness/pretension/equilibrium checks over AddCLin force-fitting.",
                "Use the physics-agent mooring path and inspect equilibrium drift plus tension channels if available.",
            ))

    if not out:
        out.append(rec(
            analysis["dof"],
            "No shadow alarm triggered by project v1 rules.",
            "No scouting-only evidence against this candidate/DOF run.",
            "Keep candidate selection and free-decay gates authoritative.",
            verdict="no_shadow_alarm",
        ))
    return out


def append_registry(records: list[dict[str, Any]]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    existing = REGISTRY.read_text(encoding="utf-8").splitlines() if REGISTRY.exists() else []
    next_id = len(existing) + 1
    with REGISTRY.open("a", encoding="utf-8") as fh:
        for rec in records:
            row = dict(rec)
            row["id"] = f"PSCOUT-{next_id:05d}"
            next_id += 1
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def render_report(state: dict[str, Any]) -> None:
    lines = [
        "# Project Shadow Scout Report",
        "",
        f"Generated: {state['generated_at']}",
        f"Candidate filter: `{state.get('candidate_id') or 'all latest successful candidates'}`",
        f"Runs analyzed: {len(state['analyses'])}",
        "",
        "Evidence level: `scouting_only`; this report cannot update final candidate recommendations.",
        "",
    ]
    for item in state["analyses"]:
        lines.append(f"## {item['candidate_id']} | {item['dof']} | {item['run_id']}")
        for finding in item["findings"]:
            lines.append(f"- Verdict: `{finding['verdict']}`")
            lines.append(f"- Observation: {finding['observation']}")
            lines.append(f"- Next action: {finding['next_action']}")
        lines.append("")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run(candidate_id: str | None, no_record: bool) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    rows = latest_successful_runs(candidate_id)
    analyses = [analyze_run(row) for row in rows]
    state = {
        "generated_at": utc_now(),
        "candidate_id": candidate_id,
        "analyses": analyses,
        "registry": str(REGISTRY),
        "report": str(LATEST_REPORT),
    }
    LATEST_STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(state)
    if not no_record:
        append_registry([f for a in analyses for f in a["findings"]])
    return state


def main() -> None:
    ap = argparse.ArgumentParser(description="Project-native non-authoritative shadow scout.")
    ap.add_argument("--candidate", help="Analyze latest successful runs for one candidate only.")
    ap.add_argument("--no-record", action="store_true", help="Do not append to shadow_experiment_registry.jsonl.")
    args = ap.parse_args()
    state = run(args.candidate, args.no_record)
    print(json.dumps({
        "runs_analyzed": len(state["analyses"]),
        "report": str(LATEST_REPORT),
        "registry": str(REGISTRY),
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
