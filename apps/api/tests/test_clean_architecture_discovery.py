from __future__ import annotations

import ast
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from app.application.discovery import RunDiscoveryCommand, run_position_discovery
from app.contexts.discovery import QueryPositionDiscovery, build_rolling_discovery_requests
from tests.runtime_database import SessionLocal, reset_database_data
import app.models  # noqa: F401
from app.infrastructure.discovery import (
    SqlAlchemyDiscoveryRepository,
    SqlAlchemyDiscoveryUnitOfWork,
    discovery_run_result,
)
from app.models.jd import JobDescription
from app.models.jd_parse_result import JDParseResult
from app.models.position_cluster import PositionCluster
from app.models.task_record import TaskRecord
from app.models.user import User
from app.ports.discovery import Actor, ClusterProjection, DiscoveryRunRequest, ReleasedJDFact
from app.domain.errors import (
    ExternalGatewayError,
    NoReleasedJDFacts,
    PermissionDenied,
    ProjectionConflict,
)
from app.domain.values import freeze


ROOT = Path(__file__).resolve().parents[1]


def test_published_source_fact_uses_jd_publish_date_as_observation_date():
    jd = JobDescription(publish_date=date(2026, 7, 30))
    publication = type(
        "Publication",
        (),
        {
            "id": "publication-1",
            "created_at": datetime(2026, 7, 30, tzinfo=timezone.utc),
            "jd_id": "jd-1",
            "source_jd_version_id": "version-1",
            "schema_version": "v2",
            "snapshot_payload": {
                "extraction_result": {
                    "company_facts": [
                        {"kind": "company_name", "value": "示例科技"}
                    ]
                },
                "jd": {
                    "title": "RAG Engineer",
                    "source_name": "market",
                    "publish_date": "2026-07-30",
                },
                "legacy": {
                    "position_title": "RAG Engineer",
                    "responsibilities": [],
                    "required_skills": [],
                    "bonus_skills": [],
                    "industry": "AI",
                    "business_scenarios": [],
                },
            },
        },
    )()
    source_version = type(
        "SourceVersion",
        (),
        {"crawl_time": datetime(2026, 7, 16, 8, tzinfo=timezone.utc)},
    )()

    fact = SqlAlchemyDiscoveryRepository._publication_fact(
        jd, publication, source_version
    )

    assert fact.publish_date == date(2026, 7, 30)
    assert fact.structured_data["company_name"] == "示例科技"


class FakeRepository:
    def __init__(self) -> None:
        self.released_fact_list_calls = 0
        self.facts = [
            ReleasedJDFact(
                source_fact_id="fact-1",
                source_fact_version="1",
                jd_id="jd-1",
                title="RAG 工程师",
                source_name="emerging-discovery-full-temporal-v1:market",
                publish_date=date(2026, 7, 1),
                structured_data={
                    "position_title": "RAG 工程师",
                    "responsibilities": [],
                    "required_skills": [{"raw_skill": "RAG"}],
                    "bonus_skills": [],
                    "industry": "AI",
                    "business_scenarios": [],
                },
            )
        ]
        self.clusters: dict[str, ClusterProjection] = {}
        self.task_inputs = []
        self.tasks = {}

    def list_released_jd_facts(self):
        self.released_fact_list_calls += 1
        return self.facts

    def list_dataset_jd_facts(self, dataset_id):
        return [fact for fact in self.facts if fact.bundle_id == dataset_id]

    def dataset_time_windows(self, dataset_id):
        return None

    def discovery_config(self):
        return {"minimum_sample_size": 1}

    def get_cluster(self, cluster_id):
        return self.clusters.get(cluster_id)

    def add_cluster(self, projection):
        self.clusters[projection.cluster_id] = projection

    def get_task(self, task_id):
        return self.tasks.get(task_id)

    def record_succeeded_task(self, **values):
        self.task_inputs.append(values)
        task = {"task_id": values["task_id"], **values["result_payload"]}
        self.tasks[values["task_id"]] = task
        return task


class FakeUnitOfWork:
    def __init__(self, repository=None) -> None:
        self.repository = repository or FakeRepository()
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        self._snapshot = deepcopy(
            (self.repository.clusters, self.repository.tasks, self.repository.task_inputs)
        )
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.rollback()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True
        clusters, tasks, task_inputs = self._snapshot
        self.repository.clusters = clusters
        self.repository.tasks = tasks
        self.repository.task_inputs = task_inputs


class FakeGateway:
    def __init__(self, run_id="run-1") -> None:
        self.run_id = run_id
        self.payload = None
        self.calls = 0
        self.algorithm_executions = 0
        self.runs_by_request = {}

    def create_run(self, payload: DiscoveryRunRequest):
        self.calls += 1
        self.payload = payload
        existing = self.runs_by_request.get(payload.request_id)
        if existing is not None:
            return discovery_run_result(existing)
        self.algorithm_executions += 1
        result = {
            "contract_version": "discovery.v2",
            "run_id": self.run_id,
            "status": "succeeded",
            "algorithm_version": "discovery-v1",
            "clusters": [
                {
                    "cluster_id": "cluster-1",
                    "cluster_name": "RAG 岗位簇",
                    "sample_count": 1,
                    "core_skills": [{"raw_skill": "RAG"}],
                    "representative_titles": ["RAG 工程师"],
                    "representative_jd_ids": ["jd-1"],
                    "stability_score": 0.8,
                    "growth_score": 0.7,
                    "distance_from_existing_positions": 0.6,
                }
            ],
        }
        self.runs_by_request[payload.request_id] = result
        return discovery_run_result(result)


def test_recent_position_signals_are_projected_only_from_published_jd_facts():
    repository = FakeRepository()
    repository.facts = [
        ReleasedJDFact(
            source_fact_id="fact-data",
            source_fact_version="1",
            jd_id="jd-data",
            title="AI Agent 研发工程师(Data Agent)-【数据平台】",
            source_name="audited-package",
            publish_date=date(2026, 8, 1),
            structured_data={
                "responsibilities": ["建设 Multi-Agent 与 RAG 数据工作流"],
                "required_skills": [{"raw_skill": "RAG"}],
                "bonus_skills": [],
            },
        ),
        ReleasedJDFact(
            source_fact_id="fact-coding",
            source_fact_version="1",
            jd_id="jd-coding",
            title="AI Coding算法工程师/专家-Dev Infra",
            source_name="audited-package",
            publish_date=date(2026, 8, 7),
            structured_data={
                "responsibilities": [
                    "参与 Coding Agent 开发和 LLM 评测，构建高质量数据合成流程"
                ],
                "required_skills": [],
                "bonus_skills": [],
            },
        ),
        ReleasedJDFact(
            source_fact_id="reviewed-only",
            source_fact_version="1",
            jd_id="jd-not-published",
            title="Agent自进化算法工程师-AI Platform",
            source_name="draft",
            publish_date=date(2026, 8, 8),
            structured_data={
                "responsibilities": ["Agent 评测、归因分析与自动优化"],
                "required_skills": [],
                "bonus_skills": [],
            },
            review_status="reviewed",
            consumption_path="legacy_reviewed",
        ),
        ReleasedJDFact(
            source_fact_id="fact-ops",
            source_fact_version="1",
            jd_id="jd-ops",
            title="大模型Agentic Ops研发工程师-基础技术",
            source_name="audited-package",
            publish_date=date(2026, 8, 1),
            structured_data={
                "responsibilities": ["建设 RAG 与 AIOps 平台，支持根因定位和可观测性"],
                "required_skills": [],
                "bonus_skills": [],
            },
        ),
        ReleasedJDFact(
            source_fact_id="fact-science",
            source_fact_version="1",
            jd_id="jd-science",
            title="Agent平台工程师-AI for Science",
            source_name="audited-package",
            publish_date=date(2026, 8, 7),
            structured_data={
                "responsibilities": [
                    "建设 Auto Research 和 Agent Runtime，支持多Agent协作及可观测性"
                ],
                "required_skills": [],
                "bonus_skills": [],
            },
        ),
    ]

    signals = QueryPositionDiscovery(lambda: FakeUnitOfWork(repository)).recent_signals(
        Actor(actor_id="reader-1", role="personal_user")
    )

    assert [item.signal_id for item in signals] == [
        "data-agent",
        "ai-coding",
        "agentic-ops",
        "ai-for-science-agent",
    ]
    assert signals[0].source_jd_ids == ("jd-data",)
    assert signals[0].skills[:2] == ("Multi-Agent", "RAG")
    assert signals[1].representative_title == "AI Coding算法工程师/专家-Dev Infra"
    assert signals[1].skills == ("Coding Agent", "LLM 评测", "数据合成")
    assert signals[2].skills == ("AIOps", "根因定位", "可观测性")
    assert signals[3].skills == ("Auto Research", "Agent Runtime", "多 Agent 协作")


def test_recent_position_signals_reuse_bounded_query_cache():
    repository = FakeRepository()
    query = QueryPositionDiscovery(lambda: FakeUnitOfWork(repository))
    actor = Actor(actor_id="reader-1", role="personal_user")

    first = query.recent_signals(actor)
    second = query.recent_signals(actor)

    assert first is second
    assert repository.released_fact_list_calls == 1


def test_discovery_run_result_preserves_optional_input_fingerprint():
    result = discovery_run_result(
        {
            "run_id": "run-1",
            "status": "succeeded",
            "algorithm_version": "discovery-v1",
            "clusters": [],
            "input_fingerprint": "sha256:abc123",
        }
    )

    assert result.input_fingerprint == "sha256:abc123"


def test_rolling_requests_partition_into_exact_three_day_windows_without_future_leakage():
    def fact(jd_id: str, published: date | None) -> ReleasedJDFact:
        return ReleasedJDFact(
            source_fact_id=f"fact-{jd_id}",
            source_fact_version="1",
            jd_id=jd_id,
            title=f"岗位 {jd_id}",
            source_name="market",
            publish_date=published,
            structured_data={
                "position_title": f"岗位 {jd_id}",
                "responsibilities": [],
                "required_skills": [],
                "bonus_skills": [],
                "industry": "AI",
                "business_scenarios": [],
            },
        )

    requests = build_rolling_discovery_requests(
        RunDiscoveryCommand(request_id="ui-request"),
        [
            fact("day-1", date(2026, 1, 15)),
            fact("day-2", date(2026, 1, 16)),
            fact("missing", None),
            fact("day-4", date(2026, 1, 18)),
            fact("day-10", date(2026, 1, 24)),
        ],
        freeze({"candidate_lifecycle_version": "candidate-lifecycle-v2"}),
    )

    assert len(requests) == 3
    assert all(
        (window.end - window.start).days == 2
        for request in requests
        for window in request.time_windows
    )
    assert [[fact.jd_id for fact in item.snapshots] for item in requests] == [
        ["day-1"],
        ["day-1", "day-2", "day-4"],
        ["day-2", "day-4", "day-10"],
    ]
    assert len(requests[0].time_windows) == 3
    assert len(requests[-1].time_windows) == 3
    assert all(
        any(window.start <= fact.publish_date <= window.end for window in request.time_windows)
        for request in requests
        for fact in request.snapshots
    )
    assert all("ui-request" not in request.request_id for request in requests)

    scoped = build_rolling_discovery_requests(
        RunDiscoveryCommand(request_id="curated", jd_ids=("day-1", "day-2")),
        [fact("day-1", date(2026, 1, 15)), fact("day-2", date(2026, 1, 16))],
        freeze({"candidate_lifecycle_version": "candidate-lifecycle-v2"}),
    )
    scope_ids = {
        window.window_id.rsplit("@", 1)[-1]
        for request in scoped
        for window in request.time_windows
    }
    assert len(scope_ids) == 1
    assert all("@" in window.window_id for window in scoped[-1].time_windows)


def test_discovery_use_case_filters_to_requested_published_jds():
    repository = FakeRepository()
    repository.facts.append(
        ReleasedJDFact(
            source_fact_id="fact-2",
            source_fact_version="1",
            jd_id="jd-2",
            title="普通后端工程师",
            source_name="market",
            publish_date=date(2026, 7, 1),
            structured_data={
                "position_title": "普通后端工程师",
                "responsibilities": [],
                "required_skills": [],
                "bonus_skills": [],
                "industry": "software",
                "business_scenarios": [],
            },
        )
    )
    gateway = FakeGateway()

    run_position_discovery(
        RunDiscoveryCommand(jd_ids=("jd-1",)),
        Actor(actor_id="admin-1", role="admin"),
        FakeUnitOfWork(repository),
        gateway,
    )

    assert [fact.jd_id for fact in gateway.payload.snapshots] == ["jd-1"]


def test_discovery_use_case_filters_to_named_replay_bundle_without_global_fallback():
    repository = FakeRepository()
    repository.facts = [
        ReleasedJDFact(
            source_fact_id=f"fact-{jd_id}",
            source_fact_version="1",
            jd_id=jd_id,
            title=jd_id,
            source_name=source_name,
            bundle_id=bundle_id,
            publish_date=date(2026, 8, day),
            structured_data={
                "position_title": jd_id,
                "responsibilities": [],
                "required_skills": [],
                "bonus_skills": [],
                "industry": "AI",
                "business_scenarios": [],
            },
        )
        for jd_id, source_name, bundle_id, day in (
            (
                "replay-jd",
                "招聘平台",
                "d5-short-window-main-v1-37585b4079dd",
                1,
            ),
            ("global-jd", "招聘平台", "another-import", 2),
        )
    ]
    gateway = FakeGateway()

    run_position_discovery(
        RunDiscoveryCommand(dataset_id="d5-short-window-main-v1-37585b4079dd"),
        Actor(actor_id="admin-1", role="admin"),
        FakeUnitOfWork(repository),
        gateway,
    )

    assert [fact.jd_id for fact in gateway.payload.snapshots] == ["replay-jd"]


def test_discovery_use_case_rejects_unknown_named_dataset():
    with pytest.raises(NoReleasedJDFacts, match="Unknown discovery dataset"):
        run_position_discovery(
            RunDiscoveryCommand(dataset_id="unknown-dataset"),
            Actor(actor_id="admin-1", role="admin"),
            FakeUnitOfWork(),
            FakeGateway(),
        )


def test_discovery_use_case_consumes_released_fact_contract_and_owns_transaction():
    uow = FakeUnitOfWork()
    gateway = FakeGateway()

    task = run_position_discovery(
        RunDiscoveryCommand(),
        Actor(actor_id="admin-1", role="admin"),
        uow,
        gateway,
    )

    snapshot = gateway.payload.snapshots[0]
    assert snapshot.schema_version == "v2"
    assert snapshot.review_status == "published"
    assert snapshot.consumption_path == "published"
    assert snapshot.structured_data["required_skills"] == ({"raw_skill": "RAG"},)
    assert task["cluster_ids"] == ["cluster-1"]
    assert uow.committed is True
    assert uow.rolled_back is False


def test_discovery_use_case_rolls_back_projection_conflict():
    repository = FakeRepository()
    repository.clusters["cluster-1"] = ClusterProjection(
        cluster_id="cluster-1",
        discovery_run_id="another-run",
        cluster_name="existing",
        algorithm_version="v1",
        sample_count=1,
    )
    uow = FakeUnitOfWork(repository)

    with pytest.raises(ProjectionConflict):
        run_position_discovery(
            RunDiscoveryCommand(),
            Actor(actor_id="admin-1", role="admin"),
            uow,
            FakeGateway(),
        )

    assert uow.committed is False
    assert uow.rolled_back is True


def test_discovery_permission_is_enforced_before_ports_are_called():
    uow = FakeUnitOfWork()
    gateway = FakeGateway()

    with pytest.raises(PermissionDenied):
        run_position_discovery(
            RunDiscoveryCommand(),
            Actor(actor_id="user-1", role="personal_user"),
            uow,
            gateway,
        )

    assert gateway.payload is None
    assert uow.committed is False


def test_same_logical_request_returns_one_local_task_and_one_remote_run():
    uow = FakeUnitOfWork()
    gateway = FakeGateway()
    actor = Actor(actor_id="admin-1", role="admin")

    first = run_position_discovery(RunDiscoveryCommand(), actor, uow, gateway)
    second = run_position_discovery(RunDiscoveryCommand(), actor, uow, gateway)

    assert first["task_id"] == second["task_id"]
    assert gateway.calls == 1
    assert gateway.algorithm_executions == 1


def test_remote_success_local_commit_failure_recovers_without_second_algorithm_run():
    class FailOnceUoW(FakeUnitOfWork):
        def __init__(self):
            super().__init__()
            self.fail_once = True

        def commit(self):
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("injected local commit failure")
            super().commit()

    uow = FailOnceUoW()
    gateway = FakeGateway()
    actor = Actor(actor_id="admin-1", role="admin")

    with pytest.raises(RuntimeError, match="injected local commit failure"):
        run_position_discovery(RunDiscoveryCommand(), actor, uow, gateway)
    assert uow.repository.clusters == {}
    assert uow.repository.tasks == {}

    recovered = run_position_discovery(RunDiscoveryCommand(), actor, uow, gateway)
    assert recovered["discovery_run_id"] == "run-1"
    assert gateway.calls == 2
    assert gateway.algorithm_executions == 1


class PersistentRemoteDiscoveryGateway:
    def __init__(self, path: Path) -> None:
        self.path = path
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "request_id TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS counters ("
                "name TEXT PRIMARY KEY, value INTEGER NOT NULL)"
            )

    def create_run(self, payload: DiscoveryRunRequest):
        with sqlite3.connect(self.path, timeout=10) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT result_json FROM runs WHERE request_id = ?",
                (payload.request_id,),
            ).fetchone()
            if row is not None:
                connection.commit()
                return discovery_run_result(json.loads(row[0]))
            value = connection.execute(
                "SELECT value FROM counters WHERE name = 'algorithm_executions'"
            ).fetchone()
            executions = 1 if value is None else value[0] + 1
            connection.execute(
                "INSERT OR REPLACE INTO counters (name, value) VALUES (?, ?)",
                ("algorithm_executions", executions),
            )
            result = {
                "contract_version": "discovery.v2",
                "run_id": f"run-{payload.request_id[-12:]}",
                "status": "succeeded",
                "algorithm_version": "discovery-v1",
                "clusters": [
                    {
                        "cluster_id": f"cluster-{payload.request_id[-12:]}",
                        "cluster_name": "Persistent RAG cluster",
                        "sample_count": len(payload.snapshots),
                        "core_skills": [{"raw_skill": "RAG"}],
                        "representative_titles": [
                            item.title for item in payload.snapshots
                        ],
                        "representative_jd_ids": [
                            item.jd_id for item in payload.snapshots
                        ],
                        "stability_score": 0.8,
                        "growth_score": 0.7,
                        "distance_from_existing_positions": 0.6,
                    }
                ],
            }
            connection.execute(
                "INSERT INTO runs (request_id, result_json) VALUES (?, ?)",
                (payload.request_id, json.dumps(result, sort_keys=True)),
            )
            connection.commit()
            return discovery_run_result(result)

    def count(self, table: str) -> int:
        with sqlite3.connect(self.path) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def algorithm_executions(self) -> int:
        with sqlite3.connect(self.path) as connection:
            row = connection.execute(
                "SELECT value FROM counters WHERE name = 'algorithm_executions'"
            ).fetchone()
            return 0 if row is None else row[0]


class FailOnceDiscoveryUnitOfWork(SqlAlchemyDiscoveryUnitOfWork):
    failed = False

    def commit(self) -> None:
        if not FailOnceDiscoveryUnitOfWork.failed:
            FailOnceDiscoveryUnitOfWork.failed = True
            raise RuntimeError("injected main commit failure")
        super().commit()


def _reset_main_database() -> None:
    reset_database_data()


def _seed_released_fact(session) -> User:
    user = User(
        id="admin-recovery",
        username="admin-recovery",
        hashed_password="x",
        role="admin",
    )
    jd = JobDescription(
        id="JD-RECOVERY",
        source_type="platform",
        source_name="market",
        title="RAG Engineer",
        raw_text="Build RAG systems",
        parse_status="completed",
        publish_date=date(2026, 8, 1),
    )
    parsed = JDParseResult(
        id="FACT-RECOVERY",
        jd_id=jd.id,
        position_title="RAG Engineer",
        responsibilities=["Build RAG systems"],
        required_skills=[{"raw_skill": "RAG"}],
        bonus_skills=[],
        industry="AI",
        business_scenarios=["assistant"],
        need_review=False,
        schema_version="v2",
        normalization_schema_version="v2",
        workflow_status="published",
    )
    session.add_all([user, jd, parsed])
    session.commit()
    return user


def test_real_persistent_recovery_uses_new_sessions_and_remote_run(tmp_path):
    _reset_main_database()
    FailOnceDiscoveryUnitOfWork.failed = False
    remote_db = tmp_path / "remote-discovery.db"
    command = RunDiscoveryCommand()
    actor = Actor(actor_id="admin-recovery", role="admin")

    first_session = SessionLocal()
    try:
        _seed_released_fact(first_session)
        with pytest.raises(RuntimeError, match="injected main commit failure"):
            run_position_discovery(
                command,
                actor,
                FailOnceDiscoveryUnitOfWork(first_session),
                PersistentRemoteDiscoveryGateway(remote_db),
            )
    finally:
        first_session.close()

    with SessionLocal() as verification:
        assert verification.query(TaskRecord).count() == 0
        assert verification.query(PositionCluster).count() == 0

    second_session = SessionLocal()
    try:
        recovered = run_position_discovery(
            command,
            actor,
            SqlAlchemyDiscoveryUnitOfWork(second_session),
            PersistentRemoteDiscoveryGateway(remote_db),
        )
    finally:
        second_session.close()

    remote = PersistentRemoteDiscoveryGateway(remote_db)
    assert remote.count("runs") == 1
    assert remote.algorithm_executions() == 1
    assert recovered["discovery_run_id"].startswith("run-")
    with SessionLocal() as verification:
        assert verification.query(TaskRecord).count() == 1
        assert verification.query(PositionCluster).count() == 1


def test_concurrent_real_sessions_share_one_remote_run_and_task(tmp_path):
    _reset_main_database()
    remote_db = tmp_path / "remote-discovery-concurrent.db"
    with SessionLocal() as session:
        _seed_released_fact(session)

    def invoke():
        session = SessionLocal()
        try:
            return run_position_discovery(
                RunDiscoveryCommand(),
                Actor(actor_id="admin-recovery", role="admin"),
                SqlAlchemyDiscoveryUnitOfWork(session),
                PersistentRemoteDiscoveryGateway(remote_db),
            )
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    assert len({item["task_id"] for item in results}) == 1
    remote = PersistentRemoteDiscoveryGateway(remote_db)
    assert remote.count("runs") == 1
    assert remote.algorithm_executions() == 1
    with SessionLocal() as verification:
        assert verification.query(TaskRecord).count() == 1
        assert verification.query(PositionCluster).count() == 1


def test_remote_http_failure_leaves_no_local_half_product():
    class FailedGateway:
        def create_run(self, payload):
            raise ExternalGatewayError("remote unavailable")

    uow = FakeUnitOfWork()
    with pytest.raises(ExternalGatewayError):
        run_position_discovery(
            RunDiscoveryCommand(),
            Actor(actor_id="admin-1", role="admin"),
            uow,
            FailedGateway(),
        )
    assert uow.repository.clusters == {}
    assert uow.repository.tasks == {}
    assert uow.rolled_back is True


def test_discovery_application_and_ports_have_no_framework_or_adapter_dependency():
    paths = [
        ROOT / "app" / "application" / "discovery.py",
        ROOT / "app" / "ports" / "discovery.py",
        ROOT / "app" / "domain" / "errors.py",
    ]
    forbidden = ("fastapi", "sqlalchemy", "app.models", "app.infrastructure", "app.integrations")
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        assert not any(module.startswith(forbidden) for module in imports), path
