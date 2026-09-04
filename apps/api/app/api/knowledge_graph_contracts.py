from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KnowledgeGraphBuildRequest(_StrictModel):
    window_start: str | None = None
    window_end: str | None = None
    minimum_effective_weight: float = Field(default=0.05, ge=0, le=1)
    minimum_valid_samples: int = Field(default=1, ge=1)


class KnowledgeGraphMappingUpdate(_StrictModel):
    knowledge_graph_id: str = Field(min_length=1, max_length=80)
