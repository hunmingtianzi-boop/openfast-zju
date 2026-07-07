from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DOFS = ("FD_SURGE", "FD_SWAY", "FD_HEAVE", "FD_ROLL", "FD_PITCH", "FD_YAW")
DOF_TO_CHANNEL = {
    "FD_SURGE": "Surge",
    "FD_SWAY": "Sway",
    "FD_HEAVE": "Heave",
    "FD_ROLL": "Roll",
    "FD_PITCH": "Pitch",
    "FD_YAW": "Yaw",
}

PARAMETER_GROUPS = ("mooring", "rna_balance", "diagonal_hydro", "coupling_disabled")
STEP_DECISIONS = ("ACCEPTED", "REJECTED", "EVIDENCE_ONLY")
LOOP_VERDICTS = ("RUNNING", "CONVERGED", "DIAGNOSTIC_FORK", "BUDGET_EXHAUSTED")
GLOBAL_SCORE_WEIGHTS = {
    "period": 0.35,
    "env": 0.25,
    "ler": 0.20,
    "fp": 0.10,
    "zeta": 0.10,
}


@dataclass(frozen=True)
class DofTarget:
    dof: str
    channel: str
    reference_file: str
    period: float | None
    zeta: float | None
    decay_start: float | None
    reference_leak: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetTruth:
    created_at: str
    source: str
    dofs: dict[str, DofTarget]
    thresholds: dict[str, Any]


@dataclass(frozen=True)
class OracleManifest:
    oracle_id: str
    created_at: str
    version: int
    active_paths: dict[str, str]
    config_hash: str
    config_sections_hash: str
    config_sections: dict[str, Any]
    reference_files: dict[str, dict[str, Any]]
    starting_model_tree_hash: str
    target_source: str = "active_curated_processed_reference"


@dataclass(frozen=True)
class Observation:
    candidate_id: str
    source: str
    per_dof_metrics: dict[str, dict[str, Any]]
    cross_energy_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    reference_cross_energy_matrix: dict[str, dict[str, float]] = field(default_factory=dict)
    equilibrium: dict[str, Any] = field(default_factory=dict)
    artifacts: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PredicateResult:
    name: str
    passed: bool
    severity: str
    reason: str
    source_role: str = "derived_metric"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class StepDecision:
    status: str
    reason: str
    hard_constraint_violations: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


Verdict = StepDecision


@dataclass(frozen=True)
class LoopVerdict:
    status: str
    reason: str
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryEpoch:
    oracle_id: str
    epoch_id: str
    root: str
    created_at: str
    state_path: str
    run_cards_path: str
    reports_path: str


@dataclass(frozen=True)
class TruthSnapshot:
    created_at: str
    oracle_id: str
    epoch_id: str
    target_truth: TargetTruth
    oracle_manifest: OracleManifest
    source_roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ArtifactManifest:
    run_id: str
    candidate_id: str
    oracle_id: str
    epoch_id: str
    artifact_policy: str
    retention_class: str
    raw_output_retained: bool
    retain_reason: str
    metrics_extracted: bool
    artifact_paths: list[str] = field(default_factory=list)
    total_size_bytes: int = 0
    sha256: str = ""
    delete_eligible: bool = False
    deleted_at: str = ""
    deleted_paths: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(frozen=True)
class RunCard:
    run_id: str
    parent_id: str
    stage: str
    parameter_group: str
    parameter_delta: dict[str, Any]
    input_hash: str
    target_source: str
    observation: Observation
    objective: dict[str, Any]
    verdict: Verdict
    artifact_policy: str
    created_at: str
    evidence_sources: list[str] = field(default_factory=list)
    oracle_id: str = ""
    epoch_id: str = ""
    predicate_results: list[PredicateResult] = field(default_factory=list)
    step_decision: StepDecision | None = None
    loop_verdict: LoopVerdict | None = None
    source_roles: dict[str, str] = field(default_factory=dict)
    review_status: str = "not_required"


@dataclass(frozen=True)
class CouplingMemoryEntry:
    parameter_group: str
    affected_dofs: list[str] = field(default_factory=list)
    improves: list[str] = field(default_factory=list)
    damages: list[str] = field(default_factory=list)
    response_strength: float = 0.0
    confidence: float = 0.0
    evidence_run_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CouplingMemory:
    created_at: str
    entries: dict[str, CouplingMemoryEntry]
    oracle_id: str = ""
    epoch_id: str = ""


@dataclass(frozen=True)
class TransitionCard:
    transition_id: str
    parent_run_id: str
    child_run_id: str
    parent_candidate_id: str
    child_candidate_id: str
    oracle_id: str
    epoch_id: str
    parameter_group: str
    action_type: str
    target_dof: str
    parameter_delta: dict[str, Any]
    global_score_delta: float | None
    metric_deltas: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    new_failures: list[str] = field(default_factory=list)
    resolved_failures: list[str] = field(default_factory=list)
    step_decision: dict[str, Any] = field(default_factory=dict)
    loop_verdict: dict[str, Any] = field(default_factory=dict)
    source_run_ids: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(frozen=True)
class RelationMemoryEntry:
    relation_id: str
    action_type: str
    parameter_group: str
    target_dof: str
    response_dof: str
    metric: str
    effect: str
    effect_size: float
    confidence: float
    status: str
    support_run_ids: list[str] = field(default_factory=list)
    refute_run_ids: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)
    applicability_notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RelationMemory:
    created_at: str
    oracle_id: str
    epoch_id: str
    entries: dict[str, RelationMemoryEntry]
    source_transition_count: int = 0


@dataclass(frozen=True)
class ProposalHistoryEntry:
    proposal_id: str
    parent_run_id: str
    parent_candidate_id: str
    target_dof: str
    action_type: str
    parameter_group: str
    materialized_candidate_ids: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    step_decisions: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "proposed"
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class MemoryGcPlan:
    created_at: str
    oracle_id: str
    epoch_id: str
    apply: bool
    candidates: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[dict[str, Any]] = field(default_factory=list)
    total_candidate_bytes: int = 0


@dataclass(frozen=True)
class DofLocalState:
    dof: str
    score: float | None
    closed: bool
    dominant_error_mode: str
    component_scores: dict[str, float | None] = field(default_factory=dict)
    coupling_risk: float = 0.0
    allowed_regression: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LocalGuardContext:
    oracle_id: str
    epoch_id: str
    current_best_run_id: str
    current_best_candidate_id: str
    open_dofs: list[str]
    protected_dofs: list[str]
    fragile_dofs: list[str]
    dof_states: dict[str, DofLocalState]
    thresholds: dict[str, Any]
    source_roles: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class LocalAction:
    action_type: str
    parameter_group: str
    vector: dict[str, Any]
    registration_ready: bool
    reason_not_ready: str = ""
    expected_primary_effect: str = ""
    expected_side_effects: list[str] = field(default_factory=list)
    risk_tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LocalProposal:
    proposal_id: str
    parent_run_id: str
    parent_candidate_id: str
    target_dof: str
    hypothesis: str
    action: LocalAction
    expected_gain: float
    protected_risk: float
    information_gain: float
    memory_support: float
    mechanism_clarity: float
    run_cost: float
    priority: float
    validation_mode: str = "full_6dof_required"
    status: str = "proposed"
    created_at: str = ""


@dataclass(frozen=True)
class ProposalPool:
    created_at: str
    oracle_id: str
    epoch_id: str
    parent_run_id: str
    parent_candidate_id: str
    guard_context: LocalGuardContext
    proposals: list[LocalProposal]
    top_k: int = 1


@dataclass(frozen=True)
class ProjectGateResult:
    name: str
    passed: bool
    cap: float | None
    reason: str
    severity: str = "gate"
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProjectScorecard:
    created_at: str
    oracle_id: str
    epoch_id: str
    overall_raw_score: float
    overall_gated_score: float
    physics_fit_score: float
    workflow_health_score: float
    delivery_status: str
    gate_results: list[ProjectGateResult]
    dof_score_table: list[dict[str, Any]]
    next_action_summary: dict[str, Any]
    score_version: int = 2
    computed_at: str = ""
    snapshot_id: str = ""
    is_snapshot: bool = False
    live_or_snapshot: str = "live"
    source_state_updated_at: str = ""
    trend_summary: dict[str, Any] = field(default_factory=dict)
    memory_reliability: dict[str, Any] = field(default_factory=dict)
    validation_readiness: dict[str, Any] = field(default_factory=dict)
    score_interpretation: dict[str, Any] = field(default_factory=dict)
    components: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowBenchmark:
    created_at: str
    computed_at: str
    oracle_id: str
    epoch_id: str
    workflow_benchmark_score: float
    benchmark_confidence: float
    benchmark_status: str
    active_decision_summary: dict[str, Any]
    historical_evidence_summary: dict[str, Any]
    memory_diagnostics: dict[str, Any]
    proposal_readiness: dict[str, Any]
    scheduler_recommendation: dict[str, Any]
    benchmark_version: int = 3
    snapshot_id: str = ""
    is_snapshot: bool = False
    live_or_snapshot: str = "live"
    source_state_updated_at: str = ""
    components: dict[str, Any] = field(default_factory=dict)


HARNESS_RECOMMENDATIONS = (
    "CONTINUE_CURRENT_CAMPAIGN",
    "LIMITED_PROBE",
    "SWITCH_PARAMETER_GROUP",
    "ENTER_DIAGNOSTIC_REVIEW",
    "STOP_AUTO_MATERIALIZE",
    "RUN_BASELINE_EVIDENCE",
    "REBUILD_MEMORY",
)

HARNESS_WINDOW_STATUSES = (
    "PRODUCTIVE",
    "EXPLORATORY",
    "PLATEAU",
    "DIAGNOSTIC_BLOCKED",
    "INSUFFICIENT_ACTIVE_DATA",
)

CAMPAIGN_STATUSES = ("SUPPORTED", "REFUTED", "AMBIGUOUS", "ACTIVE", "BLOCKED")

LLM_ANALYSIS_TYPES = (
    "pre_run",
    "during_run",
    "post_run",
    "synthesis",
    "proposal_review",
    "diagnostic_review",
)


@dataclass(frozen=True)
class LLMAnalysisJob:
    job_id: str
    analysis_type: str
    oracle_id: str
    epoch_id: str
    status: str
    candidate_id: str = ""
    run_id: str = ""
    item_id: str = ""
    proposal_id: str = ""
    packet_path: str = ""
    report_path: str = ""
    input_snapshot_hash: str = ""
    call_model: bool = False
    provider: str = ""
    model: str = ""
    error: str = ""
    created_at: str = ""
    updated_at: str = ""
    source_role: str = "advisory"


@dataclass(frozen=True)
class LLMAnalysisPacket:
    packet_id: str
    job_id: str
    analysis_type: str
    created_at: str
    oracle_id: str
    epoch_id: str
    input_snapshot_hash: str
    prompt: str
    context: dict[str, Any]
    candidate_id: str = ""
    run_id: str = ""
    item_id: str = ""
    proposal_id: str = ""
    stale_for_decision: bool = False
    advisory_only: bool = True
    source_role: str = "advisory"


@dataclass(frozen=True)
class LLMAnalysisReport:
    report_id: str
    job_id: str
    analysis_type: str
    created_at: str
    oracle_id: str
    epoch_id: str
    input_snapshot_hash: str
    report_path: str
    packet_path: str
    candidate_id: str = ""
    run_id: str = ""
    item_id: str = ""
    proposal_id: str = ""
    provider: str = ""
    model: str = ""
    provider_called: bool = False
    stale_for_decision: bool = False
    advisory_only: bool = True
    source_role: str = "advisory"


@dataclass(frozen=True)
class DiagnosticForkItem:
    item_id: str
    created_at: str
    oracle_id: str
    epoch_id: str
    run_id: str
    candidate_id: str
    trigger_reasons: list[str]
    related_dofs: list[str]
    review_type: str
    severity: str = "diagnostic"
    status: str = "pending"
    source_role: str = "diagnostic_evidence"
    source_stage: str = ""
    source_category: str = "historical"
    priority: float = 0.0


@dataclass(frozen=True)
class DiagnosticQueue:
    created_at: str
    oracle_id: str
    epoch_id: str
    items: list[DiagnosticForkItem]
    source_role: str = "diagnostic_evidence"


@dataclass(frozen=True)
class HarnessAttempt:
    run_id: str
    candidate_id: str
    stage: str
    oracle_id: str
    epoch_id: str
    campaign_id: str
    hypothesis: str
    valid: bool
    validity_reason: str
    complete_6dof: bool
    step_decision: str
    loop_verdict: str
    global_score: float | None
    fit_gain: float | None
    information_gain: float
    failure_signature: str
    parameter_group: str = ""
    target_dof: str = ""
    action_type: str = ""
    dominant_metric: str = ""
    new_failures: list[str] = field(default_factory=list)
    resolved_failures: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = ""


@dataclass(frozen=True)
class CampaignState:
    campaign_id: str
    hypothesis: str
    parameter_group: str
    target_dof: str
    action_type: str
    dominant_metric: str
    status: str
    confidence: float
    attempt_run_ids: list[str] = field(default_factory=list)
    supporting_run_ids: list[str] = field(default_factory=list)
    refuting_run_ids: list[str] = field(default_factory=list)
    repeated_failure_signatures: list[str] = field(default_factory=list)
    recommendation: str = ""
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class HarnessAssessment:
    created_at: str
    oracle_id: str
    epoch_id: str
    status: str
    recommendation: str
    active_attempt_count: int
    valid_attempt_count: int
    window_limit: int
    mean_information_gain: float
    stop_auto_materialize: bool
    dominant_campaign_id: str = ""
    attempts: list[HarnessAttempt] = field(default_factory=list)
    campaigns: dict[str, CampaignState] = field(default_factory=dict)
    active_summary: dict[str, Any] = field(default_factory=dict)
    proposal_summary: dict[str, Any] = field(default_factory=dict)
    repeated_failure_signatures: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    source_roles: dict[str, str] = field(default_factory=dict)


def to_plain(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    return value
