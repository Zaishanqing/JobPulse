from typing import Self

from app.application.contracts import PublishGraphCommand
from app.application.use_cases import PublishGraphVersionUseCase
from app.domain.publishing import PublishGateFacts
from app.domain.versioning import (
    ExistingGraphVersion,
    GraphVersionDependencies,
    PublishVersionFacts,
)


class ConcurrentPublishRepository:
    def __init__(self) -> None:
        self.find_calls = 0

    def load_publish_facts(self, run_id: int) -> PublishVersionFacts:
        return PublishVersionFacts(
            run_id=run_id,
            position_id="POS_BACKEND",
            base_version_id=1,
            current_version_id=1,
            previous_version_id=1,
            previous_version_number=1,
            existing=None,
            used_numbers=frozenset({2}),
            used_names=frozenset({"v2"}),
            snapshot={},
            algorithm_version="test",
            dependencies=GraphVersionDependencies(
                (), "catalog-v1", "mapping-v1", "normalizer-v1", "config-v1", {}
            ),
            gate=PublishGateFacts(
                build_status="approved",
                valid_sample_count=1,
                minimum_valid_samples=1,
                position_active=True,
                supports=(),
                relations=(),
                review_tasks=(),
                unresolved_count=0,
                non_exact_evidence_count=0,
                requirement_aggregate_count=0,
                task_aggregate_count=0,
            ),
        )

    def find_by_build_run(self, run_id: int) -> ExistingGraphVersion:
        self.find_calls += 1
        return ExistingGraphVersion(version_id=22, version_number=2)


class PublishUnitOfWork:
    def __init__(self, repository: ConcurrentPublishRepository) -> None:
        self.graph_versions = repository
        self.committed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def test_publish_returns_concurrent_version_seen_after_initial_fact_read() -> None:
    repository = ConcurrentPublishRepository()
    unit_of_work = PublishUnitOfWork(repository)
    use_case = PublishGraphVersionUseCase(lambda: unit_of_work)

    result = use_case.execute(
        PublishGraphCommand(
            run_id=7,
            actor_id=1,
            trace_id="trace-concurrent-publish",
            reason=None,
            version_name=None,
            version_number=None,
            release_notes=None,
        )
    )

    assert result.version_id == 22
    assert result.version_number == 2
    assert repository.find_calls == 1
    assert unit_of_work.committed is True
