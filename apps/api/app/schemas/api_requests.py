from typing import Annotated

from pydantic import BaseModel, Field, JsonValue, RootModel

from app.domain.input_limits import MAX_BATCH_SIZE


BatchStringList = Annotated[list[str], Field(max_length=MAX_BATCH_SIZE)]


class EmbeddingBatchRequest(RootModel[BatchStringList]):
    pass


class VectorSearchRequest(RootModel[dict[str, JsonValue]]):
    pass


class SimilarityRequest(RootModel[dict[str, JsonValue]]):
    pass


class GerminationScoreConfigRequest(RootModel[dict[str, JsonValue]]):
    pass


class EnterpriseCandidateMatchRequest(BaseModel):
    submission_ids: list[str] = Field(max_length=MAX_BATCH_SIZE)


class CandidateSubmissionRequest(BaseModel):
    resume_id: str = Field(min_length=1)


class EnterpriseSkillClassificationRequest(RootModel[dict[str, JsonValue]]):
    pass


class ClusterEvaluationRequest(RootModel[dict[str, JsonValue]]):
    pass


class FeedbackCreateRequest(RootModel[dict[str, JsonValue]]):
    pass


class FeedbackUpdateRequest(RootModel[dict[str, JsonValue]]):
    pass


class OCRResultUpdateRequest(BaseModel):
    text: str


class JDIdBatchRequest(RootModel[BatchStringList]):
    pass


class JDSkillAbnormalRequest(BaseModel):
    abnormal: bool = True
    reason: str | None = None
