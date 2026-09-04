from __future__ import annotations

import uuid
import os
from dataclasses import asdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.api.contracts import (
    AlgorithmConfigInput, BuildInput,
    AutoReviewBuildInput,
    DependencyPolicyInput, DependencyReviewInput,
    DraftCreate, JDCreate,
    LoginInput, MappingCandidateInput, MappingReviewInput, ProjectionRebuildInput,
    PublishInput, PublishedJDFactV3Envelope, QualityInput,
    RelationExplanationEnvelope, RelationPageEnvelope,
    RelationModify, ReviewAction, ReviewActionEnvelope, ReviewModifyAction,
    ReviewTaskCreate, ReviewTaskListEnvelope, WatermarkComparisonInput,
    PositionProfileBatchInput,
    SkillRelationBatchInput,
    DependencyReferenceInput,
    EmergenceV32EvaluateInput,
)
from app.api.dependencies import (
    current_actor,
    get_application_handlers,
    get_identity_service,
    get_query_service,
    require_graph_editor,
    require_internal_reader,
    require_publisher,
    require_reviewer,
)
from app.api.fact_mappers import (
    extraction_facts,
    jd_document_input,
    normalization_facts,
    published_jd_fact,
)
from app.application.identity import (
    AuthenticationFailed,
    AuthorizationDenied,
    IdentityService,
)
from app.application.lineage_mapper import map_published_fact_lineage
from app.application.handlers import ApplicationHandlers
from app.domain.build_jobs import BuildJobTransitionError
from app.application.errors import ConflictError, PublishGateError
from app.application.publish_gate_mapper import publish_gate_errors
from app.application.contracts import (
    AnalyzeDependenciesCommand,
    BuildGraphCommand,
    CompareWatermarksCommand,
    CreateMappingCandidateCommand,
    DocumentWorkflowCommand,
    ImportJDCommand,
    ImportPublishedJDFactCommand,
    PublishGraphCommand,
    RebuildProjectionCommand,
    ReviewMappingCandidateCommand,
    ReviewDependencyCandidateCommand,
    RollbackGraphCommand,
)
from app.domain.dependency_analysis import DependencyPolicy
from app.domain.temporal_analysis import ComparabilityContext
from app.domain.traceability import (
    MappingAffectedContext,
    MappingCandidate,
    MappingCandidateSignals,
    MappingPriorityWeights,
)
from app.application.queries import KnowledgeGraphQueryService
from app.domain.write_models import (
    AlgorithmConfigUpdate,
    RelationModification,
    ReviewCompletion,
    ReviewTaskDraft,
    SkillResolutionRequest,
)
from app.emergence.policy import (
    EMBEDDING_MODEL,
    EMBEDDING_REVISION,
    EmergenceV32Policy,
)
from app.emergence.formal_replay import run_formal_replay
from app.application.review_actions import allowed_review_actions
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import JDNormalizedResult
from jobgraph_contracts.catalog import StandardSkillSnapshotV1, StandardSkillSnapshotV2
from jobgraph_contracts.published_jd import PublishedJDFactV3
from jobgraph_contracts.position_profile import PositionProfileV3
from jobgraph_contracts.review import ReviewBatchOperationV1, ReviewBatchResultV1

router = APIRouter()


@lru_cache(maxsize=1)
def _emergence_v32_policy() -> EmergenceV32Policy:
    # The policy owns the only formal EMERGE implementation. Missing frozen
    # assets or BGE configuration must abort the request instead of selecting
    # a legacy discovery score.
    return EmergenceV32Policy.from_frozen_assets(env=os.environ)


def _etag_response(request: Request, response: Response, payload: Any):
    if isinstance(payload, dict):
        versions = []
        for item in payload.get("items", [payload]):
            if isinstance(item, dict):
                version = item.get("source_version") or item.get("graph_version")
                if version:
                    versions.append(str(version))
        if versions:
            etag = '"' + ",".join(versions) + '"'
            response.headers["ETag"] = etag
            if request.headers.get("If-None-Match") == etag:
                return Response(status_code=304, headers={"ETag": etag})
    return None


def _validated_profile(value: dict, contract_version: str) -> dict:
    if contract_version != "position-profile.v3":
        raise HTTPException(422, "position-profile.v3 is required")
    return PositionProfileV3.model_validate(value).model_dump(mode="json")


@router.put('/api/v1/integrations/catalog/skills/{skill_id}')
def import_capability_skill(
    skill_id: str,
    body: StandardSkillSnapshotV1,
    request: Request,
    handlers: ApplicationHandlers=Depends(get_application_handlers),
    user: Any=Depends(require_graph_editor),
):
    if body.skill_id != skill_id:
        raise HTTPException(422, 'path skill_id must match snapshot skill_id')
    context = integration_context(request, user, required=True)
    result = handlers.import_capability_skill.execute(
        body,
        user.id,
        request.state.trace_id,
        context,
    )
    return ok(result, request.state.trace_id)


@router.put('/api/v2/integrations/catalog/skills/{skill_id}')
def import_capability_skill_v2(
    skill_id: str,
    body: StandardSkillSnapshotV2,
    request: Request,
    handlers: ApplicationHandlers=Depends(get_application_handlers),
    user: Any=Depends(require_graph_editor),
):
    if body.skill_id != skill_id:
        raise HTTPException(422, 'path skill_id must match snapshot skill_id')
    context = integration_context(request, user, required=True)
    result = handlers.import_capability_skill.execute(
        body,
        user.id,
        request.state.trace_id,
        context,
    )
    return ok(result, request.state.trace_id)


def ok(data=None, trace_id=None, message="success"):
    return {"code": 0, "message": message, "data": data,
            "trace_id": trace_id or f"req_{uuid.uuid4().hex[:16]}"}


def missing(name="resource"):
    raise HTTPException(404, f"{name} not found")


def integration_context(request: Request, user: Any, *, required: bool = False) -> dict:
    try:
        identity = request.app.state.identity_service.integration_identity(
            user,
            request.headers.get("X-Main-User-Id"),
            request.headers.get("X-Main-User-Role"),
            required=required,
        )
    except AuthorizationDenied as exc:
        raise HTTPException(
            403,
            {
                "message": str(exc),
                "error_code": "AUTHORITATIVE_IMPORT_REQUIRES_SERVICE_ACCOUNT",
            },
        ) from exc
    if identity is None:
        return {}
    return {"main_user_id": identity.main_user_id, "main_user_role": identity.main_user_role}


@router.get("/health")
def health(request: Request):
    return ok({"status": "healthy", "providers": request.app.state.providers})


@router.get("/readiness")
@router.get("/api/v1/readiness")
def readiness(request: Request, response: Response):
    data = request.app.state.readiness.check()
    data["providers"] = request.app.state.providers
    if data["status"] != "ready":
        response.status_code = 503
    return ok(data, request.state.trace_id)


@router.post("/api/v1/auth/token")
def login(body: LoginInput, request: Request, identities: IdentityService = Depends(get_identity_service)):
    try:
        token = identities.login(body.username, body.password)
    except AuthenticationFailed as exc:
        raise HTTPException(401, str(exc)) from exc
    return ok({"access_token": token.value, "token_type": "bearer", "role": token.role}, request.state.trace_id)


@router.get("/api/v1/auth/me")
def me(request: Request, user: Any = Depends(current_actor)):
    return ok({"id": user.id, "username": user.username, "role": user.role}, request.state.trace_id)


@router.post("/api/v1/jds")
def create_jd(body: JDCreate, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.import_jd.execute(ImportJDCommand(
        jd_document_input(body), user.id, request.state.trace_id,
        integration_context(request, user),
    ))
    return ok(asdict(result), request.state.trace_id)


@router.put("/api/v1/integrations/jds/{document_id}")
def upsert_integration_jd(document_id: str, body: JDCreate, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.upsert_jd.execute(
        jd_document_input(body, document_id=document_id),
        user.id,
        request.state.trace_id,
        integration_context(request, user),
    )
    return ok(asdict(result), request.state.trace_id)


@router.post(
    "/api/v3/integrations/published-jd-facts",
    response_model=PublishedJDFactV3Envelope,
)
def import_published_jd_fact_v3(
    body: PublishedJDFactV3,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_graph_editor),
):
    context = integration_context(request, user, required=True)
    validation_payload = body.validation_lineage.model_dump(mode="json")
    validation = None
    if validation_payload.pop("state") == "present":
        validation_payload.pop("absent_reason")
        validation = validation_payload
    else:
        validation_payload.pop("absent_reason")
        if any(value is not None for value in validation_payload.values()):
            raise HTTPException(422, "absent validation lineage contains values")
    lineage = map_published_fact_lineage(
        validation=validation,
        catalog=body.skill_catalog_snapshot.model_dump(mode="json"),
    )
    command = ImportPublishedJDFactCommand(published_jd_fact(body), lineage)
    result = handlers.import_published_fact.execute(
        command, user.id, request.state.trace_id, context
    )
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/jds")
def list_jds(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    return ok(queries.list_documents(), request.state.trace_id)


@router.get("/api/v1/jds/{document_id}")
def get_jd(document_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    return ok(queries.document(document_id) or missing("JD"), request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/extract")
@router.post("/api/v1/jds/{document_id}/parse")
def extract(document_id: str, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.extract_jd.execute(DocumentWorkflowCommand(document_id))
    return ok(asdict(result.facts), request.state.trace_id)


@router.get("/api/v1/jds/{document_id}/extraction-result")
@router.get("/api/v1/jds/{document_id}/parse-result")
def extraction_result(document_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    return ok(queries.extraction(document_id) or missing("extraction"), request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/extraction-result/import")
def import_extraction(document_id: str, body: JDExtractionResult, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.import_extraction.execute(
        document_id,
        extraction_facts(body),
        user.id,
        request.state.trace_id,
        integration_context(request, user),
    )
    return ok(asdict(result), request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/extraction-result/align")
def align(document_id: str, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.confirm_extraction.execute(
        document_id,
        user.id,
        request.state.trace_id,
        integration_context(request, user),
    )
    return ok(asdict(result), request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/normalize")
def normalize(document_id: str, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.normalize_jd.execute(DocumentWorkflowCommand(document_id))
    return ok(asdict(result.facts), request.state.trace_id)


@router.get("/api/v1/jds/{document_id}/normalized-result")
def normalized_result(document_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    return ok(queries.normalization(document_id) or missing("normalization"), request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/normalized-result/import")
def import_normalized(document_id: str, body: JDNormalizedResult, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.import_normalized.execute(
        document_id,
        normalization_facts(body),
        user.id,
        request.state.trace_id,
        integration_context(request, user),
    )
    return ok({"record_id": result.record_id, **asdict(result.facts)}, request.state.trace_id)


@router.post("/api/v1/jds/{document_id}/duplicate-check")
@router.post("/api/v1/jds/{document_id}/inflation-check")
def assess(document_id: str, body: QualityInput, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.assess_quality.execute(DocumentWorkflowCommand(document_id))
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/normalization/unresolved-items")
def unresolved(request: Request, status: str="open", queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_reviewer)):
    return ok(queries.unresolved_items(status), request.state.trace_id)


def _resolve(item_id, action, body, request, handlers, user):
    if action == 'create_skill' and not request.app.state.settings.catalog_writes_enabled:
        raise ConflictError(
            'standard skill writes belong to the main capability catalog',
            error_code='CAPABILITY_CATALOG_OWNS_STANDARD_SKILLS',
        )
    values = body.payload or {}
    known = {"skill_id", "canonical_name", "category_code", "subcategory_code", "alias"}
    resolution = SkillResolutionRequest(
        skill_id=values.get("skill_id"),
        canonical_name=values.get("canonical_name"),
        category_code=values.get("category_code"),
        subcategory_code=values.get("subcategory_code"),
        alias=values.get("alias"),
        extensions={key: value for key, value in values.items() if key not in known},
    )
    result = handlers.resolve_skill.execute(
        item_id,
        action,
        resolution,
        user.id,
        body.reason,
        request.state.trace_id,
    )
    data = {"id": result.item_id, "status": result.status}
    if result.skill_id is not None:
        data["skill_id"] = result.skill_id
    if result.canonical_name is not None:
        data["canonical_name"] = result.canonical_name
    return ok(data, request.state.trace_id)


@router.post("/api/v1/normalization/unresolved-items/{item_id}/resolve")
def resolve(item_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _resolve(item_id, "resolve", body, request, handlers, user)
@router.post("/api/v1/normalization/unresolved-items/{item_id}/create-skill")
def create_skill(item_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _resolve(item_id, "create_skill", body, request, handlers, user)
@router.post("/api/v1/normalization/unresolved-items/{item_id}/reject")
def reject_item(item_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _resolve(item_id, "reject", body, request, handlers, user)


@router.get("/api/v1/positions")
def positions(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.positions(), request.state.trace_id)
@router.get("/api/v1/integrations/positions")
def integration_positions(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)): return ok(queries.integration_positions(), request.state.trace_id)
@router.get("/api/v1/integrations/position-references")
def integration_position_references(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    return ok(queries.integration_position_references(), request.state.trace_id)


@router.post("/api/v1/integrations/emergence/v3.2/evaluate")
def evaluate_emergence_v32(
    body: EmergenceV32EvaluateInput,
    request: Request,
    user: Any = Depends(require_internal_reader),
):
    policy = _emergence_v32_policy()
    results = []
    for cluster in body.clusters:
        explanation = policy.explain_candidate(
            title=cluster.title,
            skills=cluster.skills,
            responsibilities=cluster.responsibilities,
            exclude_document_ids=[item.document_id for item in cluster.members],
        )
        members = [
            {
                "date": item.observation_date.isoformat(),
                "source_record_id": item.source_record_id,
                "content_hash": item.content_hash,
                "company": item.company,
                "platform": item.source_platform,
                "bundle_id": item.bundle_id,
                "evidence_refs": item.evidence_refs,
            }
            for item in cluster.members
        ]
        layers = policy.cluster_layers(cluster_key=cluster.cluster_id, members=members)
        decisions = policy.decide_cluster(
            cluster_relation=str(explanation.get("relation") or ""),
            layers=layers,
            structural_evidence=explanation,
        )
        decision = dict(decisions["baseline"])
        counts = dict(decision.get("counts") or {})
        counts["observations"] = len(cluster.members)
        decision["counts"] = counts
        results.append(
            {
                "cluster_id": cluster.cluster_id,
                "state": decision.pop("state"),
                "evidence_level": decision.pop("evidence_level"),
                "stage1": explanation,
                "temporal_layers": layers,
                **decision,
            }
        )
    return ok(
        {
            "algorithm": "emerge_v3_2",
            "algorithm_version": "emerge-v3.2",
            "dataset_id": body.dataset_id,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_revision": EMBEDDING_REVISION,
            "clusters": results,
        },
        request.state.trace_id,
    )


@router.post("/api/v1/integrations/emergence/v3.2/formal-replay")
def replay_formal_emergence_v32(
    request: Request,
    user: Any = Depends(require_internal_reader),
):
    return ok(run_formal_replay(), request.state.trace_id)


@router.get("/api/v1/integrations/positions/{position_id}/skill-relations")
def integration_skill_relations(
    position_id: str,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_graph_editor),
):
    integration_context(request, user, required=True)
    result = queries.skill_relation_snapshot(position_id)
    if result is None:
        missing("published skill relation snapshot")
    return ok(result, request.state.trace_id)


@router.get("/api/v2/integrations/positions/{position_id}/skill-relations")
def integration_skill_relations_v2(
    position_id: str,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_graph_editor),
):
    integration_context(request, user, required=True)
    result = queries.skill_relation_snapshot_v2(position_id)
    if result is None:
        missing("published skill relation snapshot")
    return ok(result, request.state.trace_id)


@router.get("/api/v1/positions/{position_id}")
def position(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.position(position_id) or missing("position"), request.state.trace_id)
@router.get("/api/v1/skills")
def skills(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.skills(), request.state.trace_id)
@router.get("/api/v1/skills/{skill_id}")
def skill(skill_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.skill(skill_id) or missing("skill"), request.state.trace_id)
@router.get("/api/v1/skill-categories/tree")
def skill_tree(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.category_tree("skill"), request.state.trace_id)
@router.get("/api/v1/position-categories/tree")
def position_tree(request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.category_tree("position"), request.state.trace_id)


def _build_job_data(job, queries) -> dict:
    value = queries.build_job(job.job_id)
    return value or {
        "job_id": job.job_id,
        "job_key": job.job_key,
        "position_id": job.position_id,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "build_run_id": job.build_run_id,
        "error": None,
    }


@router.post("/api/v1/positions/{position_id}/graph/build", status_code=202)
def build(position_id: str, body: BuildInput, request: Request, response: Response, handlers: ApplicationHandlers=Depends(get_application_handlers), queries: KnowledgeGraphQueryService = Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    context = integration_context(request, user)
    try:
        job = handlers.build_jobs.enqueue(
            BuildGraphCommand(
                position_id,
                body.window_start,
                body.window_end,
                body.minimum_effective_weight,
                body.minimum_valid_samples,
                bool(context),
                user.id,
                request.state.trace_id,
                context,
            ),
            request.app.state.settings.build_job_max_attempts,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except BuildJobTransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    if request.app.state.settings.runs_build_jobs_inline:
        job = request.app.state.build_job_runner.run_once(job.job_id) or job
        response.status_code = 200
    return ok(_build_job_data(job, queries), request.state.trace_id)


@router.get("/api/v1/graph/build-jobs/{job_id}")
def build_job(job_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    result = queries.build_job(job_id)
    return ok(result if result is not None else missing("build job"), request.state.trace_id)


@router.post("/api/v1/graph/build-jobs/{job_id}/retry", status_code=202)
def retry_build_job(job_id: int, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    try:
        job = handlers.build_jobs.retry(job_id)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    if request.app.state.settings.runs_build_jobs_inline:
        job = request.app.state.build_job_runner.run_once(job.job_id) or job
    return ok(_build_job_data(job, queries), request.state.trace_id)


@router.get("/api/v1/positions/{position_id}/graph/build-runs")
def runs(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)): return ok(queries.build_runs(position_id), request.state.trace_id)
@router.get("/api/v1/graph/build-runs/{run_id}/samples")
def build_samples(run_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    result=queries.build_samples(run_id); return ok(result if result is not None else missing("build run"), request.state.trace_id)
@router.get("/api/v1/graph/build-runs/{run_id}")
def build_run(run_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    result=queries.build_run(run_id); return ok(result if result is not None else missing("build run"), request.state.trace_id)
@router.get("/api/v1/graph/build-runs/{run_id}/publish-gate")
def build_publish_gate(run_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    result=queries.publish_gate(run_id); return ok(result if result is not None else missing("build run"), request.state.trace_id)


@router.get("/api/v1/positions/{position_id}/graph")
def graph(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.graph(position_id), request.state.trace_id)


@router.get("/api/v1/position-profiles/{position_id}")
def position_profile(
    position_id: str,
    request: Request,
    response: Response,
    contract_version: Literal["position-profile.v3"] = "position-profile.v3",
    graph_version_id: int | None = Query(None, ge=1),
    view: Literal["published", "draft", "experimental"] = "published",
    draft_id: int | None = Query(None, ge=1),
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    if view != "published" and draft_id is None:
        raise HTTPException(422, "draft_id is required for draft or experimental reads")
    result = queries.position_profile(
        position_id,
        contract_version=contract_version,
        graph_version_id=graph_version_id,
        view=view,
        draft_id=draft_id,
    )
    if result is None:
        raise HTTPException(404, "position profile not found")
    profile = _validated_profile(result, contract_version)
    cached = _etag_response(request, response, profile)
    return cached or ok(profile, request.state.trace_id)


def _batch_profiles(body: PositionProfileBatchInput, queries: KnowledgeGraphQueryService):
    if body.view != "published":
        missing_drafts = [
            position_id
            for position_id in body.position_ids
            if position_id not in body.draft_ids
        ]
        if missing_drafts:
            raise HTTPException(
                422,
                {"message": "draft_ids are required", "position_ids": missing_drafts},
            )
    start = (body.page - 1) * body.page_size
    selected = body.position_ids[start : start + body.page_size]
    items = []
    missing_ids = []
    for position_id in selected:
        result = queries.position_profile(
            position_id,
            contract_version=body.contract_version,
            graph_version_id=body.graph_version_ids.get(position_id),
            view=body.view,
            draft_id=body.draft_ids.get(position_id),
        )
        if result is None:
            missing_ids.append(position_id)
        else:
            items.append(_validated_profile(result, body.contract_version))
    return {
        "items": items,
        "missing_position_ids": missing_ids,
        "page": body.page,
        "page_size": body.page_size,
        "total": len(body.position_ids),
    }


@router.post("/api/v1/position-profiles/batch")
def position_profiles_batch(
    body: PositionProfileBatchInput,
    request: Request,
    response: Response,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    result = _batch_profiles(body, queries)
    cached = _etag_response(request, response, result)
    return cached or ok(result, request.state.trace_id)


@router.post("/api/v1/position-profiles/evidence/batch")
def position_profile_evidence_batch(
    body: PositionProfileBatchInput,
    request: Request,
    response: Response,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    profiles = _batch_profiles(body, queries)
    result = {
        **profiles,
        "items": [
            {
                "position_id": item["position_id"],
                "graph_version": item["graph_version"],
                "evidence_summary": item["evidence_summary"],
            }
            for item in profiles["items"]
        ],
    }
    cached = _etag_response(request, response, result)
    return cached or ok(result, request.state.trace_id)


@router.post("/api/v1/skill-relations/batch")
def skill_relations_batch(
    body: SkillRelationBatchInput,
    request: Request,
    response: Response,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    result = queries.skill_relations_batch(tuple(body.skill_ids))
    cached = _etag_response(request, response, result)
    return cached or ok(result, request.state.trace_id)


@router.post("/api/v1/positions/{position_id}/graph/drafts")
def open_graph_draft(position_id: str, body: DraftCreate, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    run = handlers.open_graph_draft.execute(position_id, body.base_version_id)
    return ok(asdict(run), request.state.trace_id)
@router.get("/api/v1/graph/build-runs/{run_id}/graph")
def draft_graph(run_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_graph_editor)):
    result = queries.draft_graph(run_id)
    return ok(result if result is not None else missing("editable draft"), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/visualization")
def visualization(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.visualization(position_id), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/skills")
def position_skills(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.graph(position_id).get("skill_relations", []), request.state.trace_id)
@router.get(
    "/api/v1/positions/{position_id}/relations",
    response_model=RelationPageEnvelope,
)
def position_relations(
    position_id: str,
    request: Request,
    version_id: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    skill_id: str | None = None,
    category_code: str | None = None,
    importance_level: str | None = None,
    modality: str | None = None,
    min_weight: float | None = Query(None, ge=0, le=1),
    min_confidence: float | None = Query(None, ge=0, le=1),
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
):
    result = queries.relations(
        position_id,
        version_id=version_id,
        page=page,
        page_size=page_size,
        skill_id=skill_id,
        category_code=category_code,
        importance_level=importance_level,
        modality=modality,
        min_weight=min_weight,
        min_confidence=min_confidence,
    )
    return ok(
        result if result is not None else missing("graph version"),
        request.state.trace_id,
    )
@router.get("/api/v1/positions/{position_id}/requirements")
def requirements(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.graph(position_id).get("requirement_profile", []), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/responsibilities")
def responsibilities(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.graph(position_id).get("responsibilities", []), request.state.trace_id)


@router.get("/api/v1/relations/{relation_id}/evidence")
def relation_evidence(relation_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    result=queries.relation_evidence(relation_id); return ok(result if result is not None else missing("relation"), request.state.trace_id)
@router.get(
    "/api/v1/relations/{relation_id}/explanation",
    response_model=RelationExplanationEnvelope,
)
def relation_explanation(
    relation_id: int,
    request: Request,
    version_id: int | None = Query(None, ge=1),
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
):
    result = queries.relation_explanation(relation_id, version_id)
    return ok(
        result if result is not None else missing("relation explanation"),
        request.state.trace_id,
    )
@router.get("/api/v1/requirements/{aggregate_id}/evidence")
def requirement_evidence(aggregate_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    result=queries.aggregate_evidence(aggregate_id, "requirement"); return ok(result if result is not None else missing("requirement aggregate"), request.state.trace_id)
@router.get("/api/v1/tasks/{aggregate_id}/evidence")
def task_evidence(aggregate_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    result=queries.aggregate_evidence(aggregate_id, "task"); return ok(result if result is not None else missing("task aggregate"), request.state.trace_id)
@router.get("/api/v1/company_facts/{aggregate_id}/evidence")
def company_fact_evidence(aggregate_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    result=queries.aggregate_evidence(aggregate_id, "requirement"); return ok(result if result is not None else missing("company fact aggregate"), request.state.trace_id)
@router.get("/api/v1/employment_facts/{aggregate_id}/evidence")
def employment_fact_evidence(aggregate_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)):
    result=queries.aggregate_evidence(aggregate_id, "requirement"); return ok(result if result is not None else missing("employment fact aggregate"), request.state.trace_id)
@router.get("/api/v1/jds/{document_id}/evidence")
def jd_evidence(document_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service), user: Any=Depends(require_internal_reader)): return ok(queries.document_evidence(document_id), request.state.trace_id)


@router.get(
    "/api/v1/review-tasks",
    response_model=ReviewTaskListEnvelope,
    responses={
        200: {
            "headers": {
                "X-Total-Count": {"schema": {"type": "integer"}},
                "X-Page": {"schema": {"type": "integer"}},
                "X-Page-Size": {"schema": {"type": "integer"}},
            }
        }
    },
)
def reviews(
    request: Request,
    response: Response,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    risk_level: Literal["low", "medium", "high"] | None = None,
    task_kind: str | None = None,
    build_run_id: int | None = Query(None, ge=1),
    queries: KnowledgeGraphQueryService=Depends(get_query_service),
    user: Any=Depends(require_reviewer),
):
    raw_statuses = (
        [value.strip() for value in status.split(",") if value.strip()]
        if status
        else []
    )
    statuses = tuple(raw_statuses) if raw_statuses else None
    result = queries.review_tasks(
        page=page, page_size=page_size,
        status=statuses[0] if statuses and len(statuses) == 1 else None,
        statuses=statuses,
        risk_level=risk_level, task_kind=task_kind,
        build_run_id=build_run_id,
    )
    response.headers["X-Total-Count"] = str(result["total"])
    response.headers["X-Page"] = str(result["page"])
    response.headers["X-Page-Size"] = str(result["page_size"])
    return ok(result["items"], request.state.trace_id)


@router.get("/api/v1/review-tasks/{task_id}")
def review_task(
    task_id: int,
    request: Request,
    queries: KnowledgeGraphQueryService=Depends(get_query_service),
    user: Any=Depends(require_reviewer),
):
    result = queries.review_task(task_id)
    return ok(
        result if result is not None else missing("review task"),
        request.state.trace_id,
    )


@router.post("/api/v1/review-tasks/batch")
def batch_reviews(
    body: ReviewBatchOperationV1,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_reviewer),
):
    statuses = handlers.batch_review_tasks.execute(
        tuple(body.task_ids), body.action, user.id, request.state.trace_id, body.reason
    )
    result = ReviewBatchResultV1(
        action=body.action,
        task_ids=body.task_ids,
        statuses={str(key): value for key, value in statuses.items()},
    )
    return ok(result.model_dump(), request.state.trace_id)
@router.post("/api/v1/graph/build-runs/{build_run_id}/auto-review")
def auto_review_build(
    build_run_id: int,
    body: AutoReviewBuildInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_reviewer),
):
    result = handlers.auto_review_build.execute(
        build_run_id,
        body.policy_version,
        user.id,
        request.state.trace_id,
        body.reason,
    )
    return ok(asdict(result), request.state.trace_id)
@router.post("/api/v1/review-tasks")
def create_review_task(body: ReviewTaskCreate, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.create_review_task.execute(
        ReviewTaskDraft(body.object_type, body.object_id, body.build_run_id, body.payload),
        user.id,
        request.state.trace_id,
    )
    return ok({"id": result.task_id, "status": result.status}, request.state.trace_id)


def _review(task_id, action, body, request, handlers, user):
    use_case = handlers.claim_review_task if action == "claim" else handlers.complete_review_task
    result = (
        use_case.execute(task_id, user.id, request.state.trace_id, body.reason)
        if action == "claim"
        else use_case.execute(
            task_id,
            ReviewCompletion(action, body.reason, body.payload or {}),
            user.id,
            request.state.trace_id,
        )
    )
    feedback = {
        "claim": "review task claimed",
        "modify": "review changes saved",
        "approve": "review task approved",
        "reject": "review task rejected",
    }[action]
    return ok({
        "id": result.task_id,
        "status": result.status,
        "action": action,
        "feedback": feedback,
        "assignee_id": user.id if result.status != "pending" else None,
        "allowed_actions": list(allowed_review_actions(result.status)),
    }, request.state.trace_id)


@router.post(
    "/api/v1/review-tasks/{task_id}/claim", response_model=ReviewActionEnvelope
)
def claim(task_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _review(task_id, "claim", body, request, handlers, user)
@router.post(
    "/api/v1/review-tasks/{task_id}/approve", response_model=ReviewActionEnvelope
)
def approve(task_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _review(task_id, "approve", body, request, handlers, user)
@router.post(
    "/api/v1/review-tasks/{task_id}/reject", response_model=ReviewActionEnvelope
)
def reject(task_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _review(task_id, "reject", body, request, handlers, user)
@router.post(
    "/api/v1/review-tasks/{task_id}/modify", response_model=ReviewActionEnvelope
)
def modify(task_id: int, body: ReviewModifyAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_reviewer)): return _review(task_id, "modify", body, request, handlers, user)


@router.post("/api/v1/relations/{relation_id}/modify")
def modify_relation(relation_id: int, body: RelationModify, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.modify_relation.execute(
        relation_id,
        RelationModification(
            body.build_run_id,
            body.position_id,
            body.expected_revision,
            body.reason,
            body.weight,
            body.confidence,
            body.importance_level,
            frozenset(body.model_fields_set),
        ),
        user.id,
        request.state.trace_id,
    )
    return ok(asdict(result), request.state.trace_id)
@router.post("/api/v1/graph/build-runs/{run_id}/publish")
def publish(run_id: int, body: PublishInput, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_publisher)):
    try: version=handlers.publish_graph.execute(PublishGraphCommand(
        run_id, user.id, request.state.trace_id, body.reason,
        body.version_name, body.version_number, body.release_notes,
    ))
    except PublishGateError as exc:
        raise HTTPException(
            409,
            {"message": str(exc), "errors": publish_gate_errors(exc.errors)},
        ) from exc
    return ok(asdict(version), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions")
def versions(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)): return ok(queries.versions(position_id), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions/{version_id:int}")
def version(position_id: str, version_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)):
    result = queries.version(position_id, version_id)
    return ok(result if result is not None else missing("version"), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions/diff")
def diff(position_id: str, from_version_id: int, to_version_id: int, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)):
    result=queries.version_diff(position_id, from_version_id, to_version_id); return ok(result if result is not None else missing("version"), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions/evolution-events")
def evolution_events(position_id: str, from_version_id: int, to_version_id: int, request: Request, event_type: str | None = None, queries: KnowledgeGraphQueryService=Depends(get_query_service)):
    result = queries.evolution_events(position_id, from_version_id, to_version_id, event_type)
    return ok(result if result is not None else missing("version"), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions/evolution-events/{event_id}")
def evolution_event(position_id: str, event_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)):
    result = queries.evolution_event(position_id, event_id)
    return ok(result if result is not None else missing("evolution event"), request.state.trace_id)
@router.get("/api/v1/positions/{position_id}/graph/versions/capability-evolution")
def capability_evolution(position_id: str, request: Request, queries: KnowledgeGraphQueryService=Depends(get_query_service)):
    return ok(queries.capability_evolution(position_id), request.state.trace_id)
@router.post("/api/v1/positions/{position_id}/graph/versions/{version_id}/rollback")
def rollback(position_id: str, version_id: int, body: ReviewAction, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_publisher)):
    version=handlers.rollback_graph.execute(RollbackGraphCommand(
        position_id, version_id, user.id, request.state.trace_id, body.reason,
    ))
    return ok(asdict(version), request.state.trace_id)


@router.put("/api/v1/algorithm-config")
def update_algorithm_config(body: AlgorithmConfigInput, request: Request, handlers: ApplicationHandlers=Depends(get_application_handlers), user: Any=Depends(require_graph_editor)):
    result = handlers.update_algorithm_config.execute(
        AlgorithmConfigUpdate(body.version, body.payload, body.active),
        user.id,
        request.state.trace_id,
    )
    return ok({"id": result.config_id, "version": result.version}, request.state.trace_id)


@router.get("/api/v1/innovation/build-runs/{run_id}/watermark")
def build_watermark(
    run_id: int,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    result = queries.build_watermark(run_id)
    return ok(result if result is not None else missing("build watermark"), request.state.trace_id)


@router.post("/api/v1/innovation/watermarks/compare")
def compare_watermarks(
    body: WatermarkComparisonInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_internal_reader),
):
    result = handlers.compare_watermarks.execute(
        CompareWatermarksCommand(
            body.left_build_run_id,
            body.right_build_run_id,
            ComparabilityContext(
                body.approved_catalog_crosswalk,
                body.policy_replay_completed,
                body.minimum_input_coverage,
            ),
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/innovation/graph-versions/{version_id}/claims")
def relation_claims(
    version_id: int,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    return ok(queries.relation_claims(version_id), request.state.trace_id)


@router.post("/api/v1/innovation/mapping-candidates")
def create_mapping_candidate(
    body: MappingCandidateInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_graph_editor),
):
    candidate = MappingCandidate(
        body.candidate_id,
        body.source_expression,
        body.proposed_skill_id,
        MappingCandidateSignals(**body.signals.model_dump()),
        body.model_version,
        body.index_version,
        body.mapping_policy_version,
        tuple(
            MappingAffectedContext(**item.model_dump())
            for item in body.affected_contexts
        ),
    )
    result = handlers.create_mapping_candidate.execute(
        CreateMappingCandidateCommand(
            candidate,
            MappingPriorityWeights(**body.weights.model_dump()),
            user.id,
            request.state.trace_id,
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/innovation/mapping-candidates")
def mapping_candidates(
    request: Request,
    status: str | None = None,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_reviewer),
):
    return ok(queries.mapping_candidates(status), request.state.trace_id)


@router.get("/api/v1/change-impact")
def change_impact(
    request: Request,
    entity_type: Literal["skill_mapping"],
    entity_id: str = Query(min_length=1),
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_reviewer),
):
    result = queries.change_impact(entity_type, entity_id)
    return ok(result if result is not None else missing("change impact"), request.state.trace_id)


@router.post("/api/v1/innovation/mapping-candidates/{candidate_id}/impact-preview")
def mapping_impact_preview(
    candidate_id: str,
    body: MappingReviewInput,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_reviewer),
):
    result = queries.change_impact("skill_mapping", candidate_id)
    if result is None:
        return ok(missing("change impact"), request.state.trace_id)
    return ok(
        {
            **result,
            "proposed_change": body.model_dump(),
            "preview_only": True,
        },
        request.state.trace_id,
    )


@router.get("/api/v1/dependency-events")
def dependency_events(
    request: Request,
    entity_type: Literal["skill_mapping"],
    entity_id: str = Query(min_length=1),
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_reviewer),
):
    return ok(
        queries.dependency_events(entity_type, entity_id), request.state.trace_id
    )


@router.post("/api/v1/dependency-references")
def register_dependency_reference(
    body: DependencyReferenceInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_internal_reader),
):
    reference_id = handlers.dependency_references.execute(**body.model_dump())
    return ok({"dependency_reference_id": reference_id}, request.state.trace_id)


@router.post("/api/v1/innovation/mapping-candidates/{candidate_id}/review")
def review_mapping_candidate(
    candidate_id: str,
    body: MappingReviewInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_reviewer),
):
    result = handlers.review_mapping_candidate.execute(
        ReviewMappingCandidateCommand(
            candidate_id,
            body.expected_revision,
            body.decision,
            user.id,
            body.reason,
            body.policy_version,
            datetime.now(timezone.utc).isoformat(),
            body.effective_scope,
            body.replacement_candidate_id,
            request.state.trace_id,
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.post("/api/v1/innovation/build-runs/{run_id}/dependencies/analyze")
def analyze_dependencies(
    run_id: int,
    body: DependencyPolicyInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_graph_editor),
):
    result = handlers.analyze_dependencies.execute(
        AnalyzeDependenciesCommand(
            run_id,
            DependencyPolicy(**body.model_dump()),
            user.id,
            request.state.trace_id,
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/innovation/build-runs/{run_id}/dependencies")
def dependency_analysis(
    run_id: int,
    request: Request,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    result = queries.dependency_analysis(run_id)
    return ok(result if result is not None else missing("dependency analysis"), request.state.trace_id)


@router.post("/api/v1/innovation/dependency-candidates/{candidate_id}/review")
def review_dependency_candidate(
    candidate_id: int,
    body: DependencyReviewInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_reviewer),
):
    result = handlers.review_dependency_candidate.execute(
        ReviewDependencyCandidateCommand(
            candidate_id,
            body.decision,
            user.id,
            body.reason,
            body.policy_version,
            datetime.now(timezone.utc).isoformat(),
            request.state.trace_id,
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.post("/api/v1/innovation/graph-versions/{version_id}/projection/rebuild")
def rebuild_projection(
    version_id: int,
    body: ProjectionRebuildInput,
    request: Request,
    handlers: ApplicationHandlers = Depends(get_application_handlers),
    user: Any = Depends(require_graph_editor),
):
    result = handlers.rebuild_projection.execute(
        RebuildProjectionCommand(
            version_id,
            body.projection_version,
            user.id,
            request.state.trace_id,
        )
    )
    return ok(asdict(result), request.state.trace_id)


@router.get("/api/v1/innovation/graph-versions/{version_id}/projection")
def graph_projection(
    version_id: int,
    request: Request,
    projection_version: str | None = None,
    queries: KnowledgeGraphQueryService = Depends(get_query_service),
    user: Any = Depends(require_internal_reader),
):
    result = queries.graph_projection(version_id, projection_version)
    return ok(result if result is not None else missing("graph projection"), request.state.trace_id)


@router.get("/api/v1/schemas/jd-extraction-v2.json")
def extraction_schema(): return JDExtractionResult.model_json_schema()
@router.get("/api/v1/schemas/jd-normalized-v2.json")
def normalization_schema(): return JDNormalizedResult.model_json_schema()
