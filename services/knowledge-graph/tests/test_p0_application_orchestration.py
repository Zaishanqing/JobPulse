from sqlalchemy import select

from app.application.errors import DuplicateFactVersion
from app.application.use_cases import (
    CompleteReviewTaskUseCase,
    ImportPublishedJDFactUseCase,
)
from app.domain.published_facts import (
    PublishedFactIdentity,
    PublishedFactRecord,
    PublishedFactValidationFacts,
)
from app.domain.review_tasks import (
    ReviewTaskCommand,
    ReviewTaskFacts,
    ReviewTaskResult,
    decide_review_task_transition,
)
from app.domain.write_models import ReviewCompletion
from app.infrastructure.sqlalchemy.repository_adapters import (
    SqlAlchemyReviewTaskRepository,
)
from app.models import AuditLog, ReviewTask, ReviewTaskEvent
from tests.test_published_fact_ingestion import published_command


def test_published_fact_concurrency_reloads_facts_and_redecides():
    command = published_command()
    calls: list[str] = []

    class PublishedFacts:
        load_count = 0

        def load_validation_facts(self, incoming, lineage):
            self.load_count += 1
            calls.append("load")
            existing = None
            if self.load_count == 2:
                existing = PublishedFactRecord(
                    incoming.source_jd_id,
                    PublishedFactIdentity(
                        incoming.source_system, incoming.source_fact_id
                    ),
                    incoming.source_fact_version,
                    incoming.source_version,
                )
            return PublishedFactValidationFacts(
                incoming, existing, existing, lineage
            )

        def save_import_plan(self, _plan):
            calls.append("save")
            raise DuplicateFactVersion("concurrent import")

    class Uow:
        published_facts = PublishedFacts()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, _exc, _traceback):
            calls.append("rollback" if exc_type else "exit")

        def commit(self):
            calls.append("commit")

    result = ImportPublishedJDFactUseCase(Uow).execute(command)

    # A stable technical conflict causes a fresh fact load and domain decision;
    # the complete persistence workflow is not blindly invoked a second time.
    assert calls == ["load", "save", "rollback", "load", "commit", "exit"]
    assert result.idempotent is True


def test_review_application_orders_cas_effect_event_audit_and_commit():
    calls: list[str] = []

    class Reviews:
        def load_review_task_facts(self, _task_id):
            calls.append("load")
            return ReviewTaskFacts(2, "evidence", "7", None, "claimed", 5)

        def apply_review_task_plan(self, plan):
            calls.append("apply")
            return ReviewTaskResult(plan.task_id, plan.transition.target_status)

        def append_review_event(self, _plan):
            calls.append("event")

    class Effects:
        def apply(self, _effect):
            calls.append("effect")

    class Audits:
        def record(self, _record):
            calls.append("audit")

    class Uow:
        review_tasks = Reviews()
        review_effects = Effects()
        audits = Audits()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def commit(self):
            calls.append("commit")

    result = CompleteReviewTaskUseCase(Uow).execute(
        2, ReviewCompletion("approve", "checked"), 5, "trace"
    )

    assert result.status == "approved"
    assert calls == ["load", "apply", "effect", "event", "audit", "commit"]


def test_review_repository_adapter_applies_only_the_domain_cas_plan(db, users):
    task = ReviewTask(object_type="evidence", object_id="7")
    db.add(task)
    db.commit()
    reviewer_id = users["reviewer"].id
    repository = SqlAlchemyReviewTaskRepository(db)
    decision = decide_review_task_transition(
        repository.load_review_task_facts(task.id),
        ReviewTaskCommand("claim", reviewer_id, "trace"),
    )
    assert decision.accepted

    result = repository.apply_review_task_plan(decision.plan)
    db.flush()

    assert result.status == "claimed"
    assert db.scalar(
        select(ReviewTaskEvent).where(ReviewTaskEvent.task_id == task.id)
    ) is None
    assert db.scalar(
        select(AuditLog).where(
            AuditLog.object_type == "review_task",
            AuditLog.object_id == str(task.id),
        )
    ) is None
