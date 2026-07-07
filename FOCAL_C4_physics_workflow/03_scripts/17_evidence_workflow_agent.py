from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _loader():
    spec = importlib.util.spec_from_file_location("cfg", Path(__file__).with_name("00_config_loader.py"))
    cfg = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(cfg)
    return cfg


cfgmod = _loader()
ROOT = cfgmod.workspace_root()
LOG_DIR = ROOT / "08_logs" / "evidence_workflow"
REPORT_DIR = ROOT / "07_reports" / "evidence_workflow"
LATEST_STATE = LOG_DIR / "latest_evidence_workflow_state.json"
LATEST_REPORT = REPORT_DIR / "latest_evidence_workflow_report.md"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def run_cmd(args: list[str], timeout_sec: int = 120) -> dict[str, Any]:
    t0 = time.time()
    proc = subprocess.run(
        args,
        cwd=str(ROOT),
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    return {
        "args": args,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "walltime_s": time.time() - t0,
        "stdout_tail": "\n".join((proc.stdout or "").splitlines()[-60:]),
        "stderr_tail": "\n".join((proc.stderr or "").splitlines()[-60:]),
    }


def candidate_run_summary(candidate_id: str | None) -> dict[str, Any]:
    rows = read_csv(cfgmod.registry_path("run_registry.csv"))
    if candidate_id:
        rows = [r for r in rows if r.get("candidate_id") == candidate_id]
    latest: dict[str, dict[str, str]] = {}
    for row in rows:
        dof = row.get("dof", "")
        if dof and (dof not in latest or row.get("end_time", "") > latest[dof].get("end_time", "")):
            latest[dof] = row
    return {
        "candidate_id": candidate_id,
        "dofs": {
            dof: {
                "status": row.get("status"),
                "run_id": row.get("run_id"),
                "elapsed_sec": row.get("elapsed_sec"),
                "return_code": row.get("return_code"),
            }
            for dof, row in sorted(latest.items())
        },
    }


def candidate_metric_summary(candidate_id: str | None) -> dict[str, Any]:
    rows = read_csv(cfgmod.registry_path("results_registry.csv"))
    if candidate_id:
        run_rows = read_csv(cfgmod.registry_path("run_registry.csv"))
        run_ids = {r.get("run_id") for r in run_rows if r.get("candidate_id") == candidate_id}
        rows = [r for r in rows if r.get("run_id") in run_ids]
    latest: dict[str, dict[str, str]] = {}
    run_rows_by_id = {r.get("run_id"): r for r in read_csv(cfgmod.registry_path("run_registry.csv"))}
    for row in rows:
        run = run_rows_by_id.get(row.get("run_id"), {})
        dof = row.get("dof", "") or run.get("dof", "")
        if dof and (dof not in latest or row.get("run_id", "") > latest[dof].get("run_id", "")):
            latest[dof] = row
    return {
        "dofs": {
            dof: {
                "metric_status": row.get("metric_status"),
                "period_error_pct": row.get("period_error_pct"),
                "envelope_error": row.get("envelope_error"),
                "ler_error": row.get("ler_error"),
                "fp_error": row.get("fp_error"),
            }
            for dof, row in sorted(latest.items())
        }
    }


def run_mainline_free_decay(candidate_id: str, dofs: str, continue_on_failure: bool) -> dict[str, Any]:
    args = [sys.executable, "03_scripts/13_run_candidate_sixdof.py", "--candidate", candidate_id]
    if dofs:
        args += ["--dofs", dofs]
    if continue_on_failure:
        args.append("--continue-on-failure")
    return run_cmd(args, timeout_sec=7200)


def run_shadow(candidate_id: str | None, no_record: bool) -> dict[str, Any]:
    args = [sys.executable, "03_scripts/16_shadow_scout_agent.py"]
    if candidate_id:
        args += ["--candidate", candidate_id]
    if no_record:
        args.append("--no-record")
    result = run_cmd(args, timeout_sec=180)
    state_path = ROOT / "08_logs" / "shadow_scout" / "latest_shadow_scout_state.json"
    shadow_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    alarms = []
    for analysis in shadow_state.get("analyses", []):
        for finding in analysis.get("findings", []):
            if finding.get("verdict") == "shadow_alarm":
                alarms.append({
                    "candidate_id": analysis.get("candidate_id"),
                    "dof": finding.get("dof"),
                    "observation": finding.get("observation"),
                    "next_action": finding.get("next_action"),
                })
    return {
        "command": result,
        "state": str(state_path),
        "report": str(ROOT / "07_reports" / "shadow_scout" / "latest_shadow_scout_report.md"),
        "alarms": alarms,
    }


def run_objective_review(candidate_id: str | None, target_dofs: str) -> dict[str, Any] | None:
    if not target_dofs:
        return None
    if not candidate_id:
        raise SystemExit("--review-target-dofs requires --candidate")
    args = [
        sys.executable,
        "03_scripts/19_objective_review_agent.py",
        "--candidate",
        candidate_id,
        "--target-dofs",
        target_dofs,
    ]
    result = run_cmd(args, timeout_sec=180)
    state_path = ROOT / "08_logs" / "objective_review" / "latest_objective_review_state.json"
    review_state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    return {
        "command": result,
        "state": str(state_path),
        "report": str(ROOT / "07_reports" / "objective_review_report.md"),
        "decision": review_state.get("decision"),
        "reason": review_state.get("reason"),
        "target_dofs": review_state.get("target_dofs", []),
    }


def followups_from_alarms(alarms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for alarm in alarms:
        dof = alarm.get("dof")
        if dof in {"FD_ROLL", "FD_SWAY"}:
            actions.append({
                "id": f"FOLLOWUP-{dof}-RNA-GEOMETRY",
                "trigger": alarm,
                "evidence_level": "requires_free_decay_or_causal_falsification",
                "recommended_action": "Run RNA/geometric coupling on/off or dose-response; do not tune a scalar damping knob from scouting alone.",
            })
        elif dof in {"FD_SURGE", "FD_YAW"}:
            actions.append({
                "id": f"FOLLOWUP-{dof}-MOORING",
                "trigger": alarm,
                "evidence_level": "requires_free_decay_or_causal_falsification",
                "recommended_action": "Use physics-agent MoorDyn EA/UnstrLen path and inspect equilibrium drift/tension before any AddCLin path.",
            })
        else:
            actions.append({
                "id": f"FOLLOWUP-{dof or 'GLOBAL'}",
                "trigger": alarm,
                "evidence_level": "requires_free_decay_or_causal_falsification",
                "recommended_action": "Repeat or falsify through an authoritative project workflow gate.",
            })
    return actions


def render_report(state: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Evidence Workflow Report",
        "",
        f"Generated: {state['generated_at']}",
        f"Candidate: `{state.get('candidate_id') or 'all/latest'}`",
        f"Overall: `{state['overall_status']}`",
        "",
        "## Gates",
        "",
    ]
    for gate in state["gates"]:
        lines.append(f"### {gate['name']} | pass={gate['pass']}")
        lines.append(gate["summary"])
        lines.append("")
    lines.append("## Candidate Run Summary")
    lines.append("")
    for dof, item in state["run_summary"]["dofs"].items():
        lines.append(f"- `{dof}`: {item['status']} ({item['run_id']})")
    lines.append("")
    lines.append("## Shadow Alarms")
    lines.append("")
    if not state["shadow"]["alarms"]:
        lines.append("No shadow alarms.")
    else:
        for alarm in state["shadow"]["alarms"]:
            lines.append(f"- `{alarm['candidate_id']}` `{alarm['dof']}`: {alarm['observation']}")
            lines.append(f"  Next: {alarm['next_action']}")
    lines.append("")
    lines.append("## Follow-Up Actions")
    lines.append("")
    if not state["followup_actions"]:
        lines.append("No follow-up actions.")
    else:
        for action in state["followup_actions"]:
            lines.append(f"- `{action['id']}`: {action['recommended_action']}")
    lines.append("")
    lines.append("## Objective Review")
    lines.append("")
    if not state.get("objective_review"):
        lines.append("Not requested.")
    else:
        review = state["objective_review"]
        lines.append(f"- Decision: `{review.get('decision')}`")
        lines.append(f"- Target DOFs: `{','.join(review.get('target_dofs') or [])}`")
        lines.append(f"- Reason: {review.get('reason')}")
    lines.append("")
    lines.append("## Boundary")
    lines.append("")
    lines.append("Shadow scouting is non-authoritative. Final candidate recommendation remains controlled by the project candidate selector, metrics gates, consistency checks, and manual package decision.")
    LATEST_REPORT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run(candidate_id: str | None, no_record_shadow: bool, run_mainline: bool, dofs: str, continue_on_failure: bool, review_target_dofs: str) -> dict[str, Any]:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    data_gate = run_cmd([sys.executable, "03_scripts/01_data_gate.py"])
    model_gate = run_cmd([sys.executable, "03_scripts/02_model_gate.py"])
    mainline = None
    if run_mainline:
        if not candidate_id:
            raise SystemExit("--run-mainline requires --candidate")
        mainline = run_mainline_free_decay(candidate_id, dofs, continue_on_failure)
    run_summary = candidate_run_summary(candidate_id)
    metric_summary = candidate_metric_summary(candidate_id)
    shadow = run_shadow(candidate_id, no_record_shadow)
    objective_review = run_objective_review(candidate_id, review_target_dofs)
    followups = followups_from_alarms(shadow["alarms"])
    gates = [
        {"name": "Data gate", "pass": data_gate["ok"], "summary": "Project curated reference data gate.", "command": data_gate},
        {"name": "Model gate", "pass": model_gate["ok"], "summary": "Project model/path audit gate.", "command": model_gate},
        {
            "name": "Mainline free-decay runner",
            "pass": True if mainline is None else mainline["ok"],
            "summary": "Skipped; using existing run registry." if mainline is None else "Ran project six-DOF free-decay runner before evidence gates.",
            "command": mainline,
        },
        {"name": "Run registry gate", "pass": bool(run_summary["dofs"]), "summary": "At least one project run exists for the requested scope."},
        {"name": "Shadow scout gate", "pass": shadow["command"]["ok"], "summary": "Non-authoritative project shadow scouting completed."},
    ]
    if objective_review:
        gates.append({
            "name": "Objective review gate",
            "pass": objective_review.get("decision") == "OBJECTIVE_REVIEW_PASS",
            "summary": objective_review.get("reason") or "Target-aware package review.",
            "command": objective_review.get("command"),
        })
    if not all(g["pass"] for g in gates):
        overall = "blocked"
    elif followups:
        overall = "scouting_mixed_requires_project_followup"
    else:
        overall = "pass_no_shadow_alarm"
    state = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "candidate_id": candidate_id,
        "overall_status": overall,
        "gates": gates,
        "run_summary": run_summary,
        "metric_summary": metric_summary,
        "shadow": shadow,
        "objective_review": objective_review,
        "followup_actions": followups,
    }
    LATEST_STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    render_report(state)
    return state


def main() -> None:
    ap = argparse.ArgumentParser(description="Project-native evidence workflow orchestrator.")
    ap.add_argument("--candidate", help="Scope to one candidate id.")
    ap.add_argument("--run-mainline", action="store_true", help="Run the project mainline free-decay six-DOF runner before shadow/evidence gates.")
    ap.add_argument("--dofs", default="", help="Optional comma-separated DOF list passed to 13_run_candidate_sixdof.py.")
    ap.add_argument("--continue-on-failure", action="store_true", help="Continue mainline six-DOF runner after a DOF failure.")
    ap.add_argument("--no-record-shadow", action="store_true", help="Do not append shadow scout records.")
    ap.add_argument("--review-target-dofs", default="", help="Optional comma-separated target DOFs for package objective review, e.g. FD_SURGE.")
    ap.add_argument("--status", action="store_true", help="Print latest state path and status.")
    args = ap.parse_args()
    if args.status:
        state = json.loads(LATEST_STATE.read_text(encoding="utf-8")) if LATEST_STATE.exists() else {}
        print(json.dumps({
            "state": str(LATEST_STATE) if LATEST_STATE.exists() else None,
            "report": str(LATEST_REPORT) if LATEST_REPORT.exists() else None,
            "overall_status": state.get("overall_status"),
            "generated_at": state.get("generated_at"),
        }, indent=2, ensure_ascii=False))
        return
    state = run(args.candidate, args.no_record_shadow, args.run_mainline, args.dofs, args.continue_on_failure, args.review_target_dofs)
    print(json.dumps({
        "overall_status": state["overall_status"],
        "report": str(LATEST_REPORT),
        "state": str(LATEST_STATE),
        "followups": len(state["followup_actions"]),
    }, indent=2, ensure_ascii=False))
    if state["overall_status"] == "blocked":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
