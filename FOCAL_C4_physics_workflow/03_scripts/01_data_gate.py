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

FIELDS = ["data_id", "dof", "reference_file", "reference_kind", "old_heave_reference_used", "sha256", "status", "notes"]


def main() -> int:
    cfgmod.ensure_core_dirs()
    cfg = cfgmod.load_config()
    ref_dir = cfgmod.reference_dir(cfg)
    rows = []
    issues = []
    for dof in cfg["dofs"]:
        expected = ref_dir / f"{dof}_curated_processed.csv"
        old_heave = dof == "FD_HEAVE" and expected.name == "FD_HEAVE_processed.csv"
        if expected.exists() and not old_heave:
            status = "PASS"
            sha = cfgmod.sha256_file(expected)
            notes = "curated processed reference"
        else:
            status = "FAIL"
            sha = ""
            notes = "missing curated reference" if not expected.exists() else "old heave reference name rejected"
            issues.append(f"{dof}: {notes}")
        rows.append({
            "data_id": f"REF_{dof}",
            "dof": dof,
            "reference_file": expected.relative_to(cfgmod.workspace_root()).as_posix(),
            "reference_kind": cfg["reference"]["kind"],
            "old_heave_reference_used": str(old_heave).lower(),
            "sha256": sha,
            "status": status,
            "notes": notes,
        })
    path_text = "\n".join(str(r["reference_file"]) for r in rows)
    forbidden = cfgmod.forbidden_hits(path_text, cfg)
    if forbidden:
        issues.extend(f"forbidden reference path token: {item}" for item in forbidden)
    cfgmod.write_csv(cfgmod.registry_path("data_manifest.csv"), FIELDS, rows)
    status = "PASS" if not issues and ref_dir.exists() else "FAIL"
    lines = [
        f"- Workspace: {cfgmod.workspace_root()}",
        f"- Reference directory: {ref_dir}",
        f"- Gate status: {status}",
        f"- DOF files checked: {len(rows)}",
        f"- Old heave reference used: false",
        "",
        "## Issues",
    ]
    lines.extend([f"- {i}" for i in issues] or ["- None"])
    cfgmod.write_report("07_reports/data_gate_report.md", "Data Gate Report", lines)
    cfgmod.write_report("08_logs/data_path_integrity_check.md", "Data Path Integrity Check", lines)
    cfgmod.append_log("08_logs/harness_build_log.md", f"data_gate status={status}")
    print(f"Data Gate: {status}")
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
