from __future__ import annotations

from types import SimpleNamespace

import pytest

from jobgraph_contracts.position_classifier import (
    PositionClassifier,
    build_jd_position_profile,
    load_position_catalog,
)


CATALOG_PATH = "config/position_taxonomy_catalog.v3.json"


def _profile() -> dict:
    return {
        "document_id": "jd-1",
        "title": "后端开发工程师",
        "responsibilities": [],
        "skills": ["Python"],
        "skill_domains": ["software_engineering"],
        "available_evidence_refs": ["src_0001"],
    }


def _decision(*, evidence_refs=None) -> dict:
    return {
        "decisions": [
            {
                "document_id": "jd-1",
                "classification_status": "resolved",
                "position_code": "BACKEND_ENGINEER",
                "candidate_positions": [
                    {
                        "position_code": "BACKEND_ENGINEER",
                        "score": 0.91,
                    }
                ],
                "career_level": "mid",
                "leadership_scope": "none",
                "technology_focus_codes": [],
                "industry_context_codes": [],
                "observed_skill_domain_codes": [
                    "software_engineering"
                ],
                "confidence": 0.91,
                "review_reason_codes": [],
                "evidence_refs": evidence_refs or ["src_0001"],
            }
        ]
    }


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def extract(self, system_prompt, user_prompt):
        self.calls += 1
        return SimpleNamespace(data=self.payload)


def test_catalog_and_resolved_decision_materialize_v3_identity():
    catalog = load_position_catalog(CATALOG_PATH)
    client = FakeClient(_decision())
    classifier = PositionClassifier(
        catalog_path=CATALOG_PATH,
        client=client,
        max_attempts=1,
    )

    decision = classifier.classify([_profile()])["jd-1"]
    materialized = classifier.materialize(
        decision,
        source_title="后端开发工程师",
    )

    assert catalog["catalog_version"] == "position-taxonomy.v3.0.0"
    assert client.calls == 0
    assert materialized["classification_status"] == "resolved"
    assert materialized["position_code"] == "BACKEND_ENGINEER"
    assert materialized["family_code"] == "SOFTWARE_ENGINEERING"


def test_classifier_rejects_evidence_outside_profile():
    classifier = PositionClassifier(
        catalog_path=CATALOG_PATH,
        client=FakeClient(_decision(evidence_refs=["src_unknown"])),
        max_attempts=1,
    )

    with pytest.raises(
        ValueError,
        match="classification evidence refs exceed input evidence",
    ):
        classifier.classify([{**_profile(), "title": "后端服务研发专家"}])


def test_jd_profile_includes_title_evidence_from_contract_shape():
    profile = build_jd_position_profile(
        {
            "document_id": "jd-1",
            "job_title": {
                "text": "后端开发工程师",
                "evidence": {"source_id": "src_title"},
            },
            "responsibilities": [],
        },
        {"normalized_requirements": []},
    )

    assert profile["title"] == "后端开发工程师"
    assert profile["available_evidence_refs"] == ["src_title"]
