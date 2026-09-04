from __future__ import annotations

import ast
from datetime import date
from pathlib import Path
import subprocess
import sys

from app.api.discovery_mapping import cluster_data
from app.contexts.discovery import (
    Actor,
    ClusterProjection,
    DiscoveryRunRequest,
    HistoricalTimeWindow,
    ReleasedJDFact,
    released_jd_contract,
)
from app.domain.emerging_position import GerminationAssessment
from app.domain.values import freeze
from app.infrastructure.discovery import (
    SqlAlchemyDiscoveryRepository,
    discovery_run_payload,
)
from app.infrastructure.emerging_positions import SqlAlchemyEmergingPositionRepository
from app.contexts.platform import DEFAULT_CONFIGS


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.append(node.module)
    return result


def _released_fact() -> ReleasedJDFact:
    return ReleasedJDFact(
        source_fact_id="fact-1",
        source_fact_version="1",
        jd_id="jd-1",
        title="RAG 工程师",
        source_name="market",
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


def test_legacy_discovery_and_emerging_types_are_identity_preserving_reexports() -> None:
    from app.contexts import discovery as discovery_context
    from app.contexts import emerging_positions as emerging_context
    from app.contexts.discovery import contracts as discovery_compatibility_contracts
    from app.ports import discovery as legacy_discovery
    from app.ports import emerging_position as legacy_emerging

    discovery_names = (
        "Actor",
        "ClusterJDRecord",
        "ClusterProjection",
        "DiscoveryClusterResult",
        "DiscoveryGateway",
        "DiscoveryRepository",
        "DiscoveryRunRequest",
        "DiscoveryRunResult",
        "DiscoveryUnitOfWork",
        "ReleasedJDFact",
    )
    emerging_names = (
        "ClusterRecord",
        "DefinitionSelectionRecord",
        "DefinitionVersionRecord",
        "DuplicateEmergingProjection",
        "EmergingActor",
        "EmergingChanges",
        "EmergingPositionRepository",
        "EmergingPositionUnitOfWork",
        "EmergingRecord",
        "GeneratedDefinitionRecord",
        "GerminationAssessmentRecord",
        "ReleaseGateConfig",
        "StandardPositionRecord",
    )
    for name in discovery_names:
        assert getattr(legacy_discovery, name) is getattr(discovery_context, name)
    for name in ("Actor", "ClusterJDRecord", "ClusterProjection", "ReleasedJDFact"):
        assert getattr(discovery_compatibility_contracts, name) is getattr(
            discovery_context, name
        )
    for name in emerging_names:
        assert getattr(legacy_emerging, name) is getattr(emerging_context, name)


def test_discovery_v2_source_identity_and_http_provider_json_include_three_windows() -> None:
    fact = _released_fact()
    assert (fact.source_fact_id, fact.source_fact_version) == ("fact-1", "1")
    request = DiscoveryRunRequest(
        contract_version="discovery.v2",
        request_id="request-1",
        algorithm="multi_view",
        time_window_start=date(2026, 7, 1),
        time_window_end=date(2026, 7, 3),
        snapshots=(fact,),
        config=freeze({"minimum_sample_size": 1}),
        time_windows=(
            HistoricalTimeWindow("2026-05", date(2026, 5, 1), date(2026, 5, 31)),
            HistoricalTimeWindow("2026-06", date(2026, 6, 1), date(2026, 6, 30)),
            HistoricalTimeWindow("2026-07", date(2026, 7, 1), date(2026, 7, 31)),
        ),
        current_observation_window_id="2026-07",
    )

    assert discovery_run_payload(request) == {
        "contract_version": "discovery.v2",
        "request_id": "request-1",
        "algorithm": "multi_view",
        "time_windows": [
            {"window_id": "2026-05", "start": "2026-05-01", "end": "2026-05-31"},
            {"window_id": "2026-06", "start": "2026-06-01", "end": "2026-06-30"},
            {"window_id": "2026-07", "start": "2026-07-01", "end": "2026-07-31"},
        ],
        "current_observation_window_id": "2026-07",
        "snapshots": [released_jd_contract(fact)],
        "config": {"minimum_sample_size": 1},
    }
    assert released_jd_contract(fact) == {
        "source_fact_id": "fact-1",
        "source_fact_version": "1",
        "jd_id": "jd-1",
        "schema_version": "v2",
        "review_status": "published",
        "consumption_path": "published",
        "title": "RAG 工程师",
        "source_name": "market",
        "publish_date": "2026-07-01",
        "structured_data": {
            "position_title": "RAG 工程师",
            "responsibilities": [],
            "required_skills": [{"raw_skill": "RAG"}],
            "bonus_skills": [],
            "industry": "AI",
            "business_scenarios": [],
        },
    }


def test_product_discovery_defaults_enable_promoted_temporal_quality_pipeline() -> None:
    config = DEFAULT_CONFIGS["germination-score"]
    assert config["candidate_identity_linker_version"] == (
        "identity-v2-semantic-temporal-verifier.v1"
    )
    assert config["candidate_lifecycle_version"] == "candidate-lifecycle-v2"
    assert config["split_refinement_enabled"] is False
    assert config["formal_algorithm_version"] == "emerge-v3.2"
    assert config["candidate_clustering_version"] == "occupation-title-key-v1"
    assert config["stage2_unit"] == "occupation_cluster"
    assert config["anti_over_split_guards"] is True
    assert config["company_partition_requires_contradiction"] is True
    assert config["lineage_admission_version"] == "v2"


def test_discovery_request_preserves_request_id_and_formal_default() -> None:
    from tests.test_clean_architecture_discovery import FakeGateway, FakeUnitOfWork
    from app.contexts.discovery import RunDiscoveryCommand, run_position_discovery

    gateway = FakeGateway()
    run_position_discovery(
        RunDiscoveryCommand(),
        Actor(actor_id="admin-1", role="admin"),
        FakeUnitOfWork(),
        gateway,
    )

    assert gateway.payload.request_id == ""
    assert gateway.payload.algorithm == "emerge_v3_2"


def test_discovery_orm_projection_round_trip_is_unchanged() -> None:
    class CapturingSession:
        def add(self, row: object) -> None:
            self.row = row

    projection = ClusterProjection(
        cluster_id="cluster-1",
        discovery_run_id="run-1",
        cluster_name="RAG 岗位簇",
        algorithm_version="discovery-v1",
        sample_count=2,
        core_skills=(freeze({"raw_skill": "RAG"}),),
        representative_titles=("RAG 工程师",),
        representative_jd_ids=("jd-1",),
        stability_score=0.8,
        growth_score=0.7,
        distance_from_existing_positions=0.6,
        discovery_assessment=freeze({"germination_score": 0.75}),
        generated_definition=freeze({"position_name": "RAG 工程师"}),
        time_window_start=date(2026, 7, 1),
    )
    session = CapturingSession()
    repository = SqlAlchemyDiscoveryRepository(session)  # type: ignore[arg-type]
    repository.add_cluster(projection)

    restored = repository._to_projection(session.row)  # type: ignore[arg-type]
    assert restored == projection


def test_emerging_cluster_orm_mapper_restores_typed_domain_facts() -> None:
    class Row:
        id = "cluster-1"
        cluster_name = "RAG 岗位簇"
        core_skills = [{"raw_skill": "RAG"}]
        representative_jd_ids = ["jd-1"]
        stability_score = 0.8
        discovery_run_id = "run-1"
        discovery_run_status = "succeeded"
        discovery_assessment = {
            "germination_score": 0.75,
            "qualified_as_emerging": True,
        }
        generated_definition = {"position_name": "RAG 工程师"}

    class Session:
        def get(self, model: object, identity: str) -> Row:
            return Row()

    record = SqlAlchemyEmergingPositionRepository(Session()).get_cluster("cluster-1")  # type: ignore[arg-type]
    assert record is not None
    assert record.core_skills == (freeze({"raw_skill": "RAG"}),)
    assert record.assessment == GerminationAssessment.from_values(
        Row.discovery_assessment, "run-1"
    )
    assert record.generated_definition == freeze({"position_name": "RAG 工程师"})


def test_discovery_api_json_contract_is_unchanged() -> None:
    projection = ClusterProjection(
        cluster_id="cluster-1",
        discovery_run_id="run-1",
        cluster_name="RAG 岗位簇",
        algorithm_version="discovery-v1",
        sample_count=1,
        core_skills=(freeze({"raw_skill": "RAG"}),),
        representative_titles=("RAG 工程师",),
        representative_jd_ids=("jd-1",),
        stability_score=0.8,
        growth_score=0.7,
        distance_from_existing_positions=0.6,
    )
    payload = cluster_data(projection)
    assert payload["cluster_id"] == "cluster-1"
    assert payload["algorithm"] == "discovery-v1"
    assert payload["core_skills"] == [{"raw_skill": "RAG"}]
    assert payload["representative_jd_ids"] == ["jd-1"]
    assert payload["discovery_run_id"] == "run-1"
    assert payload["status"] == "active"


def test_ports_only_define_protocols_and_legacy_modules_only_reexport() -> None:
    legacy_paths = (APP / "ports" / "discovery.py", APP / "ports" / "emerging_position.py")
    for path in legacy_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assert not any(isinstance(node, (ast.ClassDef, ast.FunctionDef)) for node in tree.body)

    discovery_port = ast.parse(
        (APP / "contexts" / "discovery" / "ports.py").read_text(encoding="utf-8")
    )
    defined = {
        node.name for node in discovery_port.body if isinstance(node, ast.ClassDef)
    }
    assert defined == {"DiscoveryGateway", "DiscoveryRepository", "DiscoveryUnitOfWork"}


def test_context_domains_are_framework_and_transport_independent() -> None:
    forbidden = (
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "httpx",
        "requests",
        "app.core.config",
        "app.infrastructure",
        "app.models",
    )
    violations: dict[str, list[str]] = {}
    for context in ("discovery", "emerging_positions"):
        path = APP / "contexts" / context / "domain.py"
        bad = [module for module in _imports(path) if module.startswith(forbidden)]
        if bad:
            violations[str(path.relative_to(APP))] = bad
    assert violations == {}


def test_context_outsiders_do_not_import_internal_type_modules() -> None:
    internal_prefixes = tuple(
        f"app.contexts.{context}.{module}"
        for context in ("discovery", "emerging_positions")
        for module in ("application_types", "contracts", "domain", "ports")
    )
    compatibility = {
        Path("ports/discovery.py"),
        Path("ports/emerging_position.py"),
    }
    violations: dict[str, list[str]] = {}
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP)
        if relative.parts[:1] == ("contexts",) or relative in compatibility:
            continue
        bad = [module for module in _imports(path) if module.startswith(internal_prefixes)]
        if bad:
            violations[str(relative)] = bad
    assert violations == {}


def test_new_and_legacy_type_imports_have_no_cycles() -> None:
    statement = "; ".join(
        (
            "import app.ports.discovery",
            "import app.application.discovery",
            "import app.contexts.discovery",
            "import app.ports.emerging_position",
            "import app.application.emerging_positions",
            "import app.contexts.emerging_positions",
            "import app.main",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", statement],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
