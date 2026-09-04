from __future__ import annotations

from calendar import monthrange
import hashlib
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.bootstrap.settings import Settings
from app.domain.lineage import ClusterLineageSpec, match_cluster_lineage
from tests.runtime_database import SessionLocal
from app.infrastructure.models import DiscoveryRun, InputSnapshot
from app.infrastructure.providers import (
    AgglomerativeClusteringAlgorithm,
    KnowledgeGraphPositionReferenceProvider,
    PositionReferenceError,
    TfidfSvdSkillEmbeddingProvider,
)
from app.api.contracts import DiscoveryRunRequest
from app.application.contracts import DiscoveryTimeWindow, HistoricalTimeWindow, RunDiscoveryCommand
from app.domain.discovery import JDStructuredData, JDSnapshot, PositionReference, SkillReference
from app.infrastructure.models import (
    AlgorithmConfigSnapshot,
    Cluster,
)
from app.infrastructure.repositories import SqlAlchemyClusterRepository
from app.main import app

client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}


def payload(*, month_offset: int = 0) -> dict:
    snapshots = []
    for index in range(3):
        month = index + 1 + month_offset
        year = 2026 + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        snapshots.append(
            {
                "source_fact_id": f"fact-{index}",
                "source_fact_version": "1",
                "jd_id": f"jd-{index}",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": "智能应用工程师",
                "source_name": f"platform-{index % 2}",
                "publish_date": f"{year}-{month:02d}-01",
                "content_hash": "sha256:" + hashlib.sha256(
                    f"remediation:{index}".encode()
                ).hexdigest(),
                "structured_data": {
                    "responsibilities": ["应用开发"],
                    "required_skills": [{"raw_skill": "Python"}, {"raw_skill": "RAG"}],
                    "bonus_skills": [],
                    "industry": "软件",
                    "business_scenarios": ["智能客服"],
                    "source_record_id": f"remediation-source-{index}",
                },
            }
        )
    time_windows = []
    for index in range(3):
        month = index + 1 + month_offset
        year = 2026 + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        end_day = monthrange(year, month)[1]
        time_windows.append(
            {
                "window_id": f"w{index + 1 + month_offset}",
                "start": f"{year}-{month:02d}-01",
                "end": f"{year}-{month:02d}-{end_day:02d}",
            }
        )
    return {
        "contract_version": "discovery.v2",
        "request_id": "remediation-request",
        "algorithm": "emerge_v3_2",
        "time_windows": time_windows,
        "snapshots": snapshots,
        "position_references": [
            {
                "position_id": "formal-1",
                "graph_version_id": "graph-v1",
                "required_skills": [{"normalized_skill_id": "java"}],
            }
        ],
        "config": {"dataset_id": "emerging-discovery-full-temporal-v1"},
    }


def application_command() -> RunDiscoveryCommand:
    request = DiscoveryRunRequest.model_validate(payload())
    return RunDiscoveryCommand(
        contract_version="discovery.v2",
        request_id=request.request_id or "test-request",
        algorithm="emerge_v3_2",
        snapshots=[
            JDSnapshot(
                source_fact_id=item.source_fact_id,
                source_fact_version=item.source_fact_version,
                window_id=f"w{item.publish_date.month}",
                jd_id=item.jd_id,
                schema_version=item.schema_version,
                review_status=item.review_status,
                title=item.title,
                source_name=item.source_name,
                publish_date=item.publish_date,
                structured_data=JDStructuredData(
                    responsibilities=tuple(item.structured_data.responsibilities),
                    required_skills=tuple(
                        SkillReference(
                            raw_skill=skill.raw_skill,
                            normalized_skill_id=skill.normalized_skill_id,
                        )
                        for skill in item.structured_data.required_skills
                    ),
                    bonus_skills=tuple(
                        SkillReference(
                            raw_skill=skill.raw_skill,
                            normalized_skill_id=skill.normalized_skill_id,
                        )
                        for skill in item.structured_data.bonus_skills
                    ),
                    business_scenarios=tuple(item.structured_data.business_scenarios),
                    position_title=item.structured_data.position_title,
                    industry=item.structured_data.industry,
                ),
                consumption_path=item.consumption_path,
            )
            for item in request.snapshots
        ],
        position_references=[
            PositionReference(
                item.position_id,
                tuple(
                    SkillReference(
                        raw_skill=skill.raw_skill,
                        normalized_skill_id=skill.normalized_skill_id,
                    )
                    for skill in item.required_skills
                ),
                item.graph_version_id,
            )
            for item in request.position_references
        ],
        config=request.config,
        time_window=DiscoveryTimeWindow(
            request.time_windows[0].start,
            request.time_windows[-1].end,
            tuple(
                HistoricalTimeWindow(item.window_id, item.start, item.end)
                for item in request.time_windows
            ),
        ),
    )


def test_internal_auth_and_v2_validation_have_zero_writes():
    assert client.post("/api/v1/discovery-runs", json=payload()).status_code == 401
    assert client.get("/api/v1/discovery-runs/not-found").status_code == 401
    invalid = payload()
    invalid["snapshots"][0]["schema_version"] = "v1"
    assert client.post("/api/v1/discovery-runs", json=invalid, headers=HEADERS).status_code == 422
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 0
        assert db.query(InputSnapshot).count() == 0

    incomplete = payload()
    del incomplete["snapshots"][0]["structured_data"]["required_skills"]
    assert (
        client.post("/api/v1/discovery-runs", json=incomplete, headers=HEADERS).status_code == 422
    )
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 0

    unapproved = payload()
    unapproved["snapshots"][0]["review_status"] = "draft"
    assert (
        client.post("/api/v1/discovery-runs", json=unapproved, headers=HEADERS).status_code == 422
    )
    duplicate = payload()
    duplicate["snapshots"][1]["jd_id"] = duplicate["snapshots"][0]["jd_id"]
    assert client.post("/api/v1/discovery-runs", json=duplicate, headers=HEADERS).status_code == 422
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 0
        assert db.query(InputSnapshot).count() == 0


def test_saved_config_snapshot_keeps_old_run_explanation_stable():
    old_payload = payload()
    old_payload["request_id"] = "remediation-old-config"
    old_payload["config"] = {
        "dataset_id": "emerging-discovery-full-temporal-v1",
        "emerging_threshold": 0.50,
    }
    new_payload = payload(month_offset=3)
    new_payload["request_id"] = "remediation-new-config"
    new_payload["config"] = {
        "dataset_id": "emerging-discovery-full-temporal-v1",
        "emerging_threshold": 0.80,
    }

    old_run = client.post("/api/v1/discovery-runs", json=old_payload, headers=HEADERS).json()[
        "data"
    ]
    new_run = client.post("/api/v1/discovery-runs", json=new_payload, headers=HEADERS).json()[
        "data"
    ]
    old_again = client.get(f"/api/v1/discovery-runs/{old_run['run_id']}", headers=HEADERS).json()[
        "data"
    ]

    old_assessment = old_run["clusters"][0]["germination_assessment"]
    new_assessment = new_run["clusters"][0]["germination_assessment"]
    repeated_assessment = old_again["clusters"][0]["germination_assessment"]
    assert old_assessment["score_dimensions"] == repeated_assessment["score_dimensions"]
    assert old_assessment["evidence_package"] == repeated_assessment["evidence_package"]
    assert (
        old_assessment["evidence_package"]["thresholds"]["emerging"]
        != new_assessment["evidence_package"]["thresholds"]["emerging"]
    )


def test_embeddings_are_used_by_clustering_and_unknown_jobs_are_not_dropped():
    algorithm = AgglomerativeClusteringAlgorithm(similarity_threshold=0.5)
    snapshots = application_command().snapshots[:2]
    together = algorithm.cluster(snapshots, [[1.0, 0.0], [1.0, 0.0]])
    apart = algorithm.cluster(snapshots, [[1.0, 0.0], [0.0, 1.0]])
    assert len(together) == 1
    assert len(apart) == 2
    unknown = [replace(snapshots[0], jd_id="unknown", title="会计")]
    assert algorithm.cluster(unknown, [[0.2, 0.7]])[0].members[0].jd_id == "unknown"
    assert TfidfSvdSkillEmbeddingProvider().embed(snapshots)


def test_production_rejects_fake_provider_and_weak_credentials():
    with pytest.raises(ValueError, match="strong"):
        Settings(
            ENVIRONMENT="production",
            INTERNAL_SERVICE_TOKEN="weak",
            POSITION_REFERENCE_PROVIDER="knowledge_graph_http",
        )
    with pytest.raises(ValueError, match="fake"):
        Settings(
            ENVIRONMENT="production",
            INTERNAL_SERVICE_TOKEN="a" * 40,
            KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password",
            POSITION_REFERENCE_PROVIDER="payload_fake",
        )
    with pytest.raises(ValueError, match="distinct"):
        Settings(
            ENVIRONMENT="production",
            INTERNAL_SERVICE_TOKEN="a" * 40,
            MAINTENANCE_TOKEN="a" * 40,
            KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password",
            POSITION_REFERENCE_PROVIDER="knowledge_graph_http",
        )


def test_production_requires_strong_formal_knowledge_graph_password(monkeypatch):
    base = {
        "ENVIRONMENT": "production",
        "INTERNAL_SERVICE_TOKEN": "a" * 40,
        "MAINTENANCE_TOKEN": "b" * 40,
        "POSITION_REFERENCE_PROVIDER": "knowledge_graph_http",
    }
    with pytest.raises(ValueError, match="explicitly configured"):
        Settings(**base)
    with pytest.raises(ValueError, match="strong non-placeholder"):
        Settings(**base, KNOWLEDGE_GRAPH_SERVICE_PASSWORD="change-me")
    configured = Settings(**base, KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password")
    assert configured.KNOWLEDGE_GRAPH_SERVICE_PASSWORD == "a-unique-kg-service-password"

    monkeypatch.setenv("KG_SERVICE_PASSWORD", "legacy-alias-password-is-not-formal")
    with pytest.raises(ValueError, match="aliases"):
        Settings(**base, KNOWLEDGE_GRAPH_SERVICE_PASSWORD="a-unique-kg-service-password")


def test_missing_reference_is_rejected_and_single_window_is_explicitly_unavailable():
    missing = payload()
    missing["position_references"] = []
    assert client.post("/api/v1/discovery-runs", json=missing, headers=HEADERS).status_code == 422
    single = payload()
    for snapshot in single["snapshots"]:
        snapshot["publish_date"] = "2026-01-01"
    response = client.post("/api/v1/discovery-runs", json=single, headers=HEADERS)
    assert response.status_code == 201
    cluster = response.json()["data"]["clusters"][0]
    emergence = cluster["germination_assessment"]["evidence_package"]["emergence_index"]
    assert emergence["dimensions"]["cross_window_persistence"]["normalized_value"] < 1
    assert cluster["germination_assessment"]["qualified_as_emerging"] is False
    with SessionLocal() as db:
        assert db.query(DiscoveryRun).count() == 1


def test_repeated_single_platform_jd_is_not_high_potential():
    repeated = payload()
    template = repeated["snapshots"][0]
    repeated["snapshots"] = []
    for index, publish_date in enumerate(("2026-01-01", "2026-02-01", "2026-03-01")):
        repeated["snapshots"].append(
            {
                **template,
                "source_fact_id": f"duplicate-fact-{index}",
                "jd_id": f"duplicate-{index}",
                "source_fact_version": f"source-record-v{index + 1}",
                "publish_date": publish_date,
                "source_name": "one-platform",
            }
        )
    response = client.post("/api/v1/discovery-runs", json=repeated, headers=HEADERS)
    assert response.status_code == 201
    assessment = response.json()["data"]["clusters"][0]["germination_assessment"]
    assert assessment["qualified_as_emerging"] is False
    assert assessment["level"] == "watchlist"
    assert assessment["evidence_package"]["effective_sample_count"] == 1
    assert assessment["score_dimensions"]["duplicate_sample_penalty"] < 0


def test_source_identity_and_version_are_stable_without_content_signatures():
    first = JDSnapshot(
        jd_id="jd-1",
        schema_version="v2",
        review_status="published",
        title="智能应用工程师",
        source_name="platform",
        publish_date=date(2026, 1, 1),
        structured_data=JDStructuredData((), (), (), ()),
        source_fact_id="fact-1",
        source_fact_version="source-v1",
    )
    same_version = replace(first)
    changed_version = replace(first, source_fact_version="source-v2")
    assert (first.source_fact_id, first.source_fact_version) == (
        same_version.source_fact_id,
        same_version.source_fact_version,
    )
    assert (first.source_fact_id, first.source_fact_version) != (
        changed_version.source_fact_id,
        changed_version.source_fact_version,
    )


def test_knowledge_graph_invalid_json_is_structured(monkeypatch):
    class Login:
        status_code = 200

        def json(self):
            return {"data": {"access_token": "token"}}

    class Invalid:
        status_code = 200

        def json(self):
            raise ValueError("bad json")

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: Login())
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: Invalid())
    provider = KnowledgeGraphPositionReferenceProvider("http://kg", "user", "password")
    with pytest.raises(PositionReferenceError) as captured:
        provider.resolve([])
    assert captured.value.code == "knowledge_graph_invalid_json"


@pytest.mark.parametrize(
    ("effect", "expected_code"),
    [
        (Exception("timeout-placeholder"), "knowledge_graph_timeout"),
        (Exception("connection-placeholder"), "knowledge_graph_unavailable"),
    ],
)
def test_knowledge_graph_transport_errors_are_structured(monkeypatch, effect, expected_code):
    import httpx

    actual = (
        httpx.ReadTimeout("timeout")
        if expected_code == "knowledge_graph_timeout"
        else httpx.ConnectError("refused")
    )
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: (_ for _ in ()).throw(actual))
    provider = KnowledgeGraphPositionReferenceProvider("http://kg", "user", "password")
    with pytest.raises(PositionReferenceError) as captured:
        provider.resolve([])
    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"status_code": 503, "body": {"code": 503}}, "knowledge_graph_http_error"),
        ({"status_code": 200, "body": {"code": 0}}, "knowledge_graph_contract_error"),
        ({"status_code": 200, "body": {"code": 0, "data": []}}, "knowledge_graph_empty_reference"),
    ],
)
def test_knowledge_graph_http_and_contract_errors_are_structured(
    monkeypatch, response, expected_code
):
    class Reply:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self.body = body

        def json(self):
            return self.body

    login = Reply(200, {"data": {"access_token": "token"}})
    upstream = Reply(response["status_code"], response["body"])
    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: login)
    monkeypatch.setattr("httpx.get", lambda *args, **kwargs: upstream)
    provider = KnowledgeGraphPositionReferenceProvider("http://kg", "user", "password")
    with pytest.raises(PositionReferenceError) as captured:
        provider.resolve([])
    assert captured.value.code == expected_code


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        ([], [{"id": "n", "members": {"1"}, "centroid": [1.0], "skills": {"x"}}], "birth"),
        ([{"id": "d", "members": {"1"}, "centroid": [1.0], "skills": {"x"}}], [], "decline"),
        (
            [{"id": "p", "members": {"1"}, "centroid": [1.0], "skills": {"x"}}],
            [{"id": "c", "members": {"1"}, "centroid": [1.0], "skills": {"x"}}],
            "continue",
        ),
        (
            [{"id": "p", "members": {"1", "2"}, "centroid": [1.0], "skills": {"x"}}],
            [
                {"id": "a", "members": {"1"}, "centroid": [1.0], "skills": {"x"}},
                {"id": "b", "members": {"2"}, "centroid": [1.0], "skills": {"x"}},
            ],
            "split",
        ),
        (
            [
                {"id": "a", "members": {"1"}, "centroid": [1.0], "skills": {"x"}},
                {"id": "b", "members": {"2"}, "centroid": [1.0], "skills": {"x"}},
            ],
            [{"id": "c", "members": {"1", "2"}, "centroid": [1.0], "skills": {"x"}}],
            "merge",
        ),
    ],
)
def test_lineage_relations(previous, current, expected):
    def specs(values):
        return [
            ClusterLineageSpec(
                item["id"],
                frozenset(item["members"]),
                tuple(item["centroid"]),
                frozenset(item["skills"]),
            )
            for item in values
        ]

    relations = match_cluster_lineage(specs(previous), specs(current))
    assert expected in {item.relation_type for item in relations}
    assert all(item.decision_version for item in relations)
    assert all(item.evidence_cluster_ids for item in relations)
    assert all(item.decision_reason for item in relations)
    assert all(item.threshold == 0.35 for item in relations)


def test_lineage_reads_only_the_latest_adjacent_completed_window():
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        cluster_ids = {}
        for label, start, end in (
            ("old", date(2025, 1, 1), date(2025, 3, 31)),
            ("adjacent", date(2025, 4, 1), date(2025, 6, 15)),
            ("incompatible", date(2025, 4, 1), date(2025, 6, 30)),
            ("future", date(2025, 10, 1), date(2025, 12, 31)),
            ("no-window", None, None),
            ("invalid-window", date(2025, 6, 20), date(2025, 6, 10)),
        ):
            run = DiscoveryRun(
                request_id=f"request-{label}",
                status="succeeded",
                algorithm_version="test",
                formula_version="test",
                time_window_start=start,
                time_window_end=end,
                completed_at=now,
            )
            db.add(run)
            db.flush()
            db.add(
                AlgorithmConfigSnapshot(
                    run_id=run.id,
                    algorithm_version="test",
                    formula_version="test",
                    config={
                        "lineage_compatibility": "different" if label == "incompatible" else "compatible"
                    },
                )
            )
            cluster = Cluster(
                run_id=run.id,
                cluster_key=label,
                cluster_name=label,
                sample_count=1,
                core_skills=[label],
                representative_titles=[label],
                representative_members=[],
                core_responsibilities=[],
                semantic_centroid=[],
                algorithm_sources=["test"],
                merge_basis={"rule": "test"},
                stability_score=1.0,
                growth_score=0.5,
                distance_from_existing_positions=0.5,
                feature_summary={"centroid": [1.0]},
            )
            db.add(cluster)
            db.flush()
            cluster_ids[label] = cluster.id
        db.commit()
        selected = SqlAlchemyClusterRepository(db).latest_specs_before(
            date(2025, 7, 1), date(2025, 9, 30), "compatible"
        )
        assert [item.cluster_id for item in selected] == [cluster_ids["adjacent"]]
        assert SqlAlchemyClusterRepository(db).latest_specs_before(None, None, "compatible") == []
