from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from . import config, memory, oracle, workflow_benchmark
from .schema import (
    DOFS,
    GLOBAL_SCORE_WEIGHTS,
    PARAMETER_GROUPS,
    ProjectGateResult,
    ProjectScorecard,
    to_plain,
)


SCORE_VERSION = 2
TREND_WINDOW = 20
CORE_COMPONENTS = ("period", "env", "ler", "fp")
COMPONENT_KEYS = {
    "period": "period_error_pct",
    "env": "envelope_error",
    "ler": "ler_error",
    "fp": "fp_error",
    "zeta": "zeta_error_ratio",
}


def build_scorecard(is_snapshot: bool = False) -> ProjectScorecard:
    cfg = config.load_config()
    path_error = _active_path_error(cfg)
    manifest = oracle.ensure_current_manifest(cfg)
    state = memory.read_global_state()
    cards = memory.load_run_cards()
    coupling = memory.read_coupling_memory()
    proposal_pool = memory.read_proposal_pool()
    best = _best_complete_card(cards) or _card_by_run_id(cards, state.get("current_best_run_id", ""))
    computed_at = config.utc_now()
    benchmark = workflow_benchmark.build_benchmark(is_snapshot=False)

    physics_fit_score, dof_table, complete_best, all_closed, physics_summary = _physics_fit_score(best, cfg)
    trend_summary = _project_trend_from_benchmark(benchmark.active_decision_summary)
    memory_reliability = benchmark.memory_diagnostics
    hard = _hard_violations(best)
    diagnostic = [item for item in hard if _is_diagnostic_violation(item)]
    other_hard = [item for item in hard if item not in diagnostic]
    validation_readiness = _validation_readiness(state, cards, all_closed, hard)
    final_validated = bool(validation_readiness.get("final_validated"))
    final_artifact_retained = bool(validation_readiness.get("artifact_retained"))
    has_proposal = bool(proposal_pool.get("proposals"))
    truth_valid = _truth_valid(to_plain(manifest))
    paths_clean = path_error == ""
    oracle_current = _oracle_current(manifest.oracle_id, state)

    workflow_health_score = benchmark.workflow_benchmark_score
    workflow_components = {
        **benchmark.components,
        "benchmark_status": benchmark.benchmark_status,
        "benchmark_confidence": benchmark.benchmark_confidence,
        "scheduler_recommendation": benchmark.scheduler_recommendation,
        "active_decision_summary": benchmark.active_decision_summary,
        "historical_evidence_summary": benchmark.historical_evidence_summary,
        "proposal_readiness": benchmark.proposal_readiness,
    }
    overall_raw = 0.55 * physics_fit_score + 0.45 * workflow_health_score

    gates = [
        _gate(
            "oracle_drift_or_truth_invalid",
            oracle_current and truth_valid,
            30.0,
            "oracle manifest, config sections, and six curated references are current"
            if oracle_current and truth_valid
            else "oracle drift or invalid truth manifest detected",
            tags=["oracle", "truth"],
        ),
        _gate(
            "legacy_or_forbidden_path",
            paths_clean,
            30.0,
            "active paths are confined to the active workflow" if paths_clean else path_error,
            tags=["path"],
        ),
        _gate(
            "complete_sixdof_current_best",
            complete_best,
            50.0,
            "current best has complete core metrics for all six DOFs"
            if complete_best
            else "no complete 6DOF current best with period/env/LER/fp metrics",
            tags=["current_best", "coverage"],
        ),
        _gate(
            "diagnostic_hard_constraints",
            not diagnostic,
            55.0,
            "no diagnostic hard constraint on current best" if not diagnostic else "; ".join(diagnostic),
            tags=["diagnostic"],
        ),
        _gate(
            "other_hard_constraints",
            not other_hard,
            65.0,
            "no non-diagnostic hard constraint on current best" if not other_hard else "; ".join(other_hard),
            tags=["hard_constraint"],
        ),
        _gate(
            "proposal_available_or_converged",
            has_proposal or all_closed,
            75.0,
            "proposal pool is available or project is converged"
            if has_proposal or all_closed
            else "not converged and no proposal pool is available",
            tags=["actionability"],
        ),
        _gate(
            "final_validation",
            final_validated,
            90.0,
            "final validation has passed" if final_validated else "final validation has not passed",
            tags=["final_validation"],
        ),
        _gate(
            "final_validation_artifact_retained",
            (not final_validated) or final_artifact_retained,
            92.0,
            "final validation artifact is retained"
            if (not final_validated) or final_artifact_retained
            else "final validation passed but full artifact is not retained",
            tags=["final_validation", "artifact"],
        ),
    ]
    active_caps = [gate.cap for gate in gates if not gate.passed and gate.cap is not None]
    overall_gated = min(overall_raw, *active_caps) if active_caps else overall_raw
    delivery_status = _delivery_status(
        complete_best=complete_best,
        all_closed=all_closed,
        hard=hard,
        diagnostic=diagnostic,
        final_validated=final_validated,
        final_artifact_retained=final_artifact_retained,
        overall_gated=overall_gated,
        paths_clean=paths_clean,
        truth_valid=truth_valid,
        oracle_current=oracle_current,
    )
    next_action = _next_action_summary(
        status=delivery_status,
        best=best,
        proposal_pool=proposal_pool,
        all_closed=all_closed,
        hard=hard,
        final_validated=final_validated,
    )
    score_interpretation = _score_interpretation(
        delivery_status=delivery_status,
        physics_summary=physics_summary,
        trend_summary=trend_summary,
        memory_reliability=memory_reliability,
        validation_readiness=validation_readiness,
        active_gates=[gate for gate in gates if not gate.passed],
        next_action=next_action,
    )

    return ProjectScorecard(
        created_at=computed_at,
        computed_at=computed_at,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        score_version=SCORE_VERSION,
        snapshot_id=_snapshot_id(manifest.oracle_id, computed_at) if is_snapshot else "",
        is_snapshot=is_snapshot,
        live_or_snapshot="snapshot" if is_snapshot else "live",
        source_state_updated_at=str(state.get("updated_at", "")),
        overall_raw_score=_round_score(overall_raw),
        overall_gated_score=_round_score(overall_gated),
        physics_fit_score=_round_score(physics_fit_score),
        workflow_health_score=_round_score(workflow_health_score),
        delivery_status=delivery_status,
        gate_results=gates,
        dof_score_table=dof_table,
        next_action_summary=next_action,
        trend_summary=trend_summary,
        memory_reliability=memory_reliability,
        validation_readiness=validation_readiness,
        score_interpretation=score_interpretation,
        components={
            "physics": physics_summary,
            "workflow": workflow_components,
            "workflow_benchmark": to_plain(benchmark),
            "weights": {
                "overall": {"physics_fit_score": 0.55, "workflow_health_score": 0.45},
                "physics_fit": {"mean_dof_quality": 0.70, "worst_dof_quality": 0.20, "closed_dof_ratio": 0.10},
                "physics_objective": dict(GLOBAL_SCORE_WEIGHTS),
                "workflow": {
                    "oracle_data_integrity": 20,
                    "evidence_coverage": 15,
                    "coupling_memory_reliability": 20,
                    "search_trend_efficiency": 20,
                    "actionability": 15,
                    "artifact_report_hygiene": 10,
                },
            },
        },
    )


def write_scorecard(scorecard: ProjectScorecard) -> Path:
    oracle.ensure_epoch_dirs(scorecard.oracle_id)
    path = oracle.epoch_root(scorecard.oracle_id) / "project_score.json"
    path.write_text(json.dumps(to_plain(scorecard), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_snapshot_scorecard(oracle_id: str | None = None) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    path = oracle.epoch_root(oid) / "project_score.json"
    return _read_json(path)


def report_lines(scorecard: ProjectScorecard) -> list[str]:
    data = to_plain(scorecard)
    interpretation = data.get("score_interpretation", {})
    physics = data.get("components", {}).get("physics", {})
    workflow = data.get("components", {}).get("workflow", {})
    trend = data.get("trend_summary", {})
    memory_rel = data.get("memory_reliability", {})
    validation = data.get("validation_readiness", {})
    next_action = data.get("next_action_summary", {})
    active_gates = [gate for gate in data.get("gate_results", []) if not gate.get("passed")]

    lines = [
        "## 结论",
        "",
        f"- 分数类型: `{data.get('live_or_snapshot', '')}`",
        f"- Snapshot id: `{data.get('snapshot_id', '') or 'not_snapshot'}`",
        f"- 状态: `{data.get('delivery_status', '')}`",
        f"- 项目总分: {data.get('overall_gated_score')}",
        f"- 原始总分: {data.get('overall_raw_score')}",
        f"- 物理拟合分: {data.get('physics_fit_score')}",
        f"- workflow 健康分: {data.get('workflow_health_score')}",
        f"- 解释: {interpretation.get('headline', '')}",
        f"- 最大阻塞: {interpretation.get('primary_blocker', '')}",
        f"- 下一步: `{next_action.get('action', '')}` - {next_action.get('reason', '')}",
    ]
    if next_action.get("command"):
        lines.append(f"- 建议命令: `{next_action.get('command')}`")

    lines.extend([
        "",
        "## Active Gates",
        "",
        "| Gate | Cap | Reason |",
        "|---|---:|---|",
    ])
    if active_gates:
        for gate in active_gates:
            lines.append(f"| `{gate.get('name', '')}` | {gate.get('cap')} | {gate.get('reason', '')} |")
    else:
        lines.append("| none |  | no active blocking gates |")

    worst = physics.get("worst_dof", {})
    lines.extend([
        "",
        "## 6DOF 拟合",
        "",
        f"- closed DOF: {physics.get('closed_dof_count', 0)}/{len(DOFS)}",
        f"- worst DOF: `{worst.get('dof', '')}` quality={_fmt(worst.get('quality'))} dominant=`{worst.get('dominant_error', '')}`",
        f"- closure gap: `{','.join(physics.get('closure_gap', {}).get('open_dofs', []))}`",
        "",
        "| DOF | Closed | Quality | Dominant | Period | Env | LER | fp | zeta |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|",
    ])
    for row in data.get("dof_score_table", []):
        comp = row.get("component_quality", {})
        lines.append(
            f"| `{row.get('dof', '')}` | `{row.get('closed')}` | {_fmt(row.get('quality'))} | "
            f"`{row.get('dominant_error', '')}` | {_fmt(comp.get('period'))} | {_fmt(comp.get('env'))} | "
            f"{_fmt(comp.get('ler'))} | {_fmt(comp.get('fp'))} | {_fmt(comp.get('zeta'))} |"
        )

    lines.extend([
        "",
        "## Workflow 子分",
        "",
        "| Component | Score |",
        "|---|---:|",
    ])
    for key, value in workflow.get("scores", {}).items():
        lines.append(f"| `{key}` | {_fmt(value)} |")

    lines.extend([
        "",
        "## 趋势与 Memory",
        "",
        f"- trend score: {_fmt(trend.get('score'))}",
        f"- trend window: {trend.get('window_size', 0)}",
        f"- best improvement ratio: {_fmt(trend.get('best_score_improvement_ratio'))}",
        f"- acceptance rate: {_fmt(trend.get('acceptance_rate'))}",
        f"- diagnostic rate: {_fmt(trend.get('diagnostic_rate'))}",
        f"- plateau count: {trend.get('plateau_count', 0)}",
        f"- memory reliability score: {_fmt(memory_rel.get('score'))}",
        f"- memory conflicts: {memory_rel.get('conflict_count', 0)}",
        f"- diagnostic evidence rate: {_fmt(memory_rel.get('diagnostic_evidence_rate'))}",
        "",
        "## Final Validation",
        "",
        f"- final_validated: `{validation.get('final_validated')}`",
        f"- artifact_retained: `{validation.get('artifact_retained')}`",
        f"- reason: {validation.get('reason', '')}",
    ])
    return lines


def summary(scorecard: ProjectScorecard) -> dict[str, Any]:
    active = [
        gate.name
        for gate in scorecard.gate_results
        if not gate.passed and gate.cap is not None and gate.cap < 100
    ]
    latest_snapshot = read_snapshot_scorecard(scorecard.oracle_id)
    snapshot_score = latest_snapshot.get("overall_gated_score")
    snapshot_id = latest_snapshot.get("snapshot_id", "")
    snapshot_at = latest_snapshot.get("computed_at") or latest_snapshot.get("created_at", "")
    is_snapshot = bool(scorecard.is_snapshot)
    return {
        "project_score": scorecard.overall_gated_score,
        "project_score_mode": scorecard.live_or_snapshot,
        "project_score_version": scorecard.score_version,
        "live_project_score": None if is_snapshot else scorecard.overall_gated_score,
        "snapshot_project_score": scorecard.overall_gated_score if is_snapshot else snapshot_score,
        "snapshot_project_score_id": scorecard.snapshot_id if is_snapshot else snapshot_id,
        "snapshot_project_score_computed_at": scorecard.computed_at if is_snapshot else snapshot_at,
        "project_raw_score": scorecard.overall_raw_score,
        "project_physics_fit_score": scorecard.physics_fit_score,
        "project_workflow_health_score": scorecard.workflow_health_score,
        "project_delivery_status": scorecard.delivery_status,
        "project_active_gates": active,
        "project_next_action": scorecard.next_action_summary,
        "project_worst_dof": scorecard.components.get("physics", {}).get("worst_dof", {}),
        "project_dominant_residuals": scorecard.components.get("physics", {}).get("dominant_residuals", {}),
        "project_trend_summary": scorecard.trend_summary,
        "project_memory_reliability": scorecard.memory_reliability,
        "project_validation_readiness": scorecard.validation_readiness,
        "project_score_interpretation": scorecard.score_interpretation,
    }


def _project_trend_from_benchmark(active_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        **active_summary,
        "score": active_summary.get("score"),
        "window_size": active_summary.get("active_decision_count", 0),
        "window_limit": active_summary.get("window_limit", TREND_WINDOW),
        "complete_card_count": active_summary.get("active_decision_count", 0),
        "benchmark_status": active_summary.get("status", ""),
        "note": active_summary.get("reason", ""),
    }


def _physics_fit_score(
    best: dict[str, Any] | None,
    cfg: dict[str, Any],
) -> tuple[float, list[dict[str, Any]], bool, bool, dict[str, Any]]:
    if not best:
        table = [_missing_dof_row(dof) for dof in DOFS]
        summary_data = _physics_summary(best, table, 0.0, 0.0, 0, False, False)
        return 0.0, table, False, False, summary_data

    per_dof = best.get("objective", {}).get("per_dof", {})
    table: list[dict[str, Any]] = []
    qualities: list[float] = []
    complete_best = len(per_dof) == len(DOFS)
    closed_count = 0

    for dof in DOFS:
        metrics = per_dof.get(dof)
        if not metrics:
            table.append(_missing_dof_row(dof))
            qualities.append(0.0)
            complete_best = False
            continue
        components = _normalized_components(metrics, cfg)
        missing_core = [name for name in CORE_COMPONENTS if components.get(name) is None]
        if missing_core:
            complete_best = False
        quality, component_quality = _weighted_quality(components)
        closed = bool(metrics.get("closed"))
        if closed:
            closed_count += 1
        qualities.append(quality)
        dominant = _dominant_error(components)
        table.append({
            "dof": dof,
            "closed": closed,
            "objective_score": _round_optional(_num(metrics.get("score"))),
            "quality": _round_score(quality),
            "dominant_error": dominant,
            "component_normalized_error": {key: _round_optional(value) for key, value in components.items()},
            "component_quality": {key: _round_optional(value) for key, value in component_quality.items()},
            "missing_core_components": missing_core,
        })

    mean_quality = sum(qualities) / len(DOFS)
    worst_quality = min(qualities) if qualities else 0.0
    closed_ratio = closed_count / len(DOFS)
    physics = 0.70 * mean_quality + 0.20 * worst_quality + 0.10 * closed_ratio * 100.0
    all_closed = complete_best and closed_count == len(DOFS)
    return physics, table, complete_best, all_closed, _physics_summary(
        best, table, mean_quality, worst_quality, closed_count, complete_best, all_closed
    )


def _physics_summary(
    best: dict[str, Any] | None,
    table: list[dict[str, Any]],
    mean_quality: float,
    worst_quality: float,
    closed_count: int,
    complete_best: bool,
    all_closed: bool,
) -> dict[str, Any]:
    open_rows = [row for row in table if not row.get("closed")]
    worst_row = min(table, key=lambda row: _num(row.get("quality")) or 0.0) if table else {}
    by_mode: dict[str, list[str]] = {}
    for row in table:
        by_mode.setdefault(str(row.get("dominant_error", "missing")), []).append(str(row.get("dof", "")))
    ranked_modes = [
        {"mode": mode, "count": len(dofs), "dofs": sorted(dofs)}
        for mode, dofs in sorted(by_mode.items(), key=lambda item: (-len(item[1]), item[0]))
    ]
    return {
        "complete_best": complete_best,
        "closed_dof_count": closed_count,
        "all_closed": all_closed,
        "mean_dof_quality": _round_score(mean_quality),
        "worst_dof_quality": _round_score(worst_quality),
        "worst_dof": {
            "dof": worst_row.get("dof", ""),
            "quality": worst_row.get("quality"),
            "dominant_error": worst_row.get("dominant_error", ""),
        },
        "dominant_residuals": {"ranked": ranked_modes},
        "closure_gap": {
            "open_dof_count": len(open_rows),
            "open_dofs": [str(row.get("dof", "")) for row in open_rows],
            "required_closed_dof_count": len(DOFS),
        },
        "source_run_id": best.get("run_id", "") if best else "",
        "source_candidate_id": best.get("observation", {}).get("candidate_id", "") if best else "",
    }


def _workflow_health_score(
    *,
    manifest: dict[str, Any],
    state: dict[str, Any],
    cards: list[dict[str, Any]],
    coupling: dict[str, Any],
    proposal_pool: dict[str, Any],
    best: dict[str, Any] | None,
    complete_best: bool,
    all_closed: bool,
    path_error: str,
    trend_summary: dict[str, Any],
    memory_reliability: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    reports_dir = oracle.epoch_reports_dir(manifest.get("oracle_id", ""))
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    report_names = {path.name for path in reports}
    registry_files = [
        config.registry_path("candidate_registry.csv"),
        config.registry_path("run_registry.csv"),
        config.registry_path("results_registry.csv"),
    ]

    oracle_score = 0.0
    oracle_score += 5.0 if path_error == "" else 0.0
    oracle_score += 5.0 if len(manifest.get("reference_files", {})) == len(DOFS) else 0.0
    heave_ref = manifest.get("reference_files", {}).get("FD_HEAVE", {}).get("relative_path", "")
    oracle_score += 3.0 if heave_ref.endswith("FD_HEAVE_curated_processed.csv") else 0.0
    sections = manifest.get("config_sections", {})
    oracle_score += 4.0 if all(sections.get(key) for key in ("reference", "model", "targets", "physics")) else 0.0
    oracle_score += 3.0 if manifest.get("target_source") == "active_curated_processed_reference" else 0.0

    evidence_score = 0.0
    evidence_score += min(4.0, len(cards) / 30.0 * 4.0)
    evidence_score += 5.0 if complete_best else 0.0
    evidence_score += sum(1.0 for path in registry_files if path.exists()) / len(registry_files) * 4.0
    evidence_score += 2.0 if best and best.get("evidence_sources") else 0.0

    actionability_score = _actionability_score(proposal_pool, state, best, all_closed)

    hygiene_score = 0.0
    hygiene_score += 2.0 if reports_dir.exists() else 0.0
    hygiene_score += min(3.0, len(report_names) / 6.0 * 3.0)
    hygiene_score += 2.0 if "project_score_report.md" in report_names else 0.0
    hygiene_score += 1.5 if "oracle_status_report.md" in report_names or "offline_import_report.md" in report_names else 0.0
    hygiene_score += 1.5 if state.get("oracle_id") == manifest.get("oracle_id") else 0.0

    scores = {
        "oracle_data_integrity": _round_score(min(20.0, oracle_score)),
        "evidence_coverage": _round_score(min(15.0, evidence_score)),
        "coupling_memory_reliability": _round_score(min(20.0, _num(memory_reliability.get("score")) or 0.0)),
        "search_trend_efficiency": _round_score(min(20.0, _num(trend_summary.get("score")) or 0.0)),
        "actionability": _round_score(min(15.0, actionability_score)),
        "artifact_report_hygiene": _round_score(min(10.0, hygiene_score)),
    }
    return sum(scores.values()), {
        "scores": scores,
        "run_cards_total": len(cards),
        "report_count": len(report_names),
        "coupling_groups": sorted((coupling.get("entries", {}) or {}).keys()) if isinstance(coupling, dict) else [],
        "proposal_count": len(proposal_pool.get("proposals", [])) if isinstance(proposal_pool, dict) else 0,
        "registry_files_present": [path.name for path in registry_files if path.exists()],
    }


def _trend_summary(cards: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    complete = [card for card in cards if _has_complete_core_metrics(card, cfg)]
    recent = sorted(complete, key=_card_sort_key)[-TREND_WINDOW:]
    scores = [_num(card.get("objective", {}).get("global_score")) for card in recent]
    scores = [score for score in scores if score is not None]
    n = len(recent)
    status_counts = Counter((_card_decision_status(card) or "UNKNOWN") for card in recent)
    diagnostic_count = sum(1 for card in recent if _card_is_diagnostic(card))
    rejected_count = status_counts.get("REJECTED", 0)
    accepted_count = status_counts.get("ACCEPTED", 0)
    decision_count = accepted_count + rejected_count
    acceptance_rate = accepted_count / decision_count if decision_count else 0.0
    rejection_rate = rejected_count / max(decision_count, 1)
    diagnostic_rate = diagnostic_count / max(n, 1)
    plateau_count = _plateau_count_by_scores(scores)
    plateau_rate = plateau_count / max(len(scores) - 1, 1)
    improvement_abs = 0.0
    improvement_ratio = 0.0
    if len(scores) >= 2:
        start_best = scores[0]
        end_best = min(scores)
        improvement_abs = max(0.0, start_best - end_best)
        improvement_ratio = improvement_abs / max(abs(start_best), 1e-12)

    coverage_score = min(4.0, n / TREND_WINDOW * 4.0)
    improvement_score = min(5.0, improvement_ratio / 0.10 * 5.0)
    acceptance_score = min(4.0, acceptance_rate / 0.30 * 4.0) if decision_count else 1.0 if n else 0.0
    diagnostic_score = max(0.0, 4.0 * (1.0 - diagnostic_rate * 2.0))
    plateau_score = max(0.0, 3.0 * (1.0 - plateau_rate))
    trend_score = coverage_score + improvement_score + acceptance_score + diagnostic_score + plateau_score

    return {
        "score": _round_score(min(20.0, trend_score)),
        "window_size": n,
        "window_limit": TREND_WINDOW,
        "complete_card_count": len(complete),
        "best_score_start": _round_optional(scores[0] if scores else None),
        "best_score_end": _round_optional(min(scores) if scores else None),
        "best_score_improvement": _round_score(improvement_abs),
        "best_score_improvement_ratio": _round_score(improvement_ratio),
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "evidence_only_count": status_counts.get("EVIDENCE_ONLY", 0),
        "diagnostic_count": diagnostic_count,
        "acceptance_rate": _round_score(acceptance_rate),
        "rejection_rate": _round_score(rejection_rate),
        "diagnostic_rate": _round_score(diagnostic_rate),
        "plateau_count": plateau_count,
        "plateau_rate": _round_score(plateau_rate),
    }


def _memory_reliability(coupling: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    entries = coupling.get("entries", {}) if isinstance(coupling, dict) else {}
    if not entries:
        return {
            "score": 0.0,
            "group_coverage": 0.0,
            "average_confidence": 0.0,
            "consistency": 0.0,
            "diagnostic_cleanliness": 0.0,
            "diagnostic_evidence_rate": 0.0,
            "conflict_count": 0,
            "evidence_run_count": 0,
            "groups": {},
        }
    cards_by_id = {card.get("run_id", ""): card for card in cards}
    group_coverage = sum(1 for group in PARAMETER_GROUPS if group in entries) / len(PARAMETER_GROUPS)
    confidences = [_num(entry.get("confidence")) or 0.0 for entry in entries.values()]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    conflict_count = 0
    affected_pair_count = 0
    evidence_ids: set[str] = set()
    diagnostic_evidence_ids: set[str] = set()
    group_details: dict[str, Any] = {}
    for group, entry in entries.items():
        improves = set(entry.get("improves", []))
        damages = set(entry.get("damages", []))
        conflicts = sorted(improves & damages)
        affected = set(entry.get("affected_dofs", [])) | improves | damages
        conflict_count += len(conflicts)
        affected_pair_count += len(affected)
        group_evidence = [run_id for run_id in entry.get("evidence_run_ids", []) if run_id]
        evidence_ids.update(group_evidence)
        diagnostic_runs = [run_id for run_id in group_evidence if _card_is_diagnostic(cards_by_id.get(run_id, {}))]
        diagnostic_evidence_ids.update(diagnostic_runs)
        group_details[group] = {
            "confidence": _round_optional(_num(entry.get("confidence"))),
            "affected_dofs": sorted(affected),
            "improves": sorted(improves),
            "damages": sorted(damages),
            "conflicts": conflicts,
            "evidence_count": len(group_evidence),
            "diagnostic_evidence_count": len(diagnostic_runs),
        }

    consistency = 1.0 - conflict_count / max(affected_pair_count, 1)
    diagnostic_rate = len(diagnostic_evidence_ids) / max(len(evidence_ids), 1)
    diagnostic_cleanliness = 1.0 - diagnostic_rate
    score = (
        4.0
        + 4.0 * group_coverage
        + 4.0 * average_confidence
        + 4.0 * max(0.0, consistency)
        + 4.0 * max(0.0, diagnostic_cleanliness)
    )
    return {
        "score": _round_score(min(20.0, score)),
        "group_coverage": _round_score(group_coverage),
        "average_confidence": _round_score(average_confidence),
        "consistency": _round_score(max(0.0, consistency)),
        "diagnostic_cleanliness": _round_score(max(0.0, diagnostic_cleanliness)),
        "diagnostic_evidence_rate": _round_score(diagnostic_rate),
        "conflict_count": conflict_count,
        "affected_pair_count": affected_pair_count,
        "evidence_run_count": len(evidence_ids),
        "diagnostic_evidence_count": len(diagnostic_evidence_ids),
        "groups": group_details,
    }


def _validation_readiness(
    state: dict[str, Any],
    cards: list[dict[str, Any]],
    all_closed: bool,
    hard: list[str],
) -> dict[str, Any]:
    candidate = str(state.get("final_validation_candidate_id") or "")
    final_validated = _final_validated(state, all_closed, hard)
    candidate_cards = [
        card for card in cards
        if candidate and card.get("observation", {}).get("candidate_id") == candidate
    ]
    retained_cards = [
        card for card in candidate_cards
        if _artifact_retained(card.get("artifact_policy", ""))
    ]
    complete_cards = [card for card in candidate_cards if len(card.get("objective", {}).get("per_dof", {})) == len(DOFS)]
    if not all_closed:
        reason = "not all six DOFs are closed"
    elif hard:
        reason = "hard constraints remain on current best"
    elif not final_validated:
        reason = "final validation has not passed"
    elif not retained_cards:
        reason = "final validation passed but no retained full artifact was found"
    else:
        reason = "final validation is complete and artifact is retained"
    return {
        "final_validated": final_validated,
        "artifact_retained": bool(retained_cards),
        "candidate_id": candidate,
        "validation_card_count": len(candidate_cards),
        "complete_validation_card_count": len(complete_cards),
        "retained_validation_card_ids": [card.get("run_id", "") for card in retained_cards],
        "reason": reason,
    }


def _active_path_error(cfg: dict[str, Any]) -> str:
    try:
        config.assert_active_paths(cfg)
    except Exception as exc:
        return str(exc)
    paths = config.active_paths(cfg)
    joined = "\n".join(str(value) for value in paths.values()).lower().replace("\\", "/")
    forbidden = [str(item).lower().replace("\\", "/") for item in cfg.get("forbidden_paths", [])]
    hits = [item for item in forbidden if item and item in joined]
    return f"active global-loop paths contain forbidden legacy tokens: {hits}" if hits else ""


def _truth_valid(manifest: dict[str, Any]) -> bool:
    refs = manifest.get("reference_files", {})
    heave = refs.get("FD_HEAVE", {}).get("relative_path", "")
    sections = manifest.get("config_sections", {})
    return bool(
        manifest.get("target_source") == "active_curated_processed_reference"
        and len(refs) == len(DOFS)
        and all(dof in refs for dof in DOFS)
        and heave.endswith("FD_HEAVE_curated_processed.csv")
        and all(sections.get(key) for key in ("reference", "model", "targets", "physics"))
    )


def _oracle_current(oracle_id: str, state: dict[str, Any]) -> bool:
    top_path = config.global_memory_root() / "global_state.json"
    top_state = _read_json(top_path)
    ids = [
        state.get("oracle_id"),
        state.get("epoch_id"),
        top_state.get("oracle_id"),
        top_state.get("current_oracle_id"),
        top_state.get("current_epoch_id"),
    ]
    present = [item for item in ids if item]
    return not present or all(item == oracle_id for item in present)


def _delivery_status(
    *,
    complete_best: bool,
    all_closed: bool,
    hard: list[str],
    diagnostic: list[str],
    final_validated: bool,
    final_artifact_retained: bool,
    overall_gated: float,
    paths_clean: bool,
    truth_valid: bool,
    oracle_current: bool,
) -> str:
    if not paths_clean or not truth_valid or not oracle_current or not complete_best:
        return "BLOCKED"
    if diagnostic:
        return "DIAGNOSTIC"
    if hard:
        return "BLOCKED"
    if all_closed and final_validated and final_artifact_retained and overall_gated >= 90.0:
        return "DELIVERABLE"
    if all_closed and final_validated and not final_artifact_retained:
        return "BLOCKED"
    if all_closed:
        return "READY_FOR_FINAL_VALIDATE"
    return "CALIBRATING"


def _next_action_summary(
    *,
    status: str,
    best: dict[str, Any] | None,
    proposal_pool: dict[str, Any],
    all_closed: bool,
    hard: list[str],
    final_validated: bool,
) -> dict[str, Any]:
    if not best:
        return {
            "action": "bootstrap_memory",
            "reason": "no current best RunCard is available",
            "command": "python 03_scripts/20_global_calibration_loop.py --offline-import",
        }
    if hard:
        return {"action": "review_diagnostic", "reason": "; ".join(hard), "command": ""}
    if all_closed and not final_validated:
        return {
            "action": "final_validate",
            "reason": "all six DOFs are closed but final validation has not passed",
            "command": "python 03_scripts/20_global_calibration_loop.py --final-validate --run-live",
        }
    if status == "DELIVERABLE":
        return {"action": "deliver", "reason": "final validation passed and project is deliverable", "command": ""}
    proposals = proposal_pool.get("proposals", []) if isinstance(proposal_pool, dict) else []
    if proposals:
        top = proposals[0]
        action = top.get("action", {})
        proposal_id = top.get("proposal_id", "")
        if action.get("registration_ready"):
            return {
                "action": "materialize_top_proposal",
                "reason": f"top proposal targets {top.get('target_dof', '')} with priority {top.get('priority')}",
                "proposal_id": proposal_id,
                "command": f"python 03_scripts/20_global_calibration_loop.py --materialize-proposal {proposal_id}",
            }
        return {
            "action": "review_non_materializable_proposal",
            "reason": action.get("reason_not_ready", "top proposal is diagnostic-only"),
            "proposal_id": proposal_id,
            "command": "",
        }
    return {
        "action": "generate_local_proposal_pool",
        "reason": "project is not converged and no proposal pool is available",
        "command": "python 03_scripts/20_global_calibration_loop.py --local-polish --top-k 3",
    }


def _score_interpretation(
    *,
    delivery_status: str,
    physics_summary: dict[str, Any],
    trend_summary: dict[str, Any],
    memory_reliability: dict[str, Any],
    validation_readiness: dict[str, Any],
    active_gates: list[ProjectGateResult],
    next_action: dict[str, Any],
) -> dict[str, Any]:
    open_count = physics_summary.get("closure_gap", {}).get("open_dof_count", 0)
    worst = physics_summary.get("worst_dof", {})
    primary_blocker = active_gates[0].name if active_gates else "none"
    if delivery_status == "CALIBRATING":
        headline = (
            f"项目流程可继续推进，但还有 {open_count} 个 DOF 未 closed；"
            f"当前 worst DOF 是 {worst.get('dof', '')}，主导残差是 {worst.get('dominant_error', '')}。"
        )
    elif delivery_status == "DELIVERABLE":
        headline = "项目已通过 final validation，且评分达到可交付门槛。"
    elif delivery_status == "READY_FOR_FINAL_VALIDATE":
        headline = "六自由度已 closed，下一步应做 final validation。"
    elif delivery_status == "DIAGNOSTIC":
        headline = "项目触发诊断硬约束，需要人工 review 后再推进。"
    else:
        headline = "项目存在基础阻塞 gate，当前不能作为可交付结果。"
    return {
        "headline": headline,
        "primary_blocker": primary_blocker,
        "trend_note": (
            f"最近 {trend_summary.get('window_size', 0)} 张完整 RunCard 的 trend score "
            f"为 {trend_summary.get('score')}。"
        ),
        "memory_note": (
            f"CouplingMemory reliability 为 {memory_reliability.get('score')}，"
            f"冲突数 {memory_reliability.get('conflict_count', 0)}。"
        ),
        "validation_note": validation_readiness.get("reason", ""),
        "next_action": next_action.get("action", ""),
        "live_snapshot_note": "status uses live score; project_score.json is a snapshot and may differ.",
    }


def _actionability_score(
    proposal_pool: dict[str, Any],
    state: dict[str, Any],
    best: dict[str, Any] | None,
    all_closed: bool,
) -> float:
    if all_closed:
        return 15.0
    proposals = proposal_pool.get("proposals", []) if isinstance(proposal_pool, dict) else []
    if not proposals:
        return 2.0 if best and state.get("mode") in {"global_step", "local_polish"} else 0.0
    top = proposals[0]
    action = top.get("action", {})
    ready = bool(action.get("registration_ready"))
    diagnostic_only = (not ready) and bool(action.get("reason_not_ready") or action.get("risk_tags"))
    priority = max(0.0, _num(top.get("priority")) or 0.0)
    score = 4.0
    score += 5.0 if ready else 2.0 if diagnostic_only else 0.0
    score += min(3.0, priority / 1.0 * 3.0)
    score += 3.0 if ready or diagnostic_only else 0.0
    return score


def _gate(name: str, passed: bool, cap: float, reason: str, tags: list[str]) -> ProjectGateResult:
    return ProjectGateResult(
        name=name,
        passed=bool(passed),
        cap=None if passed else cap,
        reason=reason,
        tags=tags,
    )


def _normalized_components(metrics: dict[str, Any], cfg: dict[str, Any]) -> dict[str, float | None]:
    targets = cfg.get("targets", {})
    scales = {
        "period": _num(targets.get("period_error_limit_pct")) or 3.0,
        "env": _num(targets.get("env_ok")) or 0.40,
        "ler": _num(targets.get("ler_ok")) or 0.50,
        "fp": _num(targets.get("fp_ok")) or 0.12,
        "zeta": 1.0,
    }
    components: dict[str, float | None] = {}
    for name, key in COMPONENT_KEYS.items():
        value = _num(metrics.get(key))
        components[name] = None if value is None else abs(value) / max(scales[name], 1e-12)
    return components


def _weighted_quality(components: dict[str, float | None]) -> tuple[float, dict[str, float | None]]:
    component_quality = {
        name: None if value is None else 100.0 / (1.0 + max(value, 0.0))
        for name, value in components.items()
    }
    available = {
        name: quality
        for name, quality in component_quality.items()
        if quality is not None and name in GLOBAL_SCORE_WEIGHTS
    }
    if not available:
        return 0.0, component_quality
    denom = sum(GLOBAL_SCORE_WEIGHTS[name] for name in available)
    quality = sum(GLOBAL_SCORE_WEIGHTS[name] * available[name] for name in available) / max(denom, 1e-12)
    return quality, component_quality


def _dominant_error(components: dict[str, float | None]) -> str:
    available = {name: value for name, value in components.items() if value is not None}
    if not available:
        return "missing"
    return max(available.items(), key=lambda item: item[1])[0]


def _missing_dof_row(dof: str) -> dict[str, Any]:
    return {
        "dof": dof,
        "closed": False,
        "objective_score": None,
        "quality": 0.0,
        "dominant_error": "missing",
        "component_normalized_error": {key: None for key in COMPONENT_KEYS},
        "component_quality": {key: None for key in COMPONENT_KEYS},
        "missing_core_components": list(CORE_COMPONENTS),
    }


def _hard_violations(best: dict[str, Any] | None) -> list[str]:
    if not best:
        return []
    objective_hard = list(best.get("objective", {}).get("hard_constraint_violations", []))
    step = best.get("step_decision") or best.get("verdict") or {}
    step_hard = list(step.get("hard_constraint_violations", []))
    return sorted({str(item) for item in [*objective_hard, *step_hard] if item})


def _card_is_diagnostic(card: dict[str, Any]) -> bool:
    if not card:
        return False
    loop = card.get("loop_verdict") or {}
    if loop.get("status") == "DIAGNOSTIC_FORK":
        return True
    return any(_is_diagnostic_violation(item) for item in _hard_violations(card))


def _is_diagnostic_violation(item: str) -> bool:
    return item.startswith(("strong_leak:", "equilibrium_out_of_bounds:", "period_reverse_worse:"))


def _final_validated(state: dict[str, Any], all_closed: bool, hard: list[str]) -> bool:
    if not all_closed or hard:
        return False
    loop = state.get("loop_verdict") or {}
    step = state.get("last_step_decision") or state.get("last_verdict") or {}
    return bool(
        state.get("mode") == "final_validate"
        and state.get("final_validation_candidate_id")
        and loop.get("status") == "CONVERGED"
        and step.get("status") in {"ACCEPTED", "EVIDENCE_ONLY"}
    )


def _artifact_retained(policy: Any) -> bool:
    text = str(policy or "")
    return text.startswith("retain_full") or text == "retain_full_final_validation"


def _best_complete_card(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    complete = [
        card for card in cards
        if len(card.get("objective", {}).get("per_dof", {})) == len(DOFS)
        and not card.get("objective", {}).get("hard_constraint_violations", [])
    ]
    if not complete:
        return None
    return min(complete, key=lambda card: float(card.get("objective", {}).get("global_score", math.inf)))


def _card_by_run_id(cards: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    for card in cards:
        if card.get("run_id") == run_id:
            return card
    return None


def _has_complete_core_metrics(card: dict[str, Any], cfg: dict[str, Any]) -> bool:
    per_dof = card.get("objective", {}).get("per_dof", {})
    if len(per_dof) != len(DOFS):
        return False
    for dof in DOFS:
        metrics = per_dof.get(dof)
        if not isinstance(metrics, dict):
            return False
        components = _normalized_components(metrics, cfg)
        if any(components.get(name) is None for name in CORE_COMPONENTS):
            return False
    return True


def _card_sort_key(card: dict[str, Any]) -> tuple[str, str]:
    return (str(card.get("created_at", "")), str(card.get("run_id", "")))


def _card_decision_status(card: dict[str, Any]) -> str:
    return str((card.get("step_decision") or card.get("verdict") or {}).get("status", ""))


def _plateau_count_by_scores(scores: list[float]) -> int:
    best = math.inf
    last_improvement_idx = -1
    for idx, score in enumerate(scores):
        if score < best - 1e-4:
            best = score
            last_improvement_idx = idx
    if last_improvement_idx < 0:
        return 0
    return max(0, len(scores) - 1 - last_improvement_idx)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _snapshot_id(oracle_id: str, computed_at: str) -> str:
    compact = (
        computed_at
        .replace("-", "")
        .replace(":", "")
        .replace("+", "Z")
        .replace(".", "")
    )
    return f"PS_{oracle_id}_{compact}"


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _round_score(value: float) -> float:
    return round(float(value), 3)


def _round_optional(value: float | None) -> float | None:
    return None if value is None else _round_score(value)


def _fmt(value: Any) -> str:
    numeric = _num(value)
    if numeric is None:
        return ""
    return f"{numeric:.3f}"
