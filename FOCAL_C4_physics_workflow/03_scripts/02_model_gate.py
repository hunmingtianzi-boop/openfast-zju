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
FIELDS = ["model_id", "model_dir", "source", "parent_model_id", "sha256_manifest", "path_status", "status", "notes"]
PARSE_SUFFIXES = {".fst", ".dat", ".yaml", ".yml", ".in"}
REF_SUFFIXES = {".fst", ".dat", ".yaml", ".yml", ".in", ".txt", ".dll"}


def _refs(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    refs: list[str] = []
    for line in text.splitlines():
        value_part = line.split(" - ", 1)[0]
        for match in re.finditer(r'"([^"]+)"', value_part):
            value = match.group(1).strip()
            if value and value.lower() not in {"none", "unused"} and "://" not in value:
                refs.append(value)
        for token in re.findall(r"(?<![\w:])(?:\.\./|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./\\:-]+", value_part):
            if Path(token).suffix.lower() in REF_SUFFIXES:
                refs.append(token)
    return sorted(set(refs))


def _resolved(parent: Path, ref: str) -> Path:
    raw = Path(ref)
    return raw.resolve() if raw.is_absolute() else (parent.parent / raw).resolve()


def _reachable_files(fst_files: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    queue = list(fst_files)
    while queue:
        current = queue.pop(0).resolve()
        if current in seen or not current.exists() or current.suffix.lower() not in PARSE_SUFFIXES:
            continue
        seen.add(current)
        for ref in _refs(current):
            target = _resolved(current, ref)
            if target.exists() and target.is_file() and target.suffix.lower() in PARSE_SUFFIXES:
                queue.append(target)
    return sorted(seen)


def main() -> int:
    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    model_dir = cfgmod.starting_model_dir(cfg)
    input_root = model_dir / "OpenFAST_input_files"
    issues: list[str] = []
    fst_files = sorted(input_root.glob("*.fst")) if input_root.exists() else []
    if not model_dir.exists():
        issues.append("starting model directory is missing")
    if not fst_files:
        issues.append("no top-level .fst file found in OpenFAST_input_files")
    audit_files = _reachable_files(fst_files) if model_dir.exists() else []
    for model_file in audit_files:
        issues.extend(cfgmod.audit_text_paths(model_file, cfg))
    manifest_rows = cfgmod.file_manifest(model_dir) if model_dir.exists() else []
    manifest_path = cfgmod.workspace_root() / "08_logs" / "model_sha256_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["relative_path", "size_bytes", "sha256"])
        writer.writeheader()
        writer.writerows(manifest_rows)
    tree_sha = cfgmod.sha256_tree(model_dir) if model_dir.exists() else ""
    path_status = "PASS" if not issues else "FAIL"
    status = "PASS" if model_dir.exists() and fst_files and not issues else "FAIL"
    cfgmod.write_csv(cfgmod.registry_path("model_manifest.csv"), FIELDS, [{
        "model_id": "STARTING_MODEL",
        "model_dir": model_dir.relative_to(cfgmod.workspace_root()).as_posix() if model_dir.exists() else str(model_dir),
        "source": "clean_workspace_starting_model",
        "parent_model_id": "",
        "sha256_manifest": tree_sha,
        "path_status": path_status,
        "status": status,
        "notes": "; ".join(issues) if issues else f"{len(fst_files)} fst file(s) found",
    }])
    lines = [
        f"- Workspace: {cfgmod.workspace_root()}",
        f"- Starting model directory: {model_dir}",
        f"- FST files found: {len(fst_files)}",
        f"- Text model files audited: {len(audit_files)}",
        f"- Gate status: {status}",
        f"- SHA-256 manifest: {manifest_path.relative_to(cfgmod.workspace_root()).as_posix()}",
        "",
        "## FST Files",
    ]
    lines.extend([f"- {p.relative_to(cfgmod.workspace_root()).as_posix()}" for p in fst_files] or ["- None"])
    lines.append("")
    lines.append("## Issues")
    lines.extend([f"- {i}" for i in issues] or ["- None"])
    cfgmod.write_report("07_reports/model_gate_report.md", "Model Gate Report", lines)
    cfgmod.write_report("08_logs/model_path_audit.md", "Model Path Audit", lines)
    cfgmod.append_log("08_logs/harness_build_log.md", f"model_gate status={status}")
    print(f"Model Gate: {status}")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
