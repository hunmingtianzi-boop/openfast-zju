from __future__ import annotations

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
