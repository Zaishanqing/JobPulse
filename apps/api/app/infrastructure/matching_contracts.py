from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session, sessionmaker

from app.contexts.matching_learning.contracts_service import (
    MatchingContractUnavailable,
    NO_FORMAL_REQUIREMENTS,
    NO_VALID_SPECIALTY_ROUTE,
    ROUTE_SUPPORT_UNAVAILABLE,
    STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION,
    StandardPositionProfileInsufficient,
)
from app.infrastructure.jd_pipeline import extract_jd
from app.integrations.knowledge_graph.exceptions import KnowledgeGraphError
from app.models.candidate_submission import CandidateSubmission
from app.models.enterprise import Enterprise
from app.models.enterprise_job import EnterpriseJob
from app.models.enterprise_job_weight import EnterpriseJobSkillWeight
from app.models.resume import Resume
from app.models.standard_position import StandardPosition
from app.models.source_cv import CVExtractionTask, ValidatedCVSnapshot
from app.models.cv_position_classification import CVPositionClassification
from jobgraph_contracts.matching import (
    CV_BUNDLE_SCHEMA_VERSION,
    POSITION_PROFILE_SCHEMA_VERSION,
)
from jobgraph_contracts.requirement_graph import RequirementGraph
from jobgraph_contracts.privacy import PII_FORBIDDEN_KEYS, find_pii

_OWNERSHIP_PATTERNS = (
    ("led", re.compile(r"主导|带领|\blead\b|负责人|\bowner\b", re.IGNORECASE)),
    ("designed", re.compile(r"设计|架构|\barchitect(?:ed|ure)?\b|\bdesign(?:ed)?\b", re.IGNORECASE)),
    ("owned", re.compile(r"独立|自主|负责核心|\bown(?:ed)?\b", re.IGNORECASE)),
    ("implemented", re.compile(r"实现|开发|落地|构建|\bimplement(?:ed)?\b|\bdevelop(?:ed)?\b", re.IGNORECASE)),
    ("participated", re.compile(r"参与|协助|支持|\bparticipat(?:e|ed)\b|\bassist(?:ed)?\b", re.IGNORECASE)),
)
_OWNERSHIP_TO_LEVEL = {
    "used": "basic",
    "participated": "basic",
    "implemented": "working",
    "owned": "proficient",
    "designed": "advanced",
    "led": "advanced",
}

# The route graph is a Matching-side projection of the published KG profile.
# It does not alter the published PositionProfileV3 or the shared/exact-JD
# projection used by enterprise jobs.
_STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION = (
    STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION
)
_STANDARD_POSITION_ROUTE_ROOT_PREFIX = "standard-route-root:"
_STANDARD_POSITION_ROUTE_PREFIX = "standard-route:"
_STANDARD_POSITION_CLAUSE_PREFIX = "standard-clause:"
_STANDARD_POSITION_SKILL_REQUIREMENT_PREFIX = "standard-position:skill:"

_ALTERNATIVE_REQUIREMENT = re.compile(
    r"(?:或者|或(?:者)?|任一|任选|其中(?:任意)?一(?:种|项)?|至少[^，。；;]{0,24}(?:一种|1\s*种)|"
    r"\b(?:or|either|one\s+of|any\s+one)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _SpecialtyClause:
    requirement_id: str
    skill_ids: tuple[str, ...]
    group_type: str
    evidence: dict[str, object]


@dataclass(frozen=True)
class _SpecialtyRoute:
    clauses: tuple[_SpecialtyClause, ...]
    evidence: dict[str, object]

    @property
    def signature(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return tuple((item.group_type, item.skill_ids) for item in self.clauses)


def _safe_text(value: object) -> str:
    return str(value or "").strip()


_PII_REPLACEMENT = "[已脱敏]"


def _scrub_profile_pii(value: object, path: str = "$") -> object:
    """Redact PII leaves so profiles always pass the matching-service gate.

    Uses the exact rules the matching service enforces (jobgraph_contracts.privacy
    is a verbatim copy of its boundary check), so a scrubbed profile is accepted
    by construction.  Structured identifier fields keep their values unless they
    were already flagged, in which case the profile fails loudly downstream.
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if str(key).strip().lower() in PII_FORBIDDEN_KEYS:
                result[key] = _PII_REPLACEMENT
            else:
                result[key] = _scrub_profile_pii(item, f"{path}.{key}")
        return result
    if isinstance(value, (list, tuple)):
        scrubbed = [
            _scrub_profile_pii(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
        return type(value)(scrubbed)
    if isinstance(value, str) and find_pii(value, path):
        return _PII_REPLACEMENT
    return value


def _ownership_level(values: list[str]) -> str:
    text = " ".join(value for value in values if value)
    return next(
        (level for level, pattern in _OWNERSHIP_PATTERNS if pattern.search(text)),
        "used",
    )


def _evidence(source_id: str, quote: str) -> dict[str, object]:
    safe = _safe_text(quote)
    return {
        "source_id": source_id,
        "quote": safe,
        "start": None,
        "end": None,
        "alignment": "unresolved",
        "occurrence_index": None,
    }


def _snapshot_evidence(raw: object, fallback_id: str, fallback_quote: str) -> dict[str, object]:
    item = raw if isinstance(raw, dict) else {}
    source_id = str(item.get("source_id") or fallback_id)
    quote = _safe_text(item.get("quote") or fallback_quote)
    start = item.get("start")
    end = item.get("end")
    has_valid_span = (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and 0 <= start < end
    )
    raw_alignment = str(item.get("alignment") or "unresolved")
    alignment = (
        raw_alignment
        if has_valid_span and raw_alignment in {"exact", "normalized_exact"}
        else "unresolved"
    )
    occurrence_index = item.get("occurrence_index")
    if (
        not isinstance(occurrence_index, int)
        or isinstance(occurrence_index, bool)
        or occurrence_index < 0
    ):
        occurrence_index = None
    return {
        "source_id": source_id,
        "quote": quote,
        "start": start if alignment != "unresolved" else None,
        "end": end if alignment != "unresolved" else None,
        "alignment": alignment,
        "occurrence_index": occurrence_index if alignment != "unresolved" else None,
    }


def _sourced_value(value: object, fallback: str) -> dict[str, object]:
    item = value if isinstance(value, dict) else {}
    text = _safe_text(item.get("value") or item.get("text") or fallback)
    return {"value": text, "evidence": _snapshot_evidence(item.get("evidence"), "sourced", text)}


def _profile_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _profile_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _required_skill_gate(raw: object) -> dict[str, float | int]:
    profile = raw if isinstance(raw, dict) else {}
    dependencies = profile.get("dependencies")
    thresholds = (
        dependencies.get("position_profile_thresholds")
        if isinstance(dependencies, dict)
        else None
    )
    gate = (
        thresholds.get("required_skill_gate")
        if isinstance(thresholds, dict)
        else None
    )
    return {
        "min_required_prevalence": (
            _profile_float(gate.get("min_required_prevalence"), 1.0)
            if isinstance(gate, dict)
            else 1.0
        ),
        "min_required_prevalence_jd_count": (
            _profile_int(gate.get("min_required_prevalence_jd_count"), 2**31 - 1)
            if isinstance(gate, dict)
            else 2**31 - 1
        ),
        "min_required_purity": (
            _profile_float(gate.get("min_required_purity"), 1.0)
            if isinstance(gate, dict)
            else 1.0
        ),
    }


def _kg_evidence(raw: object) -> dict[str, object]:
    item = raw if isinstance(raw, dict) else {}
    quote = _safe_text(
        item.get("quote")
        or item.get("document_id")
        or item.get("requirement_id")
        or "published position profile"
    )
    projected = dict(item)
    projected["source_id"] = str(
        item.get("evidence_id") or item.get("source_id") or "kg-position-profile"
    )
    projected["quote"] = quote
    return _snapshot_evidence(projected, "kg-position-profile", quote)


def _skill_feature_id(document_id: str, source_item_id: str) -> str:
    """Keep feature identity stable per source skill across snapshot revisions."""
    return f"{document_id}:skill:{source_item_id}"


def _enterprise_atom_id(job_id: str, namespace: str, value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"enterprise-job:{job_id}:{namespace}:{hashlib.sha256(payload).hexdigest()[:16]}"


def _dedupe_evidence(*groups: object) -> tuple[dict[str, object], ...]:
    result: list[dict[str, object]] = []
    seen: set[tuple[object, ...]] = set()
    for group in groups:
        if not isinstance(group, (list, tuple)):
            continue
        for raw in group:
            if not isinstance(raw, dict):
                continue
            key = (
                raw.get("source_id"),
                raw.get("quote"),
                raw.get("start"),
                raw.get("end"),
                raw.get("alignment"),
                raw.get("occurrence_index"),
            )
            if key not in seen:
                seen.add(key)
                result.append(raw)
    return tuple(result)


def _remap_requirement_graph_reference_ids(
    graph: RequirementGraph | dict[str, object] | list[object] | None,
    mapping: dict[str, str],
) -> RequirementGraph | dict[str, object] | list[object] | None:
    if graph is None:
        return None
    if isinstance(graph, RequirementGraph):
        return RequirementGraph.model_validate(
            _remap_requirement_graph_reference_ids(
                graph.model_dump(mode="json"), mapping
            )
        )
    if isinstance(graph, dict):
        if graph.get("node_type") == "requirement_ref":
            ref_id = str(graph.get("ref_id") or "")
            return {**graph, "ref_id": mapping.get(ref_id, ref_id)}
        return {
            key: _remap_requirement_graph_reference_ids(value, mapping)
            for key, value in graph.items()
        }
    if isinstance(graph, list):
        return [
            _remap_requirement_graph_reference_ids(item, mapping)
            for item in graph
        ]
    return graph


def _enterprise_job_match_profile(
    base: dict[str, object], reference: dict[str, object]
) -> dict[str, object]:
    """Overlay exact, job-owned JD facts on the reusable standard-position profile."""

    job_id = str(reference["job_id"])
    source_id = f"enterprise-job:{job_id}:jd"
    jd_text = str(reference.get("jd_text") or "").strip()
    extracted = extract_jd(
        source_id, jd_text, str(reference.get("title") or "")
    ).model_dump(mode="python")
    enterprise_evidence: list[dict[str, object]] = []

    def evidence(raw: object, fallback: str) -> dict[str, object]:
        projected = _snapshot_evidence(raw, source_id, fallback)
        enterprise_evidence.append(projected)
        return projected

    responsibilities: list[str] = []
    responsibility_ref_map: dict[str, str] = {}
    responsibility_index = 0
    for item in extracted["responsibilities"]:
        text = _safe_text(item.get("action"))
        if not text:
            continue
        responsibility_index += 1
        responsibilities.append(text)
        original_id = item.get("requirement_id")
        if original_id:
            responsibility_ref_map[str(original_id)] = (
                f"responsibility:{responsibility_index}"
            )
        evidence(item.get("evidence"), text)
    responsibilities.extend(
        value
        for value in base.get("core_responsibilities", ())
        if value not in responsibilities
    )

    base_skills = [
        item
        for field in ("required_skills", "preferred_skills")
        for item in base.get(field, ())
        if isinstance(item, dict)
    ]
    base_by_name = {
        str(item.get("canonical_name") or "").casefold(): item
        for item in base_skills
        if item.get("canonical_name")
    }
    enterprise_required: list[dict[str, object]] = []
    enterprise_preferred: list[dict[str, object]] = []
    overlaid_names: set[str] = set()
    hard_conditions: list[dict[str, object]] = []
    for item in extracted["requirements"]:
        raw_evidence = item.get("evidence")
        kind = item.get("kind")
        modality = str(item.get("modality") or "unknown")
        if kind == "skill":
            for skill in item.get("items", []):
                if not isinstance(skill, dict):
                    continue
                name = _safe_text(skill.get("name"))
                if not name:
                    continue
                key = name.casefold()
                base_skill = base_by_name.get(key)
                skill_evidence = evidence(raw_evidence, name)
                requirement = {
                    "requirement_id": _enterprise_atom_id(
                        job_id, "skill", (key, modality, skill_evidence)
                    ),
                    "skill_id": base_skill.get("skill_id") if base_skill else None,
                    "canonical_name": (
                        base_skill.get("canonical_name") if base_skill else name
                    ),
                    "required_level": item.get("proficiency"),
                    "importance": 1.0 if modality == "required" else 0.7,
                    "resolution_status": "resolved" if base_skill else "unresolved",
                    "evidence_refs": (skill_evidence,),
                }
                target = (
                    enterprise_required
                    if modality == "required"
                    else enterprise_preferred
                )
                target.append(requirement)
                overlaid_names.add(key)
            continue
        condition_type = None
        operator = "at_least"
        value = None
        if kind == "education" and item.get("minimum_degree"):
            condition_type = "education"
            value = str(item["minimum_degree"])
        elif kind == "experience" and item.get("minimum_years") is not None:
            condition_type = "experience"
            value = f"{float(item['minimum_years']):g} years"
        elif kind == "certificate" and item.get("certificates"):
            condition_type = "certificate"
            operator = "one_of"
            value = "|".join(str(entry) for entry in item["certificates"])
        if condition_type and value:
            condition_evidence = evidence(raw_evidence, value)
            hard_conditions.append(
                {
                    "condition_id": _enterprise_atom_id(
                        job_id, "condition", (condition_type, value, condition_evidence)
                    ),
                    "condition_type": condition_type,
                    "operator": operator,
                    "value": value,
                    "resolution_status": "resolved",
                    "evidence_refs": (condition_evidence,),
                }
            )

    required_skills = enterprise_required + [
        item
        for item in base.get("required_skills", ())
        if isinstance(item, dict)
        and str(item.get("canonical_name") or "").casefold() not in overlaid_names
    ]
    preferred_skills = enterprise_preferred + [
        item
        for item in base.get("preferred_skills", ())
        if isinstance(item, dict)
        and str(item.get("canonical_name") or "").casefold() not in overlaid_names
    ]
    extracted_locations = [
        fact
        for fact in extracted["employment_facts"]
        if fact.get("kind") in {"location", "work_location"}
        and _safe_text(fact.get("value"))
    ]
    if extracted_locations:
        location_values = [_safe_text(fact["value"]) for fact in extracted_locations]
        location_evidence = [
            evidence(fact.get("evidence"), value)
            for fact, value in zip(extracted_locations, location_values, strict=True)
        ]
        hard_conditions.insert(
            0,
            {
                "condition_id": _enterprise_atom_id(
                    job_id, "condition", ("location", location_values, location_evidence)
                ),
                "condition_type": "location",
                "operator": "one_of",
                "value": "|".join(location_values),
                "resolution_status": "resolved",
                "evidence_refs": tuple(location_evidence),
            },
        )
    enterprise_condition_types = {
        item["condition_type"] for item in hard_conditions
    }
    hard_conditions.extend(
        item
        for item in base.get("hard_conditions", ())
        if isinstance(item, dict)
        and item.get("condition_type") not in enterprise_condition_types
    )

    scenario_values: list[str] = []
    scenario_evidence: list[dict[str, object]] = []
    industry_values: list[str] = []
    industry_evidence: list[dict[str, object]] = []
    for fact in extracted["company_facts"]:
        value = _safe_text(fact.get("value"))
        if not value:
            continue
        if fact.get("kind") == "business":
            scenario_values.append(value)
            scenario_evidence.append(evidence(fact.get("evidence"), value))
        elif fact.get("kind") == "industry":
            industry_values.append(value)
            industry_evidence.append(evidence(fact.get("evidence"), value))

    for field, values, refs in (
        ("business_scenarios", scenario_values, scenario_evidence),
        ("industries", industry_values, industry_evidence),
    ):
        base_context = base.get(field)
        if isinstance(base_context, dict) and base_context.get("availability") == "available":
            values.extend(
                value for value in base_context.get("values", ()) if value not in values
            )
            refs.extend(
                item
                for item in base_context.get("evidence_refs", ())
                if isinstance(item, dict)
            )

    requirement_graph = _remap_requirement_graph_reference_ids(
        reference.get("requirement_graph"), responsibility_ref_map
    )
    identity_input = {
        "jd_text": jd_text,
        "requirement_graph": requirement_graph,
        "weights": reference.get("weights"),
        "title": reference.get("title"),
        "standard_position_id": reference.get("standard_position_id"),
        "location": reference.get("location"),
        "employment_type": reference.get("employment_type"),
        "headcount": reference.get("headcount"),
        "salary_min": reference.get("salary_min"),
        "salary_max": reference.get("salary_max"),
        "base_source_version": base.get("source_version"),
    }
    digest = hashlib.sha256(
        json.dumps(
            identity_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    version = f"enterprise-job-profile.v1:{job_id}:{digest}"
    return {
        **base,
        "source_version": version,
        "profile_id": f"enterprise-job:{job_id}",
        "profile_version": version,
        "created_at": reference.get("updated_at") or base.get("created_at"),
        "core_responsibilities": tuple(responsibilities),
        "required_skills": tuple(required_skills),
        "preferred_skills": tuple(preferred_skills),
        "hard_conditions": tuple(hard_conditions),
        "industries": {
            "values": tuple(dict.fromkeys(industry_values)),
            "evidence_refs": _dedupe_evidence(industry_evidence),
            "availability": "available" if industry_values else "unavailable",
        },
        "business_scenarios": {
            "values": tuple(dict.fromkeys(scenario_values)),
            "evidence_refs": _dedupe_evidence(scenario_evidence),
            "availability": "available" if scenario_values else "unavailable",
        },
        "evidence_refs": _dedupe_evidence(
            enterprise_evidence, base.get("evidence_refs", ())
        ),
        "quality_context": {
            **base["quality_context"],
            "snapshot_id": f"enterprise-job:{job_id}:{digest[:16]}",
            "evidence_refs": _dedupe_evidence(enterprise_evidence),
        },
        "requirement_graph": requirement_graph,
    }


def _standard_position_skill_requirement_id(skill_id: str) -> str:
    return f"{_STANDARD_POSITION_SKILL_REQUIREMENT_PREFIX}{skill_id}"


def _standard_position_route_support_rows(
    raw: dict[str, object],
    profile: dict[str, object],
) -> tuple[_SpecialtyRoute, ...]:
    """Recover coherent required-skill routes from published JD support rows.

    ``evidence_summary`` is the published KG projection of PositionSkillSupport.
    Grouping its formal rows by ``document_id`` preserves source-JD membership;
    prevalence is used only as the already-published market-status signal and
    never ranks or truncates the route skill set.
    """

    emitted_skill_ids = {
        str(item.get("skill_id") or "")
        for field in ("required_skills", "preferred_skills")
        for item in profile.get(field, ())
        if isinstance(item, dict) and item.get("skill_id")
    }
    eligible_skill_ids: set[str] = set()
    for item in raw.get("skill_relations", ()):
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "")
        resolution_status = item.get("resolution_status")
        market_status = str(
            item.get("requirement_market_status")
            or item.get("market_status")
            or ""
        )
        if (
            skill_id in emitted_skill_ids
            and str(item.get("modality") or "").lower() == "required"
            and market_status == "market_supported"
            and resolution_status in (None, "resolved")
        ):
            eligible_skill_ids.add(skill_id)

    support_rows = [
        item
        for item in raw.get("evidence_summary", ())
        if isinstance(item, dict)
    ]

    def support_key(item: dict[str, object]) -> tuple[str, str, str, int] | None:
        document_id = str(item.get("document_id") or "")
        requirement_id = str(item.get("requirement_id") or "")
        skill_id = str(item.get("skill_id") or "")
        evidence_id = _profile_int(item.get("evidence_id"), 0)
        if not document_id or not requirement_id or not skill_id or evidence_id <= 0:
            return None
        return document_id, requirement_id, skill_id, evidence_id

    formal_support_keys: set[tuple[str, str, str, int]] = set()
    inflation = raw.get("requirement_inflation")
    diagnostics = inflation.get("jd_diagnostics", ()) if isinstance(inflation, dict) else ()
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        document_id = str(diagnostic.get("document_id") or "")
        for requirement in diagnostic.get("requirements", ()):
            if not isinstance(requirement, dict):
                continue
            modality = str(
                requirement.get("jd_modality")
                or requirement.get("modality")
                or ""
            ).lower()
            if modality != "required":
                continue
            marker = support_key(
                {
                    **requirement,
                    "document_id": document_id,
                }
            )
            if marker is not None:
                formal_support_keys.add(marker)
    for item in support_rows:
        modality = str(
            item.get("jd_modality")
            or item.get("modality")
            or ""
        ).lower()
        if modality == "required":
            marker = support_key(item)
            if marker is not None:
                formal_support_keys.add(marker)

    by_document: dict[str, dict[str, set[str]]] = {}
    first_evidence_by_requirement: dict[tuple[str, str], dict[str, object]] = {}
    support_rows.sort(
        key=lambda item: (
            str(item.get("document_id") or ""),
            _profile_int(item.get("evidence_id"), 0),
            str(item.get("requirement_id") or ""),
            str(item.get("skill_id") or ""),
        )
    )
    for item in support_rows:
        marker = support_key(item)
        if marker is None:
            continue
        document_id, requirement_id, skill_id, _evidence_id = marker
        if skill_id not in eligible_skill_ids:
            continue
        if marker not in formal_support_keys:
            continue
        by_document.setdefault(document_id, {}).setdefault(requirement_id, set()).add(
            skill_id
        )
        first_evidence_by_requirement.setdefault((document_id, requirement_id), item)

    routes_by_signature: dict[
        tuple[tuple[str, tuple[str, ...]], ...], _SpecialtyRoute
    ] = {}
    for document_id, requirements in sorted(by_document.items()):
        clauses: list[_SpecialtyClause] = []
        for requirement_id, skill_ids in sorted(requirements.items()):
            evidence = first_evidence_by_requirement[(document_id, requirement_id)]
            quote = str(evidence.get("quote") or "")
            ordered_skill_ids = tuple(sorted(skill_ids))
            group_type = (
                "one_of"
                if len(ordered_skill_ids) >= 2
                and _ALTERNATIVE_REQUIREMENT.search(quote)
                else "and"
            )
            clauses.append(
                _SpecialtyClause(
                    requirement_id=requirement_id,
                    skill_ids=ordered_skill_ids,
                    group_type=group_type,
                    evidence=evidence,
                )
            )
        if not clauses:
            continue
        route = _SpecialtyRoute(tuple(clauses), clauses[0].evidence)
        routes_by_signature.setdefault(route.signature, route)
    return tuple(routes_by_signature[key] for key in sorted(routes_by_signature))


def _standard_position_no_route_reason(
    raw: dict[str, object],
    profile: dict[str, object],
) -> str:
    """Classify why a published profile cannot produce a specialty route."""

    published_skill_ids = {
        str(item.get("skill_id") or "")
        for item in raw.get("skill_relations", ())
        if isinstance(item, dict) and item.get("skill_id")
    }
    published_skill_ids.update(
        str(item.get("skill_id") or "")
        for field in ("required_skills", "preferred_skills")
        for item in profile.get(field, ())
        if isinstance(item, dict) and item.get("skill_id")
    )

    formal_required_skill = any(
        isinstance(item, dict)
        and item.get("skill_id")
        and str(item.get("modality") or "").lower() == "required"
        and str(
            item.get("requirement_market_status")
            or item.get("market_status")
            or ""
        )
        == "market_supported"
        and item.get("resolution_status") in (None, "resolved")
        for item in raw.get("skill_relations", ())
    )

    def valid_support(item: object, *, document_id: str = "") -> bool:
        if not isinstance(item, dict):
            return False
        modality = str(
            item.get("jd_modality") or item.get("modality") or ""
        ).lower()
        return bool(
            modality == "required"
            and (str(item.get("document_id") or document_id))
            and str(item.get("requirement_id") or "")
            and str(item.get("skill_id") or "")
            and _profile_int(item.get("evidence_id"), 0) > 0
        )

    formal_support = any(
        valid_support(item) for item in raw.get("evidence_summary", ())
    )
    inflation = raw.get("requirement_inflation")
    diagnostics = (
        inflation.get("jd_diagnostics", ())
        if isinstance(inflation, dict)
        else ()
    )
    formal_support = formal_support or any(
        valid_support(requirement, document_id=str(diagnostic.get("document_id") or ""))
        for diagnostic in diagnostics
        if isinstance(diagnostic, dict)
        for requirement in diagnostic.get("requirements", ())
    )

    if formal_required_skill or formal_support:
        return ROUTE_SUPPORT_UNAVAILABLE
    if published_skill_ids:
        return NO_VALID_SPECIALTY_ROUTE
    return NO_FORMAL_REQUIREMENTS


def _build_standard_position_specialty_route_graph(
    raw: dict[str, object],
    profile: dict[str, object],
) -> RequirementGraph | None:
    routes = _standard_position_route_support_rows(raw, profile)
    if not routes:
        return None

    groups: list[dict[str, object]] = []
    route_ids: list[str] = []
    for route in routes:
        signature = "|".join(
            f"{group_type}:{','.join(skill_ids)}"
            for group_type, skill_ids in route.signature
        )
        route_id = _STANDARD_POSITION_ROUTE_PREFIX + hashlib.sha256(
            signature.encode("utf-8")
        ).hexdigest()[:16]
        route_ids.append(route_id)
        route_children: list[dict[str, object]] = []
        for clause in route.clauses:
            atomic_children = tuple(
                {
                    "node_type": "requirement_ref",
                    "ref_id": _standard_position_skill_requirement_id(skill_id),
                    "aspect": next(
                        (
                            str(item.get("canonical_name") or skill_id)
                            for field in ("required_skills", "preferred_skills")
                            for item in profile.get(field, ())
                            if isinstance(item, dict)
                            and str(item.get("skill_id") or "") == skill_id
                        ),
                        skill_id,
                    ),
                }
                for skill_id in clause.skill_ids
            )
            if len(atomic_children) == 1:
                route_children.append(atomic_children[0])
                continue
            clause_signature = (
                f"{route_id}|{clause.requirement_id}|{clause.group_type}|"
                + "|".join(clause.skill_ids)
            )
            clause_id = _STANDARD_POSITION_CLAUSE_PREFIX + hashlib.sha256(
                clause_signature.encode("utf-8")
            ).hexdigest()[:16]
            groups.append(
                {
                    "requirement_group_id": clause_id,
                    "group_type": clause.group_type,
                    "priority": "required",
                    "children": atomic_children,
                    "evidence": _kg_evidence(clause.evidence),
                    "confidence": 1.0,
                    "note": "source JD requirement clause",
                }
            )
            route_children.append({"node_type": "group_ref", "ref_id": clause_id})
        groups.append(
            {
                "requirement_group_id": route_id,
                "group_type": "and" if len(route_children) >= 2 else "must",
                "priority": "required",
                "children": tuple(route_children),
                "evidence": _kg_evidence(route.evidence),
                "confidence": 1.0,
                "note": "source JD required-skill route",
            }
        )
    root_id = _STANDARD_POSITION_ROUTE_ROOT_PREFIX + hashlib.sha256(
        "|".join(route_ids).encode("utf-8")
    ).hexdigest()[:16]
    root_children = tuple(
        {"node_type": "group_ref", "ref_id": route_id}
        for route_id in route_ids
    )
    root_evidence = _kg_evidence(routes[0].evidence)
    graph_groups = (
        *groups,
        {
            "requirement_group_id": root_id,
            "group_type": "one_of" if len(root_children) >= 2 else "must",
            "priority": "required",
            "children": root_children,
            "evidence": root_evidence,
            "confidence": 1.0,
            "note": "select one source JD specialty route",
        },
    )
    return RequirementGraph.model_validate(
        {
            "graph_version": _STANDARD_POSITION_SPECIALTY_ROUTE_GRAPH_VERSION,
            "status": "complete",
            "groups": graph_groups,
            "unresolved_items": [],
        }
    )


def _apply_standard_position_specialty_routes(
    raw: dict[str, object],
    profile: dict[str, object],
) -> dict[str, object]:
    graph = _build_standard_position_specialty_route_graph(raw, profile)
    if graph is None:
        raise StandardPositionProfileInsufficient(
            _standard_position_no_route_reason(raw, profile)
        )

    support_evidence_by_skill: dict[str, tuple[dict[str, object], ...]] = {}
    for item in raw.get("evidence_summary", ()):
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "")
        if (
            skill_id
            and str(item.get("document_id") or "")
            and str(item.get("requirement_id") or "")
            and _profile_int(item.get("evidence_id"), 0) > 0
        ):
            support_evidence_by_skill.setdefault(skill_id, ())
            support_evidence_by_skill[skill_id] += (_kg_evidence(item),)

    def with_requirement_id(item: dict[str, object]) -> dict[str, object]:
        skill_id = str(item.get("skill_id") or "")
        return {
            **item,
            "requirement_id": _standard_position_skill_requirement_id(skill_id),
            "evidence_refs": item.get("evidence_refs")
            or support_evidence_by_skill.get(skill_id, ())[:1],
        }

    return {
        **profile,
        "required_skills": tuple(
            with_requirement_id(item)
            for item in profile.get("required_skills", ())
            if isinstance(item, dict)
        ),
        "preferred_skills": tuple(
            with_requirement_id(item)
            for item in profile.get("preferred_skills", ())
            if isinstance(item, dict)
        ),
        "requirement_graph": graph.model_dump(mode="python"),
    }


def _standard_position_match_profile(
    raw: object,
    *,
    position_id: str,
    canonical_position_id: str,
    canonical_title: str,
) -> dict[str, object] | None:
    """Project a published KG profile with source-JD specialty routes."""

    profile = _kg_position_match_profile(
        raw,
        position_id=position_id,
        canonical_position_id=canonical_position_id,
        canonical_title=canonical_title,
    )
    if profile is None:
        if isinstance(raw, dict) and raw.get("profile_state") == "published":
            raise StandardPositionProfileInsufficient(
                _standard_position_no_route_reason(raw, {})
            )
        return profile
    if not isinstance(raw, dict):
        return profile
    return _apply_standard_position_specialty_routes(raw, profile)


def _kg_responsibility_requirements(
    raw: object,
) -> tuple[dict[str, object], ...]:
    """Project one formal requirement per KG semantic responsibility group."""
    if not isinstance(raw, dict):
        return ()
    requirements: list[dict[str, object]] = []
    seen_requirement_ids: set[str] = set()
    for index, item in enumerate(raw.get("responsibilities", [])):
        if not isinstance(item, dict):
            continue
        text = _safe_text(item.get("representative_text") or item.get("text"))
        if not text:
            continue
        topic_coordinate = str(
            item.get("topic")
            or item.get("representative_source_id")
            or item.get("aggregate_id")
            or item.get("requirement_id")
            or index + 1
        )
        requirement_id = "standard-position:responsibility:" + hashlib.sha256(
            topic_coordinate.encode("utf-8")
        ).hexdigest()[:16]
        if requirement_id in seen_requirement_ids:
            continue
        seen_requirement_ids.add(requirement_id)
        evidence = tuple(
            _kg_evidence(raw_evidence)
            for raw_evidence in item.get("evidence") or ()
            if isinstance(raw_evidence, dict)
        )
        requirements.append(
            {
                "requirement_id": requirement_id,
                "text": text,
                "skill_ids": tuple(
                    sorted(
                        {
                            str(skill_id).strip()
                            for skill_id in item.get("skill_ids") or ()
                            if str(skill_id).strip()
                        }
                    )
                ),
                "resolution_status": "resolved",
                "evidence_refs": evidence,
            }
        )
    return tuple(requirements)


def _kg_responsibility_texts(
    raw: object,
) -> tuple[str, ...]:
    return tuple(
        dict(item)["text"]
        for item in _kg_responsibility_requirements(raw)
    )


_EXPERIENCE_YEARS = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*年")
_DEGREE_LEVELS = (
    ("doctor", ("博士", "doctor", "phd")),
    ("master", ("硕士", "master")),
    ("bachelor", ("本科", "学士", "bachelor")),
    ("associate", ("大专", "专科", "associate")),
)
_MANDATORY_CERTIFICATE_MARKERS = (
    "必须",
    "必需",
    "须具备",
    "要求具备",
    "硬性要求",
    "required",
    "mandatory",
)


def _kg_requirement_evidence(item: dict[str, object]) -> tuple[dict[str, object], ...]:
    raw_evidence = item.get("evidence")
    values = (
        raw_evidence
        if isinstance(raw_evidence, (list, tuple))
        else (raw_evidence,)
        if isinstance(raw_evidence, dict)
        else ()
    )
    return tuple(_kg_evidence(value) for value in values if isinstance(value, dict))


def _kg_certificate_is_mandatory(item: dict[str, object]) -> bool:
    """Only explicit mandatory credentials may enter the Hard Gate."""
    if item.get("is_required") is True or str(
        item.get("importance_level") or ""
    ).casefold() == "required":
        return True
    evidence_text = " ".join(
        _safe_text(value.get("quote"))
        for value in item.get("evidence") or ()
        if isinstance(value, dict)
    )
    text = f"{_safe_text(item.get('text'))} {evidence_text}".casefold()
    return any(marker in text for marker in _MANDATORY_CERTIFICATE_MARKERS)


def _kg_hard_conditions(raw: dict[str, object]) -> tuple[dict[str, object], ...]:
    """Project only semantically structured KG requirement aggregates."""
    requirements = tuple(
        item for item in raw.get("requirements", ()) if isinstance(item, dict)
    )
    conditions: list[dict[str, object]] = []

    education_candidates: list[tuple[int, int, str, dict[str, object]]] = []
    for item in requirements:
        if item.get("kind") != "education":
            continue
        text = _safe_text(item.get("text") or item.get("minimum_degree"))
        lowered = text.casefold()
        structured = str(item.get("minimum_degree") or "").casefold()
        if not structured and ("优先" in text or "preferred" in lowered):
            continue
        for rank, (degree, aliases) in enumerate(_DEGREE_LEVELS):
            if structured == degree or any(alias in lowered for alias in aliases):
                support = _profile_int(item.get("support_document_count"), 1)
                education_candidates.append((support, -rank, degree, item))
                break
    if education_candidates:
        _support, _rank, degree, source = max(
            education_candidates, key=lambda value: value[:3]
        )
        degree_value = str(source.get("minimum_degree") or degree)
        conditions.append(
            {
                "condition_id": "standard-position:education:" + degree,
                "condition_type": "education",
                "operator": "at_least",
                "value": degree_value,
                "resolution_status": "resolved",
                "evidence_refs": _kg_requirement_evidence(source),
            }
        )

    experience_candidates: list[tuple[int, float, dict[str, object]]] = []
    for item in requirements:
        if item.get("kind") != "experience":
            continue
        years = item.get("minimum_years")
        if years is None:
            match = _EXPERIENCE_YEARS.search(_safe_text(item.get("text")))
            years = float(match.group(1)) if match else None
        if years is None:
            continue
        experience_candidates.append(
            (_profile_int(item.get("support_document_count"), 1), float(years), item)
        )
    if experience_candidates:
        support = max(value[0] for value in experience_candidates)
        _count, years, source = min(
            (value for value in experience_candidates if value[0] == support),
            key=lambda value: value[1],
        )
        conditions.append(
            {
                "condition_id": "standard-position:experience:years",
                "condition_type": "experience",
                "operator": "at_least",
                "value": f"{years:g} years",
                "resolution_status": "resolved",
                "evidence_refs": _kg_requirement_evidence(source),
            }
        )

    certificate_items = [
        item
        for item in requirements
        if item.get("kind") == "certificate" and _kg_certificate_is_mandatory(item)
    ]
    certificate_values = sorted(
        {
            _safe_text(value)
            for item in certificate_items
            for value in (
                item.get("certificates")
                if isinstance(item.get("certificates"), (list, tuple))
                else (item.get("text"),)
            )
            if _safe_text(value)
        }
    )
    if certificate_values:
        conditions.append(
            {
                "condition_id": "standard-position:certificate:published",
                "condition_type": "certificate",
                "operator": "one_of",
                "value": "|".join(certificate_values),
                "resolution_status": "resolved",
                "evidence_refs": _dedupe_evidence(
                    *(_kg_requirement_evidence(item) for item in certificate_items)
                ),
            }
        )
    return tuple(conditions)


def _kg_position_match_profile(
    raw: object,
    *,
    position_id: str,
    canonical_position_id: str,
    canonical_title: str,
    weights: tuple[dict[str, object], ...] = (),
    requirement_graph: object | None = None,
) -> dict[str, object] | None:
    if not isinstance(raw, dict) or raw.get("profile_state") != "published":
        return None
    required_keys = {
        "position_id",
        "position_code",
        "graph_version_id",
        "skill_relations",
        "taxonomy_version",
        "classification_status",
        "sample_support_status",
    }
    if not required_keys.issubset(raw):
        return None
    evidence_by_skill: dict[str, list[dict[str, object]]] = {}
    for item in raw.get("evidence_summary", []):
        if isinstance(item, dict):
            evidence_by_skill.setdefault(str(item.get("skill_id") or ""), []).append(
                _kg_evidence(item)
            )
    weight_by_skill = {
        str(item["skill_id"]): item
        for item in weights
        if isinstance(item, dict) and item.get("skill_id")
    }
    kg_weights = [
        float(item.get("weight", 1.0))
        for item in raw.get("skill_relations", [])
        if isinstance(item, dict)
    ]
    max_weight = max(
        [*(float(item["weight"]) for item in weight_by_skill.values()), *kg_weights],
        default=1.0,
    )
    required_skill_gate = _required_skill_gate(raw)
    required_skills = []
    preferred_skills = []
    for index, item in enumerate(raw.get("skill_relations", [])):
        if not isinstance(item, dict):
            continue
        skill_id = str(item.get("skill_id") or "")
        skill_name = _safe_text(item.get("skill_name") or skill_id)
        if not skill_id or not skill_name:
            continue
        modality = _safe_text(item.get("modality") or "").lower()
        if modality not in {"required", "preferred", "bonus"}:
            # Unknown/invalid modality is rejected from the formal match profile
            # and remains visible in the full graph/evidence.
            continue
        weight = weight_by_skill.get(skill_id)
        if weight is not None:
            importance = float(weight["weight"]) / max_weight
        else:
            importance = float(item.get("weight", 1.0)) / max_weight
        required_prevalence = _profile_float(item.get("required_prevalence"))
        required_supporting_jd_count = _profile_int(
            item.get("required_supporting_jd_count")
        )
        required_purity = _profile_float(item.get("required_purity"))
        is_required = (
            modality == "required"
            and required_prevalence
            >= float(required_skill_gate["min_required_prevalence"])
            and required_supporting_jd_count
            >= int(required_skill_gate["min_required_prevalence_jd_count"])
            and required_purity
            >= float(required_skill_gate["min_required_purity"])
        )
        raw_level = item.get("required_level") or item.get("proficiency_level")
        required_level = (
            str(raw_level).lower()
            if isinstance(raw_level, str)
            and raw_level.lower()
            in {"unknown", "basic", "working", "proficient", "advanced", "expert"}
            else None
        )
        requirement = {
            "skill_id": skill_id,
            "canonical_name": skill_name,
            "required_level": required_level,
            "importance": importance,
            "resolution_status": "resolved",
            "evidence_refs": tuple(
                evidence_by_skill.get(skill_id, ())[index : index + 1]
                if index < len(evidence_by_skill.get(skill_id, ()))
                else ()
            ),
        }
        if is_required:
            required_skills.append(requirement)
        else:
            preferred_skills.append(requirement)
    hard_conditions = _kg_hard_conditions(raw)
    quality = raw.get("quality") if isinstance(raw.get("quality"), dict) else {}
    responsibility_requirements = _kg_responsibility_requirements(raw)
    responsibility_texts = tuple(
        str(item["text"]) for item in responsibility_requirements
    ) or tuple(
        _safe_text(item.get("text") if isinstance(item, dict) else item)
        for item in raw.get("responsibilities", [])
    )
    quality_id = "kg-graph-" + str(
        raw.get("graph_version_id")
        or raw.get("graph_version")
        or "published-profile"
    )
    quality_status = (
        "trusted"
        if bool(quality.get("publication_gate_passed", True))
        else "review_required"
    )
    assessed_at = (
        str(raw.get("published_at") or datetime.now(timezone.utc).date().isoformat())[:10]
    )
    profile = {
        "schema_version": POSITION_PROFILE_SCHEMA_VERSION,
        "contract_version": POSITION_PROFILE_SCHEMA_VERSION,
        "source_version": (
            f"kg={raw.get('position_id')}:{quality_id}:"
            f"{raw.get('graph_version')}"
        ),
        "created_at": (
            raw.get("published_at")
            or datetime.now(timezone.utc).isoformat()
        ),
        "position_id": position_id,
        "canonical_position_id": canonical_position_id,
        "canonical_title": _safe_text(canonical_title or raw.get("position_name") or position_id),
        "core_responsibilities": responsibility_texts,
        "responsibility_requirements": responsibility_requirements,
        "required_skills": tuple(required_skills),
        "preferred_skills": tuple(preferred_skills),
        "hard_conditions": tuple(hard_conditions),
        "tools": {"values": (), "evidence_refs": (), "availability": "unavailable"},
        "industries": {"values": (), "evidence_refs": (), "availability": "unavailable"},
        "business_scenarios": {
            "values": (),
            "evidence_refs": (),
            "availability": "unavailable",
        },
        "evidence_refs": (
            *(
                item
                for group in evidence_by_skill.values()
                for item in group
            ),
            *(
                evidence
                for requirement in responsibility_requirements
                for evidence in requirement.get("evidence_refs", ())
                if isinstance(evidence, dict)
            ),
        ),
        "quality_context": {
            "snapshot_id": quality_id,
            "status": quality_status,
            "completeness": float(
                quality.get("completeness", 1.0 if raw.get("skill_relations") else 0.5)
            ),
            "assessed_at": assessed_at,
            "evidence_refs": (),
        },
        "trend_context": None,
        "unresolved_items": (),
        "requirement_graph": requirement_graph,
        "graph_mode": "enabled",
        "review_status": "approved",
        "taxonomy_version": str(raw.get("taxonomy_version")),
        "graph_version": str(raw.get("graph_version_id")),
        "position_code": str(raw.get("position_code")),
        "classification_status": str(raw.get("classification_status")),
        "career_level": raw.get("career_level"),
        "leadership_scope": raw.get("leadership_scope"),
        "sample_support_status": str(raw.get("sample_support_status")),
    }
    return profile


def _skill_values(raw: object, importance: str) -> list[dict[str, object]]:
    values = raw if isinstance(raw, list) else []
    result = []
    for index, value in enumerate(values):
        item = value if isinstance(value, dict) else {"skill_id": value, "skill_name": value}
        skill_id = str(item.get("skill_id") or item.get("id") or item.get("name") or value)
        name = _safe_text(item.get("skill_name") or item.get("name") or skill_id)
        result.append(
            {
                "skill_id": skill_id,
                "skill_name": name,
                "weight": float(item.get("weight", 1.0 if importance == "required" else 0.5)),
                "confidence": float(item.get("confidence", 1.0)),
                "importance_level": importance,
                "index": index,
            }
        )
    return result


class SqlAlchemyMatchingContractReader:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def cv_profile(
        self, cv_id: str, snapshot_id: str | None = None
    ) -> dict[str, object] | None:
        with self._session_factory() as session:
            resume = session.get(Resume, cv_id)
            if resume is None:
                return None
            selected_snapshot_id = snapshot_id or resume.validated_cv_snapshot_id
            if selected_snapshot_id is None:
                return None
            snapshot = session.get(ValidatedCVSnapshot, selected_snapshot_id)
            if (
                snapshot is None
                or snapshot.source_cv_version_id != resume.source_cv_version_id
                or snapshot.confirmed_at is None
            ):
                return None
            task = session.get(CVExtractionTask, snapshot.cv_extraction_task_id)
            if (
                task is None
                or task.confirmation_status != "confirmed"
            ):
                return None
            extraction = (
                snapshot.extraction_payload
                if isinstance(snapshot.extraction_payload, dict)
                else {}
            )
            normalized = (
                snapshot.normalized_payload
                if isinstance(snapshot.normalized_payload, dict)
                else {}
            )
            position_row = session.get(CVPositionClassification, cv_id)
            position_classifications = (
                list(position_row.classifications or [])
                if position_row is not None
                and position_row.taxonomy_version
                == "position-taxonomy.v3.0.0"
                else []
            )
        document_id = cv_id
        taxonomy = str(snapshot.taxonomy_version or "jobgraph-skill-catalog.v1")
        normalized_skills = []
        features = []
        capabilities = []
        links = []
        extraction_skills = {
            str(item.get("item_id")): item
            for item in extraction.get("skills", [])
            if isinstance(item, dict)
        }
        for index, row in enumerate(normalized.get("normalized_skills", [])):
            if not isinstance(row, dict) or row.get("resolution_status") != "resolved":
                continue
            source_item_id = str(row.get("source_item_id") or f"skill:{index}")
            source_skill = extraction_skills.get(source_item_id, {})
            name = _safe_text(row.get("source_name") or row.get("canonical_name") or source_item_id)
            skill_id = str(row.get("skill_id") or "")
            canonical_name = _safe_text(row.get("canonical_name") or name)
            if not skill_id or not canonical_name:
                continue
            evidence = _snapshot_evidence(
                source_skill.get("evidence"),
                f"snapshot-skill:{index}",
                name,
            )
            feature_id = _skill_feature_id(document_id, source_item_id)
            normalized_skills.append(
                {
                    "source_item_id": source_item_id,
                    "source_scope": "skills",
                    "source_name": name,
                    "skill_id": skill_id,
                    "canonical_name": canonical_name,
                    # Stored snapshots created before provenance fields were
                    # introduced are trusted legacy records. Mark that origin
                    # explicitly; current upstream contracts remain strict.
                    "normalization_confidence": (
                        row.get("normalization_confidence")
                        if row.get("normalization_confidence") is not None
                        else 1.0
                    ),
                    "resolution_source": (
                        row.get("resolution_source") or "legacy_unspecified"
                    ),
                    "declared_level": source_skill.get("proficiency"),
                    "resolution_status": "resolved",
                    "evidence": [evidence],
                }
            )
            features.append(
                {
                    "feature_id": feature_id,
                    "document_id": document_id,
                    "side": "cv",
                    "feature_type": "skill",
                    "source_object_id": source_item_id,
                    "source_scope": "skills",
                    "canonical_id": skill_id,
                    "canonical_name": canonical_name,
                    "raw_text": name,
                    "vector_text": name,
                    "requirement_modality": None,
                    "candidate_level": source_skill.get("proficiency"),
                    "structured_values": {"aggregation_key": f"skill:{skill_id}"},
                    "resolution_status": "resolved",
                    "evidence_refs": [evidence],
                    "taxonomy_version": taxonomy,
                    "derivation_version": "jobgraph-cv-match-features.v1",
                }
            )
            capabilities.append(
                {
                    "profile_id": f"capability:snapshot:{snapshot.id}:{index}",
                    "document_id": document_id,
                    "aggregation_key": f"skill:{skill_id}",
                    "skill_id": skill_id,
                    "canonical_name": canonical_name,
                    "declared_feature_ids": [feature_id],
                    "experience_skill_feature_ids": [],
                    "evidence_link_ids": [],
                    "declared_level": source_skill.get("proficiency"),
                    "demonstrated_level": "unknown",
                    "demonstrated_level_label": "unknown",
                    "verification_status": "not_observed",
                    "support_confidence": 1.0,
                    "confidence_band": "high",
                    "independent_experience_count": 0,
                    "aggregate_support_score": 0,
                    "evidence_bonus": 0,
                    "resolution_status": "resolved",
                }
            )
        for index, classification in enumerate(position_classifications):
            if not isinstance(classification, dict):
                continue
            raw_text = _safe_text(
                classification.get("raw_text")
                or classification.get("position_code")
                or f"role-{index}"
            )
            evidence = [
                _snapshot_evidence(
                    item,
                    f"snapshot-position:{index}:{evidence_index}",
                    raw_text,
                )
                for evidence_index, item in enumerate(
                    classification.get("feature_evidence_refs") or []
                )
                if isinstance(item, dict)
            ]
            status = str(
                classification.get("classification_status") or "ambiguous"
            )
            features.append(
                {
                    "feature_id": f"{document_id}:position-role:{index}",
                    "document_id": document_id,
                    "side": "cv",
                    "feature_type": "role",
                    "source_object_id": str(
                        classification.get("source_object_id")
                        or f"position-role:{index}"
                    ),
                    "source_scope": str(
                        classification.get("source_scope") or "cv:role"
                    ),
                    "canonical_id": None,
                    "canonical_name": classification.get("position_name"),
                    "raw_text": raw_text,
                    "vector_text": raw_text,
                    "requirement_modality": None,
                    "candidate_level": classification.get("career_level"),
                    "structured_values": {
                        key: classification.get(key)
                        for key in (
                            "position_code",
                            "candidate_positions",
                            "career_level",
                            "leadership_scope",
                            "technology_focus_codes",
                            "industry_context_codes",
                            "observed_skill_domain_codes",
                            "confidence",
                            "classification_status",
                            "review_reason_codes",
                            "evidence_refs",
                            "classification_policy_version",
                        )
                    },
                    "resolution_status": (
                        "resolved"
                        if status in {"resolved", "manually_confirmed"}
                        else "unresolved"
                    ),
                    "evidence_refs": evidence,
                    "taxonomy_version": "position-taxonomy.v3.0.0",
                    "derivation_version": "position-classifier.v3.0",
                }
            )
        projects = [
            {
                "experience_id": str(item.get("entry_id", index)),
                "kind": "project",
                "role": _safe_text(item.get("role") or "contributor"),
                "responsibilities": [
                    _sourced_value(entry, "project responsibility")
                    for entry in item.get("highlights", [])
                ]
                or [
                    _sourced_value(
                        item.get("description"),
                        _safe_text(item.get("name") or "project"),
                    )
                ],
                "business_scenarios": [],
                "tool_source_item_ids": [
                    str(skill.get("item_id") or "")
                    for skill in item.get("tech_stack", [])
                    if isinstance(skill, dict) and skill.get("item_id")
                ],
                "start_date": (item.get("date") or {}).get("start"),
                "end_date": (item.get("date") or {}).get("end"),
                "evidence": [
                    _snapshot_evidence(
                        item.get("evidence"),
                        f"snapshot-project:{index}",
                        _safe_text(item.get("name") or "project"),
                    )
                ],
            }
            for index, item in enumerate(extraction.get("project_experience", []))
            if isinstance(item, dict)
        ]
        structure_education = [
            {
                "education_id": str(item.get("entry_id", index)),
                "degree_level": _safe_text(item.get("degree")),
                "field_of_study": _safe_text(item.get("major")),
                "resolution_status": "resolved",
                "evidence": [
                    _snapshot_evidence(
                        item.get("evidence"),
                        f"snapshot-education:{index}",
                        _safe_text(item.get("school") or "education"),
                    )
                ],
            }
            for index, item in enumerate(extraction.get("education", []))
            if isinstance(item, dict)
        ]
        structure_work = [
            {
                "experience_id": str(item.get("entry_id", index)),
                "kind": "work",
                "role": _safe_text(item.get("position") or item.get("role")),
                "responsibilities": [
                    _sourced_value(entry, "work responsibility")
                    for entry in item.get("responsibilities", [])
                ],
                "business_scenarios": [],
                "tool_source_item_ids": [
                    str(skill.get("item_id") or "")
                    for skill in item.get("tech_stack", [])
                    if isinstance(skill, dict) and skill.get("item_id")
                ],
                "start_date": (item.get("date") or {}).get("start"),
                "end_date": (item.get("date") or {}).get("end"),
                "evidence": [
                    _snapshot_evidence(
                        item.get("evidence"),
                        f"snapshot-work:{index}",
                        _safe_text(item.get("company") or "work"),
                    )
                ],
            }
            for index, item in enumerate(extraction.get("work_experience", []))
            if isinstance(item, dict)
        ]
        normalized_by_source = {
            str(item.get("source_item_id")): item
            for item in normalized.get("normalized_skills", [])
            if isinstance(item, dict) and item.get("resolution_status") == "resolved"
        }
        capability_by_skill = {
            str(item.get("skill_id")): item for item in capabilities if item.get("skill_id")
        }
        source_experiences = [
            *(('project', item) for item in extraction.get("project_experience", []) if isinstance(item, dict)),
            *(('work', item) for item in extraction.get("work_experience", []) if isinstance(item, dict)),
        ]
        for experience_index, (kind, item) in enumerate(source_experiences):
            experience_id = str(item.get("entry_id") or f"{kind}:{experience_index}")
            raw_tasks = (
                item.get("highlights", [])
                if kind == "project"
                else [*item.get("responsibilities", []), *item.get("achievements", [])]
            )
            task_texts = [
                _safe_text(value.get("value") or value.get("text"))
                for value in raw_tasks
                if isinstance(value, dict) and (value.get("value") or value.get("text"))
            ]
            ownership = _ownership_level(
                [
                    _safe_text(item.get("role") or item.get("position")),
                    *task_texts,
                ]
            )
            for skill_index, skill in enumerate(item.get("tech_stack", [])):
                if not isinstance(skill, dict):
                    continue
                source_item_id = str(skill.get("item_id") or "")
                normalized_skill = normalized_by_source.get(source_item_id)
                if not normalized_skill:
                    continue
                skill_id = str(normalized_skill.get("skill_id") or "")
                capability = capability_by_skill.get(skill_id)
                if not skill_id or capability is None:
                    continue
                name = _safe_text(
                    normalized_skill.get("canonical_name")
                    or normalized_skill.get("source_name")
                    or skill_id
                )
                link_id = f"capability-link:{snapshot.id}:{experience_id}:{skill_index}"
                support_evidence = _snapshot_evidence(
                    skill.get("evidence") or item.get("evidence"),
                    f"snapshot-{kind}-skill:{experience_id}:{skill_index}",
                    name,
                )
                demonstrated_level = _OWNERSHIP_TO_LEVEL[ownership]
                links.append(
                    {
                        "link_id": link_id,
                        "document_id": document_id,
                        "aggregation_key": f"skill:{skill_id}",
                        "skill_id": skill_id,
                        "canonical_name": name,
                        "declared_feature_ids": capability["declared_feature_ids"],
                        "experience_skill_feature_id": f"experience-skill:{experience_id}:{skill_index}",
                        "experience_feature_id": experience_id,
                        "supporting_task_feature_ids": [],
                        "support_signals": [
                            "direct_experience_occurrence",
                            f"ownership:{ownership}",
                        ],
                        "support_score": 3,
                        "demonstrated_level": demonstrated_level,
                        "support_confidence": 0.8,
                        "confidence_band": "high",
                        "evidence_refs": [support_evidence],
                        "taxonomy_version": taxonomy,
                        "derivation_version": "jobgraph-capability-projection.v1",
                    }
                )
                capability["experience_skill_feature_ids"].append(
                    f"experience-skill:{experience_id}:{skill_index}"
                )
                capability["evidence_link_ids"].append(link_id)
                capability["demonstrated_level"] = demonstrated_level
                capability["demonstrated_level_label"] = demonstrated_level
                capability["verification_status"] = "supported"
                capability["support_confidence"] = 0.8
                capability["independent_experience_count"] += 1
                capability["aggregate_support_score"] += 3
                capability["evidence_bonus"] = 0.2
        structure_certificates = [
            {
                "credential_id": str(item.get("entry_id", index)),
                "name": _safe_text(item.get("name")),
                "level": _safe_text(item.get("kind")),
                "resolution_status": "resolved",
                "evidence": [
                    _snapshot_evidence(
                        item.get("evidence"),
                        f"snapshot-certificate:{index}",
                        _safe_text(item.get("name") or "certificate"),
                    )
                ],
            }
            for index, item in enumerate(extraction.get("certificates", []))
            if isinstance(item, dict)
        ]
        structure_research_outputs = [
            {
                "output_id": str(item.get("entry_id", index)),
                "output_type": output_type,
                "title": _safe_text(item.get("title") or item.get("name")),
                "status": _safe_text(item.get("status")) or None,
                "role": _safe_text(item.get("author_role") or item.get("role")) or None,
                "order": item.get("author_order") or item.get("inventor_order"),
                "year": item.get("year"),
                "date": _safe_text(item.get("date")) or None,
                "url": _safe_text(item.get("url") or item.get("doi")) or None,
                "evidence": [
                    _snapshot_evidence(
                        item.get("evidence"),
                        f"snapshot-{output_type}:{index}",
                        _safe_text(item.get("title") or item.get("name") or output_type),
                    )
                ],
            }
            for output_type, collection in (
                ("publication", "publications"),
                ("patent", "patents"),
                ("research_output", "research_outputs"),
            )
            for index, item in enumerate(extraction.get(collection, []))
            if isinstance(item, dict) and (item.get("title") or item.get("name"))
        ]
        return _scrub_profile_pii({
            "schema_version": CV_BUNDLE_SCHEMA_VERSION,
            "contract_version": CV_BUNDLE_SCHEMA_VERSION,
            "source_system": "jobgraph-main",
            "source_version": (
                f"snapshot={snapshot.id}:{snapshot.snapshot_revision}:"
            ),
            "created_at": (snapshot.confirmed_at or snapshot.created_at).isoformat(),
            "user_ref": f"subject:{resume.user_id}",
            "verification_snapshot_id": snapshot.id,
            "review_status": "approved",
            "structure_derivation_version": "jobgraph-cv-structure.v1",
            "structure": {
                "document_id": document_id,
                "education": structure_education,
                "work_experiences": structure_work,
                "projects": projects,
                "certificates": structure_certificates,
                "languages": [
                    {
                        "language_code": _safe_text(item.get("language")),
                        "proficiency": _safe_text(item.get("proficiency")),
                        "resolution_status": "resolved",
                        "evidence": [
                            _snapshot_evidence(
                                item.get("evidence"),
                                f"snapshot-language:{index}",
                                _safe_text(item.get("language") or "language"),
                            )
                        ],
                    }
                    for index, item in enumerate(extraction.get("languages", []))
                    if isinstance(item, dict)
                ],
                # Research output facts remain visible to the matching service,
                # while the v1 scorer intentionally assigns no implicit points.
                "research_outputs": structure_research_outputs,
                "evidence": [],
            },
            "normalization": {
                "document_id": document_id,
                "taxonomy_version": taxonomy,
                "derivation_version": "jobgraph-cv-normalization.v1",
                "skills": normalized_skills,
            },
            "match_features": {
                "document_id": document_id,
                "as_of_date": (snapshot.confirmed_at or snapshot.created_at).date().isoformat(),
                "taxonomy_version": taxonomy,
                "derivation_version": "jobgraph-cv-match-features.v1",
                "features": features,
            },
            "capabilities": {
                "document_id": document_id,
                "taxonomy_version": taxonomy,
                "derivation_version": "jobgraph-capability-projection.v1",
                "profiles": capabilities,
                "evidence_links": links,
            },
            "unresolved_items": [],
        })

    def is_cv_owner(self, subject_id: str, cv_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.query(Resume.id)
                .filter(Resume.id == cv_id, Resume.user_id == subject_id)
                .first()
                is not None
            )

    def has_application_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, position_id: str
    ) -> bool:
        with self._session_factory() as session:
            return (
                session.query(CandidateSubmission.id)
                .join(EnterpriseJob, EnterpriseJob.id == CandidateSubmission.enterprise_job_id)
                .join(Enterprise, Enterprise.id == CandidateSubmission.enterprise_id)
                .filter(
                    Enterprise.id == tenant_id,
                    Enterprise.owner_user_id == subject_id,
                    Enterprise.status == "active",
                    EnterpriseJob.enterprise_id == Enterprise.id,
                    CandidateSubmission.enterprise_id == EnterpriseJob.enterprise_id,
                    CandidateSubmission.resume_id == cv_id,
                    CandidateSubmission.status == "submitted",
                    EnterpriseJob.standard_position_id == position_id,
                    EnterpriseJob.status.in_(("published", "paused")),
                )
                .first()
                is not None
            )

    def has_enterprise_job_grant(
        self, subject_id: str, tenant_id: str, cv_id: str, enterprise_job_id: str
    ) -> bool:
        with self._session_factory() as session:
            return (
                session.query(CandidateSubmission.id)
                .join(EnterpriseJob, EnterpriseJob.id == CandidateSubmission.enterprise_job_id)
                .join(Enterprise, Enterprise.id == CandidateSubmission.enterprise_id)
                .filter(
                    Enterprise.id == tenant_id,
                    Enterprise.status == "active",
                    CandidateSubmission.enterprise_job_id == enterprise_job_id,
                    CandidateSubmission.resume_id == cv_id,
                    CandidateSubmission.status == "submitted",
                    EnterpriseJob.status.in_(("published", "paused")),
                )
                .first()
                is not None
            )

    def position_reference(self, position_id: str) -> dict[str, str] | None:
        """Resolve the main-system UUID to the KG-owned taxonomy identity."""

        with self._session_factory() as session:
            position = (
                session.query(StandardPosition)
                .filter(
                    or_(
                        StandardPosition.id == position_id,
                        StandardPosition.position_code == position_id,
                    )
                )
                .one_or_none()
            )
            if (
                position is None
                or not position.position_code
                or position.taxonomy_version != "position-taxonomy.v3.0.0"
                or position.lifecycle_status != "active"
            ):
                return None
            return {
                "main_system_position_id": position.id,
                "position_code": position.position_code,
                "position_name": position.position_name,
            }

    def position_references(
        self, position_ids: tuple[str, ...]
    ) -> dict[str, dict[str, str]]:
        if not position_ids:
            return {}
        requested = set(position_ids)
        with self._session_factory() as session:
            rows = (
                session.query(StandardPosition)
                .filter(
                    or_(
                        StandardPosition.id.in_(requested),
                        StandardPosition.position_code.in_(requested),
                    ),
                    StandardPosition.position_code.is_not(None),
                    StandardPosition.taxonomy_version == "position-taxonomy.v3.0.0",
                    StandardPosition.lifecycle_status == "active",
                )
                .all()
            )
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            reference = {
                "main_system_position_id": row.id,
                "position_code": row.position_code,
                "position_name": row.position_name,
            }
            if row.id in requested:
                result[row.id] = reference
            if row.position_code in requested:
                result[row.position_code] = reference
        return result

    def enterprise_job_reference(self, job_id: str) -> dict[str, object] | None:
        with self._session_factory() as session:
            job = session.get(EnterpriseJob, job_id)
            if job is None:
                return None
            weights = (
                session.query(EnterpriseJobSkillWeight)
                .filter(EnterpriseJobSkillWeight.enterprise_job_id == job_id)
                .order_by(EnterpriseJobSkillWeight.skill_id.asc())
                .all()
            )
            return {
                "job_id": job.id,
                "title": job.title,
                "standard_position_id": job.standard_position_id,
                "jd_text": job.jd_text,
                "requirement_graph": job.requirement_graph,
                "location": job.location,
                "employment_type": job.employment_type,
                "headcount": job.headcount,
                "salary_min": job.salary_min,
                "salary_max": job.salary_max,
                "salary_unit": job.salary_unit,
                "updated_at": job.updated_at.isoformat(),
                "weights": [
                    {
                        "skill_id": w.skill_id,
                        "weight": float(w.weight),
                        "is_required": bool(w.is_required),
                        "is_bonus": bool(w.is_bonus),
                    }
                    for w in weights
                ],
            }


class KnowledgeGraphMatchingContractReader:
    """Use KG for position profiles while retaining main-owned CV/grant contracts."""

    def __init__(self, main_reader, knowledge_graph_client) -> None:
        self._main_reader = main_reader
        self._knowledge_graph_client = knowledge_graph_client

    def position_profile(self, position_id: str) -> dict[str, object] | None:
        reference = self._main_reader.position_reference(position_id)
        if reference is None:
            return None
        position_code = reference["position_code"]
        try:
            value = self._knowledge_graph_client.position_profile(position_code).data
        except KnowledgeGraphError as exc:
            raise MatchingContractUnavailable(
                "Knowledge graph position profile is unavailable"
            ) from exc
        profile = _standard_position_match_profile(
            value,
            position_id=position_id,
            canonical_position_id=position_code,
            canonical_title=reference["position_name"],
        )
        return _scrub_profile_pii(profile) if profile is not None else None

    def position_profiles_batch(
        self, position_ids: tuple[str, ...]
    ) -> dict[str, dict[str, object] | StandardPositionProfileInsufficient | None]:
        references = self._main_reader.position_references(position_ids)
        by_code = {
            reference["position_code"]: reference
            for reference in references.values()
        }
        raw_by_code: dict[str, dict[str, object]] = {}
        codes = sorted(by_code)
        for offset in range(0, len(codes), 100):
            batch = codes[offset:offset + 100]
            try:
                payload = self._knowledge_graph_client.position_profiles_batch(
                    batch,
                    page=1,
                    page_size=len(batch),
                ).data
            except KnowledgeGraphError as exc:
                raise MatchingContractUnavailable(
                    "Knowledge graph position profile batch is unavailable"
                ) from exc
            if not isinstance(payload, dict):
                raise MatchingContractUnavailable(
                    "Knowledge graph position profile batch is unavailable"
                )
            for item in payload.get("items", ()):
                if isinstance(item, dict) and item.get("position_id"):
                    raw_by_code[str(item["position_id"])] = item

        result: dict[
            str, dict[str, object] | StandardPositionProfileInsufficient | None
        ] = {}
        for position_id in position_ids:
            reference = references.get(position_id)
            if reference is None:
                result[position_id] = None
                continue
            raw = raw_by_code.get(reference["position_code"])
            if raw is None:
                result[position_id] = None
                continue
            try:
                profile = _standard_position_match_profile(
                    raw,
                    position_id=reference["main_system_position_id"],
                    canonical_position_id=reference["position_code"],
                    canonical_title=reference["position_name"],
                )
                result[position_id] = (
                    _scrub_profile_pii(profile) if profile is not None else None
                )
            except StandardPositionProfileInsufficient as exc:
                result[position_id] = exc
        return result

    def enterprise_job_profile(self, job_id: str) -> dict[str, object] | None:
        reference = self._main_reader.enterprise_job_reference(job_id)
        if reference is None:
            return None
        standard_position_id = reference.get("standard_position_id")
        if not isinstance(standard_position_id, str) or not standard_position_id:
            return None
        position_reference = self._main_reader.position_reference(
            standard_position_id
        )
        if position_reference is None:
            return None
        try:
            value = self._knowledge_graph_client.position_profile(
                position_reference["position_code"]
            ).data
        except KnowledgeGraphError as exc:
            raise MatchingContractUnavailable(
                "Knowledge graph position profile is unavailable"
            ) from exc
        if not isinstance(value, dict):
            return None
        weights = reference.get("weights") or ()
        base = _kg_position_match_profile(
            value,
            position_id=f"enterprise_job:{reference['job_id']}",
            canonical_position_id=position_reference["position_code"],
            canonical_title=str(reference.get("title") or value.get("position_name") or job_id),
            weights=tuple(item for item in weights if isinstance(item, dict)),
            requirement_graph=reference.get("requirement_graph"),
        )
        if base is None:
            return None
        return _scrub_profile_pii(_enterprise_job_match_profile(base, reference))

    def skill_relations(self, skill_ids: tuple[str, ...]) -> dict[str, object]:
        value = self._knowledge_graph_client.skill_relations_batch(skill_ids).data
        if not isinstance(value, dict):
            raise RuntimeError("Knowledge Graph returned an invalid skill relation contract")
        return value

    def __getattr__(self, name):
        return getattr(self._main_reader, name)
