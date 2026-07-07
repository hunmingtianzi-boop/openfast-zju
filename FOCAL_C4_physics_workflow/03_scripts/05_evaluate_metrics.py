from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
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

FIELDS = [
    "run_id",
    "candidate_id",
    "dof",
    "reference_kind",
    "old_heave_reference_used",
    "period",
    "reference_period",
    "period_error_pct",
    "zeta",
    "reference_zeta",
    "nrmse",
    "j_dof",
    "j_shape",
    "amplitude_error",
    "start_amplitude_error",
    "start_amplitude_abs_ratio",
    "start_amplitude_signed_ratio",
    "reference_decay_start",
    "model_decay_start",
    "reference_raw_start",
    "model_raw_start",
    "decay_start_time_shift",
    "phase_error",
    "envelope_error",
    "ler_error",
    "fp_error",
    "leak_json",
    "reference_leak_json",
    "morph_json",
    "metric_status",
    "notes",
]

DOF_META = {
    "FD_SURGE": ("Surge", "PtfmSurge"),
    "FD_SWAY": ("Sway", "PtfmSway"),
    "FD_HEAVE": ("Heave", "PtfmHeave"),
    "FD_ROLL": ("Roll", "PtfmRoll"),
    "FD_PITCH": ("Pitch", "PtfmPitch"),
    "FD_YAW": ("Yaw", "PtfmYaw"),
}
PTFM_CHANS = ["PtfmSurge", "PtfmSway", "PtfmHeave", "PtfmRoll", "PtfmPitch", "PtfmYaw"]


def read_reference(path: Path) -> dict[str, np.ndarray]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        cols: dict[str, list[float]] = {k: [] for k in ["Time", "Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw"]}
        for row in reader:
            for key in cols:
                cols[key].append(float(row[key]))
    out = {k: np.asarray(v, float) for k, v in cols.items()}
    out["t"] = out.pop("Time")
    return out


def read_openfast_out(path: Path) -> dict[str, np.ndarray]:
    rows: list[list[float]] = []
    header: list[str] | None = None
    with path.open(encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split()
            if header is None:
                if parts and parts[0] == "Time":
                    header = parts
                continue
            if parts[0].startswith("("):
                continue
            try:
                rows.append([float(x) for x in parts[: len(header)]])
            except ValueError:
                continue
    if header is None or not rows:
        raise ValueError(f"no OpenFAST text output table found: {path}")
    data = np.asarray(rows, float)
    return {name: data[:, idx] for idx, name in enumerate(header)}


def _env(x: np.ndarray, dt: float) -> np.ndarray:
    n = len(x)
    X = np.fft.fft(x)
    h = np.zeros(n)
    if n % 2 == 0:
        h[0] = h[n // 2] = 1
        h[1 : n // 2] = 2
    else:
        h[0] = 1
        h[1 : (n + 1) // 2] = 2
    e = np.abs(np.fft.ifft(X * h))
    w = max(3, int(0.5 / dt))
    return np.convolve(e, np.ones(w) / w, "same")


def decay_seg(t: np.ndarray, x: np.ndarray, floor: float = 0.02) -> tuple[np.ndarray, np.ndarray, np.ndarray, int, int, float]:
    dt = float(np.median(np.diff(t)))
    eq = float(np.median(x[-len(x) // 4 :]))
    xc = x - eq
    env = _env(xc, dt)
    i0 = int(np.argmax(env))
    thr = floor * env[i0]
    i1 = len(t) - 1
    for i in range(len(t) - 1, i0, -1):
        if env[i] > thr:
            i1 = i
            break
    return t[i0 : i1 + 1] - t[i0], xc[i0 : i1 + 1], env[i0 : i1 + 1], i0, i1, eq


def zero_cross_period(t: np.ndarray, x: np.ndarray) -> tuple[float, int]:
    s = np.signbit(x)
    crossings: list[float] = []
    for i in range(1, len(x)):
        if s[i] != s[i - 1] and x[i] != x[i - 1]:
            frac = -x[i - 1] / (x[i] - x[i - 1])
            crossings.append(float(t[i - 1] + frac * (t[i] - t[i - 1])))
    if len(crossings) < 3:
        return math.nan, len(crossings)
    half = np.diff(np.asarray(crossings))
    return float(2.0 * np.median(half)), len(crossings)


def zeta_from_seg(t: np.ndarray, e: np.ndarray, T: float) -> float:
    m = e > 0.05 * np.max(e)
    if int(np.sum(m)) < 5 or not math.isfinite(T) or T <= 0:
        return math.nan
    alpha = -float(np.polyfit(t[m], np.log(e[m]), 1)[0])
    w = 2 * np.pi / T
    return float(alpha / np.sqrt(alpha**2 + w**2))


def morph_metrics(tM: np.ndarray, xM: np.ndarray, eM: np.ndarray, tD: np.ndarray, xD: np.ndarray, eD: np.ndarray) -> dict[str, float]:
    tend = float(min(tM[-1], tD[-1]))
    m = tD <= tend
    tg = tD[m]
    xDg = xD[m]
    eDg = eD[m]
    xMg = np.interp(tg, tM, xM)
    eMg = np.interp(tg, tM, eM)
    rms = lambda a: float(np.sqrt(np.mean(a**2)))
    span = float(np.max(xDg) - np.min(xDg))
    j_wave = rms(xMg - xDg) / max(span, 1e-12)
    fl = 0.05 * max(float(np.max(eDg)), float(np.max(eMg)))
    xhatD = xDg / np.maximum(eDg, fl)
    xhatM = xMg / np.maximum(eMg, fl)
    j_shape = rms(xhatM - xhatD) / max(rms(xhatD), 1e-12)
    env_err = rms(eMg - eDg) / max(rms(eDg), 1e-12)
    A1D = float(np.max(eDg[: max(3, len(eDg) // 20)]))
    A1M = float(np.max(eMg[: max(3, len(eMg) // 20)]))
    fp_err = abs(A1M - A1D) / max(A1D, 1e-12)
    q = max(1, len(xDg) // 4)
    LERD = float(np.sum(xDg[-q:] ** 2) / max(np.sum(xDg[:q] ** 2), 1e-12))
    LERM = float(np.sum(xMg[-q:] ** 2) / max(np.sum(xMg[:q] ** 2), 1e-12))
    return {
        "J_wave": j_wave,
        "J_shape": j_shape,
        "env_err": env_err,
        "fp_err": fp_err,
        "LERD": LERD,
        "LERM": LERM,
        "LER_err": abs(LERM - LERD) / max(LERD, 1e-12),
        "Tend": tend,
    }


def latest_run(candidate: str, dof: str) -> dict[str, str] | None:
    runs = [
        r
        for r in cfgmod.read_csv(cfgmod.registry_path("run_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("dof") == dof and r.get("status") == "SUCCESS"
    ]
    return runs[-1] if runs else None


def find_out_file(run: dict[str, str]) -> Path | None:
    fst = cfgmod.resolve_workspace_path(run["fst_file"])
    candidates = [fst.with_suffix(".out")]
    candidates.extend(sorted(fst.parent.glob("*.out")))
    output_dir = cfgmod.resolve_workspace_path(run["output_dir"])
    candidates.extend(sorted(output_dir.glob("*.out")))
    for path in candidates:
        if path.exists():
            return path
    return None


def evaluate(run: dict[str, str], cfg: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    dof = run["dof"]
    ref_col, out_col = DOF_META[dof]
    if run["status"] != "SUCCESS":
        return "SKIPPED", {}, "requires a successful OpenFAST text output"
    out_file = find_out_file(run)
    if not out_file:
        return "FAIL", {}, "no OpenFAST .out file found"
    ref_file = cfgmod.reference_dir(cfg) / f"{dof}_curated_processed.csv"
    model = read_openfast_out(out_file)
    ref = read_reference(ref_file)
    if out_col not in model:
        raise ValueError(f"{out_col} missing in {out_file}")

    tM, xM, eM, i0, i1, _ = decay_seg(model["Time"], model[out_col])
    tD, xD, eD, j0, j1, _ = decay_seg(ref["t"], ref[ref_col])
    T = zero_cross_period(tM, xM)[0]
    T_ref = zero_cross_period(tD, xD)[0]
    zeta = zeta_from_seg(tM, eM, T)
    zeta_ref = zeta_from_seg(tD, eD, T_ref)
    morph = morph_metrics(tM, xM, eM, tD, xD, eD)
    ref_decay_start = float(xD[0])
    model_decay_start = float(xM[0])
    start_abs_ratio = abs(model_decay_start) / max(abs(ref_decay_start), 1e-12)
    start_signed_ratio = model_decay_start / ref_decay_start if abs(ref_decay_start) > 1e-12 else math.nan
    start_amplitude_error = abs(start_abs_ratio - 1.0)
    decay_start_time_shift = float(model["Time"][i0] - ref["t"][j0])

    p_seg = model[out_col][i0 : i1 + 1]
    p_rms = float(np.sqrt(np.mean((p_seg - np.median(p_seg[-len(p_seg) // 4 :])) ** 2)))
    leak: dict[str, float] = {}
    for chan in PTFM_CHANS:
        if chan == out_col or chan not in model:
            continue
        seg = model[chan][i0 : i1 + 1]
        leak[chan.replace("Ptfm", "")] = float(np.sqrt(np.mean((seg - np.median(seg[-len(seg) // 4 :])) ** 2)) / max(p_rms, 1e-12))
    ref_p_seg = ref[ref_col][j0 : j1 + 1]
    ref_p_rms = float(np.sqrt(np.mean((ref_p_seg - np.median(ref_p_seg[-len(ref_p_seg) // 4 :])) ** 2)))
    ref_leak: dict[str, float] = {}
    for col in ["Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw"]:
        if col == ref_col:
            continue
        seg = ref[col][j0 : j1 + 1]
        ref_leak[col] = float(np.sqrt(np.mean((seg - np.median(seg[-len(seg) // 4 :])) ** 2)) / max(ref_p_rms, 1e-12))

    period_error_pct = abs(T - T_ref) / T_ref * 100.0 if math.isfinite(T) and math.isfinite(T_ref) else math.nan
    payload = {
        "period": T,
        "reference_period": T_ref,
        "period_error_pct": period_error_pct,
        "zeta": zeta,
        "reference_zeta": zeta_ref,
        "nrmse": morph["J_wave"],
        "j_dof": morph["J_wave"],
        "j_shape": morph["J_shape"],
        "amplitude_error": morph["fp_err"],
        "start_amplitude_error": start_amplitude_error,
        "start_amplitude_abs_ratio": start_abs_ratio,
        "start_amplitude_signed_ratio": start_signed_ratio,
        "reference_decay_start": ref_decay_start,
        "model_decay_start": model_decay_start,
        "reference_raw_start": float(ref[ref_col][0]),
        "model_raw_start": float(model[out_col][0]),
        "decay_start_time_shift": decay_start_time_shift,
        "phase_error": "",
        "envelope_error": morph["env_err"],
        "ler_error": morph["LER_err"],
        "fp_error": morph["fp_err"],
        "leak": leak,
        "reference_leak": ref_leak,
        "morph": morph,
        "out_file": out_file.relative_to(cfgmod.workspace_root()).as_posix(),
    }
    return "PASS", payload, f"metrics computed from {payload['out_file']}"


def fnum(value: Any) -> str:
    if isinstance(value, float) and math.isfinite(value):
        return f"{value:.8g}"
    return "" if value == "" or value is None else str(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="CAND_TEST")
    parser.add_argument("--dof", default="FD_ROLL", choices=sorted(DOF_META))
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    run = latest_run(args.candidate, args.dof)
    if not run:
        status, payload, notes = "SKIPPED", {}, "no matching run found"
        run_id = ""
    else:
        run_id = run["run_id"]
        try:
            status, payload, notes = evaluate(run, cfg)
        except Exception as exc:
            status, payload, notes = "FAIL", {}, str(exc)

    row = {
        "run_id": run_id,
        "candidate_id": args.candidate,
        "dof": args.dof,
        "reference_kind": "curated",
        "old_heave_reference_used": "false",
        "period": fnum(payload.get("period")),
        "reference_period": fnum(payload.get("reference_period")),
        "period_error_pct": fnum(payload.get("period_error_pct")),
        "zeta": fnum(payload.get("zeta")),
        "reference_zeta": fnum(payload.get("reference_zeta")),
        "nrmse": fnum(payload.get("nrmse")),
        "j_dof": fnum(payload.get("j_dof")),
        "j_shape": fnum(payload.get("j_shape")),
        "amplitude_error": fnum(payload.get("amplitude_error")),
        "start_amplitude_error": fnum(payload.get("start_amplitude_error")),
        "start_amplitude_abs_ratio": fnum(payload.get("start_amplitude_abs_ratio")),
        "start_amplitude_signed_ratio": fnum(payload.get("start_amplitude_signed_ratio")),
        "reference_decay_start": fnum(payload.get("reference_decay_start")),
        "model_decay_start": fnum(payload.get("model_decay_start")),
        "reference_raw_start": fnum(payload.get("reference_raw_start")),
        "model_raw_start": fnum(payload.get("model_raw_start")),
        "decay_start_time_shift": fnum(payload.get("decay_start_time_shift")),
        "phase_error": fnum(payload.get("phase_error")),
        "envelope_error": fnum(payload.get("envelope_error")),
        "ler_error": fnum(payload.get("ler_error")),
        "fp_error": fnum(payload.get("fp_error")),
        "leak_json": json.dumps(payload.get("leak", {}), separators=(",", ":"), ensure_ascii=False),
        "reference_leak_json": json.dumps(payload.get("reference_leak", {}), separators=(",", ":"), ensure_ascii=False),
        "morph_json": json.dumps(payload.get("morph", {}), separators=(",", ":"), ensure_ascii=False),
        "metric_status": status,
        "notes": notes,
    }
    cfgmod.upsert_csv(cfgmod.registry_path("results_registry.csv"), FIELDS, "run_id", row)
    cfgmod.write_report("07_reports/metric_evaluation_report.md", "Metric Evaluation Report", [
        f"- Candidate: {args.candidate}",
        f"- DOF: {args.dof}",
        f"- Run ID: {run_id or 'none'}",
        f"- Status: {status}",
        f"- Reference kind: curated",
        f"- Old heave reference used: false",
        f"- Period: {row['period']}",
        f"- Reference period: {row['reference_period']}",
        f"- Period error pct: {row['period_error_pct']}",
        f"- Start amplitude error: {row['start_amplitude_error']}",
        f"- Start amplitude abs ratio: {row['start_amplitude_abs_ratio']}",
        f"- Reference/model decay start: {row['reference_decay_start']} / {row['model_decay_start']}",
        f"- Envelope error: {row['envelope_error']}",
        f"- LER error: {row['ler_error']}",
        f"- FP error: {row['fp_error']}",
        f"- Leak JSON: `{row['leak_json']}`",
        f"- Reference leak JSON: `{row['reference_leak_json']}`",
        f"- Notes: {notes}",
    ])
    cfgmod.append_log("08_logs/harness_build_log.md", f"metric_evaluator candidate={args.candidate} dof={args.dof} status={status}")
    print(f"Metric Evaluator: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
