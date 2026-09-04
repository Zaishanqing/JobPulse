from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.domain.auth import AuthContext, derive_access_scope
from app.domain.skill_relations import SkillRelation
from app.infrastructure.authentication import FakeAuthenticationProvider

os.environ.setdefault("MATCHING_RUNTIME_MODE", "test")
os.environ.setdefault("MATCHING_AUTH_MODE", "fake")
os.environ.setdefault("MATCHING_FAKE_AUTH_TOKEN", "test-token")

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def auth_context() -> AuthContext:
    roles = frozenset({"candidate"})
    return AuthContext(
        subject_id="test-user",
        tenant_id="test-tenant",
        roles=roles,
        access_scope=derive_access_scope("test-user", "test-tenant", roles),
        token_id="test-token-id",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


@pytest.fixture
def auth_provider(auth_context: AuthContext) -> FakeAuthenticationProvider:
    return FakeAuthenticationProvider({"test-token": auth_context})


@pytest.fixture
def auth_headers(auth_context: AuthContext) -> dict[str, str]:
    return {
        "Authorization": "Bearer test-token",
        "X-Access-Scope": auth_context.access_scope,
    }


@pytest.fixture
def worker_auth_context() -> AuthContext:
    roles = frozenset({"matching.worker"})
    return AuthContext(
        subject_id="test-worker",
        tenant_id="matching-platform",
        roles=roles,
        access_scope=derive_access_scope("test-worker", "matching-platform", roles),
        token_id="test-worker-token-id",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


def evidence(source_id: str = "cv:block:1", quote: str = "Python") -> dict:
    return {
        "source_id": source_id,
        "quote": quote,
        "start": 0,
        "end": len(quote),
        "alignment": "exact",
        "occurrence_index": 0,
    }


@pytest.fixture
def cv_payload() -> dict:
    ref = evidence()
    second_ref = evidence("cv:block:2", "SQL")
    return {
        "contract_version": "cv-match-profile.v1",
        "source_version": "cv-source.v1",
        "cv_id": "cv_001",
        "user_id": "usr_opaque_001",
        "verification_snapshot_id": "verify_001",
        "as_of_date": "2026-07-27",
        "skills": [
            {
                "aggregation_key": "skill:python",
                "skill_id": "skill_python",
                "canonical_name": "Python",
                "normalization_confidence": 1.0,
                "resolution_source": "canonical_name",
                "declared_level": "proficient",
                "demonstrated_level": "working",
                "verification_status": "supported",
                "resolution_status": "resolved",
                "evidence_refs": [ref],
            },
            {
                "aggregation_key": "skill:sql",
                "skill_id": "skill_sql",
                "canonical_name": "SQL",
                "normalization_confidence": 0.99,
                "resolution_source": "canonical_name",
                "declared_level": None,
                "demonstrated_level": "basic",
                "verification_status": "experience_only",
                "resolution_status": "resolved",
                "evidence_refs": [second_ref],
            },
        ],
        "match_features": [
            {
                "feature_id": "feature_skill_python",
                "document_id": "cv_001",
                "side": "cv",
                "feature_type": "skill",
                "source_object_id": "skill_item_1",
                "source_scope": "skills",
                "canonical_id": "skill_python",
                "canonical_name": "Python",
                "raw_text": "Python",
                "vector_text": "Python",
                "requirement_modality": None,
                "candidate_level": "proficient",
                "structured_values": {"aggregation_key": "skill:python"},
                "resolution_status": "resolved",
                "evidence_refs": [ref],
                "taxonomy_version": "taxonomy-2026-07",
                "derivation_version": "cv-match-feature.v2",
            }
        ],
        "capability_profiles": [
            {
                "profile_id": "cap_python",
                "document_id": "cv_001",
                "aggregation_key": "skill:python",
                "skill_id": "skill_python",
                "canonical_name": "Python",
                "declared_feature_ids": ["feature_skill_python"],
                "experience_skill_feature_ids": ["feature_exp_python"],
                "evidence_link_ids": ["link_python"],
                "declared_level": "proficient",
                "demonstrated_level": "working",
                "demonstrated_level_label": "可工作应用",
                "verification_status": "supported",
                "support_confidence": 0.8,
                "confidence_band": "high",
                "independent_experience_count": 1,
                "aggregate_support_score": 3,
                "evidence_bonus": 0.2,
                "resolution_status": "resolved",
            }
        ],
        "capability_evidence_links": [
            {
                "link_id": "link_python",
                "document_id": "cv_001",
                "aggregation_key": "skill:python",
                "skill_id": "skill_python",
                "canonical_name": "Python",
                "declared_feature_ids": ["feature_skill_python"],
                "experience_skill_feature_id": "feature_exp_python",
                "experience_feature_id": "experience_1",
                "supporting_task_feature_ids": ["task_1"],
                "support_signals": ["direct_experience"],
                "support_score": 3,
                "demonstrated_level": "working",
                "support_confidence": 0.8,
                "confidence_band": "high",
                "evidence_refs": [ref],
                "taxonomy_version": "taxonomy-2026-07",
                "derivation_version": "capability-verification.v1",
            }
        ],
        "projects": [],
        "work_experiences": [],
        "education": [],
        "certificates": [],
        "languages": [],
        "evidence_refs": [ref, second_ref],
        "unresolved_items": [],
        "review_status": "approved",
        "taxonomy_version": "taxonomy-2026-07",
        "derivation_version": "matching-profile.v1",
        "profile_version": None,
        "position_classifications": [
            {
                "taxonomy_version": "taxonomy-2026-07",
                "position_code": "BACKEND_ENGINEER",
                "classification_status": "resolved",
                "career_level": "senior",
                "leadership_scope": "none",
            }
        ],
    }


@pytest.fixture
def position_payload() -> dict:
    ref = evidence("jd:block:1", "负责后端服务开发")
    skill_ref = evidence("jd:block:2", "熟练掌握 Python")
    return {
        "contract_version": "position-match-profile.v1",
        "source_version": "position-source.v1",
        "position_id": "position_source_001",
        "canonical_position_id": "position_backend_engineer",
        "canonical_title": "后端开发工程师",
        "core_responsibilities": ["负责后端服务开发", "维护服务可靠性"],
        "required_skills": [
            {
                "skill_id": "skill_python",
                "canonical_name": "Python",
                "required_level": "proficient",
                "importance": 1.0,
                "resolution_status": "resolved",
                "evidence_refs": [skill_ref],
            },
            {
                "skill_id": "skill_sql",
                "canonical_name": "SQL",
                "required_level": "working",
                "importance": 0.8,
                "resolution_status": "resolved",
                "evidence_refs": [],
            },
        ],
        "preferred_skills": [],
        "hard_conditions": [
            {
                "condition_id": "condition_exp",
                "condition_type": "experience",
                "operator": "at_least",
                "value": "3 years",
                "resolution_status": "resolved",
                "evidence_refs": [],
            }
        ],
        "tools": {"values": ["Git"], "evidence_refs": []},
        "industries": {"values": ["互联网"], "evidence_refs": []},
        "business_scenarios": {"values": ["高并发服务"], "evidence_refs": []},
        "evidence_refs": [ref, skill_ref],
        "quality_context": {
            "snapshot_id": "quality_001",
            "status": "trusted",
            "completeness": 0.95,
            "assessed_at": "2026-07-27",
            "evidence_refs": [ref],
        },
        "trend_context": {
            "snapshot_id": "trend_001",
            "window_start": "2026-01-01",
            "window_end": "2026-06-30",
            "trend_version": "trend.v1",
            "signals": ["demand_stable"],
            "evidence_refs": [],
        },
        "unresolved_items": [],
        "review_status": "approved",
        "taxonomy_version": "taxonomy-2026-07",
        "graph_version": "graph-42",
        "profile_version": None,
        "position_code": "BACKEND_ENGINEER",
        "classification_status": "resolved",
        "career_level": "senior",
        "leadership_scope": "none",
        "sample_support_status": "sufficient",
    }


@pytest.fixture
def clone():
    return deepcopy


@pytest.fixture
def ready_cv_json() -> dict:
    return json.loads((FIXTURES / "cv_ready.json").read_text("utf-8"))


@pytest.fixture
def ready_position_json() -> dict:
    return json.loads((FIXTURES / "position_ready.json").read_text("utf-8"))


@pytest.fixture
def context_overrides_json() -> dict:
    return json.loads((FIXTURES / "context_overrides.json").read_text("utf-8"))


@pytest.fixture
def skill_relations_fixture() -> tuple[SkillRelation, ...]:
    payload = json.loads((FIXTURES / "skill_relations.json").read_text("utf-8"))
    return tuple(SkillRelation.model_validate(item) for item in payload["relations"])


@pytest.fixture
def upstream_cv_anonymized() -> dict:
    return json.loads((FIXTURES / "upstream_cv_anonymized.json").read_text("utf-8"))


@pytest.fixture
def upstream_position_anonymized() -> dict:
    return json.loads((FIXTURES / "upstream_position_anonymized.json").read_text("utf-8"))


@pytest.fixture
def upstream_relations_anonymized() -> tuple[SkillRelation, ...]:
    payload = json.loads((FIXTURES / "upstream_skill_relations_anonymized.json").read_text("utf-8"))
    return tuple(SkillRelation.model_validate(item) for item in payload["relations"])
