import pytest
from pydantic import ValidationError

from app.api.v1.matching_contracts import SkillRelationQueryRequest
from app.domain.input_limits import MAX_BATCH_SIZE
from app.schemas.api_requests import (
    EmbeddingBatchRequest,
    EnterpriseCandidateMatchRequest,
    JDIdBatchRequest,
)
from app.schemas.jd import JDBatchCreateRequest, JDParseBatchRequest
from app.schemas.review import ReviewTaskBatch
from app.schemas.skill import SkillNormalizeBatchRequest
from app.schemas.trend_delivery import TrendBatchQuery


def _jd_item() -> dict[str, str]:
    return {"title": "后端工程师", "raw_text": "负责服务端开发"}


def _skill_item() -> dict[str, str]:
    return {"raw_skill": "Python"}


@pytest.mark.parametrize(
    ("model", "valid_payload", "oversize_payload"),
    [
        (EmbeddingBatchRequest, ["jd-1"] * MAX_BATCH_SIZE, ["jd-1"] * (MAX_BATCH_SIZE + 1)),
        (JDIdBatchRequest, ["jd-1"] * MAX_BATCH_SIZE, ["jd-1"] * (MAX_BATCH_SIZE + 1)),
        (
            JDBatchCreateRequest,
            [_jd_item()] * MAX_BATCH_SIZE,
            [_jd_item()] * (MAX_BATCH_SIZE + 1),
        ),
    ],
)
def test_root_batch_models_enforce_max_size(model, valid_payload, oversize_payload):
    model.model_validate(valid_payload)
    with pytest.raises(ValidationError):
        model.model_validate(oversize_payload)


@pytest.mark.parametrize(
    ("model", "valid_payload", "oversize_payload"),
    [
        (
            EnterpriseCandidateMatchRequest,
            {"submission_ids": ["submission-1"] * MAX_BATCH_SIZE},
            {"submission_ids": ["submission-1"] * (MAX_BATCH_SIZE + 1)},
        ),
        (
            JDParseBatchRequest,
            {"jd_ids": ["jd-1"] * MAX_BATCH_SIZE, "extraction_mode": "rule"},
            {"jd_ids": ["jd-1"] * (MAX_BATCH_SIZE + 1), "extraction_mode": "rule"},
        ),
        (
            SkillNormalizeBatchRequest,
            {"items": [_skill_item()] * MAX_BATCH_SIZE},
            {"items": [_skill_item()] * (MAX_BATCH_SIZE + 1)},
        ),
        (
            ReviewTaskBatch,
            {
                "task_ids": ["task-1"] * MAX_BATCH_SIZE,
                "action": "claim",
                "reason": "批量处理",
            },
            {
                "task_ids": ["task-1"] * (MAX_BATCH_SIZE + 1),
                "action": "claim",
                "reason": "批量处理",
            },
        ),
        (
            TrendBatchQuery,
            {"ids": ["run-1"] * MAX_BATCH_SIZE},
            {"ids": ["run-1"] * (MAX_BATCH_SIZE + 1)},
        ),
        (
            SkillRelationQueryRequest,
            {
                "contract_version": "skill-relation-query.v1",
                "skill_ids": ["skill-1"] * MAX_BATCH_SIZE,
            },
            {
                "contract_version": "skill-relation-query.v1",
                "skill_ids": ["skill-1"] * (MAX_BATCH_SIZE + 1),
            },
        ),
    ],
)
def test_object_batch_models_enforce_max_size(model, valid_payload, oversize_payload):
    model.model_validate(valid_payload)
    with pytest.raises(ValidationError):
        model.model_validate(oversize_payload)
