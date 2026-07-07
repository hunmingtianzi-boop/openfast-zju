from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()

FIELDS = [
    "candidate_id",
    "baseline_candidate_id",
    "candidate_parent_id",
    "lineage_status",
    "target_dofs",
    "decision",
    "improved_targets",
    "regressed_targets",
    "unchanged_targets",
    "reason",
]

METRICS = [
    "period_error_pct",
    "envelope_error",
    "ler_error",
    "fp_error",
]

EPS = 1.0e-7
MAJOR_REGRESSION = {
    "period_error_pct": 0.25,
    "envelope_error": 0.05,
    "ler_error": 0.10,
    "fp_error": 0.05,
    "zeta_abs_error": 0.002,
}


def parse_dofs(value: str) -> list[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def load_json_field(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        obj = json.loads(value)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def candidate_row(candidate_id: str) -> dict[str, str] | None:
    for row in cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    return None


def infer_baseline(candidate: dict[str, str]) -> str:
    parent = candidate.get("parent_candidate_id", "").strip()
    if parent:
        return parent
    meta = load_json_field(candidate.get("parameter_vector_json", ""))
    parent = str(meta.get("decision_meta", {}).get("parent_candidate_id", "")).strip()
    if parent:
        return parent
    raise SystemExit("baseline not supplied and parent_candidate_id could not be inferred")


def infer_targets(candidate: dict[str, str]) -> list[str]:
    meta = load_json_field(candidate.get("parameter_vector_json", ""))
    selected = meta.get("decision_meta", {}).get("selected_dof_candidates", {})
    if isinstance(selected, dict):
        parent = candidate.get("parent_candidate_id", "")
        changed = [dof for dof, source in selected.items() if source and source != parent]
        if changed:
            return sorted(changed)
    return []


def latest_pass_metrics(candidate_id: str) -> dict[str, dict[str, str]]:
    run_rows = cfgmod.read_csv(cfgmod.registry_path("run_registry.csv"))
    run_by_id = {row.get("run_id"): row for row in run_rows if row.get("candidate_id") == candidate_id}
    latest: dict[str, dict[str, str]] = {}
    for row in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv")):
        if row.get("metric_status") != "PASS":
            continue
        run = run_by_id.get(row.get("run_id"))
        if not run:
            continue
        dof = row.get("dof") or run.get("dof", "")
        if dof and (dof not in latest or run.get("end_time", "") > latest[dof].get("_end_time", "")):
            latest[dof] = {**row, "_end_time": run.get("end_time", "")}
    return latest


def f(row: dict[str, str], key: str) -> float | None:
    try:
        value = row.get(key, "")
        return float(value) if value != "" else None
    except ValueError:
        return None


def zeta_abs_error(row: dict[str, str]) -> float | None:
    zeta = f(row, "zeta")
    ref = f(row, "reference_zeta")
    if zeta is None or ref is None:
        return None
    return abs(zeta - ref)


def metric_snapshot(row: dict[str, str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in METRICS:
        value = f(row, key)
        if value is not None:
            out[key] = value
    zerr = zeta_abs_error(row)
    if zerr is not None:
        out["zeta_abs_error"] = zerr
    return out


def compare_dof(candidate: dict[str, str], baseline: dict[str, str]) -> dict[str, Any]:
    cand = metric_snapshot(candidate)
    base = metric_snapshot(baseline)
    deltas = {key: cand[key] - base[key] for key in cand.keys() & base.keys()}
    improved = [key for key, delta in deltas.items() if delta < -EPS]
    regressed = [key for key, delta in deltas.items() if delta > EPS]
    major = [
        key for key, delta in deltas.items()
        if delta > MAJOR_REGRESSION.get(key, float("inf"))
    ]
    if improved and not major:
        decision = "IMPROVED"
    elif major:
        decision = "REGRESSED"
    elif improved:
        decision = "MIXED"
    else:
        decision = "UNCHANGED"
    return {
        "decision": decision,
        "candidate": cand,
        "baseline": base,
        "deltas": deltas,
        "improved_metrics": improved,
        "regressed_metrics": regressed,
        "major_regressions": major,
    }


def render_report(state: dict[str, Any]) -> None:
    lines = [
        f"- Candidate: {state['candidate_id']}",
        f"- Baseline: {state['baseline_candidate_id']}",
        f"- Candidate parent: {state['candidate_parent_id']}",
        f"- Lineage status: {state['lineage_status']}",
        f"- Target DOFs: {', '.join(state['target_dofs'])}",
        f"- Decision: {state['decision']}",
        f"- Reason: {state['reason']}",
        "",
        "## Target Comparisons",
        "",
    ]
    for dof, item in state["comparisons"].items():
        lines.append(f"### {dof}: {item['decision']}")
        for key, delta in sorted(item["deltas"].items()):
            base = item["baseline"].get(key)
            cand = item["candidate"].get(key)
            lines.append(f"- `{key}`: baseline={base:.8g}, candidate={cand:.8g}, delta={delta:+.8g}")
        lines.append("")
    lines.append("## Routing")
    lines.append("")
    if state["decision"] == "HOLD_LINEAGE_MISMATCH":
        lines.append("- Do not compare or package from mixed starting points.")
        lines.append("- Re-run the candidate from the intended baseline, or pass an explicit cross-parent override only for exploratory diagnostics.")
    elif state["decision"] == "HOLD_OBJECTIVE_NOT_IMPROVED":
        lines.append("- Do not package as final recommendation from this branch.")
        lines.append("- Loop back through physics-agent/free-decay diagnosis for the target DOF.")
        lines.append("- For `FD_SURGE` or `FD_YAW`, prefer MoorDyn equilibrium/tension and low-frequency drift review before AddCLin forcing.")
    else:
        lines.append("- Objective review passed; continue with consistency, shadow/evidence, and package gates.")
    cfgmod.write_report("07_reports/objective_review_report.md", "Objective Review Report", lines)


def run(candidate_id: str, baseline_id: str | None, target_dofs: list[str], allow_cross_parent: bool = False) -> dict[str, Any]:
    cfgmod.ensure_core_dirs()
    cand_row = candidate_row(candidate_id)
    if not cand_row:
        raise SystemExit(f"candidate not found: {candidate_id}")
    parent_id = infer_baseline(cand_row)
    baseline_id = baseline_id or parent_id
    if not target_dofs:
        target_dofs = infer_targets(cand_row)
    if not target_dofs:
        raise SystemExit("--target-dofs required when targets cannot be inferred")

    lineage_status = "same_parent"
    if baseline_id != parent_id:
        lineage_status = "cross_parent_allowed" if allow_cross_parent else "cross_parent_blocked"
    if lineage_status == "cross_parent_blocked":
        state = {
            "candidate_id": candidate_id,
            "baseline_candidate_id": baseline_id,
            "candidate_parent_id": parent_id,
            "lineage_status": lineage_status,
            "target_dofs": target_dofs,
            "decision": "HOLD_LINEAGE_MISMATCH",
            "reason": f"baseline {baseline_id} does not match candidate parent {parent_id}",
            "improved_targets": [],
            "regressed_targets": [],
            "unchanged_targets": [],
            "comparisons": {},
        }
        cfgmod.write_json("08_logs/objective_review/latest_objective_review_state.json", state)
        render_report(state)
        cfgmod.upsert_csv(cfgmod.registry_path("objective_review_registry.csv"), FIELDS, "candidate_id", {
            "candidate_id": candidate_id,
            "baseline_candidate_id": baseline_id,
            "candidate_parent_id": parent_id,
            "lineage_status": lineage_status,
            "target_dofs": ",".join(target_dofs),
            "decision": state["decision"],
            "improved_targets": "",
            "regressed_targets": "",
            "unchanged_targets": "",
            "reason": state["reason"],
        })
        return state

    candidate_metrics = latest_pass_metrics(candidate_id)
    baseline_metrics = latest_pass_metrics(baseline_id)
    comparisons: dict[str, Any] = {}
    missing: list[str] = []
    for dof in target_dofs:
        if dof not in candidate_metrics or dof not in baseline_metrics:
            missing.append(dof)
            continue
        comparisons[dof] = compare_dof(candidate_metrics[dof], baseline_metrics[dof])

    improved = [dof for dof, item in comparisons.items() if item["decision"] == "IMPROVED"]
    regressed = [dof for dof, item in comparisons.items() if item["decision"] in {"REGRESSED", "MIXED"}]
    unchanged = [dof for dof, item in comparisons.items() if item["decision"] == "UNCHANGED"]

    if missing:
        decision = "HOLD_OBJECTIVE_NOT_REVIEWABLE"
        reason = f"missing PASS metrics for target DOFs: {','.join(missing)}"
    elif unchanged or regressed:
        decision = "HOLD_OBJECTIVE_NOT_IMPROVED"
        pieces = []
        if unchanged:
            pieces.append(f"unchanged: {','.join(unchanged)}")
        if regressed:
            pieces.append(f"regressed/mixed: {','.join(regressed)}")
        reason = "; ".join(pieces)
    else:
        decision = "OBJECTIVE_REVIEW_PASS"
        reason = f"all target DOFs improved: {','.join(improved)}"

    state = {
        "candidate_id": candidate_id,
        "baseline_candidate_id": baseline_id,
        "candidate_parent_id": parent_id,
        "lineage_status": lineage_status,
        "target_dofs": target_dofs,
        "decision": decision,
        "reason": reason,
        "improved_targets": improved,
        "regressed_targets": regressed,
        "unchanged_targets": unchanged,
        "comparisons": comparisons,
    }
    cfgmod.write_json("08_logs/objective_review/latest_objective_review_state.json", state)
    render_report(state)
    cfgmod.upsert_csv(cfgmod.registry_path("objective_review_registry.csv"), FIELDS, "candidate_id", {
        "candidate_id": candidate_id,
        "baseline_candidate_id": baseline_id,
        "candidate_parent_id": parent_id,
        "lineage_status": lineage_status,
        "target_dofs": ",".join(target_dofs),
        "decision": decision,
        "improved_targets": ",".join(improved),
        "regressed_targets": ",".join(regressed),
        "unchanged_targets": ",".join(unchanged),
        "reason": reason,
    })
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Target-aware objective review gate before packaging.")
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--baseline")
    parser.add_argument("--target-dofs", default="", help="Comma-separated DOFs, for example FD_SURGE or FD_SURGE,FD_YAW.")
    parser.add_argument("--allow-cross-parent", action="store_true", help="Allow exploratory comparison against a baseline that is not the candidate parent.")
    args = parser.parse_args()
    state = run(args.candidate, args.baseline, parse_dofs(args.target_dofs), allow_cross_parent=args.allow_cross_parent)
    print(json.dumps({
        "decision": state["decision"],
        "reason": state["reason"],
        "report": str(cfgmod.workspace_root() / "07_reports" / "objective_review_report.md"),
    }, indent=2, ensure_ascii=False))
    return 0 if state["decision"] == "OBJECTIVE_REVIEW_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
