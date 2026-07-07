from __future__ import annotations

import argparse
import importlib.util
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


cfgmod = _load_script("00_config_loader.py")
executor = _load_script("04_run_executor.py")


DOF_CFG = {
    "FD_SURGE": {"idx": 0, "meff_prior": 2.0e7},
    "FD_SWAY": {"idx": 1, "meff_prior": 2.0e7},
    "FD_HEAVE": {"idx": 2, "meff_prior": 2.0e7},
    "FD_ROLL": {"idx": 3, "meff_prior": 5.8e10},
    "FD_PITCH": {"idx": 4, "meff_prior": 5.8e10},
    "FD_YAW": {"idx": 5, "meff_prior": 2.3e9},
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


def run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=cfgmod.workspace_root(), text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return proc


def safe_stamp() -> str:
    return cfgmod.utc_now().replace(":", "").replace("-", "").replace("+", "Z")


def safe_dof(dof: str) -> str:
    return dof.replace("FD_", "")


def candidate_row(candidate_id: str) -> dict[str, str]:
    for row in cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    raise SystemExit(f"candidate not found: {candidate_id}")


def latest_result(candidate_id: str, dof: str) -> dict[str, str]:
    rows = [
        row
        for row in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv"))
        if row.get("candidate_id") == candidate_id and row.get("dof") == dof and row.get("metric_status") == "PASS"
    ]
    if not rows:
        raise RuntimeError(f"no PASS result for {candidate_id} {dof}")
    return rows[-1]


def latest_success_run(candidate_id: str, dof: str) -> dict[str, str]:
    rows = [
        row
        for row in cfgmod.read_csv(cfgmod.registry_path("run_registry.csv"))
        if row.get("candidate_id") == candidate_id and row.get("dof") == dof and row.get("status") == "SUCCESS"
    ]
    if not rows:
        raise RuntimeError(f"no SUCCESS run for {candidate_id} {dof}")
    return rows[-1]


def dof_spec(parent: dict[str, str], dof: str) -> dict[str, str]:
    cfg = cfgmod.load_config()
    vector = json.loads(parent.get("parameter_vector_json") or "{}")
    meta = vector.get("decision_meta") or {}
    packages = meta.get("dof_packages") or {}
    spec = packages.get(dof) or {}
    return {
        "model_input_dir": spec.get("model_input_dir") or parent["model_input_dir"],
        "openfast_subdir": spec.get("openfast_subdir") or parent.get("openfast_subdir") or cfg["model"]["openfast_subdir"],
        "fst_file": spec.get("fst_file") or parent.get("fst_file") or cfg["model"]["fst_file"],
        "openfast_exe": spec.get("openfast_exe") or meta.get("openfast_exe") or cfg.get("openfast_exe", ""),
    }


def current_addclin_diag(spec: dict[str, str], idx: int) -> float:
    model_dir = cfgmod.resolve_workspace_path(spec["model_input_dir"])
    fst_path = model_dir / spec["openfast_subdir"] / spec["fst_file"]
    hydro = executor._find_ref(fst_path, "HydroFile")
    if hydro is None:
        raise RuntimeError(f"HydroFile not found from {fst_path}")
    lines = hydro.read_text(encoding="utf-8", errors="ignore").splitlines()
    start = executor._block_start(lines, "AddCLin")
    mat = executor._read_matrix(lines, start)
    return float(mat[idx][idx])


def period_error_pct(row: dict[str, str]) -> float:
    return float(row["period_error_pct"])


def omega2(row: dict[str, str]) -> float:
    return (2.0 * math.pi / float(row["period"])) ** 2


def target_omega2(row: dict[str, str]) -> float:
    return (2.0 * math.pi / float(row["reference_period"])) ** 2


def hyd_edit(idx: int, value: float) -> dict[str, Any]:
    return {"block": "CLin", "i": idx, "j": idx, "value": value}


def vector_for(parent_id: str, dof: str, idx: int, clin: float, spec: dict[str, str], decision_id: str) -> dict[str, Any]:
    return {
        "hydrodyn_edits": [hyd_edit(idx, clin)],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {
            "decision_id": decision_id,
            "parent_candidate_id": parent_id,
            "period_tune_dof": dof,
            "openfast_exe": spec["openfast_exe"],
        },
    }


def register_period_candidate(
    candidate_id: str,
    parent_id: str,
    dof: str,
    clin: float,
    spec: dict[str, str],
    decision_id: str,
    label: str,
) -> str:
    idx = int(DOF_CFG[dof]["idx"])
    vector = vector_for(parent_id, dof, idx, clin, spec, decision_id)
    run_cmd([
        sys.executable,
        str(Path("03_scripts") / "03_generate_candidates.py"),
        "--candidate",
        candidate_id,
        "--mode",
        "period_tune",
        "--stage",
        "period_tune_imported",
        "--parent",
        parent_id,
        "--vector-json",
        json.dumps(vector, separators=(",", ":"), ensure_ascii=False),
        "--model-input-dir",
        spec["model_input_dir"],
        "--openfast-subdir",
        spec["openfast_subdir"],
        "--fst-file",
        spec["fst_file"],
        "--notes",
        f"period_tune_imported {dof} {label} AddCLin[{idx},{idx}]={clin:.9e}",
    ])
    return candidate_id


def candidate_clin(row: dict[str, str]) -> float:
    vector = json.loads(row.get("parameter_vector_json") or "{}")
    return float((vector.get("hydrodyn_edits") or [{}])[0]["value"])


def candidate_decision_id(row: dict[str, str]) -> str:
    vector = json.loads(row.get("parameter_vector_json") or "{}")
    return str((vector.get("decision_meta") or {}).get("decision_id") or "")


def run_eval(candidate_id: str, dof: str) -> dict[str, str]:
    run_cmd([sys.executable, str(Path("03_scripts") / "04_run_executor.py"), "--candidate", candidate_id, "--dof", dof])
    run_cmd([sys.executable, str(Path("03_scripts") / "05_evaluate_metrics.py"), "--candidate", candidate_id, "--dof", dof])
    row = latest_result(candidate_id, dof)
    if row.get("metric_status") != "PASS":
        raise RuntimeError(f"metrics did not PASS for {candidate_id} {dof}")
    return row


def selected_tuned_result(parent_id: str, dof: str) -> dict[str, Any] | None:
    rows = [
        row
        for row in cfgmod.read_csv(cfgmod.registry_path("decision_registry.csv"))
        if row.get("parent_candidate_id") == parent_id
        and row.get("dof") == dof
        and row.get("fix_path") == "addclin_period_tune_imported"
        and row.get("status") == "CONVERGED"
    ]
    if not rows:
        return None
    row = rows[-1]
    cid = row["selected_candidate_id"]
    return {
        "dof": dof,
        "decision_id": row["decision_id"],
        "status": "CONVERGED",
        "selected_candidate_id": cid,
        "selected_run": latest_success_run(cid, dof),
        "selected_result": latest_result(cid, dof),
        "clin": float(row["clin"]),
    }


def write_decision(row: dict[str, Any]) -> None:
    out = {k: row.get(k, "") for k in DECISION_FIELDS}
    out["created_time"] = out.get("created_time") or cfgmod.utc_now()
    cfgmod.upsert_csv(cfgmod.registry_path("decision_registry.csv"), DECISION_FIELDS, "decision_id", out)


def tune_dof(parent_id: str, dof: str, max_iterations: int, period_limit: float) -> dict[str, Any]:
    parent = candidate_row(parent_id)
    spec = dof_spec(parent, dof)
    idx = int(DOF_CFG[dof]["idx"])
    decision_id = f"DEC_IMP_{safe_dof(dof)}_{safe_stamp()}"
    base = latest_result(parent_id, dof)
    c0 = current_addclin_diag(spec, idx)
    w0 = omega2(base)
    wt = target_omega2(base)
    dws = wt - w0
    c_probe = c0 + 0.5 * float(DOF_CFG[dof]["meff_prior"]) * dws
    prefix = f"IMP_{safe_dof(dof)}_{decision_id[-10:]}"
    observations: list[tuple[float, float, str, dict[str, str]]] = [(c0, w0, parent_id, base)]

    probe_id = register_period_candidate(f"{prefix}_P1", parent_id, dof, c_probe, spec, decision_id, "probe")
    probe = run_eval(probe_id, dof)
    observations.append((c_probe, omega2(probe), probe_id, probe))
    best = min(observations, key=lambda item: period_error_pct(item[3]))
    if period_error_pct(best[3]) <= period_limit:
        max_iterations = 1

    for iteration in range(max(0, max_iterations - 1)):
        a, b = observations[-2], observations[-1]
        slope = (b[1] - a[1]) / (b[0] - a[0])
        if abs(slope) < 1e-18:
            break
        c_next = b[0] + (wt - b[1]) / slope
        candidate_id = register_period_candidate(f"{prefix}_S{iteration + 1}", parent_id, dof, c_next, spec, decision_id, f"solve{iteration + 1}")
        row = run_eval(candidate_id, dof)
        observations.append((c_next, omega2(row), candidate_id, row))
        if period_error_pct(row) < period_error_pct(best[3]):
            best = observations[-1]
        if period_error_pct(row) <= period_limit:
            break

    best_c, best_w, best_id, best_row = best
    r_base = (float(base["period"]) / float(base["reference_period"])) ** 2
    k_ratio = best_w / w0 if w0 else ""
    conv = {
        "base_period_error_pct": period_error_pct(base),
        "best_period_error_pct": period_error_pct(best_row),
        "period_ok": period_error_pct(best_row) <= period_limit,
        "nrmse": float(best_row["nrmse"]),
        "j_dof": float(best_row["j_dof"]),
        "observations": [
            {"candidate_id": cid, "clin": c, "period": float(row["period"]), "period_error_pct": period_error_pct(row)}
            for c, _, cid, row in observations
        ],
    }
    status = "CONVERGED" if conv["period_ok"] else "DIAGNOSTIC-FORK"
    write_decision({
        "decision_id": decision_id,
        "dof": dof,
        "parent_candidate_id": parent_id,
        "status": status,
        "fix_path": "addclin_period_tune_imported",
        "selected_candidate_id": best_id,
        "R": r_base,
        "K_ratio": k_ratio,
        "clin": best_c,
        "conv_json": json.dumps(conv, separators=(",", ":"), ensure_ascii=False),
        "fork_reason": "" if status == "CONVERGED" else "period-gate-unclosed",
        "notes": f"base {period_error_pct(base):.3f}% -> best {period_error_pct(best_row):.3f}%",
    })
    return {
        "dof": dof,
        "decision_id": decision_id,
        "status": status,
        "selected_candidate_id": best_id,
        "selected_run": latest_success_run(best_id, dof),
        "selected_result": best_row,
        "clin": best_c,
        "spec": spec,
        "observations": observations,
    }


def continue_dof(parent_id: str, dof: str, prefix: str, period_limit: float) -> dict[str, Any]:
    parent = candidate_row(parent_id)
    spec = dof_spec(parent, dof)
    idx = int(DOF_CFG[dof]["idx"])
    base = latest_result(parent_id, dof)
    c0 = current_addclin_diag(spec, idx)
    wt = target_omega2(base)
    candidates = [
        row
        for row in cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv"))
        if row.get("candidate_id", "").startswith(prefix)
    ]
    candidates.sort(key=lambda row: row.get("created_time", ""))
    if len(candidates) < 2:
        raise RuntimeError(f"need at least two existing observations for {prefix}")
    decision_id = candidate_decision_id(candidates[0]) or f"DEC_IMP_{safe_dof(dof)}_{safe_stamp()}"
    observations: list[tuple[float, float, str, dict[str, str]]] = [(c0, omega2(base), parent_id, base)]
    for cand in candidates:
        cid = cand["candidate_id"]
        row = latest_result(cid, dof)
        observations.append((candidate_clin(cand), omega2(row), cid, row))
    best = min(observations, key=lambda item: period_error_pct(item[3]))
    if period_error_pct(best[3]) > period_limit:
        a, b = observations[-2], observations[-1]
        slope = (b[1] - a[1]) / (b[0] - a[0])
        if abs(slope) < 1e-18:
            raise RuntimeError(f"cannot continue {prefix}: zero secant slope")
        c_next = b[0] + (wt - b[1]) / slope
        suffix = f"S{len([c for c in candidates if '_S' in c['candidate_id']]) + 1}"
        cid = register_period_candidate(f"{prefix}_{suffix}", parent_id, dof, c_next, spec, decision_id, f"continue-{suffix.lower()}")
        row = run_eval(cid, dof)
        observations.append((c_next, omega2(row), cid, row))
        if period_error_pct(row) < period_error_pct(best[3]):
            best = observations[-1]

    best_c, best_w, best_id, best_row = best
    w0 = omega2(base)
    conv = {
        "base_period_error_pct": period_error_pct(base),
        "best_period_error_pct": period_error_pct(best_row),
        "period_ok": period_error_pct(best_row) <= period_limit,
        "nrmse": float(best_row["nrmse"]),
        "j_dof": float(best_row["j_dof"]),
        "continued_from_prefix": prefix,
        "observations": [
            {"candidate_id": cid, "clin": c, "period": float(row["period"]), "period_error_pct": period_error_pct(row)}
            for c, _, cid, row in observations
        ],
    }
    status = "CONVERGED" if conv["period_ok"] else "DIAGNOSTIC-FORK"
    write_decision({
        "decision_id": decision_id,
        "dof": dof,
        "parent_candidate_id": parent_id,
        "status": status,
        "fix_path": "addclin_period_tune_imported",
        "selected_candidate_id": best_id,
        "R": (float(base["period"]) / float(base["reference_period"])) ** 2,
        "K_ratio": best_w / w0 if w0 else "",
        "clin": best_c,
        "conv_json": json.dumps(conv, separators=(",", ":"), ensure_ascii=False),
        "fork_reason": "" if status == "CONVERGED" else "period-gate-unclosed",
        "notes": f"continued {prefix}; base {period_error_pct(base):.3f}% -> best {period_error_pct(best_row):.3f}%",
    })
    return {
        "dof": dof,
        "decision_id": decision_id,
        "status": status,
        "selected_candidate_id": best_id,
        "selected_run": latest_success_run(best_id, dof),
        "selected_result": best_row,
        "clin": best_c,
        "spec": spec,
        "observations": observations,
    }


def copy_clean_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    for pattern in ("*.out", "*.MD.out", "*.ED.sum", "*.HD.sum", "*.MD.sum", "*.SrvD.sum", "*.sum"):
        for path in dst.glob(pattern):
            if path.is_file():
                path.unlink()


def make_composite(parent_id: str, tuned: dict[str, dict[str, Any]]) -> str:
    parent = candidate_row(parent_id)
    cfg = cfgmod.load_config()
    dofs = list(cfg["dofs"])
    composite_id = f"{parent_id}_PERIOD_TUNED_{safe_stamp()[-10:]}"
    root_rel = f"02_starting_model/period_tuned_packages/{composite_id}"
    root = cfgmod.resolve_workspace_path(root_rel)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    first_spec = dof_spec(parent, dofs[0])
    imported_root = cfgmod.resolve_workspace_path(first_spec["model_input_dir"]).parent
    bin_src = imported_root / "bin"
    if bin_src.exists():
        shutil.copytree(bin_src, root / "bin")

    packages: dict[str, dict[str, str]] = {}
    for dof in dofs:
        spec = dof_spec(parent, dof)
        if dof in tuned and tuned[dof]["status"] == "CONVERGED":
            run = tuned[dof]["selected_run"]
            src = cfgmod.resolve_workspace_path(run["input_dir"])
        else:
            src = cfgmod.resolve_workspace_path(spec["model_input_dir"])
        dst = root / dof
        copy_clean_tree(src, dst)
        packages[dof] = {
            "model_input_dir": f"{root_rel}/{dof}",
            "openfast_subdir": ".",
            "fst_file": spec["fst_file"],
            "openfast_exe": f"{root_rel}/bin/openfast_x64.exe",
        }

    vector = {
        "hydrodyn_edits": [],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {
            "parent_candidate_id": parent_id,
            "period_tune_imported": True,
            "tuned_dofs": {dof: tuned[dof]["selected_candidate_id"] for dof in sorted(tuned) if tuned[dof]["status"] == "CONVERGED"},
            "openfast_exe": f"{root_rel}/bin/openfast_x64.exe",
            "dof_packages": packages,
        },
    }
    run_cmd([
        sys.executable,
        str(Path("03_scripts") / "03_generate_candidates.py"),
        "--candidate",
        composite_id,
        "--mode",
        "period_tuned_composite",
        "--stage",
        "period_tuned_composite",
        "--parent",
        parent_id,
        "--vector-json",
        json.dumps(vector, separators=(",", ":"), ensure_ascii=False),
        "--model-input-dir",
        root_rel,
        "--openfast-subdir",
        ".",
        "--fst-file",
        "dof_packages_dispatch",
        "--notes",
        "composite per-DOF package after imported AddCLin period tuning",
    ])
    return composite_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", default="RBQ90_PBL255_IMPORTED_PACKAGE")
    parser.add_argument("--dofs", nargs="+", default=["FD_SURGE", "FD_HEAVE", "FD_PITCH"])
    parser.add_argument("--max-iterations", type=int, default=2)
    parser.add_argument("--continue-prefix", default=None)
    parser.add_argument("--make-composite", action="store_true")
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    period_limit = float(cfg["targets"]["period_error_limit_pct"])
    tuned: dict[str, dict[str, Any]] = {}
    lines = [
        f"- Parent: {args.parent}",
        f"- Period limit pct: {period_limit}",
        f"- DOFs requested: {', '.join(args.dofs)}",
    ]
    if args.continue_prefix:
        dof = args.dofs[0]
        result = continue_dof(args.parent, dof, args.continue_prefix, period_limit)
        tuned[dof] = result
        row = result["selected_result"]
        lines.append(
            f"- {dof}: {result['status']} selected={result['selected_candidate_id']} "
            f"period_error_pct={float(row['period_error_pct']):.6g} j_dof={float(row['j_dof']):.6g}"
        )
        for other in args.dofs[1:]:
            existing = selected_tuned_result(args.parent, other)
            if existing:
                tuned[other] = existing
                row = existing["selected_result"]
                lines.append(
                    f"- {other}: {existing['status']} selected={existing['selected_candidate_id']} "
                    f"period_error_pct={float(row['period_error_pct']):.6g} j_dof={float(row['j_dof']):.6g}"
                )
    else:
        for dof in args.dofs:
            result = tune_dof(args.parent, dof, args.max_iterations, period_limit)
            tuned[dof] = result
            row = result["selected_result"]
            lines.append(
                f"- {dof}: {result['status']} selected={result['selected_candidate_id']} "
                f"period_error_pct={float(row['period_error_pct']):.6g} j_dof={float(row['j_dof']):.6g}"
            )

    composite = ""
    if args.make_composite and set(args.dofs).issubset(tuned) and all(tuned[dof]["status"] == "CONVERGED" for dof in args.dofs):
        composite = make_composite(args.parent, tuned)
        lines.append(f"- Composite candidate: {composite}")
    elif args.make_composite:
        lines.append("- Composite candidate: not created because at least one DOF did not converge")

    cfgmod.write_report("07_reports/period_tune_imported_report.md", "Imported Package Period Tune Report", lines)
    print(f"Period tuner: complete composite={composite or '-'}")
    return 0 if all(v["status"] == "CONVERGED" for v in tuned.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
