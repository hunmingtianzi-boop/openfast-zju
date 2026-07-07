from __future__ import annotations

import argparse
import json
from typing import Any

from global_loop import supervisor


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Global six-DOF calibration loop supervisor.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--init-oracle", action="store_true", help="Create or refresh the oracle manifest and current epoch.")
    mode.add_argument("--status", action="store_true", help="Show the current oracle epoch, best run, and memory status.")
    mode.add_argument("--project-score", action="store_true", help="Score current project readiness without running OpenFAST.")
    mode.add_argument("--workflow-benchmark", action="store_true", help="Benchmark workflow health and scheduler readiness without running OpenFAST.")
    mode.add_argument("--harness-status", action="store_true", help="Assess active calibration campaign efficiency without running OpenFAST.")
    mode.add_argument("--offline-import", action="store_true", help="Import active registry metrics into global memory.")
    mode.add_argument("--baseline-suite", action="store_true", help="Run/import a six-DOF baseline suite.")
    mode.add_argument("--global-step", action="store_true", help="Arbitrate one global candidate step.")
    mode.add_argument("--local-polish", action="store_true", help="Recommend the next local polish target.")
    mode.add_argument("--propose-candidate", action="store_true", help="Write a parameter-group candidate proposal report.")
    mode.add_argument("--materialize-proposal", default="", help="Register one local proposal as a candidate without running OpenFAST.")
    mode.add_argument("--final-validate", action="store_true", help="Run/import final validation for the current best or selected candidate.")
    mode.add_argument("--memory-rebuild", action="store_true", help="Rebuild v3.1 memory: truth snapshot, artifacts, transitions, relations, proposal history.")
    mode.add_argument("--memory-report", action="store_true", help="Write a human-readable v3.1 evidence-harness memory report.")
    mode.add_argument("--memory-gc", action="store_true", help="Plan or apply artifact cleanup from v3 artifact manifest.")
    mode.add_argument("--query-memory", action="store_true", help="Query RelationMemory without running OpenFAST.")
    mode.add_argument("--llm-pre-run", action="store_true", help="Generate an advisory LLM pre-run packet/report for a candidate.")
    mode.add_argument("--llm-during-run", action="store_true", help="Generate an advisory LLM during-run historical analysis packet/report.")
    mode.add_argument("--llm-post-run", action="store_true", help="Generate an advisory LLM post-run packet/report for a RunCard.")
    mode.add_argument("--llm-synthesize", action="store_true", help="Synthesize LLM analyst packets for a RunCard.")
    mode.add_argument("--llm-review-proposals", action="store_true", help="Generate an advisory review of the current ProposalPool.")
    mode.add_argument("--diagnostic-refresh", action="store_true", help="Refresh the deterministic Diagnostic Fork Queue.")
    mode.add_argument("--diagnostic-review", action="store_true", help="Generate an advisory LLM review for one diagnostic queue item.")
    parser.add_argument("--candidate", default="", help="Candidate id for baseline/global-step or comma-filtered offline import.")
    parser.add_argument("--group", default="", help="Parameter group for --propose-candidate/query-memory.")
    parser.add_argument("--target-dof", default="", help="Limit --local-polish/query-memory to one DOF.")
    parser.add_argument("--top-k", type=int, default=1, help="Number of local proposals to recommend.")
    parser.add_argument("--run-live", action="store_true", help="Execute OpenFAST via existing active workflow runner before import.")
    parser.add_argument("--dry-run", action="store_true", help="For --memory-gc, only write cleanup plan. This is the default.")
    parser.add_argument("--apply", action="store_true", help="For --memory-gc, execute cleanup for eligible artifacts.")
    parser.add_argument("--metric", default="", help="Metric filter for --query-memory, e.g. fp_error or score.")
    parser.add_argument("--action-type", default="", help="Action type filter for --query-memory.")
    parser.add_argument("--run-id", default="", help="RunCard id for --llm-post-run/--llm-synthesize.")
    parser.add_argument("--latest", action="store_true", help="Use the latest RunCard for --llm-post-run.")
    parser.add_argument("--item-id", default="", help="Diagnostic queue item id for --diagnostic-review.")
    parser.add_argument("--call-model", action="store_true", help="Call configured LLM provider after writing the packet.")
    parser.add_argument("--json", action="store_true", help="Print full JSON state.")
    args = parser.parse_args()

    if args.init_oracle:
        state = supervisor.init_oracle()
    elif args.status:
        state = supervisor.status()
    elif args.project_score:
        state = supervisor.project_score()
    elif args.workflow_benchmark:
        state = supervisor.workflow_benchmark()
    elif args.harness_status:
        state = supervisor.harness_status()
    elif args.offline_import:
        candidate_ids = _split_csv(args.candidate) if args.candidate else None
        state = supervisor.offline_import(candidate_ids=candidate_ids)
    elif args.baseline_suite:
        state = supervisor.baseline_suite(candidate_id=args.candidate or "CAND_TEST", run_live=args.run_live)
    elif args.global_step:
        if not args.candidate:
            raise SystemExit("--global-step requires --candidate")
        state = supervisor.global_step(candidate_id=args.candidate, run_live=args.run_live)
    elif args.local_polish:
        state = supervisor.local_polish(target_dof=args.target_dof or None, top_k=args.top_k)
    elif args.propose_candidate:
        if not args.group:
            raise SystemExit("--propose-candidate requires --group")
        state = supervisor.propose_candidate(group=args.group, parent=args.candidate or None)
    elif args.materialize_proposal:
        state = supervisor.materialize_proposal(args.materialize_proposal)
    elif args.final_validate:
        state = supervisor.final_validate(candidate_id=args.candidate or None, run_live=args.run_live)
    elif args.memory_rebuild:
        state = supervisor.memory_rebuild()
    elif args.memory_report:
        state = supervisor.memory_status_report()
    elif args.memory_gc:
        if args.dry_run and args.apply:
            raise SystemExit("--memory-gc accepts either --dry-run or --apply, not both")
        state = supervisor.memory_gc_plan(apply=bool(args.apply))
    elif args.llm_pre_run:
        if not args.candidate:
            raise SystemExit("--llm-pre-run requires --candidate")
        state = supervisor.llm_pre_run(args.candidate, call_model=args.call_model)
    elif args.llm_during_run:
        if not args.candidate:
            raise SystemExit("--llm-during-run requires --candidate")
        state = supervisor.llm_during_run(args.candidate, call_model=args.call_model)
    elif args.llm_post_run:
        if not args.run_id and not args.latest:
            raise SystemExit("--llm-post-run requires --run-id or --latest")
        state = supervisor.llm_post_run(run_id=args.run_id, latest=args.latest, call_model=args.call_model)
    elif args.llm_synthesize:
        if not args.run_id:
            raise SystemExit("--llm-synthesize requires --run-id")
        state = supervisor.llm_synthesize(args.run_id, call_model=args.call_model)
    elif args.llm_review_proposals:
        state = supervisor.llm_review_proposals(call_model=args.call_model)
    elif args.diagnostic_refresh:
        state = supervisor.diagnostic_refresh()
    elif args.diagnostic_review:
        if not args.item_id:
            raise SystemExit("--diagnostic-review requires --item-id")
        state = supervisor.diagnostic_review(args.item_id, call_model=args.call_model)
    else:
        state = supervisor.query_memory(
            dof=args.target_dof or "",
            metric=args.metric or "",
            parameter_group=args.group or "",
            action_type=args.action_type or "",
        )

    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False, default=_json_default))
    else:
        _print_human_state(state)
    return 0


def _json_default(value: Any) -> str:
    return str(value)


def _print_human_state(state: dict[str, Any]) -> None:
    print(f"global_loop mode={state.get('mode')} updated={state.get('updated_at')}")
    if state.get("project_score") is not None:
        mode = "snapshot" if state.get("project_score_mode") == "snapshot" else "live"
        print(f"Project score v{state.get('project_score_version')} ({mode}) = {state.get('project_score')}")
        print(f"Project status = {state.get('project_delivery_status')}")
        if state.get("project_active_gates"):
            print("Project active gates = " + ",".join(state.get("project_active_gates", [])))
    if state.get("workflow_benchmark_score") is not None:
        mode = "snapshot" if state.get("workflow_benchmark_mode") == "snapshot" else "live"
        print(f"Workflow benchmark v{state.get('workflow_benchmark_version')} ({mode}) = {state.get('workflow_benchmark_score')}")
        print(f"Benchmark confidence = {state.get('workflow_benchmark_confidence')}")
        print(f"Benchmark status = {state.get('workflow_benchmark_status')}")
        scheduler = state.get("scheduler_recommendation") or {}
        if scheduler:
            print(f"Scheduler recommendation = {scheduler.get('recommendation')} - {scheduler.get('main_issue')}")
            if scheduler.get("reason"):
                print(f"Scheduler reason = {scheduler.get('reason')}")
        active = state.get("active_decision_summary") or {}
        if active:
            print(
                "active loop = "
                f"status={active.get('status')} count={active.get('active_decision_count')} "
                f"score={active.get('score')} confidence={active.get('confidence')}"
            )
        historical = state.get("historical_evidence_summary") or {}
        if historical:
            print(
                "historical evidence = "
                f"cards={historical.get('historical_card_count')} "
                f"diagnostic_rate={historical.get('diagnostic_rate')}"
            )
        proposal = state.get("proposal_readiness") or {}
        if proposal:
            print(
                "proposal readiness = "
                f"{proposal.get('status')} top={proposal.get('top_proposal_id')} "
                f"materializable={','.join(proposal.get('materializable_proposal_ids', []))}"
            )
    if state.get("harness_status") is not None:
        print(f"Harness status = {state.get('harness_status')}")
        print(f"Harness recommendation = {state.get('harness_recommendation')}")
        print(f"Mean information gain = {state.get('mean_information_gain')}")
        print(f"Active attempts = {state.get('active_attempt_count')}")
        print(f"Dominant campaign = {state.get('dominant_campaign_id')}")
        print(f"Stop auto materialize = {state.get('stop_auto_materialize')}")
        if state.get("harness_reason"):
            print(f"Harness reason = {state.get('harness_reason')}")
    if state.get("current_best_candidate_id"):
        print(f"current_best={state.get('current_best_candidate_id')} score={state.get('current_best_score')}")
    if state.get("last_candidate_id"):
        verdict = state.get("last_step_decision") or state.get("last_verdict", {})
        loop = state.get("loop_verdict", {})
        print(f"last_candidate={state.get('last_candidate_id')} step={verdict.get('status')} loop={loop.get('status')} score={state.get('last_score')}")
    if state.get("recommended_dof"):
        print(f"recommended_dof={state.get('recommended_dof')} group={state.get('recommended_parameter_group')}")
    if state.get("oracle_id"):
        print(f"oracle_id={state.get('oracle_id')}")
    if state.get("epoch", {}).get("root"):
        print(f"epoch_root={state.get('epoch', {}).get('root')}")
    for key in (
        "project_score_path",
        "project_score_report_path",
        "workflow_benchmark_path",
        "workflow_benchmark_report_path",
        "harness_status_path",
        "campaign_memory_path",
        "harness_report_path",
        "diagnostic_queue_path",
        "diagnostic_queue_report_path",
        "llm_packet_path",
        "llm_report_path",
        "artifact_map_path",
        "final_validation_plot_report_path",
        "final_validation_release_normalized_figure_path",
        "final_validation_raw_figure_path",
    ):
        if state.get(key):
            print(f"{key}={state.get(key)}")
    if state.get("llm_job_id"):
        print(
            "llm_analysis="
            f"{state.get('llm_analysis_type')} status={state.get('llm_status')} "
            f"job={state.get('llm_job_id')} provider_called={state.get('provider_called')}"
        )
    if state.get("diagnostic_queue_count") is not None:
        print(f"diagnostic_queue_count={state.get('diagnostic_queue_count')}")
        if state.get("diagnostic_item_ids"):
            print("diagnostic_items=" + ",".join(state.get("diagnostic_item_ids", [])))
    if state.get("registration_command"):
        print("registration_command=" + " ".join(state.get("registration_command", [])))
    if state.get("top_proposal_ids"):
        print("top_proposals=" + ",".join(state.get("top_proposal_ids", [])))
    if state.get("materializable_proposal_ids"):
        print("materializable_proposals=" + ",".join(state.get("materializable_proposal_ids", [])))
    if state.get("mode") == "materialize_proposal" and state.get("proposal_id") and state.get("candidate_id"):
        print(f"materialized_proposal={state.get('proposal_id')} candidate={state.get('candidate_id')}")
    if state.get("mode") == "memory_rebuild":
        print(f"memory_rebuild transitions={state.get('transition_count')} relations={state.get('relation_count')}")
    if state.get("mode") == "memory_report":
        print(f"memory_report_path={state.get('memory_report_path')}")
    if state.get("mode") == "memory_gc":
        print(
            "memory_gc "
            f"apply={state.get('memory_gc_apply')} "
            f"candidates={state.get('memory_gc_candidate_count')} "
            f"deleted={state.get('memory_gc_deleted_count')} "
            f"bytes={state.get('memory_gc_candidate_bytes')}"
        )
    if state.get("mode") == "query_memory":
        print(f"query_memory matches={state.get('match_count')}")
        bundle = state.get("evidence_bundle") or {}
        if bundle:
            print(
                "evidence_bundle="
                f"support={bundle.get('support_run_count')} "
                f"refute={bundle.get('refute_run_count')} "
                f"mean_confidence={bundle.get('mean_confidence')} "
                f"no_veto={bundle.get('no_veto')}"
            )
            if bundle.get("status_counts"):
                print("evidence_status=" + ",".join(
                    f"{key}:{value}" for key, value in sorted(bundle.get("status_counts", {}).items())
                ))
            for gap in bundle.get("gaps", [])[:3]:
                print(f"evidence_gap={gap}")
            for guide in bundle.get("guidance", [])[:2]:
                print(f"evidence_guidance={guide}")
        for item in state.get("matches", [])[:5]:
            print(
                f"- {item.get('status')} {item.get('action_type')} "
                f"{item.get('target_dof')}->{item.get('response_dof')}.{item.get('metric')} "
                f"effect={item.get('effect_size')} confidence={item.get('confidence')}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
