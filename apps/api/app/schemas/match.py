from pydantic import BaseModel


class MatchTaskCreate(BaseModel):
    resume_id: str
    target_type: str = "standard_position"
    target_id: str
    use_enterprise_weights: bool = False
    generate_learning_path: bool = False


class MatchRankingCreate(BaseModel):
    resume_id: str
