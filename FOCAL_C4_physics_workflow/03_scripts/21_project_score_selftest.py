from __future__ import annotations

from copy import deepcopy

from global_loop import config, project_scoring, supervisor
from global_loop.schema import DOFS, PARAMETER_GROUPS


def main() -> int:
    _current_project_checks()
    _status_does_not_write_snapshot()
    _synthetic_gate_checks()
    print("project score v2 selftest passed")
    return 0


def _current_project_checks() -> None:
    scorecard = project_scoring.build_scorecard()
    assert scorecard.score_version == 2
    assert scorecard.live_or_snapshot == "live"
    assert scorecard.delivery_status == "CALIBRATING"
    assert scorecard.components["physics"]["closed_dof_count"] == 0
    assert scorecard.components["physics"]["worst_dof"]["dof"] == "FD_SWAY"
    assert scorecard.trend_summary["window_size"] <= 20
    assert scorecard.memory_reliability["conflict_count"] >= 0


def _status_does_not_write_snapshot() -> None:
    manifest = project_scoring.oracle.ensure_current_manifest()
    path = project_scoring.oracle.epoch_root(manifest.oracle_id) / "project_score.json"
    before = path.stat().st_mtime_ns if path.exists() else None
    supervisor.status()
    after = path.stat().st_mtime_ns if path.exists() else None
    assert before == after


def _synthetic_gate_checks() -> None:
    cfg = config.load_config()
    manifest = project_scoring.oracle.ensure_current_manifest(cfg)
    base_card = _base_card()

    original = (
        project_scoring.oracle.ensure_current_manifest,
        project_scoring.memory.read_global_state,
        project_scoring.memory.load_run_cards,
        project_scoring.memory.read_coupling_memory,
        project_scoring.memory.read_proposal_pool,
        project_scoring.config.assert_active_paths,
    )
    try:
        project_scoring.oracle.ensure_current_manifest = lambda cfg=None: manifest
        project_scoring.memory.read_coupling_memory = lambda: {}
        project_scoring.memory.read_proposal_pool = lambda: {}
        project_scoring.memory.read_global_state = lambda: _state(manifest.oracle_id)

        bad_one = deepcopy(base_card)
        bad_one["objective"]["per_dof"]["FD_YAW"] = dict(
            _base_metrics(),
            period_error_pct=300.0,
            envelope_error=40.0,
            ler_error=50.0,
            fp_error=12.0,
            zeta_error_ratio=10.0,
            score=10.0,
            closed=False,
        )
        project_scoring.memory.load_run_cards = lambda: [bad_one]
        bad_score = project_scoring.build_scorecard()
        assert bad_score.physics_fit_score < 90.0
        assert bad_score.components["physics"]["worst_dof"]["dof"] == "FD_YAW"

        missing_card = deepcopy(base_card)
        missing_card["objective"]["per_dof"].pop("FD_YAW")
        project_scoring.memory.load_run_cards = lambda: [missing_card]
        missing = project_scoring.build_scorecard()
        assert missing.overall_gated_score <= 50.0
        assert missing.delivery_status == "BLOCKED"

        project_scoring.memory.read_global_state = lambda: {
            "oracle_id": "oracle_mismatch",
            "epoch_id": "oracle_mismatch",
            "current_best_run_id": "RC_SYNTH_000",
        }
        project_scoring.memory.load_run_cards = lambda: [base_card]
        mismatch = project_scoring.build_scorecard()
        assert mismatch.overall_gated_score <= 30.0
        assert mismatch.delivery_status == "BLOCKED"
        project_scoring.memory.read_global_state = lambda: _state(manifest.oracle_id)

        project_scoring.config.assert_active_paths = lambda cfg=None: (_ for _ in ()).throw(
            RuntimeError("D:/OpenFast/work-zx forbidden")
        )
        path_blocked = project_scoring.build_scorecard()
        assert path_blocked.overall_gated_score <= 30.0
        assert path_blocked.delivery_status == "BLOCKED"
        project_scoring.config.assert_active_paths = original[-1]

        leak_card = deepcopy(base_card)
        leak_card["objective"]["hard_constraint_violations"] = ["strong_leak:FD_HEAVE->Pitch:1.2>ref0.1"]
        leak_card["step_decision"]["hard_constraint_violations"] = list(
            leak_card["objective"]["hard_constraint_violations"]
        )
        project_scoring.memory.load_run_cards = lambda: [leak_card]
        leak = project_scoring.build_scorecard()
        assert leak.overall_gated_score <= 55.0
        assert leak.delivery_status == "DIAGNOSTIC"

        eq_card = deepcopy(base_card)
        eq_card["objective"]["hard_constraint_violations"] = ["equilibrium_out_of_bounds:Pitch:9>1"]
        eq_card["step_decision"]["hard_constraint_violations"] = list(
            eq_card["objective"]["hard_constraint_violations"]
        )
        project_scoring.memory.load_run_cards = lambda: [eq_card]
        eq_score = project_scoring.build_scorecard()
        assert eq_score.overall_gated_score <= 55.0
        assert eq_score.delivery_status == "DIAGNOSTIC"

        project_scoring.memory.load_run_cards = lambda: [base_card]
        ready = project_scoring.build_scorecard()
        assert ready.delivery_status == "READY_FOR_FINAL_VALIDATE"
        assert ready.overall_gated_score <= 90.0

        project_scoring.memory.read_global_state = lambda: _final_state(manifest.oracle_id)
        missing_artifact = project_scoring.build_scorecard()
        assert missing_artifact.overall_gated_score <= 92.0
        assert missing_artifact.delivery_status == "BLOCKED"

        synth_cards = _deliverable_cards()
        project_scoring.memory.load_run_cards = lambda: synth_cards
        project_scoring.memory.read_global_state = lambda: _final_state(manifest.oracle_id, run_id="RC_SYNTH_019")
        project_scoring.memory.read_coupling_memory = lambda: {
            "entries": {
                group: {
                    "confidence": 1.0,
                    "affected_dofs": list(DOFS),
                    "improves": list(DOFS),
                    "damages": [],
                    "evidence_run_ids": [card["run_id"] for card in synth_cards[:5]],
                }
                for group in PARAMETER_GROUPS
            }
        }
        deliverable = project_scoring.build_scorecard()
        assert deliverable.overall_gated_score >= 90.0
        assert deliverable.delivery_status == "DELIVERABLE"
    finally:
        (
            project_scoring.oracle.ensure_current_manifest,
            project_scoring.memory.read_global_state,
            project_scoring.memory.load_run_cards,
            project_scoring.memory.read_coupling_memory,
            project_scoring.memory.read_proposal_pool,
            project_scoring.config.assert_active_paths,
        ) = original


def _base_metrics() -> dict[str, object]:
    return {
        "score": 0.01,
        "period_error_pct": 0.01,
        "envelope_error": 0.001,
        "ler_error": 0.001,
        "fp_error": 0.001,
        "zeta_error_ratio": 0.001,
        "closed": True,
    }


def _base_card() -> dict[str, object]:
    per_dof = {dof: dict(_base_metrics()) for dof in DOFS}
    return {
        "run_id": "RC_SYNTH_000",
        "stage": "global_step",
        "created_at": "2026-07-05T00:00:00+00:00",
        "observation": {"candidate_id": "SYNTH"},
        "objective": {"global_score": 0.1, "per_dof": per_dof, "hard_constraint_violations": []},
        "step_decision": {"status": "ACCEPTED", "hard_constraint_violations": []},
        "verdict": {"status": "ACCEPTED", "hard_constraint_violations": []},
        "loop_verdict": {"status": "RUNNING"},
        "evidence_sources": ["synthetic"],
        "parameter_group": "diagonal_hydro",
        "artifact_policy": "summary_only",
    }


def _deliverable_cards() -> list[dict[str, object]]:
    cards = []
    for idx in range(20):
        card = deepcopy(_base_card())
        card["run_id"] = f"RC_SYNTH_{idx:03d}"
        card["created_at"] = f"2026-07-05T00:{idx:02d}:00+00:00"
        card["objective"]["global_score"] = 1.0 - idx * 0.04
        card["artifact_policy"] = "retain_full_final_validation" if idx == 19 else "summary_only"
        cards.append(card)
    return cards


def _state(oracle_id: str, run_id: str = "RC_SYNTH_000") -> dict[str, object]:
    return {
        "oracle_id": oracle_id,
        "epoch_id": oracle_id,
        "current_best_run_id": run_id,
        "mode": "offline_import",
        "updated_at": "unit",
    }


def _final_state(oracle_id: str, run_id: str = "RC_SYNTH_000") -> dict[str, object]:
    return {
        "oracle_id": oracle_id,
        "epoch_id": oracle_id,
        "current_best_run_id": run_id,
        "mode": "final_validate",
        "final_validation_candidate_id": "SYNTH",
        "loop_verdict": {"status": "CONVERGED"},
        "last_step_decision": {"status": "ACCEPTED"},
        "updated_at": "unit",
    }


if __name__ == "__main__":
    raise SystemExit(main())
