"""DeepSeek LLM semantic candidate source using the shared client."""

from __future__ import annotations

import json
from typing import Any

from jobgraph_contracts.deepseek import (
    DeepSeekClient,
    InvalidJSONError,
    MissingAPIKeyError,
)
from openai import APIConnectionError, APITimeoutError, RateLimitError

from app.domain.deepseek_candidates import (
    LLMSemanticCandidateBatch,
    RawDeepSeekSkillCandidate,
)
from app.domain.privacy import find_pii
from app.domain.profiles import CVMatchProfile, PositionMatchProfile
from app.domain.relation_matching import _capability_evidence, _verified_capabilities
from app.ports.llm_semantic_candidates import (
    LLMSemanticCandidateError,
)


def _safe_quote(value: str | None) -> str | None:
    if not value:
        return None
    text = " ".join(value.split())
    return None if find_pii(text) else text


class DeepSeekSemanticCandidateSource:
    def __init__(
        self,
        *,
        model: str,
        algorithm_version: str,
        timeout_seconds: int = 120,
        max_candidates: int = 10,
    ) -> None:
        if not model.strip():
            raise ValueError("DeepSeek semantic model is required")
        if not algorithm_version.strip():
            raise ValueError("DeepSeek semantic algorithm version is required")
        if timeout_seconds <= 0 or not 1 <= max_candidates <= 20:
            raise ValueError("DeepSeek semantic timeout or candidate limit is invalid")
        self._model = model
        self._algorithm_version = algorithm_version
        self._timeout = timeout_seconds
        self._max_candidates = max_candidates

    def generate_candidates(
        self,
        *,
        cv: CVMatchProfile,
        position: PositionMatchProfile,
    ) -> LLMSemanticCandidateBatch:
        capabilities = _verified_capabilities(cv)
        candidate_skills = []
        for skill_id, capability in sorted(capabilities.items()):
            evidence = _capability_evidence(cv, capability)
            quote = _safe_quote(evidence[0].quote) if evidence else None
            candidate_skills.append(
                {
                    "skill_id": skill_id,
                    "skill_name": capability.canonical_name or skill_id,
                    "demonstrated_level": capability.demonstrated_level,
                    "evidence_quote": quote,
                }
            )
        requirements = []
        for importance, group in (
            ("required", position.required_skills),
            ("bonus", position.preferred_skills),
        ):
            for item in group:
                if item.skill_id is None:
                    continue
                quote = (
                    _safe_quote(item.evidence_refs[0].quote)
                    if item.evidence_refs
                    else None
                )
                requirements.append(
                    {
                        "requirement_id": f"{importance}:{item.skill_id}",
                        "skill_id": item.skill_id,
                        "skill_name": item.canonical_name or item.skill_id,
                        "importance": item.importance,
                        "evidence_quote": quote,
                    }
                )
        system_prompt = (
            "你是技能语义候选召回器。你的唯一职责是从候选人已核验技能中提出与岗位"
            "技能要求可能等价的候选，并给出原文依据。禁止评分，禁止输出岗位或候选人不存在的技能。"
            "只输出 JSON object，格式："
            '{"candidates":[{"requirement_id":"...","required_skill_id":"...",'
            '"required_skill_name":"...","candidate_skill_id":"...",'
            '"candidate_skill_name":"...","proposed_relation_type":"related",'
            '"rationale":"...","position_quote":"...","candidate_quote":"..."}]}。'
            "proposed_relation_type 只能是 exact、equivalent、related、transferable 或 unknown。"
        )
        user_prompt = json.dumps(
            {
                "task": "召回技能语义候选",
                "requirements": requirements,
                "candidate_skills": candidate_skills,
                "max_candidates": self._max_candidates,
            },
            ensure_ascii=False,
        )
        try:
            result = DeepSeekClient(model=self._model, timeout=self._timeout).extract(
                system_prompt, user_prompt
            )
        except MissingAPIKeyError as exc:
            raise LLMSemanticCandidateError(
                "DEEPSEEK_API_KEY_MISSING", str(exc)
            ) from exc
        except InvalidJSONError as exc:
            raise LLMSemanticCandidateError(
                "DEEPSEEK_RESPONSE_INVALID", "DeepSeek semantic candidate JSON is invalid"
            ) from exc
        except (APIConnectionError, APITimeoutError, RateLimitError) as exc:
            raise LLMSemanticCandidateError(
                "DEEPSEEK_API_UNAVAILABLE", "DeepSeek semantic candidate API failed"
            ) from exc
        except Exception as exc:  # pragma: no cover - final adapter boundary
            raise LLMSemanticCandidateError(
                "DEEPSEEK_CANDIDATE_UNAVAILABLE",
                f"DeepSeek semantic candidate failed: {type(exc).__name__}",
            ) from exc
        raw_candidates = self._parse_candidates(result.data)
        return LLMSemanticCandidateBatch(
            model=self._model,
            algorithm_version=self._algorithm_version,
            candidates=raw_candidates,
        )

    @staticmethod
    def _parse_candidates(data: dict[str, Any]) -> tuple[RawDeepSeekSkillCandidate, ...]:
        values = data.get("candidates") if isinstance(data, dict) else None
        if not isinstance(values, list):
            raise LLMSemanticCandidateError(
                "DEEPSEEK_RESPONSE_INVALID",
                "DeepSeek semantic candidate response has no candidates list",
            )
        try:
            return tuple(RawDeepSeekSkillCandidate.model_validate(item) for item in values)
        except Exception as exc:
            raise LLMSemanticCandidateError(
                "DEEPSEEK_RESPONSE_INVALID",
                "DeepSeek semantic candidate schema is invalid",
            ) from exc


__all__ = ["DeepSeekSemanticCandidateSource"]
