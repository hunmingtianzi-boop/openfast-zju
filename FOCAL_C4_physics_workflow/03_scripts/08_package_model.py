from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--skip-objective-review", action="store_true")
    args = parser.parse_args()
    cfgmod.ensure_core_dirs()
    dst = cfgmod.workspace_root() / "04_current_runs" / "packaged_models" / args.candidate
    summaries = cfgmod.read_csv(cfgmod.registry_path("candidate_summary.csv"))
    summary = next((r for r in summaries if r.get("candidate_id") == args.candidate), None)
    if not summary or summary.get("decision") != "PACKAGE_READY":
        if dst.exists():
            shutil.rmtree(dst)
        cfgmod.write_report("07_reports/package_report_todo.md", "Package Model Held", [
            f"- Candidate: {args.candidate}",
            "- Status: HOLD",
            "- Reason: candidate is not PACKAGE_READY in candidate_summary.csv.",
            "- No model package was created.",
        ])
        print("Packager: HOLD")
        return 2
    if not args.skip_objective_review:
        reviews = cfgmod.read_csv(cfgmod.registry_path("objective_review_registry.csv"))
        review = next((r for r in reviews if r.get("candidate_id") == args.candidate), None)
        if review and review.get("decision") != "OBJECTIVE_REVIEW_PASS":
            if dst.exists():
                shutil.rmtree(dst)
            cfgmod.write_report("07_reports/package_report_todo.md", "Package Model Held", [
                f"- Candidate: {args.candidate}",
                "- Status: HOLD",
                f"- Reason: objective review gate is {review.get('decision')}: {review.get('reason')}",
                "- Run `03_scripts/19_objective_review_agent.py` again after the target DOF has been improved.",
                "- No model package was created.",
            ])
            print("Packager: HOLD_OBJECTIVE_REVIEW")
            return 2
    cand = next((r for r in cfgmod.read_csv(cfgmod.registry_path("candidate_registry.csv")) if r.get("candidate_id") == args.candidate), None)
    if not cand:
        raise SystemExit(f"candidate not found: {args.candidate}")
    src = cfgmod.resolve_workspace_path(cand["model_input_dir"])
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    cfgmod.write_report("07_reports/package_report.md", "Package Model Report", [
        f"- Candidate: {args.candidate}",
        "- Status: PACKAGE_READY",
        f"- Package directory: {dst.relative_to(cfgmod.workspace_root()).as_posix()}",
        "- Note: only six-DOF passing candidates are packaged.",
    ])
    print("Packager: PACKAGE_READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
