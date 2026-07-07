from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import config
from .schema import DOF_TO_CHANNEL, DofLocalState, LocalAction


DOF_CFG = {
    "FD_SURGE": {"idx": 0, "name": "Surge", "tmax": 900},
    "FD_SWAY": {"idx": 1, "name": "Sway", "tmax": 900},
    "FD_HEAVE": {"idx": 2, "name": "Heave", "tmax": 400},
    "FD_ROLL": {"idx": 3, "name": "Roll", "tmax": 520},
    "FD_PITCH": {"idx": 4, "name": "Pitch", "tmax": 520},
    "FD_YAW": {"idx": 5, "name": "Yaw", "tmax": 700},
}


executor = config._load_script("04_run_executor.py", "active_workflow_run_executor_for_local_actions")


def actions_for_dof(
    state: DofLocalState,
    parent_candidate_id: str,
    target_decay_start: float | None,
) -> list[LocalAction]:
    mode = state.dominant_error_mode
    if mode == "leak":
        return [_diagnostic_action(state, "coupling leak dominates; do not scalar-polish this DOF")]
    if mode == "period":
        return [_diagnostic_action(state, "period error indicates a mechanism search before local damping polish")]
    if mode == "missing":
        return [_diagnostic_action(state, "missing metrics; rerun/import full 6DOF before local polish")]
    if mode == "fp":
        action = _bquad_small_step_action(
            state,
            parent_candidate_id,
            target_decay_start,
            action_type="fp_bquad_probe",
            factor=_probe_bquad_factor(state, step=0.025),
            risk_tags=["full_6dof_required", "limited_probe", "fp_probe"],
        )
        if action.registration_ready:
            return [action]
        return [action, _diagnostic_action(state, "BQuad value unavailable; inspect candidate package before fp probe")]
    if mode == "ler":
        action = _bquad_small_step_action(
            state,
            parent_candidate_id,
            target_decay_start,
            action_type="ler_bquad_probe",
            factor=_probe_bquad_factor(state, step=0.025),
            risk_tags=["full_6dof_required", "limited_probe", "ler_probe"],
        )
        if action.registration_ready:
            return [action]
        return [action, _diagnostic_action(state, "BQuad value unavailable; inspect candidate package before ler probe")]
    if mode not in {"envelope", "zeta", "zeta_signed_ratio"}:
        return [_diagnostic_action(state, f"{mode} error is not safe for first-pass local BQuad materialization")]
    action = _bquad_small_step_action(state, parent_candidate_id, target_decay_start)
    if action.registration_ready:
        return [action]
    return [action, _diagnostic_action(state, "BQuad value unavailable; inspect candidate package before local polish")]


def _bquad_small_step_action(
    state: DofLocalState,
    parent_candidate_id: str,
    target_decay_start: float | None,
    action_type: str = "bquad_small_step",
    factor: float | None = None,
    risk_tags: list[str] | None = None,
) -> LocalAction:
    dof = state.dof
    cfg = DOF_CFG[dof]
    bquad = _current_bquad(parent_candidate_id, dof)
    if bquad is None:
        return LocalAction(
            action_type=action_type,
            parameter_group="diagonal_hydro",
            vector=_meta_only_vector(dof, action_type),
            registration_ready=False,
            reason_not_ready=f"could not read current AddBQuad[{cfg['idx']},{cfg['idx']}] for {parent_candidate_id}",
            expected_primary_effect="diagnose diagonal damping direction",
            expected_side_effects=_side_effects_for(dof),
            risk_tags=["requires_parameter_read"],
        )
    factor = factor if factor is not None else _bquad_factor(state)
    vector = {
        "hydrodyn_edits": [{
            "block": "BQuad",
            "i": int(cfg["idx"]),
            "j": int(cfg["idx"]),
            "value": float(bquad * factor),
        }],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {
            "local_loop_v1": True,
            "action_type": action_type,
            "target_dof": dof,
            "bquad_base": float(bquad),
            "bquad_multiplier": factor,
            "requires_full_sixdof": True,
        },
    }
    signed_ic = _signed_ic(dof, target_decay_start)
    if signed_ic:
        vector["decision_meta"]["dof_initial_conditions"] = {dof: signed_ic}
    return LocalAction(
        action_type=action_type,
        parameter_group="diagonal_hydro",
        vector=vector,
        registration_ready=True,
        expected_primary_effect=f"{dof} diagonal BQuad x {factor:.3g}",
        expected_side_effects=_side_effects_for(dof),
        risk_tags=risk_tags or ["full_6dof_required"],
    )


def _mooring_probe_action(state: DofLocalState) -> LocalAction:
    dof = state.dof
    vector = {
        "hydrodyn_edits": [],
        "moordyn": {"EA_scale": 1.05},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {
            "local_loop_v1": True,
            "action_type": "mooring_ea_probe",
            "target_dof": dof,
            "requires_full_sixdof": True,
        },
    }
    return LocalAction(
        action_type="mooring_ea_probe",
        parameter_group="mooring",
        vector=vector,
        registration_ready=True,
        expected_primary_effect=f"{dof} low-frequency period/mechanism probe via MoorDyn EA_scale",
        expected_side_effects=["FD_SURGE", "FD_YAW", "FD_SWAY"],
        risk_tags=["mechanism_probe", "full_6dof_required"],
    )


def _diagnostic_action(state: DofLocalState, reason: str) -> LocalAction:
    return LocalAction(
        action_type="diagnostic_review",
        parameter_group="coupling_disabled" if state.dominant_error_mode == "leak" else "diagonal_hydro",
        vector=_meta_only_vector(state.dof, "diagnostic_review"),
        registration_ready=False,
        reason_not_ready=reason,
        expected_primary_effect="increase information before materializing a local candidate",
        expected_side_effects=_side_effects_for(state.dof),
        risk_tags=["diagnostic_only"],
    )


def _meta_only_vector(dof: str, action_type: str) -> dict[str, Any]:
    return {
        "hydrodyn_edits": [],
        "moordyn": {},
        "initial_conditions": {},
        "tmax": None,
        "decision_meta": {
            "local_loop_v1": True,
            "action_type": action_type,
            "target_dof": dof,
            "requires_full_sixdof": True,
        },
    }


def _bquad_factor(state: DofLocalState) -> float:
    signed = state.component_scores.get("zeta_signed_ratio")
    if state.dominant_error_mode == "zeta" and signed is not None and signed > 0.25:
        return 0.95
    return 1.05


def _probe_bquad_factor(state: DofLocalState, step: float) -> float:
    signed = state.component_scores.get("zeta_signed_ratio")
    if signed is not None and signed > 0.15:
        return 1.0 - step
    return 1.0 + step


def _signed_ic(dof: str, target_decay_start: float | None) -> dict[str, float]:
    if target_decay_start is None:
        return {}
    return {DOF_TO_CHANNEL[dof]: float(target_decay_start)}


def _side_effects_for(dof: str) -> list[str]:
    coupling_watch = {
        "FD_SURGE": ["FD_PITCH", "FD_YAW"],
        "FD_SWAY": ["FD_ROLL", "FD_YAW", "FD_PITCH"],
        "FD_HEAVE": ["FD_PITCH", "FD_SURGE"],
        "FD_ROLL": ["FD_PITCH", "FD_SWAY"],
        "FD_PITCH": ["FD_SURGE", "FD_HEAVE", "FD_ROLL"],
        "FD_YAW": ["FD_SURGE", "FD_SWAY"],
    }
    return coupling_watch.get(dof, [])


def _current_bquad(candidate_id: str, dof: str) -> float | None:
    cand = _candidate_row(candidate_id)
    if not cand:
        return None
    vector = _candidate_vector(cand)
    idx = int(DOF_CFG[dof]["idx"])
    for edit in vector.get("hydrodyn_edits", []):
        if edit.get("block") == "BQuad" and int(edit.get("i", -1)) == idx and int(edit.get("j", -1)) == idx:
            try:
                return float(edit["value"])
            except (TypeError, ValueError):
                return None
    return _read_bquad_from_package(cand, vector, dof, idx)


def _candidate_row(candidate_id: str) -> dict[str, str] | None:
    for row in config.read_csv(config.registry_path("candidate_registry.csv")):
        if row.get("candidate_id") == candidate_id:
            return row
    return None


def _candidate_vector(cand: dict[str, str]) -> dict[str, Any]:
    try:
        return json.loads(cand.get("parameter_vector_json") or "{}")
    except json.JSONDecodeError:
        return {}


def _read_bquad_from_package(cand: dict[str, str], vector: dict[str, Any], dof: str, idx: int) -> float | None:
    try:
        root_rel, subdir, fst_name = executor._dof_package_spec(cand, vector, dof, config.load_config())
        root = config.resolve_workspace_path(root_rel)
        fst = root / subdir / fst_name if subdir not in {"", "."} else root / fst_name
        if not fst.exists():
            return None
        hydro = executor._find_ref(fst, "HydroFile")
        if not hydro or not Path(hydro).exists():
            return None
        lines = Path(hydro).read_text(encoding="utf-8", errors="ignore").splitlines()
        start = executor._block_start(lines, "AddBQuad")
        mat = executor._read_matrix(lines, start)
        return float(mat[idx][idx])
    except Exception:
        return None
