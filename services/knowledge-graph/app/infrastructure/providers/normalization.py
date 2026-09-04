from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ROOT
from app.domain.policies import normalize_key
from app.schemas.extraction import JDExtractionResult
from app.schemas.normalization import (
    JDNormalizedResult, JobClassification, NormalizedRequirement,
    NormalizedSalary, NormalizedSkill, UnresolvedItem,
)
from app.domain.structured_facts import ExtractionFacts, NormalizationFacts
from app.infrastructure.sqlalchemy.structured_fact_mappers import (
    extraction_schema,
    normalization_facts,
)


@dataclass(frozen=True)
class _Resolution:
    value: dict[str, object] | None
    reason: str | None
    source: str | None = None


@dataclass(frozen=True)
class _Candidate:
    value: dict[str, object]
    source: str


class Normalizer:
    """Resolve exact names against the map and the request catalog projection."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        session_factory: Callable[[], object] | None = None,
    ):
        self.data = yaml.safe_load(
            (path or ROOT / "config/normalization_map.yaml").read_text("utf-8")
        )
        self.skills = self._group_candidates(self.data["skills"], "explicit_mapping")
        self.positions = self._group_candidates(self.data["positions"], "explicit_mapping")
        self._session_factory = session_factory
        self._db_skills_cache: dict[str, tuple[_Candidate, ...]] | None = None
        self._db_positions_cache: dict[str, tuple[_Candidate, ...]] | None = None

    @staticmethod
    def _group_candidates(
        values: dict[str, dict[str, object]], source: str,
    ) -> dict[str, tuple[_Candidate, ...]]:
        grouped: dict[str, list[_Candidate]] = {}
        for name, value in values.items():
            key = normalize_key(name)
            if not key:
                continue
            grouped.setdefault(key, []).append(_Candidate(dict(value), source))
        return {key: tuple(candidates) for key, candidates in grouped.items()}

    def _load_skills(self) -> dict[str, tuple[_Candidate, ...]]:
        if self._session_factory is None:
            return {}
        from app.models import Skill

        session: Session = self._session_factory()
        rows = session.scalars(select(Skill).where(Skill.status == "active")).all()
        result: dict[str, list[_Candidate]] = {}
        for row in rows:
            name = str(row.canonical_name or "").strip()
            key = normalize_key(name)
            if not key:
                continue
            result.setdefault(key, []).append(_Candidate(
                {
                    "skill_id": row.skill_id,
                    "canonical_name": name,
                    "category_code": row.category_code or None,
                    "subcategory_code": row.subcategory_code or None,
                },
                "canonical_name",
            ))
        return {key: tuple(values) for key, values in result.items()}

    def _load_positions(self) -> dict[str, tuple[_Candidate, ...]]:
        if self._session_factory is None:
            return {}
        from app.models import StandardPosition

        session: Session = self._session_factory()
        rows = session.scalars(
            select(StandardPosition).where(StandardPosition.status == "active")
        ).all()
        result: dict[str, list[_Candidate]] = {}
        for row in rows:
            name = str(row.name or "").strip()
            key = normalize_key(name)
            if not key:
                continue
            result.setdefault(key, []).append(_Candidate(
                {
                    "position_id": row.position_id,
                    "position_name": name,
                },
                "canonical_name",
            ))
        return {key: tuple(values) for key, values in result.items()}

    @property
    def _db_skills(self) -> dict[str, tuple[_Candidate, ...]]:
        if self._db_skills_cache is None:
            self._db_skills_cache = self._load_skills()
        return self._db_skills_cache

    @property
    def _db_positions(self) -> dict[str, tuple[_Candidate, ...]]:
        if self._db_positions_cache is None:
            self._db_positions_cache = self._load_positions()
        return self._db_positions_cache

    @staticmethod
    def _candidate_key(candidate: _Candidate) -> str:
        value = {
            key: item
            for key, item in candidate.value.items()
            if key != "resolution_source"
        }
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _resolve(
        self,
        source_name: str,
        yaml_map: dict[str, tuple[_Candidate, ...]],
        db_map: dict[str, tuple[_Candidate, ...]],
        item_type: str,
    ) -> _Resolution:
        key = normalize_key(source_name)
        candidates = [*yaml_map.get(key, ()), *db_map.get(key, ())]
        unique_candidates: list[_Candidate] = []
        seen = set()
        for candidate in candidates:
            candidate_key = self._candidate_key(candidate)
            if candidate_key not in seen:
                seen.add(candidate_key)
                unique_candidates.append(candidate)
        if not unique_candidates:
            return _Resolution(None, "no exact normalized mapping")
        if len(unique_candidates) > 1:
            return _Resolution(
                None,
                f"normalization catalog conflict: multiple {item_type} candidates",
            )
        candidate = unique_candidates[0]
        return _Resolution(dict(candidate.value), None, candidate.source)

    def _resolve_skill(self, source_name: str) -> _Resolution:
        return self._resolve(source_name, self.skills, self._db_skills, "skill")

    def _resolve_position(self, source_name: str) -> _Resolution:
        return self._resolve(source_name, self.positions, self._db_positions, "position")

    def normalize(self, extraction: JDExtractionResult) -> JDNormalizedResult:
        title = extraction.job_title.text if extraction.job_title else None
        classification = JobClassification(
            taxonomy_version="position-taxonomy.v3.0.0",
            source_title=title,
            classification_status="catalog_gap",
            review_reason_codes=["AUTHORITATIVE_POSITION_CLASSIFICATION_REQUIRED"],
            classification_policy_version="position-classifier.v3.0",
        )
        requirements, unresolved = [], []
        for requirement in extraction.requirements:
            skills = []
            if requirement.kind == "skill":
                for item in requirement.items:
                    skill_resolution = self._resolve_skill(item.name)
                    hit = skill_resolution.value
                    skills.append(NormalizedSkill(
                        source_name=item.name, **(hit or {}),
                        resolution_status="resolved" if hit else "unresolved",
                        resolution_source=skill_resolution.source or "unresolved",
                    ))
                    if not hit:
                        unresolved.append(UnresolvedItem(
                            source_name=item.name, item_type="skill",
                            reason=skill_resolution.reason or "no exact normalized mapping",
                        ))
            requirements.append(NormalizedRequirement(
                requirement_id=requirement.requirement_id, kind=requirement.kind,
                normalized_skills=skills,
            ))
        if title:
            unresolved.append(UnresolvedItem(
                source_name=title, item_type="position",
                reason="authoritative position-taxonomy.v3 classification required",
            ))
        salary = next((item.text for item in extraction.employment_facts
                       if item.fact_type == "salary"), None)
        return JDNormalizedResult(
            document_id=extraction.document_id, job_classification=classification,
            normalized_requirements=requirements,
            salary=normalize_salary(salary) if salary else None,
            unresolved_items=unresolved,
        )


class NormalizationProviderAdapter:
    """Translate framework-free facts at the provider boundary."""

    def __init__(self, normalizer: Normalizer | None = None):
        self._normalizer = normalizer or Normalizer()

    def produce(self, facts: ExtractionFacts) -> NormalizationFacts:
        return normalization_facts(self._normalizer.normalize(extraction_schema(facts)))


def normalize_salary(value: str) -> NormalizedSalary:
    normalized = normalize_key(value).replace(",", "")
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", normalized)]
    multiplier = 1000 if re.search(r"(?<=\d)k\b|千", normalized) else 10000 if "万" in normalized else 1
    period = "month" if re.search(r"/\s*(?:月|month)|月薪", normalized) else "year" if re.search(r"/\s*(?:年|year)|年薪", normalized) else "day" if re.search(r"/\s*(?:天|day)", normalized) else "hour" if re.search(r"/\s*(?:时|小时|hour)", normalized) else "unknown"
    currency = "USD" if "$" in value or "usd" in normalized else "CNY"
    minimum = numbers[0] * multiplier if numbers else None
    maximum = numbers[1] * multiplier if len(numbers) > 1 else minimum
    return NormalizedSalary(currency=currency, minimum=minimum, maximum=maximum, period=period)
