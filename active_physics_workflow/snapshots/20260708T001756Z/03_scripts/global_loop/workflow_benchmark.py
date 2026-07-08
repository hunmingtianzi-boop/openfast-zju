from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from . import config, harness as harness_mod, memory, oracle
from .schema import DOFS, PARAMETER_GROUPS, WorkflowBenchmark, to_plain


BENCHMARK_VERSION = 3
ACTIVE_STAGES = {"global_step", "baseline_suite", "final_validate"}
HISTORICAL_STAGES = {"offline_import"}
ACTIVE_WINDOW = 20
MIN_ACTIVE_DECISIONS = 3


def build_benchmark(is_snapshot: bool = False) -> WorkflowBenchmark:
    cfg = config.load_config()
    path_error = _active_path_error(cfg)
    manifest = oracle.ensure_current_manifest(cfg)
    state = memory.read_global_state()
    cards = memory.load_run_cards()
    coupling = memory.read_coupling_memory()
    proposal_pool = memory.read_proposal_pool()
    harness_assessment = harness_mod.build_harness_assessment(window_limit=ACTIVE_WINDOW)
    computed_at = config.utc_now()

    active_cards = _active_decision_cards(cards)
    historical_cards = _historical_cards(cards)
    current_best = _best_complete_card(cards)
    all_closed = _all_closed(current_best)
    hard = _hard_violations(current_best)
    oracle_summary = _oracle_path_readiness(to_plain(manifest), path_error, state)
    evidence_inventory = _evidence_inventory(cards, current_best)
    active_summary = harness_mod.active_summary_for_benchmark(harness_assessment)
    historical_summary = _historical_evidence_summary(historical_cards)
    memory_diagnostics = _memory_diagnostics(coupling, cards)
    proposal_readiness = (
        _proposal_readiness(proposal_pool, all_closed)
        if all_closed
        else harness_mod.proposal_summary_for_benchmark(harness_assessment)
    )
    artifact_hygiene = _artifact_report_hygiene(to_plain(manifest), state)

    scheduler = _scheduler_recommendation(
        oracle_summary=oracle_summary,
        evidence_inventory=evidence_inventory,
        active_summary=active_summary,
        historical_summary=historical_summary,
        memory_diagnostics=memory_diagnostics,
        proposal_readiness=proposal_readiness,
        all_closed=all_closed,
        hard=hard,
    )
    if not (
        oracle_summary["score"] < 15.0
        or not evidence_inventory.get("current_best_complete")
        or all_closed
        or hard
    ):
        scheduler = harness_mod.scheduler_summary_for_benchmark(harness_assessment)
    benchmark_status = _benchmark_status(
        oracle_summary=oracle_summary,
        evidence_inventory=evidence_inventory,
        active_summary=active_summary,
        scheduler=scheduler,
    )

    active_score = active_summary.get("score")
    active_contribution = (
        float(active_score)
        if active_score is not None
        else 20.0 * float(active_summary.get("confidence", 0.0))
    )
    components = {
        "scores": {
            "oracle_path_readiness": _round_score(oracle_summary["score"]),
            "evidence_inventory": _round_score(evidence_inventory["score"]),
            "active_search_efficiency": _round_score(active_contribution),
            "historical_evidence_quality": _round_score(historical_summary["score"]),
            "coupling_memory_utility": _round_score(memory_diagnostics["score"]),
            "proposal_scheduler_readiness": _round_score(proposal_readiness["score"]),
            "artifact_report_hygiene": _round_score(artifact_hygiene["score"]),
        },
        "weights": {
            "oracle_path_readiness": 15,
            "evidence_inventory": 15,
            "active_search_efficiency": 20,
            "historical_evidence_quality": 10,
            "coupling_memory_utility": 20,
            "proposal_scheduler_readiness": 15,
            "artifact_report_hygiene": 5,
        },
        "active_stage_names": sorted(ACTIVE_STAGES),
        "historical_stage_names": sorted(HISTORICAL_STAGES),
        "artifact_report_hygiene": artifact_hygiene,
        "oracle_path_readiness": oracle_summary,
        "evidence_inventory": evidence_inventory,
        "harness": to_plain(harness_assessment),
    }
    score = sum(components["scores"].values())
    confidence = _benchmark_confidence(
        active_summary=active_summary,
        evidence_inventory=evidence_inventory,
        memory_diagnostics=memory_diagnostics,
    )

    return WorkflowBenchmark(
        created_at=computed_at,
        computed_at=computed_at,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        benchmark_version=BENCHMARK_VERSION,
        snapshot_id=_snapshot_id(manifest.oracle_id, computed_at) if is_snapshot else "",
        is_snapshot=is_snapshot,
        live_or_snapshot="snapshot" if is_snapshot else "live",
        source_state_updated_at=str(state.get("updated_at", "")),
        workflow_benchmark_score=_round_score(score),
        benchmark_confidence=_round_score(confidence),
        benchmark_status=benchmark_status,
        active_decision_summary=active_summary,
        historical_evidence_summary=historical_summary,
        memory_diagnostics=memory_diagnostics,
        proposal_readiness=proposal_readiness,
        scheduler_recommendation=scheduler,
        components=components,
    )


def write_benchmark(benchmark: WorkflowBenchmark) -> Path:
    oracle.ensure_epoch_dirs(benchmark.oracle_id)
    path = oracle.epoch_root(benchmark.oracle_id) / "workflow_benchmark.json"
    path.write_text(json.dumps(to_plain(benchmark), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_snapshot_benchmark(oracle_id: str | None = None) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    return _read_json(oracle.epoch_root(oid) / "workflow_benchmark.json")


def summary(benchmark: WorkflowBenchmark) -> dict[str, Any]:
    latest_snapshot = read_snapshot_benchmark(benchmark.oracle_id)
    is_snapshot = bool(benchmark.is_snapshot)
    return {
        "workflow_benchmark_score": benchmark.workflow_benchmark_score,
        "workflow_benchmark_mode": benchmark.live_or_snapshot,
        "workflow_benchmark_version": benchmark.benchmark_version,
        "workflow_benchmark_confidence": benchmark.benchmark_confidence,
        "workflow_benchmark_status": benchmark.benchmark_status,
        "live_workflow_benchmark_score": None if is_snapshot else benchmark.workflow_benchmark_score,
        "snapshot_workflow_benchmark_score": (
            benchmark.workflow_benchmark_score
            if is_snapshot
            else latest_snapshot.get("workflow_benchmark_score")
        ),
        "snapshot_workflow_benchmark_id": (
            benchmark.snapshot_id if is_snapshot else latest_snapshot.get("snapshot_id", "")
        ),
        "active_decision_summary": benchmark.active_decision_summary,
        "historical_evidence_summary": benchmark.historical_evidence_summary,
        "memory_diagnostics": benchmark.memory_diagnostics,
        "proposal_readiness": benchmark.proposal_readiness,
        "scheduler_recommendation": benchmark.scheduler_recommendation,
        "workflow_benchmark_components": benchmark.components,
    }


def report_lines(benchmark: WorkflowBenchmark) -> list[str]:
    data = to_plain(benchmark)
    active = data.get("active_decision_summary", {})
    historical = data.get("historical_evidence_summary", {})
    memory_diag = data.get("memory_diagnostics", {})
    proposal = data.get("proposal_readiness", {})
    scheduler = data.get("scheduler_recommendation", {})
    scores = data.get("components", {}).get("scores", {})
    return [
        "## 结论",
        "",
        f"- Benchmark 类型: `{data.get('live_or_snapshot', '')}`",
        f"- Snapshot id: `{data.get('snapshot_id', '') or 'not_snapshot'}`",
        f"- Workflow benchmark score: {data.get('workflow_benchmark_score')}",
        f"- Confidence: {data.get('benchmark_confidence')}",
        f"- Status: `{data.get('benchmark_status', '')}`",
        f"- 主问题: {scheduler.get('main_issue', '')}",
        f"- 调度建议: `{scheduler.get('recommendation', '')}`",
        f"- 建议原因: {scheduler.get('reason', '')}",
        "",
        "## 子分",
        "",
        "| Component | Score |",
        "|---|---:|",
        *[f"| `{key}` | {_fmt(value)} |" for key, value in scores.items()],
        "",
        "## Active Loop Decisions",
        "",
        f"- Active stages: `{','.join(data.get('components', {}).get('active_stage_names', []))}`",
        f"- Active decision count: {active.get('active_decision_count', 0)}",
        f"- Active status: `{active.get('status', '')}`",
        f"- Active score: {_fmt(active.get('score'))}",
        f"- Active confidence: {_fmt(active.get('confidence'))}",
        f"- Accepted: {active.get('accepted_count', 0)}",
        f"- Rejected: {active.get('rejected_count', 0)}",
        f"- Diagnostic: {active.get('diagnostic_count', 0)}",
        f"- Plateau count: {active.get('plateau_count', 0)}",
        "",
        "## Historical Evidence",
        "",
        f"- Historical cards: {historical.get('historical_card_count', 0)}",
        f"- Complete historical cards: {historical.get('complete_historical_card_count', 0)}",
        f"- Diagnostic rate: {_fmt(historical.get('diagnostic_rate'))}",
        f"- Decision counts: `{historical.get('decision_counts', {})}`",
        "",
        "## Memory Diagnostics",
        "",
        f"- Memory utility score: {_fmt(memory_diag.get('score'))}",
        f"- Group coverage: {_fmt(memory_diag.get('group_coverage'))}",
        f"- Average confidence: {_fmt(memory_diag.get('average_confidence'))}",
        f"- Consistency: {_fmt(memory_diag.get('consistency'))}",
        f"- Conflict count: {memory_diag.get('conflict_count', 0)}",
        f"- Empty group count: {memory_diag.get('empty_group_count', 0)}",
        f"- Ambiguous groups: `{','.join(memory_diag.get('ambiguous_groups', []))}`",
        "",
        "## Proposal Readiness",
        "",
        f"- Status: `{proposal.get('status', '')}`",
        f"- Score: {_fmt(proposal.get('score'))}",
        f"- Proposal count: {proposal.get('proposal_count', 0)}",
        f"- Top proposal: `{proposal.get('top_proposal_id', '')}`",
        f"- Reason: {proposal.get('reason', '')}",
    ]


def _oracle_path_readiness(manifest: dict[str, Any], path_error: str, state: dict[str, Any]) -> dict[str, Any]:
    refs = manifest.get("reference_files", {})
    sections = manifest.get("config_sections", {})
    heave_ref = refs.get("FD_HEAVE", {}).get("relative_path", "")
    checks = {
        "paths_clean": path_error == "",
        "six_references_present": len(refs) == len(DOFS) and all(dof in refs for dof in DOFS),
        "heave_recut_reference": heave_ref.endswith("FD_HEAVE_curated_processed.csv"),
        "config_sections_present": all(sections.get(key) for key in ("reference", "model", "targets", "physics")),
        "state_oracle_matches": not state.get("oracle_id") or state.get("oracle_id") == manifest.get("oracle_id"),
    }
    score = sum(checks.values()) / len(checks) * 15.0
    return {"score": score, "checks": checks, "path_error": path_error}


def _evidence_inventory(cards: list[dict[str, Any]], current_best: dict[str, Any] | None) -> dict[str, Any]:
    registry_files = [
        config.registry_path("candidate_registry.csv"),
        config.registry_path("run_registry.csv"),
        config.registry_path("results_registry.csv"),
    ]
    stage_counts = Counter(str(card.get("stage", "")) for card in cards)
    score = 0.0
    score += min(4.0, len(cards) / 30.0 * 4.0)
    score += 5.0 if current_best and len(current_best.get("objective", {}).get("per_dof", {})) == len(DOFS) else 0.0
    score += sum(1.0 for path in registry_files if path.exists()) / len(registry_files) * 4.0
    score += 2.0 if current_best and current_best.get("evidence_sources") else 0.0
    return {
        "score": score,
        "run_cards_total": len(cards),
        "stage_counts": dict(stage_counts),
        "current_best_complete": bool(current_best and len(current_best.get("objective", {}).get("per_dof", {})) == len(DOFS)),
        "registry_files_present": [path.name for path in registry_files if path.exists()],
    }


def _active_decision_summary(active_cards: list[dict[str, Any]]) -> dict[str, Any]:
    recent = sorted(active_cards, key=_card_sort_key)[-ACTIVE_WINDOW:]
    n = len(recent)
    status_counts = Counter(_card_decision_status(card) or "UNKNOWN" for card in recent)
    diagnostic_count = sum(1 for card in recent if _card_is_diagnostic(card))
    accepted_count = status_counts.get("ACCEPTED", 0)
    rejected_count = status_counts.get("REJECTED", 0)
    decision_count = accepted_count + rejected_count
    scores = [_num(card.get("objective", {}).get("global_score")) for card in recent]
    scores = [score for score in scores if score is not None]
    confidence = min(1.0, n / MIN_ACTIVE_DECISIONS)

    if n < MIN_ACTIVE_DECISIONS:
        return {
            "status": "INSUFFICIENT_ACTIVE_DATA",
            "score": None,
            "confidence": _round_score(confidence),
            "active_decision_count": n,
            "window_limit": ACTIVE_WINDOW,
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "evidence_only_count": status_counts.get("EVIDENCE_ONLY", 0),
            "diagnostic_count": diagnostic_count,
            "acceptance_rate": 0.0,
            "rejection_rate": 0.0,
            "diagnostic_rate": _round_score(diagnostic_count / max(n, 1)),
            "plateau_count": 0,
            "best_score_improvement_ratio": 0.0,
            "reason": f"active decision cards {n}/{MIN_ACTIVE_DECISIONS}; do not score active search efficiency from offline imports",
        }

    acceptance_rate = accepted_count / max(decision_count, 1)
    rejection_rate = rejected_count / max(decision_count, 1)
    diagnostic_rate = diagnostic_count / max(n, 1)
    plateau_count = _plateau_count_by_scores(scores)
    plateau_rate = plateau_count / max(len(scores) - 1, 1)
    improvement_ratio = 0.0
    if len(scores) >= 2:
        start_best = scores[0]
        end_best = min(scores)
        improvement_ratio = max(0.0, start_best - end_best) / max(abs(start_best), 1e-12)

    coverage_score = min(4.0, n / ACTIVE_WINDOW * 4.0)
    improvement_score = min(5.0, improvement_ratio / 0.10 * 5.0)
    acceptance_score = min(4.0, acceptance_rate / 0.30 * 4.0)
    diagnostic_score = max(0.0, 4.0 * (1.0 - diagnostic_rate * 2.0))
    plateau_score = max(0.0, 3.0 * (1.0 - plateau_rate))
    total = coverage_score + improvement_score + acceptance_score + diagnostic_score + plateau_score
    status = "ACTIVE_SEARCH_HEALTHY"
    if diagnostic_rate >= 0.50:
        status = "ACTIVE_DIAGNOSTIC_HEAVY"
    elif plateau_rate >= 0.50 and rejection_rate >= 0.50:
        status = "ACTIVE_PLATEAU"
    return {
        "status": status,
        "score": _round_score(min(20.0, total)),
        "confidence": 1.0,
        "active_decision_count": n,
        "window_limit": ACTIVE_WINDOW,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "evidence_only_count": status_counts.get("EVIDENCE_ONLY", 0),
        "diagnostic_count": diagnostic_count,
        "acceptance_rate": _round_score(acceptance_rate),
        "rejection_rate": _round_score(rejection_rate),
        "diagnostic_rate": _round_score(diagnostic_rate),
        "plateau_count": plateau_count,
        "plateau_rate": _round_score(plateau_rate),
        "best_score_improvement_ratio": _round_score(improvement_ratio),
        "reason": "active loop decision sample is sufficient",
    }


def _historical_evidence_summary(historical_cards: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(historical_cards)
    complete = [card for card in historical_cards if len(card.get("objective", {}).get("per_dof", {})) == len(DOFS)]
    diagnostic_count = sum(1 for card in historical_cards if _card_is_diagnostic(card))
    decision_counts = Counter(_card_decision_status(card) or "UNKNOWN" for card in historical_cards)
    diagnostic_rate = diagnostic_count / max(n, 1)
    coverage_score = min(4.0, n / 50.0 * 4.0)
    completeness_score = min(3.0, len(complete) / max(n, 1) * 3.0)
    cleanliness_score = max(0.0, 3.0 * (1.0 - diagnostic_rate))
    return {
        "score": _round_score(coverage_score + completeness_score + cleanliness_score),
        "historical_card_count": n,
        "complete_historical_card_count": len(complete),
        "diagnostic_count": diagnostic_count,
        "diagnostic_rate": _round_score(diagnostic_rate),
        "decision_counts": dict(decision_counts),
        "note": "historical offline imports inform evidence and memory, not active loop search efficiency",
    }


def _memory_diagnostics(coupling: dict[str, Any], cards: list[dict[str, Any]]) -> dict[str, Any]:
    entries = coupling.get("entries", {}) if isinstance(coupling, dict) else {}
    if not entries:
        return {
            "score": 0.0,
            "group_coverage": 0.0,
            "average_confidence": 0.0,
            "consistency": 0.0,
            "diagnostic_cleanliness": 0.0,
            "conflict_count": 0,
            "actionable_group_count": 0,
            "empty_group_count": len(PARAMETER_GROUPS),
            "ambiguous_groups": [],
            "groups": {},
        }

    cards_by_id = {card.get("run_id", ""): card for card in cards}
    group_coverage = sum(1 for group in PARAMETER_GROUPS if group in entries) / len(PARAMETER_GROUPS)
    confidences = [_num(entry.get("confidence")) or 0.0 for entry in entries.values()]
    average_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    conflict_count = 0
    affected_pair_count = 0
    evidence_ids: set[str] = set()
    diagnostic_ids: set[str] = set()
    actionable_groups: list[str] = []
    empty_groups: list[str] = []
    ambiguous_groups: list[str] = []
    details: dict[str, Any] = {}

    for group in PARAMETER_GROUPS:
        entry = entries.get(group, {})
        improves = set(entry.get("improves", []))
        damages = set(entry.get("damages", []))
        affected = set(entry.get("affected_dofs", [])) | improves | damages
        conflicts = sorted(improves & damages)
        evidence = [run_id for run_id in entry.get("evidence_run_ids", []) if run_id]
        diag = [run_id for run_id in evidence if _card_is_diagnostic(cards_by_id.get(run_id, {}))]
        evidence_ids.update(evidence)
        diagnostic_ids.update(diag)
        conflict_count += len(conflicts)
        affected_pair_count += len(affected)
        if not evidence and not affected:
            empty_groups.append(group)
        if conflicts:
            ambiguous_groups.append(group)
        if evidence and affected and not conflicts:
            actionable_groups.append(group)
        details[group] = {
            "confidence": _round_optional(_num(entry.get("confidence"))),
            "affected_dofs": sorted(affected),
            "improves": sorted(improves),
            "damages": sorted(damages),
            "conflicts": conflicts,
            "evidence_count": len(evidence),
            "diagnostic_evidence_count": len(diag),
            "is_ambiguous": bool(conflicts),
            "is_empty": group in empty_groups,
            "is_actionable": group in actionable_groups,
        }

    consistency = 1.0 - conflict_count / max(affected_pair_count, 1)
    diagnostic_rate = len(diagnostic_ids) / max(len(evidence_ids), 1)
    diagnostic_cleanliness = 1.0 - diagnostic_rate
    actionability = len(actionable_groups) / len(PARAMETER_GROUPS)
    empty_penalty = len(empty_groups) / len(PARAMETER_GROUPS)
    score = (
        3.0
        + 3.5 * group_coverage
        + 3.5 * average_confidence
        + 4.0 * max(0.0, consistency)
        + 3.0 * max(0.0, diagnostic_cleanliness)
        + 3.0 * actionability
        - 2.0 * empty_penalty
    )
    return {
        "score": _round_score(max(0.0, min(20.0, score))),
        "group_coverage": _round_score(group_coverage),
        "average_confidence": _round_score(average_confidence),
        "consistency": _round_score(max(0.0, consistency)),
        "diagnostic_cleanliness": _round_score(max(0.0, diagnostic_cleanliness)),
        "diagnostic_evidence_rate": _round_score(diagnostic_rate),
        "conflict_count": conflict_count,
        "affected_pair_count": affected_pair_count,
        "evidence_run_count": len(evidence_ids),
        "diagnostic_evidence_count": len(diagnostic_ids),
        "actionable_group_count": len(actionable_groups),
        "empty_group_count": len(empty_groups),
        "ambiguous_groups": ambiguous_groups,
        "empty_groups": empty_groups,
        "actionable_groups": actionable_groups,
        "groups": details,
    }


def _proposal_readiness(proposal_pool: dict[str, Any], all_closed: bool) -> dict[str, Any]:
    if all_closed:
        return {
            "status": "FINAL_VALIDATE_READY",
            "score": 15.0,
            "proposal_count": 0,
            "top_proposal_id": "",
            "materializable_proposal_ids": [],
            "reason": "all DOFs are closed; final validation should run",
        }
    proposals = proposal_pool.get("proposals", []) if isinstance(proposal_pool, dict) else []
    if not proposals:
        return {
            "status": "NO_PROPOSAL",
            "score": 0.0,
            "proposal_count": 0,
            "top_proposal_id": "",
            "materializable_proposal_ids": [],
            "reason": "no proposal pool is available",
        }
    top = proposals[0]
    action = top.get("action", {})
    materializable = [
        proposal.get("proposal_id", "")
        for proposal in proposals
        if proposal.get("action", {}).get("registration_ready")
    ]
    if action.get("registration_ready"):
        status = "MATERIALIZABLE"
        score = 15.0
        reason = "top proposal can be materialized and then validated with full 6DOF"
    else:
        status = "DIAGNOSTIC_ONLY"
        score = 10.0 if materializable else 8.0
        reason = action.get("reason_not_ready") or "top proposal requires diagnostic review"
    return {
        "status": status,
        "score": score,
        "proposal_count": len(proposals),
        "top_proposal_id": top.get("proposal_id", ""),
        "top_target_dof": top.get("target_dof", ""),
        "top_parameter_group": action.get("parameter_group", ""),
        "top_registration_ready": bool(action.get("registration_ready")),
        "materializable_proposal_ids": materializable,
        "reason": reason,
    }


def _artifact_report_hygiene(manifest: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    reports_dir = oracle.epoch_reports_dir(manifest.get("oracle_id", ""))
    reports = sorted(reports_dir.glob("*.md")) if reports_dir.exists() else []
    names = {path.name for path in reports}
    checks = {
        "reports_dir_exists": reports_dir.exists(),
        "enough_reports": len(names) >= 4,
        "workflow_or_project_report_present": "workflow_benchmark_report.md" in names or "project_score_report.md" in names,
        "oracle_or_import_report_present": "oracle_status_report.md" in names or "offline_import_report.md" in names,
        "state_oracle_matches": not state.get("oracle_id") or state.get("oracle_id") == manifest.get("oracle_id"),
    }
    score = sum(checks.values()) / len(checks) * 5.0
    return {"score": score, "report_count": len(names), "checks": checks}


def _scheduler_recommendation(
    *,
    oracle_summary: dict[str, Any],
    evidence_inventory: dict[str, Any],
    active_summary: dict[str, Any],
    historical_summary: dict[str, Any],
    memory_diagnostics: dict[str, Any],
    proposal_readiness: dict[str, Any],
    all_closed: bool,
    hard: list[str],
) -> dict[str, Any]:
    if oracle_summary["score"] < 15.0:
        return _recommend("BLOCKED_FIX_WORKFLOW", "oracle/path readiness failed", "fix oracle/path/truth readiness before more loop work")
    if not evidence_inventory.get("current_best_complete"):
        return _recommend("RUN_BASELINE_OR_IMPORT", "no complete current-best 6DOF evidence", "run baseline suite or offline import")
    if all_closed:
        return _recommend("FINAL_VALIDATE", "all six DOFs are closed", "run final validation with retained artifacts")
    if hard:
        return _recommend("RUN_DIAGNOSTIC_REVIEW", "current best has hard constraint evidence", "; ".join(hard))

    proposal_status = proposal_readiness.get("status")
    active_status = active_summary.get("status")
    if proposal_status == "STALE_PROPOSAL_POOL":
        return _recommend("REFRESH_LOCAL_PROPOSALS", "proposal pool is stale", proposal_readiness.get("reason", ""))
    if proposal_status == "DIAGNOSTIC_ONLY":
        return _recommend("RUN_DIAGNOSTIC_REVIEW", "top proposal is diagnostic-only", proposal_readiness.get("reason", ""))
    if proposal_status == "MATERIALIZABLE":
        return _recommend("MATERIALIZE_AND_VALIDATE", "top proposal is materializable", proposal_readiness.get("reason", ""))
    if active_status == "INSUFFICIENT_ACTIVE_DATA":
        if memory_diagnostics.get("conflict_count", 0) or historical_summary.get("diagnostic_rate", 0.0) >= 0.25:
            return _recommend(
                "RUN_DIAGNOSTIC_REVIEW",
                "active loop samples are insufficient and historical evidence shows diagnostic/memory ambiguity",
                "review coupling diagnostics before treating historical rejected imports as active loop failure",
            )
        return _recommend(
            "RUN_ACTIVE_VALIDATION_BATCH",
            "active loop samples are insufficient",
            "validate a small active candidate batch before judging loop efficiency",
        )
    if active_summary.get("diagnostic_rate", 0.0) >= 0.50:
        return _recommend("RUN_DIAGNOSTIC_REVIEW", "active diagnostic rate is high", "diagnostic forks dominate active decisions")
    if active_status == "ACTIVE_PLATEAU":
        return _recommend("SWITCH_SEARCH_STRATEGY", "active search plateau detected", "rejected decisions dominate without improvement")
    if proposal_status == "NO_PROPOSAL":
        return _recommend("RUN_ACTIVE_VALIDATION_BATCH", "no proposal pool is available", "generate proposals or validate a small active batch")
    return _recommend("RUN_ACTIVE_VALIDATION_BATCH", "workflow benchmark needs more active evidence", "continue active validation with full 6DOF arbitration")


def _benchmark_status(
    *,
    oracle_summary: dict[str, Any],
    evidence_inventory: dict[str, Any],
    active_summary: dict[str, Any],
    scheduler: dict[str, Any],
) -> str:
    if oracle_summary["score"] < 15.0 or not evidence_inventory.get("current_best_complete"):
        return "BLOCKED"
    if active_summary.get("status") == "INSUFFICIENT_ACTIVE_DATA":
        return "NEEDS_ACTIVE_DECISION_DATA"
    if scheduler.get("recommendation") == "RUN_DIAGNOSTIC_REVIEW":
        return "DIAGNOSTIC_REVIEW_REQUIRED"
    if scheduler.get("recommendation") == "SWITCH_SEARCH_STRATEGY":
        return "SEARCH_PLATEAU"
    if scheduler.get("recommendation") == "FINAL_VALIDATE":
        return "FINAL_VALIDATE_READY"
    if scheduler.get("recommendation") == "REFRESH_LOCAL_PROPOSALS":
        return "PROPOSAL_REFRESH_REQUIRED"
    return "RUNNING"


def _benchmark_confidence(
    *,
    active_summary: dict[str, Any],
    evidence_inventory: dict[str, Any],
    memory_diagnostics: dict[str, Any],
) -> float:
    active_conf = float(active_summary.get("confidence", 0.0))
    evidence_conf = min(1.0, float(evidence_inventory.get("run_cards_total", 0)) / 30.0)
    memory_conf = float(memory_diagnostics.get("average_confidence", 0.0))
    return max(0.0, min(1.0, 0.40 * active_conf + 0.30 * evidence_conf + 0.30 * memory_conf))


def _active_decision_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [card for card in cards if card.get("stage") in ACTIVE_STAGES]


def _historical_cards(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [card for card in cards if card.get("stage") in HISTORICAL_STAGES]


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


def _best_complete_card(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    complete = [
        card for card in cards
        if len(card.get("objective", {}).get("per_dof", {})) == len(DOFS)
        and not card.get("objective", {}).get("hard_constraint_violations", [])
    ]
    if not complete:
        return None
    return min(complete, key=lambda card: float(card.get("objective", {}).get("global_score", math.inf)))


def _all_closed(card: dict[str, Any] | None) -> bool:
    if not card:
        return False
    per = card.get("objective", {}).get("per_dof", {})
    return len(per) == len(DOFS) and all(metrics.get("closed") for metrics in per.values())


def _hard_violations(card: dict[str, Any] | None) -> list[str]:
    if not card:
        return []
    objective_hard = list(card.get("objective", {}).get("hard_constraint_violations", []))
    step = card.get("step_decision") or card.get("verdict") or {}
    step_hard = list(step.get("hard_constraint_violations", []))
    return sorted({str(item) for item in [*objective_hard, *step_hard] if item})


def _card_is_diagnostic(card: dict[str, Any]) -> bool:
    if not card:
        return False
    loop = card.get("loop_verdict") or {}
    if loop.get("status") == "DIAGNOSTIC_FORK":
        return True
    return any(item.startswith(("strong_leak:", "equilibrium_out_of_bounds:", "period_reverse_worse:")) for item in _hard_violations(card))


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
    return 0 if last_improvement_idx < 0 else max(0, len(scores) - 1 - last_improvement_idx)


def _recommend(recommendation: str, main_issue: str, reason: str) -> dict[str, str]:
    return {"recommendation": recommendation, "main_issue": main_issue, "reason": reason}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _snapshot_id(oracle_id: str, computed_at: str) -> str:
    compact = computed_at.replace("-", "").replace(":", "").replace("+", "Z").replace(".", "")
    return f"WB_{oracle_id}_{compact}"


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
