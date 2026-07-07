from __future__ import annotations

import argparse
import importlib.util
import json
import math
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
metrics = _load_script("05_evaluate_metrics.py")

FIELDS = [
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

DOF_CFG = {
    "FD_SURGE": {"idx": 0, "name": "Surge", "tmax": 900, "meff_prior": 2.0e7, "bquad": 9.23e5},
    "FD_SWAY": {"idx": 1, "name": "Sway", "tmax": 900, "meff_prior": 2.0e7, "bquad": 9.23e5},
    "FD_HEAVE": {"idx": 2, "name": "Heave", "tmax": 400, "meff_prior": 2.0e7, "bquad": 2.30e6},
    "FD_ROLL": {"idx": 3, "name": "Roll", "tmax": 520, "meff_prior": 5.8e10, "bquad": 1.68e10},
    "FD_PITCH": {"idx": 4, "name": "Pitch", "tmax": 520, "meff_prior": 5.8e10, "bquad": 1.68e10},
    "FD_YAW": {"idx": 5, "name": "Yaw", "tmax": 700, "meff_prior": 2.3e9, "bquad": 4.80e10},
}


class Budget:
    def __init__(self, max_runs: int):
        self.max_runs = max_runs
        self.used = 0

    @property
    def left(self) -> int:
        return self.max_runs - self.used

    def spend(self) -> None:
        if self.left <= 0:
            raise RuntimeError("budget exhausted")
        self.used += 1


def run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(args, cwd=cfgmod.workspace_root(), text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")
    return proc


def safe_id(text: str) -> str:
    return text.replace("FD_", "").replace("-", "_").upper()


def decision_id(dof: str) -> str:
    stamp = cfgmod.utc_now().replace(":", "").replace("-", "").replace("+", "Z")
    return f"DEC_{safe_id(dof)}_{stamp}"


def read_reference_ic(dof: str) -> float:
    cfg = cfgmod.load_config()
    ref_col, _ = metrics.DOF_META[dof]
    ref_file = cfgmod.reference_dir(cfg) / f"{dof}_curated_processed.csv"
    ref = metrics.read_reference(ref_file)
    _, x, _, _, _, _ = metrics.decay_seg(ref["t"], ref[ref_col])
    return round(float(abs(x[0])), 2)


def candidate_vector(
    dof: str,
    ic: float,
    hydrodyn_edits: list[dict[str, Any]] | None = None,
    moordyn: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = DOF_CFG[dof]
    return {
        "hydrodyn_edits": hydrodyn_edits or [],
        "moordyn": moordyn or {},
        "initial_conditions": {cfg["name"]: ic},
        "tmax": cfg["tmax"],
        "decision_meta": meta or {},
    }


def make_candidate(dof: str, label: str, vector: dict[str, Any], decision: str) -> str:
    cid = f"PHYS_{safe_id(dof)}_{label}_{decision[-10:]}"
    run_cmd([
        sys.executable,
        str(Path("03_scripts") / "03_generate_candidates.py"),
        "--candidate",
        cid,
        "--mode",
        "physics",
        "--stage",
        "physics_agent",
        "--vector-json",
        json.dumps(vector, separators=(",", ":"), ensure_ascii=False),
        "--notes",
        f"physics_agent {label}",
    ])
    return cid


def latest_result(candidate: str, dof: str) -> dict[str, str]:
    rows = [
        r
        for r in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv"))
        if r.get("candidate_id") == candidate and r.get("dof") == dof
    ]
    if not rows:
        raise RuntimeError(f"no metrics for {candidate} {dof}")
    return rows[-1]


def run_eval(candidate: str, dof: str, budget: Budget, dry_run: bool = False) -> dict[str, str] | None:
    budget.spend()
    args = [
        sys.executable,
        str(Path("03_scripts") / "04_run_executor.py"),
        "--candidate",
        candidate,
        "--dof",
        dof,
    ]
    if dry_run:
        args.append("--dry-run")
    run_cmd(args, check=not dry_run)
    if dry_run:
        return None
    run_cmd([
        sys.executable,
        str(Path("03_scripts") / "05_evaluate_metrics.py"),
        "--candidate",
        candidate,
        "--dof",
        dof,
    ])
    res = latest_result(candidate, dof)
    if res.get("metric_status") != "PASS":
        raise RuntimeError(f"metric evaluation failed for {candidate}: {res.get('notes')}")
    return res


def f(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value not in {"", None} else math.nan


def b_cost(row: dict[str, str], cfg: dict[str, Any]) -> float:
    targets = cfg["targets"]
    return max(f(row, "envelope_error") / float(targets["env_ok"]), f(row, "ler_error") / float(targets["ler_ok"]))


def convergence(row: dict[str, str], env_base: float, cfg: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    targets = cfg["targets"]
    period_err = abs(f(row, "period") - f(row, "reference_period")) / f(row, "reference_period")
    env = f(row, "envelope_error")
    ler = f(row, "ler_error")
    fp = f(row, "fp_error")
    env_ok = env <= float(targets["env_ok"]) or env <= float(targets["env_improve"]) * env_base
    detail = {
        "period_err": period_err,
        "env_err": env,
        "LER_err": ler,
        "fp_err": fp,
        "period_ok": period_err <= float(targets["period_error_limit_pct"]) / 100.0,
        "env_ok": env_ok,
        "ler_ok": ler <= float(targets["ler_ok"]),
        "fp_ok": fp <= float(targets["fp_ok"]),
    }
    return bool(detail["period_ok"] and detail["env_ok"] and detail["ler_ok"] and detail["fp_ok"]), detail


def leak_trip(row: dict[str, str], cfg: dict[str, Any]) -> tuple[str, float, float] | None:
    leak = json.loads(row.get("leak_json") or "{}")
    ref = json.loads(row.get("reference_leak_json") or "{}")
    worst: tuple[str, float, float] | None = None
    for dof, ratio in leak.items():
        rr = float(ref.get(dof, 0.0))
        ratio = float(ratio)
        if ratio > float(cfg["physics"]["leak_abs"]) and ratio > float(cfg["physics"]["leak_factor"]) * max(rr, 1e-3):
            if worst is None or ratio > worst[1]:
                worst = (dof, ratio, rr)
    return worst


def hyd_edit(block: str, idx: int, value: float) -> dict[str, Any]:
    return {"block": block, "i": idx, "j": idx, "value": value}


def write_decision(row: dict[str, Any]) -> None:
    out = {k: row.get(k, "") for k in FIELDS}
    out["created_time"] = out.get("created_time") or cfgmod.utc_now()
    cfgmod.upsert_csv(cfgmod.registry_path("decision_registry.csv"), FIELDS, "decision_id", out)
    cfgmod.write_report("07_reports/physics_agent_report.md", "Physics Agent Report", [
        f"- Decision ID: {out['decision_id']}",
        f"- DOF: {out['dof']}",
        f"- Status: {out['status']}",
        f"- Fix path: {out['fix_path']}",
        f"- Selected candidate: {out['selected_candidate_id']}",
        f"- R: {out['R']}",
        f"- K ratio: {out['K_ratio']}",
        f"- Fork reason: {out['fork_reason']}",
        f"- Notes: {out['notes']}",
    ])


def addclin_path(dof: str, did: str, ic: float, base: dict[str, str], budget: Budget, cfg: dict[str, Any], caveat: bool) -> dict[str, Any]:
    dc = DOF_CFG[dof]
    idx = int(dc["idx"])
    w_base = 2 * math.pi / f(base, "period")
    w_tgt = 2 * math.pi / f(base, "reference_period")
    dws = w_tgt**2 - w_base**2
    C_probe = 0.5 * float(dc["meff_prior"]) * dws
    probe_cid = make_candidate(dof, "APROBE", candidate_vector(dof, ic, [hyd_edit("CLin", idx, C_probe)], meta={"decision_id": did}), did)
    pr = run_eval(probe_cid, dof, budget)
    assert pr is not None
    w_probe = 2 * math.pi / f(pr, "period")
    if math.copysign(1, w_probe**2 - w_base**2) != math.copysign(1, C_probe) and abs(w_probe - w_base) > 1e-4:
        return {"status": "DIAGNOSTIC-FORK", "fork_reason": "addclin-monotonicity", "selected_candidate_id": probe_cid}
    slope = (w_probe**2 - w_base**2) / C_probe
    C_star = dws / slope
    solve_cid = make_candidate(dof, "ASOLVE", candidate_vector(dof, ic, [hyd_edit("CLin", idx, C_star)], meta={"decision_id": did}), did)
    a = run_eval(solve_cid, dof, budget)
    assert a is not None
    B0 = float(dc["bquad"])
    pts: list[tuple[float, float, str, dict[str, str]]] = [(B0, b_cost(a, cfg), solve_cid, a)]
    for mult in (4.0, 8.0, 12.0):
        if budget.left <= 1:
            break
        B = B0 * mult
        cid = make_candidate(dof, f"B{int(mult)}X", candidate_vector(dof, ic, [hyd_edit("CLin", idx, C_star), hyd_edit("BQuad", idx, B)], meta={"decision_id": did}), did)
        r = run_eval(cid, dof, budget)
        assert r is not None
        pts.append((B, b_cost(r, cfg), cid, r))
    B_best, _, best_cid, best = sorted(pts, key=lambda p: p[1])[0]
    ok, conv = convergence(best, f(base, "envelope_error"), cfg)
    leak = leak_trip(best, cfg)
    if leak:
        status, fork = "DIAGNOSTIC-FORK", f"energy-leak-after-search:{leak[0]}"
    elif ok:
        status, fork = ("CONVERGED-WITH-CAVEAT" if caveat else "CONVERGED"), ""
    else:
        status, fork = ("BUDGET-EXHAUSTED" if budget.left <= 0 else "DIAGNOSTIC-FORK"), "morphology-unclosed"
    return {
        "status": status,
        "fix_path": "addclin",
        "selected_candidate_id": best_cid,
        "clin": C_star,
        "bquad": B_best,
        "bmult": B_best / B0,
        "conv": conv,
        "fork_reason": fork,
    }


def moor_sens(w2_base: float, w2_probe: float, knob_rel: float) -> float:
    return math.log(w2_probe / w2_base) / math.log(knob_rel)


def mooring_path(dof: str, did: str, ic: float, base: dict[str, str], budget: Budget, cfg: dict[str, Any]) -> dict[str, Any]:
    dc = DOF_CFG[dof]
    idx = int(dc["idx"])
    R = (f(base, "period") / f(base, "reference_period")) ** 2
    w2_base = (2 * math.pi / f(base, "period")) ** 2
    w2_tgt = (2 * math.pi / f(base, "reference_period")) ** 2
    g_probe = float(cfg["physics"]["moor_ea_probe_frac"]) * R
    ea_cid = make_candidate(dof, "MEAPROBE", candidate_vector(dof, ic, moordyn={"EA_scale": g_probe}, meta={"decision_id": did}), did)
    ea = run_eval(ea_cid, dof, budget)
    assert ea is not None
    k_EA = moor_sens(w2_base, (2 * math.pi / f(ea, "period")) ** 2, g_probe)
    if k_EA < float(cfg["physics"]["moor_k_mono"]):
        return {"status": "DIAGNOSTIC-FORK", "fix_path": "mooring", "fork_reason": "EA-monotonicity", "selected_candidate_id": ea_cid, "moor": {"EA_probe": g_probe, "k_EA": k_EA}}
    g_star = math.exp(math.log(R) / k_EA) if k_EA > float(cfg["physics"]["moor_k_min"]) else math.inf
    if k_EA > float(cfg["physics"]["moor_k_min"]) and 1.0 < g_star <= float(cfg["physics"]["moor_ea_max"]):
        knob, key, probe_rel, probe_row = "EA", "EA_scale", g_probe, ea
    else:
        s_probe = float(cfg["physics"]["moor_unstrlen_probe"])
        ul_cid = make_candidate(dof, "MULPROBE", candidate_vector(dof, ic, moordyn={"UnstrLen_scale": s_probe}, meta={"decision_id": did}), did)
        ul = run_eval(ul_cid, dof, budget)
        assert ul is not None
        k_U = moor_sens(w2_base, (2 * math.pi / f(ul, "period")) ** 2, s_probe)
        if k_U >= 0:
            return {"status": "DIAGNOSTIC-FORK", "fix_path": "mooring", "fork_reason": "UnstrLen-monotonicity", "selected_candidate_id": ul_cid, "moor": {"k_EA": k_EA, "k_U": k_U}}
        knob, key, probe_rel, probe_row = "UnstrLen", "UnstrLen_scale", s_probe, ul
    pa = (0.0, math.log(w2_base))
    pb = (math.log(probe_rel), math.log((2 * math.pi / f(probe_row, "period")) ** 2))
    sol_cid = ""
    sol: dict[str, str] | None = None
    knob_rel = probe_rel
    for it in range(int(cfg["physics"]["moor_max_secant"])):
        if budget.left <= 2:
            break
        slope = (pb[1] - pa[1]) / (pb[0] - pa[0])
        knob_rel = math.exp(pb[0] + (math.log(w2_tgt) - pb[1]) / slope)
        if key == "UnstrLen_scale" and knob_rel < float(cfg["physics"]["moor_unstrlen_taut"]):
            return {"status": "DIAGNOSTIC-FORK", "fix_path": "mooring", "fork_reason": "mooring-saturated", "selected_candidate_id": sol_cid or ea_cid, "moor": {"knob": knob, "knob_rel": knob_rel, "k_EA": k_EA}}
        cid = make_candidate(dof, f"MSOLVE{it+1}", candidate_vector(dof, ic, moordyn={key: knob_rel}, meta={"decision_id": did}), did)
        sol = run_eval(cid, dof, budget)
        assert sol is not None
        sol_cid = cid
        if abs(f(sol, "period") - f(sol, "reference_period")) / f(sol, "reference_period") <= float(cfg["targets"]["period_error_limit_pct"]) / 100.0:
            break
        pa, pb = pb, (math.log(knob_rel), math.log((2 * math.pi / f(sol, "period")) ** 2))
    if sol is None:
        return {"status": "DIAGNOSTIC-FORK", "fix_path": "mooring", "fork_reason": "mooring-unsolved", "selected_candidate_id": ea_cid, "moor": {"k_EA": k_EA}}
    B0 = float(dc["bquad"])
    pts: list[tuple[float, float, str, dict[str, str]]] = [(B0, b_cost(sol, cfg), sol_cid, sol)]
    for mult in (4.0, 8.0, 12.0):
        if budget.left <= 1:
            break
        B = B0 * mult
        cid = make_candidate(dof, f"MB{int(mult)}X", candidate_vector(dof, ic, [hyd_edit("BQuad", idx, B)], moordyn={key: knob_rel}, meta={"decision_id": did}), did)
        r = run_eval(cid, dof, budget)
        assert r is not None
        pts.append((B, b_cost(r, cfg), cid, r))
    B_best, _, best_cid, best = sorted(pts, key=lambda p: p[1])[0]
    ok, conv = convergence(best, f(base, "envelope_error"), cfg)
    leak = leak_trip(best, cfg)
    if leak:
        status, fork = "DIAGNOSTIC-FORK", f"energy-leak-after-mooring:{leak[0]}"
    elif ok:
        status, fork = "CONVERGED", ""
    else:
        status, fork = ("BUDGET-EXHAUSTED" if budget.left <= 0 else "DIAGNOSTIC-FORK"), "mooring-morphology-unclosed"
    return {
        "status": status,
        "fix_path": "mooring",
        "selected_candidate_id": best_cid,
        "K_ratio": (f(base, "period") / f(best, "period")) ** 2,
        "bquad": B_best,
        "bmult": B_best / B0,
        "moor": {"knob": knob, "key": key, "knob_rel": knob_rel, "k_EA": k_EA},
        "conv": conv,
        "fork_reason": fork,
    }


def run_physics(dof: str, mode: str, max_runs: int, force_addclin: bool = False) -> dict[str, Any]:
    cfg = cfgmod.load_config()
    did = decision_id(dof)
    budget = Budget(max_runs)
    ic = read_reference_ic(dof)
    base_cid = make_candidate(dof, "BASE", candidate_vector(dof, ic, meta={"decision_id": did}), did)
    if mode == "dry-run":
        run_eval(base_cid, dof, budget, dry_run=True)
        row = {"decision_id": did, "dof": dof, "parent_candidate_id": "STARTING_MODEL", "status": "DRY-RUN", "selected_candidate_id": base_cid, "notes": "dry-run candidate materialized only"}
        write_decision(row)
        return row
    base = run_eval(base_cid, dof, budget)
    assert base is not None
    R = (f(base, "period") / f(base, "reference_period")) ** 2
    mech_trip = not (float(cfg["physics"]["R_lo"]) <= R <= float(cfg["physics"]["R_hi"]))
    leak = leak_trip(base, cfg)
    row: dict[str, Any] = {"decision_id": did, "dof": dof, "parent_candidate_id": "STARTING_MODEL", "R": R}
    if leak:
        row.update({"status": "DIAGNOSTIC-FORK", "selected_candidate_id": base_cid, "fork_reason": f"energy-leak:{leak[0]}", "notes": f"model leak {leak[1]:.3g}; reference {leak[2]:.3g}"})
    elif mech_trip and not force_addclin:
        allowed = set(cfg["physics"].get("mooring_repair_dofs", []))
        if dof in allowed:
            result = mooring_path(dof, did, ic, base, budget, cfg)
            row.update(result)
        else:
            row.update({"status": "DIAGNOSTIC-FORK", "selected_candidate_id": base_cid, "fork_reason": "mechanism-trip-not-mooring-allowed", "notes": "mechanism trip without leak is not automatically interpreted as mooring for this DOF"})
    else:
        row.update(addclin_path(dof, did, ic, base, budget, cfg, caveat=bool(mech_trip and force_addclin)))
    row.setdefault("fix_path", "")
    row.setdefault("K_ratio", "")
    row.setdefault("clin", "")
    row.setdefault("bquad", "")
    row.setdefault("bmult", "")
    row["moor_json"] = json.dumps(row.pop("moor", {}), separators=(",", ":"), ensure_ascii=False)
    row["conv_json"] = json.dumps(row.pop("conv", {}), separators=(",", ":"), ensure_ascii=False)
    write_decision(row)
    return row


def fixture_regression() -> int:
    expected = {
        "calib_pitch_regression_v2.json": ("pitch", "CONVERGED", None),
        "calib_yaw_v2.json": ("yaw", "CONVERGED", "mooring"),
        "calib_surge_v2.json": ("surge", "DIAGNOSTIC-FORK", "mooring"),
        "calib_sway_v2.json": ("sway", "DIAGNOSTIC-FORK", None),
    }
    lines = []
    ok = True
    for name, (dof, status, fix) in expected.items():
        path = cfgmod.workspace_root() / "09_fixtures" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        got = data.get("status")
        got_fix = data.get("fix_path")
        passed = got == status and (fix is None or got_fix == fix)
        ok = ok and passed
        lines.append(f"- {name}: expected {status}/{fix or '-'} got {got}/{got_fix or '-'} -> {'PASS' if passed else 'FAIL'}")
    cfgmod.write_report("07_reports/physics_fixture_regression_report.md", "Physics Fixture Regression Report", lines)
    cfgmod.append_log("08_logs/harness_build_log.md", f"physics_fixture_regression status={'PASS' if ok else 'FAIL'}")
    print(f"Physics fixture regression: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dof", choices=sorted(DOF_CFG), default="FD_PITCH")
    parser.add_argument("--mode", choices=["dry-run", "run", "fixtures"], default="dry-run")
    parser.add_argument("--max-runs", type=int, default=12)
    parser.add_argument("--force-addclin", action="store_true")
    args = parser.parse_args()
    cfgmod.ensure_core_dirs()
    if args.mode == "fixtures":
        return fixture_regression()
    row = run_physics(args.dof, args.mode, args.max_runs, force_addclin=args.force_addclin)
    print(f"Physics Agent: {row.get('status')} {row.get('decision_id')}")
    return 0 if row.get("status") not in {"BUDGET-EXHAUSTED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
