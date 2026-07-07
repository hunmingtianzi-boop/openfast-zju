from __future__ import annotations

from copy import deepcopy

from global_loop import config, supervisor, workflow_benchmark
from global_loop.schema import DOFS, PARAMETER_GROUPS


def main() -> int:
    _current_smoke()
    _status_does_not_write_snapshot()
    _active_historical_split()
    _active_trend_and_scheduler()
    _plateau_and_diagnostic_scheduler()
    print("workflow benchmark v3 selftest passed")
    return 0


def _current_smoke() -> None:
    benchmark = workflow_benchmark.build_benchmark()
    assert benchmark.benchmark_version == 3
    assert benchmark.live_or_snapshot == "live"
    assert benchmark.workflow_benchmark_score >= 0.0
    assert benchmark.active_decision_summary["status"] in {
        "INSUFFICIENT_ACTIVE_DATA",
        "ACTIVE_SEARCH_HEALTHY",
        "ACTIVE_DIAGNOSTIC_HEAVY",
        "ACTIVE_PLATEAU",
    }


def _status_does_not_write_snapshot() -> None:
    manifest = workflow_benchmark.oracle.ensure_current_manifest()
    path = workflow_benchmark.oracle.epoch_root(manifest.oracle_id) / "workflow_benchmark.json"
    before = path.stat().st_mtime_ns if path.exists() else None
    supervisor.status()
    after = path.stat().st_mtime_ns if path.exists() else None
    assert before == after


def _active_historical_split() -> None:
    historical = [_card(f"RC_HIST_{idx:03d}", stage="offline_import", closed=False) for idx in range(179)]
    active = [_card("RC_ACTIVE_000", stage="global_step", closed=False)]
    with _patched(cards=historical + active, proposals=_diagnostic_proposals(), coupling=_ambiguous_memory(historical)):
        benchmark = workflow_benchmark.build_benchmark()
        assert benchmark.active_decision_summary["status"] == "INSUFFICIENT_ACTIVE_DATA"
        assert benchmark.active_decision_summary["active_decision_count"] == 1
        assert benchmark.historical_evidence_summary["historical_card_count"] == 179
        assert benchmark.scheduler_recommendation["recommendation"] == "RUN_DIAGNOSTIC_REVIEW"


def _active_trend_and_scheduler() -> None:
    cards = []
    for idx in range(5):
        card = _card(f"RC_ACCEPT_{idx:03d}", stage="global_step", closed=False)
        card["objective"]["global_score"] = 1.0 - idx * 0.12
        card["step_decision"]["status"] = "ACCEPTED"
        cards.append(card)
    with _patched(cards=cards, proposals=_materializable_proposals(), coupling=_clean_memory(cards)):
        benchmark = workflow_benchmark.build_benchmark()
        assert benchmark.active_decision_summary["score"] is not None
        assert benchmark.active_decision_summary["score"] > 8.0
        assert benchmark.scheduler_recommendation["recommendation"] == "MATERIALIZE_AND_VALIDATE"


def _plateau_and_diagnostic_scheduler() -> None:
    plateau_cards = []
    for idx in range(5):
        card = _card(f"RC_REJECT_{idx:03d}", stage="global_step", closed=False)
        card["objective"]["global_score"] = 1.0
        card["step_decision"]["status"] = "REJECTED"
        plateau_cards.append(card)
    with _patched(cards=plateau_cards, proposals={}, coupling=_clean_memory(plateau_cards)):
        plateau = workflow_benchmark.build_benchmark()
        assert plateau.scheduler_recommendation["recommendation"] == "SWITCH_SEARCH_STRATEGY"

    diagnostic_cards = []
    for idx in range(5):
        card = _card(f"RC_DIAG_{idx:03d}", stage="global_step", closed=False)
        card["loop_verdict"]["status"] = "DIAGNOSTIC_FORK"
        card["step_decision"]["status"] = "REJECTED"
        diagnostic_cards.append(card)
    with _patched(cards=diagnostic_cards, proposals={}, coupling=_clean_memory(diagnostic_cards)):
        diagnostic = workflow_benchmark.build_benchmark()
        assert diagnostic.scheduler_recommendation["recommendation"] == "RUN_DIAGNOSTIC_REVIEW"


class _patched:
    def __init__(self, *, cards: list[dict[str, object]], proposals: dict[str, object], coupling: dict[str, object]):
        cfg = config.load_config()
        self.manifest = workflow_benchmark.oracle.ensure_current_manifest(cfg)
        self.cards = cards
        self.proposals = proposals
        self.coupling = coupling
        self.original = (
            workflow_benchmark.oracle.ensure_current_manifest,
            workflow_benchmark.memory.read_global_state,
            workflow_benchmark.memory.load_run_cards,
            workflow_benchmark.memory.read_coupling_memory,
            workflow_benchmark.memory.read_proposal_pool,
        )

    def __enter__(self):
        workflow_benchmark.oracle.ensure_current_manifest = lambda cfg=None: self.manifest
        workflow_benchmark.memory.read_global_state = lambda: {
            "oracle_id": self.manifest.oracle_id,
            "epoch_id": self.manifest.oracle_id,
            "current_best_run_id": self.cards[-1]["run_id"] if self.cards else "",
            "updated_at": "unit",
        }
        workflow_benchmark.memory.load_run_cards = lambda: self.cards
        workflow_benchmark.memory.read_coupling_memory = lambda: self.coupling
        workflow_benchmark.memory.read_proposal_pool = lambda: self.proposals

    def __exit__(self, exc_type, exc, tb):
        (
            workflow_benchmark.oracle.ensure_current_manifest,
            workflow_benchmark.memory.read_global_state,
            workflow_benchmark.memory.load_run_cards,
            workflow_benchmark.memory.read_coupling_memory,
            workflow_benchmark.memory.read_proposal_pool,
        ) = self.original


def _card(run_id: str, *, stage: str, closed: bool = True) -> dict[str, object]:
    metrics = {
        "score": 0.1,
        "period_error_pct": 0.1,
        "envelope_error": 0.01,
        "ler_error": 0.01,
        "fp_error": 0.01,
        "zeta_error_ratio": 0.01,
        "closed": closed,
    }
    return {
        "run_id": run_id,
        "stage": stage,
        "created_at": "2026-07-05T00:00:00+00:00",
        "observation": {"candidate_id": run_id.replace("RC_", "CAND_")},
        "objective": {
            "global_score": 0.5,
            "per_dof": {dof: dict(metrics) for dof in DOFS},
            "hard_constraint_violations": [],
        },
        "step_decision": {"status": "EVIDENCE_ONLY", "hard_constraint_violations": []},
        "verdict": {"status": "EVIDENCE_ONLY", "hard_constraint_violations": []},
        "loop_verdict": {"status": "RUNNING"},
        "parameter_group": "diagonal_hydro",
        "evidence_sources": ["synthetic"],
        "artifact_policy": "summary_only",
    }


def _diagnostic_proposals() -> dict[str, object]:
    return {
        "proposals": [
            {
                "proposal_id": "LP_DIAG",
                "target_dof": "FD_HEAVE",
                "action": {
                    "parameter_group": "diagonal_hydro",
                    "registration_ready": False,
                    "reason_not_ready": "coupling leak dominates",
                },
            }
        ]
    }


def _materializable_proposals() -> dict[str, object]:
    return {
        "proposals": [
            {
                "proposal_id": "LP_READY",
                "target_dof": "FD_HEAVE",
                "action": {"parameter_group": "diagonal_hydro", "registration_ready": True},
            }
        ]
    }


def _clean_memory(cards: list[dict[str, object]]) -> dict[str, object]:
    evidence = [card["run_id"] for card in cards]
    return {
        "entries": {
            group: {
                "confidence": 1.0,
                "affected_dofs": list(DOFS),
                "improves": list(DOFS),
                "damages": [],
                "evidence_run_ids": evidence,
            }
            for group in PARAMETER_GROUPS
        }
    }


def _ambiguous_memory(cards: list[dict[str, object]]) -> dict[str, object]:
    memory = deepcopy(_clean_memory(cards))
    memory["entries"]["diagonal_hydro"]["damages"] = list(DOFS)
    return memory


if __name__ == "__main__":
    raise SystemExit(main())
