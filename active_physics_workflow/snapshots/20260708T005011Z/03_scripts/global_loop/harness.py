from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import config, memory, oracle
from .schema import DOFS, CampaignState, HarnessAssessment, HarnessAttempt, to_plain


ACTIVE_STAGES = {"global_step", "baseline_suite", "final_validate"}
HISTORICAL_STAGES = {"offline_import"}
WINDOW_LIMIT = 20
MIN_ACTIVE_ATTEMPTS = 3
HARNESS_VERSION = 1


def build_harness_assessment(window_limit: int = WINDOW_LIMIT) -> HarnessAssessment:
    manifest = oracle.ensure_current_manifest()
    cards = memory.load_run_cards()
    transitions = memory.read_transition_cards()
    relation = memory.read_relation_memory()
    proposal_history = memory.read_proposal_history()
    proposal_pool = memory.read_proposal_pool()
    created_at = config.utc_now()

    active_cards = _recent_active_cards(cards, window_limit)
    transitions_by_child = {str(item.get("child_run_id", "")): item for item in transitions}
    relation_run_ids = _relation_run_ids(relation)
    proposal_history_by_id = _proposal_history_by_id(proposal_history)
    current_best_run_id = _current_best_run_id(cards)
    seen_failures: Counter[str] = Counter()
    attempts: list[HarnessAttempt] = []
    for card in active_cards:
        attempt = _attempt_from_card(
            card,
            manifest.oracle_id,
            transitions_by_child.get(str(card.get("run_id", "")), {}),
            relation_run_ids,
            proposal_history_by_id,
            seen_failures,
        )
        attempts.append(attempt)
        if attempt.failure_signature != "clean":
            seen_failures[attempt.failure_signature] += 1

    campaigns = _campaign_states(attempts)
    proposal_summary = _proposal_summary(proposal_pool, current_best_run_id=current_best_run_id)
    repeated_failures = [
        {"signature": signature, "count": count}
        for signature, count in seen_failures.most_common()
        if signature != "clean" and count > 1
    ]
    active_summary = _active_summary(attempts, proposal_summary, repeated_failures, window_limit)
    recommendation, stop_auto, reason = _recommendation(active_summary, proposal_summary, campaigns)
    dominant_campaign_id = _dominant_campaign(campaigns)

    return HarnessAssessment(
        created_at=created_at,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        status=str(active_summary.get("status", "")),
        recommendation=recommendation,
        active_attempt_count=len(attempts),
        valid_attempt_count=sum(1 for attempt in attempts if attempt.valid),
        window_limit=window_limit,
        mean_information_gain=float(active_summary.get("mean_information_gain", 0.0)),
        stop_auto_materialize=stop_auto,
        dominant_campaign_id=dominant_campaign_id,
        attempts=attempts,
        campaigns=campaigns,
        active_summary=active_summary,
        proposal_summary=proposal_summary,
        repeated_failure_signatures=repeated_failures,
        reason=reason,
        source_roles={
            "run_cards": "observation_evidence",
            "transitions": "derived_memory",
            "relation_memory": "derived_memory",
            "proposal_history": "process_memory",
            "proposal_pool": "process_memory",
            "harness_assessment": "process_memory",
        },
    )


def write_assessment_outputs(assessment: HarnessAssessment) -> dict[str, Path]:
    status_path = memory.write_harness_status(assessment)
    campaign_path = memory.write_campaign_memory(assessment.campaigns)
    report_path = memory.write_report(
        "harness_report.md",
        "Calibration Harness",
        report_lines(assessment),
    )
    return {"status": status_path, "campaign_memory": campaign_path, "report": report_path}


def summary(assessment: HarnessAssessment | dict[str, Any]) -> dict[str, Any]:
    data = to_plain(assessment)
    return {
        "harness_status": data.get("status", ""),
        "harness_recommendation": data.get("recommendation", ""),
        "mean_information_gain": data.get("mean_information_gain", 0.0),
        "active_attempt_count": data.get("active_attempt_count", 0),
        "valid_active_attempt_count": data.get("valid_attempt_count", 0),
        "dominant_campaign_id": data.get("dominant_campaign_id", ""),
        "stop_auto_materialize": bool(data.get("stop_auto_materialize", False)),
        "harness_reason": data.get("reason", ""),
        "harness_active_summary": data.get("active_summary", {}),
        "harness_proposal_summary": data.get("proposal_summary", {}),
    }


def active_summary_for_benchmark(assessment: HarnessAssessment | dict[str, Any]) -> dict[str, Any]:
    data = to_plain(assessment)
    active = dict(data.get("active_summary", {}) or {})
    if "active_decision_count" not in active:
        active["active_decision_count"] = data.get("valid_attempt_count", 0)
    if "confidence" not in active:
        active["confidence"] = min(1.0, float(active.get("active_decision_count", 0)) / MIN_ACTIVE_ATTEMPTS)
    return active


def proposal_summary_for_benchmark(assessment: HarnessAssessment | dict[str, Any]) -> dict[str, Any]:
    data = to_plain(assessment)
    return dict(data.get("proposal_summary", {}) or {})


def scheduler_summary_for_benchmark(assessment: HarnessAssessment | dict[str, Any]) -> dict[str, Any]:
    data = to_plain(assessment)
    recommendation = str(data.get("recommendation", ""))
    mapped = {
        "CONTINUE_CURRENT_CAMPAIGN": "RUN_ACTIVE_VALIDATION_BATCH",
        "LIMITED_PROBE": "RUN_LIMITED_PROBE",
        "SWITCH_PARAMETER_GROUP": "SWITCH_SEARCH_STRATEGY",
        "ENTER_DIAGNOSTIC_REVIEW": "RUN_DIAGNOSTIC_REVIEW",
        "STOP_AUTO_MATERIALIZE": "RUN_DIAGNOSTIC_REVIEW",
        "REFRESH_LOCAL_PROPOSALS": "REFRESH_LOCAL_PROPOSALS",
        "RUN_BASELINE_EVIDENCE": "RUN_ACTIVE_VALIDATION_BATCH",
        "REBUILD_MEMORY": "REBUILD_MEMORY",
    }.get(recommendation, recommendation or "RUN_ACTIVE_VALIDATION_BATCH")
    return {
        "recommendation": mapped,
        "main_issue": recommendation or "HARNESS_UNAVAILABLE",
        "reason": str(data.get("reason", "")),
        "harness_recommendation": recommendation,
    }


def campaign_id_for(parameter_group: str, target_dof: str, action_type: str, dominant_metric: str) -> str:
    parts = [
        _safe_token(parameter_group or "unknown"),
        _safe_token(target_dof or "GLOBAL"),
        _safe_token(action_type or "unknown"),
        _safe_token(dominant_metric or "mixed"),
    ]
    readable = "_".join(parts)
    digest = hashlib.sha1(readable.encode("utf-8")).hexdigest()[:8].upper()
    return f"CAMP_{readable}_{digest}"


def report_lines(assessment: HarnessAssessment | dict[str, Any]) -> list[str]:
    data = to_plain(assessment)
    active = data.get("active_summary", {})
    proposal = data.get("proposal_summary", {})
    repeated = data.get("repeated_failure_signatures", [])
    campaigns = data.get("campaigns", {})
    lines = [
        "## Summary",
        "",
        f"- Oracle: `{data.get('oracle_id', '')}`",
        f"- Window status: `{data.get('status', '')}`",
        f"- Supervisor recommendation: `{data.get('recommendation', '')}`",
        f"- Stop auto materialize: `{data.get('stop_auto_materialize')}`",
        f"- Reason: {data.get('reason', '')}",
        "",
        "## Active Attempts",
        "",
        f"- Active attempts in window: {data.get('active_attempt_count', 0)}",
        f"- Valid active attempts: {data.get('valid_attempt_count', 0)}",
        f"- Window limit: {data.get('window_limit', 0)}",
        f"- Accepted: {active.get('accepted_count', 0)}",
        f"- Rejected: {active.get('rejected_count', 0)}",
        f"- Evidence only: {active.get('evidence_only_count', 0)}",
        f"- Diagnostic: {active.get('diagnostic_count', 0)}",
        f"- Plateau count: {active.get('plateau_count', 0)}",
        f"- Mean information gain: {_fmt(data.get('mean_information_gain'))}",
        "",
        "## Proposal State",
        "",
        f"- Proposal status: `{proposal.get('status', '')}`",
        f"- Proposal count: {proposal.get('proposal_count', 0)}",
        f"- Top proposal: `{proposal.get('top_proposal_id', '')}`",
        f"- Materializable proposals: `{','.join(proposal.get('materializable_proposal_ids', []))}`",
        f"- Reason: {proposal.get('reason', '')}",
    ]
    if active.get("status") == "INSUFFICIENT_ACTIVE_DATA" and proposal.get("status") == "DIAGNOSTIC_ONLY":
        lines.extend([
            "",
            "> active data insufficient for trend score, but current proposal pool is diagnostic-only; stop auto materialize.",
        ])
    lines.extend([
        "",
        "## Repeated Failure Signatures",
        "",
        "| Signature | Count |",
        "|---|---:|",
    ])
    if repeated:
        lines.extend(f"| `{item.get('signature', '')}` | {item.get('count', 0)} |" for item in repeated)
    else:
        lines.append("| none | 0 |")
    lines.extend([
        "",
        "## Campaigns",
        "",
        "| Campaign | Status | Confidence | Attempts | Support | Refute | Hypothesis |",
        "|---|---|---:|---:|---:|---:|---|",
    ])
    if campaigns:
        for campaign in sorted(campaigns.values(), key=lambda item: (-len(item.get("attempt_run_ids", [])), item.get("campaign_id", ""))):
            lines.append(
                f"| `{campaign.get('campaign_id', '')}` | `{campaign.get('status', '')}` | "
                f"{_fmt(campaign.get('confidence'))} | {len(campaign.get('attempt_run_ids', []))} | "
                f"{len(campaign.get('supporting_run_ids', []))} | {len(campaign.get('refuting_run_ids', []))} | "
                f"{campaign.get('hypothesis', '')} |"
            )
    else:
        lines.append("| none | `n/a` | 0.000 | 0 | 0 | 0 | no active campaign evidence |")
    return lines


def _recent_active_cards(cards: list[dict[str, Any]], window_limit: int) -> list[dict[str, Any]]:
    active = [card for card in cards if str(card.get("stage", "")) in ACTIVE_STAGES]
    return sorted(active, key=_card_sort_key)[-max(1, int(window_limit)):]


def _attempt_from_card(
    card: dict[str, Any],
    oracle_id: str,
    transition: dict[str, Any],
    relation_run_ids: set[str],
    proposal_history_by_id: dict[str, dict[str, Any]],
    seen_failures: Counter[str],
) -> HarnessAttempt:
    run_id = str(card.get("run_id", ""))
    meta = _decision_meta(card)
    target_dof = _target_dof(card, transition, meta)
    action_type = _action_type(card, transition, meta)
    parameter_group = str(card.get("parameter_group") or transition.get("parameter_group") or "unknown")
    dominant_metric = _dominant_metric(card, target_dof)
    hypothesis = _hypothesis(card, meta, proposal_history_by_id, parameter_group, target_dof, action_type, dominant_metric)
    campaign_id = campaign_id_for(parameter_group, target_dof, action_type, dominant_metric)
    failure_signature = _failure_signature(card)
    complete = _complete_6dof(card)
    score = _num(card.get("objective", {}).get("global_score"))
    valid, validity_reason = _validity(card, oracle_id, complete, score)
    new_failures = [str(item) for item in transition.get("new_failures", []) if item]
    resolved_failures = [str(item) for item in transition.get("resolved_failures", []) if item]
    step = str((card.get("step_decision") or card.get("verdict") or {}).get("status", ""))
    loop = str((card.get("loop_verdict") or {}).get("status", ""))
    fit_gain = _fit_gain(transition)
    information_gain, tags = _information_gain(
        valid=valid,
        complete=complete,
        fit_gain=fit_gain,
        run_id=run_id,
        relation_run_ids=relation_run_ids,
        failure_signature=failure_signature,
        seen_failure_count=seen_failures.get(failure_signature, 0),
        new_failures=new_failures,
        resolved_failures=resolved_failures,
        step_decision=step,
        loop_verdict=loop,
        campaign_id=campaign_id,
    )
    return HarnessAttempt(
        run_id=run_id,
        candidate_id=str(card.get("observation", {}).get("candidate_id", "")),
        stage=str(card.get("stage", "")),
        oracle_id=str(card.get("oracle_id", "")),
        epoch_id=str(card.get("epoch_id", "")),
        campaign_id=campaign_id,
        hypothesis=hypothesis,
        valid=valid,
        validity_reason=validity_reason,
        complete_6dof=complete,
        step_decision=step or "UNKNOWN",
        loop_verdict=loop or "UNKNOWN",
        global_score=score,
        fit_gain=fit_gain,
        information_gain=information_gain,
        failure_signature=failure_signature,
        parameter_group=parameter_group,
        target_dof=target_dof,
        action_type=action_type,
        dominant_metric=dominant_metric,
        new_failures=new_failures,
        resolved_failures=resolved_failures,
        tags=tags,
        created_at=str(card.get("created_at", "")),
    )


def _validity(card: dict[str, Any], oracle_id: str, complete: bool, score: float | None) -> tuple[bool, str]:
    if str(card.get("oracle_id", "")) not in {"", oracle_id}:
        return False, "oracle mismatch"
    if not complete:
        return False, "incomplete 6DOF metrics"
    if score is None:
        return False, "missing global score"
    if _is_dry_run(card):
        return False, "dry-run evidence is not an active attempt"
    if str(card.get("stage", "")) not in ACTIVE_STAGES:
        return False, "stage is not active loop evidence"
    return True, "valid active full-6DOF attempt"


def _information_gain(
    *,
    valid: bool,
    complete: bool,
    fit_gain: float | None,
    run_id: str,
    relation_run_ids: set[str],
    failure_signature: str,
    seen_failure_count: int,
    new_failures: list[str],
    resolved_failures: list[str],
    step_decision: str,
    loop_verdict: str,
    campaign_id: str,
) -> tuple[float, list[str]]:
    score = 0.0
    tags: list[str] = []
    if valid and complete:
        score += 0.35
        tags.append("full_6dof_complete")
    if fit_gain is not None and abs(fit_gain) > 1e-4:
        score += 0.20
        tags.append("residual_vector_changed")
    if run_id in relation_run_ids:
        score += 0.15
        tags.append("relation_memory_updated")
    if failure_signature != "clean" and seen_failure_count == 0:
        score += 0.20
        tags.append("new_failure_signature")
    if new_failures or resolved_failures:
        score += 0.20
        tags.append("failure_set_changed")
    if step_decision in {"ACCEPTED", "REJECTED"} and campaign_id:
        score += 0.15
        tags.append("hypothesis_confirmed_or_refuted")
    if failure_signature != "clean" and seen_failure_count > 0:
        score -= 0.25
        tags.append("repeated_same_failure")
    if loop_verdict == "DIAGNOSTIC_FORK" and failure_signature != "clean" and seen_failure_count > 0:
        score -= 0.20
        tags.append("diagnostic_without_new_signature")
    if not valid:
        score -= 0.40
        tags.append("incomplete_or_unusable_evidence")
    return round(max(-0.50, min(1.30, score)), 3), tags


def _campaign_states(attempts: list[HarnessAttempt]) -> dict[str, CampaignState]:
    grouped: dict[str, list[HarnessAttempt]] = defaultdict(list)
    for attempt in attempts:
        if attempt.valid:
            grouped[attempt.campaign_id].append(attempt)
    campaigns: dict[str, CampaignState] = {}
    for campaign_id, items in grouped.items():
        support = [
            item.run_id for item in items
            if item.step_decision == "ACCEPTED" or (item.fit_gain is not None and item.fit_gain > 1e-4 and item.step_decision != "REJECTED")
        ]
        refute = [
            item.run_id for item in items
            if item.step_decision == "REJECTED" or "repeated_same_failure" in item.tags
        ]
        diagnostic = [item for item in items if item.loop_verdict == "DIAGNOSTIC_FORK"]
        repeated = [
            sig for sig, count in Counter(item.failure_signature for item in items if item.failure_signature != "clean").items()
            if count > 1
        ]
        status = _campaign_status(items, support, refute, diagnostic, repeated)
        inferred = sum(1 for item in items if item.hypothesis.startswith("inferred:"))
        confidence = min(1.0, len(items) / 5.0) * (1.0 - 0.25 * inferred / max(len(items), 1))
        first = items[0]
        campaigns[campaign_id] = CampaignState(
            campaign_id=campaign_id,
            hypothesis=first.hypothesis,
            parameter_group=first.parameter_group,
            target_dof=first.target_dof,
            action_type=first.action_type,
            dominant_metric=first.dominant_metric,
            status=status,
            confidence=round(confidence, 3),
            attempt_run_ids=[item.run_id for item in items],
            supporting_run_ids=support,
            refuting_run_ids=refute,
            repeated_failure_signatures=sorted(repeated),
            recommendation=_campaign_recommendation(status),
            notes=[f"derived from {len(items)} valid active attempt(s)"],
        )
    return campaigns


def _campaign_status(
    items: list[HarnessAttempt],
    support: list[str],
    refute: list[str],
    diagnostic: list[HarnessAttempt],
    repeated: list[str],
) -> str:
    if diagnostic and repeated and len(diagnostic) >= max(1, len(items) // 2):
        return "BLOCKED"
    if support and refute:
        return "AMBIGUOUS"
    if len(refute) >= 2 and not support:
        return "REFUTED"
    if support and len(support) >= max(1, len(refute)):
        return "SUPPORTED"
    return "ACTIVE"


def _campaign_recommendation(status: str) -> str:
    if status == "SUPPORTED":
        return "CONTINUE_CURRENT_CAMPAIGN"
    if status == "REFUTED":
        return "SWITCH_PARAMETER_GROUP"
    if status == "AMBIGUOUS":
        return "LIMITED_PROBE"
    if status == "BLOCKED":
        return "ENTER_DIAGNOSTIC_REVIEW"
    return "LIMITED_PROBE"


def _active_summary(
    attempts: list[HarnessAttempt],
    proposal_summary: dict[str, Any],
    repeated_failures: list[dict[str, Any]],
    window_limit: int,
) -> dict[str, Any]:
    valid = [attempt for attempt in attempts if attempt.valid]
    n = len(valid)
    counts = Counter(attempt.step_decision for attempt in valid)
    diagnostic_count = sum(1 for attempt in valid if attempt.loop_verdict == "DIAGNOSTIC_FORK")
    mean_info = sum(attempt.information_gain for attempt in valid) / max(n, 1)
    fit_gains = [attempt.fit_gain for attempt in valid if attempt.fit_gain is not None]
    accepted = counts.get("ACCEPTED", 0)
    rejected = counts.get("REJECTED", 0)
    evidence_only = counts.get("EVIDENCE_ONLY", 0)
    plateau_count = _plateau_count(valid)
    confidence = min(1.0, n / MIN_ACTIVE_ATTEMPTS)
    best_score_improvement_ratio = _best_score_improvement_ratio(valid)

    if n < MIN_ACTIVE_ATTEMPTS:
        status = "INSUFFICIENT_ACTIVE_DATA"
        active_score = None
        reason = (
            f"valid active attempts {n}/{MIN_ACTIVE_ATTEMPTS}; "
            "offline_import is historical evidence and is not scored as active search efficiency"
        )
    else:
        diagnostic_rate = diagnostic_count / max(n, 1)
        rejection_rate = rejected / max(accepted + rejected, 1)
        if diagnostic_rate >= 0.50 and repeated_failures:
            status = "DIAGNOSTIC_BLOCKED"
        elif accepted and best_score_improvement_ratio > 0.005:
            status = "PRODUCTIVE"
        elif mean_info >= 0.35 and (accepted == 0 or best_score_improvement_ratio <= 0.005):
            status = "EXPLORATORY"
        elif rejection_rate >= 0.50 and (mean_info < 0.30 or plateau_count >= max(2, n // 2)):
            status = "PLATEAU"
        else:
            status = "EXPLORATORY"
        coverage_score = min(4.0, n / window_limit * 4.0)
        fit_score = min(5.0, max(0.0, sum(gain for gain in fit_gains if gain > 0.0)) / max(abs(_first_score(valid)), 1e-12) / 0.10 * 5.0)
        info_score = min(5.0, max(0.0, mean_info) / 0.60 * 5.0)
        accepted_score = min(3.0, accepted / max(accepted + rejected, 1) / 0.30 * 3.0)
        diagnostic_score = max(0.0, 3.0 * (1.0 - diagnostic_rate * 2.0))
        active_score = round(min(20.0, coverage_score + fit_score + info_score + accepted_score + diagnostic_score), 3)
        reason = "active loop attempts are sufficient for harness process assessment"

    return {
        "status": status,
        "score": active_score,
        "confidence": round(confidence, 3),
        "active_attempt_count": len(attempts),
        "valid_attempt_count": n,
        "active_decision_count": n,
        "window_limit": window_limit,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "evidence_only_count": evidence_only,
        "diagnostic_count": diagnostic_count,
        "acceptance_rate": round(accepted / max(accepted + rejected, 1), 3),
        "rejection_rate": round(rejected / max(accepted + rejected, 1), 3),
        "diagnostic_rate": round(diagnostic_count / max(n, 1), 3),
        "plateau_count": plateau_count,
        "mean_information_gain": round(mean_info, 3),
        "best_score_improvement_ratio": round(best_score_improvement_ratio, 3),
        "proposal_status": proposal_summary.get("status", ""),
        "reason": reason,
    }


def _recommendation(
    active_summary: dict[str, Any],
    proposal_summary: dict[str, Any],
    campaigns: dict[str, CampaignState],
) -> tuple[str, bool, str]:
    proposal_status = str(proposal_summary.get("status", ""))
    active_status = str(active_summary.get("status", ""))
    if proposal_status == "STALE_PROPOSAL_POOL":
        return "REFRESH_LOCAL_PROPOSALS", False, str(proposal_summary.get("reason", ""))
    if proposal_status == "DIAGNOSTIC_ONLY":
        reason = "active data insufficient for trend score, but current proposal pool is diagnostic-only; stop auto materialize."
        if active_status != "INSUFFICIENT_ACTIVE_DATA":
            reason = f"proposal pool is diagnostic-only while active status is {active_status}; require diagnostic review before materialization"
        return "STOP_AUTO_MATERIALIZE", True, reason
    if active_status == "DIAGNOSTIC_BLOCKED":
        return "ENTER_DIAGNOSTIC_REVIEW", True, "diagnostic forks repeat without adding new failure signatures"
    if active_status == "INSUFFICIENT_ACTIVE_DATA":
        return "RUN_BASELINE_EVIDENCE", False, str(active_summary.get("reason", ""))
    if active_status == "PRODUCTIVE":
        return "CONTINUE_CURRENT_CAMPAIGN", False, "recent active attempts are producing accepted or improving evidence"
    if active_status == "PLATEAU":
        return "SWITCH_PARAMETER_GROUP", False, "recent active attempts are repeating failures or rejected low-information steps"
    if any(campaign.status == "AMBIGUOUS" for campaign in campaigns.values()):
        return "LIMITED_PROBE", False, "campaign evidence is conflicting; use a small probe batch"
    return "LIMITED_PROBE", False, "active attempts are exploratory; keep probe count small until campaign support improves"


def _proposal_summary(proposal_pool: dict[str, Any], current_best_run_id: str = "") -> dict[str, Any]:
    proposals = proposal_pool.get("proposals", []) if isinstance(proposal_pool, dict) else []
    if not proposals:
        return {
            "status": "NO_PROPOSAL",
            "score": 0.0,
            "proposal_count": 0,
            "top_proposal_id": "",
            "top_target_dof": "",
            "top_parameter_group": "",
            "top_registration_ready": False,
            "materializable_proposal_ids": [],
            "reason": "no proposal pool is available",
        }
    parent_run_id = str(proposal_pool.get("parent_run_id", ""))
    if current_best_run_id and parent_run_id and parent_run_id != current_best_run_id:
        return {
            "status": "STALE_PROPOSAL_POOL",
            "score": 4.0,
            "proposal_count": len(proposals),
            "top_proposal_id": str(proposals[0].get("proposal_id", "")),
            "top_target_dof": str(proposals[0].get("target_dof", "")),
            "top_parameter_group": str(proposals[0].get("action", {}).get("parameter_group", "")),
            "top_registration_ready": False,
            "materializable_proposal_ids": [],
            "diagnostic_only": False,
            "parent_run_id": parent_run_id,
            "current_best_run_id": current_best_run_id,
            "reason": f"proposal pool parent RunCard {parent_run_id} does not match current best {current_best_run_id}; rerun --local-polish",
        }
    top = proposals[0]
    top_action = top.get("action", {})
    materializable = [
        str(item.get("proposal_id", ""))
        for item in proposals
        if item.get("action", {}).get("registration_ready")
    ]
    all_diagnostic = all(
        str(item.get("action", {}).get("action_type", "")) == "diagnostic_review"
        or not item.get("action", {}).get("registration_ready")
        for item in proposals
    )
    if materializable and top_action.get("registration_ready"):
        status = "MATERIALIZABLE"
        score = 15.0
        reason = "top proposal can be materialized but must still pass global-step full 6DOF validation"
    elif all_diagnostic and not materializable:
        status = "DIAGNOSTIC_ONLY"
        score = 8.0
        reason = str(top_action.get("reason_not_ready") or "proposal pool is diagnostic-only")
    else:
        status = "LIMITED_PROBE_READY" if materializable else "DIAGNOSTIC_ONLY"
        score = 10.0
        reason = "proposal pool contains mixed diagnostic/actionable evidence; limit materialization count"
    return {
        "status": status,
        "score": score,
        "proposal_count": len(proposals),
        "top_proposal_id": str(top.get("proposal_id", "")),
        "top_target_dof": str(top.get("target_dof", "")),
        "top_parameter_group": str(top_action.get("parameter_group", "")),
        "top_registration_ready": bool(top_action.get("registration_ready")),
        "materializable_proposal_ids": materializable,
        "diagnostic_only": status == "DIAGNOSTIC_ONLY",
        "reason": reason,
    }


def _dominant_campaign(campaigns: dict[str, CampaignState]) -> str:
    if not campaigns:
        return ""
    return max(campaigns.values(), key=lambda item: (len(item.attempt_run_ids), item.confidence, item.campaign_id)).campaign_id


def _current_best_run_id(cards: list[dict[str, Any]]) -> str:
    state = memory.read_global_state()
    state_run_id = str(state.get("current_best_run_id", ""))
    if state_run_id:
        return state_run_id
    complete = [
        card for card in cards
        if len(card.get("objective", {}).get("per_dof", {})) == len(DOFS)
        and card.get("objective", {}).get("global_score") is not None
    ]
    if not complete:
        return ""
    return str(min(complete, key=lambda card: float(card.get("objective", {}).get("global_score", math.inf))).get("run_id", ""))


def _relation_run_ids(relation: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for entry in (relation.get("entries", {}) or {}).values():
        out.update(str(item) for item in entry.get("support_run_ids", []) if item)
        out.update(str(item) for item in entry.get("refute_run_ids", []) if item)
    return out


def _proposal_history_by_id(history: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = history.get("entries", {}) if isinstance(history, dict) else {}
    return {str(key): value for key, value in entries.items() if isinstance(value, dict)}


def _decision_meta(card: dict[str, Any]) -> dict[str, Any]:
    meta = ((card.get("parameter_delta") or {}).get("decision_meta") or {})
    return meta if isinstance(meta, dict) else {}


def _target_dof(card: dict[str, Any], transition: dict[str, Any], meta: dict[str, Any]) -> str:
    for value in (meta.get("proposal_target_dof"), meta.get("target_dof"), transition.get("target_dof")):
        if value in DOFS:
            return str(value)
    candidate = str(card.get("observation", {}).get("candidate_id", "")).upper()
    for dof in DOFS:
        if dof.replace("FD_", "") in candidate:
            return dof
    return "GLOBAL"


def _action_type(card: dict[str, Any], transition: dict[str, Any], meta: dict[str, Any]) -> str:
    for value in (meta.get("proposal_action_type"), meta.get("action_type"), transition.get("action_type")):
        if value:
            return str(value)
    delta = card.get("parameter_delta") or {}
    if delta.get("moordyn"):
        return "mooring_probe"
    if delta.get("hydrodyn_edits"):
        return "hydrodyn_edit"
    if delta.get("initial_conditions"):
        return "initial_condition"
    return "unknown"


def _hypothesis(
    card: dict[str, Any],
    meta: dict[str, Any],
    proposal_history_by_id: dict[str, dict[str, Any]],
    parameter_group: str,
    target_dof: str,
    action_type: str,
    dominant_metric: str,
) -> str:
    if meta.get("proposal_hypothesis"):
        return str(meta.get("proposal_hypothesis"))
    proposal_id = str(meta.get("proposal_id", ""))
    hist = proposal_history_by_id.get(proposal_id, {})
    if hist.get("hypothesis"):
        return str(hist.get("hypothesis"))
    candidate = str(card.get("observation", {}).get("candidate_id", ""))
    return f"inferred:{parameter_group}/{target_dof}/{action_type}/{dominant_metric} from {candidate}"


def _dominant_metric(card: dict[str, Any], target_dof: str) -> str:
    per = card.get("objective", {}).get("per_dof", {})
    targets = config.load_config().get("targets", {})
    limits = {
        "period": _num(targets.get("period_error_limit_pct")) or 3.0,
        "envelope": _num(targets.get("env_ok")) or 0.40,
        "ler": _num(targets.get("ler_ok")) or 0.50,
        "fp": _num(targets.get("fp_ok")) or 0.12,
        "zeta": 1.0,
    }
    metrics = [per.get(target_dof, {})] if target_dof in DOFS else [value for value in per.values() if value]
    scores: dict[str, list[float]] = defaultdict(list)
    for item in metrics:
        values = {
            "period": _num(item.get("period_error_abs_pct", item.get("period_error_pct"))),
            "envelope": _num(item.get("envelope_error")),
            "ler": _num(item.get("ler_error")),
            "fp": _num(item.get("fp_error")),
            "zeta": _num(item.get("zeta_error_ratio")),
        }
        for key, value in values.items():
            if value is not None:
                scores[key].append(abs(value) / max(abs(limits[key]), 1e-12))
    if not scores:
        return "mixed"
    averages = {key: sum(values) / len(values) for key, values in scores.items() if values}
    return max(averages.items(), key=lambda item: item[1])[0] if averages else "mixed"


def _failure_signature(card: dict[str, Any]) -> str:
    failures: set[str] = set()
    objective = card.get("objective", {})
    failures.update(str(item) for item in objective.get("hard_constraint_violations", []) if item)
    step = card.get("step_decision") or card.get("verdict") or {}
    failures.update(str(item) for item in step.get("hard_constraint_violations", []) if item)
    for pred in card.get("predicate_results", []) or []:
        if isinstance(pred, dict) and not pred.get("passed", True):
            failures.add(str(pred.get("name") or pred.get("reason") or "predicate_failed"))
    if (card.get("loop_verdict") or {}).get("status") == "DIAGNOSTIC_FORK":
        failures.add("diagnostic_fork")
    if not failures and str(step.get("status", "")) == "REJECTED":
        failures.add("rejected_without_hard_constraint")
    return "|".join(sorted(failures)) if failures else "clean"


def _complete_6dof(card: dict[str, Any]) -> bool:
    per = card.get("objective", {}).get("per_dof", {})
    return bool(len(per) == len(DOFS) and all(dof in per for dof in DOFS))


def _is_dry_run(card: dict[str, Any]) -> bool:
    blob = json.dumps({
        "parameter_delta": card.get("parameter_delta", {}),
        "evidence_sources": card.get("evidence_sources", []),
        "artifacts": card.get("observation", {}).get("artifacts", {}),
    }, ensure_ascii=False).lower()
    return "dry_run" in blob or "dry-run" in blob


def _fit_gain(transition: dict[str, Any]) -> float | None:
    delta = _num(transition.get("global_score_delta"))
    if delta is None:
        return None
    return round(-delta, 6)


def _plateau_count(attempts: list[HarnessAttempt]) -> int:
    if not attempts:
        return 0
    count = 0
    for attempt in reversed(attempts):
        if attempt.step_decision == "ACCEPTED" or (attempt.fit_gain is not None and attempt.fit_gain > 1e-4):
            break
        if attempt.step_decision in {"REJECTED", "EVIDENCE_ONLY"} or "repeated_same_failure" in attempt.tags:
            count += 1
    return count


def _best_score_improvement_ratio(attempts: list[HarnessAttempt]) -> float:
    scores = [attempt.global_score for attempt in attempts if attempt.global_score is not None]
    if len(scores) < 2:
        return 0.0
    start = scores[0]
    best = min(scores)
    return max(0.0, start - best) / max(abs(start), 1e-12)


def _first_score(attempts: list[HarnessAttempt]) -> float:
    for attempt in attempts:
        if attempt.global_score is not None:
            return float(attempt.global_score)
    return 1.0


def _card_sort_key(card: dict[str, Any]) -> tuple[str, str]:
    return (str(card.get("created_at", "")), str(card.get("run_id", "")))


def _safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", str(value)).strip("_").upper() or "UNKNOWN"


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _fmt(value: Any) -> str:
    num = _num(value)
    return "" if num is None else f"{num:.3f}"
