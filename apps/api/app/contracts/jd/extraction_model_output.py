"""V2 model-output boundary: models may emit semantics and quote only."""

from typing import Literal

from pydantic import Field, JsonValue

from app.contracts.jd.evidence import StrictModel
from app.contracts.jd.extraction_v2 import Modality


class ModelEvidence(StrictModel):
    source_id: str
    quote: str


class ModelSourcedText(StrictModel):
    value: str
    evidence: ModelEvidence


class ModelResponsibility(StrictModel):
    kind: Literal["task"] = "task"
    modality: Modality = "unknown"
    action: str
    evidence: ModelEvidence


class ModelCandidateRequirement(StrictModel):
    kind: Literal["skill", "tool", "education", "experience", "certificate", "soft_skill", "other"]
    modality: Modality = "unknown"
    evidence: ModelEvidence
    payload: dict[str, JsonValue] = Field(default_factory=dict)


class ModelFact(StrictModel):
    scope: Literal["company", "employment"]
    kind: str
    value: str
    evidence: ModelEvidence


class ModelExtractionOutput(StrictModel):
    schema_version: Literal["v2"] = "v2"
    document_id: str
    job_title: ModelSourcedText | None = None
    responsibilities: list[ModelResponsibility] = Field(default_factory=list)
    requirements: list[ModelCandidateRequirement] = Field(default_factory=list)
    facts: list[ModelFact] = Field(default_factory=list)
