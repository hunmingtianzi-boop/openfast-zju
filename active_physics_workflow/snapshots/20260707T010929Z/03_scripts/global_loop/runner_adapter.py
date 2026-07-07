from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import config
from .schema import DOF_TO_CHANNEL, DOFS, DofTarget, Observation, RunCard, TargetTruth
from .scoring import loop_verdict_from_step, score_observation, verdict_from_objective


metricmod = config._load_script("05_evaluate_metrics.py", "active_workflow_metric_evaluator")


def build_target_truth(cfg: dict[str, Any] | None = None) -> TargetTruth:
    cfg = cfg or config.load_config()
    targets: dict[str, DofTarget] = {}
    thresholds = {
        "targets": cfg.get("targets", {}),
        "physics": cfg.get("physics", {}),
    }
    ref_dir = config.reference_dir(cfg)
    for dof in DOFS:
        channel = DOF_TO_CHANNEL[dof]
        ref_file = ref_dir / f"{dof}_curated_processed.csv"
        ref = metricmod.read_reference(ref_file)
        tD, xD, eD, i0, i1, _ = metricmod.decay_seg(ref["t"], ref[channel])
        period = metricmod.zero_cross_period(tD, xD)[0]
        zeta = metricmod.zeta_from_seg(tD, eD, period)
        ref_leak = _reference_leak(ref, channel, i0, i1)
        targets[dof] = DofTarget(
            dof=dof,
            channel=channel,
            reference_file=ref_file.relative_to(config.workspace_root()).as_posix(),
            period=period,
            zeta=zeta,
            decay_start=float(xD[0]) if len(xD) else None,
            reference_leak=ref_leak,
        )
    return TargetTruth(
        created_at=config.utc_now(),
        source="active_curated_processed_reference",
        dofs=targets,
        thresholds=thresholds,
    )


def import_registry_run_cards(
    candidate_ids: list[str] | None = None,
    stage: str = "offline_import",
    parent_objective: dict[str, Any] | None = None,
) -> list[RunCard]:
    cfg = config.load_config()
    config.assert_active_paths(cfg)
    target = build_target_truth(cfg)
    candidates = {row.get("candidate_id", ""): row for row in config.read_csv(config.registry_path("candidate_registry.csv"))}
    runs_by_id = {row.get("run_id", ""): row for row in config.read_csv(config.registry_path("run_registry.csv"))}
    summaries = {row.get("candidate_id", ""): row for row in config.read_csv(config.registry_path("candidate_summary.csv"))}
    result_rows = config.read_csv(config.registry_path("results_registry.csv"))
    allowed = set(candidate_ids or [])
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in result_rows:
        candidate = row.get("candidate_id", "")
        if allowed and candidate not in allowed:
            continue
        if not candidate:
            continue
        grouped.setdefault(candidate, []).append(row)

    cards: list[RunCard] = []
    for candidate, rows in sorted(grouped.items()):
        cand = candidates.get(candidate, {})
        observation = observation_from_result_rows(candidate, rows, runs_by_id)
        objective = score_observation(observation, cfg, parent_objective=parent_objective)
        verdict = verdict_from_objective(objective, parent_objective=parent_objective)
        loop_verdict = loop_verdict_from_step(verdict, objective)
        group = parameter_group_for_candidate(cand)
        policy_status = loop_verdict.status if loop_verdict.status == "DIAGNOSTIC_FORK" else verdict.status
        policy = artifact_policy_for(candidate, cand, summaries.get(candidate, {}), policy_status)
        run_id = f"RC_{candidate}"
        cards.append(RunCard(
            run_id=run_id,
            parent_id=cand.get("parent_candidate_id", ""),
            stage=stage or cand.get("stage", "") or "offline_import",
            parameter_group=group,
            parameter_delta=parameter_delta_for_candidate(cand),
            input_hash=_candidate_input_hash(observation),
            target_source=target.source,
            observation=observation,
            objective=objective,
            verdict=verdict,
            artifact_policy=policy,
            created_at=config.utc_now(),
            evidence_sources=_evidence_sources(rows, runs_by_id),
            predicate_results=objective.get("predicate_results", []),
            step_decision=verdict,
            loop_verdict=loop_verdict,
            source_roles={
                "target_truth": "oracle",
                "observation": "observation",
                "objective": "derived_metric",
                "coupling_memory": "memory_inference",
            },
            review_status="pending" if loop_verdict.status == "DIAGNOSTIC_FORK" else "not_required",
        ))
    return cards


def observation_from_result_rows(
    candidate_id: str,
    rows: list[dict[str, str]],
    runs_by_id: dict[str, dict[str, str]],
) -> Observation:
    latest_by_dof: dict[str, dict[str, str]] = {}
    ignored: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        dof = row.get("dof", "")
        if not dof:
            continue
        run = runs_by_id.get(row.get("run_id", ""), {})
        if not _usable_result_row(row, run):
            ignored.append(_ignored_result(row, run, "not PASS+SUCCESS"))
            continue
        previous = latest_by_dof.get(dof)
        if previous is None or _result_sort_key(row, run, idx) > _result_sort_key(
            previous,
            runs_by_id.get(previous.get("run_id", ""), {}),
            -1,
        ):
            latest_by_dof[dof] = row

    per_dof: dict[str, dict[str, Any]] = {}
    cross: dict[str, dict[str, float]] = {}
    ref_cross: dict[str, dict[str, float]] = {}
    artifacts: dict[str, Any] = {"runs": {}, "ignored_results": ignored}
    for dof, row in latest_by_dof.items():
        metrics = {
            "run_id": row.get("run_id", ""),
            "metric_status": row.get("metric_status", ""),
            "period": _float_or_none(row.get("period")),
            "reference_period": _float_or_none(row.get("reference_period")),
            "period_error_pct": _float_or_none(row.get("period_error_pct")),
            "zeta": _float_or_none(row.get("zeta")),
            "reference_zeta": _float_or_none(row.get("reference_zeta")),
            "nrmse": _float_or_none(row.get("nrmse")),
            "j_dof": _float_or_none(row.get("j_dof")),
            "j_shape": _float_or_none(row.get("j_shape")),
            "envelope_error": _float_or_none(row.get("envelope_error")),
            "ler_error": _float_or_none(row.get("ler_error")),
            "fp_error": _float_or_none(row.get("fp_error")),
            "start_amplitude_error": _float_or_none(row.get("start_amplitude_error")),
        }
        leak = _json_dict(row.get("leak_json"))
        ref_leak = _json_dict(row.get("reference_leak_json"))
        metrics["leak"] = leak
        metrics["reference_leak"] = ref_leak
        metrics["morph"] = _json_dict(row.get("morph_json"))
        per_dof[dof] = metrics
        cross[dof] = {str(k): float(v) for k, v in leak.items() if _is_number(v)}
        ref_cross[dof] = {str(k): float(v) for k, v in ref_leak.items() if _is_number(v)}

        run = runs_by_id.get(row.get("run_id", ""), {})
        run_root = _run_root_from_registry_row(run)
        output_path = _main_output_path(run)
        artifacts["runs"][dof] = {
            "run_id": row.get("run_id", ""),
            "run_dir": run_root,
            "logs_dir": f"{run_root}/logs" if run_root else "",
            "manifest_path": f"{run_root}/manifest.json" if run_root else "",
            "fst_file": run.get("fst_file", ""),
            "input_dir": run.get("input_dir", ""),
            "output_dir": run.get("output_dir", ""),
            "main_output_path": output_path,
            "stdout_path": run.get("stdout_path", ""),
            "stderr_path": run.get("stderr_path", ""),
            "notes": row.get("notes", ""),
        }
    return Observation(
        candidate_id=candidate_id,
        source="active_registry_results",
        per_dof_metrics=per_dof,
        cross_energy_matrix=cross,
        reference_cross_energy_matrix=ref_cross,
        equilibrium={},
        artifacts=artifacts,
    )


def run_candidate_suite(candidate_id: str, dofs: list[str] | None = None, continue_on_failure: bool = True) -> int:
    suite_id = f"{candidate_id}_{config.utc_now().replace('-', '').replace(':', '').replace('+', 'Z')}"
    args = [
        sys.executable,
        str(config.SCRIPT_DIR / "13_run_candidate_sixdof.py"),
        "--candidate",
        candidate_id,
        "--suite-id",
        suite_id,
    ]
    if dofs:
        args.extend(["--dofs", ",".join(dofs)])
    if continue_on_failure:
        args.append("--continue-on-failure")
    proc = subprocess.run(args, cwd=config.workspace_root())
    return int(proc.returncode)


def _run_root_from_registry_row(run: dict[str, str]) -> str:
    input_dir = run.get("input_dir", "")
    if input_dir:
        path = Path(input_dir.replace("\\", "/"))
        parts = path.parts
        if len(parts) >= 4 and parts[-1] == "input":
            return Path(*parts[:-1]).as_posix()
    run_id = run.get("run_id", "")
    return f"04_current_runs/runs/{run_id}" if run_id else ""


def _main_output_path(run: dict[str, str]) -> str:
    fst_file = run.get("fst_file", "")
    if not fst_file:
        return ""
    direct = Path(fst_file).with_suffix(".out").as_posix()
    direct_path = config.workspace_root() / direct
    if direct_path.exists():
        return direct
    output_dir = run.get("output_dir", "")
    if output_dir:
        candidate = config.workspace_root() / output_dir / Path(fst_file).with_suffix(".out").name
        if candidate.exists():
            return candidate.relative_to(config.workspace_root()).as_posix()
    return direct


def parameter_group_for_candidate(cand: dict[str, str]) -> str:
    text = " ".join([
        cand.get("candidate_id", ""),
        cand.get("stage", ""),
        cand.get("changed_parameters", ""),
        cand.get("notes", ""),
    ]).lower()
    vector = _candidate_vector(cand)
    if vector.get("moordyn"):
        return "mooring"
    if "rna" in text or "gravity" in text:
        return "rna_balance"
    meta = vector.get("decision_meta") or {}
    if meta.get("signed_ic_bquad_tune") or meta.get("bquad_multipliers"):
        return "diagonal_hydro"
    if "bqic" in text or "bquad" in text or "signed_ic" in text:
        return "diagonal_hydro"
    packages = meta.get("dof_packages") or {}
    if packages and any("bquad_tuned" in json.dumps(spec).lower() for spec in packages.values()):
        return "diagonal_hydro"
    edits = vector.get("hydrodyn_edits") or []
    if edits:
        if all(int(e.get("i", -1)) == int(e.get("j", -2)) for e in edits):
            return "diagonal_hydro"
        return "coupling_disabled"
    return "baseline"


def parameter_delta_for_candidate(cand: dict[str, str]) -> dict[str, Any]:
    vector = _candidate_vector(cand)
    meta = vector.get("decision_meta") or {}
    return {
        "hydrodyn_edits": vector.get("hydrodyn_edits", []),
        "moordyn": vector.get("moordyn", {}),
        "initial_conditions": vector.get("initial_conditions", {}),
        "tmax": vector.get("tmax"),
        "decision_meta": {
            "signed_ic_bquad_tune": meta.get("signed_ic_bquad_tune"),
            "selected_dof_candidates": meta.get("selected_dof_candidates", {}),
            "bquad_multipliers": meta.get("bquad_multipliers", {}),
            "signed_initial_conditions": meta.get("signed_initial_conditions", {}),
            "dof_packages": sorted((meta.get("dof_packages") or {}).keys()),
        },
        "changed_parameters": cand.get("changed_parameters", ""),
    }


def artifact_policy_for(candidate_id: str, cand: dict[str, str], summary: dict[str, str], verdict_status: str) -> str:
    stage = cand.get("stage", "").lower()
    decision = summary.get("decision", "").upper()
    rank = summary.get("rank", "")
    if "baseline" in stage or "base" in candidate_id.lower():
        return "retain_full_baseline"
    if verdict_status in {"ACCEPTED", "DIAGNOSTIC_FORK"}:
        return "retain_full"
    if decision == "PACKAGE_READY" and rank == "1":
        return "retain_full_stage_best"
    return "summary_only"


def _candidate_vector(cand: dict[str, str]) -> dict[str, Any]:
    try:
        return json.loads(cand.get("parameter_vector_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _candidate_input_hash(observation: Observation) -> str:
    for item in observation.artifacts.get("runs", {}).values():
        run_id = item.get("run_id", "")
        if run_id:
            return run_id
    return ""


def _evidence_sources(rows: list[dict[str, str]], runs_by_id: dict[str, dict[str, str]]) -> list[str]:
    sources: list[str] = []
    for row in rows:
        run = runs_by_id.get(row.get("run_id", ""), {})
        for key in ("fst_file", "input_dir", "output_dir"):
            if run.get(key):
                sources.append(run[key])
    return sorted(set(sources))


def _reference_leak(ref: dict[str, Any], primary: str, i0: int, i1: int) -> dict[str, float]:
    import numpy as np

    p_seg = ref[primary][i0 : i1 + 1]
    p_rms = float(np.sqrt(np.mean((p_seg - np.median(p_seg[-len(p_seg) // 4 :])) ** 2)))
    leak: dict[str, float] = {}
    for channel in ("Surge", "Sway", "Heave", "Roll", "Pitch", "Yaw"):
        if channel == primary:
            continue
        seg = ref[channel][i0 : i1 + 1]
        leak[channel] = float(np.sqrt(np.mean((seg - np.median(seg[-len(seg) // 4 :])) ** 2)) / max(p_rms, 1e-12))
    return leak


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _float_or_none(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _usable_result_row(row: dict[str, str], run: dict[str, str]) -> bool:
    return (
        str(row.get("metric_status", "")).upper() == "PASS"
        and str(run.get("status", "")).upper() == "SUCCESS"
        and bool(row.get("run_id"))
    )


def _result_sort_key(row: dict[str, str], run: dict[str, str], idx: int) -> tuple[str, str, str, int]:
    return (
        str(run.get("end_time") or ""),
        str(run.get("start_time") or ""),
        str(row.get("run_id") or ""),
        idx,
    )


def _ignored_result(row: dict[str, str], run: dict[str, str], reason: str) -> dict[str, str]:
    return {
        "run_id": row.get("run_id", ""),
        "dof": row.get("dof", ""),
        "metric_status": row.get("metric_status", ""),
        "run_status": run.get("status", ""),
        "reason": reason,
    }
