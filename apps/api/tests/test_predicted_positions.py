import pytest
from fastapi.testclient import TestClient

from tests.runtime_database import reset_database_data, Base, SessionLocal, engine
from app.main import app
from app.models.emerging_position import EmergingPosition
from app.models.predicted_position import PredictedPosition
from app.models.standard_position import StandardPosition
from app.models.task_record import TaskRecord
from app.contexts.market_intelligence import ManagePredictedPositions
from app.contexts.market_intelligence._ports.trend_intelligence_gateway_v1 import (
    TrendIntelligenceRunV1,
)
from app.contexts.tasks import ManageTasks
from app.domain.accounts import AccountActor
from app.infrastructure.tasks import SqlAlchemyTaskUnitOfWork
from app.infrastructure.trend_intelligence_gateway import (
    DisabledTrendIntelligenceGatewayV1,
    TestTrendIntelligenceGatewayV1,
)
from app.infrastructure.trends import (
    SqlAlchemyPredictedPositionRepository,
    SqlAlchemyTrendUnitOfWork,
)
from tests.user_factory import create_internal_user


client = TestClient(app)

REQUIRED_DELIVERY_FIELDS = {
    "schema_version", "resource_type", "resource_id", "status", "progress",
    "source_coverage", "missing_sources", "quality_flags", "evidence_references",
    "review_status", "review_task_id", "publication_gate",
}


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


def test_legacy_main_system_trend_source_routes_are_deleted():
    admin_token = _register_and_login("predict_admin001", "admin")
    assert client.get(
        "/api/v1/trend-sources", headers=_auth_headers(admin_token)
    ).status_code == 404
    assert client.post(
        "/api/v1/trend-sources/policy", json={}, headers=_auth_headers(admin_token)
    ).status_code == 404


def test_create_prediction_task_and_publish_predicted_position():
    admin_token = _register_and_login("predict_admin002", "developer")
    task_response = client.post(
        "/api/v1/predicted-positions/tasks",
        json={"source_ids": []},
        headers=_auth_headers(admin_token),
    )
    assert task_response.status_code == 200
    task = task_response.json()["data"]
    assert task["status"] == "completed"
    assert len(task["predicted_ids"]) >= 1
    assert task["mock"] is False
    assert task["provider"] == "trend_intelligence_http"
    assert "emerging_position" in task["note"]

    task_detail_response = client.get(
        f"/api/v1/predicted-positions/tasks/{task['task_id']}",
        headers=_auth_headers(admin_token),
    )
    list_response = client.get(
        "/api/v1/predicted-positions",
        headers=_auth_headers(admin_token),
    )
    predicted_id = list_response.json()["data"]["items"][0]["predicted_id"]
    detail_response = client.get(
        f"/api/v1/predicted-positions/{predicted_id}",
        headers=_auth_headers(admin_token),
    )
    score_response = client.get(
        f"/api/v1/predicted-positions/{predicted_id}/confidence-score",
        headers=_auth_headers(admin_token),
    )
    publish_response = client.post(
        f"/api/v1/predicted-positions/{predicted_id}/publish",
        headers=_auth_headers(admin_token),
    )

    assert task_detail_response.json()["data"]["task_id"] == task["task_id"]
    repeated_detail = client.get(
        f"/api/v1/predicted-positions/tasks/{task['task_id']}",
        headers=_auth_headers(admin_token),
    )
    assert repeated_detail.json()["data"]["predicted_ids"] == task["predicted_ids"]
    assert list_response.status_code == 200
    assert detail_response.json()["data"]["predicted_id"] == predicted_id
    assert score_response.json()["data"]["confidence_score"] > 0
    assert publish_response.status_code == 422
    with SessionLocal() as db:
        assert db.query(PredictedPosition).count() == len(task["predicted_ids"])
        projected = db.query(PredictedPosition).one()
        assert projected.provider_run_id == task["provider_run_id"]
        assert projected.emergence_score == 0.8
        assert projected.evidence_references
        task_row = db.get(TaskRecord, task["task_id"])
        assert task_row is not None
        assert task_row.task_type == "predicted_position_analysis"
        assert task_row.result_payload["predicted_ids"] == task["predicted_ids"]
        assert db.query(StandardPosition).count() == 0
        assert db.query(EmergingPosition).count() == 0


def test_predicted_position_admin_permissions():
    personal_token = _register_and_login("predict_user003", "personal_user")

    trend_response = client.get(
        "/api/v1/trend-sources",
        headers=_auth_headers(personal_token),
    )
    prediction_response = client.get(
        "/api/v1/predicted-positions",
        headers=_auth_headers(personal_token),
    )

    assert trend_response.status_code == 404
    assert prediction_response.status_code == 200
    assert prediction_response.json()["data"]["items"] == []
    assert prediction_response.json()["data"]["pagination"]["total"] == 0


class _StateGateway:
    provider_name = "trend_intelligence_http"

    def __init__(self) -> None:
        self.polls = iter(("running", "failed"))

    def create_market_prediction(self, request):
        return TrendIntelligenceRunV1("provider-run-state", "pending")

    def get_run(self, run_id: str):
        status = next(self.polls)
        return TrendIntelligenceRunV1(
            run_id,
            status,
            error_message="real upstream failure" if status == "failed" else None,
        )


def _prediction_use_case(gateway) -> ManagePredictedPositions:
    return ManagePredictedPositions(
        lambda: SqlAlchemyTrendUnitOfWork(SessionLocal),
        ManageTasks(lambda: SqlAlchemyTaskUnitOfWork(SessionLocal)),
        gateway,
    )


def test_remote_pending_running_failed_are_persisted() -> None:
    account_id = create_internal_user("prediction_state_admin", "admin")
    actor = AccountActor(account_id, "admin")
    use_case = _prediction_use_case(_StateGateway())

    pending = use_case.run(actor, [])
    running = use_case.task(actor, pending.task_id)
    failed = use_case.task(actor, pending.task_id)

    assert pending.status == "pending"
    assert running.status == "running"
    assert failed.status == "failed"
    assert failed.error_code == "TREND_INTELLIGENCE_RUN_FAILED"
    assert failed.error_message == "real upstream failure"


def test_disabled_service_cannot_create_a_false_success() -> None:
    account_id = create_internal_user("prediction_disabled_admin", "admin")
    task = _prediction_use_case(DisabledTrendIntelligenceGatewayV1()).run(
        AccountActor(account_id, "admin"), []
    )

    assert task.status == "failed"
    assert task.error_code == "TREND_INTELLIGENCE_DISABLED"
    assert task.result_payload.get("mock") is False
    with SessionLocal() as db:
        assert db.query(PredictedPosition).count() == 0


def test_remote_success_local_projection_failure_rolls_back_and_can_retry(
    monkeypatch,
) -> None:
    account_id = create_internal_user("prediction_retry_admin", "admin")
    actor = AccountActor(account_id, "admin")
    use_case = _prediction_use_case(TestTrendIntelligenceGatewayV1())
    original_add = SqlAlchemyPredictedPositionRepository.add
    failed_once = False

    def fail_once(repository, values):
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("local projection failed")
        return original_add(repository, values)

    monkeypatch.setattr(SqlAlchemyPredictedPositionRepository, "add", fail_once)
    with pytest.raises(RuntimeError, match="local projection failed"):
        use_case.run(actor, [])

    with SessionLocal() as db:
        task_row = db.query(TaskRecord).filter_by(created_by=account_id).one()
        task_id = task_row.id
        assert task_row.status == "running"
        assert task_row.result_payload["provider_run_id"]
        assert db.query(PredictedPosition).count() == 0

    retried = use_case.task(actor, task_id)
    assert retried.status == "succeeded"
    with SessionLocal() as db:
        assert db.query(PredictedPosition).count() == 1


def test_prediction_delivery_pagination_batch_empty_and_validation_contract() -> None:
    token = _register_and_login("prediction_delivery_admin", "admin")
    task = client.post(
        "/api/v1/predicted-positions/tasks",
        headers=_auth_headers(token),
    ).json()["data"]
    predicted_id = task["predicted_ids"][0]

    listing = client.get(
        "/api/v1/predicted-positions?page=1&page_size=1&sort_by=emergence_score&sort_order=desc",
        headers=_auth_headers(token),
    )
    assert listing.status_code == 200
    collection = listing.json()["data"]
    assert collection["schema_version"] == "trend-delivery.v1"
    assert collection["pagination"]["total"] >= 1
    assert len(collection["items"]) == 1
    assert REQUIRED_DELIVERY_FIELDS <= set(collection["items"][0])

    batch = client.post(
        "/api/v1/predicted-positions/batch-query",
        json={"ids": [predicted_id, predicted_id, "missing-prediction"]},
        headers=_auth_headers(token),
    ).json()["data"]
    assert [item["predicted_id"] for item in batch["items"]] == [predicted_id]
    assert batch["not_found_ids"] == ["missing-prediction"]

    empty = client.get(
        "/api/v1/predicted-positions?status=rejected",
        headers=_auth_headers(token),
    ).json()["data"]
    assert empty["items"] == []
    assert empty["pagination"]["total"] == 0
    assert client.post(
        "/api/v1/predicted-positions/batch-query",
        json={"ids": []},
        headers=_auth_headers(token),
    ).status_code == 422
    assert client.get("/api/v1/predicted-positions").status_code == 401


@pytest.mark.parametrize(
    ("sort_order", "expected_ids"),
    [
        ("asc", ["prediction-low", "prediction-high", "prediction-missing"]),
        ("desc", ["prediction-high", "prediction-low", "prediction-missing"]),
    ],
)
def test_predicted_position_sort_keeps_null_values_last(
    sort_order: str,
    expected_ids: list[str],
) -> None:
    token = _register_and_login(f"prediction_sort_{sort_order}", "admin")
    with SessionLocal() as db:
        db.add_all([
            PredictedPosition(
                id="prediction-low",
                position_name="Low score",
                emergence_score=0.2,
                confidence_score=0.2,
            ),
            PredictedPosition(
                id="prediction-missing",
                position_name="Missing score",
                emergence_score=None,
                confidence_score=0.0,
            ),
            PredictedPosition(
                id="prediction-high",
                position_name="High score",
                emergence_score=0.8,
                confidence_score=0.8,
            ),
        ])
        db.commit()

    response = client.get(
        f"/api/v1/predicted-positions?sort_by=emergence_score&sort_order={sort_order}",
        headers=_auth_headers(token),
    )

    assert response.status_code == 200
    assert [
        item["predicted_id"] for item in response.json()["data"]["items"]
    ] == expected_ids
