from dataclasses import dataclass
import unicodedata
import re


SKILL_ADMIN_ROLES = frozenset({"admin", "developer", "reviewer"})
SKILL_TAXONOMY_FACETS = frozenset(
    {"concept_class", "technology_kind", "domain"}
)
SKILL_TAXONOMY_STATUSES = frozenset({"active", "inactive"})
_TAXONOMY_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class SkillRuleViolation(ValueError):
    pass


class SkillCatalogConflict(SkillRuleViolation):
    pass


def require_skill_admin(role: str) -> None:
    if role not in SKILL_ADMIN_ROLES:
        raise SkillRuleViolation("No permission to manage skills")


def validate_taxonomy_node(
    facet: str,
    code: str,
    name_zh: str,
    status: str,
) -> None:
    if facet not in SKILL_TAXONOMY_FACETS:
        raise SkillRuleViolation("Unsupported skill taxonomy facet")
    if not _TAXONOMY_CODE_PATTERN.fullmatch(code):
        raise SkillRuleViolation(
            "Taxonomy code must use lowercase snake_case"
        )
    if not name_zh.strip():
        raise SkillRuleViolation("Taxonomy Chinese name is required")
    if status not in SKILL_TAXONOMY_STATUSES:
        raise SkillRuleViolation("Unsupported skill taxonomy status")


def validate_taxonomy_facet(facet: str) -> None:
    if facet not in SKILL_TAXONOMY_FACETS:
        raise SkillRuleViolation("Unsupported skill taxonomy facet")


@dataclass(frozen=True)
class NormalizationMatch:
    skill_id: str
    confidence: float


def clean_skill_expression(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def normalize_skill_expression(value: str) -> str:
    return clean_skill_expression(value).casefold()


def find_text_match(
    raw_skill: str,
    skills: tuple[tuple[str, str], ...],
    aliases: tuple[tuple[str, str], ...],
) -> NormalizationMatch | None:
    normalized_raw = normalize_skill_expression(raw_skill)
    for skill_id, name in skills:
        if normalize_skill_expression(name) == normalized_raw:
            return NormalizationMatch(skill_id, 1.0)
    for skill_id, alias in aliases:
        if normalize_skill_expression(alias) == normalized_raw:
            return NormalizationMatch(skill_id, 0.96)
    for skill_id, name in skills:
        normalized_name = normalize_skill_expression(name)
        if normalized_name in normalized_raw or normalized_raw in normalized_name:
            return NormalizationMatch(skill_id, 0.72)
    for skill_id, alias in aliases:
        normalized_alias = normalize_skill_expression(alias)
        if normalized_alias in normalized_raw or normalized_raw in normalized_alias:
            return NormalizationMatch(skill_id, 0.72)
    return None
