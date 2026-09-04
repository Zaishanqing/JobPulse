from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.use_cases import get_position_discovery_handlers
from app.contexts.discovery import QueryPositionDiscovery
from app.domain.accounts import AccountActor
from app.infrastructure.discovery_datasets import (
    formal_discovery_experiment_clusters,
    formal_discovery_experiment_report,
)
from app.main import app
from app.models.emerging_position import EmergingPosition
from app.models.emerging_definition_version import EmergingDefinitionVersion
from tests.runtime_database import SessionLocal, reset_database_data


def forbidden_database():
    raise AssertionError("Asset display must not open a database unit of work")


@pytest.fixture
def client():
    reset_database_data()
    query = QueryPositionDiscovery(
        forbidden_database,
        experiment_report_loader=formal_discovery_experiment_report,
        experiment_clusters_loader=formal_discovery_experiment_clusters,
    )
    app.dependency_overrides[get_position_discovery_handlers] = lambda: SimpleNamespace(query=query)
    app.dependency_overrides[get_account_actor] = lambda: AccountActor("asset-reader", "personal_user")
    yield TestClient(app)
    app.dependency_overrides.pop(get_position_discovery_handlers, None)
    app.dependency_overrides.pop(get_account_actor, None)
    reset_database_data()


def test_all_ten_assets_have_direct_details_without_publication_database(client):
    result = client.get("/api/v1/portal/emerging-assets")
    assert result.status_code == 200
    rows = result.json()["data"]
    assert len(rows) == 10
    assert len({row["emerging_id"] for row in rows}) == 10
    for row in rows:
        detail = client.get("/api/v1/portal/emerging-assets/" + quote(row["emerging_id"], safe=""))
        assert detail.status_code == 200
        assert detail.json()["data"] == row
        assert row["required_skills"] and row["core_responsibilities"] and row["industry_scenarios"]
        assert row["asset_definition"]["position_summary"]
        assert row["source_kind"] == "discovery_asset"
        assert row["status"] == "discovered"
        assert "published_snapshot" not in row
        assert any(skill.get("evidence") for skill in row["required_skills"])
    assert client.get("/api/v1/portal/emerging-assets/formal:missing").status_code == 404


def test_asset_reader_does_not_gain_access_to_discovery_admin_api(client):
    assert client.get("/api/v1/portal/admin/discovery-formal-experiment/clusters").status_code == 403


def test_assets_require_authentication(client):
    app.dependency_overrides.pop(get_account_actor)
    assert client.get("/api/v1/portal/emerging-assets").status_code == 401


def test_asset_edit_creates_candidates_and_versions_without_overwriting_baseline(client):
    app.dependency_overrides[get_account_actor] = lambda: AccountActor("asset-admin", "admin")
    original = client.get("/api/v1/portal/emerging-assets").json()["data"][0]
    path = "/api/v1/portal/emerging-assets/" + quote(original["emerging_id"], safe="")
    payload = {
        "position_name": "人工优化岗位",
        "core_responsibilities": ["人工修改的职责"],
        "bonus_skills": [],
        "field_evidence": {**original["field_evidence"], "position_summary": {"content": "人工优化概述"}},
    }
    saved = client.put(path, json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["data"]["position_name"] == "人工优化岗位"
    assert saved.json()["data"]["asset_definition"]["field_evidence"]["position_summary"]["content"] == "人工优化概述"
    assert client.get(path).json()["data"]["core_responsibilities"] == ["人工修改的职责"]
    assert any(row["position_name"] == "人工优化岗位" for row in client.get("/api/v1/portal/emerging-assets").json()["data"])
    with SessionLocal() as session:
        assert session.query(EmergingPosition).count() == 10
        assert session.query(EmergingDefinitionVersion).count() == 11
        candidate = session.get(EmergingPosition, original["governance_id"])
        assert candidate.status == "pending_review"
        assert candidate.position_name == "人工优化岗位"
        assert candidate.published_snapshot["definition"]["position_name"] == original["position_name"]
    assert client.put(path, json={"industry_scenarios": ["更新场景"]}).status_code == 200
    assert client.get(path).json()["data"]["position_name"] == "人工优化岗位"
    with SessionLocal() as session:
        assert session.query(EmergingPosition).count() == 10
        assert session.query(EmergingDefinitionVersion).count() == 12
    app.dependency_overrides[get_account_actor] = lambda: AccountActor("reader", "personal_user")
    assert client.get(path).json()["data"]["position_name"] == original["position_name"]
    assert client.put(path, json=payload).status_code == 403


def test_import_orders_candidate_before_initial_version_with_foreign_keys_enabled():
    from sqlalchemy import event
    from app.core.database import Base, create_database
    from app.contexts.emerging_positions import EmergingActor, ImportFormalExperimentResults
    from app.infrastructure.emerging_positions import SqlAlchemyEmergingPositionUnitOfWork
    from app.models.user import User

    database = create_database("sqlite://")
    @event.listens_for(database.engine, "connect")
    def enable_foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    try:
        Base.metadata.create_all(database.engine)
        with database.session_factory() as session:
            session.add(User(id="import-admin", username="import-admin", role="admin", hashed_password="unused"))
            session.commit()
        command = ImportFormalExperimentResults(
            lambda: SqlAlchemyEmergingPositionUnitOfWork(database.session_factory),
            formal_discovery_experiment_clusters,
        )
        assert command.execute(EmergingActor("import-admin", "admin")).imported == 10
        assert command.execute(EmergingActor("import-admin", "admin")).existing == 10
        with database.session_factory() as session:
            assert session.query(EmergingPosition).count() == 10
            assert session.query(EmergingDefinitionVersion).count() == 10
    finally:
        database.dispose()
