from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from . import config, memory, oracle
from .schema import MemoryGcPlan, to_plain


PROTECTED_CLASSES = {
    "baseline",
    "current_best",
    "stage_best",
    "diagnostic_fork",
    "final_validation",
    "manual_keep",
}


def build_gc_plan(apply: bool = False) -> MemoryGcPlan:
    manifest = oracle.ensure_current_manifest()
    artifact_manifest = memory.read_artifact_manifest()
    if not artifact_manifest:
        cards = memory.load_run_cards()
        memory.write_artifact_manifest(memory.build_artifact_manifests(cards))
        artifact_manifest = memory.read_artifact_manifest()
    entries = list(artifact_manifest.get("entries", {}).values())
    protected_roots = _protected_run_roots(entries)
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    for entry in entries:
        reason = _skip_reason(entry, protected_roots)
        if reason:
            skipped.append({"run_id": entry.get("run_id", ""), "reason": reason})
            continue
        roots = _run_roots(entry.get("artifact_paths", []))
        item = {
            "run_id": entry.get("run_id", ""),
            "candidate_id": entry.get("candidate_id", ""),
            "artifact_paths": entry.get("artifact_paths", []),
            "run_roots": [p.relative_to(config.workspace_root().resolve()).as_posix() for p in roots],
            "total_size_bytes": _paths_size(roots),
            "retention_class": entry.get("retention_class", ""),
        }
        candidates.append(item)
        if apply:
            removed = _delete_paths(entry.get("artifact_paths", []))
            item = {**item, "deleted_paths": removed, "deleted_at": config.utc_now()}
            deleted.append(item)
            if removed:
                _mark_artifacts_deleted_for_roots(removed, item["deleted_at"])
    return MemoryGcPlan(
        created_at=config.utc_now(),
        oracle_id=manifest.oracle_id,
        epoch_id=manifest.oracle_id,
        apply=apply,
        candidates=candidates,
        skipped=skipped,
        deleted=deleted,
        total_candidate_bytes=sum(int(item.get("total_size_bytes") or 0) for item in candidates),
    )


def write_gc_plan(plan: MemoryGcPlan) -> dict[str, Path]:
    manifest = oracle.ensure_current_manifest()
    path = memory.artifacts_dir(manifest.oracle_id) / ("memory_gc_apply.json" if plan.apply else "memory_gc_dry_run.json")
    payload = to_plain(plan)
    text = __import__("json").dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    report = memory.write_report(
        "memory_gc_report.md",
        "Global Loop Memory GC",
        [
            f"- Mode: {'apply' if plan.apply else 'dry-run'}",
            f"- Delete candidates: {len(plan.candidates)}",
            f"- Skipped protected/unsafe entries: {len(plan.skipped)}",
            f"- Deleted entries: {len(plan.deleted)}",
            f"- Candidate bytes: {plan.total_candidate_bytes}",
        ],
    )
    top = config.global_memory_root() / "memory_gc_plan.json"
    top.write_text(text, encoding="utf-8")
    return {"plan": path, "report": report}


def _skip_reason(entry: dict[str, Any], protected_roots: set[Path] | None = None) -> str:
    retention = str(entry.get("retention_class", ""))
    if retention in PROTECTED_CLASSES:
        return f"protected retention class: {retention}"
    if not entry.get("metrics_extracted"):
        return "metrics not extracted"
    if not entry.get("delete_eligible"):
        return "not delete eligible"
    paths = entry.get("artifact_paths", [])
    if not paths:
        return "no artifact paths"
    unsafe = [path for path in paths if not _safe_artifact_path(path)]
    if unsafe:
        return "unsafe path outside 04_current_runs/runs: " + ",".join(str(item) for item in unsafe[:3])
    roots = set(_run_roots(paths))
    if not roots:
        return "no concrete run directory paths"
    conflicts = roots & (protected_roots or set())
    if conflicts:
        rel = [p.relative_to(config.workspace_root().resolve()).as_posix() for p in sorted(conflicts, key=lambda x: x.as_posix())]
        return "run root shared with protected artifact: " + ",".join(rel[:3])
    return ""


def _safe_artifact_path(value: str) -> bool:
    if not value:
        return False
    root = config.workspace_root().resolve()
    path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return _run_root(path) is not None


def _delete_paths(values: list[str]) -> list[str]:
    removed: list[str] = []
    for path in _run_roots(values):
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(path.relative_to(config.workspace_root().resolve()).as_posix())
    return removed


def _protected_run_roots(entries: list[dict[str, Any]]) -> set[Path]:
    roots: set[Path] = set()
    for entry in entries:
        retention = str(entry.get("retention_class", ""))
        if retention in PROTECTED_CLASSES or not entry.get("metrics_extracted") or not entry.get("delete_eligible"):
            roots.update(_run_roots(entry.get("artifact_paths", [])))
    return roots


def _run_roots(values: list[str]) -> list[Path]:
    roots: set[Path] = set()
    for value in values:
        root = config.workspace_root().resolve()
        path = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
        run_root = _run_root(path)
        if run_root is not None:
            roots.add(run_root)
    return sorted(roots, key=lambda item: item.as_posix())


def _run_root(path: Path) -> Path | None:
    workspace = config.workspace_root().resolve()
    runs_root = workspace / "04_current_runs" / "runs"
    try:
        rel = path.resolve().relative_to(runs_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    candidate_root = (runs_root / parts[0]).resolve()
    try:
        candidate_root.relative_to(workspace)
    except ValueError:
        return None
    return candidate_root


def _mark_artifacts_deleted_for_roots(deleted_paths: list[str], deleted_at: str) -> None:
    manifest = memory.read_artifact_manifest()
    entries = manifest.get("entries", {})
    deleted_roots = {
        (config.workspace_root().resolve() / path).resolve()
        for path in deleted_paths
    }
    changed = False
    for run_id, entry in list(entries.items()):
        roots = set(_run_roots(entry.get("artifact_paths", [])))
        hit = roots & deleted_roots
        if not hit:
            continue
        updated = dict(entry)
        prior_deleted = list(updated.get("deleted_paths", []) or [])
        rel_hit = [p.relative_to(config.workspace_root().resolve()).as_posix() for p in sorted(hit, key=lambda item: item.as_posix())]
        updated.update({
            "raw_output_retained": False,
            "delete_eligible": False,
            "deleted_at": deleted_at,
            "deleted_paths": sorted(set(prior_deleted + rel_hit)),
            "total_size_bytes": 0,
        })
        entries[run_id] = updated
        changed = True
    if not changed:
        return
    _write_artifact_manifest_payload(manifest)


def _paths_size(paths: list[Path]) -> int:
    total = 0
    for path in paths:
        try:
            if not path.exists():
                continue
            if path.is_file():
                total += path.stat().st_size
                continue
            for child in path.rglob("*"):
                if child.is_file():
                    total += child.stat().st_size
        except OSError:
            continue
    return total


def _write_artifact_manifest_payload(payload: dict[str, Any]) -> None:
    manifest = oracle.ensure_current_manifest()
    payload = {**payload, "oracle_id": manifest.oracle_id, "epoch_id": manifest.oracle_id}
    import json

    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path = memory.artifacts_dir(manifest.oracle_id) / "artifact_manifest.json"
    path.write_text(text, encoding="utf-8")
    (config.global_memory_root() / "artifact_manifest.json").write_text(text, encoding="utf-8")
