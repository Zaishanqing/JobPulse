from typing import Literal

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.skills import get_skill_use_cases
from app.api.skill_mapping import (
    alias_data,
    catalog_draft_preview_data,
    catalog_version_data,
    candidate_data,
    classification_data,
    downstream_skill_projection_data,
    merge_preview_data,
    normalization_data,
    normalization_suggestion_data,
    renormalization_summary_data,
    skill_data,
    taxonomy_node_data,
)
from app.contexts.catalog import (
    ManageSkills,
    NormalizationCandidateNotFound,
    SkillAliasNotFound,
    SkillClassificationNotFound,
    SkillCatalogVersionNotFound,
    SkillNotFound,
    SkillTaxonomyNodeNotFound,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.skills import SkillCatalogConflict, SkillRuleViolation
from app.contexts.catalog import (
    SkillChanges,
    SkillDraft,
    SkillTaxonomyNodeChanges,
    SkillTaxonomyNodeDraft,
)
from app.schemas.skill import (
    CandidateConfirmRequest,
    CandidateCreateNewRequest,
    CandidateMapExistingRequest,
    CandidateReasonRequest,
    SkillAliasCreate,
    SkillClassificationCreate,
    SkillCreate,
    SkillMergeRequest,
    SkillNormalizeBatchRequest,
    SkillNormalizeRequest,
    SkillNormalizationSuggestionsRequest,
    SkillTaxonomyNodeCreate,
    SkillTaxonomyNodeUpdate,
    SkillUpdate,
)


router = APIRouter(tags=["skills"])


def _raise(exc: Exception) -> None:
    if isinstance(
        exc,
        (
            SkillNotFound,
            SkillAliasNotFound,
            SkillTaxonomyNodeNotFound,
            SkillClassificationNotFound,
            SkillCatalogVersionNotFound,
            NormalizationCandidateNotFound,
        ),
    ):
        code = 404
    elif isinstance(exc, SkillCatalogConflict):
        code = 400
    elif isinstance(exc, SkillRuleViolation):
        code = 403
    else:
        code = 400
    raise HTTPException(status_code=code, detail=str(exc)) from exc


@router.get("/skill-categories/tree")
def get_skill_category_tree(use_cases: ManageSkills = Depends(get_skill_use_cases)):
    return success_response(data=[
        {"category": category, "skills": [skill_data(item) for item in skills]}
        for category, skills in use_cases.category_tree()
    ])


@router.get("/skills/domain-tree")
def get_skill_domain_tree(use_cases: ManageSkills = Depends(get_skill_use_cases)):
    return success_response(data=[
        {"category": category, "skills": [skill_data(item) for item in skills]}
        for category, skills in use_cases.domain_tree()
    ])


@router.post("/skill-taxonomy/nodes")
def create_skill_taxonomy_node_api(
    payload: SkillTaxonomyNodeCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.create_taxonomy_node(
            actor,
            SkillTaxonomyNodeDraft(**payload.model_dump()),
        )
    except (SkillRuleViolation, SkillCatalogConflict, SkillTaxonomyNodeNotFound) as exc:
        _raise(exc)
    return success_response(data=taxonomy_node_data(item))


@router.get("/skill-taxonomy/nodes")
def list_skill_taxonomy_nodes_api(
    facet: str | None = None,
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        items = use_cases.list_taxonomy_nodes(facet)
    except SkillRuleViolation as exc:
        _raise(exc)
    return success_response(data=[taxonomy_node_data(item) for item in items])


@router.put("/skill-taxonomy/nodes/{node_id}")
def update_skill_taxonomy_node_api(
    node_id: str,
    payload: SkillTaxonomyNodeUpdate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    raw = payload.model_dump(exclude_unset=True)
    try:
        item = use_cases.update_taxonomy_node(
            actor,
            node_id,
            SkillTaxonomyNodeChanges(frozenset(raw), **raw),
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillTaxonomyNodeNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=taxonomy_node_data(item))


@router.post("/skills/{skill_id}/classifications")
def classify_skill_api(
    skill_id: str,
    payload: SkillClassificationCreate,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.classify(
            actor,
            skill_id,
            payload.taxonomy_node_id,
            payload.is_primary,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillNotFound,
        SkillTaxonomyNodeNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=classification_data(item))


@router.get("/skills/{skill_id}/classifications")
def list_skill_classifications_api(
    skill_id: str,
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        items = use_cases.list_classifications(skill_id)
    except SkillNotFound as exc:
        _raise(exc)
    return success_response(data=[classification_data(item) for item in items])


@router.delete(
    "/skills/{skill_id}/classifications/{classification_id}"
)
def delete_skill_classification_api(
    skill_id: str,
    classification_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        use_cases.remove_classification(
            actor,
            skill_id,
            classification_id,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillNotFound,
        SkillClassificationNotFound,
    ) as exc:
        _raise(exc)
    return success_response(
        data={"classification_id": classification_id, "deleted": True}
    )


@router.post("/skills")
def create_skill_api(payload: SkillCreate, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        item = use_cases.create(actor, SkillDraft(payload.skill_name, payload.category, payload.description, payload.parent_skill_id), tuple(payload.aliases))
    except (SkillRuleViolation, SkillCatalogConflict) as exc:
        _raise(exc)
    return success_response(data=skill_data(item))


@router.get("/skills")
def list_skills_api(use_cases: ManageSkills = Depends(get_skill_use_cases)):
    return success_response(data=[skill_data(item) for item in use_cases.list()])


@router.post("/skills/normalize")
def normalize_skill_api(payload: SkillNormalizeRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        result = use_cases.normalize(
            actor,
            payload.raw_skill,
            payload.context,
            payload.source_type,
            payload.evidence,
        )
    except SkillRuleViolation as exc:
        _raise(exc)
    return success_response(data=normalization_data(result))


@router.post("/skills/normalization-suggestions")
def suggest_skill_normalizations_api(
    payload: SkillNormalizationSuggestionsRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        result = use_cases.suggest_normalizations(
            actor,
            payload.raw_skill,
            payload.context,
            payload.top_k,
        )
    except SkillRuleViolation as exc:
        _raise(exc)
    return success_response(
        data=[normalization_suggestion_data(item) for item in result]
    )


@router.post("/skills/normalize-batch")
def normalize_skill_batch_api(payload: SkillNormalizeBatchRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        result = use_cases.normalize_batch(
            actor,
            tuple(
                (item.raw_skill, item.context, item.source_type, item.evidence)
                for item in payload.items
            ),
        )
    except SkillRuleViolation as exc:
        _raise(exc)
    return success_response(data=[normalization_data(item) for item in result])


@router.get("/skills/normalize-candidates")
def list_normalization_candidates_api(
    status: Literal[
        "pending",
        "mapped_existing",
        "created_new",
        "excluded_non_skill",
        "deferred",
    ]
    | None = None,
    keyword: str | None = None,
    source_type: Literal["jd", "cv", "manual", "unknown"] | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        items = use_cases.list_candidates(actor, status, keyword, source_type)
    except SkillRuleViolation as exc:
        _raise(exc)
    return success_response(data=[candidate_data(item) for item in items])


@router.post("/skills/normalize-candidates/{candidate_id}/confirm")
def confirm_normalization_candidate_api(candidate_id: str, payload: CandidateConfirmRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        item = use_cases.confirm_candidate(
            actor,
            candidate_id,
            payload.skill_id,
            payload.decision_reason,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillNotFound,
        NormalizationCandidateNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data={"candidate_id": item.candidate_id, "status": item.status})


@router.post("/skills/normalize-candidates/{candidate_id}/reject")
def reject_normalization_candidate_api(
    candidate_id: str,
    payload: CandidateReasonRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.reject_candidate(
            actor,
            candidate_id,
            payload.decision_reason,
        )
    except (SkillRuleViolation, NormalizationCandidateNotFound) as exc:
        _raise(exc)
    return success_response(data={"candidate_id": item.candidate_id, "status": item.status})


@router.post("/skills/normalize-candidates/{candidate_id}/map-existing")
def map_existing_normalization_candidate_api(
    candidate_id: str,
    payload: CandidateMapExistingRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.map_existing_candidate(
            actor,
            candidate_id,
            payload.skill_id,
            payload.add_alias,
            payload.decision_reason,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillNotFound,
        NormalizationCandidateNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=candidate_data(item))


@router.post("/skills/normalize-candidates/{candidate_id}/create-new")
def create_new_normalization_candidate_api(
    candidate_id: str,
    payload: CandidateCreateNewRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.create_new_candidate(
            actor,
            candidate_id,
            SkillDraft(
                payload.skill_name,
                payload.category,
                payload.description,
                None,
            ),
            payload.concept_class_id,
            payload.technology_kind_id,
            payload.domain_id,
            payload.add_alias,
            payload.decision_reason,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        SkillTaxonomyNodeNotFound,
        NormalizationCandidateNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=candidate_data(item))


@router.post("/skills/normalize-candidates/{candidate_id}/exclude-non-skill")
def exclude_non_skill_normalization_candidate_api(
    candidate_id: str,
    payload: CandidateReasonRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.exclude_non_skill_candidate(
            actor,
            candidate_id,
            payload.decision_reason,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        NormalizationCandidateNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=candidate_data(item))


@router.post("/skills/normalize-candidates/{candidate_id}/defer")
def defer_normalization_candidate_api(
    candidate_id: str,
    payload: CandidateReasonRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.defer_candidate(
            actor,
            candidate_id,
            payload.decision_reason,
        )
    except (
        SkillRuleViolation,
        SkillCatalogConflict,
        NormalizationCandidateNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=candidate_data(item))


@router.post("/skills/merge")
def merge_skills_api(payload: SkillMergeRequest, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        item = use_cases.merge(actor, payload.source_skill_id, payload.target_skill_id)
    except (SkillRuleViolation, SkillCatalogConflict, SkillNotFound) as exc:
        _raise(exc)
    return success_response(data={
        "source_skill_id": item.source_skill_id,
        "target_skill_id": item.target_skill_id,
        "target_skill_name": item.target_skill_name,
        "source_status": item.source_status,
    })


@router.post("/skills/merge/preview")
def preview_skill_merge_api(
    payload: SkillMergeRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.preview_merge(
            actor,
            payload.source_skill_id,
            payload.target_skill_id,
        )
    except (SkillRuleViolation, SkillCatalogConflict, SkillNotFound) as exc:
        _raise(exc)
    return success_response(data=merge_preview_data(item))


@router.get("/skills/catalog/draft")
def preview_skill_catalog_draft_api(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.preview_catalog_draft(actor)
    except (SkillRuleViolation, SkillCatalogConflict) as exc:
        _raise(exc)
    return success_response(data=catalog_draft_preview_data(item))


@router.post("/skills/catalog/publish")
def publish_skill_catalog_api(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.publish_catalog(actor)
    except (SkillRuleViolation, SkillCatalogConflict) as exc:
        _raise(exc)
    return success_response(data=catalog_version_data(item))


@router.get("/skills/catalog/versions/latest")
def get_latest_skill_catalog_api(
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.latest_published_catalog()
    except SkillCatalogVersionNotFound as exc:
        _raise(exc)
    return success_response(data=catalog_version_data(item))


@router.post("/skills/normalize-candidates/re-normalize")
def renormalize_skill_candidates_api(
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.renormalize_candidates(actor)
    except (SkillRuleViolation, SkillCatalogVersionNotFound) as exc:
        _raise(exc)
    return success_response(data=renormalization_summary_data(item))


@router.get("/skills/catalog/downstream")
def get_downstream_skill_projection_api(
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.downstream_skill_projection()
    except SkillCatalogVersionNotFound as exc:
        _raise(exc)
    return success_response(data=downstream_skill_projection_data(item))


@router.get("/skills/catalog/versions/{catalog_version}")
def get_skill_catalog_version_api(
    catalog_version: str,
    use_cases: ManageSkills = Depends(get_skill_use_cases),
):
    try:
        item = use_cases.get_published_catalog(catalog_version)
    except SkillCatalogVersionNotFound as exc:
        _raise(exc)
    return success_response(data=catalog_version_data(item))


@router.get("/skills/{skill_id}")
def get_skill_api(skill_id: str, use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        item = use_cases.get(skill_id)
    except SkillNotFound as exc:
        _raise(exc)
    return success_response(data=skill_data(item))


@router.put("/skills/{skill_id}")
def update_skill_api(skill_id: str, payload: SkillUpdate, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    raw = payload.model_dump(exclude_unset=True)
    try:
        item = use_cases.update(actor, skill_id, SkillChanges(frozenset(raw), **raw))
    except (SkillRuleViolation, SkillCatalogConflict, SkillNotFound) as exc:
        _raise(exc)
    return success_response(data=skill_data(item))


@router.delete("/skills/{skill_id}")
def delete_skill_api(skill_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        use_cases.delete(actor, skill_id)
    except (SkillRuleViolation, SkillNotFound) as exc:
        _raise(exc)
    return success_response(data={"skill_id": skill_id, "deleted": True})


@router.post("/skills/{skill_id}/aliases")
def add_skill_alias_api(skill_id: str, payload: SkillAliasCreate, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        item = use_cases.add_alias(actor, skill_id, payload.alias)
    except (SkillRuleViolation, SkillCatalogConflict, SkillNotFound) as exc:
        _raise(exc)
    return success_response(data=alias_data(item))


@router.get("/skills/{skill_id}/aliases")
def list_skill_aliases_api(skill_id: str, use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        items = use_cases.list_aliases(skill_id)
    except SkillNotFound as exc:
        _raise(exc)
    return success_response(data=[alias_data(item) for item in items])


@router.delete("/skills/{skill_id}/aliases/{alias_id}")
def delete_skill_alias_api(skill_id: str, alias_id: str, actor: AccountActor = Depends(get_account_actor), use_cases: ManageSkills = Depends(get_skill_use_cases)):
    try:
        use_cases.delete_alias(actor, skill_id, alias_id)
    except (SkillRuleViolation, SkillNotFound, SkillAliasNotFound) as exc:
        _raise(exc)
    return success_response(data={"alias_id": alias_id, "deleted": True})
