from __future__ import annotations

from typing import Any

from . import config, memory, oracle


def build_report() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    cards = memory.load_run_cards()
    artifacts = memory.read_artifact_manifest()
    transitions = memory.read_transition_cards()
    relations = memory.read_relation_memory()
    proposals = memory.read_proposal_history()
    lines = report_lines(cards, artifacts, transitions, relations, proposals)
    report_path = memory.write_report("memory_report.md", "Global Loop Memory Report", lines)
    entries = relations.get("entries", {}) if isinstance(relations, dict) else {}
    artifact_entries = artifacts.get("entries", {}) if isinstance(artifacts, dict) else {}
    return {
        **memory.read_global_state(),
        "updated_at": config.utc_now(),
        "mode": "memory_report",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "run_cards_total": len(cards),
        "artifact_manifest_count": len(artifact_entries),
        "transition_count": len(transitions),
        "relation_count": len(entries),
        "proposal_history_count": len((proposals or {}).get("entries", {})),
        "memory_report_path": report_path.relative_to(config.workspace_root()).as_posix(),
        "top_supported_relations": _top_relations(entries, "supported"),
        "top_fragile_relations": _top_relations(entries, "fragile"),
    }


def report_lines(
    cards: list[dict[str, Any]],
    artifacts: dict[str, Any],
    transitions: list[dict[str, Any]],
    relations: dict[str, Any],
    proposals: dict[str, Any],
) -> list[str]:
    artifact_entries = artifacts.get("entries", {}) if isinstance(artifacts, dict) else {}
    relation_entries = relations.get("entries", {}) if isinstance(relations, dict) else {}
    proposal_entries = proposals.get("entries", {}) if isinstance(proposals, dict) else {}
    retained = _count_by(artifact_entries.values(), "retention_class")
    relation_status = _count_by(relation_entries.values(), "status")
    total_bytes = sum(int(item.get("total_size_bytes") or 0) for item in artifact_entries.values())
    lines = [
        f"- RunCards: {len(cards)}",
        f"- Artifact entries: {len(artifact_entries)}",
        f"- TransitionCards: {len(transitions)}",
        f"- Relation entries: {len(relation_entries)}",
        f"- Proposal history entries: {len(proposal_entries)}",
        f"- Known artifact bytes: {total_bytes}",
        "",
        "## Artifact Retention",
    ]
    lines.extend(_kv_lines(retained))
    lines.extend(["", "## Relation Status"])
    lines.extend(_kv_lines(relation_status))
    lines.extend(["", "## Supported Relations"])
    lines.extend(_relation_lines(_top_relations(relation_entries, "supported")))
    lines.extend(["", "## Fragile Relations"])
    lines.extend(_relation_lines(_top_relations(relation_entries, "fragile")))
    lines.extend(["", "## Proposal Outcomes"])
    lines.extend(_kv_lines(_count_by(proposal_entries.values(), "outcome")))
    return lines


def _top_relations(entries: dict[str, Any], status: str, limit: int = 10) -> list[dict[str, Any]]:
    items = [item for item in entries.values() if item.get("status") == status]
    items.sort(key=lambda item: (float(item.get("confidence", 0.0)), abs(float(item.get("effect_size", 0.0)))), reverse=True)
    return items[:limit]


def _relation_lines(items: list[dict[str, Any]]) -> list[str]:
    if not items:
        return ["- none"]
    return [
        "- "
        f"`{item.get('action_type')}` {item.get('parameter_group')} "
        f"{item.get('target_dof')} -> {item.get('response_dof')}.{item.get('metric')} "
        f"effect={float(item.get('effect_size', 0.0)):.4g} "
        f"confidence={float(item.get('confidence', 0.0)):.2f} "
        f"support={len(item.get('support_run_ids', []))} refute={len(item.get('refute_run_ids', []))}"
        for item in items
    ]


def _count_by(items: Any, key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key, "") or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _kv_lines(values: dict[str, int]) -> list[str]:
    if not values:
        return ["- none"]
    return [f"- {key}: {value}" for key, value in values.items()]
