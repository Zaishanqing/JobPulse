from pydantic import BaseModel, ConfigDict, Field


class LearningPathCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str
    target_position_id: str | None = None
    time_budget_hours: float | None = Field(default=None, ge=0)
