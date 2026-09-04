from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.v1.matches import _raise
from app.contexts.matching_learning._applications.matching import ManageMatching
from app.contexts.matching_learning.contracts_service import (
    NO_VALID_SPECIALTY_ROUTE,
    ROUTE_SUPPORT_UNAVAILABLE,
    STANDARD_POSITION_PROFILE_INSUFFICIENT,
    StandardPositionProfileInsufficient,
)
from app.domain.accounts import AccountActor


class _ResumeReader:
    def get(self, _resume_id):
        return SimpleNamespace(validated_cv_snapshot_id="snapshot-1")


class _Identities:
    def authorize_request(self, *args, **kwargs):
        return None

    def resolve(self, _actor):
        return SimpleNamespace(tenant_id="tenant-a", access_scope="tenant:tenant-a")


class _FlatProfileContracts:
    def cv_profile(self, _cv_id):
        return {}

    def position_profile(self, _position_id):
        return {
            "graph_version": "kg-published-v3",
            "required_skills": (
                {"skill_id": "skill-python"},
            ),
            "preferred_skills": (),
            "requirement_graph": None,
        }


class _RecordingMatchingService:
    def __init__(self):
        self.create_task_calls = 0

    def create_task(self, *args, **kwargs):
        self.create_task_calls += 1
        raise AssertionError("no task may be created for a flat standard profile")


def test_flat_standard_position_profile_fails_closed_before_task_creation():
    service = _RecordingMatchingService()
    manager = ManageMatching(
        lambda: None,
        _ResumeReader(),
        None,
        service,
        _Identities(),
        None,
        _FlatProfileContracts(),
    )

    with pytest.raises(StandardPositionProfileInsufficient) as insufficient:
        manager.run(
            AccountActor("user-1", "personal_user"),
            resume_id="resume-1",
            target_type="standard_position",
            target_id="position-1",
            use_enterprise_weights=False,
            generate_learning_path=False,
            idempotency_key="standard-position-no-route",
        )

    assert insufficient.value.code == "STANDARD_POSITION_PROFILE_INSUFFICIENT"
    assert insufficient.value.reason_code == NO_VALID_SPECIALTY_ROUTE
    assert service.create_task_calls == 0


def test_standard_position_insufficient_is_returned_as_a_business_error():
    with pytest.raises(HTTPException) as response:
        _raise(StandardPositionProfileInsufficient(NO_VALID_SPECIALTY_ROUTE))

    assert response.value.status_code == 422
    assert response.value.detail == {
        "error_code": "STANDARD_POSITION_PROFILE_INSUFFICIENT",
        "message": (
            "standard position does not have sufficient evidence to build a reliable "
            "matching route"
        ),
        "reason_code": NO_VALID_SPECIALTY_ROUTE,
    }


def test_matchable_positions_keep_stable_structure_when_specialty_route_is_missing():
    class _PositionCatalog:
        def get(self, _position_id):
            return SimpleNamespace(
                position_id="position-1",
                position_name="Standard Position",
                taxonomy_family_name=None,
                status="published",
                lifecycle_status="active",
                position_code="POS_1",
                taxonomy_version="position-taxonomy.v3.0.0",
            )

        def list(self):
            return [self.get("position-1")]

    class _InsufficientContracts:
        def position_profile(self, _position_id):
            raise StandardPositionProfileInsufficient(ROUTE_SUPPORT_UNAVAILABLE)

    manager = ManageMatching(
        lambda: None,
        None,
        None,
        None,
        None,
        None,
        _InsufficientContracts(),
        _PositionCatalog(),
    )

    items = manager.matchable_positions(AccountActor("user-1", "personal_user"))

    assert len(items) == 1
    assert items[0].matchable is False
    assert items[0].reason == STANDARD_POSITION_PROFILE_INSUFFICIENT
    assert items[0].blockers == (
        STANDARD_POSITION_PROFILE_INSUFFICIENT,
        ROUTE_SUPPORT_UNAVAILABLE,
    )
