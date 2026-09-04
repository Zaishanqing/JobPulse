from __future__ import annotations

import dataclasses
from fastapi import APIRouter, Depends, HTTPException, status

from app.contexts.emerging_positions import (
    DefinitionVersionNotFound,
    DiscoveryEvidenceUnavailable,
    EmergingActor,
    EmergingClusterNotFound,
    EmergingPositionHandlers,
    EmergingPositionNotFound,
)
from app.core.response import success_response
from app.api.emerging_position_mapping import (
    assessment_data,
    definition_selection_data,
    definition_version_data,
    emerging_changes_from_data,
    emerging_record_data,
    generated_definition_data,
    standard_position_data,
    review_command_from_data,
)
from app.api.dependencies.accounts import get_authenticated_account
from app.domain.emerging_position import InvalidEmergingTransition, ReleaseGateRejected
from app.domain.values import thaw as _thaw
from app.api.dependencies.use_cases import get_emerging_position_handlers
from app.contexts.access import AccountRecord
from app.schemas.emerging_position import (
    EmergingPositionGenerationEnvelope,
    EmergingPositionReview,
    EmergingPositionUpdate,
)


router = APIRouter(prefix="/emerging-positions", tags=["emerging-positions"])


def _actor(user: AccountRecord) -> EmergingActor:
    return EmergingActor(actor_id=user.account_id, role=user.role)


def _translate_failure(exc: Exception) -> HTTPException:
    if isinstance(exc, (EmergingPositionNotFound, EmergingClusterNotFound, DefinitionVersionNotFound)):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ReleaseGateRejected):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "failures": list(exc.failures)},
        )
    if isinstance(exc, (InvalidEmergingTransition, DiscoveryEvidenceUnavailable)):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    raise exc


@router.post("/from-cluster/{cluster_id}")
def create_emerging_position_from_cluster(
    cluster_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.create.execute(cluster_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.get("")
def get_emerging_positions(
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    records = handlers.query.list(_actor(current_user))
    return success_response(data=[emerging_record_data(item) for item in records])


@router.post("/import-formal-experiment")
def import_formal_experiment_results(
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    record = handlers.import_formal.execute(_actor(current_user))
    return success_response(data=dataclasses.asdict(record))


@router.get("/{emerging_id}")
def get_emerging_position_detail(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.query.get(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.put("/{emerging_id}")
def edit_emerging_position(
    emerging_id: str,
    payload: EmergingPositionUpdate,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.update.execute(
            emerging_id,
            emerging_changes_from_data(payload.model_dump(exclude_unset=True)),
            _actor(current_user),
        )
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.delete("/{emerging_id}")
def delete_emerging_position_detail(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        handlers.delete.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data={"emerging_id": emerging_id, "deleted": True})


@router.post("/{emerging_id}/publish")
def publish_emerging_position_detail(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.publish.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.post("/{emerging_id}/submit-review")
def submit_emerging_position_review(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.submit_review.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.post("/{emerging_id}/review")
def review_emerging_position(
    emerging_id: str,
    payload: EmergingPositionReview,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.review.execute(
            emerging_id,
            review_command_from_data(payload.model_dump(exclude_unset=True)),
            _actor(current_user),
        )
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=emerging_record_data(data))


@router.get("/{emerging_id}/evidence")
def get_emerging_position_evidence(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        record = handlers.query.get(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(
        data={
            "emerging_id": emerging_id,
            "cluster_id": record.candidate.cluster_id,
            "field_evidence": _thaw(record.candidate.field_evidence),
            "evidence_jd_ids": list(record.candidate.evidence_jd_ids),
        }
    )


@router.post("/{emerging_id}/promote-to-position")
def promote_emerging_position_to_standard_position(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        standard = handlers.promote.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data={"emerging_id": emerging_id, "standard_position": standard_position_data(standard)})


@router.post("/{emerging_id}/germination-score")
def calculate_emerging_position_germination_score(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.assessment.execute(emerging_id, _actor(current_user), require_admin=True)
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=assessment_data(data))


@router.get("/{emerging_id}/germination-score")
def get_emerging_position_germination_score(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.assessment.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=assessment_data(data))


@router.post(
    "/{emerging_id}/generate-definition",
    response_model=EmergingPositionGenerationEnvelope,
)
def generate_emerging_position_definition(
    emerging_id: str,
    current_user: AccountRecord = Depends(get_authenticated_account),
    handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers),
):
    try:
        data = handlers.generate_definition.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=generated_definition_data(data))


@router.get("/{emerging_id}/definition-versions")
def list_definition_versions(emerging_id: str, current_user: AccountRecord = Depends(get_authenticated_account), handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers)):
    try:
        data = handlers.versions.execute(emerging_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=[definition_version_data(item) for item in data])


@router.post("/{emerging_id}/definition-versions/{version_id}/select")
def select_definition_version(emerging_id: str, version_id: str, current_user: AccountRecord = Depends(get_authenticated_account), handlers: EmergingPositionHandlers = Depends(get_emerging_position_handlers)):
    try:
        selected = handlers.select_version.execute(emerging_id, version_id, _actor(current_user))
    except Exception as exc:
        raise _translate_failure(exc) from exc
    return success_response(data=definition_selection_data(selected))
