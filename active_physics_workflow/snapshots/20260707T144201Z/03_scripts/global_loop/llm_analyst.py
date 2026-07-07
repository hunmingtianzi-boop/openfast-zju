from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any

from . import config, diagnostic_queue, memory, oracle
from .schema import (
    DOFS,
    LLMAnalysisJob,
    LLMAnalysisPacket,
    LLMAnalysisReport,
    LLM_ANALYSIS_TYPES,
)


SYSTEM_INSTRUCTION = (
    "You are an advisory engineering analyst for a six-DOF OpenFAST calibration loop. "
    "You may explain, diagnose, summarize, and recommend next probes. You must not "
    "modify TargetTruth, override hard predicates, compute official score, promote "
    "current_best, or loosen thresholds. Treat all outputs as advisory only."
)


def pre_run(candidate_id: str, call_model: bool = False) -> dict[str, Any]:
    if not candidate_id:
        raise ValueError("pre_run requires candidate_id")
    context = _base_context()
    context.update({
        "candidate_id": candidate_id,
        "candidate_registry_row": _candidate_row(candidate_id),
        "current_best": _current_best_slim(),
        "proposal_pool": _proposal_pool_summary(memory.read_proposal_pool()),
        "analysis_focus": [
            "State the candidate hypothesis.",
            "Predict likely six-DOF response and protected/fragile DOF risks.",
            "List what result would support or refute the hypothesis.",
        ],
    })
    return _run_analysis("pre_run", context, candidate_id=candidate_id, call_model=call_model)


def during_run(candidate_id: str, call_model: bool = False) -> dict[str, Any]:
    if not candidate_id:
        raise ValueError("during_run requires candidate_id")
    cards = memory.load_run_cards()
    context = _base_context()
    context.update({
        "candidate_id": candidate_id,
        "candidate_registry_row": _candidate_row(candidate_id),
        "recent_rejected_or_diagnostic_cards": _history_cards(cards, limit=12),
        "transition_summary": _transition_summary(memory.read_transition_cards(), limit=20),
        "relation_memory": _relation_summary(memory.read_relation_memory(), limit=20),
        "analysis_focus": [
            "Use OpenFAST wait time to summarize related historical evidence.",
            "Identify recurring hard-constraint and coupling patterns.",
            "Prepare a watchlist for post-run comparison.",
        ],
    })
    return _run_analysis("during_run", context, candidate_id=candidate_id, call_model=call_model)


def post_run(run_id: str = "", latest: bool = False, call_model: bool = False) -> dict[str, Any]:
    card = _select_card(run_id=run_id, latest=latest)
    context = _base_context()
    context.update({
        "run_card": _slim_card(card),
        "pre_run_packets": _related_packets(candidate_id=str(card.get("observation", {}).get("candidate_id", "")), limit=5),
        "analysis_focus": [
            "Compare observed results against the pre-run hypothesis if available.",
            "Explain deterministic step and loop verdicts without overriding them.",
            "Summarize what this evidence contributes to coupling memory.",
        ],
    })
    return _run_analysis(
        "post_run",
        context,
        candidate_id=str(card.get("observation", {}).get("candidate_id", "")),
        run_id=str(card.get("run_id", "")),
        call_model=call_model,
    )


def synthesize(run_id: str, call_model: bool = False) -> dict[str, Any]:
    card = _select_card(run_id=run_id, latest=False)
    candidate_id = str(card.get("observation", {}).get("candidate_id", ""))
    context = _base_context()
    context.update({
        "run_card": _slim_card(card),
        "related_llm_packets": _related_packets(candidate_id=candidate_id, run_id=run_id, limit=10),
        "diagnostic_queue": _diagnostic_queue_summary(memory.read_diagnostic_queue()),
        "proposal_pool": _proposal_pool_summary(memory.read_proposal_pool()),
        "analysis_focus": [
            "Synthesize pre-run, during-run, and post-run evidence.",
            "Recommend exactly one next-action category: global_search, diagnostic_fork, local_proposal, or human_review.",
            "Keep the recommendation advisory and reference deterministic verdicts.",
        ],
    })
    return _run_analysis("synthesis", context, candidate_id=candidate_id, run_id=run_id, call_model=call_model)


def proposal_review(call_model: bool = False) -> dict[str, Any]:
    pool = memory.read_proposal_pool()
    if not pool:
        raise RuntimeError("no proposal pool available; run --local-polish first")
    context = _base_context()
    context.update({
        "proposal_pool": _proposal_pool_summary(pool, include_proposals=True),
        "coupling_memory": _coupling_summary(memory.read_coupling_memory()),
        "harness_status": memory.read_harness_status(),
        "analysis_focus": [
            "Explain the proposal ranking.",
            "Identify why top candidates are materializable or diagnostic-only.",
            "Recommend which proposal should be materialized or reviewed next.",
        ],
    })
    return _run_analysis("proposal_review", context, call_model=call_model)


def diagnostic_review(item_id: str, call_model: bool = False) -> dict[str, Any]:
    if not memory.read_diagnostic_queue():
        queue = diagnostic_queue.build_queue()
        memory.write_diagnostic_queue(queue)
    item = diagnostic_queue.item_by_id(item_id)
    card = _select_card(run_id=str(item.get("run_id", "")), latest=False)
    context = _base_context()
    context.update({
        "diagnostic_item": item,
        "run_card": _slim_card(card),
        "related_history": _history_cards(memory.load_run_cards(), limit=12),
        "analysis_focus": [
            "Diagnose likely mechanism behind the queue trigger.",
            "List the smallest useful probe or artifact review.",
            "State why this item must not auto-promote current_best.",
        ],
    })
    return _run_analysis(
        "diagnostic_review",
        context,
        candidate_id=str(item.get("candidate_id", "")),
        run_id=str(item.get("run_id", "")),
        item_id=item_id,
        call_model=call_model,
    )


def _run_analysis(
    analysis_type: str,
    context: dict[str, Any],
    candidate_id: str = "",
    run_id: str = "",
    item_id: str = "",
    proposal_id: str = "",
    call_model: bool = False,
) -> dict[str, Any]:
    if analysis_type not in LLM_ANALYSIS_TYPES:
        raise ValueError(f"unknown LLM analysis type: {analysis_type}")
    manifest = oracle.ensure_current_manifest()
    context = _bounded_context(context)
    snapshot_hash = _sha256_json(context)
    target = candidate_id or run_id or item_id or proposal_id or "epoch"
    job_id = f"LLMJ_{analysis_type}_{_safe(target)}_{_stamp()}"
    packet_id = f"LLMP_{analysis_type}_{_safe(target)}_{snapshot_hash[:12].upper()}"
    prompt = _build_prompt(analysis_type, context)
    packet = LLMAnalysisPacket(
        packet_id=packet_id,
        job_id=job_id,
        analysis_type=analysis_type,
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        input_snapshot_hash=snapshot_hash,
        prompt=prompt,
        context=context,
        candidate_id=candidate_id,
        run_id=run_id,
        item_id=item_id,
        proposal_id=proposal_id,
        stale_for_decision=_stale_for_decision(context),
    )
    packet_path = memory.write_llm_packet(packet)
    provider_cfg = _provider_config()
    provider_called = False
    error = ""
    status = "packet_only"
    try:
        if call_model and provider_cfg.get("enabled"):
            body = _call_provider(prompt, provider_cfg)
            provider_called = True
            status = "complete"
        elif call_model and not provider_cfg.get("enabled"):
            body = _packet_only_body(packet, "Provider disabled or incomplete; no model call was made.")
            status = "packet_only"
        else:
            body = _packet_only_body(packet, "Model call not requested; generated advisory packet only.")
    except Exception as exc:  # noqa: BLE001 - provider failures must stay advisory-only.
        error = str(exc)
        status = "failed_advisory_only"
        body = _packet_only_body(packet, f"Provider call failed; packet retained. Error: {error}")

    report_name = _report_name(analysis_type, target)
    report_path = memory.write_report(
        report_name,
        f"LLM Analyst {analysis_type.replace('_', ' ').title()}",
        _report_lines(packet, body, provider_cfg, provider_called, error),
    )
    report = LLMAnalysisReport(
        report_id=f"LLMR_{analysis_type}_{_safe(target)}_{snapshot_hash[:12].upper()}",
        job_id=job_id,
        analysis_type=analysis_type,
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        input_snapshot_hash=snapshot_hash,
        report_path=report_path.relative_to(config.workspace_root()).as_posix(),
        packet_path=packet_path.relative_to(config.workspace_root()).as_posix(),
        candidate_id=candidate_id,
        run_id=run_id,
        item_id=item_id,
        proposal_id=proposal_id,
        provider=str(provider_cfg.get("provider", "")),
        model=str(provider_cfg.get("model", "")),
        provider_called=provider_called,
        stale_for_decision=packet.stale_for_decision,
    )
    memory.write_llm_report_index(report)
    job = LLMAnalysisJob(
        job_id=job_id,
        analysis_type=analysis_type,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        status=status,
        candidate_id=candidate_id,
        run_id=run_id,
        item_id=item_id,
        proposal_id=proposal_id,
        packet_path=packet_path.relative_to(config.workspace_root()).as_posix(),
        report_path=report_path.relative_to(config.workspace_root()).as_posix(),
        input_snapshot_hash=snapshot_hash,
        call_model=call_model,
        provider=str(provider_cfg.get("provider", "")),
        model=str(provider_cfg.get("model", "")),
        error=error,
        created_at=packet.created_at,
        updated_at=config.utc_now(),
    )
    memory.write_llm_job(job)
    return {
        **_slim_state(memory.read_global_state()),
        "updated_at": config.utc_now(),
        "mode": f"llm_{analysis_type}",
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "llm_job_id": job.job_id,
        "llm_analysis_type": analysis_type,
        "llm_status": status,
        "llm_packet_path": job.packet_path,
        "llm_report_path": job.report_path,
        "input_snapshot_hash": snapshot_hash,
        "stale_for_decision": packet.stale_for_decision,
        "provider_called": provider_called,
        "provider": job.provider,
        "model": job.model,
        "llm_error": error,
    }


def _base_context() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    state = memory.read_global_state()
    return {
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "advisory_only": True,
        "source_roles": {
            "target_truth": "oracle",
            "observation": "evidence",
            "score": "deterministic_metric",
            "llm_analysis": "advisory",
        },
        "global_state": _slim_state(state),
        "coupling_memory": _coupling_summary(memory.read_coupling_memory()),
    }


def _build_prompt(analysis_type: str, context: dict[str, Any]) -> str:
    context_json = json.dumps(context, indent=2, ensure_ascii=False, sort_keys=True)
    cfg = _provider_config()
    max_chars = int(cfg.get("max_input_chars") or 24000)
    if len(context_json) > max_chars:
        context_json = context_json[:max_chars] + "\n...<truncated>"
    return "\n".join([
        SYSTEM_INSTRUCTION,
        "",
        f"Analysis type: {analysis_type}",
        "Return a concise Markdown report with: Evidence, Interpretation, Risks, Recommended next action.",
        "Do not change deterministic verdicts or present advisory suggestions as official acceptance.",
        "",
        "Context JSON:",
        context_json,
    ])


def _call_provider(prompt: str, provider_cfg: dict[str, Any]) -> str:
    provider = str(provider_cfg.get("provider", "openai_compatible"))
    if provider != "openai_compatible":
        raise RuntimeError(f"unsupported llm_analyst.provider: {provider}")
    api_key = os.environ.get(str(provider_cfg.get("api_key_env", "OPENAI_API_KEY")))
    if not api_key:
        raise RuntimeError(f"missing API key env var: {provider_cfg.get('api_key_env', 'OPENAI_API_KEY')}")
    model = str(provider_cfg.get("model") or "")
    if not model:
        raise RuntimeError("llm_analyst.model is empty")
    url = str(provider_cfg.get("base_url") or "https://api.openai.com/v1/chat/completions")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_INSTRUCTION},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"provider HTTP {exc.code}: {detail[:500]}") from exc
    choices = data.get("choices", [])
    if not choices:
        raise RuntimeError("provider response had no choices")
    message = choices[0].get("message", {})
    content = message.get("content")
    if not content:
        raise RuntimeError("provider response had empty message content")
    return str(content)


def _provider_config() -> dict[str, Any]:
    raw = config.load_config().get("llm_analyst", {})
    if not isinstance(raw, dict):
        raw = {}
    model = str(raw.get("model") or "")
    enabled = bool(raw.get("enabled", False)) and bool(model)
    return {
        "enabled": enabled,
        "provider": raw.get("provider", "openai_compatible"),
        "model": model,
        "api_key_env": raw.get("api_key_env", "OPENAI_API_KEY"),
        "base_url": raw.get("base_url", "https://api.openai.com/v1/chat/completions"),
        "max_input_chars": raw.get("max_input_chars", 24000),
    }


def _packet_only_body(packet: LLMAnalysisPacket, reason: str) -> str:
    return "\n".join([
        "## Packet Generated",
        "",
        reason,
        "",
        f"- Packet id: `{packet.packet_id}`",
        f"- Snapshot hash: `{packet.input_snapshot_hash}`",
        f"- Stale for decision: `{packet.stale_for_decision}`",
        "- Advisory only: `true`",
        "",
        "Use the packet JSON as the model prompt/input if running an external analyst manually.",
    ])


def _report_lines(
    packet: LLMAnalysisPacket,
    body: str,
    provider_cfg: dict[str, Any],
    provider_called: bool,
    error: str,
) -> list[str]:
    lines = [
        f"- Advisory only: `{packet.advisory_only}`",
        f"- Analysis type: `{packet.analysis_type}`",
        f"- Oracle: `{packet.oracle_id}`",
        f"- Epoch: `{packet.epoch_id}`",
        f"- Candidate: `{packet.candidate_id}`",
        f"- Run: `{packet.run_id}`",
        f"- Diagnostic item: `{packet.item_id}`",
        f"- Input snapshot hash: `{packet.input_snapshot_hash}`",
        f"- Stale for decision: `{packet.stale_for_decision}`",
        f"- Provider called: `{provider_called}`",
        f"- Provider: `{provider_cfg.get('provider', '')}`",
        f"- Model: `{provider_cfg.get('model', '')}`",
    ]
    if error:
        lines.append(f"- Provider error: `{error}`")
    lines.extend(["", body])
    return lines


def _select_card(run_id: str = "", latest: bool = False) -> dict[str, Any]:
    cards = memory.load_run_cards()
    if run_id:
        for card in cards:
            if card.get("run_id") == run_id:
                return card
        raise RuntimeError(f"RunCard not found: {run_id}")
    if latest and cards:
        return sorted(cards, key=lambda item: str(item.get("created_at", "")))[-1]
    raise ValueError("run_id is required unless latest=True")


def _current_best_slim() -> dict[str, Any]:
    state = memory.read_global_state()
    run_id = str(state.get("current_best_run_id", ""))
    if not run_id:
        return {}
    try:
        return _slim_card(_select_card(run_id=run_id))
    except RuntimeError:
        return {"missing_current_best_run_id": run_id}


def _candidate_row(candidate_id: str) -> dict[str, str]:
    for row in config.read_csv(config.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    return {}


def _history_cards(cards: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    filtered = []
    for card in cards:
        step = card.get("step_decision") or card.get("verdict") or {}
        loop = card.get("loop_verdict") or {}
        objective = card.get("objective", {})
        if step.get("status") == "REJECTED" or loop.get("status") == "DIAGNOSTIC_FORK" or objective.get("hard_constraint_violations"):
            filtered.append(card)
    return [_slim_card(card) for card in sorted(filtered, key=lambda item: str(item.get("created_at", "")))[-limit:]]


def _slim_card(card: dict[str, Any]) -> dict[str, Any]:
    observation = card.get("observation", {})
    objective = card.get("objective", {})
    return {
        "run_id": card.get("run_id", ""),
        "parent_id": card.get("parent_id", ""),
        "stage": card.get("stage", ""),
        "candidate_id": observation.get("candidate_id", ""),
        "parameter_group": card.get("parameter_group", ""),
        "parameter_delta": card.get("parameter_delta", {}),
        "global_score": objective.get("global_score"),
        "hard_constraint_violations": objective.get("hard_constraint_violations", []),
        "per_dof": objective.get("per_dof", {}),
        "step_decision": card.get("step_decision") or card.get("verdict") or {},
        "loop_verdict": card.get("loop_verdict") or {},
        "predicate_results": card.get("predicate_results", []),
        "review_status": card.get("review_status", ""),
        "observation_metrics": observation.get("per_dof_metrics", {}),
        "cross_energy_matrix": observation.get("cross_energy_matrix", {}),
        "equilibrium": observation.get("equilibrium", {}),
        "created_at": card.get("created_at", ""),
    }


def _slim_state(state: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "updated_at",
        "mode",
        "current_best_run_id",
        "current_best_candidate_id",
        "current_best_score",
        "last_candidate_id",
        "last_step_decision",
        "loop_verdict",
        "last_score",
        "top_proposal_ids",
        "materializable_proposal_ids",
        "harness_status",
        "harness_recommendation",
    )
    return {key: state.get(key) for key in keys if key in state}


def _coupling_summary(coupling: dict[str, Any]) -> dict[str, Any]:
    entries = coupling.get("entries", {}) if isinstance(coupling, dict) else {}
    return {
        "created_at": coupling.get("created_at", "") if isinstance(coupling, dict) else "",
        "entries": entries,
    }


def _proposal_pool_summary(pool: dict[str, Any], include_proposals: bool = False) -> dict[str, Any]:
    if not pool:
        return {}
    proposals = pool.get("proposals", [])
    slim = {
        "created_at": pool.get("created_at", ""),
        "parent_run_id": pool.get("parent_run_id", ""),
        "parent_candidate_id": pool.get("parent_candidate_id", ""),
        "top_k": pool.get("top_k", 1),
        "proposal_count": len(proposals),
        "top_proposals": proposals[: max(1, int(pool.get("top_k", 1)))],
        "materializable_proposal_ids": [
            item.get("proposal_id", "")
            for item in proposals
            if (item.get("action") or {}).get("registration_ready")
        ],
        "guard_context": pool.get("guard_context", {}),
    }
    if include_proposals:
        slim["proposals"] = proposals
    return slim


def _transition_summary(transitions: list[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    out = []
    for item in transitions[-limit:]:
        out.append({
            "transition_id": item.get("transition_id", ""),
            "parent_run_id": item.get("parent_run_id", ""),
            "child_run_id": item.get("child_run_id", ""),
            "parameter_group": item.get("parameter_group", ""),
            "action_type": item.get("action_type", ""),
            "target_dof": item.get("target_dof", ""),
            "global_score_delta": item.get("global_score_delta"),
            "new_failures": item.get("new_failures", []),
            "resolved_failures": item.get("resolved_failures", []),
            "step_decision": item.get("step_decision", {}),
            "loop_verdict": item.get("loop_verdict", {}),
        })
    return out


def _relation_summary(relation: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    entries = list((relation.get("entries", {}) if isinstance(relation, dict) else {}).values())
    entries = sorted(entries, key=lambda item: float(item.get("confidence", 0.0)), reverse=True)
    return {
        "created_at": relation.get("created_at", "") if isinstance(relation, dict) else "",
        "source_transition_count": relation.get("source_transition_count", 0) if isinstance(relation, dict) else 0,
        "entries": entries[:limit],
    }


def _diagnostic_queue_summary(queue: dict[str, Any]) -> dict[str, Any]:
    items = queue.get("items", []) if isinstance(queue, dict) else []
    return {
        "created_at": queue.get("created_at", "") if isinstance(queue, dict) else "",
        "item_count": len(items),
        "items": items[:20],
    }


def _related_packets(candidate_id: str = "", run_id: str = "", limit: int = 5) -> list[dict[str, Any]]:
    packets = []
    for path in sorted(memory.llm_packets_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if candidate_id and data.get("candidate_id") == candidate_id:
            packets.append(_slim_packet(data))
        elif run_id and data.get("run_id") == run_id:
            packets.append(_slim_packet(data))
    return packets[-limit:]


def _slim_packet(packet: dict[str, Any]) -> dict[str, Any]:
    return {
        "packet_id": packet.get("packet_id", ""),
        "job_id": packet.get("job_id", ""),
        "analysis_type": packet.get("analysis_type", ""),
        "candidate_id": packet.get("candidate_id", ""),
        "run_id": packet.get("run_id", ""),
        "input_snapshot_hash": packet.get("input_snapshot_hash", ""),
        "stale_for_decision": packet.get("stale_for_decision", False),
        "created_at": packet.get("created_at", ""),
    }


def _bounded_context(context: dict[str, Any]) -> dict[str, Any]:
    # Keep packet JSON readable; full artifacts stay in RunCards and registries.
    return json.loads(json.dumps(context, ensure_ascii=False, default=str))


def _stale_for_decision(context: dict[str, Any]) -> bool:
    if context.get("oracle_id") != oracle.ensure_current_manifest().oracle_id:
        return True
    state = memory.read_global_state()
    state_best = str(state.get("current_best_run_id", ""))
    context_best = str((context.get("global_state") or {}).get("current_best_run_id", ""))
    return bool(context_best and state_best and context_best != state_best)


def _report_name(analysis_type: str, target: str) -> str:
    return f"llm_{analysis_type}_{_safe(target)}.md"


def _safe(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value)).strip("_")
    return cleaned[:80] or "epoch"


def _stamp() -> str:
    return config.utc_now().replace("-", "").replace(":", "").replace("+", "Z").replace(".", "")


def _sha256_json(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
