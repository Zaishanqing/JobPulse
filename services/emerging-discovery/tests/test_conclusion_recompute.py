from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.application.contracts import (
    DiscoveryTimeWindow,
    HistoricalTimeWindow,
    RunDiscoveryCommand,
)
from app.application.discovery_identity import discovery_identity
from app.application.recompute import (
    EmergingRecomputeRequest,
    RecomputeEmergingConclusion,
    recompute_config_hash,
)
from app.domain.discovery import JDStructuredData, JDSnapshot, PositionReference, SkillReference
from tests.test_ports_with_fakes import FakeAlgorithm, FakeReferences


def _command() -> RunDiscoveryCommand:
    windows = (
        HistoricalTimeWindow("w1", date(2026, 1, 1), date(2026, 1, 31)),
        HistoricalTimeWindow("w2", date(2026, 2, 1), date(2026, 2, 28)),
        HistoricalTimeWindow("w3", date(2026, 3, 1), date(2026, 3, 31)),
    )
    return RunDiscoveryCommand(
        contract_version="discovery.v2",
        request_id="recompute-1",
        algorithm="emerge_v3_2",
        snapshots=tuple(
            JDSnapshot(
                jd_id=f"jd-{index}",
                schema_version="v2",
                review_status="published",
                title="LLM Engineer",
                source_name=f"source-{index}",
                publish_date=date(2026, index, 1),
                structured_data=JDStructuredData(
                    responsibilities=("Build LLM systems",),
                    required_skills=(SkillReference(raw_skill="LLM"),),
                    bonus_skills=(),
                    business_scenarios=(),
                    extensions={"evidence_ids": (f"ev-{index}",)},
                ),
                source_fact_id=f"fact-{index}",
                source_fact_version=f"2026-0{index}-01T00:00:00+00:00",
                window_id=f"w{index}",
            )
            for index in (1, 2, 3)
        ),
        position_references=(
            PositionReference("formal", graph_version_id="graph-v1"),
        ),
        time_window=DiscoveryTimeWindow(
            windows[0].start,
            windows[-1].end,
            windows,
            "w3",
        ),
    )


def _request(command: RunDiscoveryCommand, references: FakeReferences, **changes):
    resolved = references.resolve(command.position_references)
    identity = discovery_identity(command, resolved)
    request = EmergingRecomputeRequest(
        dataset_id="dataset-1",
        release_id="release-1",
        subject_ref="LLM_ALGORITHM_ENGINEER",
        algorithm_version="fake:emerge_v3_2",
        config_hash=recompute_config_hash(
            identity.algorithm.canonical_name, identity.config.values
        ),
        command=command,
    )
    return replace(request, **changes)


def test_recompute_is_isolated_and_deterministic() -> None:
    references = FakeReferences()
    use_case = RecomputeEmergingConclusion(references, FakeAlgorithm())
    request = _request(_command(), references)
    first = use_case.execute(request)
    repeated = use_case.execute(request)
    assert first.target_found is True
    assert first.threshold_passed is True
    assert first.business_state == "emerging"
    assert first.target_anchor is not None
    assert repeated.request_fingerprint == first.request_fingerprint
    assert repeated.target_anchor == first.target_anchor


def test_recompute_matches_anchor_when_cluster_id_changes() -> None:
    references = FakeReferences()
    use_case = RecomputeEmergingConclusion(references, FakeAlgorithm())
    baseline_request = _request(_command(), references)
    baseline = use_case.execute(baseline_request)
    assert baseline.target_anchor is not None
    after = use_case.execute(
        replace(baseline_request, target_anchor=baseline.target_anchor)
    )
    assert after.target_found is True
    assert after.continuity_certificate is not None
    assert after.continuity_certificate["decision"] == "same"


def test_recompute_rejects_config_identity_drift() -> None:
    references = FakeReferences()
    use_case = RecomputeEmergingConclusion(references, FakeAlgorithm())
    request = _request(_command(), references)
    try:
        use_case.execute(replace(request, config_hash="sha256:" + "0" * 64))
    except ValueError as exc:
        assert str(exc) == "emerging recompute config_hash mismatch"
    else:
        raise AssertionError("config identity drift must fail closed")
