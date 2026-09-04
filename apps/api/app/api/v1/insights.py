from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.dependencies.accounts import (
    get_account_actor,
    get_authenticated_account,
)
from app.api.dependencies.container import get_application_container
from app.api.dependencies.evidence_independence import (
    get_emerging_conclusion_provider,
)
from app.api.dependencies.knowledge_graph import get_knowledge_graph_handlers
from app.api.dependencies.matching import get_matching_use_cases
from app.api.dependencies.release_registry import get_release_registry
from app.api.dependencies.trend_reports import get_trend_report_use_cases
from app.api.dependencies.use_cases import get_emerging_position_handlers
from app.application_container import ApplicationContainer
from app.contexts.access import AccountRecord
from app.contexts.emerging_positions import (
    EmergingPositionHandlers,
    EmergingPositionNotFound,
)
from app.contexts.insight_cards import (
    assemble_insight_card,
    emerging_card_source,
    evolution_event_evidence_ids,
    evolution_event_card_source,
    HumanDecision,
    matching_what_if_card_source,
    trend_report_card_source,
)
from app.contexts.insight_cards.business import (
    build_evidence_context,
    governance_evidence_to_independence,
)
from app.contexts.insight_cards.matching_scenarios import (
    WhatIfScenarioDraft,
    WhatIfScenarioNotFound,
)
from app.contexts.insight_cards.evidence_resolver import (
    ClaimEvidenceUnresolved,
    resolve_claim_evidence,
)
from app.contexts.insight_cards.release_registry import (
    ReleaseNotFound,
    ReleaseRegistry,
)
from app.contexts.evidence_independence.contracts import (
    ConclusionRecomputePort,
    ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
)
from app.contexts.evidence_independence.temporal import TemporalFreshnessRules
from app.contexts.source_jds import SourceJDNotFound
from app.contexts.knowledge_graph import (
    KnowledgeGraphPortalCommand,
    KnowledgeGraphPortalOperation,
    ManageKnowledgeGraphIntegration,
)
from app.contexts.market_intelligence import (
    ManageTrendReports,
    TrendReportNotFound,
)
from app.contexts.matching_learning import (
    ManageMatching,
    MatchingEvaluationNotFound,
    MatchingRuleViolation,
    MatchingServiceError,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.errors import PermissionDenied
from app.domain.json_types import thaw_json
from app.domain.json_types import freeze_json_object
from app.schemas.matching_bff import WhatIfCreate


router = APIRouter(prefix="/innovation/insights", tags=["insights"])


def _resolve_crawler_times(
    container: ApplicationContainer,
    rows: tuple,
) -> dict[str, datetime]:
    """Map each governance Evidence to its real crawler acquisition time.

    Crawler lineage is keyed ONLY by ``SourceJDVersion.id``
    (``source_jd_version_id``), never by the governance ``source_version`` /
    ``source_fact_version``.  The version id is looked up directly with
    ``SourceJDUseCases.get_version()`` and accepted only when it belongs to the
    row's ``source_jd_id`` and carries a real ``crawl_time``.  Rows without a
    ``source_jd_version_id`` (or with a mismatched/unknown version) are skipped
    and ``governance_evidence_to_independence`` falls through to
    pipeline-observed / unknown.  ``created_at`` / ``run_started_at`` are never
    used here (pipeline bookkeeping, not crawler).
    """
    use_cases = getattr(container, "source_jds", None)
    if use_cases is None:
        return {}
    crawl_times: dict[str, datetime] = {}
    for row in rows:
        source_jd_id = getattr(row, "source_jd_id", None)
        if source_jd_id is None and getattr(row, "related_object_type", None) == "source_jd":
            source_jd_id = getattr(row, "related_object_id", None)
        source_jd_version_id = getattr(row, "source_jd_version_id", None)
        if not source_jd_id or not source_jd_version_id:
            continue
        try:
            version = use_cases.get_version(source_jd_version_id)
        except SourceJDNotFound:
            continue
        if version is None:
            continue
        # The version id must actually belong to this SourceJD; a mismatched id
        # from another JD is never treated as crawler provenance.
        if getattr(version, "source_jd_id", None) != source_jd_id:
            continue
        crawl_time = getattr(version, "crawl_time", None)
        if crawl_time is not None:
            crawl_times[row.evidence_id] = crawl_time
    return crawl_times


def _release_evidence(
    container: ApplicationContainer,
    registry: ReleaseRegistry,
    release_id: str | None,
    *,
    subject_ref: str,
    rows: tuple,
    conclusion: ConclusionRecomputePort | None,
):
    if not release_id:
        return None, None
    try:
        identity = registry.resolve(release_id)
    except ReleaseNotFound as exc:
        raise HTTPException(
            status_code=422, detail=f"unknown release: {release_id}"
        ) from exc
    outside = [
        row.evidence_id
        for row in rows
        if not registry.evidence_within_release(identity, row.publish_date)
    ]
    if outside:
        raise HTTPException(
            status_code=409,
            detail="evidence outside release observation window: "
            + ", ".join(outside),
        )
    incomplete: list[str] = []
    outside_membership: list[str] = []
    for row in rows:
        source_jd_id = row.source_jd_id
        if source_jd_id is None and row.related_object_type == "source_jd":
            source_jd_id = row.related_object_id
        if not source_jd_id or not row.source_fact_id or not row.source_version:
            incomplete.append(row.evidence_id)
            continue
        if not registry.evidence_belongs_to_release(
            identity,
            source_jd_id=source_jd_id,
            source_fact_id=row.source_fact_id,
            source_version=row.source_version,
        ):
            outside_membership.append(row.evidence_id)
    if outside_membership:
        raise HTTPException(
            status_code=409,
            detail="evidence outside release artifact membership: "
            + ", ".join(outside_membership),
        )
    records = governance_evidence_to_independence(
        rows,
        subject_ref,
        release_id,
        crawl_times=_resolve_crawler_times(container, rows),
    )
    return build_evidence_context(
        records,
        subject_ref,
        release_id,
        coverage_status="unknown",
        observation_reference_date=identity.observation_window_end.date(),
        conclusion=conclusion if not incomplete else None,
        pending_reasons=("evidence_identity_incomplete",) if incomplete else (),
        # TEMP-LAG-01: the real business InsightCard path explicitly enables
        # temporal freshness with a release-cutoff reference date (never wall
        # clock).  Legacy ``auto`` semantics are untouched elsewhere.
        aggregation_version=ROBUST_EVIDENCE_AGGREGATION_VERSION_V5,
        temporal_rules=TemporalFreshnessRules(
            half_life_days=60.0,
            stale_gate_enabled=True,
        ),
    )


def _resolve_evidence(
    container: ApplicationContainer,
    registry: ReleaseRegistry,
    release_id: str,
    claim_ids: tuple[str, ...],
):
    if release_id:
        try:
            registry.resolve(release_id)
        except ReleaseNotFound as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unknown release: {release_id}",
            ) from exc
    try:
        return resolve_claim_evidence(
            container.evidence_read, claim_ids
        )
    except ClaimEvidenceUnresolved as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _human_decision(
    container: ApplicationContainer,
    *,
    object_type: str,
    object_id: str,
    original_authority_state: str | None = None,
    release_ref: str | None = None,
    graph_version_ref: str | None = None,
    algorithm_version: str | None = None,
    config_version: str | None = None,
):
    decision = container.review_chain.get_terminal_decision(
        object_type, object_id
    )
    if decision is None:
        return None
    return HumanDecision(
        decision_id=decision.decision_id,
        decision=decision.decision,
        decided_at=(
            decision.decided_at.isoformat()
            if decision.decided_at is not None
            else None
        ),
        decided_by=decision.decided_by,
        reason=decision.reason,
        original_authority_state=original_authority_state,
        bound_object_type=object_type,
        bound_object_id=object_id,
        release_ref=release_ref,
        graph_version_ref=graph_version_ref,
        algorithm_version=algorithm_version,
        config_version=config_version,
    )


@router.get("/emerging/{emerging_id}")
def emerging_insight_card(
    emerging_id: str,
    release_id: str | None = None,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(
        get_emerging_position_handlers
    ),
    container: ApplicationContainer = Depends(get_application_container),
    registry: ReleaseRegistry = Depends(get_release_registry),
    conclusion: ConclusionRecomputePort | None = Depends(
        get_emerging_conclusion_provider
    ),
):
    actor = AccountActor(current_user.account_id, current_user.role)
    try:
        record = handlers.query.get(emerging_id, actor)
    except EmergingPositionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    resolved = (
        _resolve_evidence(
            container,
            registry,
            release_id,
            tuple(record.candidate.evidence_jd_ids),
        )
        if release_id
        else None
    )
    summary, certificate = _release_evidence(
        container,
        registry,
        release_id,
        subject_ref=emerging_id,
        rows=resolved.records if resolved is not None else (),
        conclusion=conclusion,
    )
    human_decision = _human_decision(
        container,
        object_type="emerging_position",
        object_id=emerging_id,
        release_ref=release_id,
        algorithm_version="emerging-position.v1",
    )
    source = emerging_card_source(
        record,
        summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        evidence_refs=resolved.refs if resolved is not None else None,
        evidence_subject_ref=emerging_id if summary is not None else None,
    )
    return success_response(data=asdict(assemble_insight_card(source)))


@router.get("/trend/{report_id}")
def trend_insight_card(
    report_id: str,
    release_id: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageTrendReports = Depends(get_trend_report_use_cases),
    container: ApplicationContainer = Depends(get_application_container),
    registry: ReleaseRegistry = Depends(get_release_registry),
):
    try:
        record = use_cases.get(report_id, actor)
    except (TrendReportNotFound, PermissionDenied) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    resolved = (
        _resolve_evidence(
            container,
            registry,
            release_id,
            tuple(record.evidence_references),
        )
        if release_id
        else None
    )
    summary, certificate = _release_evidence(
        container,
        registry,
        release_id,
        subject_ref=record.position_id,
        rows=resolved.records if resolved is not None else (),
        conclusion=None,
    )
    human_decision = _human_decision(
        container,
        object_type="trend_report",
        object_id=report_id,
        release_ref=release_id,
        graph_version_ref=record.graph_version_id,
        algorithm_version=record.algorithm_version or record.formula_version,
        config_version=record.formula_version,
    )
    source = trend_report_card_source(
        record,
        summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        evidence_refs=resolved.refs if resolved is not None else None,
        evidence_subject_ref=(
            record.position_id if summary is not None else None
        ),
    )
    return success_response(data=asdict(assemble_insight_card(source)))


@router.get("/evolution/{position_id}/{event_id}")
def evolution_insight_card(
    position_id: str,
    event_id: str,
    release_id: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    handlers: ManageKnowledgeGraphIntegration = Depends(
        get_knowledge_graph_handlers
    ),
    container: ApplicationContainer = Depends(get_application_container),
    registry: ReleaseRegistry = Depends(get_release_registry),
):
    command = KnowledgeGraphPortalCommand(
        operation=KnowledgeGraphPortalOperation.EVOLUTION_EVENT,
        position_id=position_id,
        resource_id=event_id,
    )
    value = handlers.portal(actor, command)
    event = thaw_json(value.result) or {}
    evidence_ids = evolution_event_evidence_ids(event)
    resolved = (
        _resolve_evidence(
            container, registry, release_id, evidence_ids
        )
        if release_id
        else None
    )
    summary, certificate = _release_evidence(
        container,
        registry,
        release_id,
        subject_ref=position_id,
        rows=resolved.records if resolved is not None else (),
        conclusion=None,
    )
    human_decision = _human_decision(
        container,
        object_type="evolution_event",
        object_id=event_id,
        release_ref=release_id,
        graph_version_ref=str(event.get("from_version") or ""),
        algorithm_version=(
            event.get("detector_version") or event.get("config_version")
        ),
        config_version=event.get("config_version"),
    )
    source = evolution_event_card_source(
        event,
        summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        evidence_refs=resolved.refs if resolved is not None else None,
        evidence_subject_ref=position_id if summary is not None else None,
    )
    return success_response(data=asdict(assemble_insight_card(source)))


@router.post("/matching/{evaluation_id}/what-if")
def matching_what_if_insight_card(
    evaluation_id: str,
    payload: WhatIfCreate,
    request: Request,
    release_id: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageMatching = Depends(get_matching_use_cases),
    container: ApplicationContainer = Depends(get_application_container),
    registry: ReleaseRegistry = Depends(get_release_registry),
):
    try:
        result = use_cases.what_if(
            actor,
            evaluation_id,
            actions=tuple(
                item.model_dump(mode="python") for item in payload.actions
            ),
            correlation_id=request.state.trace_id,
        )
    except (
        MatchingEvaluationNotFound,
        MatchingRuleViolation,
        MatchingServiceError,
        PermissionDenied,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    evidence_ids = _matching_evidence_ids(result)
    resolved = (
        _resolve_evidence(
            container, registry, release_id, evidence_ids
        )
        if release_id
        else None
    )
    summary, certificate = _release_evidence(
        container,
        registry,
        release_id,
        subject_ref=evaluation_id,
        rows=resolved.records if resolved is not None else (),
        conclusion=None,
    )
    scenario_id = str(result.get("scenario_id") or "")
    if scenario_id:
        container.matching_scenarios.save(
            WhatIfScenarioDraft(
                scenario_id=scenario_id,
                evaluation_id=evaluation_id,
                actions_payload=freeze_json_object(
                    {
                        "actions": [
                            item.model_dump(mode="json")
                            for item in payload.actions
                        ]
                    }
                ),
                result_payload=freeze_json_object(result),
                release_id=release_id,
                graph_version=result.get("position_graph_version"),
                algorithm_version=(
                    result.get("algorithm_version")
                    or result.get("scoring_algorithm_version")
                ),
                config_version=result.get("scoring_config_version"),
                created_by=actor.account_id,
            )
        )
        container.review_chain.create_scenario_review(
            "matching_scenario",
            scenario_id,
            "normal",
            "What-if scenario review",
        )
    human_decision = _human_decision(
        container,
        object_type="matching_scenario",
        object_id=scenario_id,
        release_ref=release_id,
        algorithm_version=(
            result.get("algorithm_version")
            or result.get("scoring_algorithm_version")
        ),
        config_version=result.get("scoring_config_version"),
    )
    source = matching_what_if_card_source(
        result,
        summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        evidence_refs=resolved.refs if resolved is not None else None,
        evidence_subject_ref=evaluation_id if summary is not None else None,
    )
    return success_response(data=asdict(assemble_insight_card(source)))


@router.get("/matching/{scenario_id}")
def matching_scenario_insight_card(
    scenario_id: str,
    actor: AccountActor = Depends(get_account_actor),
    container: ApplicationContainer = Depends(get_application_container),
    registry: ReleaseRegistry = Depends(get_release_registry),
):
    try:
        record = container.matching_scenarios.get(actor, scenario_id)
    except WhatIfScenarioNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = thaw_json(record.result_payload) or {}
    effective_release = record.release_id
    resolved = (
        _resolve_evidence(
            container,
            registry,
            effective_release or "",
            _matching_evidence_ids(result),
        )
        if effective_release
        else None
    )
    summary, certificate = _release_evidence(
        container,
        registry,
        effective_release,
        subject_ref=record.evaluation_id,
        rows=resolved.records if resolved is not None else (),
        conclusion=None,
    )
    human_decision = _human_decision(
        container,
        object_type="matching_scenario",
        object_id=scenario_id,
        release_ref=record.release_id,
        graph_version_ref=record.graph_version,
        algorithm_version=record.algorithm_version,
        config_version=record.config_version,
    )
    source = matching_what_if_card_source(
        result,
        summary=summary,
        certificate=certificate,
        human_decision=human_decision,
        evidence_refs=resolved.refs if resolved is not None else None,
        evidence_subject_ref=(
            record.evaluation_id if summary is not None else None
        ),
    )
    return success_response(data=asdict(assemble_insight_card(source)))


def _matching_evidence_ids(result: dict) -> tuple[str, ...]:
    refs = result.get("evidence_refs") or ()
    ids = []
    for item in refs:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict) and item.get("evidence_id"):
            ids.append(str(item["evidence_id"]))
    return tuple(dict.fromkeys(ids))
