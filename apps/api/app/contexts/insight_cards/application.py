from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Mapping

from app.domain.json_types import MutableJsonObject
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    EvidenceIndependenceSummary,
)
from app.contexts.evidence_independence.temporal import (
    TIME_PROVENANCE_POLICY,
)
from app.contexts.insight_cards.contracts import (
    AuthorityState,
    EvidenceRef,
    InsightCard,
    InsightCardSource,
    NextAction,
    SensitivityResult,
    TemporalEvidenceSummary,
    TemporalSourceLagRow,
)


_BLOCKING_UNCERTAINTY = frozenset(
    {"blocked", "insufficient_evidence", "not_observed"}
)
_REVIEW_UNCERTAINTY = frozenset(
    {"source_concentrated", "unresolved", "stale_observation"}
)


def assemble_insight_card(source: InsightCardSource) -> InsightCard:
    _validate_evidence_binding(source)
    authority, gate_limitations = _gated_authority(source)
    next_action = derive_next_action(
        source.uncertainty_state,
        authority,
        preferred=source.next_action_override,
    )
    sensitivity, fragile_factor = sensitivity_results_from_certificate(
        source.certificate
    )
    limitations = list(source.limitations)
    limitations.extend(source.uncertainty_reasons)
    limitations.extend(gate_limitations)
    if source.certificate is not None:
        limitations.extend(source.certificate.certificate_reasons)
    if source.certificate is None or (
        source.certificate is not None
        and source.certificate.certificate_status == "not_applicable"
    ):
        limitations.append("sensitivity_pending_verification")
    summary = source.evidence_summary
    evidence_algorithm_version = source.evidence_algorithm_version or (
        summary.algorithm_version if summary is not None else ""
    )
    evidence_config_hash = source.evidence_config_hash or (
        summary.config_hash if summary is not None else ""
    )
    coverage_status = source.coverage_status
    if coverage_status is None and summary is not None:
        coverage_status = summary.coverage_status
    return InsightCard(
        insight_id=source.insight_id,
        claim_type=source.claim_type,
        subject_ref=source.subject_ref,
        claim=source.claim,
        authority_state=authority,
        evidence_refs=source.evidence_refs,
        counter_evidence_refs=source.counter_evidence_refs,
        used_evidence_ids=_used_evidence_ids(source),
        effective_sample_size=source.effective_sample_size,
        raw_evidence_count=source.raw_evidence_count,
        uncertainty_state=source.uncertainty_state,
        uncertainty_reasons=source.uncertainty_reasons,
        sensitivity_results=sensitivity,
        fragile_factor=fragile_factor,
        data_refs=source.data_refs,
        release_refs=source.release_refs,
        graph_version_refs=source.graph_version_refs,
        catalog_refs=source.catalog_refs,
        algorithm_version=source.algorithm_version,
        algorithm_config_version=source.algorithm_config_version,
        algorithm_config_hash=source.algorithm_config_hash,
        evidence_algorithm_version=evidence_algorithm_version,
        evidence_config_hash=evidence_config_hash,
        evidence_subject_ref=source.evidence_subject_ref,
        coverage_status=coverage_status,
        coverage_summary=source.coverage_summary,
        source_coverage=source.source_coverage,
        human_decision=source.human_decision,
        limitations=tuple(dict.fromkeys(limitations)),
        temporal_evidence=(_temporal_evidence_from_summary(summary)),
        next_action=next_action,
    )


def _temporal_evidence_from_summary(
    summary: EvidenceIndependenceSummary | None,
) -> TemporalEvidenceSummary | None:
    """Propagate the temporal certificate carried by ONE aggregation.

    ``EvidenceIndependenceSummary.temporal_certificate`` is populated by the
    same ``build_summary`` run that produced this card's evidence numbers, so no
    second aggregation is ever executed to fill the card temporal block.
    """
    if summary is None or summary.temporal_certificate is None:
        return None
    certificate = summary.temporal_certificate
    return TemporalEvidenceSummary(
        reference_date=(
            certificate.reference_date.isoformat()
            if certificate.reference_date is not None
            else None
        ),
        publish_time_coverage=certificate.publish_time_coverage,
        median_market_age_days=certificate.median_market_age_days,
        p90_market_age_days=certificate.p90_market_age_days,
        stale_evidence_ratio=certificate.stale_evidence_ratio,
        freshness_adjusted_neff=summary.effective_sample_size,
        source_lag_summary=tuple(
            TemporalSourceLagRow(
                source_id=profile.source_id,
                valid_sample_count=profile.valid_crawler_delay_samples,
                median_delay_days=profile.median_delay_days,
                p90_delay_days=profile.p90_delay_days,
                pipeline_observation_count=profile.pipeline_observation_count,
                unknown_provenance_count=profile.unknown_provenance_count,
                missing_publish_count=profile.missing_publish_count,
                invalid_sample_count=profile.invalid_sample_count,
            )
            for profile in certificate.source_lag_profiles
        ),
        temporal_algorithm_version=summary.temporal_algorithm_version,
        temporal_reasons=tuple(
            reason
            for reason in summary.uncertainty_reasons
            if reason
            in {
                "temporal_coverage_low",
                "source_lag_profile_insufficient",
                "temporal_state_indeterminate",
                "temporal_anomaly_detected",
                "high_stale_evidence_ratio",
                "all_clusters_stale",
            }
        ),
        fresh_evidence_count=certificate.fresh_evidence_count,
        stale_evidence_count=certificate.stale_evidence_count,
        unknown_evidence_count=certificate.unknown_evidence_count,
        time_provenance_policy=TIME_PROVENANCE_POLICY,
    )


def derive_next_action(
    uncertainty_state: str,
    authority_state: AuthorityState,
    preferred: NextAction | None = None,
) -> NextAction:
    if uncertainty_state == "blocked":
        return "rerun"
    if uncertainty_state in ("insufficient_evidence", "not_observed"):
        return "collect_evidence"
    if uncertainty_state in _REVIEW_UNCERTAINTY:
        return "review"
    if authority_state == "candidate":
        return preferred if preferred in ("publish", "user_action") else "review"
    if authority_state == "reviewed":
        return preferred if preferred in ("publish", "user_action") else "publish"
    if preferred in ("publish", "user_action"):
        return preferred
    return "user_action"


def sensitivity_results_from_certificate(
    certificate: AblationCertificate | None,
) -> tuple[tuple[SensitivityResult, ...], str | None]:
    if certificate is None:
        return (), None
    results = tuple(
        SensitivityResult(
            ablation_type=item.ablation_type,
            removed_group_id=item.removed_group_id,
            removed_share=item.removed_share,
            before_state=item.before_state,
            after_state=item.after_state,
            threshold_crossed=item.threshold_crossed,
            before_score=item.before_score,
            after_score=item.after_score,
            certificate_status=certificate.certificate_status,
        )
        for item in certificate.ablations
    )
    fragile = next(
        (
            f"{item.ablation_type}:{item.removed_group_id}"
            for item in certificate.ablations
            if item.threshold_crossed or item.state_changed
        ),
        None,
    )
    return results, fragile


def content_hash(payload: MutableJsonObject) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


UNCERTAINTY_SEVERITY = {
    "ok": 0,
    "not_observed": 1,
    "stale_observation": 2,
    "unresolved": 3,
    "source_concentrated": 4,
    "insufficient_evidence": 5,
    "blocked": 6,
}


def merge_uncertainty_states(
    *states: tuple[str, tuple[str, ...]],
) -> tuple[str, tuple[str, ...]]:
    """Compose business and evidence uncertainty without letting one replace
    the other: the most severe state wins and all reasons are preserved."""

    ordered = sorted(
        states,
        key=lambda item: UNCERTAINTY_SEVERITY.get(item[0], len(UNCERTAINTY_SEVERITY)),
        reverse=True,
    )
    state = ordered[0][0]
    reasons: list[str] = []
    for _, state_reasons in ordered:
        reasons.extend(state_reasons)
    return state, tuple(dict.fromkeys(reasons))


def bind_evidence_versions(
    refs: tuple[EvidenceRef, ...],
    versions: Mapping[str, str] | None,
) -> tuple[EvidenceRef, ...]:
    if not versions:
        return refs
    return tuple(
        replace(ref, source_version=str(versions[ref.evidence_id]))
        if ref.evidence_id in versions
        else ref
        for ref in refs
    )


def _gated_authority(
    source: InsightCardSource,
) -> tuple[AuthorityState, tuple[str, ...]]:
    requested = source.original_authority_state or source.authority_state
    reasons: list[str] = []
    has_evidence = bool(source.evidence_refs) or bool(source.counter_evidence_refs)
    if not has_evidence:
        reasons.append("no_supporting_evidence")
        requested = "candidate"
    state = source.uncertainty_state
    if state in _BLOCKING_UNCERTAINTY:
        reasons.append(f"uncertainty_{state}_blocks_authoritative_conclusion")
        requested = "candidate"
    elif state in _REVIEW_UNCERTAINTY:
        reasons.append(f"uncertainty_{state}_requires_review")
        if requested == "authoritative":
            requested = "candidate"
    if source.human_decision is not None:
        decision = source.human_decision
        if decision.decision == "rejected":
            reasons.append("human_decision_rejected")
            requested = "candidate"
        elif state not in _BLOCKING_UNCERTAINTY:
            if requested == "candidate":
                requested = "reviewed"
    return requested, tuple(dict.fromkeys(reasons))


def _used_evidence_ids(source: InsightCardSource) -> tuple[str, ...]:
    if source.used_evidence_ids:
        return source.used_evidence_ids
    return tuple(ref.evidence_id for ref in source.evidence_refs if ref.used)


def _validate_evidence_binding(source: InsightCardSource) -> None:
    summary = source.evidence_summary
    certificate = source.certificate
    if (
        summary is not None or certificate is not None
    ) and not source.evidence_subject_ref:
        raise ValueError(
            "evidence_subject_ref is required when an evidence summary "
            "or certificate is attached"
        )
    if summary is not None:
        if source.evidence_subject_ref != summary.subject_ref:
            raise ValueError(
                "evidence_subject_ref must match summary.subject_ref"
            )
        if (
            source.release_refs
            and summary.release_id
            and summary.release_id not in source.release_refs
        ):
            raise ValueError(
                "summary.release_id must be present in release_refs"
            )
        visible_ids = {
            ref.evidence_id
            for ref in (*source.evidence_refs, *source.counter_evidence_refs)
        }
        if summary.evidence_ids:
            missing = sorted(set(summary.evidence_ids) - visible_ids)
            if missing:
                raise ValueError(
                    "summary evidence set must be a subset of card evidence: "
                    + ", ".join(missing)
                )
            if source.used_evidence_ids and set(summary.evidence_ids) != set(
                source.used_evidence_ids
            ):
                raise ValueError(
                    "summary evidence set must match used_evidence_ids"
                )
    if certificate is not None:
        expected_subject = (
            summary.subject_ref
            if summary is not None
            else source.evidence_subject_ref
        )
        if certificate.subject_ref != expected_subject:
            raise ValueError(
                "certificate.subject_ref must match the evidence subject"
            )
        if summary is not None:
            if (
                certificate.release_id is not None
                and summary.release_id is not None
                and certificate.release_id != summary.release_id
            ):
                raise ValueError(
                    "certificate.release_id must match summary.release_id"
                )
            if (
                certificate.config_hash
                and summary.config_hash
                and certificate.config_hash != summary.config_hash
            ):
                raise ValueError(
                    "certificate.config_hash must match summary.config_hash"
                )
        if (
            source.release_refs
            and certificate.release_id
            and certificate.release_id not in source.release_refs
        ):
            raise ValueError(
                "certificate.release_id must be present in release_refs"
            )


__all__ = [
    "assemble_insight_card",
    "bind_evidence_versions",
    "content_hash",
    "derive_next_action",
    "merge_uncertainty_states",
    "sensitivity_results_from_certificate",
]
