from pydantic import BaseModel, ConfigDict

from jobgraph_contracts.evidence import Evidence, EvidenceAlignment


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


__all__ = [
    "Evidence",
    "EvidenceAlignment",
    "StrictModel",
]
