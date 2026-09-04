from dataclasses import dataclass
from typing import Protocol

from app.domain.value_types import SerializedPayload


@dataclass(frozen=True)
class SemanticSimilarityJudgment:
    """Semantic comparison of a candidate position against a baseline position.

    ``similarity`` is a 0..1 graded semantic similarity; ``same_domain`` is the
    stronger binary signal of whether the two positions belong to the same
    technical direction/domain.
    """

    similarity: float
    same_domain: bool
    reason: str


class SemanticSimilarityProvider(Protocol):
    def judge(
        self,
        *,
        baseline_name: str,
        baseline_skills: str,
        baseline_responsibilities: str,
        candidate_name: str,
        candidate_skills: str,
        candidate_responsibilities: str,
    ) -> SemanticSimilarityJudgment: ...


class NormalizationMapProvider(Protocol):
    @property
    def version(self) -> str: ...

    def skill(self, source_name: str) -> SerializedPayload | None: ...

    def position(self, source_title: str) -> SerializedPayload | None: ...


class SkillIdGenerator(Protocol):
    def new_skill_id(self) -> str: ...
