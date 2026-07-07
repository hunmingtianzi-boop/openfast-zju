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
    results = cfgmod.read_csv(cfgmod.registry_path("results_registry.csv"))
    cfgmod.write_report("07_reports/plot_report_todo.md", "Plot and Report TODO", [
        f"- Result rows available: {len(results)}",
        "- Six-DOF figures will be generated after successful OpenFAST runs and metric extraction.",
    ])
    print("Reporter: TODO report written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
