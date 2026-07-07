from __future__ import annotations

import csv
import importlib.util
import re
from pathlib import Path


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()

FIELDS = [
    "referencing_file",
    "referenced_path",
    "resolved_path",
    "exists",
    "source_found",
    "copy_action",
    "sha256",
    "notes",
]

AUDIT_SUFFIXES = {
    ".fst", ".dat", ".yaml", ".yml", ".in", ".txt", ".dll", ".ss", ".ssexctn",
    ".hst", ".1", ".3", ".12d", ".12s", ".wamit",
}

KNOWN_REF_SUFFIXES = {
    ".fst", ".dat", ".yaml", ".yml", ".in", ".txt", ".dll", ".ss", ".ssexctn",
    ".hst", ".1", ".3", ".12d", ".12s", ".wamit",
}


def iter_quoted_references(path: Path) -> list[str]:
    refs: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for line in text.splitlines():
        value_part = line.split(" - ", 1)[0]
        for match in re.finditer(r'"([^"]+)"', value_part):
            value = match.group(1).strip()
            if not value or value.lower() in {"none", "unused"} or "://" in value:
                continue
            suffix = Path(value).suffix.lower()
            if "/" in value or "\\" in value or suffix in KNOWN_REF_SUFFIXES:
                refs.append(value)
        for token in re.findall(r"(?<![\w:])(?:\.\./|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./\\:-]+", value_part):
            if Path(token).suffix.lower() in KNOWN_REF_SUFFIXES:
                refs.append(token)
    return sorted(set(refs))


def resolve_ref(referencing_file: Path, referenced_path: str) -> Path:
    raw = Path(referenced_path)
    if raw.is_absolute():
        return raw.resolve()
    return (referencing_file.parent / raw).resolve()


def find_workspace_source(filename: str, preferred_root: Path) -> Path | None:
    candidates = sorted(p for p in preferred_root.rglob(filename) if p.is_file())
    if candidates:
        return candidates[0]
    candidates = sorted(p for p in cfgmod.workspace_root().rglob(filename) if p.is_file())
    return candidates[0] if candidates else None


def main() -> int:
    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    model_dir = cfgmod.starting_model_dir(cfg)
    input_root = model_dir / "OpenFAST_input_files"
    baseline_root = cfgmod.resolve_workspace_path(cfg["model"]["baseline_dir"])
    fst_files = sorted(input_root.glob("*.fst")) if input_root.exists() else []
    audit_files: list[Path] = []
    seen: set[Path] = set()
    queue = list(fst_files)
    while queue:
        current = queue.pop(0).resolve()
        if current in seen or not current.exists() or current.suffix.lower() not in {".fst", ".dat", ".yaml", ".yml", ".in"}:
            continue
        seen.add(current)
        audit_files.append(current)
        for ref in iter_quoted_references(current):
            target = resolve_ref(current, ref)
            if target.exists() and target.is_file() and target.suffix.lower() in {".fst", ".dat", ".yaml", ".yml", ".in"}:
                queue.append(target)
    rows = []
    missing = []
    copied = []
    old_path_hits = []
    for ref_file in audit_files:
        text = ref_file.read_text(encoding="utf-8", errors="ignore")
        for hit in cfgmod.forbidden_hits(text, cfg):
            old_path_hits.append(f"{ref_file.relative_to(cfgmod.workspace_root()).as_posix()}: {hit}")
        for ref in iter_quoted_references(ref_file):
            resolved = resolve_ref(ref_file, ref)
            has_base_match = bool(resolved.parent.exists() and list(resolved.parent.glob(resolved.name + "*")))
            exists = resolved.exists() or has_base_match
            source = find_workspace_source(Path(ref).name, baseline_root)
            action = "none"
            notes = ""
            if "IEA-15-240-RWT/" in ref and exists:
                action = "copied_from_01_baseline_and_path_repaired"
                copied.append(ref)
            if not cfgmod.is_within(resolved):
                notes = "resolved outside clean workspace"
            if not exists:
                missing.append(ref)
                notes = "missing after repair"
            rows.append({
                "referencing_file": ref_file.relative_to(cfgmod.workspace_root()).as_posix(),
                "referenced_path": ref,
                "resolved_path": str(resolved),
                "exists": str(exists).lower(),
                "source_found": str(source is not None).lower(),
                "copy_action": action,
                "sha256": cfgmod.sha256_file(resolved) if resolved.exists() and resolved.is_file() else "",
                "notes": notes,
            })
    manifest_path = cfgmod.registry_path("model_dependency_manifest.csv")
    cfgmod.write_csv(manifest_path, FIELDS, rows)
    unique_missing = sorted(set(missing))
    unique_copied = sorted(set(copied))
    lines = [
        f"- Starting model: {model_dir.relative_to(cfgmod.workspace_root()).as_posix()}",
        f"- Text input files audited: {len(audit_files)}",
        f"- Referenced dependencies recorded: {len(rows)}",
        f"- Missing dependencies after repair: {len(unique_missing)}",
        f"- Clean-workspace forbidden path hits: {len(old_path_hits)}",
        "",
        "## Copied/Repaired Dependencies",
    ]
    lines.extend([f"- {item}" for item in unique_copied[:80]] or ["- None"])
    if len(unique_copied) > 80:
        lines.append(f"- ... {len(unique_copied) - 80} more; see manifest")
    lines.extend(["", "## Missing Dependencies"])
    lines.extend([f"- {item}" for item in unique_missing] or ["- None"])
    lines.extend(["", "## Forbidden/Old Path Hits"])
    lines.extend([f"- {item}" for item in old_path_hits] or ["- None"])
    cfgmod.write_report("07_reports/model_dependency_audit.md", "Model Dependency Audit", lines)
    cfgmod.append_log("08_logs/harness_build_log.md", f"model_dependency_audit rows={len(rows)} missing={len(unique_missing)}")
    print(f"Model dependency audit: rows={len(rows)} missing={len(unique_missing)}")
    return 0 if not unique_missing and not old_path_hits else 2


if __name__ == "__main__":
    raise SystemExit(main())
