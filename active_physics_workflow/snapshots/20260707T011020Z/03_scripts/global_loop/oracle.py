from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import config
from .schema import DOFS, OracleManifest, to_plain


ORACLE_VERSION = 2
ORACLE_MANIFEST_NAME = "oracle_manifest.json"


def build_manifest(cfg: dict[str, Any] | None = None) -> OracleManifest:
    cfg = cfg or config.load_config()
    config.assert_active_paths(cfg)
    ref_dir = config.reference_dir(cfg)
    model_root = config.starting_model_dir(cfg) / cfg["model"].get("openfast_subdir", "OpenFAST_input_files")
    references = _reference_manifest(ref_dir)
    sections = _config_sections(cfg)
    existing = read_manifest()
    payload = {
        "version": ORACLE_VERSION,
        "active_paths": config.active_paths(cfg),
        "config_hash": config.cfgmod.sha256_file(config.WORKSPACE_ROOT / "config.yaml"),
        "config_sections_hash": _sha256_json(sections),
        "config_sections": sections,
        "reference_files": references,
        "starting_model_tree_hash": config.cfgmod.sha256_tree(model_root),
        "target_source": "active_curated_processed_reference",
    }
    truth_payload = _truth_payload(payload)
    if existing and _truth_payload(existing) == truth_payload and existing.get("oracle_id"):
        oracle_id = str(existing["oracle_id"])
        created_at = str(existing.get("created_at") or config.utc_now())
    else:
        oracle_id = "oracle_" + _sha256_json(truth_payload)[:16]
        created_at = config.utc_now()
    return OracleManifest(
        oracle_id=oracle_id,
        created_at=created_at,
        version=ORACLE_VERSION,
        active_paths=payload["active_paths"],
        config_hash=payload["config_hash"],
        config_sections_hash=payload["config_sections_hash"],
        config_sections=sections,
        reference_files=references,
        starting_model_tree_hash=payload["starting_model_tree_hash"],
        target_source=payload["target_source"],
    )


def ensure_current_manifest(cfg: dict[str, Any] | None = None) -> OracleManifest:
    current = build_manifest(cfg)
    existing = read_manifest()
    if existing and existing.get("oracle_id") == current.oracle_id:
        ensure_epoch_dirs(current.oracle_id)
        if _manifest_changed(existing, current):
            write_manifest(current)
        return current
    write_manifest(current)
    return current


def read_manifest(path: Path | None = None) -> dict[str, Any]:
    path = path or manifest_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def manifest_from_dict(data: dict[str, Any]) -> OracleManifest:
    return OracleManifest(
        oracle_id=str(data.get("oracle_id", "")),
        created_at=str(data.get("created_at", "")),
        version=int(data.get("version", ORACLE_VERSION)),
        active_paths=dict(data.get("active_paths", {})),
        config_hash=str(data.get("config_hash", "")),
        config_sections_hash=str(data.get("config_sections_hash", "")),
        config_sections=dict(data.get("config_sections", {})),
        reference_files=dict(data.get("reference_files", {})),
        starting_model_tree_hash=str(data.get("starting_model_tree_hash", "")),
        target_source=str(data.get("target_source", "active_curated_processed_reference")),
    )


def write_manifest(manifest: OracleManifest) -> Path:
    config.ensure_global_dirs()
    ensure_epoch_dirs(manifest.oracle_id)
    payload = json.dumps(to_plain(manifest), indent=2, ensure_ascii=False)
    top_path = manifest_path()
    top_path.write_text(payload + "\n", encoding="utf-8")
    epoch_manifest_path(manifest.oracle_id).write_text(payload + "\n", encoding="utf-8")
    return top_path


def manifest_path() -> Path:
    return config.global_memory_root() / ORACLE_MANIFEST_NAME


def epochs_root() -> Path:
    return config.global_memory_root() / "epochs"


def epoch_root(oracle_id: str) -> Path:
    return epochs_root() / oracle_id


def epoch_runcards_dir(oracle_id: str) -> Path:
    return epoch_root(oracle_id) / "runcards"


def epoch_reports_dir(oracle_id: str) -> Path:
    return epoch_root(oracle_id) / "reports"


def epoch_manifest_path(oracle_id: str) -> Path:
    return epoch_root(oracle_id) / ORACLE_MANIFEST_NAME


def ensure_epoch_dirs(oracle_id: str) -> None:
    for path in (
        epochs_root(),
        epoch_root(oracle_id),
        epoch_runcards_dir(oracle_id),
        epoch_reports_dir(oracle_id),
        epoch_root(oracle_id) / "artifacts",
        epoch_root(oracle_id) / "transitions",
        epoch_root(oracle_id) / "llm_jobs",
        epoch_root(oracle_id) / "llm_packets",
    ):
        path.mkdir(parents=True, exist_ok=True)


def assert_card_oracle(card_oracle_id: str, manifest: OracleManifest) -> None:
    if card_oracle_id and card_oracle_id != manifest.oracle_id:
        raise RuntimeError(
            f"RunCard oracle_id {card_oracle_id} does not match current oracle {manifest.oracle_id}"
        )


def _reference_manifest(ref_dir: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for dof in DOFS:
        path = ref_dir / f"{dof}_curated_processed.csv"
        if not path.exists():
            raise FileNotFoundError(f"Missing curated reference for {dof}: {path}")
        if dof == "FD_HEAVE" and path.name != "FD_HEAVE_curated_processed.csv":
            raise RuntimeError(f"FD_HEAVE must use recut curated reference, got: {path.name}")
        out[dof] = {
            "relative_path": path.relative_to(config.workspace_root()).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": config.cfgmod.sha256_file(path),
        }
    return out


def _config_sections(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "reference": cfg.get("reference", {}),
        "model": cfg.get("model", {}),
        "targets": cfg.get("targets", {}),
        "physics": cfg.get("physics", {}),
    }


def _truth_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": data.get("version", ORACLE_VERSION),
        "active_paths": data.get("active_paths", {}),
        "config_sections_hash": data.get("config_sections_hash", ""),
        "config_sections": data.get("config_sections", {}),
        "reference_files": data.get("reference_files", {}),
        "starting_model_tree_hash": data.get("starting_model_tree_hash", ""),
        "target_source": data.get("target_source", "active_curated_processed_reference"),
    }


def _manifest_changed(existing: dict[str, Any], current: OracleManifest) -> bool:
    return json.dumps(existing, sort_keys=True, ensure_ascii=False) != json.dumps(
        to_plain(current), sort_keys=True, ensure_ascii=False
    )


def _sha256_json(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()
