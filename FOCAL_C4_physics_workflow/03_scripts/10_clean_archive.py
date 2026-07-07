from __future__ import annotations

import importlib.util
from pathlib import Path


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()


def main() -> int:
    cfgmod.ensure_core_dirs()
    cfgmod.write_report("08_logs/cleanup_plan.md", "Cleanup Plan", [
        "- No files are deleted by this MVP cleaner.",
        "- Future cleanup should archive stale run directories only after human review.",
        "- Preserve registries, manifests, reports, and per-run provenance.",
    ])
    print("Cleaner: cleanup plan written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
