from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from src.deepseek_client import DeepSeekResult


def valid_payload(quote: str = "熟练使用 Python") -> dict[str, Any]:
    return {
        "job_title": None,
        "responsibilities": [],
        "requirements": [
            {
                "kind": "skill",
                "modality": "required",
                "items": [{"name": "Python", "item_type": "programming_language"}],
                "proficiency": "proficient",
                "evidence": {"source_id": "src_0001", "quote": quote},
            }
        ],
        "company_facts": [],
        "employment_facts": [],
    }


class FakePositionClassifier:
    def __init__(self, *, status: str = "resolved"):
        self.status = status
        self.profiles: list[dict[str, Any]] = []

    def classify(
        self,
        profiles: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        self.profiles = deepcopy(profiles)
        decisions = {}
        for profile in profiles:
            resolved = self.status == "resolved"
            decisions[profile["document_id"]] = {
                "document_id": profile["document_id"],
                "classification_status": self.status,
                "position_code": "BACKEND_ENGINEER" if resolved else None,
                "candidate_positions": (
                    [
                        {
                            "position_code": "BACKEND_ENGINEER",
                            "score": 0.91,
                        }
                    ]
                    if resolved
                    else []
                ),
                "career_level": "mid",
                "leadership_scope": "none",
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": [],
                "confidence": 0.91 if resolved else 0.4,
                "review_reason_codes": (
                    [] if resolved else ["CATALOG_GAP"]
                ),
                "evidence_refs": (
                    list(profile["available_evidence_refs"][:1])
                    if resolved
                    else []
                ),
            }
        return decisions

    def materialize(
        self,
        decision: dict[str, Any],
        *,
        source_title: str,
    ) -> dict[str, Any]:
        resolved = decision["classification_status"] == "resolved"
        return {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": source_title,
            "position_id": None,
            "position_code": "BACKEND_ENGINEER" if resolved else None,
            "position_name": "后端开发工程师" if resolved else None,
            "family_code": "SOFTWARE_ENGINEERING" if resolved else None,
            "family_name": "软件研发" if resolved else None,
            "candidate_positions": decision["candidate_positions"],
            "career_level": decision["career_level"],
            "leadership_scope": decision["leadership_scope"],
            "technology_focus_codes": decision[
                "technology_focus_codes"
            ],
            "industry_context_codes": decision[
                "industry_context_codes"
            ],
            "observed_skill_domain_codes": decision[
                "observed_skill_domain_codes"
            ],
            "confidence": decision["confidence"],
            "classification_status": decision[
                "classification_status"
            ],
            "review_reason_codes": decision["review_reason_codes"],
            "evidence_refs": decision["evidence_refs"],
            "classification_policy_version": "position-classifier.v3.0",
        }


class FakeClient:
    def __init__(self, payload: dict[str, Any] | None = None):
        self.payload = payload or valid_payload()
        self.calls = 0

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        self.calls += 1
        payload = deepcopy(self.payload)
        return DeepSeekResult(data=payload, raw_response=json.dumps(payload, ensure_ascii=False))


class TimeoutClient:
    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        raise TimeoutError("provider details must stay internal")


class RaisingClient:
    def __init__(self, error: BaseException):
        self.error = error

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        raise self.error


class RecordingClient(FakeClient):
    def __init__(self, payload: dict[str, Any] | None = None):
        super().__init__(payload)
        self.user_prompts: list[str] = []

    def extract(self, system_prompt: str, user_prompt: str) -> DeepSeekResult:
        self.user_prompts.append(user_prompt)
        return super().extract(system_prompt, user_prompt)
