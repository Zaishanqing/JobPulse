from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.router import router
from app.bootstrap.application import create_app as build_application
from app.config import Settings
from jobgraph_contracts.published_jd import build_published_jd_fact_v3


def expect(response, status: int = 200):
    if response.status_code != status:
        raise RuntimeError(
            f"{response.request.method} {response.request.url} returned "
            f"{response.status_code}: {response.text}"
        )
    body = response.json()
    if body.get("code") != 0:
        raise RuntimeError(f"unexpected response envelope: {body}")
    return body["data"]


def catalog_snapshot() -> dict:
    return {
        "contract_version": "capability-skill-snapshot.v2",
        "skill_id": "LANG_PYTHON",
        "canonical_name": "Python",
        "aliases": ["Python3"],
        "taxonomy_version": "sha256:" + "a" * 64,
        "classifications": [
            {
                "facet": "concept_class",
                "code": "technology",
                "name_zh": "技术实体",
                "name_en": "Technology",
                "is_primary": True,
            },
            {
                "facet": "technology_kind",
                "code": "language",
                "name_zh": "编程与查询语言",
                "name_en": "Language",
                "is_primary": True,
            },
        ],
        "status": "active",
    }


def published_fact() -> dict:
    document_id = "KG_VERIFY_JD"
    title_evidence = {
        "source_id": document_id,
        "quote": "后端开发",
        "start": 0,
        "end": 4,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    skill_evidence = {
        "source_id": document_id,
        "quote": "Python",
        "start": 5,
        "end": 11,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    extraction = {
        "schema_version": "v2",
        "document_id": document_id,
        "job_title": {"text": "后端开发", "evidence": title_evidence},
        "responsibilities": [],
        "requirements": [
            {
                "requirement_id": "REQ_PYTHON",
                "kind": "skill",
                "modality": "required",
                "evidence": skill_evidence,
                "proficiency": None,
                "items": [{"name": "Python", "item_type": "language"}],
            }
        ],
        "company_facts": [],
        "employment_facts": [],
    }
    normalized = {
        "schema_version": "v2",
        "document_id": document_id,
        "job_classification": {
            "schema_version": "job-position-classification.v3",
            "taxonomy_version": "position-taxonomy.v3.0.0",
            "source_title": "后端开发",
            "position_code": "BACKEND_ENGINEER",
            "position_name": "后端开发",
            "family_code": "SOFTWARE_ENGINEERING",
            "family_name": "软件工程与研发",
            "candidate_positions": [
                {"position_code": "BACKEND_ENGINEER", "score": 0.93}
            ],
            "career_level": "mid",
            "leadership_scope": None,
            "technology_focus_codes": [],
            "industry_context_codes": [],
            "observed_skill_domain_codes": ["software_engineering"],
            "confidence": 0.93,
            "classification_status": "resolved",
            "review_reason_codes": [],
            "evidence_refs": ["REQ_PYTHON"],
            "classification_policy_version": "position-classifier.v3.0",
        },
        "normalized_requirements": [
            {
                "requirement_id": "REQ_PYTHON",
                "kind": "skill",
                "normalized_skills": [
                    {
                        "source_name": "Python",
                        "skill_id": "LANG_PYTHON",
                        "canonical_name": "Python",
                        "category_code": None,
                        "subcategory_code": None,
                        "resolution_status": "resolved",
                        "resolution_source": "explicit_mapping",
                    }
                ],
            }
        ],
        "salary": None,
        "unresolved_items": [],
    }
    payload = {
        "schema_version": "v2",
        "source_system": "main-system",
        "source_jd_id": document_id,
        "source_fact_id": "KG_VERIFY_FACT",
        "source_fact_version": "2026-07-31T00:00:00+00:00",
        "review_status": "published",
        "published_at": "2026-07-31T00:00:00+00:00",
        "position_fact": {
            **normalized["job_classification"],
            "sample_support_status": "sufficient",
        },
        "skill_facts": [{"skill_id": "LANG_PYTHON"}],
        "requirement_facts": [{"requirement_id": "REQ_PYTHON"}],
        "education_fact": None,
        "experience_fact": None,
        "industry_fact": None,
        "company_facts": [],
        "employment_facts": [],
        "evidence": [title_evidence, skill_evidence],
        "extraction_fact": extraction,
        "normalized_fact": normalized,
        "trace_metadata": {
            "source_type": "authoritative_main_system",
            "source_name": "verification",
            "enterprise_id": "verification-enterprise",
        },
        "validation_lineage": {
            "state": "present",
            "data_validation_task_id": "VERIFY_TASK",
            "validation_report_id": "VERIFY_REPORT",
            "validated_bundle_snapshot_id": "VERIFY_BUNDLE",
            "validation_policy_version": "verification-policy.v1",
            "validation_conclusion": "pass",
            "absent_reason": None,
        },
        "skill_catalog_snapshot": {
            "source": "main-system-skill-catalog",
            "catalog_version": "skill-catalog.v1",
            "content_hash": "c" * 64,
            "effective_at": "2026-07-31T00:00:00+00:00",
            "status": "active",
        },
        "position_catalog_snapshot": {
            "source": "main-system-position-catalog",
            "catalog_version": "position-taxonomy.v3.0.0",
            "content_hash": "c" * 64,
            "effective_at": "2026-07-31T00:00:00+00:00",
            "status": "active",
        },
    }
    return build_published_jd_fact_v3(payload).model_dump(mode="json")


def run() -> dict:
    settings = Settings.from_env()
    application = build_application(settings)
    application.include_router(router)
    try:
        with TestClient(application) as client:
            token = expect(
                client.post(
                    "/api/v1/auth/token",
                    json={
                        "username": settings.service_username,
                        "password": settings.service_password,
                    },
                )
            )["access_token"]
            headers = {
                "Authorization": f"Bearer {token}",
                "X-Main-User-Id": "verification-main-user",
                "X-Main-User-Role": "admin",
                "X-Trace-Id": "kg-authoritative-flow",
            }
            expect(
                client.put(
                    "/api/v2/integrations/catalog/skills/LANG_PYTHON",
                    json=catalog_snapshot(),
                    headers=headers,
                )
            )
            expect(
                client.post(
                    "/api/v3/integrations/published-jd-facts",
                    json=published_fact(),
                    headers=headers,
                )
            )
            build = expect(
                client.post(
                    "/api/v1/positions/BACKEND_ENGINEER/graph/build",
                    json={"minimum_valid_samples": 1},
                    headers=headers,
                )
            )
            if build["status"] != "succeeded":
                raise RuntimeError(f"authoritative graph build did not succeed: {build}")
            for task in expect(
                client.get("/api/v1/review-tasks", headers=headers)
            ):
                if task["build_run_id"] != build["build_run_id"]:
                    continue
                if task["status"] != "pending":
                    continue
                expect(
                    client.post(
                        f"/api/v1/review-tasks/{task['id']}/claim",
                        json={"reason": "verify authoritative graph output"},
                        headers=headers,
                    )
                )
                expect(
                    client.post(
                        f"/api/v1/review-tasks/{task['id']}/approve",
                        json={"reason": "verify authoritative graph output"},
                        headers=headers,
                    )
                )
            version = expect(
                client.post(
                    f"/api/v1/graph/build-runs/{build['build_run_id']}/publish",
                    json={"reason": "authoritative KG verification", "version_name": "verify-v1"},
                    headers=headers,
                )
            )
            result = {
                "document_id": "KG_VERIFY_JD",
                "build_run_id": build["build_run_id"],
                "version_id": version["version_id"],
                "version_number": version["version_number"],
            }
            print(json.dumps(result, ensure_ascii=False))
            return result
    finally:
        application.state.database.engine.dispose()


if __name__ == "__main__":
    run()
