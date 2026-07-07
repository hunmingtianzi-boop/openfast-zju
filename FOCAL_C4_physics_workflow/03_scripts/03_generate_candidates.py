from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()

FIELDS = [
    "candidate_id",
    "parent_candidate_id",
    "stage",
    "parameter_vector_json",
    "changed_parameters",
    "model_input_dir",
    "openfast_subdir",
    "fst_file",
    "created_time",
    "created_by",
    "status",
    "notes",
]


def default_vector() -> dict[str, Any]:
    return {
        "ed_edits": [],
        "hydrodyn_edits": [],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {},
    }


def normalize_vector(raw: dict[str, Any] | None) -> dict[str, Any]:
    vector = default_vector()
    if raw:
        for key in vector:
            if key in raw:
                vector[key] = raw[key]
    return vector


def changed_parameters(vector: dict[str, Any]) -> str:
    changed: list[str] = []
    for edit in vector.get("ed_edits", []):
        key = edit.get("key", "")
        changed.append(f"ElastoDyn.{key}")
    for edit in vector.get("hydrodyn_edits", []):
        block = edit.get("block", "")
        i = edit.get("i", "")
        if block in {"F0", "AddF0"}:
            changed.append(f"HydroDyn.AddF0[{i}]")
        else:
            j = edit.get("j", "")
            changed.append(f"HydroDyn.{block}[{i},{j}]")
    for key in sorted(vector.get("moordyn", {})):
        changed.append(f"MoorDyn.{key}")
    for key in sorted(vector.get("initial_conditions", {})):
        changed.append(f"IC.{key}")
    meta = vector.get("decision_meta") or {}
    for dof, conditions in sorted((meta.get("dof_initial_conditions") or {}).items()):
        for key in sorted(conditions or {}):
            changed.append(f"IC.{dof}.{key}")
    if vector.get("tmax"):
        changed.append("FST.TMax")
    return ";".join(changed)


def register_candidate(
    candidate_id: str,
    vector: dict[str, Any],
    stage: str,
    parent: str,
    status: str,
    notes: str,
    created_by: str,
    model_input_dir: str | None = None,
    openfast_subdir_override: str | None = None,
    fst_file_override: str | None = None,
) -> dict[str, Any]:
    cfg = cfgmod.load_config()
    if model_input_dir:
        model_root = cfgmod.resolve_workspace_path(model_input_dir)
        if not cfgmod.is_within(model_root) or not model_root.exists():
            raise SystemExit(f"model_input_dir must exist inside workspace: {model_input_dir}")
    else:
        model_root = cfgmod.starting_model_dir(cfg)
    openfast_subdir = openfast_subdir_override or cfg["model"].get("openfast_subdir", "OpenFAST_input_files")
    fst_file = fst_file_override or cfg["model"].get("fst_file", "IEA-15-240-RWT-UMaineSemi.fst")
    row = {
        "candidate_id": candidate_id,
        "parent_candidate_id": parent,
        "stage": stage,
        "parameter_vector_json": json.dumps(vector, separators=(",", ":"), ensure_ascii=False),
        "changed_parameters": changed_parameters(vector),
        "model_input_dir": model_root.relative_to(cfgmod.workspace_root()).as_posix(),
        "openfast_subdir": openfast_subdir,
        "fst_file": fst_file,
        "created_time": cfgmod.utc_now(),
        "created_by": created_by,
        "status": status,
        "notes": notes,
    }
    cfgmod.upsert_csv(cfgmod.registry_path("candidate_registry.csv"), FIELDS, "candidate_id", row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="CAND_TEST")
    parser.add_argument("--mode", default="test")
    parser.add_argument("--stage", default=None)
    parser.add_argument("--parent", default="STARTING_MODEL")
    parser.add_argument("--vector-json", default=None)
    parser.add_argument("--notes", default="")
    parser.add_argument("--model-input-dir", default=None)
    parser.add_argument("--openfast-subdir", default=None)
    parser.add_argument("--fst-file", default=None)
    args = parser.parse_args()

    cfgmod.ensure_core_dirs()
    if args.vector_json:
        raw = json.loads(args.vector_json)
        vector = normalize_vector(raw)
    else:
        vector = default_vector()

    stage = args.stage or ("mvp_test" if args.mode == "test" else args.mode)
    notes = args.notes or ("MVP unchanged starting-model candidate" if not changed_parameters(vector) else "physics candidate")
    row = register_candidate(
        args.candidate,
        vector,
        stage=stage,
        parent=args.parent,
        status="READY",
        notes=notes,
        created_by="03_generate_candidates.py",
        model_input_dir=args.model_input_dir,
        openfast_subdir_override=args.openfast_subdir,
        fst_file_override=args.fst_file,
    )
    cfgmod.write_report("07_reports/candidate_design_report.md", "Candidate Design Report", [
        f"- Candidate: {args.candidate}",
        f"- Stage: {stage}",
        f"- Model package directory: {row['model_input_dir']}",
        f"- OpenFAST subdir: {row['openfast_subdir']}",
        f"- FST file: {row['fst_file']}",
        f"- Changed parameters: {row['changed_parameters'] or 'none'}",
        f"- Notes: {notes}",
    ])
    cfgmod.append_log("08_logs/harness_build_log.md", f"candidate_generated candidate={args.candidate}")
    print(f"Candidate ready: {args.candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
