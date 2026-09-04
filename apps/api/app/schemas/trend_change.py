from pydantic import BaseModel, ConfigDict


class CreateTrendChangeAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="allow")


class CreateTrendChangeFromHistoryRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
