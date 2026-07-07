from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any

from . import actions, config, memory, oracle
from .schema import (
    CandidateIntent,
    DOFS,
    DofLocalState,
    LocalAction,
    LocalGuardContext,
    LocalProposal,
    ProposalPool,
    to_plain,
)


def build_proposal_pool(target_dof: str | None = None, top_k: int = 1) -> ProposalPool:
    manifest = oracle.ensure_current_manifest()
    state = memory.read_global_state()
    cards = memory.load_run_cards()
    prior_pool = memory.read_proposal_pool()
    proposal_history = memory.build_proposal_history(cards, prior_pool)
    preferred_run_id = str(state.get("current_best_run_id", ""))
    preferred = _card_by_run_id(cards, preferred_run_id)
    best = preferred if preferred and _is_clean_complete_card(preferred) else _best_complete_card(cards, preferred_run_id=preferred_run_id)
    if not best:
        raise RuntimeError("no complete current-best RunCard available for local loop")
    coupling = memory.read_coupling_memory()
    guard = build_guard_context(best, coupling, manifest.oracle_id)
    selected = [target_dof] if target_dof else list(DOFS)
    bad = [dof for dof in selected if dof not in DOFS]
    if bad:
        raise ValueError(f"unknown DOF(s): {', '.join(bad)}")
    proposals: list[LocalProposal] = []
    parent_candidate = best.get("observation", {}).get("candidate_id", "")
    parent_run = best.get("run_id", "")
    for dof in selected:
        local_state = guard.dof_states[dof]
        target_decay = _target_decay_start(state, dof)
        for action in actions.actions_for_dof(local_state, parent_candidate, target_decay):
            proposals.append(_proposal_for(local_state, action, guard, parent_run, parent_candidate, coupling))
    proposals = _apply_outcome_feedback(proposals, proposal_history)
    proposals = sorted(proposals, key=lambda item: item.priority, reverse=True)
    proposals = _apply_harness_guidance(proposals, top_k)
    return ProposalPool(
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        parent_run_id=parent_run,
        parent_candidate_id=parent_candidate,
        guard_context=guard,
        proposals=proposals,
        top_k=max(1, int(top_k)),
    )


def build_guard_context(best: dict[str, Any], coupling: dict[str, Any], oracle_id: str) -> LocalGuardContext:
    objective = best.get("objective", {})
    per = objective.get("per_dof", {})
    obs_per = best.get("observation", {}).get("per_dof_metrics", {})
    states: dict[str, DofLocalState] = {}
    for dof in DOFS:
        metrics = per.get(dof, {})
        states[dof] = _local_state(dof, metrics, obs_per.get(dof, {}), coupling)
    open_dofs = [dof for dof, item in states.items() if not item.closed]
    protected_dofs = [dof for dof, item in states.items() if item.closed]
    fragile_dofs = [dof for dof, item in states.items() if item.coupling_risk >= 0.50 or (item.score is not None and item.score <= 1.15)]
    cfg = config.load_config()
    return LocalGuardContext(
        oracle_id=oracle_id,
        epoch_id=oracle_id,
        current_best_run_id=best.get("run_id", ""),
        current_best_candidate_id=best.get("observation", {}).get("candidate_id", ""),
        open_dofs=open_dofs,
        protected_dofs=protected_dofs,
        fragile_dofs=sorted(set(fragile_dofs)),
        dof_states=states,
        thresholds={"targets": cfg.get("targets", {}), "physics": cfg.get("physics", {})},
        source_roles={
            "target_truth": "oracle",
            "current_best": "observation",
            "coupling_memory": "memory_inference",
            "local_guard_context": "derived_constraint",
        },
    )


def _local_state(dof: str, metrics: dict[str, Any], observation_metrics: dict[str, Any], coupling: dict[str, Any]) -> DofLocalState:
    if not metrics:
        return DofLocalState(
            dof=dof,
            score=None,
            closed=False,
            dominant_error_mode="missing",
            component_scores={},
            coupling_risk=_coupling_risk(dof, coupling),
            allowed_regression={"score": 0.25, "relative_score": 0.25},
            notes=["missing metrics in current best objective"],
        )
    score = _num(metrics.get("score"))
    cfg = config.load_config()
    targets = cfg.get("targets", {})
    components = {
        "period": _component_from_limit(metrics.get("period_error_pct"), targets.get("period_error_limit_pct", 3.0)),
        "envelope": _component_from_limit(metrics.get("envelope_error"), targets.get("env_ok", 0.40)),
        "ler": _component_from_limit(metrics.get("ler_error"), targets.get("ler_ok", 0.50)),
        "fp": _component_from_limit(metrics.get("fp_error"), targets.get("fp_ok", 0.12)),
        "zeta": _num(metrics.get("zeta_error_ratio")),
    }
    zeta = _num(observation_metrics.get("zeta"))
    ref_zeta = _num(observation_metrics.get("reference_zeta"))
    if zeta is not None and ref_zeta is not None and abs(ref_zeta) > 1e-12:
        components["zeta_signed_ratio"] = (zeta - ref_zeta) / abs(ref_zeta)
    leak = observation_metrics.get("leak") or {}
    ref_leak = observation_metrics.get("reference_leak") or {}
    if leak:
        physics = cfg.get("physics", {})
        leak_abs = float(physics.get("leak_abs", 0.50))
        leak_factor = float(physics.get("leak_factor", 3.0))
        strong_scores = []
        for key, value in leak.items():
            ratio = _num(value)
            ref = _num(ref_leak.get(key)) or 0.0
            if ratio is not None and ratio > leak_abs and ratio > leak_factor * max(ref, 1e-3):
                strong_scores.append(max(ratio / max(leak_abs, 1e-12), ratio / max(leak_factor * max(ref, 1e-3), 1e-12)))
        components["leak"] = max(strong_scores or [0.0])
    mode = _dominant_mode(components)
    risk = _coupling_risk(dof, coupling)
    notes = []
    if risk >= 0.50:
        notes.append("historically fragile in coupling memory")
    return DofLocalState(
        dof=dof,
        score=score,
        closed=bool(metrics.get("closed")),
        dominant_error_mode=mode,
        component_scores=components,
        coupling_risk=risk,
        allowed_regression={"score": 0.25, "relative_score": 0.25},
        notes=notes,
    )


def _proposal_for(
    state: DofLocalState,
    action: Any,
    guard: LocalGuardContext,
    parent_run: str,
    parent_candidate: str,
    coupling: dict[str, Any],
) -> LocalProposal:
    expected_gain = _target_need(state)
    protected_risk = _protected_risk(action.parameter_group, guard, coupling)
    information_gain = _information_gain(state, action)
    memory_support = _memory_support(action.parameter_group, state.dof, coupling)
    mechanism_clarity = _mechanism_clarity(state)
    run_cost = 1.0 if action.registration_ready else 0.25
    priority = expected_gain + mechanism_clarity + information_gain + memory_support - protected_risk - run_cost
    intent = _candidate_intent(
        state,
        action,
        expected_gain=expected_gain,
        protected_risk=protected_risk,
        information_gain=information_gain,
        memory_support=memory_support,
    )
    action = _action_with_intent(action, intent)
    pid = _proposal_id(parent_candidate, state.dof, action.action_type, action.vector)
    return LocalProposal(
        proposal_id=pid,
        parent_run_id=parent_run,
        parent_candidate_id=parent_candidate,
        target_dof=state.dof,
        hypothesis=_hypothesis(state, action),
        action=action,
        expected_gain=expected_gain,
        protected_risk=protected_risk,
        information_gain=information_gain,
        memory_support=memory_support,
        mechanism_clarity=mechanism_clarity,
        run_cost=run_cost,
        priority=priority,
        candidate_intent=intent,
        created_at=config.utc_now(),
    )


def _dominant_mode(components: dict[str, float | None]) -> str:
    leak = components.get("leak")
    if leak is not None and leak >= 1.0:
        return "leak"
    available = {k: v for k, v in components.items() if v is not None and k != "leak"}
    if not available:
        return "mixed"
    key, value = max(available.items(), key=lambda item: item[1])
    if key == "period" and value >= 1.5:
        return "period"
    if value < 0.8:
        return "mixed"
    return key


def _target_need(state: DofLocalState) -> float:
    if state.score is None:
        return 0.0
    return min(3.0, max(0.0, state.score))


def _mechanism_clarity(state: DofLocalState) -> float:
    if state.dominant_error_mode in {"missing", "mixed"}:
        return 0.2
    if state.dominant_error_mode == "leak":
        return 0.8
    return 1.0


def _information_gain(state: DofLocalState, action: Any) -> float:
    gain = 0.4
    if state.dominant_error_mode in {"period", "leak"}:
        gain += 0.5
    if not action.registration_ready:
        gain += 0.2
    if state.coupling_risk >= 0.50:
        gain += 0.2
    return min(1.5, gain)


def _memory_support(group: str, dof: str, coupling: dict[str, Any]) -> float:
    entry = coupling.get("entries", {}).get(group, {})
    confidence = _num(entry.get("confidence")) or 0.0
    improves = set(entry.get("improves", []))
    damages = set(entry.get("damages", []))
    score = 0.0
    if dof in improves:
        score += 0.5 * confidence
    if dof in damages:
        score -= 0.3 * confidence
    return score


def _protected_risk(group: str, guard: LocalGuardContext, coupling: dict[str, Any]) -> float:
    entry = coupling.get("entries", {}).get(group, {})
    damages = set(entry.get("damages", []))
    confidence = _num(entry.get("confidence")) or 0.0
    risk = 0.0
    for dof in guard.protected_dofs:
        if dof in damages:
            risk += 0.35 * confidence
    for dof in guard.fragile_dofs:
        if dof in damages:
            risk += 0.15 * confidence
    return min(2.5, risk)


def _coupling_risk(dof: str, coupling: dict[str, Any]) -> float:
    risk = 0.0
    for entry in coupling.get("entries", {}).values():
        confidence = _num(entry.get("confidence")) or 0.0
        if dof in entry.get("damages", []):
            risk += 0.35 * confidence
    return min(1.0, risk)


def _hypothesis(state: DofLocalState, action: Any) -> str:
    if action.action_type == "diagnostic_review":
        return f"{state.dof} {state.dominant_error_mode} error needs diagnostic evidence before local materialization"
    return f"{state.dof} dominant {state.dominant_error_mode} error may improve via {action.action_type}"


def _candidate_intent(
    state: DofLocalState,
    action: Any,
    expected_gain: float,
    protected_risk: float,
    information_gain: float,
    memory_support: float,
) -> CandidateIntent:
    touched = _touched_parameters(action)
    expected = action.expected_primary_effect or f"{state.dof} response changes under {action.action_type}"
    expected += "; global-step must confirm full 6DOF predicates and score"
    falsification = (
        f"{state.dof} {state.dominant_error_mode} component does not improve, "
        "or any hard predicate/new protected-DOF regression appears in full 6DOF validation"
    )
    risk_notes = list(action.expected_side_effects or [])
    risk_notes.extend(action.risk_tags or [])
    return CandidateIntent(
        source="local_loop",
        intent=_hypothesis(state, action),
        expected_observation=expected,
        falsification_condition=falsification,
        touched_parameters=touched,
        risk_notes=sorted(set(str(item) for item in risk_notes if item)),
        evidence_context={
            "dominant_error_mode": state.dominant_error_mode,
            "component_scores": state.component_scores,
            "expected_gain": expected_gain,
            "information_gain": information_gain,
            "memory_support": memory_support,
            "protected_risk": protected_risk,
            "source_role": "candidate_intent_not_truth",
            "acceptance_gate": "full_6dof_global_step",
        },
    )


def _action_with_intent(action: LocalAction, intent: CandidateIntent) -> LocalAction:
    vector = deepcopy(action.vector or {})
    meta = dict(vector.get("decision_meta") or {})
    payload = to_plain(intent)
    meta["candidate_intent"] = payload
    meta["intent"] = intent.intent
    meta["expected_observation"] = intent.expected_observation
    meta["falsification_condition"] = intent.falsification_condition
    meta["candidate_intent_source"] = intent.source
    vector["decision_meta"] = meta
    return replace(action, vector=vector)


def _touched_parameters(action: Any) -> list[str]:
    vector = action.vector or {}
    touched: list[str] = []
    for edit in vector.get("hydrodyn_edits") or []:
        block = edit.get("block", "HydroDyn")
        if "i" in edit and "j" in edit:
            touched.append(f"HydroDyn.{block}[{edit.get('i')},{edit.get('j')}]")
        else:
            touched.append(f"HydroDyn.{block}")
    moordyn = vector.get("moordyn") or {}
    if isinstance(moordyn, dict):
        touched.extend(f"MoorDyn.{key}" for key in sorted(moordyn) if key)
    initial = vector.get("initial_conditions") or {}
    if isinstance(initial, dict) and initial:
        touched.extend(f"InitialCondition.{key}" for key in sorted(initial))
    return touched or [f"{action.parameter_group}.{action.action_type}"]


def _apply_harness_guidance(proposals: list[LocalProposal], top_k: int) -> list[LocalProposal]:
    harness_status = memory.read_harness_status()
    campaign_memory = memory.read_campaign_memory()
    campaigns = _campaign_entries(harness_status, campaign_memory)
    recommendation = str(harness_status.get("recommendation", ""))
    limited_probe = recommendation == "LIMITED_PROBE"
    adjusted: list[LocalProposal] = []
    for proposal in proposals:
        adjusted.append(_apply_campaign_penalty(proposal, campaigns))
    adjusted = sorted(adjusted, key=lambda item: item.priority, reverse=True)
    if limited_probe:
        return [
            replace(item, status=item.status if item.status != "proposed" else "limited_probe_ranked")
            for item in adjusted
        ]
    return adjusted


def _apply_outcome_feedback(
    proposals: list[LocalProposal],
    history: dict[str, Any],
) -> list[LocalProposal]:
    entries = [_history_entry_dict(item) for item in history.values()]
    by_id = {str(item.get("proposal_id", "")): item for item in entries if item.get("proposal_id")}
    rejected = [item for item in entries if _history_outcome(item) == "rejected"]
    rejected_counts: dict[str, int] = {}
    for item in rejected:
        group = str(item.get("parameter_group", ""))
        if group:
            rejected_counts[group] = rejected_counts.get(group, 0) + 1

    adjusted: list[LocalProposal] = []
    for proposal in proposals:
        exact = by_id.get(proposal.proposal_id)
        if exact and _history_outcome(exact) == "accepted":
            adjusted.append(replace(proposal, priority=proposal.priority + 0.5, status="outcome_supported"))
            continue
        if exact and _history_outcome(exact) == "rejected":
            adjusted.append(_memory_warning_proposal(
                proposal,
                "exact proposal was already rejected by full 6DOF validation",
                penalty=2.0,
                status="outcome_refuted_evidence_warning",
            ))
            continue

        similar_rejections = [
            item for item in rejected
            if str(item.get("parameter_group", "")) == proposal.action.parameter_group
            and str(item.get("target_dof", "")) == proposal.target_dof
            and _action_family(str(item.get("action_type", ""))) == _action_family(proposal.action.action_type)
        ]
        if similar_rejections:
            adjusted.append(replace(
                proposal,
                priority=proposal.priority - min(2.0, 0.75 * len(similar_rejections)),
                status="similar_outcome_rejected",
            ))
            continue

        if (
            proposal.action.registration_ready
            and proposal.action.parameter_group == "diagonal_hydro"
            and rejected_counts.get("diagonal_hydro", 0) >= 3
        ):
            adjusted.append(_memory_warning_proposal(
                proposal,
                "diagonal_hydro has three recent rejected local probes in this oracle epoch; switch to mechanism search or diagnostic review",
                penalty=1.5,
                status="repeated_rejection_evidence_warning",
            ))
            continue
        adjusted.append(proposal)
    return adjusted


def _history_entry_dict(entry: Any) -> dict[str, Any]:
    if isinstance(entry, dict):
        return entry
    return {
        "proposal_id": getattr(entry, "proposal_id", ""),
        "parent_run_id": getattr(entry, "parent_run_id", ""),
        "parent_candidate_id": getattr(entry, "parent_candidate_id", ""),
        "target_dof": getattr(entry, "target_dof", ""),
        "action_type": getattr(entry, "action_type", ""),
        "parameter_group": getattr(entry, "parameter_group", ""),
        "materialized_candidate_ids": getattr(entry, "materialized_candidate_ids", []),
        "run_ids": getattr(entry, "run_ids", []),
        "step_decisions": getattr(entry, "step_decisions", []),
        "outcome": getattr(entry, "outcome", ""),
    }


def _history_outcome(entry: dict[str, Any]) -> str:
    outcome = str(entry.get("outcome", ""))
    if outcome and outcome != "materialized":
        return outcome
    statuses = [
        str(item.get("status", ""))
        for item in entry.get("step_decisions", [])
        if isinstance(item, dict)
    ]
    if "ACCEPTED" in statuses:
        return "accepted"
    if "REJECTED" in statuses:
        return "rejected"
    if "EVIDENCE_ONLY" in statuses:
        return "evidence_only"
    return outcome or "proposed"


def _action_family(action_type: str) -> str:
    text = action_type.lower()
    if "bquad" in text or text == "hydrodyn_edit":
        return "diagonal_bquad"
    if "mooring" in text:
        return "mooring"
    if "diagnostic" in text:
        return "diagnostic"
    return text or "unknown"


def _campaign_entries(harness_status: dict[str, Any], campaign_memory: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in (campaign_memory.get("entries", {}), harness_status.get("campaigns", {})):
        if isinstance(source, dict):
            entries.extend(item for item in source.values() if isinstance(item, dict))
    return entries


def _apply_campaign_penalty(proposal: LocalProposal, campaigns: list[dict[str, Any]]) -> LocalProposal:
    matched = [
        item for item in campaigns
        if item.get("parameter_group") == proposal.action.parameter_group
        and item.get("target_dof") == proposal.target_dof
        and item.get("action_type") in {proposal.action.action_type, "unknown", ""}
    ]
    if not matched:
        return proposal
    status_order = {"BLOCKED": 3, "REFUTED": 2, "AMBIGUOUS": 1}
    campaign = max(matched, key=lambda item: (status_order.get(str(item.get("status", "")), 0), float(item.get("confidence", 0.0) or 0.0)))
    status = str(campaign.get("status", ""))
    confidence = _num(campaign.get("confidence")) or 0.0
    if status == "REFUTED":
        return replace(
            proposal,
            priority=proposal.priority - max(0.5, confidence),
            status="campaign_refuted",
        )
    if status == "AMBIGUOUS":
        return replace(
            proposal,
            priority=proposal.priority - 0.25 * max(0.5, confidence),
            status="campaign_ambiguous_limited_probe",
        )
    if status == "BLOCKED":
        return _memory_warning_proposal(
            proposal,
            "matched campaign is blocked in harness memory",
            penalty=max(1.0, confidence),
            status="campaign_blocked_evidence_warning",
        )
    return proposal


def _memory_warning_proposal(proposal: LocalProposal, reason: str, penalty: float, status: str) -> LocalProposal:
    action = _action_with_memory_warning(proposal.action, reason)
    return replace(
        proposal,
        action=action,
        priority=proposal.priority - max(0.0, penalty),
        status=status,
    )


def _action_with_memory_warning(action: LocalAction, reason: str) -> LocalAction:
    vector = deepcopy(action.vector or {})
    meta = dict(vector.get("decision_meta") or {})
    warnings = list(meta.get("memory_warnings") or [])
    warnings.append(reason)
    meta["memory_warnings"] = sorted(set(str(item) for item in warnings if item))
    meta["memory_no_veto"] = True
    vector["decision_meta"] = meta
    risk_tags = sorted(set([*action.risk_tags, "memory_warning", "evidence_only_no_veto"]))
    return replace(action, vector=vector, risk_tags=risk_tags)


def _force_diagnostic_proposal(proposal: LocalProposal, reason: str) -> LocalProposal:
    vector = dict(proposal.action.vector or {})
    meta = dict(vector.get("decision_meta") or {})
    meta["harness_stop_auto_materialize"] = True
    meta["original_action_type"] = proposal.action.action_type
    vector["decision_meta"] = meta
    action = LocalAction(
        action_type="diagnostic_review",
        parameter_group=proposal.action.parameter_group,
        vector=vector,
        registration_ready=False,
        reason_not_ready=reason or "harness requires diagnostic review before materialization",
        expected_primary_effect="increase information before materializing another local candidate",
        expected_side_effects=proposal.action.expected_side_effects,
        risk_tags=sorted(set([*proposal.action.risk_tags, "diagnostic_only", "harness_stop_auto_materialize"])),
    )
    return replace(
        proposal,
        action=action,
        priority=min(proposal.priority, 0.0),
        status="harness_diagnostic_only",
    )


def _target_decay_start(state: dict[str, Any], dof: str) -> float | None:
    target = state.get("target_truth", {}).get("dofs", {}).get(dof, {})
    return _num(target.get("decay_start"))


def _proposal_id(parent_candidate: str, dof: str, action_type: str, vector: dict[str, Any]) -> str:
    vector_hash = json.dumps(vector, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raw = f"{parent_candidate}:{dof}:{action_type}:{vector_hash}"
    return "LP_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12].upper()


def _best_complete_card(cards: list[dict[str, Any]], preferred_run_id: str = "") -> dict[str, Any] | None:
    complete = [c for c in cards if _is_clean_complete_card(c)]
    if not complete:
        return None
    best_score = min(memory.run_card_global_score(c) for c in complete)
    tied = [c for c in complete if abs(memory.run_card_global_score(c) - best_score) <= 1e-12]
    if preferred_run_id:
        for card in tied:
            if str(card.get("run_id", "")) == preferred_run_id:
                return card
    return min(tied, key=lambda c: (str(c.get("created_at", "")), str(c.get("run_id", ""))))


def _is_clean_complete_card(card: dict[str, Any]) -> bool:
    objective = memory.run_card_objective(card)
    per = memory.run_card_per_dof(card)
    score = memory.run_card_global_score(card)
    return bool(
        len(per) == len(DOFS)
        and all(dof in per for dof in DOFS)
        and not objective.get("hard_constraint_violations", [])
        and score < 1e98
    )


def _card_by_run_id(cards: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    for card in cards:
        if card.get("run_id") == run_id:
            return card
    return None


def _component_from_limit(value: Any, limit: Any) -> float | None:
    v = _num(value)
    lim = _num(limit)
    if v is None or lim is None:
        return None
    return abs(v) / max(abs(lim), 1e-12)


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
