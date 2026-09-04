import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from app.main import app
from app.models.evaluation import EvaluationReport
from app.models.task_record import TaskRecord
from tests.user_factory import create_internal_user


client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    reset_database_data()
    yield
    reset_database_data()


def _register_and_login(username: str, role: str) -> str:
    create_internal_user(username, role)
    client.post(
        "/api/v1/auth/register",
        json={
            "role": role,
            "username": username,
            "password": "password123",
            "email": f"{username}@example.com",
            "phone": "13800000000",
        },
    )
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "password123"},
    )
    return response.json()["data"]["access_token"]


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_dataset(token: str, dataset_type: str, items: list[dict] | None = None) -> str:
    if items is None:
        items = (
            [
                {"case_id": "match_1", "expected": 0.8, "actual": 0.82},
                {"case_id": "match_2", "expected": 0.9, "actual": 0.7},
                {"case_id": "match_3", "expected": 0.5, "actual": 0.47},
            ]
            if dataset_type == "match"
            else [
                {"case_id": f"{dataset_type}_1", "expected": "Python", "actual": " python "},
                {"case_id": f"{dataset_type}_2", "expected": ["RAG", "LLM"], "actual": ["LLM", "RAG"]},
                {"case_id": f"{dataset_type}_3", "expected": "Docker", "actual": "Kubernetes"},
            ]
        )
    response = client.post(
        f"/api/v1/evaluation/datasets/{dataset_type}",
        json={
            "name": f"{dataset_type} 测试集",
            "description": "data-driven rule evaluation dataset",
            "payload": {"items": items},
        },
        headers=_auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["dataset_type"] == dataset_type
    return data["dataset_id"]


def test_upload_evaluation_datasets():
    admin_token = _register_and_login("eval_admin001", "admin")

    jd_dataset_id = _create_dataset(admin_token, "jd")
    resume_dataset_id = _create_dataset(admin_token, "resume")
    match_dataset_id = _create_dataset(admin_token, "match")

    response = client.get(
        "/api/v1/evaluation/datasets",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    dataset_ids = {item["dataset_id"] for item in response.json()["data"]}
    assert {jd_dataset_id, resume_dataset_id, match_dataset_id}.issubset(dataset_ids)


def test_run_jd_parse_evaluation():
    admin_token = _register_and_login("eval_admin002", "admin")
    dataset_id = _create_dataset(admin_token, "jd")

    response = client.post(
        "/api/v1/evaluation/jd-parse/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report_type"] == "jd_parse"
    assert data["dataset_id"] == dataset_id
    assert data["metrics"]["jd_parse_accuracy"] == 0.6667
    assert data["metrics"]["evaluated_count"] == 3
    assert data["evaluation_status"] == "completed"
    assert data["algorithm_version"] == "jd-rule-eval-v1"
    assert data["implementation_status"] == "data_driven_rule_evaluation"
    assert len(data["error_cases"]) == 1


def test_run_resume_parse_evaluation():
    admin_token = _register_and_login("eval_admin003", "admin")
    dataset_id = _create_dataset(admin_token, "resume")

    response = client.post(
        "/api/v1/evaluation/resume-parse/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report_type"] == "resume_parse"
    assert data["metrics"]["resume_parse_accuracy"] == 0.6667


def test_run_match_evaluation():
    admin_token = _register_and_login("eval_admin004", "admin")
    dataset_id = _create_dataset(admin_token, "match")

    response = client.post(
        "/api/v1/evaluation/match/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report_type"] == "match"
    assert data["metrics"]["match_accuracy"] == 0.6667
    assert data["config_snapshot"]["numeric_tolerance"] == 0.05


def test_get_evaluation_report():
    admin_token = _register_and_login("eval_admin005", "admin")
    dataset_id = _create_dataset(admin_token, "jd")
    run_response = client.post(
        "/api/v1/evaluation/jd-parse/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )
    report_id = run_response.json()["data"]["report_id"]

    response = client.get(
        f"/api/v1/evaluation/reports/{report_id}",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["report_id"] == report_id
    assert data["metrics"]["jd_parse_accuracy"] == 0.6667


def test_evaluation_without_actual_results_is_explicitly_not_evaluable():
    admin_token = _register_and_login("eval_admin009", "admin")
    dataset_id = _create_dataset(
        admin_token,
        "jd",
        items=[{"case_id": "missing_actual", "input": "JD", "expected": "Python"}],
    )

    response = client.post(
        "/api/v1/evaluation/jd-parse/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )

    data = response.json()["data"]
    assert response.status_code == 200
    assert data["evaluation_status"] == "insufficient_data"
    assert data["metrics"]["jd_parse_accuracy"] is None
    assert data["metrics"]["evaluated_count"] == 0
    assert data["metrics"]["skipped_count"] == 1
    assert data["error_cases"][0]["type"] == "not_evaluable"


def test_skill_normalization_evaluation_uses_dataset_cases():
    admin_token = _register_and_login("eval_admin010", "admin")
    dataset_id = _create_dataset(admin_token, "jd")

    response = client.post(
        "/api/v1/evaluation/skill-normalization/run",
        json={"dataset_id": dataset_id},
        headers=_auth_headers(admin_token),
    )

    data = response.json()["data"]
    assert data["metrics"]["skill_normalization_accuracy"] == 0.6667
    assert data["algorithm_version"] == "skill-normalization-rule-eval-v1"


def test_get_system_status():
    admin_token = _register_and_login("eval_admin006", "admin")

    response = client.get(
        "/api/v1/system/status",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["components"]["api"]["status"] == "ok"
    assert "database" in data["components"]
    assert data["configuration"] == {
        "data_validation_mode": "off",
        "crawler_data_exchange": "offline_bundle",
        "cv_extraction_enabled": False,
    }


def test_get_database_status():
    admin_token = _register_and_login("eval_admin007", "admin")

    response = client.get(
        "/api/v1/system/status/databases",
        headers=_auth_headers(admin_token),
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "ok"
    assert data["checks"]["select_1"] is True
    assert data["dialect"] == "sqlite"


def test_evaluation_and_system_require_admin_or_developer():
    personal_token = _register_and_login("eval_user008", "personal_user")

    evaluation_response = client.get(
        "/api/v1/evaluation/datasets",
        headers=_auth_headers(personal_token),
    )
    system_response = client.get(
        "/api/v1/system/status",
        headers=_auth_headers(personal_token),
    )

    assert evaluation_response.status_code == 403
    assert system_response.status_code == 403
def test_cluster_evaluation_computes_purity_from_labels():
    admin_token = _register_and_login("eval_cluster_admin", "admin")
    response = client.post(
        "/api/v1/evaluation/cluster/run",
        headers=_auth_headers(admin_token),
        json={
            "items": [
                {"case_id": "1", "expected_label": "backend", "actual_cluster": "a"},
                {"case_id": "2", "expected_label": "backend", "actual_cluster": "a"},
                {"case_id": "3", "expected_label": "data", "actual_cluster": "a"},
                {"case_id": "4", "expected_label": "data", "actual_cluster": "b"},
            ]
        },
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["canonical_status"] == "succeeded"
    assert data["metrics"]["cluster_purity"] == 0.75
    assert data["evaluation_status"] == "completed"
    assert data["implementation_status"] == "database_persisted_sync_executor"
    assert data["metrics"]["implementation_status"] == "data_driven_cluster_evaluation"
    with SessionLocal() as session:
        assert session.get(EvaluationReport, data["report_id"]) is not None
        task = session.get(TaskRecord, data["task_id"])
        assert task is not None
        assert task.task_type == "evaluation_cluster"
        assert task.result_reference == f"evaluation_report:{data['report_id']}"


def test_cluster_evaluation_reports_insufficient_data_without_fixed_metric():
    admin_token = _register_and_login("eval_cluster_empty", "admin")
    response = client.post(
        "/api/v1/evaluation/cluster/run",
        headers=_auth_headers(admin_token),
        json={"items": [{"case_id": "invalid", "expected_label": "backend"}]},
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["metrics"]["cluster_purity"] is None
    assert data["evaluation_status"] == "insufficient_data"
    report = client.get(
        f"/api/v1/evaluation/reports/{data['report_id']}",
        headers=_auth_headers(admin_token),
    )
    assert report.status_code == 200
    assert report.json()["data"]["metrics"]["cluster_purity"] is None


def test_evaluation_export_returns_real_report_and_rejects_missing_id():
    admin_token = _register_and_login("eval_export_admin", "admin")
    dataset_id = _create_dataset(admin_token, "jd")
    report_id = client.post(
        "/api/v1/evaluation/jd-parse/run",
        headers=_auth_headers(admin_token),
        json={"dataset_id": dataset_id},
    ).json()["data"]["report_id"]

    exported = client.get(
        f"/api/v1/evaluation/reports/{report_id}/export",
        headers=_auth_headers(admin_token),
    )
    missing = client.get(
        "/api/v1/evaluation/reports/not-found/export",
        headers=_auth_headers(admin_token),
    )

    assert exported.status_code == 200
    data = exported.json()["data"]
    assert data["format"] == "json"
    assert data["report"]["report_id"] == report_id
    assert data["report"]["metrics"]["jd_parse_accuracy"] == 0.6667
    assert data["implementation_status"] == "database_report_json_export"
    assert missing.status_code == 404
