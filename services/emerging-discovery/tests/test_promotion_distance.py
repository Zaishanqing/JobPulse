from dataclasses import replace
from datetime import datetime, timezone

import pytest

from app.api.mapping import promotion_distance_data
from app.application.handlers import QueryDiscovery
from app.application.promotion_distance import build_promotion_distance_certificate
from app.domain.candidate_lifecycle import assess_stable_gate, transition_candidate
from app.domain.values import FrozenDict
from app.ports.records import (
    CandidatePromotionContextRecord,
    CandidateRecord,
    LifecycleWindowRecord,
)


CONFIG = FrozenDict(
    {
        "candidate_lifecycle_version": "certificate-test-v2",
        "emerging_to_stable_min_windows": 4,
        "emerging_to_stable_min_support": 5,
        "emerging_to_stable_min_companies": 3,
        "emerging_to_stable_min_emergence": 0.7,
        "emerging_to_stable_min_identity_stability": 3,
    }
)


def _candidate(**changes) -> CandidateRecord:
    now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    base = CandidateRecord(
        id="candidate-1",
        status="emerging_candidate",
        first_seen_window_id="w1",
        last_seen_window_id="w3",
        age=3,
        current_cluster_id="cluster-3",
        previous_cluster_ids=(),
        canonical_title="Agent Engineer",
        display_title="Agent Engineer",
        definition=FrozenDict(),
        support_count=5,
        company_coverage=3,
        skill_similarity=0.9,
        responsibility_similarity=0.9,
        title_similarity=0.9,
        membership_overlap=0.9,
        identity_similarity=0.9,
        novelty_score=0.8,
        emergence_score=0.7,
        evidence=FrozenDict(),
        identity_stability=3,
        titles=("Agent Engineer",),
        skills=("agent",),
        responsibilities=("build agents",),
        member_jd_ids=("jd-1",),
        observed_window_ids=("w1", "w2", "w3", "w4"),
        semantic_centroid=(),
        created_at=now,
        updated_at=now,
    )
    return replace(base, **changes)


def _context(candidate: CandidateRecord) -> CandidatePromotionContextRecord:
    return CandidatePromotionContextRecord(
        candidate=candidate,
        latest_observation=None,
        window=LifecycleWindowRecord(
            "w4",
            "run-4",
            "request-4",
            "algorithm-v4",
            "formula-v4",
            datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
        config_snapshot_id="config-4",
        lifecycle_config=CONFIG,
    )


@pytest.mark.parametrize(
    ("changes", "condition", "missing"),
    [
        ({"observed_window_ids": ("w1", "w2")}, "windows", 2),
        ({"support_count": 2}, "support", 3),
        ({"company_coverage": 1}, "companies", 2),
        ({"identity_stability": 1}, "identity_stability", 2),
    ],
)
def test_certificate_reports_each_production_stable_gate_shortfall(
    changes, condition, missing
):
    certificate = build_promotion_distance_certificate(_context(_candidate(**changes)))
    by_name = {item.name: item for item in certificate.conditions}

    assert by_name[condition].missing == missing
    assert by_name[condition].satisfied is False
    assert condition in certificate.missing_conditions
    assert "source_coverage" not in by_name


def test_multiple_missing_conditions_and_config_identity_are_traceable():
    certificate = build_promotion_distance_certificate(
        _context(
            _candidate(
                observed_window_ids=("w1",),
                support_count=1,
                company_coverage=1,
                emergence_score=0.2,
                identity_stability=0,
            )
        )
    )

    assert certificate.missing_conditions == (
        "windows",
        "support",
        "companies",
        "emergence",
        "identity_stability",
    )
    dto = promotion_distance_data(certificate)
    assert dto["gate_identity"] == {
        "lifecycle_version": "certificate-test-v2",
        "config_snapshot_id": "config-4",
        "run_id": "run-4",
        "request_id": "request-4",
        "algorithm_version": "algorithm-v4",
        "formula_version": "formula-v4",
    }


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate(),
        _candidate(support_count=4),
        _candidate(company_coverage=2, identity_stability=1),
    ],
)
def test_certificate_uses_exactly_the_same_threshold_result_as_production_gate(candidate):
    assessment = assess_stable_gate(
        candidate.status,
        supported_window_count=len(set(candidate.observed_window_ids)),
        support_count=candidate.support_count,
        company_count=candidate.company_coverage,
        emergence_score=candidate.emergence_score,
        identity_stability=candidate.identity_stability,
        config=CONFIG,
    )
    transition = transition_candidate(
        candidate.status,
        supported_window_count=len(set(candidate.observed_window_ids)),
        support_count=candidate.support_count,
        company_count=candidate.company_coverage,
        emergence_score=candidate.emergence_score,
        identity_similarity=candidate.identity_similarity,
        identity_stability=candidate.identity_stability,
        config=CONFIG,
    )

    assert assessment.gate_satisfied == (transition.to_status == "stable_emerging_role")
    assert assessment.missing_conditions == tuple(
        item.name for item in assessment.conditions if not item.satisfied
    )


def test_already_stable_candidate_has_no_missing_conditions():
    certificate = build_promotion_distance_certificate(
        _context(_candidate(status="stable_emerging_role", support_count=1))
    )

    assert certificate.outcome == "already_stable"
    assert certificate.gate_satisfied is True
    assert certificate.missing_conditions == ()


def test_candidate_meeting_the_gate_is_reported_ready_without_promotion():
    candidate = _candidate()
    certificate = build_promotion_distance_certificate(_context(candidate))

    assert certificate.outcome == "ready_for_stable"
    assert certificate.gate_satisfied is True
    assert certificate.missing_conditions == ()
    assert candidate.status == "emerging_candidate"


def test_predecessor_state_explains_that_prior_promotions_are_required():
    certificate = build_promotion_distance_certificate(
        _context(_candidate(status="incubating"))
    )

    assert certificate.outcome == "requires_prior_promotions"
    assert certificate.eligible_state is False
    assert certificate.missing_conditions == ("current_state",)


def test_terminal_candidate_is_explicitly_blocked():
    certificate = build_promotion_distance_certificate(
        _context(_candidate(status="noise"))
    )

    assert certificate.outcome == "terminal_state"
    assert certificate.gate_satisfied is False
    assert "current_state" in certificate.missing_conditions


class _Candidates:
    def __init__(self, context):
        self.context = context
        self.calls = 0

    def promotion_contexts(self, candidate_id=None):
        self.calls += 1
        assert candidate_id == "candidate-1"
        return (self.context,)


class _Uow:
    def __init__(self, candidates):
        self.candidates = candidates

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_query_is_read_only_and_returns_same_certificate_without_mutating_candidate():
    candidate = _candidate(support_count=2)
    before = candidate
    candidates = _Candidates(_context(candidate))

    certificates = QueryDiscovery(_Uow(candidates)).promotion_distance(
        candidate_id="candidate-1"
    )

    assert certificates == (build_promotion_distance_certificate(_context(candidate)),)
    assert candidate == before
    assert candidates.calls == 1
