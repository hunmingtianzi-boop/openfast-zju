from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


cfgmod = _load_script("00_config_loader.py")
generator = _load_script("03_generate_candidates.py")
executor = _load_script("04_run_executor.py")
metrics = _load_script("05_evaluate_metrics.py")


DOF_CFG = {
    "FD_SURGE": {"idx": 0, "name": "Surge", "tmax": 900},
    "FD_SWAY": {"idx": 1, "name": "Sway", "tmax": 900},
    "FD_HEAVE": {"idx": 2, "name": "Heave", "tmax": 400},
    "FD_ROLL": {"idx": 3, "name": "Roll", "tmax": 520},
    "FD_PITCH": {"idx": 4, "name": "Pitch", "tmax": 520},
    "FD_YAW": {"idx": 5, "name": "Yaw", "tmax": 700},
}

DECISION_FIELDS = [
    "decision_id",
    "dof",
    "parent_candidate_id",
    "status",
    "fix_path",
    "selected_candidate_id",
    "R",
    "K_ratio",
    "clin",
    "bquad",
    "bmult",
    "moor_json",
    "conv_json",
    "fork_reason",
    "notes",
    "created_time",
]


def run_cmd(args: list[str]) -> None:
    proc = subprocess.run(args, cwd=cfgmod.workspace_root(), text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"command failed: {' '.join(args)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%m%d%H%M%S")


def safe_dof(dof: str) -> str:
    return dof.replace("FD_", "")


def candidate_row(candidate_id: str) -> dict[str, str]:
    for row in cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    raise RuntimeError(f"candidate not found: {candidate_id}")


def latest_result(candidate_id: str, dof: str) -> dict[str, str]:
    rows = [
        r
        for r in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv"))
        if r.get("candidate_id") == candidate_id and r.get("dof") == dof and r.get("metric_status") == "PASS"
    ]
    if not rows:
        raise RuntimeError(f"no passing metrics for {candidate_id} {dof}")
    return rows[-1]


def f(row: dict[str, str], key: str, default: float = math.nan) -> float:
    value = row.get(key, "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parent_meta(parent: dict[str, str]) -> dict[str, Any]:
    vector = json.loads(parent.get("parameter_vector_json") or "{}")
    meta = vector.get("decision_meta") or {}
    if not meta.get("dof_packages"):
        raise RuntimeError("parent candidate does not contain per-DOF package dispatch metadata")
    return meta


def dof_package(parent: dict[str, str], dof: str) -> tuple[Path, str, str, Path]:
    meta = parent_meta(parent)
    spec = meta["dof_packages"][dof]
    root = cfgmod.resolve_workspace_path(spec["model_input_dir"])
    fst_name = spec["fst_file"]
    fst = root / fst_name
    if not fst.exists():
        raise RuntimeError(f"missing FST for {dof}: {fst}")
    hd = executor._find_ref(fst, "HydroFile")
    if not hd:
        raise RuntimeError(f"missing HydroDyn ref for {dof}: {fst}")
    return root, spec.get("openfast_subdir", "."), fst_name, hd


def current_bquad(parent: dict[str, str], dof: str) -> float:
    _, _, _, hd = dof_package(parent, dof)
    lines = hd.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = executor._block_start(lines, "AddBQuad")
    mat = executor._read_matrix(lines, start)
    idx = int(DOF_CFG[dof]["idx"])
    return float(mat[idx][idx])


def current_ic(parent: dict[str, str], dof: str) -> float:
    root, _, fst_name, _ = dof_package(parent, dof)
    fst = root / fst_name
    ed = executor._find_ref(fst, "EDFile")
    if not ed:
        raise RuntimeError(f"missing ElastoDyn ref for {dof}: {fst}")
    key = executor.PTFM_KEY[DOF_CFG[dof]["name"]]
    for line in ed.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == key:
            return float(parts[0].strip('"'))
    raise RuntimeError(f"missing {key} in {ed}")


def signed_reference_ic(dof: str) -> float:
    cfg = cfgmod.load_config()
    ref_col, _ = metrics.DOF_META[dof]
    ref_file = cfgmod.reference_dir(cfg) / f"{dof}_curated_processed.csv"
    ref = metrics.read_reference(ref_file)
    _, x, _, _, _, _ = metrics.decay_seg(ref["t"], ref[ref_col])
    return float(x[0])


def signed_ic(parent: dict[str, str], dof: str, mode: str) -> float:
    ref_value = signed_reference_ic(dof)
    if mode == "reference_signed":
        return ref_value
    if mode == "parent_signed":
        sign = -1.0 if ref_value < 0 else 1.0
        return sign * abs(current_ic(parent, dof))
    raise ValueError(f"unknown IC mode: {mode}")


def hyd_edit(block: str, idx: int, value: float) -> dict[str, Any]:
    return {"block": block, "i": idx, "j": idx, "value": value}


def vector_for(parent: dict[str, str], dof: str, bquad: float, bmult: float, signed_ic: float) -> dict[str, Any]:
    meta = dict(parent_meta(parent))
    meta.update({
        "signed_ic_bquad_tune": True,
        "tune_dof": dof,
        "bquad_multiplier": bmult,
        "signed_initial_condition": signed_ic,
    })
    idx = int(DOF_CFG[dof]["idx"])
    return {
        "hydrodyn_edits": [hyd_edit("BQuad", idx, bquad)],
        "moordyn": {},
        "initial_conditions": {DOF_CFG[dof]["name"]: signed_ic},
        "tmax": DOF_CFG[dof]["tmax"],
        "decision_meta": meta,
    }


def register_probe(parent: dict[str, str], candidate_id: str, dof: str, bquad: float, bmult: float, signed_ic: float) -> None:
    generator.register_candidate(
        candidate_id,
        vector_for(parent, dof, bquad, bmult, signed_ic),
        stage="signed_ic_bquad_tune",
        parent=parent["candidate_id"],
        status="READY",
        notes=f"signed IC plus AddBQuad factor {bmult:g} for {dof}",
        created_by="15_signed_ic_bquad_tune.py",
        model_input_dir=parent["model_input_dir"],
        openfast_subdir_override=parent.get("openfast_subdir") or ".",
        fst_file_override=parent.get("fst_file") or "dof_packages_dispatch",
    )


def run_eval(candidate_id: str, dof: str) -> dict[str, str]:
    run_cmd([sys.executable, str(Path("03_scripts") / "04_run_executor.py"), "--candidate", candidate_id, "--dof", dof])
    run_cmd([sys.executable, str(Path("03_scripts") / "05_evaluate_metrics.py"), "--candidate", candidate_id, "--dof", dof])
    return latest_result(candidate_id, dof)


def score(row: dict[str, str], base: dict[str, str], cfg: dict[str, Any]) -> float:
    targets = cfg["targets"]
    period_limit = float(targets["period_error_limit_pct"])
    env_limit = float(targets["env_ok"])
    ler_limit = float(targets["ler_ok"])
    fp_limit = float(targets["fp_ok"])
    period_pen = max(0.0, f(row, "period_error_pct") - period_limit) / period_limit
    env_pen = max(0.0, f(row, "envelope_error") - env_limit) / env_limit
    ler_pen = max(0.0, f(row, "ler_error") - ler_limit) / ler_limit
    fp_pen = max(0.0, f(row, "fp_error") - fp_limit) / fp_limit
    zeta = f(row, "zeta")
    zeta_ref = f(row, "reference_zeta")
    zeta_rel = abs(zeta / zeta_ref - 1.0) if math.isfinite(zeta) and math.isfinite(zeta_ref) and zeta_ref else 10.0
    nrmse_gain = f(base, "nrmse") - f(row, "nrmse")
    return (
        f(row, "nrmse")
        + 0.05 * f(row, "j_shape")
        + 0.08 * zeta_rel
        + 0.70 * period_pen
        + 0.15 * env_pen
        + 0.10 * ler_pen
        + 0.15 * fp_pen
        - 0.10 * max(0.0, nrmse_gain)
    )


def conv_detail(row: dict[str, str], base: dict[str, str], cfg: dict[str, Any]) -> dict[str, Any]:
    targets = cfg["targets"]
    env = f(row, "envelope_error")
    base_env = f(base, "envelope_error")
    return {
        "period_error_pct": f(row, "period_error_pct"),
        "nrmse": f(row, "nrmse"),
        "zeta": f(row, "zeta"),
        "reference_zeta": f(row, "reference_zeta"),
        "env_err": env,
        "LER_err": f(row, "ler_error"),
        "fp_err": f(row, "fp_error"),
        "period_ok": f(row, "period_error_pct") <= float(targets["period_error_limit_pct"]),
        "env_ok": env <= float(targets["env_ok"]) or env <= float(targets["env_improve"]) * base_env,
        "ler_ok": f(row, "ler_error") <= float(targets["ler_ok"]),
        "fp_ok": f(row, "fp_error") <= float(targets["fp_ok"]),
    }


def write_decision(
    decision_id: str,
    dof: str,
    parent_id: str,
    selected_id: str,
    bquad: float,
    bmult: float,
    conv: dict[str, Any],
    status: str,
    notes: str,
) -> None:
    row = {
        "decision_id": decision_id,
        "dof": dof,
        "parent_candidate_id": parent_id,
        "status": status,
        "fix_path": "signed_ic_bquad",
        "selected_candidate_id": selected_id,
        "bquad": bquad,
        "bmult": bmult,
        "conv_json": json.dumps(conv, separators=(",", ":"), ensure_ascii=False),
        "moor_json": "{}",
        "fork_reason": "" if status == "CONVERGED" else "morphology-targets-not-all-closed",
        "notes": notes,
        "created_time": cfgmod.utc_now(),
    }
    cfgmod.upsert_csv(cfgmod.registry_path("decision_registry.csv"), DECISION_FIELDS, "decision_id", row)


def set_signed_ic(fst: Path, dof: str, value: float) -> None:
    ed = executor._find_ref(fst, "EDFile")
    if not ed:
        raise RuntimeError(f"missing ElastoDyn ref: {fst}")
    lines = ed.read_text(encoding="utf-8", errors="ignore").splitlines()
    for key in executor.PTFM_KEY.values():
        try:
            executor._set_val(lines, key, 0)
        except KeyError:
            pass
    executor._set_val(lines, executor.PTFM_KEY[DOF_CFG[dof]["name"]], f"{value:.10g}")
    ed.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_bquad(fst: Path, dof: str, value: float) -> None:
    hd = executor._find_ref(fst, "HydroFile")
    if not hd:
        raise RuntimeError(f"missing HydroDyn ref: {fst}")
    lines = hd.read_text(encoding="utf-8", errors="ignore").splitlines()
    idx = int(DOF_CFG[dof]["idx"])
    start = executor._block_start(lines, "AddBQuad")
    mat = executor._read_matrix(lines, start)
    mat[idx][idx] = value
    lines[start : start + 6] = executor._fmt_matrix(mat)
    hd.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_tmax(fst: Path, dof: str) -> None:
    lines = fst.read_text(encoding="utf-8", errors="ignore").splitlines()
    executor._set_val(lines, "TMax", DOF_CFG[dof]["tmax"])
    fst.write_text("\n".join(lines) + "\n", encoding="utf-8")


def register_composite(parent: dict[str, str], chosen: dict[str, dict[str, Any]], run_stamp: str) -> str:
    parent_id = parent["candidate_id"]
    composite_id = f"{parent_id}_BQIC_{run_stamp}"
    src = cfgmod.resolve_workspace_path(parent["model_input_dir"])
    dst = cfgmod.workspace_root() / "02_starting_model" / "bquad_tuned_packages" / composite_id
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

    parent_meta_obj = parent_meta(parent)
    new_packages: dict[str, dict[str, str]] = {}
    for dof, selected in chosen.items():
        old_spec = parent_meta_obj["dof_packages"][dof]
        dof_dir = dst / dof
        fst = dof_dir / old_spec["fst_file"]
        set_signed_ic(fst, dof, float(selected["signed_ic"]))
        set_bquad(fst, dof, float(selected["bquad"]))
        set_tmax(fst, dof)
        new_packages[dof] = {
            "model_input_dir": f"02_starting_model/bquad_tuned_packages/{composite_id}/{dof}",
            "openfast_subdir": ".",
            "fst_file": old_spec["fst_file"],
            "openfast_exe": f"02_starting_model/bquad_tuned_packages/{composite_id}/bin/openfast_x64.exe",
        }

    meta = {
        "parent_candidate_id": parent_id,
        "signed_ic_bquad_tune": True,
        "selected_dof_candidates": {dof: data["candidate_id"] for dof, data in chosen.items()},
        "bquad_multipliers": {dof: data["bmult"] for dof, data in chosen.items()},
        "signed_initial_conditions": {dof: data["signed_ic"] for dof, data in chosen.items()},
        "openfast_exe": f"02_starting_model/bquad_tuned_packages/{composite_id}/bin/openfast_x64.exe",
        "dof_packages": new_packages,
    }
    vector = {
        "hydrodyn_edits": [],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": meta,
    }
    generator.register_candidate(
        composite_id,
        vector,
        stage="signed_ic_bquad_composite",
        parent=parent_id,
        status="READY",
        notes="composite per-DOF package after signed IC and AddBQuad tuning",
        created_by="15_signed_ic_bquad_tune.py",
        model_input_dir=f"02_starting_model/bquad_tuned_packages/{composite_id}",
        openfast_subdir_override=".",
        fst_file_override="dof_packages_dispatch",
    )
    return composite_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", default="RBQ90_PBL255_IMPORTED_PACKAGE_PERIOD_TUNED_20225Z0000")
    parser.add_argument("--dofs", default="")
    parser.add_argument("--factors", default="1.0,1.5,2.0,3.0,4.0")
    parser.add_argument("--ic-scales", default="1.0")
    parser.add_argument("--ic-scale-overrides", default="")
    parser.add_argument("--ic-mode", choices=["parent_signed", "reference_signed"], default="parent_signed")
    parser.add_argument("--direct-composite-only", action="store_true")
    parser.add_argument("--skip-composite-run", action="store_true")
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    run_stamp = stamp()
    parent = candidate_row(args.parent)
    dofs = [d.strip() for d in args.dofs.split(",") if d.strip()] or list(DOF_CFG)
    factors = [float(x.strip()) for x in args.factors.split(",") if x.strip()]
    ic_scales = [float(x.strip()) for x in args.ic_scales.split(",") if x.strip()]
    ic_scale_overrides: dict[str, float] = {}
    for item in [x.strip() for x in args.ic_scale_overrides.split(",") if x.strip()]:
        key, sep, value = item.partition("=")
        if not sep:
            raise SystemExit(f"invalid --ic-scale-overrides item: {item}")
        ic_scale_overrides[key.strip()] = float(value)
    rows: list[dict[str, Any]] = []
    chosen: dict[str, dict[str, Any]] = {}

    if args.direct_composite_only:
        if set(dofs) != set(DOF_CFG):
            raise SystemExit("--direct-composite-only requires all six DOFs")
        for dof in dofs:
            base = latest_result(args.parent, dof)
            b0 = current_bquad(parent, dof)
            release_ic = signed_ic(parent, dof, args.ic_mode) * ic_scale_overrides.get(dof, ic_scales[0])
            chosen[dof] = {
                "candidate_id": args.parent,
                "bquad": b0,
                "bmult": 1.0,
                "signed_ic": release_ic,
                "score": "",
                "status": "DIRECT",
                "result": base,
            }
        composite_id = register_composite(parent, chosen, run_stamp)
        cfgmod.write_report("07_reports/signed_ic_bquad_tune_report.md", "Signed IC BQuad Tune Report", [
            f"- Parent: `{args.parent}`",
            f"- Composite candidate: `{composite_id}`",
            "- Mode: `direct-composite-only`",
            f"- IC mode: `{args.ic_mode}`",
            f"- IC scale overrides: `{args.ic_scale_overrides or 'none'}`",
            "- BQuad: unchanged from parent package",
        ])
        if not args.skip_composite_run:
            run_cmd([
                sys.executable,
                str(Path("03_scripts") / "13_run_candidate_sixdof.py"),
                "--candidate",
                composite_id,
                "--continue-on-failure",
            ])
        run_cmd([sys.executable, str(Path("03_scripts") / "06_select_candidates.py")])
        print(f"Signed IC direct composite complete: {composite_id}")
        return 0

    for dof in dofs:
        base = latest_result(args.parent, dof)
        b0 = current_bquad(parent, dof)
        base_ic = signed_ic(parent, dof, args.ic_mode)
        probes: list[tuple[float, dict[str, str], str, float, float, float, float]] = []
        for ic_scale in ic_scales:
            release_ic = base_ic * ic_scale
            for factor in factors:
                bquad = b0 * factor
                cid = f"BQIC_{safe_dof(dof)}_{run_stamp}_F{int(round(factor * 100)):03d}_I{int(round(ic_scale * 100)):03d}"
                register_probe(parent, cid, dof, bquad, factor, release_ic)
                print(f"running {cid} {dof} bmult={factor:g} ic_scale={ic_scale:g}", flush=True)
                result = run_eval(cid, dof)
                s = score(result, base, cfg)
                probes.append((s, result, cid, bquad, factor, release_ic, ic_scale))
                rows.append({
                    "dof": dof,
                    "candidate_id": cid,
                    "bquad_base": b0,
                    "bquad": bquad,
                    "bmult": factor,
                    "ic_scale": ic_scale,
                    "signed_ic": release_ic,
                    "score": s,
                    "period_error_pct": result.get("period_error_pct", ""),
                    "nrmse": result.get("nrmse", ""),
                    "zeta": result.get("zeta", ""),
                    "reference_zeta": result.get("reference_zeta", ""),
                    "envelope_error": result.get("envelope_error", ""),
                    "ler_error": result.get("ler_error", ""),
                    "fp_error": result.get("fp_error", ""),
                })
        best_score, best_row, best_cid, best_bquad, best_factor, best_ic, best_ic_scale = sorted(probes, key=lambda item: item[0])[0]
        best_factor = best_bquad / b0 if b0 else math.nan
        conv = conv_detail(best_row, base, cfg)
        status = "CONVERGED" if all(bool(conv[k]) for k in ["period_ok", "env_ok", "ler_ok", "fp_ok"]) else "DIAGNOSTIC-FORK"
        decision = f"DEC_BQIC_{safe_dof(dof)}_{run_stamp}"
        write_decision(
            decision,
            dof,
            args.parent,
            best_cid,
            best_bquad,
            best_factor,
            conv,
            status,
            f"{args.ic_mode} IC {best_ic:.6g} (scale {best_ic_scale:.3g}); BQuad {b0:.6g} x {best_factor:.3g}; score {best_score:.6g}",
        )
        chosen[dof] = {
            "candidate_id": best_cid,
            "bquad": best_bquad,
            "bmult": best_factor,
            "signed_ic": best_ic,
            "ic_scale": best_ic_scale,
            "score": best_score,
            "status": status,
            "result": best_row,
        }

    sweep_csv = cfgmod.workspace_root() / "05_registry" / f"signed_ic_bquad_sweep_{run_stamp}.csv"
    with sweep_csv.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "dof",
            "candidate_id",
            "bquad_base",
            "bquad",
            "bmult",
            "ic_scale",
            "signed_ic",
            "score",
            "period_error_pct",
            "nrmse",
            "zeta",
            "reference_zeta",
            "envelope_error",
            "ler_error",
            "fp_error",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    all_dofs_selected = set(chosen) == set(DOF_CFG)
    composite_id = register_composite(parent, chosen, run_stamp) if all_dofs_selected else ""
    lines = [
        f"- Parent: `{args.parent}`",
        f"- Composite candidate: `{composite_id or 'not created; subset run'}`",
        f"- Sweep table: `05_registry/{sweep_csv.name}`",
        f"- Factors: `{','.join(str(x) for x in factors)}`",
        f"- IC scales: `{','.join(str(x) for x in ic_scales)}`",
        f"- IC mode: `{args.ic_mode}`",
        "",
        "| DOF | Selected candidate | BQuad multiplier | Period error | NRMSE | zeta/ref | Status |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for dof in dofs:
        data = chosen[dof]
        row = data["result"]
        zr = f(row, "zeta") / f(row, "reference_zeta") if f(row, "reference_zeta") else math.nan
        lines.append(
            f"| {dof} | `{data['candidate_id']}` | {data['bmult']:.3g} | "
            f"{f(row, 'period_error_pct'):.3f}% | {f(row, 'nrmse'):.4f} | {zr:.3f} | {data['status']} |"
        )
    cfgmod.write_report("07_reports/signed_ic_bquad_tune_report.md", "Signed IC BQuad Tune Report", lines)

    if composite_id and not args.skip_composite_run:
        run_cmd([
            sys.executable,
            str(Path("03_scripts") / "13_run_candidate_sixdof.py"),
            "--candidate",
            composite_id,
            "--continue-on-failure",
        ])
    run_cmd([sys.executable, str(Path("03_scripts") / "06_select_candidates.py")])
    print(f"Signed IC BQuad tune complete: {composite_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
