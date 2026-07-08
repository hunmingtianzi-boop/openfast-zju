from __future__ import annotations

import json
import re
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from . import config, oracle
from .schema import (
    ArtifactManifest,
    CouplingMemory,
    CouplingMemoryEntry,
    DiagnosticQueue,
    DOFS,
    CampaignState,
    HarnessAssessment,
    LLMAnalysisJob,
    LLMAnalysisPacket,
    LLMAnalysisReport,
    MemoryEpoch,
    PARAMETER_GROUPS,
    ProposalPool,
    ProposalHistoryEntry,
    RelationMemory,
    RelationMemoryEntry,
    RunCard,
    TargetTruth,
    TransitionCard,
    TruthSnapshot,
    to_plain,
)


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_")
    return safe or "unnamed"


def current_epoch() -> MemoryEpoch:
    manifest = oracle.ensure_current_manifest()
    root = oracle.epoch_root(manifest.oracle_id)
    return MemoryEpoch(
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        root=root.relative_to(config.workspace_root()).as_posix(),
        created_at=manifest.created_at,
        state_path=(root / "global_state.json").relative_to(config.workspace_root()).as_posix(),
        run_cards_path=oracle.epoch_runcards_dir(manifest.oracle_id).relative_to(config.workspace_root()).as_posix(),
        reports_path=oracle.epoch_reports_dir(manifest.oracle_id).relative_to(config.workspace_root()).as_posix(),
    )


def artifacts_dir(oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    return oracle.epoch_root(oid) / "artifacts"


def transitions_dir(oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    return oracle.epoch_root(oid) / "transitions"


def llm_jobs_dir(oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    path = oracle.epoch_root(oid) / "llm_jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def llm_packets_dir(oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    path = oracle.epoch_root(oid) / "llm_packets"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_card_path(run_id: str, oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    return oracle.epoch_runcards_dir(oid) / f"{_safe_name(run_id)}.json"


def legacy_run_card_path(run_id: str) -> Path:
    return config.runcards_dir() / f"{_safe_name(run_id)}.json"


def write_run_card(card: RunCard) -> Path:
    manifest = oracle.ensure_current_manifest()
    plain = _attach_oracle_fields(to_plain(card), manifest.oracle_id)
    oracle.assert_card_oracle(str(plain.get("oracle_id", "")), manifest)
    path = run_card_path(str(plain.get("run_id", card.run_id)), manifest.oracle_id)
    path.write_text(json.dumps(plain, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_truth_snapshot(target_truth: TargetTruth, manifest: Any | None = None) -> Path:
    manifest = manifest or oracle.ensure_current_manifest()
    snapshot = TruthSnapshot(
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        target_truth=target_truth,
        oracle_manifest=manifest,
        source_roles={
            "target_truth": "oracle",
            "oracle_manifest": "oracle",
            "truth_snapshot": "immutable_epoch_snapshot",
        },
    )
    path = oracle.epoch_root(manifest.oracle_id) / "truth_snapshot.json"
    payload = json.dumps(to_plain(snapshot), indent=2, ensure_ascii=False)
    path.write_text(payload, encoding="utf-8")
    (config.global_memory_root() / "truth_snapshot.json").write_text(payload, encoding="utf-8")
    return path


def read_truth_snapshot() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "truth_snapshot.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_run_cards(oracle_id: str | None = None, include_legacy: bool = False) -> list[dict[str, Any]]:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    root = oracle.epoch_runcards_dir(oid)
    cards: list[dict[str, Any]] = []
    if root.exists():
        for path in sorted(root.glob("*.json")):
            cards.append(json.loads(path.read_text(encoding="utf-8")))
    if include_legacy:
        cards.extend(_legacy_cards_not_in(cards))
    return cards


def run_card_observation(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("observation", {})
    return value if isinstance(value, dict) else {}


def run_card_objective(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("objective", {})
    return value if isinstance(value, dict) else {}


def run_card_candidate_id(card: dict[str, Any]) -> str:
    return str(run_card_observation(card).get("candidate_id", ""))


def run_card_global_score(card: dict[str, Any], default: float = 1e99) -> float:
    try:
        return float(run_card_objective(card).get("global_score", default))
    except (TypeError, ValueError):
        return default


def run_card_per_dof(card: dict[str, Any]) -> dict[str, Any]:
    value = run_card_objective(card).get("per_dof", {})
    return value if isinstance(value, dict) else {}


def run_card_step_decision(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("step_decision") or card.get("verdict") or {}
    return value if isinstance(value, dict) else {}


def run_card_loop_verdict(card: dict[str, Any]) -> dict[str, Any]:
    value = card.get("loop_verdict") or {}
    return value if isinstance(value, dict) else {}


def run_card_artifact_paths(card: dict[str, Any]) -> list[str]:
    return _artifact_paths(card)


def write_run_artifact_map(card: dict[str, Any] | RunCard) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(card)
    run_id = str(payload.get("run_id", ""))
    paths = run_card_artifact_paths(payload)
    entries: list[dict[str, Any]] = []
    for rel_path in paths:
        path = config.workspace_root() / rel_path
        entries.append({
            "path": rel_path,
            "exists": path.exists(),
            "kind": "dir" if path.is_dir() else "file" if path.is_file() else "missing",
            "size_bytes": _path_size(path),
        })
    artifact_map = {
        "created_at": config.utc_now(),
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "run_id": run_id,
        "candidate_id": run_card_candidate_id(payload),
        "stage": payload.get("stage", ""),
        "artifact_paths": entries,
        "source_role": "artifact_index",
    }
    path = artifacts_dir(manifest.oracle_id) / f"{_safe_name(run_id)}_artifact_map.json"
    path.write_text(json.dumps(artifact_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def build_artifact_manifests(cards: list[dict[str, Any]]) -> list[ArtifactManifest]:
    manifest = oracle.ensure_current_manifest()
    current_state = read_global_state()
    current_best = str(current_state.get("current_best_run_id", ""))
    out: list[ArtifactManifest] = []
    for card in cards:
        policy = str(card.get("artifact_policy", "summary_only") or "summary_only")
        run_id = str(card.get("run_id", ""))
        candidate_id = str(card.get("observation", {}).get("candidate_id", ""))
        retain_reason = _artifact_retain_reason(card, current_best)
        retained = retain_reason != "summary_only"
        paths = _artifact_paths(card)
        size = _artifact_paths_size(paths)
        out.append(ArtifactManifest(
            run_id=run_id,
            candidate_id=candidate_id,
            oracle_id=manifest.oracle_id,
            epoch_id=manifest.oracle_id,
            artifact_policy=policy,
            retention_class=retain_reason,
            raw_output_retained=retained,
            retain_reason=retain_reason,
            metrics_extracted=bool(card.get("objective", {}).get("per_dof")),
            artifact_paths=paths,
            total_size_bytes=size,
            sha256=_sha256_json({"run_id": run_id, "paths": paths, "size": size, "policy": policy}),
            delete_eligible=(retain_reason == "summary_only" and bool(card.get("objective", {}).get("per_dof"))),
            created_at=config.utc_now(),
        ))
    return out


def write_artifact_manifest(manifests: list[ArtifactManifest]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = {
        "created_at": config.utc_now(),
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "entries": {item.run_id: to_plain(item) for item in manifests},
    }
    path = artifacts_dir(manifest.oracle_id) / "artifact_manifest.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "artifact_manifest.json").write_text(text, encoding="utf-8")
    return path


def read_artifact_manifest() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = artifacts_dir(manifest.oracle_id) / "artifact_manifest.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def migrate_legacy_run_cards(overwrite: bool = False) -> int:
    manifest = oracle.ensure_current_manifest()
    if not config.runcards_dir().exists():
        return 0
    migrated = 0
    for path in sorted(config.runcards_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data = _attach_oracle_fields(data, manifest.oracle_id)
        target = run_card_path(str(data.get("run_id", path.stem)), manifest.oracle_id)
        if target.exists() and not overwrite:
            continue
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        migrated += 1
    return migrated


def build_transition_cards(cards: list[dict[str, Any]]) -> list[TransitionCard]:
    manifest = oracle.ensure_current_manifest()
    by_run = {str(card.get("run_id", "")): card for card in cards if card.get("run_id")}
    by_candidate = _candidate_parent_index(cards)
    transitions: list[TransitionCard] = []
    for child in cards:
        parent = _parent_card_for(child, by_run, by_candidate)
        if not parent:
            continue
        child_run = str(child.get("run_id", ""))
        parent_run = str(parent.get("run_id", ""))
        transition_id = "TR_" + hashlib.sha1(f"{parent_run}->{child_run}".encode("utf-8")).hexdigest()[:16].upper()
        transitions.append(TransitionCard(
            transition_id=transition_id,
            parent_run_id=parent_run,
            child_run_id=child_run,
            parent_candidate_id=str(parent.get("observation", {}).get("candidate_id", "")),
            child_candidate_id=str(child.get("observation", {}).get("candidate_id", "")),
            oracle_id=manifest.oracle_id,
            epoch_id=manifest.oracle_id,
            parameter_group=str(child.get("parameter_group", "")),
            action_type=_action_type(child),
            target_dof=_target_dof(child),
            parameter_delta=dict(child.get("parameter_delta", {}) or {}),
            global_score_delta=_score_delta(parent, child),
            metric_deltas=_metric_deltas(parent, child),
            new_failures=sorted(_failure_set(child) - _failure_set(parent)),
            resolved_failures=sorted(_failure_set(parent) - _failure_set(child)),
            step_decision=dict(child.get("step_decision") or child.get("verdict") or {}),
            loop_verdict=dict(child.get("loop_verdict") or {}),
            source_run_ids=[parent_run, child_run],
            created_at=config.utc_now(),
        ))
    return transitions


def write_transition_cards(transitions: list[TransitionCard]) -> Path:
    manifest = oracle.ensure_current_manifest()
    root = transitions_dir(manifest.oracle_id)
    for old in root.glob("TR_*.json"):
        old.unlink()
    index: dict[str, Any] = {
        "created_at": config.utc_now(),
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "count": len(transitions),
        "transitions": [],
    }
    for card in transitions:
        payload = to_plain(card)
        path = root / f"{_safe_name(card.transition_id)}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        index["transitions"].append({
            "transition_id": card.transition_id,
            "parent_run_id": card.parent_run_id,
            "child_run_id": card.child_run_id,
            "parameter_group": card.parameter_group,
            "action_type": card.action_type,
            "target_dof": card.target_dof,
            "path": path.relative_to(config.workspace_root()).as_posix(),
        })
    index_path = root / "transition_index.json"
    text = json.dumps(index, indent=2, ensure_ascii=False)
    index_path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "transition_index.json").write_text(text, encoding="utf-8")
    return index_path


def read_transition_cards() -> list[dict[str, Any]]:
    root = transitions_dir()
    cards: list[dict[str, Any]] = []
    if not root.exists():
        return cards
    index_path = root / "transition_index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for item in index.get("transitions", []):
            rel_path = item.get("path", "")
            path = config.workspace_root() / rel_path if rel_path else root / f"{_safe_name(item.get('transition_id', ''))}.json"
            if path.exists():
                cards.append(json.loads(path.read_text(encoding="utf-8")))
        return cards
    for path in sorted(root.glob("TR_*.json")):
        cards.append(json.loads(path.read_text(encoding="utf-8")))
    return cards


def write_diagnostic_queue(queue: DiagnosticQueue | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(queue)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    payload["source_role"] = payload.get("source_role") or "diagnostic_evidence"
    path = oracle.epoch_root(manifest.oracle_id) / "diagnostic_queue.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    (config.global_memory_root() / "diagnostic_queue.json").write_text(text + "\n", encoding="utf-8")
    return path


def read_diagnostic_queue() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "diagnostic_queue.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    top = config.global_memory_root() / "diagnostic_queue.json"
    if top.exists():
        data = json.loads(top.read_text(encoding="utf-8"))
        if data.get("oracle_id") == manifest.oracle_id or data.get("epoch_id") == manifest.oracle_id:
            return data
    return {}


def write_llm_job(job: LLMAnalysisJob | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(job)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    payload["source_role"] = payload.get("source_role") or "advisory"
    path = llm_jobs_dir(manifest.oracle_id) / f"{_safe_name(str(payload.get('job_id', 'job')))}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_llm_jobs() -> list[dict[str, Any]]:
    root = llm_jobs_dir()
    return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(root.glob("LLMJ_*.json"))]


def write_llm_packet(packet: LLMAnalysisPacket | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(packet)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    payload["advisory_only"] = True
    payload["source_role"] = payload.get("source_role") or "advisory"
    path = llm_packets_dir(manifest.oracle_id) / f"{_safe_name(str(payload.get('packet_id', 'packet')))}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_llm_packet(packet_id: str) -> dict[str, Any]:
    path = llm_packets_dir() / f"{_safe_name(packet_id)}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_llm_report_index(report: LLMAnalysisReport | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(report)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    payload["advisory_only"] = True
    payload["source_role"] = payload.get("source_role") or "advisory"
    path = llm_jobs_dir(manifest.oracle_id) / f"{_safe_name(str(payload.get('report_id', 'report')))}.report.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_global_state(state: dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    state = {**state, "oracle_id": manifest.oracle_id, "epoch_id": manifest.oracle_id}
    path = _epoch_state_path(manifest.oracle_id)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    top_path = config.global_memory_root() / "global_state.json"
    top_path.write_text(
        json.dumps(_top_state_summary(state, manifest.oracle_id), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def read_global_state() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = _epoch_state_path(manifest.oracle_id)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    top = config.global_memory_root() / "global_state.json"
    if top.exists():
        data = json.loads(top.read_text(encoding="utf-8"))
        if data.get("oracle_id") == manifest.oracle_id or data.get("current_oracle_id") == manifest.oracle_id:
            return data
    return {}


def write_coupling_memory(memory: CouplingMemory) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(memory)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    oracle.assert_card_oracle(str(payload.get("oracle_id", "")), manifest)
    path = oracle.epoch_root(manifest.oracle_id) / "coupling_memory.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "coupling_memory.json").write_text(text, encoding="utf-8")
    return path


def read_coupling_memory() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "coupling_memory.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_relation_memory(transitions: list[dict[str, Any] | TransitionCard]) -> RelationMemory:
    manifest = oracle.ensure_current_manifest()
    grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in transitions:
        trans = to_plain(raw)
        for dof, metrics in trans.get("metric_deltas", {}).items():
            for metric, delta in metrics.items():
                grouped[(
                    str(trans.get("action_type", "unknown")),
                    str(trans.get("parameter_group", "unknown")),
                    str(trans.get("target_dof", "unknown")),
                    str(dof),
                    str(metric),
                )].append({"transition": trans, "delta": delta})

    entries: dict[str, RelationMemoryEntry] = {}
    for key, items in grouped.items():
        action_type, group, target_dof, response_dof, metric = key
        numeric = [_as_float(item.get("delta", {}).get("delta")) for item in items]
        numeric = [item for item in numeric if item is not None]
        if not numeric:
            continue
        effect_size = sum(numeric) / len(numeric)
        support: list[str] = []
        refute: list[str] = []
        risks: set[str] = set()
        for item in items:
            trans = item["transition"]
            child_run = str(trans.get("child_run_id", ""))
            delta_value = _as_float(item.get("delta", {}).get("delta"))
            if delta_value is not None and delta_value < 0:
                support.append(child_run)
            else:
                refute.append(child_run)
            risks.update(str(x) for x in trans.get("new_failures", []))
        confidence = min(1.0, len(items) / 5.0)
        status = _relation_status(effect_size, support, refute, risks, confidence)
        rid = "REL_" + hashlib.sha1(":".join(key).encode("utf-8")).hexdigest()[:16].upper()
        entries[rid] = RelationMemoryEntry(
            relation_id=rid,
            action_type=action_type,
            parameter_group=group,
            target_dof=target_dof,
            response_dof=response_dof,
            metric=metric,
            effect="improves" if effect_size < 0 else "damages_or_no_improvement",
            effect_size=effect_size,
            confidence=confidence,
            status=status,
            support_run_ids=sorted(set(support)),
            refute_run_ids=sorted(set(refute)),
            risk_tags=sorted(risks),
            applicability_notes=[f"derived from {len(items)} transition(s) in current oracle epoch"],
        )
    return RelationMemory(
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        entries=entries,
        source_transition_count=len(transitions),
    )


def write_relation_memory(relation: RelationMemory) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(relation)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    path = oracle.epoch_root(manifest.oracle_id) / "relation_memory.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "relation_memory.json").write_text(text, encoding="utf-8")
    return path


def read_relation_memory() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "relation_memory.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def build_proposal_history(cards: list[dict[str, Any]], proposal_pool: dict[str, Any] | None = None) -> dict[str, ProposalHistoryEntry]:
    by_proposal: dict[str, dict[str, Any]] = {}
    if proposal_pool:
        for proposal in proposal_pool.get("proposals", []):
            action = proposal.get("action", {})
            by_proposal[proposal.get("proposal_id", "")] = {
                "proposal_id": proposal.get("proposal_id", ""),
                "parent_run_id": proposal.get("parent_run_id", ""),
                "parent_candidate_id": proposal.get("parent_candidate_id", ""),
                "target_dof": proposal.get("target_dof", ""),
                "action_type": action.get("action_type", ""),
                "parameter_group": action.get("parameter_group", ""),
                "materialized_candidate_ids": [],
                "run_ids": [],
                "step_decisions": [],
                "outcome": "proposed",
                "candidate_intent": proposal.get("candidate_intent", {}),
                "created_at": proposal.get("created_at", ""),
            }
    for card in cards:
        candidate_id = str(card.get("observation", {}).get("candidate_id", ""))
        registry_row = _candidate_registry_row(candidate_id)
        registry_meta = _candidate_registry_decision_meta(registry_row)
        meta = dict((card.get("parameter_delta") or {}).get("decision_meta") or {})
        for key, value in registry_meta.items():
            if value not in ("", None, {}, []):
                current = meta.get(key)
                if current in ("", None, {}, []):
                    meta[key] = value
        proposal_id = str(meta.get("proposal_id") or "")
        if not proposal_id:
            continue
        item = by_proposal.setdefault(proposal_id, {
            "proposal_id": proposal_id,
            "parent_run_id": str(meta.get("proposal_parent_run_id", card.get("parent_id", ""))),
            "parent_candidate_id": str((registry_row or {}).get("parent_candidate_id", "")),
            "target_dof": str(meta.get("proposal_target_dof") or meta.get("target_dof") or _target_dof(card)),
            "action_type": str(meta.get("proposal_action_type") or meta.get("action_type") or _action_type(card)),
            "parameter_group": str(card.get("parameter_group", "")),
            "materialized_candidate_ids": [],
            "run_ids": [],
            "step_decisions": [],
            "outcome": "materialized",
            "candidate_intent": meta.get("candidate_intent") or _candidate_intent_from_meta(meta),
            "created_at": "",
        })
        if not item.get("candidate_intent"):
            item["candidate_intent"] = meta.get("candidate_intent") or _candidate_intent_from_meta(meta)
        run_id = str(card.get("run_id", ""))
        if candidate_id:
            item["materialized_candidate_ids"].append(candidate_id)
        if run_id:
            item["run_ids"].append(run_id)
        decision = dict(card.get("step_decision") or card.get("verdict") or {})
        if decision:
            item["step_decisions"].append(decision)
            status = decision.get("status", "")
            item["outcome"] = "accepted" if status == "ACCEPTED" else "rejected" if status == "REJECTED" else "evidence_only"
    now = config.utc_now()
    return {
        pid: ProposalHistoryEntry(
            proposal_id=str(item.get("proposal_id", pid)),
            parent_run_id=str(item.get("parent_run_id", "")),
            parent_candidate_id=str(item.get("parent_candidate_id", "")),
            target_dof=str(item.get("target_dof", "")),
            action_type=str(item.get("action_type", "")),
            parameter_group=str(item.get("parameter_group", "")),
            materialized_candidate_ids=sorted(set(item.get("materialized_candidate_ids", []))),
            run_ids=sorted(set(item.get("run_ids", []))),
            step_decisions=list(item.get("step_decisions", [])),
            outcome=str(item.get("outcome", "proposed")),
            candidate_intent=dict(item.get("candidate_intent") or {}),
            created_at=str(item.get("created_at", "")) or now,
            updated_at=now,
        )
        for pid, item in by_proposal.items()
        if pid
    }


def _candidate_intent_from_meta(meta: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "source": meta.get("candidate_intent_source") or meta.get("source") or "",
        "intent": meta.get("intent") or meta.get("proposal_hypothesis") or "",
        "expected_observation": meta.get("expected_observation") or "",
        "falsification_condition": meta.get("falsification_condition") or "",
    }
    if not any(fields.values()):
        return {}
    touched = meta.get("touched_parameters")
    if isinstance(touched, list):
        fields["touched_parameters"] = touched
    return fields


def _candidate_registry_row(candidate_id: str) -> dict[str, str] | None:
    if not candidate_id:
        return None
    for row in config.read_csv(config.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    return None


def _candidate_registry_decision_meta(row: dict[str, str] | None) -> dict[str, Any]:
    if not row:
        return {}
    try:
        vector = json.loads(row.get("parameter_vector_json") or "{}")
    except json.JSONDecodeError:
        return {}
    meta = vector.get("decision_meta") or {}
    return meta if isinstance(meta, dict) else {}


def write_proposal_history(entries: dict[str, ProposalHistoryEntry]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = {
        "created_at": config.utc_now(),
        "oracle_id": manifest.oracle_id,
        "epoch_id": manifest.oracle_id,
        "entries": {pid: to_plain(entry) for pid, entry in sorted(entries.items())},
    }
    path = oracle.epoch_root(manifest.oracle_id) / "proposal_history.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "proposal_history.json").write_text(text, encoding="utf-8")
    return path


def update_proposal_history() -> Path:
    return write_proposal_history(build_proposal_history(load_run_cards(), read_proposal_pool()))


def read_proposal_history() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "proposal_history.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_proposal_pool(pool: ProposalPool) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(pool)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    path = oracle.epoch_root(manifest.oracle_id) / "proposal_pool.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "proposal_pool.json").write_text(text, encoding="utf-8")
    return path


def read_proposal_pool() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "proposal_pool.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_harness_status(assessment: HarnessAssessment | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    payload = to_plain(assessment)
    payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
    payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
    path = oracle.epoch_root(manifest.oracle_id) / "harness_status.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    (config.global_memory_root() / "harness_status.json").write_text(text + "\n", encoding="utf-8")
    return path


def read_harness_status() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "harness_status.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    top = config.global_memory_root() / "harness_status.json"
    if top.exists():
        data = json.loads(top.read_text(encoding="utf-8"))
        if data.get("oracle_id") == manifest.oracle_id or data.get("epoch_id") == manifest.oracle_id:
            return data
    return {}


def write_campaign_memory(campaigns: dict[str, CampaignState] | dict[str, Any]) -> Path:
    manifest = oracle.ensure_current_manifest()
    entries = to_plain(campaigns)
    if "entries" in entries:
        payload = entries
        payload["oracle_id"] = payload.get("oracle_id") or manifest.oracle_id
        payload["epoch_id"] = payload.get("epoch_id") or manifest.oracle_id
        payload["created_at"] = payload.get("created_at") or config.utc_now()
    else:
        payload = {
            "created_at": config.utc_now(),
            "oracle_id": manifest.oracle_id,
            "epoch_id": manifest.oracle_id,
            "entries": entries,
        }
    path = oracle.epoch_root(manifest.oracle_id) / "campaign_memory.json"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text + "\n", encoding="utf-8")
    (config.global_memory_root() / "campaign_memory.json").write_text(text + "\n", encoding="utf-8")
    return path


def read_campaign_memory() -> dict[str, Any]:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_root(manifest.oracle_id) / "campaign_memory.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    top = config.global_memory_root() / "campaign_memory.json"
    if top.exists():
        data = json.loads(top.read_text(encoding="utf-8"))
        if data.get("oracle_id") == manifest.oracle_id or data.get("epoch_id") == manifest.oracle_id:
            return data
    return {}


def write_report(name: str, title: str, lines: list[str]) -> Path:
    manifest = oracle.ensure_current_manifest()
    path = oracle.epoch_reports_dir(manifest.oracle_id) / name
    body = [f"# {title}", "", f"Generated: {config.utc_now()}", ""]
    body.extend(lines)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def build_coupling_memory(cards: list[dict[str, Any]]) -> CouplingMemory:
    transitions = [to_plain(item) for item in build_transition_cards(cards)]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for transition in transitions:
        group = str(transition.get("parameter_group", ""))
        if group in PARAMETER_GROUPS:
            grouped[group].append(transition)

    entries: dict[str, CouplingMemoryEntry] = {}
    for group in PARAMETER_GROUPS:
        group_transitions = grouped.get(group, [])
        affected: set[str] = set()
        improves: set[str] = set()
        damages: set[str] = set()
        strengths: list[float] = []
        evidence: list[str] = []
        diagnostic_or_incomplete = 0

        for transition in group_transitions:
            child_run = str(transition.get("child_run_id", ""))
            if child_run:
                evidence.append(child_run)
            if _transition_is_diagnostic_or_incomplete(transition):
                diagnostic_or_incomplete += 1
            metric_deltas = transition.get("metric_deltas", {})
            for dof in DOFS:
                metrics = metric_deltas.get(dof, {})
                if not metrics:
                    continue
                affected.add(dof)
                score_delta = _as_float((metrics.get("score") or {}).get("delta"))
                if score_delta is not None:
                    strengths.append(abs(score_delta))
                    if score_delta < 0:
                        improves.add(dof)
                    elif score_delta > 0:
                        damages.add(dof)
            for item in transition.get("resolved_failures", []):
                for dof in DOFS:
                    if dof in str(item):
                        improves.add(dof)
                        affected.add(dof)
            for item in transition.get("new_failures", []):
                for dof in DOFS:
                    if dof in str(item):
                        damages.add(dof)
                        affected.add(dof)

        raw_confidence = min(1.0, len(evidence) / 5.0)
        penalty = 1.0 - (diagnostic_or_incomplete / max(len(group_transitions), 1) * 0.5)
        confidence = max(0.0, raw_confidence * penalty)
        response_strength = sum(strengths) / len(strengths) if strengths else 0.0
        entries[group] = CouplingMemoryEntry(
            parameter_group=group,
            affected_dofs=sorted(affected),
            improves=sorted(improves),
            damages=sorted(damages),
            response_strength=response_strength,
            confidence=confidence,
            evidence_run_ids=[item for item in evidence if item],
        )

    manifest = oracle.ensure_current_manifest()
    return CouplingMemory(
        created_at=config.utc_now(),
        entries=entries,
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
    )


def _transition_is_diagnostic_or_incomplete(transition: dict[str, Any]) -> bool:
    loop = transition.get("loop_verdict") or {}
    step = transition.get("step_decision") or {}
    failures = [*transition.get("new_failures", []), *transition.get("resolved_failures", [])]
    return bool(
        loop.get("status") == "DIAGNOSTIC_FORK"
        or step.get("status") == "EVIDENCE_ONLY"
        or any(str(item).startswith("missing_dof") for item in failures)
    )


def _epoch_state_path(oracle_id: str | None = None) -> Path:
    manifest = oracle.ensure_current_manifest()
    oid = oracle_id or manifest.oracle_id
    oracle.ensure_epoch_dirs(oid)
    return oracle.epoch_root(oid) / "global_state.json"


def _top_state_summary(state: dict[str, Any], oracle_id: str) -> dict[str, Any]:
    epoch_root = oracle.epoch_root(oracle_id)
    return {
        **state,
        "current_oracle_id": oracle_id,
        "current_epoch_id": oracle_id,
        "oracle_manifest_path": oracle.manifest_path().relative_to(config.workspace_root()).as_posix(),
        "epoch_root": epoch_root.relative_to(config.workspace_root()).as_posix(),
        "epoch_state_path": _epoch_state_path(oracle_id).relative_to(config.workspace_root()).as_posix(),
    }


def _legacy_cards_not_in(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = {card.get("run_id") for card in cards}
    if not config.runcards_dir().exists():
        return []
    out = []
    for path in sorted(config.runcards_dir().glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("run_id") not in seen:
            out.append(data)
    return out


def _attach_oracle_fields(data: dict[str, Any], oracle_id: str) -> dict[str, Any]:
    data = dict(data)
    data.setdefault("oracle_id", oracle_id)
    data.setdefault("epoch_id", oracle_id)
    if not data["oracle_id"]:
        data["oracle_id"] = oracle_id
    if not data["epoch_id"]:
        data["epoch_id"] = oracle_id
    if not data.get("step_decision"):
        data["step_decision"] = data.get("verdict", {})
    if not data.get("loop_verdict"):
        data["loop_verdict"] = {
            "status": "RUNNING",
            "reason": "legacy RunCard migrated into current oracle epoch",
            "tags": ["legacy"],
        }
    data.setdefault("predicate_results", [])
    data.setdefault("source_roles", {})
    data.setdefault("review_status", "not_required")
    return data


def _card_violations(card: dict[str, Any]) -> list[str]:
    verdict = card.get("step_decision") or card.get("verdict") or {}
    return list(verdict.get("hard_constraint_violations", []))


def _parent_card_for(
    child: dict[str, Any],
    by_run: dict[str, dict[str, Any]],
    by_candidate: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    parent_id = str(child.get("parent_id", ""))
    if not parent_id:
        return None
    if parent_id in by_run:
        return by_run[parent_id]
    return by_candidate.get(parent_id)


def _candidate_parent_index(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        candidate = str(card.get("observation", {}).get("candidate_id", ""))
        if candidate:
            grouped[candidate].append(card)
    return {candidate: _preferred_parent_card(items) for candidate, items in grouped.items() if items}


def _preferred_parent_card(cards: list[dict[str, Any]]) -> dict[str, Any]:
    def key(card: dict[str, Any]) -> tuple[int, int, float, str]:
        per_dof = card.get("objective", {}).get("per_dof", {})
        complete = int(len(per_dof) == len(DOFS))
        loop_status = str((card.get("loop_verdict") or {}).get("status", ""))
        clean = int(loop_status != "DIAGNOSTIC_FORK")
        score = _as_float(card.get("objective", {}).get("global_score"))
        return (complete, clean, -(score if score is not None else 1e99), str(card.get("created_at", "")))

    return max(cards, key=key)


def _action_type(card: dict[str, Any]) -> str:
    delta = card.get("parameter_delta") or {}
    meta = delta.get("decision_meta") or {}
    changed = str(delta.get("changed_parameters", "")).lower()
    if meta.get("proposal_id"):
        action = _proposal_action_from_meta(meta)
        if action:
            return action
    if delta.get("moordyn"):
        return "mooring_probe"
    if meta.get("signed_ic_bquad_tune") or meta.get("bquad_multipliers"):
        return "signed_ic_bquad"
    if delta.get("hydrodyn_edits"):
        return "hydrodyn_edit"
    if delta.get("initial_conditions"):
        return "initial_condition"
    if "bquad" in changed or "bqic" in changed:
        return "bquad_scale"
    return "unknown"


def _proposal_action_from_meta(meta: dict[str, Any]) -> str:
    if meta.get("proposal_action_type"):
        return str(meta.get("proposal_action_type"))
    text = " ".join(str(meta.get(key, "")) for key in ("proposal_hypothesis", "proposal_id"))
    text = text.lower()
    if "bquad" in text:
        return "bquad_scale"
    if "mooring" in text:
        return "mooring_probe"
    if "diagnostic" in text:
        return "diagnostic_review"
    return ""


def _target_dof(card: dict[str, Any]) -> str:
    delta = card.get("parameter_delta") or {}
    meta = delta.get("decision_meta") or {}
    for key in ("target_dof", "proposal_target_dof"):
        if meta.get(key) in DOFS:
            return str(meta[key])
    selected = meta.get("selected_dof_candidates") or {}
    if isinstance(selected, dict):
        changed = [dof for dof, candidate in selected.items() if dof in DOFS and candidate == card.get("observation", {}).get("candidate_id")]
        if len(changed) == 1:
            return changed[0]
    bquad = meta.get("bquad_multipliers") or {}
    if isinstance(bquad, dict):
        changed = []
        for dof, value in bquad.items():
            number = _as_float(value)
            if dof in DOFS and number is not None and abs(number - 1.0) > 1e-12:
                changed.append(dof)
        if len(changed) == 1:
            return changed[0]
    candidate = str(card.get("observation", {}).get("candidate_id", "")).upper()
    for dof in DOFS:
        token = dof.replace("FD_", "")
        if token in candidate:
            return dof
    return "GLOBAL"


def _score_delta(parent: dict[str, Any], child: dict[str, Any]) -> float | None:
    parent_score = _as_float(parent.get("objective", {}).get("global_score"))
    child_score = _as_float(child.get("objective", {}).get("global_score"))
    if parent_score is None or child_score is None:
        return None
    return child_score - parent_score


def _metric_deltas(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    parent_per = parent.get("objective", {}).get("per_dof", {})
    child_per = child.get("objective", {}).get("per_dof", {})
    metrics = ("score", "period_error_pct", "envelope_error", "ler_error", "fp_error", "zeta_error_ratio")
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for dof in DOFS:
        p = parent_per.get(dof, {})
        c = child_per.get(dof, {})
        if not p or not c:
            continue
        dof_out: dict[str, dict[str, Any]] = {}
        for metric in metrics:
            pv = _as_float(p.get(metric))
            cv = _as_float(c.get(metric))
            if pv is None or cv is None:
                continue
            signed_delta = cv - pv
            comparable_parent, comparable_child = _comparable_metric_values(metric, pv, cv)
            delta = comparable_child - comparable_parent
            dof_out[metric] = {
                "parent": pv,
                "child": cv,
                "delta": delta,
                "signed_delta": signed_delta,
                "comparison": "absolute_error" if _uses_absolute_error(metric) else "raw_value",
                "direction": "improved" if delta < 0 else "worsened" if delta > 0 else "unchanged",
            }
        if dof_out:
            out[dof] = dof_out
    return out


def _comparable_metric_values(metric: str, parent_value: float, child_value: float) -> tuple[float, float]:
    if _uses_absolute_error(metric):
        return abs(parent_value), abs(child_value)
    return parent_value, child_value


def _uses_absolute_error(metric: str) -> bool:
    return metric in {"score", "period_error_pct", "envelope_error", "ler_error", "fp_error", "zeta_error_ratio"}


def _failure_set(card: dict[str, Any]) -> set[str]:
    failures = set(_card_violations(card))
    for pred in card.get("predicate_results", []) or []:
        if isinstance(pred, dict) and not pred.get("passed", True):
            failures.add(str(pred.get("name", "")) or str(pred.get("reason", "")))
    return {item for item in failures if item}


def _artifact_retain_reason(card: dict[str, Any], current_best_run_id: str) -> str:
    policy = str(card.get("artifact_policy", "") or "")
    stage = str(card.get("stage", "") or "").lower()
    loop_status = str((card.get("loop_verdict") or {}).get("status", ""))
    if card.get("run_id") == current_best_run_id:
        return "current_best"
    if "final_validation" in policy or "final_validation" in stage:
        return "final_validation"
    if "baseline" in policy or "baseline" in stage:
        return "baseline"
    if "stage_best" in policy:
        return "stage_best"
    if loop_status == "DIAGNOSTIC_FORK" or "diagnostic" in policy:
        return "diagnostic_fork"
    if "manual_keep" in policy:
        return "manual_keep"
    if policy.startswith("retain_full"):
        return "stage_best"
    return "summary_only"


def _artifact_paths(card: dict[str, Any]) -> list[str]:
    raw_paths: set[str] = set()
    for item in (card.get("observation", {}).get("artifacts", {}).get("runs", {}) or {}).values():
        for key in (
            "run_dir",
            "input_dir",
            "output_dir",
            "logs_dir",
            "manifest_path",
            "fst_file",
            "main_output_path",
            "stdout_path",
            "stderr_path",
        ):
            value = item.get(key)
            if value:
                raw_paths.add(str(value).replace("\\", "/"))
    sources = [str(item).replace("\\", "/") for item in card.get("evidence_sources", []) if item]
    raw_paths.update(sources)
    return sorted(raw_paths)


def _artifact_paths_size(paths: list[str]) -> int:
    roots = _artifact_run_roots(paths)
    if roots:
        return sum(_path_size(path) for path in roots)
    return sum(_path_size(config.workspace_root() / p) for p in paths)


def _artifact_run_roots(paths: list[str]) -> list[Path]:
    roots: set[Path] = set()
    workspace = config.workspace_root().resolve()
    runs_root = workspace / "04_current_runs" / "runs"
    for value in paths:
        raw = Path(value)
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
        try:
            rel = path.relative_to(runs_root.resolve())
        except ValueError:
            continue
        if rel.parts:
            roots.add((runs_root / rel.parts[0]).resolve())
    return sorted(roots, key=lambda item: item.as_posix())


def _path_size(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            if child.is_file():
                total += child.stat().st_size
        return total
    except OSError:
        return 0


def _relation_status(
    effect_size: float,
    support: list[str],
    refute: list[str],
    risks: set[str],
    confidence: float,
) -> str:
    if len(refute) >= max(2, len(support) * 2):
        return "refuted"
    if risks and support:
        return "fragile"
    if confidence >= 0.6 and support and effect_size < 0:
        return "supported"
    return "observed"


def _sha256_json(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _as_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
