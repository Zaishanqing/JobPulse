from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.dependencies import get_handlers, require_internal_service
from app.api.router import router
from app.application.lifecycle_survival import LifecycleSurvivalResult
from app.application.promotion_distance import PromotionDistanceCertificate
from app.domain.candidate_lifecycle import PromotionCondition


class _Query:
    def lifecycle_survival(self, **filters):
        assert filters == {
            "candidate_id": "candidate-1",
            "event_type": "time_to_stable",
        }
        return (
            LifecycleSurvivalResult(
                "candidate-1",
                "w1",
                None,
                2,
                "time_to_stable",
                True,
                "w2",
                "w3",
                "run-1",
                None,
                "run-3",
                "request-1",
                "algorithm-v1",
                "formula-v1",
            ),
        )

    def promotion_distance(self, **filters):
        assert filters == {"candidate_id": "candidate-1"}
        return (
            PromotionDistanceCertificate(
                "candidate-1",
                "emerging_candidate",
                "stable_emerging_role",
                "not_ready",
                True,
                False,
                (
                    PromotionCondition("windows", 4, 2, 2, False),
                    PromotionCondition("support", 4, 4, 0, True),
                    PromotionCondition("companies", 3, 3, 0, True),
                    PromotionCondition("emergence", 0.6, 0.6, 0, True),
                    PromotionCondition("identity_stability", 3, 3, 0, True),
                ),
                ("windows",),
                "candidate-lifecycle-v1",
                "config-3",
                "run-3",
                "request-3",
                "algorithm-v1",
                "formula-v1",
            ),
        )


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_handlers] = lambda: SimpleNamespace(query=_Query())
    app.dependency_overrides[require_internal_service] = lambda: None
    return TestClient(app)


def test_lifecycle_survival_api_matches_query_dto_and_preserves_censoring():
    response = _client().get(
        "/api/v1/evaluations/lifecycle-survival",
        params={"candidate_id": "candidate-1", "event_type": "time_to_stable"},
    )

    assert response.status_code == 200
    result = response.json()["data"]["results"][0]
    assert result["event_window"] is None
    assert result["censored"] is True
    assert result["observation_end_window"] == "w3"


def test_promotion_distance_api_matches_query_dto():
    response = _client().get(
        "/api/v1/evaluations/promotion-distance",
        params={"candidate_id": "candidate-1"},
    )

    assert response.status_code == 200
    certificate = response.json()["data"]["certificates"][0]
    assert certificate["current_state"] == "emerging_candidate"
    assert certificate["windows"] == {
        "required": 4,
        "current": 2,
        "missing": 2,
        "satisfied": False,
    }
    assert certificate["missing_conditions"] == ["windows"]
    assert certificate["gate_identity"]["config_snapshot_id"] == "config-3"
