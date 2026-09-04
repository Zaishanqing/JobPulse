from __future__ import annotations

from calendar import monthrange
import hashlib

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.domain.candidate_identity import SELECTED_DECISION_VERSION
from app.main import app
from tests.runtime_database import engine


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}

ABSTAIN_CONFIG = {
    "identity_abstention_margin": 1.0,
    "identity_abstention_conflict_gap": 1.0,
}
LOW_THRESHOLDS = {
    "weak_to_incubating_min_windows": 2,
    "weak_to_incubating_min_support": 1,
    "weak_to_incubating_min_companies": 1,
    "weak_to_incubating_min_emergence": 0.0,
    "incubating_to_emerging_min_windows": 3,
    "incubating_to_emerging_min_support": 1,
    "incubating_to_emerging_min_companies": 1,
    "incubating_to_emerging_min_emergence": 0.0,
    "incubating_to_emerging_min_identity_stability": 1,
    "emerging_to_stable_min_windows": 4,
    "emerging_to_stable_min_support": 1,
    "emerging_to_stable_min_companies": 1,
    "emerging_to_stable_min_emergence": 0.0,
    "emerging_to_stable_min_identity_stability": 1,
}


def _windows(offset: int) -> list[dict[str, str]]:
    start = 1 + offset * 3
    return [
        {
            "window_id": f"2026-{month:02d}",
            "start": f"2026-{month:02d}-01",
            "end": f"2026-{month:02d}-{monthrange(2026, month)[1]}",
        }
        for month in range(start, start + 3)
    ]


def _payload(
    offset: int,
    request_id: str,
    title: str,
    skills: tuple[str, ...],
    responsibilities: tuple[str, ...],
    *,
    config: dict | None = None,
) -> dict:
    windows = _windows(offset)
    companies = ("acme", "globex", "initech")
    snapshots = []
    for index, window in enumerate(windows):
        snapshots.append(
            {
                "source_fact_id": f"{request_id}-fact-{index}",
                "source_fact_version": "1",
                "jd_id": f"{request_id}-jd-{index}",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": title,
                "source_name": f"platform-{index % 2}",
                "publish_date": f"{window['window_id']}-10",
                "content_hash": "sha256:" + hashlib.sha256(
                    f"identity:{index}".encode()
                ).hexdigest(),
                "structured_data": {
                    "responsibilities": list(responsibilities),
                    "required_skills": [{"raw_skill": skill} for skill in skills],
                    "bonus_skills": [],
                    "business_scenarios": ["智能客服"],
                    "source_record_id": f"identity-source-{index}",
                    "company_name": companies[index],
                },
            }
        )
    return {
        "contract_version": "discovery.v2",
        "request_id": request_id,
        "algorithm": "emerge_v3_2",
        "time_windows": windows,
        "snapshots": snapshots,
        "position_references": [
            {
                "position_id": "standard-backend",
                "graph_version_id": "graph-v1",
                "required_skills": [{"raw_skill": "Java"}],
            }
        ],
        "config": {
            "dataset_id": "emerging-discovery-full-temporal-v1",
            **(config or {}),
        },
    }


def _create(offset: int, request_id: str, title: str, config: dict | None = None) -> None:
    payload = _payload(
        offset,
        request_id,
        title,
        ("Python", "RAG", "Agent"),
        ("构建智能体应用",),
        config=config,
    )
    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text


def _candidates(**filters) -> list[dict]:
    response = client.get("/api/v1/candidates", params=filters, headers=HEADERS)
    assert response.status_code == 200
    return response.json()["data"]["candidates"]


def _candidate_detail(candidate_id: str) -> dict:
    response = client.get(f"/api/v1/candidates/{candidate_id}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return response.json()["data"]["candidate"]


def _resolve(
    candidate_id: str,
    *,
    resolution: str,
    target_candidate_id: str | None = None,
    idempotency_key: str | None = None,
) -> tuple[int, dict]:
    payload = {
        "resolution": resolution,
        "target_candidate_id": target_candidate_id,
        "reviewer": "reviewer-test",
        "reason": "manual identity review confirmed",
        "expected_version": SELECTED_DECISION_VERSION,
        "idempotency_key": idempotency_key,
    }
    response = client.post(
        f"/api/v1/candidates/{candidate_id}/identity-resolution",
        json=payload,
        headers=HEADERS,
    )
    return response.status_code, response.json()


def _audit_rows(provisional_candidate_id: str) -> list[dict]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id AS resolution_id, provisional_candidate_id, "
                "target_candidate_id, decision, reviewer, reason, window_id, "
                "algorithm_version, idempotency_key "
                "FROM identity_resolution_audits "
                "WHERE provisional_candidate_id = :candidate_id"
            ),
            {"candidate_id": provisional_candidate_id},
        ).mappings().all()
    return [dict(row) for row in rows]


def test_review_required_creates_pending_review_instead_of_plain_active_candidate() -> None:
    _create(0, "pending-state-1", "AI Agent Developer", ABSTAIN_CONFIG)
    _create(1, "pending-state-2", "AI Agent Developer", ABSTAIN_CONFIG)

    items = _candidates()
    assert len(items) == 2
    first = next(item for item in items if item["last_seen_window_id"] == "2026-03")
    second = next(item for item in items if item["last_seen_window_id"] == "2026-06")

    assert second["status"] == "weak_signal"
    assert second["identity_resolution_state"] == "pending_review"
    resolution = second["identity_profile"]["identity_resolution"]
    assert resolution["provisional_candidate_id"] == second["candidate_id"]
    assert resolution["closest_candidate_id"] == first["candidate_id"]
    assert resolution["identity_score"] is not None
    assert resolution["decision_margin"] is not None
    assert resolution["decision_basis"]
    assert resolution["continuity_certificate"]["decision"] == "review_required"
    assert resolution["window_id"] == "2026-06"
    assert resolution["cluster_id"]
    assert resolution["created_at"]


def test_pending_review_candidate_never_auto_promotes() -> None:
    config = {**LOW_THRESHOLDS, **ABSTAIN_CONFIG}
    for index in range(3):
        _create(index, f"no-promote-{index}", "AI Agent Developer", config)

    items = _candidates()
    pending = [item for item in items if item["identity_resolution_state"] == "pending_review"]
    assert pending
    assert all(item["status"] == "weak_signal" for item in pending)
    assert all(
        not item["identity_profile"]["lifecycle_state_v2"].get(
            "support_trajectory"
        )
        for item in pending
    )


def test_pending_review_suppresses_closest_candidate_missing_evidence() -> None:
    _create(0, "protect-1", "AI Agent Developer", ABSTAIN_CONFIG)
    _create(1, "protect-2", "AI Agent Developer", ABSTAIN_CONFIG)
    _create(2, "protect-3", "Java 后端工程师", ABSTAIN_CONFIG)
    _create(3, "protect-4", "数据分析师", ABSTAIN_CONFIG)

    protected = next(
        item
        for item in _candidates()
        if item["first_seen_window_id"] == "2026-03"
        and item["display_title"] == "AI Agent Developer"
    )
    lifecycle = protected["identity_profile"]["lifecycle_state_v2"]
    assert protected["status"] == "weak_signal"
    assert lifecycle["missed_eligible_windows"] == 0
    assert [event["reason"] for event in lifecycle["missing_events"]] == [
        "SUPPRESSED_PENDING_IDENTITY_REVIEW",
        "SUPPRESSED_PENDING_IDENTITY_REVIEW",
        "SUPPRESSED_PENDING_IDENTITY_REVIEW",
    ]


def test_confirm_same_restores_canonical_identity_and_hides_resolved_provisional() -> None:
    _create(0, "same-1", "AI Agent Developer", ABSTAIN_CONFIG)
    _create(1, "same-2", "AI Agent Developer", ABSTAIN_CONFIG)
    second = next(
        item
        for item in _candidates()
        if item["identity_resolution_state"] == "pending_review"
    )
    first = next(
        item for item in _candidates() if item["candidate_id"] != second["candidate_id"]
    )

    status, payload = _resolve(
        second["candidate_id"],
        resolution="confirm_same",
        target_candidate_id=first["candidate_id"],
        idempotency_key="confirm-same-1",
    )
    assert status == 200
    assert payload["data"]["decision"] == "confirm_same"
    assert payload["data"]["target_candidate_id"] == first["candidate_id"]

    visible_ids = {item["candidate_id"] for item in _candidates()}
    assert second["candidate_id"] not in visible_ids
    mapped = _candidate_detail(second["candidate_id"])
    assert mapped["candidate_id"] == first["candidate_id"]

    trajectory = client.get(
        f"/api/v1/candidates/{first['candidate_id']}/trajectory",
        headers=HEADERS,
    ).json()["data"]["trajectory"]
    assert [step["window_id"] for step in trajectory] == ["2026-03", "2026-06"]

    _create(2, "same-future", "AI Agent Developer")
    future_items = _candidates()
    assert second["candidate_id"] not in {item["candidate_id"] for item in future_items}
    canonical = next(
        item for item in future_items if item["candidate_id"] == first["candidate_id"]
    )
    assert "2026-09" in canonical["identity_profile"]["observed_window_ids"]

    audits = _audit_rows(second["candidate_id"])
    assert len(audits) == 1
    assert audits[0]["decision"] == "confirm_same"
    assert audits[0]["target_candidate_id"] == first["candidate_id"]
    assert audits[0]["reviewer"] == "reviewer-test"
    assert audits[0]["algorithm_version"] == SELECTED_DECISION_VERSION


def test_confirm_new_starts_independent_lifecycle() -> None:
    config = {**LOW_THRESHOLDS, **ABSTAIN_CONFIG}
    _create(0, "new-1", "AI Agent Developer", config)
    _create(1, "new-2", "AI Agent Developer", config)
    second = next(
        item
        for item in _candidates()
        if item["identity_resolution_state"] == "pending_review"
    )

    status, payload = _resolve(
        second["candidate_id"],
        resolution="confirm_new",
        idempotency_key="confirm-new-1",
    )
    assert status == 200
    assert payload["data"]["decision"] == "confirm_new"
    assert payload["data"]["target_candidate_id"] is None

    _create(2, "new-future", "AI Agent Developer", LOW_THRESHOLDS)
    items = {item["candidate_id"]: item for item in _candidates()}
    independent = items[second["candidate_id"]]
    assert independent["identity_resolution_state"] == "resolved_new"
    assert independent["status"] == "incubating"
    assert "2026-09" in independent["identity_profile"]["observed_window_ids"]


def test_resolution_is_idempotent_and_conflicting_resolution_is_rejected() -> None:
    _create(0, "idem-1", "AI Agent Developer", ABSTAIN_CONFIG)
    _create(1, "idem-2", "AI Agent Developer", ABSTAIN_CONFIG)
    second = next(
        item
        for item in _candidates()
        if item["identity_resolution_state"] == "pending_review"
    )
    first = next(
        item for item in _candidates() if item["candidate_id"] != second["candidate_id"]
    )

    status, payload = _resolve(
        second["candidate_id"],
        resolution="confirm_same",
        target_candidate_id=first["candidate_id"],
        idempotency_key="idempotent-resolution",
    )
    assert status == 200
    first_resolution_id = payload["data"]["resolution_id"]

    status, payload = _resolve(
        second["candidate_id"],
        resolution="confirm_same",
        target_candidate_id=first["candidate_id"],
        idempotency_key="idempotent-resolution",
    )
    assert status == 200
    assert payload["data"]["resolution_id"] == first_resolution_id

    status, _ = _resolve(second["candidate_id"], resolution="confirm_new")
    assert status == 422

    status, _ = _resolve(
        second["candidate_id"],
        resolution="confirm_same",
        target_candidate_id="different-target",
    )
    assert status == 422
