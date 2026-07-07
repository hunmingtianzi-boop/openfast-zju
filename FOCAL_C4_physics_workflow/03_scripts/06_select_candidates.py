from __future__ import annotations

import importlib.util
from pathlib import Path


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()

FIELDS = [
    "candidate_id",
    "num_dof_completed",
    "period_pass_count",
    "max_period_error_pct",
    "all_six_period_under_limit",
    "start_amplitude_pass_count",
    "max_start_amplitude_error",
    "all_six_start_amplitude_under_limit",
    "j_total",
    "j_shape_total",
    "start_amplitude_error_total",
    "score_total",
    "rank",
    "decision",
    "reason",
]


def main() -> int:
    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    period_limit = float(cfg["targets"]["period_error_limit_pct"])
    start_limit = float(cfg["targets"].get("start_amplitude_error_limit", 0.20))
    start_weight = float(cfg["targets"].get("start_amplitude_penalty_weight", 0.25))
    expected = set(cfg["dofs"])
    results = [r for r in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv")) if r.get("metric_status") == "PASS"]
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in results:
        grouped.setdefault(row["candidate_id"], []).append(row)
    scored = []
    for candidate, items in grouped.items():
        latest_by_dof: dict[str, dict[str, str]] = {}
        for item in items:
            latest_by_dof[item["dof"]] = item
        errors = [float(r["period_error_pct"]) for r in latest_by_dof.values() if r.get("period_error_pct")]
        start_errors = [
            float(r["start_amplitude_error"])
            for r in latest_by_dof.values()
            if r.get("start_amplitude_error") not in {"", None}
        ]
        j_total = sum(float(r.get("j_dof") or 0.0) for r in latest_by_dof.values())
        j_shape_total = sum(float(r.get("j_shape") or 0.0) for r in latest_by_dof.values())
        start_total = sum(start_errors)
        score_total = j_total + start_weight * start_total
        all_six = set(latest_by_dof) == expected
        all_period = all_six and bool(errors) and all(e <= period_limit for e in errors)
        all_start = all_six and len(start_errors) == len(expected) and all(e <= start_limit for e in start_errors)
        scored.append((not (all_period and all_start), score_total, candidate, latest_by_dof, errors, j_shape_total, all_period, start_errors, all_start, start_total, j_total))
    rows = []
    for rank, (_, score_total, candidate, latest_by_dof, errors, j_shape_total, all_period, start_errors, all_start, start_total, j_total) in enumerate(sorted(scored), start=1):
        missing = sorted(expected - set(latest_by_dof))
        period_failed = [dof for dof, row in latest_by_dof.items() if row.get("period_error_pct") and float(row["period_error_pct"]) > period_limit]
        start_failed = [dof for dof, row in latest_by_dof.items() if not row.get("start_amplitude_error") or float(row["start_amplitude_error"]) > start_limit]
        decision = "PACKAGE_READY" if all_period and all_start else "HOLD"
        if missing:
            reason = f"missing/failed DOFs: {','.join(missing)}"
        elif not all_period:
            reason = f"period gate failed: {','.join(sorted(period_failed)) or 'unknown'}"
        elif not all_start:
            reason = f"start amplitude gate failed: {','.join(sorted(start_failed)) or 'unknown'}"
        else:
            reason = "six DOF period and start amplitude gates passed"
        rows.append({
            "candidate_id": candidate,
            "num_dof_completed": len(latest_by_dof),
            "period_pass_count": len([e for e in errors if e <= period_limit]),
            "max_period_error_pct": max(errors) if errors else "",
            "all_six_period_under_limit": str(all_period).lower(),
            "start_amplitude_pass_count": len([e for e in start_errors if e <= start_limit]),
            "max_start_amplitude_error": max(start_errors) if start_errors else "",
            "all_six_start_amplitude_under_limit": str(all_start).lower(),
            "j_total": f"{j_total:.8g}",
            "j_shape_total": f"{j_shape_total:.8g}",
            "start_amplitude_error_total": f"{start_total:.8g}",
            "score_total": f"{score_total:.8g}",
            "rank": rank,
            "decision": decision,
            "reason": reason,
        })
    cfgmod.write_csv(cfgmod.registry_path("candidate_summary.csv"), FIELDS, rows)
    cfgmod.write_report("07_reports/selection_report.md", "Selection Report", [
        f"- Candidates summarized: {len(rows)}",
        f"- Period gate: <= {period_limit:.2f} pct for all six DOFs",
        f"- Start amplitude gate: <= {start_limit:.2f} relative error for all six DOFs",
        f"- Ranking score: j_total + {start_weight:.3g} * start_amplitude_error_total",
        "- Decision rule: only candidates passing both gates may be packaged as a recommendation.",
    ])
    print("Selector: complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
