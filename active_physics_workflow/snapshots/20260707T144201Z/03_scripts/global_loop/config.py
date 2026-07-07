from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


PACKAGE_DIR = Path(__file__).absolute().parent
SCRIPT_DIR = PACKAGE_DIR.parent
WORKSPACE_ROOT = SCRIPT_DIR.parent


def _load_script(filename: str, module_name: str):
    path = SCRIPT_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod


cfgmod = _load_script("00_config_loader.py", "active_workflow_config_loader")


def load_config() -> dict[str, Any]:
    return cfgmod.load_config()


def workspace_root() -> Path:
    return cfgmod.workspace_root()


def path_audit() -> dict[str, Any]:
    return cfgmod.path_audit()


def resolve_workspace_path(value: str | Path) -> Path:
    return cfgmod.resolve_workspace_path(value)


def reference_dir(cfg: dict[str, Any] | None = None) -> Path:
    return cfgmod.reference_dir(cfg)


def starting_model_dir(cfg: dict[str, Any] | None = None) -> Path:
    return cfgmod.starting_model_dir(cfg)


def openfast_exe(cfg: dict[str, Any] | None = None) -> Path | None:
    return cfgmod.openfast_exe(cfg)


def registry_path(name: str) -> Path:
    return cfgmod.registry_path(name)


def read_csv(path: Path) -> list[dict[str, str]]:
    return cfgmod.read_csv(path)


def utc_now() -> str:
    return cfgmod.utc_now()


def write_json(rel_path: str, data: dict[str, Any]) -> Path:
    return cfgmod.write_json(rel_path, data)


def append_log(rel_path: str, line: str) -> None:
    cfgmod.append_log(rel_path, line)


def ensure_global_dirs() -> None:
    for rel in (
        "10_global_memory",
        "10_global_memory/epochs",
        "10_global_memory/runcards",
        "10_global_memory/reports",
    ):
        (workspace_root() / rel).mkdir(parents=True, exist_ok=True)


def global_memory_root() -> Path:
    return workspace_root() / "10_global_memory"


def runcards_dir() -> Path:
    return global_memory_root() / "runcards"


def reports_dir() -> Path:
    return global_memory_root() / "reports"


def epochs_dir() -> Path:
    return global_memory_root() / "epochs"


def active_paths(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    model_root = starting_model_dir(cfg) / cfg["model"].get("openfast_subdir", "OpenFAST_input_files")
    return {
        "workspace_root": str(workspace_root()),
        "canonical_workspace_root": str(workspace_root()),
        "resolved_workspace_root": str(workspace_root().resolve()),
        "is_junction_alias": str(workspace_root()) != str(workspace_root().resolve()),
        "reference_processed_dir": str(reference_dir(cfg)),
        "starting_model_openfast_dir": str(model_root),
        "openfast_exe": str(openfast_exe(cfg) or ""),
    }


def assert_active_paths(cfg: dict[str, Any] | None = None) -> None:
    cfg = cfg or load_config()
    paths = active_paths(cfg)
    for key in ("reference_processed_dir", "starting_model_openfast_dir"):
        if not Path(paths[key]).exists():
            raise FileNotFoundError(f"{key} does not exist: {paths[key]}")
    forbidden = cfg.get("forbidden_paths", [])
    joined = "\n".join(str(value) for value in paths.values())
    hits = [item for item in forbidden if item and item in joined]
    if hits:
        raise RuntimeError(f"active global-loop paths contain forbidden legacy tokens: {hits}")
