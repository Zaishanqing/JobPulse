from pydantic import BaseModel, ConfigDict


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
