from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Keep the path spelling used to enter the active workflow.  The active
# workflow may be a junction, and resolving it would change oracle identity.
SCRIPT_DIR = Path(__file__).absolute().parent
WORKSPACE_ROOT = SCRIPT_DIR.parent
CONFIG_PATH = WORKSPACE_ROOT / "config.yaml"


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", ""}:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _minimal_yaml_load(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_key: tuple[int, dict[str, Any], str] | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if line.startswith("- "):
            if pending_key and indent > pending_key[0]:
                _, parent, key = pending_key
                parent[key] = []
                stack.append((indent, parent[key]))
                pending_key = None
            while stack and indent < stack[-1][0]:
                stack.pop()
            stack[-1][1].append(_parse_scalar(line[2:]))
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value:
            parent[key] = _parse_scalar(value)
            pending_key = None
        else:
            parent[key] = {}
            pending_key = (indent, parent, key)
            stack.append((indent, parent[key]))
    return root


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Missing config: {CONFIG_PATH}")
    text = CONFIG_PATH.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore

        cfg = yaml.safe_load(text) or {}
    except Exception:
        cfg = _minimal_yaml_load(text)
    cfg["_workspace_root_abs"] = str(WORKSPACE_ROOT)
    cfg["_path_audit"] = path_audit()
    return cfg


def workspace_root() -> Path:
    return WORKSPACE_ROOT


def resolve_workspace_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = WORKSPACE_ROOT / path
    return path


def path_audit() -> dict[str, Any]:
    resolved = WORKSPACE_ROOT.resolve()
    return {
        "canonical_workspace_root": str(WORKSPACE_ROOT),
        "resolved_workspace_root": str(resolved),
        "is_junction_alias": str(WORKSPACE_ROOT) != str(resolved),
    }


def reference_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    return resolve_workspace_path(cfg["reference"]["processed_dir"])


def starting_model_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg or load_config()
    return resolve_workspace_path(cfg["model"]["starting_model_dir"])


def openfast_exe(cfg: dict[str, Any] | None = None) -> Path | None:
    cfg = cfg or load_config()
    value = cfg.get("openfast_exe")
    return resolve_workspace_path(value) if value else None


def ensure_core_dirs() -> None:
    for rel in [
        "00_reference",
        "01_baseline",
        "02_starting_model",
        "03_scripts",
        "04_current_runs/candidates",
        "04_current_runs/runs",
        "04_current_runs/packaged_models",
        "05_registry",
        "06_figures",
        "07_reports",
        "08_logs",
    ]:
        (WORKSPACE_ROOT / rel).mkdir(parents=True, exist_ok=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_tree(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(sha256_file(path).encode("ascii"))
    return h.hexdigest()


def file_manifest(root: Path) -> list[dict[str, str]]:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rows.append({
            "relative_path": path.relative_to(root).as_posix(),
            "size_bytes": str(path.stat().st_size),
            "sha256": sha256_file(path),
        })
    return rows


def is_within(path: Path, root: Path = WORKSPACE_ROOT) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def forbidden_hits(text: str, cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    hits = []
    for item in cfg.get("forbidden_paths", []):
        if item and item in text:
            hits.append(item)
    return sorted(set(hits))


def audit_text_paths(path: Path, cfg: dict[str, Any] | None = None) -> list[str]:
    cfg = cfg or load_config()
    issues: list[str] = []
    text = path.read_text(encoding="utf-8", errors="ignore")
    for hit in forbidden_hits(text, cfg):
        issues.append(f"forbidden token in {path.name}: {hit}")
    for line in text.splitlines():
        value_part = line.split(" - ", 1)[0]
        for match in re.finditer(r'"([^"]+)"', value_part):
            value = match.group(1).strip()
            if not value or value.lower() in {"none", "unused"}:
                continue
            if "://" in value:
                continue
            if re.match(r"^[A-Za-z]:[/\\]", value):
                candidate = Path(value)
            else:
                candidate = (path.parent / value).resolve()
            if (".." in Path(value).parts or candidate.is_absolute()) and not is_within(candidate):
                issues.append(f"outside workspace reference in {path.name}: {value}")
            suffix = Path(value).suffix.lower()
            known_suffixes = {
                ".dat", ".fst", ".yaml", ".yml", ".in", ".dll", ".txt", ".sum",
                ".ss", ".ssexctn", ".hst", ".1", ".3", ".12s", ".12d", ".wamit",
            }
            looks_like_file = "/" in value or "\\" in value or suffix in known_suffixes
            has_base_match = bool(candidate.parent.exists() and list(candidate.parent.glob(candidate.name + "*")))
            if looks_like_file and not candidate.exists() and not has_base_match:
                issues.append(f"missing referenced file in {path.name}: {value}")
    return sorted(set(issues))


def registry_path(name: str) -> Path:
    return WORKSPACE_ROOT / "05_registry" / name


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def upsert_csv(path: Path, fieldnames: list[str], key: str, row: dict[str, Any]) -> None:
    rows = read_csv(path)
    replaced = False
    for idx, existing in enumerate(rows):
        if existing.get(key) == str(row.get(key)):
            rows[idx] = {**existing, **{k: str(v) for k, v in row.items()}}
            replaced = True
    if not replaced:
        rows.append({k: str(v) for k, v in row.items()})
    write_csv(path, fieldnames, rows)


def write_report(rel_path: str, title: str, lines: list[str]) -> Path:
    path = WORKSPACE_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    body = [f"# {title}", "", f"Generated: {utc_now()}", ""]
    body.extend(lines)
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(rel_path: str, data: dict[str, Any]) -> Path:
    path = WORKSPACE_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def append_log(rel_path: str, line: str) -> None:
    path = WORKSPACE_ROOT / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{utc_now()} {line}\n")


def load_helpers():
    return None
