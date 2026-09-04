from datetime import date, datetime, timezone

from app.application.discovery import RunDiscovery
from app.application.contracts import (
    DiscoveryAssessmentResult as DiscoveryAssessment,
    DiscoveryClusterResult,
    DiscoveryTimeWindow,
    DiscoveryResult,
    HistoricalTimeWindow,
    RunDiscoveryCommand,
)
from app.domain.discovery import (
    AlgorithmOutput,
    AlgorithmCluster,
    AlgorithmMetadata,
    ClusterFeatureSummary,
    EmbeddingVector,
    GeneratedDefinition,
    JDStructuredData,
    JDSnapshot,
    PositionReference,
    SkillReference,
)
from app.domain.germination import GerminationAssessmentResult, GerminationDimensions


class FakeReferences:
    def __init__(self):
        self.called = False

    def resolve(self, references):
        self.called = True
        return tuple(
            PositionReference(
                reference.position_id,
                (SkillReference(raw_skill="formal"),),
                reference.graph_version_id,
            )
            for reference in references
        )


class FakeLineage:
    def match(self, previous, current):
        return []


class FakeAlgorithm:
    def __init__(self):
        self.requested = None

    def execute(self, *, algorithm, snapshots, reference_skill_sets, config, time_window_ids=None):
        self.requested = algorithm.canonical_name
        assessment = GerminationAssessmentResult(
            0.8,
            GerminationDimensions(0.7, 0.8, 0.0, 0.0, 0.8, 0.0, 0.0, 0.0),
            "emerging",
            True,
            "fake evidence",
            {},
            {},
            {},
            "fake-formula-v1",
        )
        return AlgorithmOutput(
            algorithm_version=f"fake:{algorithm.canonical_name}",
            formula_version="fake-formula-v1",
            metadata=AlgorithmMetadata("Fake", algorithm.requested_name, "fake", "fake", 0.8, 1),
            embeddings=tuple(EmbeddingVector(item.jd_id, (1.0,)) for item in snapshots),
            clusters=(
                AlgorithmCluster(
                    key="fake",
                    cluster_name="Fake",
                    members=snapshots,
                    core_skills=("x",),
                    stability_score=1.0,
                    centroid=(1.0,),
                    algorithm_version="fake",
                    similarity_threshold=0.8,
                    random_seed=1,
                    assessment=assessment,
                    generated_definition=GeneratedDefinition("Fake", (), (), (), (), "fake"),
                ),
            ),
        )


class FakeRuns:
    def __init__(self):
        self.values = {}
        self.fingerprints = {}

    def by_request_id(self, request_id):
        return next((item for item in self.values.values() if item.request_id == request_id), None)

    def by_id(self, run_id):
        return self.values.get(run_id)

    def fingerprint_by_run_id(self, run_id):
        return self.fingerprints.get(run_id)

    def latest_succeeded(self):
        return next(reversed(self.values.values()), None) if self.values else None

    def add(self, run, algorithm_config):
        self.values[run.id] = run
        config = getattr(algorithm_config, "config", {})
        fingerprint = (config or {}).get("payload_fingerprint", {})
        self.fingerprints[run.id] = (fingerprint or {}).get("hash")


class FakeSnapshots:
    def __init__(self):
        self.values = []

    def add_many(self, snapshots):
        self.values.extend(snapshots)


class FakeClusters:
    def __init__(self, runs):
        self.runs = runs
        self.values = {}

    def latest_specs_before(self, window_start, window_end, compatibility):
        return []

    def add_many(self, clusters, lineages):
        for cluster in clusters:
            self.values[cluster.run_id] = cluster

    def result(self, run_id, contract_version):
        run = self.runs.by_id(run_id)
        return DiscoveryResult(
            contract_version=contract_version,
            run_id=run.id,
            request_id=run.request_id,
            status=run.status,
            algorithm_version=run.algorithm_version,
            formula_version=run.formula_version,
            created_at=datetime.now(timezone.utc),
            completed_at=run.completed_at,
            clusters=(
                DiscoveryClusterResult(
                    cluster_id=self.values[run_id].id,
                    cluster_name="Fake",
                    sample_count=2,
                    core_skills=(),
                    representative_titles=(),
                    representative_jd_ids=(),
                    representative_members=(),
                    core_responsibilities=(),
                    semantic_centroid=(),
                    algorithm_sources=("fake",),
                    merge_basis={},
                    stability_score=1.0,
                    growth_score=0.7,
                    distance_from_existing_positions=0.8,
                    feature_summary=ClusterFeatureSummary(
                        AlgorithmMetadata("Fake", "strict", "fake", "fake", 0.8, 1),
                        (1.0,),
                    ),
                    germination_assessment=DiscoveryAssessment(
                        0.8, {}, "emerging", True, "fake evidence", {}
                    ),
                    generated_definition=GeneratedDefinition("Fake", (), (), (), (), "fake"),
                ),
            ),
            lineages=(),
        )


class FakeUoW:
    def __init__(self):
        self.runs = FakeRuns()
        self.snapshots = FakeSnapshots()
        self.clusters = FakeClusters(self.runs)
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.rollback()

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


def test_application_uses_pure_fake_ports_without_fastapi_or_database():
    command = RunDiscoveryCommand(
        contract_version="discovery.v2",
        request_id="fake-request",
        algorithm="emerge_v3_2",
        snapshots=[
            JDSnapshot(
                jd_id=str(index),
                schema_version="v2",
                review_status="published",
                title="X",
                source_name="source",
                publish_date=date(2026, index, 1),
                structured_data=JDStructuredData(
                    responsibilities=(),
                    required_skills=(SkillReference(raw_skill="X"),),
                    bonus_skills=(),
                    business_scenarios=(),
                ),
                source_fact_id=f"fact-{index}",
                source_fact_version="1",
                window_id=f"w{index}",
            )
            for index in (1, 2)
        ],
        position_references=[PositionReference("formal", graph_version_id="graph-v1")],
        time_window=DiscoveryTimeWindow(
            date(2026, 1, 1),
            date(2026, 3, 31),
            (
                HistoricalTimeWindow("w1", date(2026, 1, 1), date(2026, 1, 31)),
                HistoricalTimeWindow("w2", date(2026, 2, 1), date(2026, 2, 28)),
                HistoricalTimeWindow("w3", date(2026, 3, 1), date(2026, 3, 31)),
            ),
        ),
    )
    references = FakeReferences()
    algorithm = FakeAlgorithm()
    uow = FakeUoW()

    result = RunDiscovery(references, algorithm, uow, FakeLineage()).execute(command)

    assert result.contract_version == "discovery.v2"
    assert algorithm.requested == "emerge_v3_2"
    assert references.called is True
    assert uow.committed is True
    assert uow.rolled_back is False
    assert len(uow.snapshots.values) == 2
