from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Protocol

from jobgraph_contracts.deepseek import DeepSeekClient
from jobgraph_contracts.normalization_v2 import JobClassification


SYSTEM_PROMPT = """你是 position-taxonomy.v3 岗位多维分类审查员。

先判断是否属于目录范围，再在全部 standard_positions 中比较 Top-K，不得先锁定岗位族，也不得强制选择岗位。

判定原则：
1. 岗位身份只由核心职责和交付物决定；职级、管理范围、技术方向和行业独立输出。
2. classification_status 只能是 resolved、ambiguous、out_of_scope、catalog_gap。
3. resolved 必须同时满足充分 Evidence、Top-1 分数达到阈值且 Top-1/Top-2 差值充分。
4. 多个合理候选接近时输出 ambiguous；范围内无合适岗位输出 catalog_gap；范围外输出 out_of_scope。
5. position_code 仅在 resolved 时必填；其他状态必须为 null。candidate_positions 保留最多 3 个目录内候选及分数。
6. career_level、leadership_scope、technology_focus_codes、industry_context_codes 必须使用给定枚举。
7. observed_skill_domain_codes 只能来自输入记录的 skill_domains，不得复制岗位族允许领域。
8. evidence_refs 只能引用输入 available_evidence_refs；没有完整证据不得 resolved。

只输出紧凑JSON对象：
{"decisions":[{"document_id":str,"classification_status":str,"position_code":str|null,"candidate_positions":[{"position_code":str,"score":number}],"career_level":str,"leadership_scope":str,"technology_focus_codes":list[str],"industry_context_codes":list[str],"observed_skill_domain_codes":list[str],"confidence":number,"review_reason_codes":list[str],"evidence_refs":list[str]}]}

confidence 和候选 score 范围0到1。不要输出Markdown、解释或额外字段。"""


class PositionModelClient(Protocol):
    def extract(self, system_prompt: str, user_prompt: str): ...


def load_position_catalog(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "position-taxonomy-catalog.v3"
    ):
        raise ValueError("catalog schema must be position-taxonomy-catalog.v3")
    families = payload.get("families")
    positions = payload.get("positions")
    if not isinstance(families, list) or not isinstance(positions, list):
        raise ValueError("catalog families and positions must be lists")
    family_by_code: dict[str, dict[str, Any]] = {}
    for family in families:
        code = family.get("code") if isinstance(family, dict) else None
        domains = (
            family.get("allowed_skill_domains")
            if isinstance(family, dict)
            else None
        )
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(domains, list)
            or not domains
        ):
            raise ValueError(f"invalid position family: {family!r}")
        if code in family_by_code:
            raise ValueError(f"duplicate position family: {code}")
        family_by_code[code] = family
    position_by_code: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for position in positions:
        if not isinstance(position, dict):
            raise ValueError("position entry must be an object")
        code = position.get("code")
        name = position.get("name")
        family_code = position.get("family_code")
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(name, str)
            or not name
        ):
            raise ValueError(f"invalid standard position: {position!r}")
        if family_code not in family_by_code:
            raise ValueError(
                f"position {code} references missing family {family_code}"
            )
        if code in position_by_code or name in names:
            raise ValueError(f"duplicate standard position: {code}")
        position_by_code[code] = position
        names.add(name)
    for field in (
        "career_levels",
        "leadership_scopes",
        "technology_focus_codes",
        "industry_context_codes",
    ):
        if not isinstance(payload.get(field), list):
            raise ValueError(f"catalog {field} must be a list")
    return {
        **payload,
        "_family_by_code": family_by_code,
        "_position_by_code": position_by_code,
    }


def build_jd_position_profile(
    extraction: dict[str, Any],
    normalized: dict[str, Any],
) -> dict[str, Any]:
    job_title = extraction.get("job_title") or {}
    title = job_title.get("value") or job_title.get("text")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(
            f"job title missing: {extraction.get('document_id')}"
        )
    title_evidence = job_title.get("evidence")
    title_evidence_ref = (
        title_evidence.get("source_id")
        if isinstance(title_evidence, dict)
        else None
    )
    responsibilities = []
    for item in extraction.get("responsibilities", []):
        if not isinstance(item, dict):
            continue
        text = item.get("action") or item.get("text")
        evidence = item.get("evidence")
        if not isinstance(text, str) or not text.strip():
            quote = (
                evidence.get("quote")
                if isinstance(evidence, dict)
                else None
            )
            text = quote if isinstance(quote, str) else None
        if not isinstance(text, str) or not text.strip():
            continue
        evidence_ref = item.get("requirement_id")
        if not evidence_ref and isinstance(evidence, dict):
            evidence_ref = evidence.get("source_id")
        responsibilities.append(
            {
                "text": text.strip(),
                "evidence_ref": (
                    str(evidence_ref) if evidence_ref is not None else None
                ),
            }
        )
    skills: list[str] = []
    domain_counts: Counter[str] = Counter()
    for requirement in normalized.get("normalized_requirements", []):
        if not isinstance(requirement, dict):
            continue
        normalized_skills = requirement.get(
            "normalized_skills",
            requirement.get("skills", []),
        )
        for skill in normalized_skills:
            if not isinstance(skill, dict):
                continue
            name = skill.get("canonical_name") or skill.get("source_name")
            if (
                isinstance(name, str)
                and name.strip()
                and name.strip() not in skills
            ):
                skills.append(name.strip())
            for relation in skill.get("classifications", []):
                if (
                    isinstance(relation, dict)
                    and relation.get("facet") == "domain"
                    and isinstance(relation.get("code"), str)
                ):
                    domain_counts[relation["code"]] += 1
    responsibilities = responsibilities[:8]
    available_evidence_refs = (
        [str(title_evidence_ref)]
        if title_evidence_ref is not None
        else []
    )
    for item in responsibilities:
        evidence_ref = item["evidence_ref"]
        if (
            evidence_ref is not None
            and evidence_ref not in available_evidence_refs
        ):
            available_evidence_refs.append(evidence_ref)
    return {
        "document_id": str(extraction["document_id"]),
        "title": title.strip(),
        "responsibilities": responsibilities,
        "skills": skills[:24],
        "skill_domains": [
            code for code, _ in domain_counts.most_common(8)
        ],
        "available_evidence_refs": available_evidence_refs,
    }


class PositionClassifier:
    def __init__(
        self,
        *,
        catalog_path: str | Path,
        model: str = "deepseek-v4-flash",
        max_attempts: int = 3,
        timeout_seconds: int = 240,
        transport_attempts: int = 3,
        json_attempts: int = 2,
        client: PositionModelClient | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("position classification max_attempts must be positive")
        self.catalog = load_position_catalog(catalog_path)
        self.model = model
        self.max_attempts = max_attempts
        self.client = client or DeepSeekClient(
            model=model,
            timeout=timeout_seconds,
            transport_attempts=transport_attempts,
            json_attempts=json_attempts,
        )

    def classify(
        self,
        profiles: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        if not profiles:
            return {}
        expected = {str(item["document_id"]) for item in profiles}
        if len(expected) != len(profiles):
            raise ValueError("position profiles contain duplicate document_id")
        deterministic_decisions: dict[str, dict[str, Any]] = {}
        unresolved_profiles: list[dict[str, Any]] = []
        for profile in profiles:
            deterministic = self._deterministic_exact_decision(profile)
            if deterministic is None:
                unresolved_profiles.append(profile)
            else:
                deterministic_decisions[str(profile["document_id"])] = deterministic
        if not unresolved_profiles:
            return deterministic_decisions
        unresolved_expected = {
            str(item["document_id"]) for item in unresolved_profiles
        }
        validation_failure: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            payload = {
                "standard_positions": self._compact_catalog(),
                "career_levels": self.catalog["career_levels"],
                "leadership_scopes": self.catalog["leadership_scopes"],
                "technology_focus_codes": self.catalog[
                    "technology_focus_codes"
                ],
                "industry_context_codes": self.catalog[
                    "industry_context_codes"
                ],
                "jds": unresolved_profiles,
            }
            if validation_failure:
                payload["previous_response_validation_failure"] = (
                    validation_failure
                )
                payload["correction_required"] = (
                    "重新输出整个批次，并严格使用给定枚举值"
                )
            try:
                response = self.client.extract(
                    SYSTEM_PROMPT,
                    json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ).data
                raw_decisions = response.get("decisions")
                if not isinstance(raw_decisions, list):
                    raise ValueError("DeepSeek response has no decisions list")
                llm_decisions = self._validate_decisions(
                    raw_decisions,
                    unresolved_expected,
                    {
                        str(item["document_id"]): item
                        for item in unresolved_profiles
                    },
                )
                return {**deterministic_decisions, **llm_decisions}
            except ValueError as exc:
                validation_failure = str(exc)
                if attempt == self.max_attempts:
                    raise
        raise AssertionError("unreachable")

    def classify_with_metadata(
        self,
        profiles: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
        deterministic_count = sum(
            self._deterministic_exact_decision(profile) is not None
            for profile in profiles
        )
        decisions = self.classify(profiles)
        llm_profile_count = len(profiles) - deterministic_count
        return decisions, {
            "profile_count": len(profiles),
            "deterministic_count": deterministic_count,
            "llm_profile_count": llm_profile_count,
            # The interactive CV service configures a single bounded attempt.
            "attempt_count": 1 if llm_profile_count else 0,
        }

    def _deterministic_exact_decision(
        self, profile: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Resolve only unique, evidence-backed exact catalog titles locally."""

        title = profile.get("title")
        evidence_refs = profile.get("available_evidence_refs")
        if (
            not isinstance(title, str)
            or not title.strip()
            or not isinstance(evidence_refs, list)
            or not evidence_refs
        ):
            return None
        normalized_title = title.strip().casefold()
        matches = []
        for position in self.catalog["positions"]:
            names = [position["name"], *position.get("aliases", [])]
            if any(
                isinstance(name, str)
                and name.strip().casefold() == normalized_title
                for name in names
            ):
                matches.append(position)
        if len(matches) != 1:
            return None
        position = matches[0]
        observed_domains = [
            value
            for value in profile.get("skill_domains", [])
            if isinstance(value, str)
        ]
        return {
            "document_id": str(profile["document_id"]),
            "classification_status": "resolved",
            "position_code": position["code"],
            "candidate_positions": [
                {"position_code": position["code"], "score": 1.0}
            ],
            "career_level": "unspecified",
            "leadership_scope": "none",
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": observed_domains,
            "confidence": 1.0,
            "review_reason_codes": [],
            "evidence_refs": [str(value) for value in evidence_refs],
        }

    def materialize(
        self,
        decision: dict[str, Any],
        *,
        source_title: str,
    ) -> dict[str, Any]:
        position = (
            self.catalog["_position_by_code"][decision["position_code"]]
            if decision["position_code"] is not None
            else None
        )
        family = (
            self.catalog["_family_by_code"][position["family_code"]]
            if position is not None
            else None
        )
        return JobClassification(
            taxonomy_version=self.catalog["catalog_version"],
            source_title=source_title,
            position_code=position["code"] if position else None,
            position_name=position["name"] if position else None,
            family_code=family["code"] if family else None,
            family_name=family["name"] if family else None,
            candidate_positions=decision["candidate_positions"],
            career_level=decision["career_level"],
            leadership_scope=decision["leadership_scope"],
            technology_focus_codes=decision["technology_focus_codes"],
            industry_context_codes=decision["industry_context_codes"],
            observed_skill_domain_codes=decision[
                "observed_skill_domain_codes"
            ],
            confidence=decision["confidence"],
            classification_status=decision["classification_status"],
            review_reason_codes=decision["review_reason_codes"],
            evidence_refs=decision["evidence_refs"],
            classification_policy_version=self.catalog[
                "classification_policy_version"
            ],
        ).model_dump(mode="json")

    def _compact_catalog(self) -> list[dict[str, Any]]:
        families = self.catalog["_family_by_code"]
        return [
            {
                "code": item["code"],
                "name": item["name"],
                "family_code": item["family_code"],
                "family_name": families[item["family_code"]]["name"],
                "definition": item["definition"],
                "skill_domains": families[item["family_code"]][
                    "allowed_skill_domains"
                ],
            }
            for item in self.catalog["positions"]
        ]

    def _validate_decisions(
        self,
        raw: list[dict[str, Any]],
        expected_ids: set[str],
        profiles_by_id: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        decisions: dict[str, dict[str, Any]] = {}
        position_codes = set(self.catalog["_position_by_code"])
        career_levels = set(self.catalog["career_levels"])
        leadership_scopes = set(self.catalog["leadership_scopes"])
        technology_codes = set(self.catalog["technology_focus_codes"])
        industry_codes = set(self.catalog["industry_context_codes"])
        statuses = {"resolved", "ambiguous", "out_of_scope", "catalog_gap"}
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("decision must be an object")
            document_id = item.get("document_id")
            if document_id not in expected_ids or document_id in decisions:
                raise ValueError(
                    f"unexpected or duplicate decision: {document_id}"
                )
            status = item.get("classification_status")
            position_code = item.get("position_code")
            candidates = item.get("candidate_positions")
            if status not in statuses:
                raise ValueError(
                    f"invalid classification_status for {document_id}: {status}"
                )
            if status == "resolved" and position_code not in position_codes:
                raise ValueError(
                    f"unknown position_code for {document_id}: {position_code}"
                )
            if status != "resolved" and position_code is not None:
                raise ValueError(
                    f"unresolved decision must not bind position_code: {document_id}"
                )
            if not isinstance(candidates, list) or len(candidates) > 3:
                raise ValueError(
                    f"invalid candidate_positions for {document_id}"
                )
            for candidate in candidates:
                if (
                    not isinstance(candidate, dict)
                    or candidate.get("position_code") not in position_codes
                    or not isinstance(candidate.get("score"), (int, float))
                    or isinstance(candidate.get("score"), bool)
                    or not 0 <= candidate["score"] <= 1
                ):
                    raise ValueError(f"invalid candidate for {document_id}")
            career_level = item.get("career_level")
            leadership_scope = item.get("leadership_scope")
            technologies = item.get("technology_focus_codes")
            industries = item.get("industry_context_codes")
            observed_domains = item.get("observed_skill_domain_codes")
            confidence = item.get("confidence")
            review_reasons = item.get("review_reason_codes")
            evidence_refs = item.get("evidence_refs")
            if (
                career_level not in career_levels
                or leadership_scope not in leadership_scopes
            ):
                raise ValueError(
                    f"invalid level or leadership scope for {document_id}"
                )
            if not isinstance(technologies, list) or any(
                code not in technology_codes for code in technologies
            ):
                raise ValueError(
                    f"invalid technology_focus_codes for {document_id}"
                )
            if not isinstance(industries, list) or any(
                code not in industry_codes for code in industries
            ):
                raise ValueError(
                    f"invalid industry_context_codes for {document_id}"
                )
            if not isinstance(observed_domains, list):
                raise ValueError(
                    f"invalid observed_skill_domain_codes for {document_id}"
                )
            if (
                not isinstance(confidence, (int, float))
                or isinstance(confidence, bool)
                or not 0 <= confidence <= 1
            ):
                raise ValueError(
                    f"invalid confidence for {document_id}: {confidence}"
                )
            if not isinstance(review_reasons, list) or not isinstance(
                evidence_refs,
                list,
            ):
                raise ValueError(f"incomplete decision for {document_id}")
            profile = profiles_by_id[str(document_id)]
            if not set(observed_domains).issubset(
                set(profile.get("skill_domains") or [])
            ):
                raise ValueError(
                    f"observed skill domains exceed input evidence for {document_id}"
                )
            if not set(evidence_refs).issubset(
                set(profile.get("available_evidence_refs") or [])
            ):
                raise ValueError(
                    f"classification evidence refs exceed input evidence for {document_id}"
                )
            decision = {
                "document_id": str(document_id),
                "classification_status": status,
                "position_code": position_code,
                "candidate_positions": candidates,
                "career_level": career_level,
                "leadership_scope": leadership_scope,
                "technology_focus_codes": technologies,
                "industry_context_codes": industries,
                "observed_skill_domain_codes": observed_domains,
                "confidence": round(float(confidence), 4),
                "review_reason_codes": review_reasons,
                "evidence_refs": evidence_refs,
            }
            self.materialize(
                decision,
                source_title=str(profile["title"]),
            )
            decisions[str(document_id)] = decision
        if set(decisions) != expected_ids:
            raise ValueError(
                "batch decision set differs: "
                f"missing={sorted(expected_ids - set(decisions))}"
            )
        return decisions
