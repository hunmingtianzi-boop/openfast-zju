from __future__ import annotations

import math
from typing import Any

from .schema import DOFS, GLOBAL_SCORE_WEIGHTS, LoopVerdict, Observation, PredicateResult, StepDecision


def _num(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _ratio(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return abs(value) / max(scale, 1e-12)


def score_observation(
    observation: Observation,
    cfg: dict[str, Any],
    parent_objective: dict[str, Any] | None = None,
) -> dict[str, Any]:
    targets = cfg.get("targets", {})
    period_limit = float(targets.get("period_error_limit_pct", 3.0))
    env_ok = float(targets.get("env_ok", 0.40))
    ler_ok = float(targets.get("ler_ok", 0.50))
    fp_ok = float(targets.get("fp_ok", 0.12))

    per_dof: dict[str, dict[str, Any]] = {}
    weighted_scores: list[float] = []
    predicates: list[PredicateResult] = []
    hard = _missing_dof_violations(observation)

    for dof in DOFS:
        metrics = observation.per_dof_metrics.get(dof)
        if not metrics:
            continue
        period_score = _ratio(_num(metrics.get("period_error_pct")), period_limit)
        env_score = _ratio(_num(metrics.get("envelope_error")), env_ok)
        ler_score = _ratio(_num(metrics.get("ler_error")), ler_ok)
        fp_score = _ratio(_num(metrics.get("fp_error")), fp_ok)
        zeta_score = _zeta_score(metrics)
        scores = {
            "period": period_score,
            "env": env_score,
            "ler": ler_score,
            "fp": fp_score,
            "zeta": zeta_score,
        }
        available = {k: v for k, v in scores.items() if v is not None}
        if available:
            denom = sum(GLOBAL_SCORE_WEIGHTS[k] for k in available)
            score = sum(GLOBAL_SCORE_WEIGHTS[k] * available[k] for k in available) / max(denom, 1e-12)
            weighted_scores.append(score)
        else:
            score = math.inf
        per_dof[dof] = {
            "score": score,
            "period_error_pct": _num(metrics.get("period_error_pct")),
            "period_error_abs_pct": abs(_num(metrics.get("period_error_pct")) or 0.0)
            if _num(metrics.get("period_error_pct")) is not None else None,
            "envelope_error": _num(metrics.get("envelope_error")),
            "ler_error": _num(metrics.get("ler_error")),
            "fp_error": _num(metrics.get("fp_error")),
            "zeta_error_ratio": zeta_score,
            "closed": _closed(metrics, period_limit, env_ok, ler_ok, fp_ok),
        }

    hard.extend(_leak_violations(observation, cfg))
    hard.extend(_equilibrium_violations(observation, cfg))
    if parent_objective:
        hard.extend(_regression_violations(per_dof, parent_objective))
        hard.extend(_period_reverse_violations(per_dof, parent_objective))
    predicates.extend(_predicates_from_violations(hard))

    global_score = sum(weighted_scores) / len(weighted_scores) if weighted_scores else math.inf
    return {
        "global_score": global_score,
        "weights": dict(GLOBAL_SCORE_WEIGHTS),
        "per_dof": per_dof,
        "hard_constraint_violations": sorted(set(hard)),
        "predicate_results": [p.__dict__ for p in predicates],
    }


def verdict_from_objective(
    objective: dict[str, Any],
    parent_objective: dict[str, Any] | None = None,
    complete_required: bool = True,
) -> StepDecision:
    hard = list(objective.get("hard_constraint_violations", []))
    available_dofs = len(objective.get("per_dof", {}))
    if complete_required and available_dofs < len(DOFS):
        return StepDecision(
            status="EVIDENCE_ONLY",
            reason=f"incomplete suite: {available_dofs}/{len(DOFS)} DOFs have metrics",
            hard_constraint_violations=hard,
            tags=["incomplete_suite"],
        )
    if hard:
        return StepDecision(
            status="REJECTED",
            reason="hard constraints failed",
            hard_constraint_violations=hard,
            tags=["hard_constraint"],
        )
    if parent_objective:
        score = _num(objective.get("global_score"))
        parent = _num(parent_objective.get("global_score"))
        if score is None or parent is None:
            return StepDecision(status="EVIDENCE_ONLY", reason="missing score for parent comparison")
        if score < parent:
            return StepDecision(status="ACCEPTED", reason=f"global score improved {parent:.4g} -> {score:.4g}")
        return StepDecision(status="REJECTED", reason=f"global score did not improve {parent:.4g} -> {score:.4g}")
    return StepDecision(status="EVIDENCE_ONLY", reason="scored without parent arbitration")


def loop_verdict_from_step(
    step_decision: StepDecision,
    objective: dict[str, Any],
    budget_remaining: int | None = None,
    plateau_count: int = 0,
    complete_required: bool = True,
) -> LoopVerdict:
    hard = list(step_decision.hard_constraint_violations)
    diagnostic = [item for item in hard if _is_diagnostic_violation(item)]
    if diagnostic:
        return LoopVerdict(
            status="DIAGNOSTIC_FORK",
            reason="diagnostic hard constraint requires review",
            tags=["diagnostic", *sorted({item.split(':', 1)[0] for item in diagnostic})],
        )
    if budget_remaining is not None and budget_remaining <= 0:
        return LoopVerdict(status="BUDGET_EXHAUSTED", reason="candidate budget exhausted", tags=["budget"])
    if complete_required and len(objective.get("per_dof", {})) < len(DOFS):
        return LoopVerdict(status="RUNNING", reason="suite incomplete", tags=["incomplete_suite"])
    if _all_dofs_closed(objective) and step_decision.status in {"ACCEPTED", "EVIDENCE_ONLY"}:
        return LoopVerdict(status="CONVERGED", reason="all six DOFs satisfy closure predicates", tags=["closed_all_dofs"])
    if plateau_count >= 3:
        return LoopVerdict(status="RUNNING", reason="global search plateau; local polish recommended", tags=["plateau"])
    return LoopVerdict(status="RUNNING", reason=f"step decision: {step_decision.status.lower()}", tags=list(step_decision.tags))


def _zeta_score(metrics: dict[str, Any]) -> float | None:
    zeta = _num(metrics.get("zeta"))
    ref = _num(metrics.get("reference_zeta"))
    if zeta is None or ref is None:
        return None
    return abs(zeta - ref) / max(abs(ref), 1e-12)


def _closed(metrics: dict[str, Any], period_limit: float, env_ok: float, ler_ok: float, fp_ok: float) -> bool:
    period = _num(metrics.get("period_error_pct"))
    env = _num(metrics.get("envelope_error"))
    ler = _num(metrics.get("ler_error"))
    fp = _num(metrics.get("fp_error"))
    return bool(
        period is not None
        and env is not None
        and ler is not None
        and fp is not None
        and abs(period) <= period_limit
        and abs(env) <= env_ok
        and abs(ler) <= ler_ok
        and abs(fp) <= fp_ok
    )


def _missing_dof_violations(observation: Observation) -> list[str]:
    missing = [dof for dof in DOFS if dof not in observation.per_dof_metrics]
    return [f"missing_dof:{dof}" for dof in missing]


def _predicates_from_violations(violations: list[str]) -> list[PredicateResult]:
    if not violations:
        return [PredicateResult(name="hard_constraints", passed=True, severity="info", reason="all hard constraints passed")]
    return [
        PredicateResult(
            name=item.split(":", 1)[0],
            passed=False,
            severity="hard",
            reason=item,
            tags=[item.split(":", 1)[0]],
        )
        for item in sorted(set(violations))
    ]


def _leak_violations(observation: Observation, cfg: dict[str, Any]) -> list[str]:
    physics = cfg.get("physics", {})
    leak_abs = float(physics.get("leak_abs", 0.50))
    leak_factor = float(physics.get("leak_factor", 3.0))
    out: list[str] = []
    for primary, row in observation.cross_energy_matrix.items():
        ref_row = observation.reference_cross_energy_matrix.get(primary, {})
        for secondary, ratio in row.items():
            ref = float(ref_row.get(secondary, 0.0))
            if ratio > leak_abs and ratio > leak_factor * max(ref, 1e-3):
                out.append(f"strong_leak:{primary}->{secondary}:{ratio:.3g}>ref{ref:.3g}")
    return out


def _equilibrium_violations(observation: Observation, cfg: dict[str, Any]) -> list[str]:
    limits = cfg.get("physics", {}).get("equilibrium_limits", {})
    if not isinstance(limits, dict) or not observation.equilibrium:
        return []
    out: list[str] = []
    for key, limit in limits.items():
        value = _num(observation.equilibrium.get(key))
        lim = _num(limit)
        if value is not None and lim is not None and abs(value) > abs(lim):
            out.append(f"equilibrium_out_of_bounds:{key}:{value:.3g}>{lim:.3g}")
    return out


def _regression_violations(per_dof: dict[str, dict[str, Any]], parent_objective: dict[str, Any]) -> list[str]:
    out: list[str] = []
    parent_per = parent_objective.get("per_dof", {})
    for dof, parent_metrics in parent_per.items():
        if not parent_metrics.get("closed"):
            continue
        current = per_dof.get(dof)
        if not current:
            out.append(f"regressed_closed_dof:{dof}:missing")
            continue
        parent_score = _num(parent_metrics.get("score"))
        current_score = _num(current.get("score"))
        if parent_score is not None and current_score is not None and current_score > max(parent_score * 1.25, parent_score + 0.25):
            out.append(f"regressed_closed_dof:{dof}:score {parent_score:.3g}->{current_score:.3g}")
    return out


def _period_reverse_violations(per_dof: dict[str, dict[str, Any]], parent_objective: dict[str, Any]) -> list[str]:
    out: list[str] = []
    parent_per = parent_objective.get("per_dof", {})
    for dof, current in per_dof.items():
        parent = parent_per.get(dof, {})
        p0 = _num(parent.get("period_error_pct"))
        p1 = _num(current.get("period_error_pct"))
        if p0 is None or p1 is None:
            continue
        a0 = abs(p0)
        a1 = abs(p1)
        if a1 > max(a0 * 1.50, a0 + 3.0):
            out.append(f"period_reverse_worse:{dof}:abs {a0:.3g}->{a1:.3g} signed {p0:.3g}->{p1:.3g}")
    return out


def _is_diagnostic_violation(item: str) -> bool:
    return item.startswith(("strong_leak:", "equilibrium_out_of_bounds:", "period_reverse_worse:"))


def _all_dofs_closed(objective: dict[str, Any]) -> bool:
    per = objective.get("per_dof", {})
    return len(per) == len(DOFS) and all(metrics.get("closed") for metrics in per.values())
