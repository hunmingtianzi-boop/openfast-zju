from __future__ import annotations

import hashlib
import json
from typing import Any

from . import config, memory, oracle
from .schema import DOFS, DOF_TO_CHANNEL, DiagnosticForkItem, DiagnosticQueue


TRIGGER_PREFIXES = (
    "strong_leak:",
    "equilibrium_out_of_bounds:",
    "period_reverse_worse:",
    "regressed_closed_dof:",
)


def build_queue(cards: list[dict[str, Any]] | None = None) -> DiagnosticQueue:
    manifest = oracle.ensure_current_manifest()
    cards = cards if cards is not None else memory.load_run_cards()
    items = [_item_for_card(card, manifest.oracle_id) for card in cards]
    items = [item for item in items if item is not None]
    items = sorted(items, key=lambda item: (item.status, item.created_at, item.run_id))
    return DiagnosticQueue(
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        items=items,
    )


def refresh() -> dict[str, Any]:
    queue = build_queue()
    path = memory.write_diagnostic_queue(queue)
    state = memory.read_global_state()
    summary = queue_summary(
        queue,
        current_best_candidate_id=str(state.get("current_best_candidate_id", "")),
        current_best_run_id=str(state.get("current_best_run_id", "")),
    )
    report = memory.write_report(
        "diagnostic_queue_report.md",
        "Global Loop Diagnostic Fork Queue",
        report_lines(queue, summary=summary),
    )
    return {
        "current_best_run_id": state.get("current_best_run_id", ""),
        "current_best_candidate_id": state.get("current_best_candidate_id", ""),
        "current_best_score": state.get("current_best_score"),
        "updated_at": config.utc_now(),
        "mode": "diagnostic_refresh",
        "oracle_id": queue.oracle_id,
        "epoch_id": queue.epoch_id,
        "diagnostic_queue_count": len(queue.items),
        "diagnostic_queue_summary": summary,
        "diagnostic_active_item_count": summary.get("active_item_count", 0),
        "diagnostic_actionable_item_count": summary.get("actionable_item_count", 0),
        "diagnostic_queue_path": path.relative_to(config.workspace_root()).as_posix(),
        "diagnostic_queue_report_path": report.relative_to(config.workspace_root()).as_posix(),
        "diagnostic_item_ids": [item.item_id for item in queue.items],
        "diagnostic_actionable_item_ids": summary.get("top_actionable_item_ids", []),
    }


def item_by_id(item_id: str) -> dict[str, Any]:
    queue = memory.read_diagnostic_queue()
    for item in queue.get("items", []):
        if item.get("item_id") == item_id:
            return item
    raise RuntimeError(f"diagnostic queue item not found: {item_id}")


def queue_summary(queue: DiagnosticQueue | dict[str, Any], current_best_candidate_id: str = "", current_best_run_id: str = "") -> dict[str, Any]:
    items = _queue_items(queue)
    pending = [item for item in items if item.get("status", "pending") == "pending"]
    active = [item for item in pending if item.get("source_category") == "active"]
    current = [
        item for item in pending
        if item.get("candidate_id") == current_best_candidate_id or item.get("run_id") == current_best_run_id
    ]
    actionable: dict[str, dict[str, Any]] = {}
    for item in active + current:
        actionable[str(item.get("item_id", ""))] = item
    top_actionable = sorted(
        actionable.values(),
        key=lambda item: (-float(item.get("priority") or 0), str(item.get("created_at", "")), str(item.get("run_id", ""))),
    )
    return {
        "total_item_count": len(items),
        "pending_item_count": len(pending),
        "active_item_count": len(active),
        "current_best_item_count": len(current),
        "actionable_item_count": len(top_actionable),
        "by_source_category": _count_by(items, "source_category"),
        "by_review_type": _count_by(items, "review_type"),
        "by_severity": _count_by(items, "severity"),
        "top_actionable_item_ids": [str(item.get("item_id", "")) for item in top_actionable[:10]],
        "top_actionable_items": top_actionable[:10],
        "noise_note": "Full historical diagnostic evidence is retained in diagnostic_queue.json; reports prioritize active/current pending items.",
    }


def report_lines(queue: DiagnosticQueue | dict[str, Any], summary: dict[str, Any] | None = None) -> list[str]:
    data = queue if isinstance(queue, dict) else {
        "oracle_id": queue.oracle_id,
        "epoch_id": queue.epoch_id,
        "items": [item.__dict__ for item in queue.items],
    }
    summary = summary or queue_summary(data)
    active_items = summary.get("top_actionable_items", [])
    lines = [
        f"- Oracle: `{data.get('oracle_id', '')}`",
        f"- Epoch: `{data.get('epoch_id', '')}`",
        f"- Items: {summary.get('total_item_count', len(data.get('items', [])))}",
        f"- Pending: {summary.get('pending_item_count', 0)}",
        f"- Active/current actionable: {summary.get('actionable_item_count', 0)}",
        f"- Source categories: `{json.dumps(summary.get('by_source_category', {}), ensure_ascii=False)}`",
        f"- Review types: `{json.dumps(summary.get('by_review_type', {}), ensure_ascii=False)}`",
        "",
        "## Active / Current Review Queue",
        "",
        "These are the items to inspect first. Full historical evidence remains in `diagnostic_queue.json`.",
        "",
        "| Item | Priority | Source | Candidate | Run | Type | DOFs | Reasons |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    if not active_items:
        lines.append("|  |  |  |  |  |  |  | No active/current pending diagnostic item. |")
    for item in active_items:
        lines.append(
            f"| `{item.get('item_id', '')}` | {float(item.get('priority') or 0):.1f} | "
            f"`{item.get('source_category', '')}` | `{item.get('candidate_id', '')}` | "
            f"`{item.get('run_id', '')}` | `{item.get('review_type', '')}` | "
            f"`{','.join(item.get('related_dofs', []))}` | "
            f"{'; '.join(item.get('trigger_reasons', []))} |"
        )
    lines.extend([
        "",
        "## Historical Evidence Summary",
        "",
        "Historical items are kept for memory/relation evidence but are not all listed here.",
    ])
    for key, value in sorted((summary.get("by_source_category") or {}).items()):
        lines.append(f"- {key}: {value}")
    return lines


def _queue_items(queue: DiagnosticQueue | dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(queue, dict):
        return list(queue.get("items", []) or [])
    return [item.__dict__ for item in queue.items]


def _item_for_card(card: dict[str, Any], oracle_id: str) -> DiagnosticForkItem | None:
    triggers = _trigger_reasons(card)
    if not triggers:
        return None
    run_id = str(card.get("run_id", ""))
    candidate_id = str(card.get("observation", {}).get("candidate_id", ""))
    stage = str(card.get("stage") or "")
    source_category = _source_category(card)
    item_id = _item_id(run_id, candidate_id, triggers)
    return DiagnosticForkItem(
        item_id=item_id,
        created_at=str(card.get("created_at") or config.utc_now()),
        oracle_id=oracle_id,
        epoch_id=oracle_id,
        run_id=run_id,
        candidate_id=candidate_id,
        trigger_reasons=triggers,
        related_dofs=_related_dofs(triggers),
        review_type=_review_type(triggers, card),
        severity=_severity(triggers),
        status=str(card.get("review_status") or "pending"),
        source_stage=stage,
        source_category=source_category,
        priority=_priority(triggers, card, source_category),
    )


def _trigger_reasons(card: dict[str, Any]) -> list[str]:
    reasons: set[str] = set()
    loop = card.get("loop_verdict") or {}
    if loop.get("status") == "DIAGNOSTIC_FORK":
        reasons.add(f"loop_verdict:DIAGNOSTIC_FORK:{loop.get('reason', '')}")
    for value in card.get("objective", {}).get("hard_constraint_violations", []) or []:
        if _is_queue_trigger(str(value)):
            reasons.add(str(value))
    for value in (card.get("step_decision") or card.get("verdict") or {}).get("hard_constraint_violations", []) or []:
        if _is_queue_trigger(str(value)):
            reasons.add(str(value))
    for pred in card.get("predicate_results", []) or []:
        if not isinstance(pred, dict) or pred.get("passed", True):
            continue
        reason = str(pred.get("reason", "") or pred.get("name", ""))
        if _is_queue_trigger(reason):
            reasons.add(reason)
    return sorted(reasons)


def _is_queue_trigger(value: str) -> bool:
    return value.startswith(TRIGGER_PREFIXES)


def _related_dofs(triggers: list[str]) -> list[str]:
    out: set[str] = set()
    channel_to_dof = {channel.upper(): dof for dof, channel in DOF_TO_CHANNEL.items()}
    for text in triggers:
        upper = text.upper()
        for dof in DOFS:
            if dof in upper:
                out.add(dof)
        for channel, dof in channel_to_dof.items():
            if channel in upper:
                out.add(dof)
    return sorted(out)


def _review_type(triggers: list[str], card: dict[str, Any]) -> str:
    joined = " ".join(triggers)
    if "strong_leak:" in joined:
        return "leak_diagnostic"
    if "equilibrium_out_of_bounds:" in joined:
        return "equilibrium_diagnostic"
    if "period_reverse_worse:" in joined:
        return "period_reverse_diagnostic"
    if "regressed_closed_dof:" in joined:
        return "protected_regression_review"
    if (card.get("loop_verdict") or {}).get("status") == "DIAGNOSTIC_FORK":
        return "diagnostic_fork_review"
    return "diagnostic_review"


def _severity(triggers: list[str]) -> str:
    joined = " ".join(triggers)
    if "strong_leak:" in joined or "equilibrium_out_of_bounds:" in joined:
        return "diagnostic"
    if "period_reverse_worse:" in joined:
        return "warning"
    return "review"


def _source_category(card: dict[str, Any]) -> str:
    stage = str(card.get("stage") or "")
    if stage == "offline_import":
        return "historical_import"
    if stage:
        return "active"
    return "historical"


def _priority(triggers: list[str], card: dict[str, Any], source_category: str) -> float:
    severity_weight = {"diagnostic": 30.0, "warning": 20.0, "review": 10.0}.get(_severity(triggers), 5.0)
    source_weight = 40.0 if source_category == "active" else 0.0
    loop_weight = 10.0 if (card.get("loop_verdict") or {}).get("status") == "DIAGNOSTIC_FORK" else 0.0
    reason_weight = min(10.0, float(len(triggers)))
    return severity_weight + source_weight + loop_weight + reason_weight


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _item_id(run_id: str, candidate_id: str, triggers: list[str]) -> str:
    payload = json.dumps(
        {"run_id": run_id, "candidate_id": candidate_id, "triggers": sorted(triggers)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "DQ_" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12].upper()
