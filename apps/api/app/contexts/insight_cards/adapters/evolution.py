from __future__ import annotations

from collections.abc import Mapping

from app.domain.json_types import MutableJsonObject
from app.contexts.evidence_independence.contracts import (
    AblationCertificate,
    EvidenceIndependenceSummary,
)
from app.contexts.insight_cards.application import merge_uncertainty_states
from app.contexts.insight_cards.contracts import (
    EvidenceRef,
    HumanDecision,
    InsightCardSource,
)


EVOLUTION_EVENT_ALGORITHM_VERSION = "position-evolution-events.v1"


def evolution_event_card_source(
    event: MutableJsonObject,
    *,
    summary: EvidenceIndependenceSummary | None = None,
    certificate: AblationCertificate | None = None,
    human_decision: HumanDecision | None = None,
    insight_id: str | None = None,
    evidence_subject_ref: str | None = None,
    evidence_refs: tuple[EvidenceRef, ...] | None = None,
) -> InsightCardSource:
    """Map an Evolution Event BFF DTO into the shared InsightCard source.

    Graph version refs come from the event's from/to versions, data refs from
    the event lineage, and algorithm/config versions from the detector and
    config fields. Evidence refs are only set when the event payload carries
    explicit evidence ids, never invented by this adapter.
    """

    if not isinstance(event, Mapping):
        raise ValueError("evolution event must be an object")
    event_id = str(event.get("event_id") or "")
    position_id = str(event.get("position_id") or "")
    if not event_id or not position_id:
        raise ValueError(
            "evolution event requires non-empty event_id and position_id"
        )
    event_type = str(event.get("event_type") or "")
    from_version = _version(
        event.get("from_version") or event.get("from_graph_version_id")
    )
    to_version = _version(
        event.get("to_version") or event.get("to_graph_version_id")
    )
    source_labels = ", ".join(
        _entity_label(item) for item in _list(event.get("source_entities"))
    )
    target_labels = ", ".join(
        _entity_label(item) for item in _list(event.get("target_entities"))
    )
    if evidence_refs is None:
        evidence_refs = _evidence_refs(event)
    if summary is not None and summary.subject_ref != position_id:
        raise ValueError(
            "summary.subject_ref must match evolution position_id"
        )
    limitations: list[str] = []
    if not evidence_refs:
        limitations.append("evolution_event_without_evidence_refs")
    if not source_labels and not target_labels:
        limitations.append("evolution_event_without_entity_labels")

    graph_refs = tuple(
        str(value)
        for value in (from_version, to_version)
        if value is not None
    )
    catalog_raw = event.get("catalog_snapshot_id") or event.get(
        "catalog_refs"
    )
    catalog_refs = (
        tuple(str(item) for item in _list(catalog_raw))
        if catalog_raw is not None
        else ()
    )
    data_refs = _data_refs(event)

    business_uncertainty = (
        "blocked" if not evidence_refs else "ok"
    )
    business_reasons = (
        ("evolution_event_without_evidence_refs",)
        if not evidence_refs
        else ()
    )
    if summary is not None:
        uncertainty, reasons = merge_uncertainty_states(
            (summary.uncertainty_state, tuple(summary.uncertainty_reasons)),
            (business_uncertainty, business_reasons),
        )
        effective_size = summary.effective_sample_size
        raw_count = summary.raw_evidence_count
        release_refs = (summary.release_id,) if summary.release_id else ()
        evidence_algorithm_version = summary.algorithm_version
        evidence_config_hash = summary.config_hash
        coverage_status = summary.coverage_status
    else:
        uncertainty = business_uncertainty
        reasons = business_reasons
        effective_size = None
        raw_count = None
        release_refs = ()
        evidence_algorithm_version = ""
        evidence_config_hash = ""
        coverage_status = None
    algorithm_version = str(
        event.get("detector_version")
        or event.get("config_version")
        or EVOLUTION_EVENT_ALGORITHM_VERSION
    )

    return InsightCardSource(
        insight_id=insight_id or f"insight:evolution:{event_id}",
        claim_type="role_migration",
        subject_ref=position_id,
        claim=(
            f"position {position_id} evolution {event_type} "
            f"from {source_labels or 'none'} to {target_labels or 'none'} "
            f"between graph versions {from_version} and {to_version}"
        ),
        algorithm_version=algorithm_version,
        algorithm_config_version=event.get("config_version"),
        algorithm_config_hash=None,
        evidence_algorithm_version=evidence_algorithm_version,
        evidence_config_hash=evidence_config_hash,
        evidence_subject_ref=evidence_subject_ref,
        coverage_status=coverage_status,
        coverage_summary=(),
        source_coverage=None,
        authority_state="candidate",
        evidence_refs=evidence_refs,
        used_evidence_ids=tuple(ref.evidence_id for ref in evidence_refs),
        effective_sample_size=effective_size,
        raw_evidence_count=raw_count,
        uncertainty_state=uncertainty,
        uncertainty_reasons=reasons,
        release_refs=release_refs,
        graph_version_refs=graph_refs,
        catalog_refs=catalog_refs,
        data_refs=data_refs,
        limitations=tuple(limitations),
        evidence_summary=summary,
        certificate=certificate,
        human_decision=human_decision,
    )


def evolution_event_evidence_ids(event: MutableJsonObject) -> tuple[str, ...]:
    return tuple(ref.evidence_id for ref in _evidence_refs(event))


def _evidence_refs(event: Mapping[str, object]) -> tuple[EvidenceRef, ...]:
    evidence = event.get("evidence")
    if not isinstance(evidence, Mapping):
        return ()
    refs: list[EvidenceRef] = []
    for item in _list(evidence.get("evidence_refs")):
        if isinstance(item, str):
            refs.append(_document_ref(item))
        elif isinstance(item, Mapping):
            refs.append(_ref_from_mapping(item))
    source_document_id = evidence.get("source_document_id")
    if source_document_id:
        refs.append(_document_ref(str(source_document_id)))
    return _dedupe(refs)


def _ref_from_mapping(raw: Mapping[str, object]) -> EvidenceRef:
    evidence_id = str(raw["evidence_id"])
    return EvidenceRef(
        evidence_id=evidence_id,
        source_object_type=str(
            raw.get("source_object_type") or "source_document"
        ),
        source_object_id=str(raw.get("source_object_id") or evidence_id),
        source_document_id=str(
            raw.get("source_document_id") or evidence_id
        ),
        source_version=str(raw.get("source_version") or ""),
        quote=raw.get("quote"),
        location_start=raw.get("location_start"),
        location_end=raw.get("location_end"),
        used=bool(raw.get("used", True)),
    )


def _document_ref(evidence_id: str) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        source_object_type="source_document",
        source_object_id=evidence_id,
        source_document_id=evidence_id,
        source_version="",
        used=True,
    )


def _dedupe(refs: list[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    seen: set[str] = set()
    result: list[EvidenceRef] = []
    for ref in refs:
        if ref.evidence_id not in seen:
            seen.add(ref.evidence_id)
            result.append(ref)
    return tuple(result)


def _data_refs(event: Mapping[str, object]) -> tuple[str, ...]:
    evidence = event.get("evidence")
    if not isinstance(evidence, Mapping):
        return (f"evolution:{event.get('event_id')}",)
    lineage = evidence.get("lineage")
    if not isinstance(lineage, Mapping):
        return (f"evolution:{event.get('event_id')}",)
    for key in ("release_id", "from_release_id", "to_release_id"):
        value = lineage.get(key)
        if value:
            return (str(value),)
    return (f"evolution:{event.get('event_id')}",)


def _entity_label(item: object) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        return str(
            item.get("canonical_name")
            or item.get("name")
            or item.get("skill_id")
            or item.get("skill_name")
            or ""
        )
    return str(item)


def _version(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _list(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return (value,)


__all__ = [
    "EVOLUTION_EVENT_ALGORITHM_VERSION",
    "evolution_event_evidence_ids",
    "evolution_event_card_source",
]
