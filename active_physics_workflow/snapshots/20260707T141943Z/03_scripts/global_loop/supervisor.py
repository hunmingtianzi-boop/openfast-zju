from __future__ import annotations

import json
import shutil
import time
from typing import Any

from . import config, diagnostic_queue, harness as harness_mod, llm_analyst, local_loop, memory, memory_gc, memory_report, oracle, project_scoring, relation_memory, runner_adapter, workflow_benchmark as workflow_benchmark_mod
from .schema import DOFS, PARAMETER_GROUPS, RunCard, to_plain
from .scoring import loop_verdict_from_step, score_observation, verdict_from_objective


DEFAULT_QUEUE = list(PARAMETER_GROUPS)


def init_oracle() -> dict[str, Any]:
    cfg = config.load_config()
    manifest = oracle.ensure_current_manifest(cfg)
    epoch = memory.current_epoch()
    state = {
        "updated_at": config.utc_now(),
        "mode": "init_oracle",
        "oracle_id": manifest.oracle_id,
        "epoch_id": epoch.epoch_id,
        "oracle_manifest": to_plain(manifest),
        "epoch": to_plain(epoch),
    }
    memory.write_global_state(state)
    memory.write_report("oracle_status_report.md", "Global Loop Oracle Status", _oracle_report(state))
    return state


def status() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    cards = memory.load_run_cards()
    state = _without_transient_status_fields(_with_latest_run_state(memory.read_global_state(), cards))
    coupling = memory.read_coupling_memory()
    best_info = select_current_best(cards, state)
    best = best_info.get("selected_card")
    score_summary: dict[str, Any] = {}
    try:
        score_summary = project_scoring.summary(project_scoring.build_scorecard())
    except Exception as exc:
        score_summary = {"project_score_error": str(exc)}
    benchmark_summary: dict[str, Any] = {}
    try:
        benchmark_summary = workflow_benchmark_mod.summary(workflow_benchmark_mod.build_benchmark())
    except Exception as exc:
        benchmark_summary = {"workflow_benchmark_error": str(exc)}
    harness_summary: dict[str, Any] = {}
    try:
        harness_summary = harness_mod.summary(harness_mod.build_harness_assessment())
    except Exception as exc:
        harness_summary = {"harness_error": str(exc)}
    artifact_manifest = memory.read_artifact_manifest()
    artifact_entries = artifact_manifest.get("entries", {}) if isinstance(artifact_manifest, dict) else {}
    diagnostic_summary: dict[str, Any] = {}
    try:
        diagnostic_summary = diagnostic_queue.queue_summary(
            memory.read_diagnostic_queue(),
            current_best_candidate_id=str(state.get("current_best_candidate_id", "")),
            current_best_run_id=str(state.get("current_best_run_id", "")),
        )
    except Exception as exc:
        diagnostic_summary = {"diagnostic_queue_error": str(exc)}
    return {
        **state,
        **score_summary,
        **benchmark_summary,
        **harness_summary,
        "updated_at": config.utc_now(),
        "mode": "status",
        "oracle_id": manifest.oracle_id,
        "epoch": to_plain(memory.current_epoch()),
        "run_cards_total": len(cards),
        "artifact_manifest_count": len(artifact_entries),
        "diagnostic_queue_summary": diagnostic_summary,
        "diagnostic_active_item_count": diagnostic_summary.get("active_item_count"),
        "diagnostic_actionable_item_count": diagnostic_summary.get("actionable_item_count"),
        "coupling_memory_present": bool(coupling),
        "state_current_best_run_id": state.get("current_best_run_id", ""),
        "state_current_best_candidate_id": state.get("current_best_candidate_id", ""),
        "recomputed_best_run_id": (best_info.get("recomputed_card") or {}).get("run_id", ""),
        "recomputed_best_candidate_id": (best_info.get("recomputed_card") or {}).get("observation", {}).get("candidate_id", ""),
        "current_best_drift": best_info.get("drift", False),
        "current_best_drift_reason": best_info.get("reason", ""),
        "current_best_run_id": best.get("run_id", "") if best else state.get("current_best_run_id", ""),
        "current_best_candidate_id": best.get("observation", {}).get("candidate_id", "") if best else state.get("current_best_candidate_id", ""),
        "current_best_score": best.get("objective", {}).get("global_score") if best else state.get("current_best_score"),
    }


def _without_transient_status_fields(state: dict[str, Any]) -> dict[str, Any]:
    transient = {
        "proposal_id",
        "candidate_id",
        "registered_candidate_row",
        "registration_ready",
        "next_step",
        "parent_candidate_id",
        "memory_gc_plan",
    }
    return {k: v for k, v in state.items() if k not in transient}


def _latest_decision_card(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    decision_cards = [
        card for card in cards
        if (card.get("step_decision") or card.get("verdict")) and card.get("objective", {}).get("global_score") is not None
    ]
    if not decision_cards:
        return None
    return max(decision_cards, key=lambda card: (str(card.get("created_at", "")), str(card.get("run_id", ""))))


def _with_latest_run_state(state: dict[str, Any], cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    card = _latest_decision_card(cards if cards is not None else memory.load_run_cards())
    if not card:
        return dict(state)
    step = memory.run_card_step_decision(card)
    loop = memory.run_card_loop_verdict(card)
    candidate_id = str(card.get("observation", {}).get("candidate_id") or card.get("candidate_id", ""))
    return {
        **state,
        "last_run_id": card.get("run_id", ""),
        "last_candidate_id": candidate_id,
        "last_step_decision": step,
        "last_verdict": step,
        "loop_verdict": loop,
        "last_score": card.get("objective", {}).get("global_score"),
        "last_stage": card.get("stage", ""),
    }


def project_score() -> dict[str, Any]:
    scorecard = project_scoring.build_scorecard(is_snapshot=True)
    score_path = project_scoring.write_scorecard(scorecard)
    report_path = memory.write_report(
        "project_score_report.md",
        "Global Loop Project Scorecard",
        project_scoring.report_lines(scorecard),
    )
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "project_score",
        "oracle_id": scorecard.oracle_id,
        "epoch_id": scorecard.epoch_id,
        "project_score_path": score_path.relative_to(config.workspace_root()).as_posix(),
        "project_score_report_path": report_path.relative_to(config.workspace_root()).as_posix(),
        **project_scoring.summary(scorecard),
        "project_scorecard": to_plain(scorecard),
    }


def workflow_benchmark() -> dict[str, Any]:
    benchmark = workflow_benchmark_mod.build_benchmark(is_snapshot=True)
    benchmark_path = workflow_benchmark_mod.write_benchmark(benchmark)
    report_path = memory.write_report(
        "workflow_benchmark_report.md",
        "Global Loop Workflow Benchmark",
        workflow_benchmark_mod.report_lines(benchmark),
    )
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "workflow_benchmark",
        "oracle_id": benchmark.oracle_id,
        "epoch_id": benchmark.epoch_id,
        "workflow_benchmark_path": benchmark_path.relative_to(config.workspace_root()).as_posix(),
        "workflow_benchmark_report_path": report_path.relative_to(config.workspace_root()).as_posix(),
        **workflow_benchmark_mod.summary(benchmark),
        "workflow_benchmark": to_plain(benchmark),
    }


def harness_status() -> dict[str, Any]:
    assessment = harness_mod.build_harness_assessment()
    paths = harness_mod.write_assessment_outputs(assessment)
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "harness_status",
        "oracle_id": assessment.oracle_id,
        "epoch_id": assessment.epoch_id,
        "harness_status_path": paths["status"].relative_to(config.workspace_root()).as_posix(),
        "campaign_memory_path": paths["campaign_memory"].relative_to(config.workspace_root()).as_posix(),
        "harness_report_path": paths["report"].relative_to(config.workspace_root()).as_posix(),
        **harness_mod.summary(assessment),
        "harness_assessment": to_plain(assessment),
    }


def memory_rebuild() -> dict[str, Any]:
    state = relation_memory.rebuild_all()
    state = _with_latest_run_state(state)
    state = _without_transient_status_fields(state)
    memory.write_global_state(state)
    return state


def memory_status_report() -> dict[str, Any]:
    state = memory_report.build_report()
    state = _without_transient_status_fields(state)
    memory.write_global_state(state)
    return state


def memory_gc_plan(apply: bool = False) -> dict[str, Any]:
    plan = memory_gc.build_gc_plan(apply=apply)
    paths = memory_gc.write_gc_plan(plan)
    state = {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "memory_gc",
        "oracle_id": plan.oracle_id,
        "epoch_id": plan.epoch_id,
        "memory_gc_apply": apply,
        "memory_gc_candidate_count": len(plan.candidates),
        "memory_gc_skipped_count": len(plan.skipped),
        "memory_gc_deleted_count": len(plan.deleted),
        "memory_gc_candidate_bytes": plan.total_candidate_bytes,
        "memory_gc_report_path": paths["report"].relative_to(config.workspace_root()).as_posix(),
        "memory_gc_plan_path": paths["plan"].relative_to(config.workspace_root()).as_posix(),
    }
    state = _without_transient_status_fields(state)
    memory.write_global_state(state)
    return state


def query_memory(dof: str = "", metric: str = "", parameter_group: str = "", action_type: str = "") -> dict[str, Any]:
    return _without_transient_status_fields(relation_memory.query(
        dof=dof,
        metric=metric,
        parameter_group=parameter_group,
        action_type=action_type,
    ))


def diagnostic_refresh() -> dict[str, Any]:
    return _without_transient_status_fields(diagnostic_queue.refresh())


def llm_pre_run(candidate_id: str, call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.pre_run(candidate_id, call_model=call_model))


def llm_during_run(candidate_id: str, call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.during_run(candidate_id, call_model=call_model))


def llm_post_run(run_id: str = "", latest: bool = False, call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.post_run(run_id=run_id, latest=latest, call_model=call_model))


def llm_synthesize(run_id: str, call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.synthesize(run_id, call_model=call_model))


def llm_review_proposals(call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.proposal_review(call_model=call_model))


def diagnostic_review(item_id: str, call_model: bool = False) -> dict[str, Any]:
    return _without_transient_status_fields(llm_analyst.diagnostic_review(item_id, call_model=call_model))


def offline_import(candidate_ids: list[str] | None = None) -> dict[str, Any]:
    cfg = config.load_config()
    config.assert_active_paths(cfg)
    manifest = oracle.ensure_current_manifest(cfg)
    migrated = 0
    target = runner_adapter.build_target_truth(cfg)
    memory.write_truth_snapshot(target, manifest)
    cards = runner_adapter.import_registry_run_cards(candidate_ids=candidate_ids, stage="offline_import")
    written = [memory.write_run_card(card) for card in cards]
    all_cards = memory.load_run_cards()
    coupling = memory.build_coupling_memory(all_cards)
    memory.write_coupling_memory(coupling)
    memory.write_artifact_manifest(memory.build_artifact_manifests(all_cards))
    best = select_current_best(all_cards, {})["selected_card"]
    state = {
        "updated_at": config.utc_now(),
        "mode": "offline_import",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "oracle_manifest": to_plain(manifest),
        "target_truth": to_plain(target),
        "candidate_filter": candidate_ids or [],
        "legacy_run_cards_migrated": migrated,
        "run_cards_written": len(written),
        "run_cards_total": len(all_cards),
        "current_best_run_id": best.get("run_id", "") if best else "",
        "current_best_candidate_id": best.get("observation", {}).get("candidate_id", "") if best else "",
        "current_best_score": best.get("objective", {}).get("global_score") if best else None,
        "loop_verdict": {"status": "RUNNING", "reason": "offline import complete", "tags": ["offline_import"]},
    }
    memory.write_global_state(state)
    report = _offline_report(cards, all_cards, best)
    memory.write_report("offline_import_report.md", "Global Loop Offline Import", report)
    config.append_log("08_logs/harness_build_log.md", f"global_loop offline_import cards={len(cards)}")
    return state


def baseline_suite(candidate_id: str = "CAND_TEST", run_live: bool = False) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    if run_live:
        rc = runner_adapter.run_candidate_suite(candidate_id, list(DOFS), continue_on_failure=True)
        if rc != 0:
            raise RuntimeError(f"baseline suite failed for {candidate_id} with rc={rc}")
    cards = runner_adapter.import_registry_run_cards(candidate_ids=[candidate_id], stage="baseline_suite")
    for card in cards:
        memory.write_run_card(card)
    all_cards = memory.load_run_cards()
    coupling = memory.build_coupling_memory(all_cards)
    memory.write_coupling_memory(coupling)
    memory.write_artifact_manifest(memory.build_artifact_manifests(all_cards))
    best = select_current_best(all_cards, memory.read_global_state())["selected_card"]
    state = {
        "updated_at": config.utc_now(),
        "mode": "baseline_suite",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "candidate_id": candidate_id,
        "run_live": run_live,
        "cards_written": len(cards),
        "current_best_run_id": best.get("run_id", "") if best else "",
        "current_best_candidate_id": best.get("observation", {}).get("candidate_id", "") if best else "",
    }
    memory.write_global_state({**memory.read_global_state(), **state})
    memory.write_report("baseline_suite_report.md", "Global Loop Baseline Suite", _cards_report(cards))
    return state


def global_step(candidate_id: str, run_live: bool = False) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    if run_live:
        rc = runner_adapter.run_candidate_suite(candidate_id, list(DOFS), continue_on_failure=True)
        if rc != 0:
            raise RuntimeError(f"global candidate suite failed for {candidate_id} with rc={rc}")

    state = memory.read_global_state()
    cards = memory.load_run_cards()
    parent = _require_clean_current_best(cards, state)
    parent_obj = parent.get("objective")
    imported = runner_adapter.import_registry_run_cards(
        candidate_ids=[candidate_id],
        stage="global_step",
        parent_objective=parent_obj,
    )
    if not imported:
        raise RuntimeError(f"candidate has no registry metrics to import: {candidate_id}")

    card = imported[0]
    objective = score_observation(card.observation, config.load_config(), parent_objective=parent_obj)
    step_decision = verdict_from_objective(objective, parent_objective=parent_obj)
    loop_verdict = loop_verdict_from_step(step_decision, objective, plateau_count=_plateau_count(cards))
    review_status = "pending" if loop_verdict.status == "DIAGNOSTIC_FORK" else "not_required"
    step_run_id = f"RC_STEP_{candidate_id}_{_stamp()}"
    card = RunCard(
        run_id=step_run_id,
        parent_id=parent.get("run_id", "") if parent else card.parent_id,
        stage="global_step",
        parameter_group=card.parameter_group,
        parameter_delta=card.parameter_delta,
        input_hash=card.input_hash,
        target_source=card.target_source,
        observation=card.observation,
        objective=objective,
        verdict=step_decision,
        artifact_policy=runner_adapter.artifact_policy_for(
            candidate_id,
            {},
            {},
            loop_verdict.status if loop_verdict.status == "DIAGNOSTIC_FORK" else step_decision.status,
        ),
        created_at=config.utc_now(),
        evidence_sources=card.evidence_sources,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        predicate_results=objective.get("predicate_results", []),
        step_decision=step_decision,
        loop_verdict=loop_verdict,
        source_roles=_source_roles(),
        review_status=review_status,
    )
    memory.write_run_card(card)

    all_cards = memory.load_run_cards()
    coupling = memory.build_coupling_memory(all_cards)
    memory.write_coupling_memory(coupling)
    memory.write_artifact_manifest(memory.build_artifact_manifests(all_cards))
    memory.update_proposal_history()
    next_state = {
        **state,
        "updated_at": config.utc_now(),
        "mode": "global_step",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "last_candidate_id": candidate_id,
        "last_step_decision": to_plain(step_decision),
        "last_verdict": to_plain(step_decision),
        "loop_verdict": to_plain(loop_verdict),
        "last_score": objective.get("global_score"),
        "run_cards_total": len(all_cards),
    }
    if step_decision.status == "ACCEPTED" and loop_verdict.status != "DIAGNOSTIC_FORK":
        next_state["current_best_run_id"] = card.run_id
        next_state["current_best_candidate_id"] = candidate_id
        next_state["current_best_score"] = objective.get("global_score")
    memory.write_global_state(next_state)
    memory.write_report("global_step_report.md", "Global Loop Step", _cards_report([to_plain(card)]))
    return next_state


generator = config._load_script("03_generate_candidates.py", "active_workflow_candidate_generator_for_global_loop")


def local_polish(target_dof: str | None = None, top_k: int = 1) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    cards = memory.load_run_cards()
    state = _with_latest_run_state(memory.read_global_state(), cards)
    _require_clean_current_best(cards, state)
    pool = local_loop.build_proposal_pool(target_dof=target_dof, top_k=top_k)
    memory.write_proposal_pool(pool)
    memory.write_proposal_history(memory.build_proposal_history(memory.load_run_cards(), to_plain(pool)))
    harness_assessment = harness_mod.build_harness_assessment()
    harness_paths = harness_mod.write_assessment_outputs(harness_assessment)
    report = _proposal_report(pool)
    memory.write_report("local_proposal_report.md", "Global Loop Local Proposal Pool", report)
    top = pool.proposals[: max(1, int(top_k))]
    materializable = [p.proposal_id for p in pool.proposals if p.action.registration_ready]
    next_state = {
        **state,
        "updated_at": config.utc_now(),
        "mode": "local_polish",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "recommended_dof": top[0].target_dof if top else "",
        "recommended_parameter_group": top[0].action.parameter_group if top else "",
        "proposal_pool_path": f"10_global_memory/epochs/{manifest.oracle_id}/proposal_pool.json",
        "top_proposal_ids": [p.proposal_id for p in top],
        "materializable_proposal_ids": materializable,
        "proposal_count": len(pool.proposals),
        "harness_status_path": harness_paths["status"].relative_to(config.workspace_root()).as_posix(),
        **harness_mod.summary(harness_assessment),
        "local_polish_verdict": {"status": "RUNNING", "reason": "local polish recommendation issued", "tags": ["local_polish"]},
    }
    memory.write_global_state(next_state)
    return next_state


def materialize_proposal(proposal_id: str) -> dict[str, Any]:
    state = memory.read_global_state()
    current_best = _require_clean_current_best(memory.load_run_cards(), state)
    pool = memory.read_proposal_pool()
    if not pool:
        raise RuntimeError("no proposal pool available; run --local-polish first")
    proposal = _proposal_by_id(pool, proposal_id)
    action = proposal.get("action", {})
    if not action.get("registration_ready"):
        raise RuntimeError(f"proposal is not registration-ready: {proposal_id}: {action.get('reason_not_ready', '')}")
    _validate_materializable_action(action)
    parent_candidate = proposal.get("parent_candidate_id", "")
    if parent_candidate != current_best.get("observation", {}).get("candidate_id", ""):
        raise RuntimeError(
            "proposal parent candidate does not match clean current best: "
            f"{parent_candidate} != {current_best.get('observation', {}).get('candidate_id', '')}"
        )
    if proposal.get("parent_run_id", "") != current_best.get("run_id", ""):
        raise RuntimeError(
            "proposal parent RunCard does not match clean current best: "
            f"{proposal.get('parent_run_id', '')} != {current_best.get('run_id', '')}"
        )
    harness_assessment = harness_mod.build_harness_assessment()
    harness_paths = harness_mod.write_assessment_outputs(harness_assessment)
    harness_status = harness_mod.summary(harness_assessment)
    harness_warnings: list[str] = []
    if harness_status.get("stop_auto_materialize") or harness_status.get("harness_recommendation") == "STOP_AUTO_MATERIALIZE":
        harness_warnings.append(
            "harness STOP_AUTO_MATERIALIZE is active for the current proposal pool; v3.1 records this as evidence warning for explicit materialization"
        )
    parent = _candidate_row(parent_candidate)
    if not parent:
        raise RuntimeError(f"parent candidate not found in registry: {parent_candidate}")
    candidate_id = f"{proposal_id}_{proposal.get('target_dof', '').replace('FD_', '')}_{_stamp()}"
    vector = action.get("vector", {})
    meta = vector.setdefault("decision_meta", {})
    parent_meta = _candidate_meta(parent)
    if parent_meta.get("dof_packages") and not meta.get("dof_packages"):
        meta["dof_packages"] = parent_meta["dof_packages"]
    if parent_meta.get("openfast_exe") and not meta.get("openfast_exe"):
        meta["openfast_exe"] = parent_meta["openfast_exe"]
    meta.update({
        "proposal_id": proposal_id,
        "proposal_parent_run_id": proposal.get("parent_run_id", ""),
        "proposal_target_dof": proposal.get("target_dof", ""),
        "proposal_action_type": action.get("action_type", ""),
        "proposal_priority": proposal.get("priority"),
        "proposal_hypothesis": proposal.get("hypothesis", ""),
        "candidate_intent": proposal.get("candidate_intent") or meta.get("candidate_intent") or {},
        "intent": (proposal.get("candidate_intent") or {}).get("intent") or proposal.get("hypothesis", ""),
        "expected_observation": (proposal.get("candidate_intent") or {}).get("expected_observation", ""),
        "falsification_condition": (proposal.get("candidate_intent") or {}).get("falsification_condition", ""),
        "memory_policy": "evidence_only_no_veto",
        "harness_warnings": harness_warnings,
    })
    row = generator.register_candidate(
        candidate_id,
        vector,
        stage="local_loop_proposal",
        parent=parent_candidate,
        status="READY",
        notes=f"materialized local proposal {proposal_id}; full 6DOF validation required",
        created_by="20_global_calibration_loop.py",
        model_input_dir=parent.get("model_input_dir") or None,
        openfast_subdir_override=parent.get("openfast_subdir") or None,
        fst_file_override=parent.get("fst_file") or None,
    )
    state = {
        **state,
        "updated_at": config.utc_now(),
        "mode": "materialize_proposal",
        "proposal_id": proposal_id,
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_candidate,
        "registration_ready": True,
        "registered_candidate_row": row,
        "harness_status_path": harness_paths["status"].relative_to(config.workspace_root()).as_posix(),
        "harness_warnings": harness_warnings,
        "memory_policy": "evidence_only_no_veto",
        "next_step": f"run full validation with --global-step --candidate {candidate_id}",
    }
    memory.write_global_state(state)
    memory.update_proposal_history()
    memory.write_report("materialized_proposal_report.md", "Materialized Local Proposal", [
        f"- Proposal: `{proposal_id}`",
        f"- Candidate: `{candidate_id}`",
        f"- Parent candidate: `{parent_candidate}`",
        f"- Target DOF: `{proposal.get('target_dof', '')}`",
        f"- Action: `{action.get('action_type', '')}`",
        f"- Parameter group: `{action.get('parameter_group', '')}`",
        f"- Intent: {meta.get('intent', '')}",
        f"- Expected observation: {meta.get('expected_observation', '')}",
        f"- Falsification condition: {meta.get('falsification_condition', '')}",
        f"- Memory policy: `{state.get('memory_policy')}`",
        f"- Harness warnings: `{'; '.join(harness_warnings) if harness_warnings else 'none'}`",
        "- OpenFAST was not run by materialization.",
        f"- Next: `python 03_scripts/20_global_calibration_loop.py --global-step --candidate {candidate_id} --run-live`",
    ])
    return state


def propose_candidate(group: str, parent: str | None = None) -> dict[str, Any]:
    if group not in PARAMETER_GROUPS:
        raise ValueError(f"group must be one of {', '.join(PARAMETER_GROUPS)}")
    state = memory.read_global_state()
    parent_id = parent or state.get("current_best_candidate_id") or "STARTING_MODEL"
    candidate_id = f"GLV2_{group.upper()}_{config.utc_now().replace(':', '').replace('-', '').replace('+', 'Z')}"
    vector = _proposal_vector(group)
    args = [
        "python",
        "03_scripts/03_generate_candidates.py",
        "--candidate",
        candidate_id,
        "--stage",
        f"global_loop_v2_{group}",
        "--parent",
        parent_id,
        "--vector-json",
        json.dumps(vector, separators=(",", ":"), ensure_ascii=False),
        "--notes",
        f"global_loop_v2 proposal group={group}; full 6DOF validation required",
    ]
    report = [
        f"- Candidate id: `{candidate_id}`",
        f"- Parent: `{parent_id}`",
        f"- Parameter group: `{group}`",
        "- This command is intentionally not executed by the proposal mode:",
        f"  `{ ' '.join(args) }`",
        "- Edit vector values before registration when the physical parameter delta is selected.",
    ]
    memory.write_report("candidate_proposal_report.md", "Global Loop Candidate Proposal", report)
    return {
        **state,
        "updated_at": config.utc_now(),
        "mode": "propose_candidate",
        "candidate_id": candidate_id,
        "parent_candidate_id": parent_id,
        "parameter_group": group,
        "registration_command": args,
    }


def final_validate(candidate_id: str | None = None, run_live: bool = False) -> dict[str, Any]:
    state = memory.read_global_state()
    current_best = _require_clean_current_best(memory.load_run_cards(), state)
    candidate = candidate_id or current_best.get("observation", {}).get("candidate_id", "")
    if not candidate:
        raise RuntimeError("no candidate available for final validation")
    manifest = oracle.ensure_current_manifest()
    if run_live:
        rc = runner_adapter.run_candidate_suite(candidate, list(DOFS), continue_on_failure=True)
        if rc != 0:
            raise RuntimeError(f"final validation suite failed for {candidate} with rc={rc}")
    imported = runner_adapter.import_registry_run_cards(candidate_ids=[candidate], stage="final_validate")
    if not imported:
        raise RuntimeError(f"candidate has no PASS/SUCCESS registry metrics to validate: {candidate}")
    base_card = imported[0]
    objective = score_observation(base_card.observation, config.load_config(), parent_objective=None)
    step_decision = verdict_from_objective(objective, parent_objective=None)
    loop_verdict = loop_verdict_from_step(step_decision, objective)
    final_card = RunCard(
        run_id=f"RC_FINAL_{candidate}_{_stamp()}",
        parent_id=current_best.get("run_id", ""),
        stage="final_validate",
        parameter_group=base_card.parameter_group,
        parameter_delta=base_card.parameter_delta,
        input_hash=base_card.input_hash,
        target_source=base_card.target_source,
        observation=base_card.observation,
        objective=objective,
        verdict=step_decision,
        artifact_policy="retain_full_final_validation",
        created_at=config.utc_now(),
        evidence_sources=base_card.evidence_sources,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        predicate_results=objective.get("predicate_results", []),
        step_decision=step_decision,
        loop_verdict=loop_verdict,
        source_roles=_source_roles(),
        review_status="pending" if loop_verdict.status == "DIAGNOSTIC_FORK" else "not_required",
    )
    memory.write_run_card(final_card)
    all_cards = memory.load_run_cards()
    memory.write_coupling_memory(memory.build_coupling_memory(all_cards))
    memory.write_artifact_manifest(memory.build_artifact_manifests(all_cards))
    artifact_map_path = memory.write_run_artifact_map(to_plain(final_card))
    plot_state = _write_final_validation_plots(candidate, final_card.run_id)
    result = {
        **state,
        "updated_at": config.utc_now(),
        "mode": "final_validate",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "final_validation_candidate_id": candidate,
        "last_candidate_id": candidate,
        "last_step_decision": to_plain(step_decision),
        "last_verdict": to_plain(step_decision),
        "loop_verdict": to_plain(loop_verdict),
        "last_score": objective.get("global_score"),
        "final_validation_run_id": final_card.run_id,
        "artifact_policy": "retain_full_final_validation",
        "artifact_map_path": artifact_map_path.relative_to(config.workspace_root()).as_posix(),
        **plot_state,
        "run_cards_total": len(all_cards),
    }
    if _is_clean_complete_card(to_plain(final_card)):
        result["current_best_run_id"] = final_card.run_id
        result["current_best_candidate_id"] = candidate
        result["current_best_score"] = objective.get("global_score")
    memory.write_global_state(result)
    memory.write_report("final_validation_report.md", "Global Loop Final Validation", [
        f"- Candidate: `{candidate}`",
        f"- Step decision: `{result.get('last_step_decision', {}).get('status', '')}`",
        f"- Loop verdict: `{result.get('loop_verdict', {}).get('status', '')}`",
        f"- Score: {result.get('last_score')}",
        f"- Artifact map: `{result.get('artifact_map_path', '')}`",
        f"- Plot report: `{result.get('final_validation_plot_report_path', '')}`",
    ])
    return result


def _offline_report(new_cards: list[RunCard], all_cards: list[dict[str, Any]], best: dict[str, Any] | None) -> list[str]:
    return [
        f"- New RunCards written: {len(new_cards)}",
        f"- Total RunCards available: {len(all_cards)}",
        f"- Current best candidate: `{best.get('observation', {}).get('candidate_id', '') if best else 'none'}`",
        f"- Current best RunCard: `{best.get('run_id', '') if best else 'none'}`",
        f"- Current best score: {best.get('objective', {}).get('global_score') if best else 'n/a'}",
        f"- Oracle epoch: `{oracle.ensure_current_manifest().oracle_id}`",
        "- Source: active workflow `candidate_registry.csv`, `run_registry.csv`, `results_registry.csv`.",
    ]


def _cards_report(cards: list[Any]) -> list[str]:
    lines: list[str] = []
    for raw in cards:
        card = raw if isinstance(raw, dict) else to_plain(raw)
        verdict = memory.run_card_step_decision(card)
        loop = memory.run_card_loop_verdict(card)
        lines.extend([
            f"- RunCard: `{card.get('run_id', '')}`",
            f"  Candidate: `{memory.run_card_candidate_id(card)}`",
            f"  Stage: `{card.get('stage', '')}`",
            f"  Parameter group: `{card.get('parameter_group', '')}`",
            f"  Score: {memory.run_card_objective(card).get('global_score')}",
            f"  Step decision: `{verdict.get('status', '')}` - {verdict.get('reason', '')}",
            f"  Loop verdict: `{loop.get('status', '')}` - {loop.get('reason', '')}",
            "",
        ])
    return lines or ["- No cards."]


def _proposal_report(pool: Any) -> list[str]:
    data = to_plain(pool)
    guard = data.get("guard_context", {})
    lines = [
        f"- Oracle: `{data.get('oracle_id', '')}`",
        f"- Parent RunCard: `{data.get('parent_run_id', '')}`",
        f"- Parent candidate: `{data.get('parent_candidate_id', '')}`",
        f"- Open DOFs: `{','.join(guard.get('open_dofs', []))}`",
        f"- Protected DOFs: `{','.join(guard.get('protected_dofs', []))}`",
        f"- Fragile DOFs: `{','.join(guard.get('fragile_dofs', []))}`",
        "- Memory policy: `evidence_only_no_veto`",
        "- Local proposals rank and explain candidates; only full 6DOF global-step can accept or reject.",
        "",
        "| Rank | Proposal | Target | Action | Ready | Priority | Status | Intent | Warnings |",
        "|---:|---|---|---|---|---:|---|---|---|",
    ]
    for idx, proposal in enumerate(data.get("proposals", []), start=1):
        action = proposal.get("action", {})
        intent = proposal.get("candidate_intent") or {}
        meta = (action.get("vector") or {}).get("decision_meta") or {}
        warnings = meta.get("memory_warnings") or []
        lines.append(
            f"| {idx} | `{proposal.get('proposal_id', '')}` | `{proposal.get('target_dof', '')}` | "
            f"`{action.get('action_type', '')}` | `{action.get('registration_ready')}` | "
            f"{float(proposal.get('priority', 0.0)):.3f} | `{proposal.get('status', '')}` | "
            f"{intent.get('intent') or proposal.get('hypothesis', '')} | {'; '.join(warnings) if warnings else 'none'} |"
        )
    return lines


def select_current_best(cards: list[dict[str, Any]], state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = state or {}
    preferred_run_id = str(state.get("current_best_run_id", ""))
    state_card = _card_by_run_id(cards, preferred_run_id)
    recomputed = _best_complete_card(cards, preferred_run_id=preferred_run_id)
    selected = recomputed or state_card
    drift = False
    reason = ""
    if state.get("current_best_run_id") and not state_card:
        drift = True
        reason = "state current_best RunCard is missing in current epoch"
    elif state_card and not _is_clean_complete_card(state_card):
        drift = True
        reason = "state current_best is not clean and complete"
    elif state_card and recomputed and state_card.get("run_id") != recomputed.get("run_id"):
        drift = True
        reason = "state current_best differs from recomputed clean best"
    return {
        "state_card": state_card,
        "recomputed_card": recomputed,
        "selected_card": selected if selected and _is_clean_complete_card(selected) else recomputed,
        "drift": drift,
        "reason": reason,
    }


def _require_clean_current_best(cards: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    last_reason = ""
    current_cards = cards
    current_state = state
    for attempt in range(3):
        best_info = select_current_best(current_cards, current_state)
        selected = best_info.get("selected_card")
        if selected and _is_clean_complete_card(selected):
            return selected
        last_reason = str(best_info.get("reason") or "no clean complete card selected")
        if attempt < 2:
            time.sleep(0.25)
            current_state = memory.read_global_state()
            current_cards = memory.load_run_cards()
    raise RuntimeError(
        "no clean complete current_best available; run --offline-import after fixing registry evidence"
        f" ({last_reason})"
    )


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
    return min(tied, key=lambda c: (
        str(c.get("created_at", "")),
        str(c.get("run_id", "")),
    ))


def _is_clean_complete_card(card: dict[str, Any]) -> bool:
    objective = memory.run_card_objective(card)
    per = memory.run_card_per_dof(card)
    score_value = memory.run_card_global_score(card)
    return bool(
        len(per) == len(DOFS)
        and all(dof in per for dof in DOFS)
        and not objective.get("hard_constraint_violations", [])
        and score_value < 1e98
    )


def _card_by_run_id(cards: list[dict[str, Any]], run_id: str) -> dict[str, Any] | None:
    if not run_id:
        return None
    for card in cards:
        if card.get("run_id") == run_id:
            return card
    return None


def _candidate_row(candidate_id: str) -> dict[str, str] | None:
    for row in config.read_csv(config.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    return None


def _candidate_meta(candidate: dict[str, str]) -> dict[str, Any]:
    try:
        vector = json.loads(candidate.get("parameter_vector_json") or "{}")
    except json.JSONDecodeError:
        return {}
    meta = vector.get("decision_meta") or {}
    return meta if isinstance(meta, dict) else {}


def _proposal_by_id(pool: dict[str, Any], proposal_id: str) -> dict[str, Any]:
    for proposal in pool.get("proposals", []):
        if proposal.get("proposal_id") == proposal_id:
            return proposal
    raise RuntimeError(f"proposal not found: {proposal_id}")


def _validate_materializable_action(action: dict[str, Any]) -> None:
    action_type = str(action.get("action_type", ""))
    vector = action.get("vector") or {}
    if action_type not in {"bquad_small_step", "fp_bquad_probe", "ler_bquad_probe"}:
        raise RuntimeError(f"local proposal action is not materializable by the guarded path: {action_type}")
    edits = vector.get("hydrodyn_edits") or []
    if len(edits) != 1:
        raise RuntimeError(f"{action_type} must contain exactly one HydroDyn edit")
    edit = edits[0]
    if edit.get("block") != "BQuad" or int(edit.get("i", -1)) != int(edit.get("j", -2)):
        raise RuntimeError(f"{action_type} must edit one diagonal BQuad entry")
    if "value" not in edit:
        raise RuntimeError(f"{action_type} is missing a target value")


def _worst_dof(card: dict[str, Any]) -> tuple[str, float]:
    per = card.get("objective", {}).get("per_dof", {})
    worst = ("", -1.0)
    for dof, metrics in per.items():
        try:
            score = float(metrics.get("score", -1.0))
        except (TypeError, ValueError):
            score = -1.0
        if score > worst[1]:
            worst = (dof, score)
    return worst


def _oracle_report(state: dict[str, Any]) -> list[str]:
    manifest = state.get("oracle_manifest", {})
    return [
        f"- Oracle id: `{state.get('oracle_id', '')}`",
        f"- Epoch id: `{state.get('epoch_id', '')}`",
        f"- Config hash: `{manifest.get('config_hash', '')}`",
        f"- Starting model tree hash: `{manifest.get('starting_model_tree_hash', '')}`",
        f"- Reference files: {len(manifest.get('reference_files', {}))}",
    ]


def _plateau_count(cards: list[dict[str, Any]]) -> int:
    recent = cards[-3:]
    return sum(1 for card in recent if (card.get("step_decision") or card.get("verdict") or {}).get("status") == "REJECTED")


def _source_roles() -> dict[str, str]:
    return {
        "target_truth": "oracle",
        "observation": "observation",
        "objective": "derived_metric",
        "coupling_memory": "memory_inference",
    }


def _proposal_vector(group: str) -> dict[str, Any]:
    vector = {
        "hydrodyn_edits": [],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {"global_loop_v2_group": group, "requires_full_sixdof": True},
    }
    if group == "mooring":
        vector["moordyn"] = {"proposal_placeholder": "select physical mooring delta before registration"}
    elif group == "rna_balance":
        vector["initial_conditions"] = {"proposal_placeholder": "select RNA balance delta before registration"}
    elif group == "diagonal_hydro":
        vector["decision_meta"]["diagonal_hydro_polish"] = True
    elif group == "coupling_disabled":
        vector["decision_meta"]["coupling_disabled_probe"] = True
    return vector


def _stamp() -> str:
    return (
        config.utc_now()
        .replace("-", "")
        .replace(":", "")
        .replace("+", "Z")
        .replace(".", "")
    )


def _write_final_validation_plots(candidate: str, run_id: str) -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    figures_dir = oracle.epoch_reports_dir(manifest.oracle_id) / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    plot_report = oracle.epoch_reports_dir(manifest.oracle_id) / f"sixdof_comparison_{_safe_plot_name(run_id)}.md"
    out: dict[str, str] = {}
    errors: list[str] = []
    try:
        plot6 = config._load_script("21_plot_candidate_sixdof_normalized.py", f"plot6_{_safe_plot_name(run_id)}")
        for raw_overlay, label in ((False, "release_normalized"), (True, "raw")):
            result = plot6.plot_candidate(candidate, raw_overlay=raw_overlay)
            src = config.resolve_workspace_path(result["figure"])
            dst = figures_dir / f"sixdof_comparison_{_safe_plot_name(run_id)}_{label}.png"
            shutil.copy2(src, dst)
            out[f"final_validation_{label}_figure_path"] = dst.relative_to(config.workspace_root()).as_posix()
    except Exception as exc:
        errors.append(str(exc))

    lines = [
        f"- Candidate: `{candidate}`",
        f"- RunCard: `{run_id}`",
        "- Source: latest successful six-DOF registry rows for the candidate.",
    ]
    if errors:
        lines.append(f"- Plot generation error: `{'; '.join(errors)}`")
    for key in ("final_validation_release_normalized_figure_path", "final_validation_raw_figure_path"):
        if out.get(key):
            rel = out[key]
            lines.extend(["", f"## {key.replace('final_validation_', '').replace('_figure_path', '').replace('_', ' ').title()}", "", f"![{key}](figures/{rel.rsplit('/', 1)[-1]})"])
    plot_report.write_text("\n".join(["# Final Validation Six-DOF Comparison", "", *lines, ""]) + "\n", encoding="utf-8")
    out["final_validation_plot_report_path"] = plot_report.relative_to(config.workspace_root()).as_posix()
    if errors:
        out["final_validation_plot_errors"] = errors
    return out


def _safe_plot_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value).strip("_") or "run"
