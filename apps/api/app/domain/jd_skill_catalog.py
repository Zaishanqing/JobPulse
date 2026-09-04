from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CatalogResolutionStatus = Literal["resolved", "unresolved", "conflict"]


@dataclass(frozen=True)
class CatalogClassification:
    facet: str
    code: str
    is_primary: bool


@dataclass(frozen=True)
class CatalogSkill:
    skill_id: str
    canonical_name: str
    category_code: str | None
    catalog_code: str | None = None
    classifications: tuple[CatalogClassification, ...] = ()


@dataclass(frozen=True)
class CatalogAlias:
    skill_id: str
    alias: str


@dataclass(frozen=True)
class CatalogIdentity:
    catalog_version: str
    content_hash: str


@dataclass(frozen=True)
class CatalogResolution:
    status: CatalogResolutionStatus
    skill_id: str | None
    canonical_name: str | None
    category_code: str | None
    resolution_source: str
    error_code: str | None = None


class SkillCatalogGateError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _key(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def _snapshot_complete(skill: CatalogSkill) -> bool:
    if not skill.skill_id.strip() or not skill.canonical_name.strip():
        return False
    if not skill.classifications:
        return bool((skill.category_code or "").strip())
    concepts = [x for x in skill.classifications if x.facet == "concept_class"]
    kinds = [x for x in skill.classifications if x.facet == "technology_kind"]
    return bool(
        skill.catalog_code
        and len(concepts) == 1
        and concepts[0].is_primary
        and ((concepts[0].code == "technology") == (len(kinds) == 1))
    )


def resolve_catalog_skill(
    *,
    source_name: str,
    claimed_skill_id: str | None,
    claimed_canonical_name: str | None,
    skills: tuple[CatalogSkill, ...],
    aliases: tuple[CatalogAlias, ...],
) -> CatalogResolution:
    """Resolve an untrusted extraction skill against authoritative catalog facts."""

    by_id = {item.skill_id: item for item in skills}
    by_id.update(
        {
            item.catalog_code: item
            for item in skills
            if item.catalog_code is not None
        }
    )
    aliases_by_skill: dict[str, set[str]] = {}
    candidates_by_label: dict[str, set[str]] = {}
    for skill in skills:
        candidates_by_label.setdefault(_key(skill.canonical_name), set()).add(
            skill.skill_id
        )
    for alias in aliases:
        aliases_by_skill.setdefault(alias.skill_id, set()).add(_key(alias.alias))
        candidates_by_label.setdefault(_key(alias.alias), set()).add(alias.skill_id)

    claimed = by_id.get(claimed_skill_id or "")
    if claimed is not None:
        canonical_conflict = (
            claimed_canonical_name is not None
            and _key(claimed_canonical_name) != _key(claimed.canonical_name)
        )
        valid_source_names = {
            _key(claimed.canonical_name),
            *aliases_by_skill.get(claimed.skill_id, set()),
        }
        source_conflict = (
            _key(source_name) not in valid_source_names
            and claimed_canonical_name is None
        )
        if canonical_conflict or source_conflict:
            return CatalogResolution(
                "conflict",
                None,
                claimed_canonical_name,
                None,
                "capability_catalog_id_conflict",
                "skill_catalog_conflict",
            )
        if not _snapshot_complete(claimed):
            return CatalogResolution(
                "conflict",
                None,
                claimed.canonical_name,
                claimed.category_code,
                "capability_catalog_snapshot",
                "skill_catalog_snapshot_missing",
            )
        return CatalogResolution(
            "resolved",
            claimed.skill_id,
            claimed.canonical_name,
            claimed.category_code,
            "capability_catalog_id",
        )

    candidate_ids: set[str] = set()
    for label in {source_name, claimed_canonical_name}:
        if label:
            candidate_ids.update(candidates_by_label.get(_key(label), set()))
    if len(candidate_ids) > 1:
        return CatalogResolution(
            "conflict",
            None,
            claimed_canonical_name,
            None,
            "capability_catalog_exact_match",
            "skill_catalog_conflict",
        )
    if not candidate_ids:
        return CatalogResolution(
            "unresolved",
            None,
            claimed_canonical_name,
            None,
            "capability_catalog_exact_match",
            "skill_catalog_unresolved",
        )
    resolved = by_id[next(iter(candidate_ids))]
    if not _snapshot_complete(resolved):
        return CatalogResolution(
            "conflict",
            None,
            resolved.canonical_name,
            resolved.category_code,
            "capability_catalog_snapshot",
            "skill_catalog_snapshot_missing",
        )
    return CatalogResolution(
        "resolved",
        resolved.skill_id,
        resolved.canonical_name,
        resolved.category_code,
        "capability_catalog_alias",
    )


def require_catalog_binding(
    *,
    resolution_status: str,
    skill_id: str | None,
    canonical_name: str | None,
    skills: tuple[CatalogSkill, ...],
) -> CatalogSkill:
    if resolution_status == "conflict":
        raise SkillCatalogGateError("skill_catalog_conflict")
    if resolution_status not in {"resolved", "manually_confirmed"} or not skill_id:
        raise SkillCatalogGateError("skill_catalog_unresolved")
    skill = next((item for item in skills if item.skill_id == skill_id), None)
    if skill is None or not _snapshot_complete(skill):
        raise SkillCatalogGateError("skill_catalog_snapshot_missing")
    if canonical_name is not None and _key(canonical_name) != _key(
        skill.canonical_name
    ):
        raise SkillCatalogGateError("skill_catalog_conflict")
    return skill
