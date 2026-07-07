from __future__ import annotations

from collections import Counter
from typing import Any

from . import config, memory, oracle, runner_adapter
from .schema import to_plain


def rebuild_all() -> dict[str, Any]:
    cfg = config.load_config()
    manifest = oracle.ensure_current_manifest(cfg)
    target = runner_adapter.build_target_truth(cfg)
    truth_path = memory.write_truth_snapshot(target, manifest)
    cards = memory.load_run_cards()
    artifacts = memory.build_artifact_manifests(cards)
    artifact_path = memory.write_artifact_manifest(artifacts)
    transitions = memory.build_transition_cards(cards)
    transition_index_path = memory.write_transition_cards(transitions)
    relation = memory.build_relation_memory([to_plain(item) for item in transitions])
    relation_path = memory.write_relation_memory(relation)
    proposal_history = memory.build_proposal_history(cards, memory.read_proposal_pool())
    proposal_history_path = memory.write_proposal_history(proposal_history)
    report_path = memory.write_report(
        "memory_rebuild_report.md",
        "Global Loop Memory Rebuild",
        rebuild_report_lines(
            len(cards),
            len(artifacts),
            len(transitions),
            len(relation.entries),
            len(proposal_history),
        ),
    )
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "memory_rebuild",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "run_cards_total": len(cards),
        "artifact_manifest_count": len(artifacts),
        "transition_count": len(transitions),
        "relation_count": len(relation.entries),
        "proposal_history_count": len(proposal_history),
        "truth_snapshot_path": truth_path.relative_to(config.workspace_root()).as_posix(),
        "artifact_manifest_path": artifact_path.relative_to(config.workspace_root()).as_posix(),
        "transition_index_path": transition_index_path.relative_to(config.workspace_root()).as_posix(),
        "relation_memory_path": relation_path.relative_to(config.workspace_root()).as_posix(),
        "proposal_history_path": proposal_history_path.relative_to(config.workspace_root()).as_posix(),
        "memory_rebuild_report_path": report_path.relative_to(config.workspace_root()).as_posix(),
    }


def query(dof: str = "", metric: str = "", parameter_group: str = "", action_type: str = "") -> dict[str, Any]:
    relation = memory.read_relation_memory()
    entries = relation.get("entries", {})
    matches = []
    for entry in entries.values():
        if dof and dof not in {entry.get("target_dof"), entry.get("response_dof")}:
            continue
        if metric and metric != entry.get("metric"):
            continue
        if parameter_group and parameter_group != entry.get("parameter_group"):
            continue
        if action_type and action_type != entry.get("action_type"):
            continue
        matches.append(entry)
    matches.sort(key=_relation_sort_key)
    bundle = _evidence_bundle(matches, dof=dof, metric=metric, parameter_group=parameter_group, action_type=action_type)
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "query_memory",
        "oracle_id": oracle.ensure_current_manifest().oracle_id,
        "query": {
            "dof": dof,
            "metric": metric,
            "parameter_group": parameter_group,
            "action_type": action_type,
        },
        "match_count": len(matches),
        "evidence_bundle": bundle,
        "memory_policy": {
            "role": "evidence_only",
            "no_veto": True,
            "acceptance_gate": "full_6dof_global_step",
            "truth_source": "oracle_harness",
        },
        "matches": matches[:20],
    }


def rebuild_report_lines(
    cards: int,
    artifacts: int,
    transitions: int,
    relations: int,
    proposals: int,
) -> list[str]:
    return [
        f"- RunCards scanned: {cards}",
        f"- Artifact manifests: {artifacts}",
        f"- TransitionCards built: {transitions}",
        f"- RelationMemory entries: {relations}",
        f"- ProposalHistory entries: {proposals}",
        "- No OpenFAST run was executed.",
        "- RelationMemory is inference only; global-step remains the only current_best writer.",
    ]


def _relation_sort_key(item: dict[str, Any]) -> tuple[int, float, float]:
    rank = {
        "supported": 0,
        "fragile": 1,
        "observed": 2,
        "refuted": 3,
    }.get(str(item.get("status", "")), 4)
    return (rank, -float(item.get("confidence", 0.0)), -abs(float(item.get("effect_size", 0.0))))


def _evidence_bundle(
    matches: list[dict[str, Any]],
    dof: str = "",
    metric: str = "",
    parameter_group: str = "",
    action_type: str = "",
) -> dict[str, Any]:
    status_counts = Counter(str(item.get("status") or "unknown") for item in matches)
    support_ids = sorted({
        str(run_id)
        for item in matches
        for run_id in item.get("support_run_ids", [])
        if run_id
    })
    refute_ids = sorted({
        str(run_id)
        for item in matches
        for run_id in item.get("refute_run_ids", [])
        if run_id
    })
    risk_counts = Counter(
        str(tag)
        for item in matches
        for tag in item.get("risk_tags", [])
        if tag
    )
    direct = [
        item for item in matches
        if (not dof or (item.get("target_dof") == dof and item.get("response_dof") == dof))
        and (not metric or item.get("metric") == metric)
    ]
    gaps: list[str] = []
    if not matches:
        gaps.append("no matching RelationMemory entries in current oracle epoch")
    if dof and metric and not direct:
        gaps.append(f"no direct self-response relation for {dof}.{metric}")
    if matches and not support_ids:
        gaps.append("no supporting transitions for this query")
    if matches and not refute_ids:
        gaps.append("no refuting transitions for this query")
    if matches and support_ids and refute_ids:
        gaps.append("mixed support/refute evidence; treat as fragile unless full 6DOF validation confirms")

    top_risks = [
        {"tag": tag, "count": count}
        for tag, count in risk_counts.most_common(12)
    ]
    top_relations = [
        {
            "relation_id": item.get("relation_id", ""),
            "status": item.get("status", ""),
            "action_type": item.get("action_type", ""),
            "parameter_group": item.get("parameter_group", ""),
            "target_dof": item.get("target_dof", ""),
            "response_dof": item.get("response_dof", ""),
            "metric": item.get("metric", ""),
            "effect_size": item.get("effect_size"),
            "confidence": item.get("confidence"),
            "support_count": len(item.get("support_run_ids", [])),
            "refute_count": len(item.get("refute_run_ids", [])),
        }
        for item in matches[:8]
    ]
    confidence_values = [float(item.get("confidence", 0.0) or 0.0) for item in matches]
    return {
        "query": {
            "dof": dof,
            "metric": metric,
            "parameter_group": parameter_group,
            "action_type": action_type,
        },
        "match_count": len(matches),
        "status_counts": dict(sorted(status_counts.items())),
        "support_run_count": len(support_ids),
        "refute_run_count": len(refute_ids),
        "support_run_ids_sample": support_ids[:10],
        "refute_run_ids_sample": refute_ids[:10],
        "common_risk_tags": top_risks,
        "top_relations": top_relations,
        "mean_confidence": round(sum(confidence_values) / len(confidence_values), 4) if confidence_values else 0.0,
        "gaps": gaps,
        "guidance": [
            "RelationMemory is advisory evidence, not a veto.",
            "Legal candidates remain materializable; full 6DOF global-step is the acceptance gate.",
            "Support/refute counts are historical under this oracle epoch, not truth drift.",
        ],
        "recommendation_semantics": "rank_and_explain_only",
        "no_veto": True,
    }
