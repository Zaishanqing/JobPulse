from types import SimpleNamespace

from app.contexts.matching_learning._applications.matching import (
    _ACTIVE_RANKINGS,
    _ACTIVE_RANKINGS_LOCK,
    ManageMatching,
)
from app.contexts.matching_learning._ports.matching import (
    MatchingPositionCandidate,
    MatchingServiceReferenceRecord,
)
from app.domain.accounts import AccountActor


class _MatchingRepository:
    def __init__(self, references=()):
        self.references = list(references)

    def list_service_references(self, *_args, **_kwargs):
        return self.references


class _UnitOfWork:
    def __init__(self, references=()):
        self.matching = _MatchingRepository(references)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _Resumes:
    def get(self, resume_id):
        return SimpleNamespace(
            resume_id=resume_id,
            owner_id="user-1",
            validated_cv_snapshot_id="snapshot-1",
        )


class _Contracts:
    def cv_profile(self, _resume_id):
        return {
            "source_version": "cv-v1",
            "normalization": {
                "skills": [{"skill_id": "python"}, {"skill_id": "fastapi"}]
            },
            "capabilities": {"profiles": [{"skill_id": "python"}]},
        }

    @staticmethod
    def _position_profile(position_id):
        required = {
            "position-python": [{"skill_id": "python"}],
            "position-java": [{"skill_id": "java"}],
        }[position_id]
        return {
            "source_version": f"{position_id}-v1",
            "graph_version": "graph-v1",
            "required_skills": required,
            "preferred_skills": [],
            "requirement_graph": {
                "graph_version": "standard-position-specialty-routes.v2"
            },
        }

    def position_profile(self, _position_id):
        raise AssertionError("ranking must reuse the batch position-profile read")

    def position_profiles_batch(self, position_ids):
        return {
            position_id: self._position_profile(position_id)
            for position_id in position_ids
        }


class _Catalog:
    items = [
        MatchingPositionCandidate(
            "position-java", "Java 工程师", "工程", "published", "active",
            "JAVA", "position-taxonomy.v3.0.0",
        ),
        MatchingPositionCandidate(
            "position-python", "Python 工程师", "工程", "published", "active",
            "PYTHON", "position-taxonomy.v3.0.0",
        ),
    ]

    def list(self):
        return self.items

    def get(self, position_id):
        return next(item for item in self.items if item.position_id == position_id)


def test_ranking_uses_skill_coverage_for_initial_priority():
    manager = ManageMatching(
        lambda: _UnitOfWork(),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )

    result = manager.ranking(AccountActor("user-1", "personal_user"), resume_id="resume-1")

    assert result["status"] == "ready"
    assert result["total"] == 2
    assert [item["position_id"] for item in result["items"]] == [
        "position-python",
        "position-java",
    ]
    assert [item["score"] for item in result["items"]] == [100.0, 0.0]
    assert all(item["calculation_status"] == "preliminary" for item in result["items"])


def test_ranking_refreshes_from_latest_current_manual_evaluation():
    reference = MatchingServiceReferenceRecord(
        task_id="task-current-python",
        evaluation_id="evaluation-current-python",
        user_id="user-1",
        tenant_id="",
        resume_id="resume-1",
        position_id="position-python",
        provider="matching-service",
        status="current",
        idempotency_key="manual-run-python",
        cv_profile_version="cv-v1",
        position_profile_version="position-python-v1",
        graph_version="graph-v1",
        overall_score=42.0,
    )
    manager = ManageMatching(
        lambda: _UnitOfWork((reference,)),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )

    result = manager.ranking(AccountActor("user-1", "personal_user"), resume_id="resume-1")

    python_item = next(
        item for item in result["items"] if item["position_id"] == "position-python"
    )
    assert python_item["score"] == 42.0
    assert python_item["calculation_status"] == "completed"
    assert python_item["evaluation_id"] == "evaluation-current-python"


def test_ranking_marks_succeeded_references_without_scores_as_failed():
    seed = ManageMatching(
        lambda: _UnitOfWork(),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )
    resume = _Resumes().get("resume-1")
    candidates = seed._ranking_candidates(resume, _Contracts().cv_profile("resume-1"))
    references = tuple(
        MatchingServiceReferenceRecord(
            task_id=f"task-{candidate.position_id}",
            evaluation_id=f"evaluation-{candidate.position_id}",
            user_id="user-1",
            tenant_id="",
            resume_id="resume-1",
            position_id=candidate.position_id,
            provider="matching-service",
            status="succeeded",
            idempotency_key=candidate.idempotency_key,
            cv_profile_version=candidate.cv_profile_version,
            position_profile_version=candidate.position_profile_version,
            graph_version=candidate.graph_version,
            overall_score=None,
        )
        for candidate in candidates
    )
    manager = ManageMatching(
        lambda: _UnitOfWork(references),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )

    result = manager.ranking(
        AccountActor("user-1", "personal_user"), resume_id="resume-1"
    )

    assert result["status"] == "completed"
    assert result["completed"] == 0
    assert all(item["calculation_status"] == "failed" for item in result["items"])
    assert all(
        item["error_code"] == "MATCHING_RESULT_SCORE_MISSING"
        for item in result["items"]
    )


def test_ranking_keeps_succeeded_references_without_scores_running_while_active():
    seed = ManageMatching(
        lambda: _UnitOfWork(),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )
    resume = _Resumes().get("resume-1")
    candidates = seed._ranking_candidates(resume, _Contracts().cv_profile("resume-1"))
    references = tuple(
        MatchingServiceReferenceRecord(
            task_id=f"task-{candidate.position_id}",
            evaluation_id=f"evaluation-{candidate.position_id}",
            user_id="user-1",
            tenant_id="",
            resume_id="resume-1",
            position_id=candidate.position_id,
            provider="matching-service",
            status="succeeded",
            idempotency_key=candidate.idempotency_key,
            cv_profile_version=candidate.cv_profile_version,
            position_profile_version=candidate.position_profile_version,
            graph_version=candidate.graph_version,
            overall_score=None,
        )
        for candidate in candidates
    )
    manager = ManageMatching(
        lambda: _UnitOfWork(references),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )
    ranking_key = manager._ranking_key("user-1", "snapshot-1")

    with _ACTIVE_RANKINGS_LOCK:
        _ACTIVE_RANKINGS.add(ranking_key)
    try:
        result = manager.ranking(
            AccountActor("user-1", "personal_user"), resume_id="resume-1"
        )
    finally:
        with _ACTIVE_RANKINGS_LOCK:
            _ACTIVE_RANKINGS.discard(ranking_key)

    assert result["status"] == "running"
    assert result["completed"] == 0
    assert all(item["calculation_status"] == "running" for item in result["items"])
    assert all("error_code" not in item for item in result["items"])


def test_ranking_idempotency_changes_when_resume_is_restored_from_same_snapshot():
    manager = ManageMatching(
        lambda: _UnitOfWork(),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )
    profile = _Contracts().cv_profile("resume-1")

    first = manager._ranking_candidates(_Resumes().get("resume-1"), profile)
    restored = manager._ranking_candidates(_Resumes().get("resume-restored"), profile)

    assert first[0].idempotency_key.startswith("ranking-v3:")
    assert first[0].idempotency_key != restored[0].idempotency_key


def test_ranking_cancellation_is_kept_until_an_explicit_restart():
    manager = ManageMatching(
        lambda: _UnitOfWork(),
        _Resumes(),
        None,
        None,
        None,
        None,
        _Contracts(),
        _Catalog(),
    )
    actor = AccountActor("user-1", "personal_user")

    manager.cancel_ranking(actor, resume_id="resume-1")
    assert manager.ranking(actor, resume_id="resume-1")["status"] == "cancelled"

    manager.prepare_ranking(actor, resume_id="resume-1")
    assert manager.ranking(actor, resume_id="resume-1")["status"] == "ready"
