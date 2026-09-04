from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from jobgraph_contracts.source_identity import compute_content_hash

from app.main import app
from app.models.extraction_task import ExtractionTask
from app.models.matching_service_reference import MatchingServiceReference
from app.models.position_cluster import PositionCluster
from app.models.resume import Resume
from app.models.source_cv import CVExtractionTask, SourceCV, SourceCVVersion
from app.models.source_jd import SourceJD, SourceJDVersion
from app.models.task_record import TaskRecord
from tests.runtime_database import SessionLocal, reset_database_data
from tests.user_factory import create_internal_user


client = TestClient(app)

DEMO_TASK_FIELDS = {
    "task_id",
    "task_type",
    "object_type",
    "object_id",
    "service",
    "status",
    "progress",
    "error",
    "result_reference",
    "created_at",
    "updated_at",
}
DEMO_TASK_TYPES = {
    "jd_extraction",
    "cv_extraction",
    "trend",
    "discovery",
    "matching",
}
DEMO_TASK_STATUSES = {
    "pending",
    "running",
    "succeeded",
    "failed",
    "cancelled",
}


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _token(username: str, role: str) -> str:
    create_internal_user(username, role)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["data"]["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_five_demo_task_types(admin_id: str, owner_id: str) -> dict[str, str]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        source_jd = SourceJD(
            source_platform="competition_fixed_dataset",
            source_record_id="portal-demo-jd",
        )
        source_cv = SourceCV(
            owner_id=owner_id,
            source_platform="jobgraph_demo_data",
            source_record_id="portal-demo-cv",
        )
        resume = Resume(
            user_id=owner_id,
            source_type="text",
            raw_text="Python",
            parse_status="completed",
        )
        session.add_all([source_jd, source_cv, resume])
        session.flush()

        jd_version = SourceJDVersion(
            source_jd_id=source_jd.id,
            source_version="1",
            schema_version="v2",
            raw_text="Python 工程师",
            content_hash=compute_content_hash("Python 工程师"),
            raw_payload={},
            crawl_time=now,
            text_canonicalization_version="v1",
        )
        cv_version = SourceCVVersion(
            source_cv_id=source_cv.id,
            raw_text="Python",
            source_version="1",
        )
        session.add_all([jd_version, cv_version])
        session.flush()

        jd_task = ExtractionTask(
            id="jd-task-demo",
            source_jd_version_id=jd_version.id,
            status="failed",
            extraction_mode="llm",
            provider="jd-extraction",
            request_id="jd-demo-request",
            attempt_count=1,
            max_attempts=3,
            retryable=True,
            last_error_code="JD_TIMEOUT",
            last_error_message="upstream timed out",
        )
        cv_task = CVExtractionTask(
            id="cv-task-demo",
            source_cv_version_id=cv_version.id,
            request_id="cv-demo-request",
            status="running",
            attempt_count=1,
            max_attempts=3,
        )
        trend_task = TaskRecord(
            id="trend-task-demo",
            task_type="trend_analysis",
            status="succeeded",
            progress=1.0,
            input_payload={"position_id": "position-demo"},
            result_payload={"provider_run_id": "trend-run-demo"},
            result_reference="/admin/trends/not-an-api-result",
            created_by=admin_id,
        )
        cluster = PositionCluster(
            id="cluster-demo",
            cluster_name="Agent 工程师",
            algorithm="demo-v1",
            discovery_run_id="discovery-run-demo",
            discovery_run_status="completed",
            discovery_assessment={},
        )
        matching = MatchingServiceReference(
            task_id="matching-task-demo",
            evaluation_id="evaluation-demo",
            user_id=owner_id,
            tenant_id="demo-tenant",
            resume_id=resume.id,
            position_id="target-position-demo",
            provider="matching-service",
            target_type="standard_position",
            status="canceled",
            idempotency_key="matching-demo-key",
            cv_profile_version="cv-profile.v1",
            position_profile_version="position-profile.v1",
        )
        session.add_all([jd_task, cv_task, trend_task, cluster, matching])
        session.commit()
        return {
            "jd_version_id": jd_version.id,
            "cv_version_id": cv_version.id,
        }


def test_portal_demo_tasks_projects_five_types_status_errors_and_results():
    admin_id = create_internal_user("portal_demo_admin", "admin")
    owner_id = create_internal_user("portal_demo_owner", "personal_user")
    assert admin_id and owner_id
    token = _token("portal_demo_admin", "admin")
    ids = _seed_five_demo_task_types(admin_id, owner_id)

    response = client.get(
        "/api/v1/portal/admin/demo-tasks",
        headers=_headers(token),
    )

    assert response.status_code == 200
    items = response.json()["data"]
    assert {item["task_type"] for item in items} == DEMO_TASK_TYPES
    assert all(set(item) == DEMO_TASK_FIELDS for item in items)
    by_type = {item["task_type"]: item for item in items}
    assert by_type["jd_extraction"]["object_id"] == ids["jd_version_id"]
    assert by_type["jd_extraction"]["status"] == "failed"
    assert by_type["jd_extraction"]["progress"] == 0.0
    assert by_type["jd_extraction"]["error"] == {
        "code": "JD_TIMEOUT",
        "message": "upstream timed out",
    }
    assert by_type["jd_extraction"]["result_reference"] == (
        "/api/v1/extraction-tasks/jd-task-demo"
    )
    assert by_type["cv_extraction"]["status"] == "running"
    assert by_type["cv_extraction"]["progress"] == 0.5
    assert by_type["cv_extraction"]["object_id"] == ids["cv_version_id"]
    assert by_type["cv_extraction"]["result_reference"] == (
        "/api/v1/cv-extraction-tasks/cv-task-demo"
    )
    assert by_type["trend"]["status"] == "succeeded"
    assert by_type["trend"]["result_reference"] == (
        "/api/v1/trend-analysis/tasks/trend-task-demo"
    )
    assert by_type["discovery"]["status"] == "succeeded"
    assert by_type["discovery"]["result_reference"] == (
        "discovery_run:discovery-run-demo"
    )
    assert by_type["matching"]["status"] == "cancelled"
    assert by_type["matching"]["result_reference"] == (
        "/api/v1/matches/reports/evaluation-demo"
    )


def test_portal_demo_tasks_openapi_contract_is_frozen():
    schema = app.openapi()
    operation = schema["paths"]["/api/v1/portal/admin/demo-tasks"]["get"]
    assert {item["name"] for item in operation["parameters"]} == {
        "task_type",
        "status",
        "object_id",
    }
    response = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response["$ref"].endswith("/PortalDemoTaskCollectionEnvelope")

    resource = schema["components"]["schemas"]["PortalDemoTaskResponse"]
    assert set(resource["properties"]) == DEMO_TASK_FIELDS
    assert set(resource["required"]) == DEMO_TASK_FIELDS
    assert set(resource["properties"]["task_type"]["enum"]) == DEMO_TASK_TYPES
    assert set(resource["properties"]["status"]["enum"]) == DEMO_TASK_STATUSES


def test_portal_demo_tasks_filters_and_empty_result():
    admin_id = create_internal_user("portal_demo_filter_admin", "admin")
    owner_id = create_internal_user("portal_demo_filter_owner", "personal_user")
    assert admin_id and owner_id
    token = _token("portal_demo_filter_admin", "admin")
    _seed_five_demo_task_types(admin_id, owner_id)

    by_type = client.get(
        "/api/v1/portal/admin/demo-tasks?task_type=trend",
        headers=_headers(token),
    ).json()["data"]
    by_status = client.get(
        "/api/v1/portal/admin/demo-tasks?status=succeeded",
        headers=_headers(token),
    ).json()["data"]
    by_object = client.get(
        "/api/v1/portal/admin/demo-tasks?object_id=target-position-demo",
        headers=_headers(token),
    ).json()["data"]
    empty = client.get(
        "/api/v1/portal/admin/demo-tasks?object_id=missing",
        headers=_headers(token),
    ).json()["data"]

    assert [item["task_type"] for item in by_type] == ["trend"]
    assert {item["task_type"] for item in by_status} == {"trend", "discovery"}
    assert [item["task_type"] for item in by_object] == ["matching"]
    assert empty == []


def test_portal_demo_tasks_returns_empty_list_without_records():
    token = _token("portal_demo_empty_admin", "admin")

    response = client.get(
        "/api/v1/portal/admin/demo-tasks",
        headers=_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["data"] == []


def test_portal_demo_tasks_requires_integration_status_permission():
    token = _token("portal_demo_personal", "personal_user")

    response = client.get(
        "/api/v1/portal/admin/demo-tasks",
        headers=_headers(token),
    )

    assert response.status_code == 403
