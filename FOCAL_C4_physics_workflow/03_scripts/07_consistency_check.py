from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
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
    parser.add_argument("--candidate", default="CAND_TEST")
    parser.add_argument("--dof", default="FD_ROLL")
    args = parser.parse_args()
    cfgmod.ensure_core_dirs()
    before_results = [r for r in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv")) if r.get("candidate_id") == args.candidate and r.get("dof") == args.dof and r.get("metric_status") == "PASS"]
    runs = [r for r in cfgmod.read_csv(cfgmod.registry_path("run_registry.csv")) if r.get("candidate_id") == args.candidate and r.get("dof") == args.dof]
    status = "SKIPPED"
    notes = "requires a successful baseline run and evaluated metrics"
    clean_run_id = ""
    comparison = ""
    if before_results:
        baseline = before_results[-1]
        run_cmd = [sys.executable, str(Path(__file__).with_name("04_run_executor.py")), "--candidate", args.candidate, "--dof", args.dof]
        eval_cmd = [sys.executable, str(Path(__file__).with_name("05_evaluate_metrics.py")), "--candidate", args.candidate, "--dof", args.dof]
        run_proc = subprocess.run(run_cmd, cwd=cfgmod.workspace_root(), text=True, capture_output=True)
        if run_proc.returncode != 0:
            status = "FAIL"
            notes = f"clean rerun failed: {run_proc.stdout.strip()} {run_proc.stderr.strip()}".strip()
        else:
            eval_proc = subprocess.run(eval_cmd, cwd=cfgmod.workspace_root(), text=True, capture_output=True)
            all_runs = [r for r in cfgmod.read_csv(cfgmod.registry_path("run_registry.csv")) if r.get("candidate_id") == args.candidate and r.get("dof") == args.dof]
            clean_run_id = all_runs[-1]["run_id"] if all_runs else ""
            after_results = [r for r in cfgmod.read_csv(cfgmod.registry_path("results_registry.csv")) if r.get("run_id") == clean_run_id]
            if eval_proc.returncode != 0 or not after_results or after_results[-1].get("metric_status") != "PASS":
                status = "FAIL"
                notes = f"clean rerun metric evaluation failed: {eval_proc.stdout.strip()} {eval_proc.stderr.strip()}".strip()
            else:
                clean = after_results[-1]
                period_delta = abs(float(clean["period"]) - float(baseline["period"]))
                nrmse_delta = abs(float(clean["nrmse"]) - float(baseline["nrmse"]))
                comparison = f"period_delta={period_delta:.6g}; nrmse_delta={nrmse_delta:.6g}"
                if period_delta <= 1e-3 and nrmse_delta <= 1e-6:
                    status = "PASS"
                    notes = "clean rerun metrics match cached metrics within tolerance"
                else:
                    status = "FAIL"
                    notes = "clean rerun metrics differ from cached metrics"
    lines = [
        f"- Candidate: {args.candidate}",
        f"- DOF: {args.dof}",
        f"- Matching runs: {len(runs)}",
        f"- Passing baseline results before check: {len(before_results)}",
        f"- Clean rerun ID: {clean_run_id}",
        f"- Consistency status: {status}",
        f"- Comparison: {comparison or 'not available'}",
        f"- Notes: {notes}",
    ]
    cfgmod.write_report("07_reports/consistency_check_report.md", "Consistency Check Report", lines)
    cfgmod.write_report("08_logs/provenance_check.md", "Provenance Check", lines)
    cfgmod.append_log("08_logs/harness_build_log.md", f"consistency_check candidate={args.candidate} dof={args.dof} status={status}")
    print(f"Consistency Checker: {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
