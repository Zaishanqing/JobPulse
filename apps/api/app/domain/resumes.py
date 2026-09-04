from dataclasses import dataclass


PERSONAL_ROLE = "personal_user"
RESUME_READ_ROLES = frozenset({"admin", "developer", "reviewer"})
RESUME_WRITE_ROLES = frozenset({"admin", "developer"})
SKILL_KEYWORDS = {
    "Python": "skill_python",
    "Java": "skill_java",
    "RAG": "skill_rag",
    "FastAPI": "skill_fastapi",
    "Neo4j": "skill_neo4j",
    "Docker": "skill_docker",
}


class ResumeRuleViolation(ValueError):
    pass


@dataclass(frozen=True)
class SkillCandidate:
    raw_skill: str
    normalized_skill_id: str
    confidence: float
    evidence: str


def require_personal_role(role: str) -> None:
    if role != PERSONAL_ROLE:
        raise ResumeRuleViolation("Only personal users can manage resumes")


def extract_skill_candidates(raw_text: str) -> tuple[SkillCandidate, ...]:
    lowered = raw_text.lower()
    return tuple(
        SkillCandidate(name, skill_id, 0.93, "简历文本")
        for name, skill_id in SKILL_KEYWORDS.items()
        if name.lower() in lowered
    )
