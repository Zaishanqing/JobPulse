from __future__ import annotations

import hashlib
import pytest
from fastapi.testclient import TestClient

from app.application.candidate_lifecycle import (
    SyncCandidateLifecycle,
    _Bundle,
    _enforce_unique_window_candidate_claims,
)
from app.domain.values import FrozenDict
from app.domain.candidate_identity import (
    LEGACY_DECISION_VERSION,
    SELECTED_CONFIG_VERSION,
    SELECTED_DECISION_VERSION,
    CandidateIdentitySpec,
    identity_decision,
    match_candidate_identity,
    select_candidate_identity,
)
from app.main import app


client = TestClient(app)
HEADERS = {"Authorization": "Bearer development-emerging-discovery-token-change-me"}

LOW_THRESHOLDS = {
    "weak_to_incubating_min_windows": 2,
    "weak_to_incubating_min_support": 1,
    "weak_to_incubating_min_companies": 1,
    "weak_to_incubating_min_emergence": 0.0,
    "incubating_to_emerging_min_windows": 3,
    "incubating_to_emerging_min_support": 1,
    "incubating_to_emerging_min_companies": 1,
    "incubating_to_emerging_min_emergence": 0.0,
    "incubating_to_emerging_min_identity_stability": 1,
    "emerging_to_stable_min_windows": 4,
    "emerging_to_stable_min_support": 1,
    "emerging_to_stable_min_companies": 1,
    "emerging_to_stable_min_emergence": 0.0,
    "emerging_to_stable_min_identity_stability": 1,
}


def test_candidate_id_preserves_distinct_non_ascii_occupation_keys():
    first = SyncCandidateLifecycle._candidate_id("D5-SW-03", "大模型应用")
    second = SyncCandidateLifecycle._candidate_id("D5-SW-03", "智能驾驶")

    assert first != second
    assert first == SyncCandidateLifecycle._candidate_id("D5-SW-03", "大模型应用")
    assert first.startswith("cand-D5-SW-03-occupation-")


def test_final_window_assignment_rehomes_duplicate_candidate_claim():
    shared = "cand-D5-SW-03-existing"
    first = _Bundle(
        candidate_id=shared,
        primary_cluster_id="cluster-a",
        primary_cluster_key="大模型应用",
        contributing_cluster_ids=("cluster-a",),
        decision="same",
        support_count=1,
        company_count=1,
        inputs=None,  # type: ignore[arg-type]
        bundle_compatibility=FrozenDict(),
    )
    second = _Bundle(
        candidate_id=shared,
        primary_cluster_id="cluster-b",
        primary_cluster_key="智能驾驶",
        contributing_cluster_ids=("cluster-b",),
        decision="same",
        support_count=1,
        company_count=1,
        inputs=None,  # type: ignore[arg-type]
        bundle_compatibility=FrozenDict(),
    )

    resolved = _enforce_unique_window_candidate_claims(
        (first, second),
        "D5-SW-04",
    )

    assert len({item.candidate_id for item in resolved}) == 2
    assert sum(item.candidate_id == shared for item in resolved) == 1
    loser = next(item for item in resolved if item.candidate_id != shared)
    assert loser.decision == "review_required"
    assert loser.assignment_reason == "final_window_candidate_claim_conflict"


def _windows(run_index: int) -> list[dict]:
    end_days = {
        1: 31,
        2: 28,
        3: 31,
        4: 30,
        5: 31,
        6: 30,
        7: 31,
        8: 31,
        9: 30,
        10: 31,
        11: 30,
        12: 31,
    }
    windows = []
    for offset in range(3):
        month_index = 1 + run_index * 3 + offset
        year = 2026 + (month_index - 1) // 12
        month = ((month_index - 1) % 12) + 1
        windows.append(
            {
                "window_id": f"{year}-{month:02d}",
                "start": f"{year}-{month:02d}-01",
                "end": f"{year}-{month:02d}-{end_days[month]}",
            }
        )
    return windows


def _payload(
    run_index: int,
    request_id: str,
    title: str,
    skills: tuple[str, ...],
    responsibilities: tuple[str, ...],
    *,
    companies: tuple[str, ...] | None = None,
    config: dict | None = None,
    snapshot_count: int = 3,
) -> dict:
    companies = companies or tuple(f"company-{index}" for index in range(snapshot_count))
    windows = _windows(run_index)
    snapshots = []
    for index in range(snapshot_count):
        window = windows[index]
        snapshots.append(
            {
                "source_fact_id": f"{request_id}-fact-{index}",
                "source_fact_version": "1",
                "jd_id": f"{request_id}-jd-{index}",
                "schema_version": "v2",
                "review_status": "published",
                "consumption_path": "published",
                "title": title,
                "source_name": f"platform-{index % 2}",
                "publish_date": f"{window['window_id']}-10",
                "content_hash": "sha256:" + hashlib.sha256(
                    f"{request_id}:{index}".encode()
                ).hexdigest(),
                "structured_data": {
                    "responsibilities": list(responsibilities),
                    "required_skills": [{"raw_skill": skill} for skill in skills],
                    "bonus_skills": [],
                    "business_scenarios": ["智能客服"],
                    "company_name": companies[index % len(companies)],
                    "source_record_id": f"{request_id}-source-{index}",
                },
            }
        )
    return {
        "contract_version": "discovery.v2",
        "request_id": request_id,
        "algorithm": "emerge_v3_2",
        "time_windows": _windows(run_index),
        "snapshots": snapshots,
        "position_references": [
            {
                "position_id": "standard-backend",
                "graph_version_id": "graph-v1",
                "required_skills": [{"raw_skill": "Java"}],
            }
        ],
        "config": {
            "dataset_id": "emerging-discovery-full-temporal-v1",
            **(config or {}),
        },
    }


def _create(payload: dict) -> dict:
    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _candidates(**filters) -> list[dict]:
    response = client.get("/api/v1/candidates", params=filters, headers=HEADERS)
    assert response.status_code == 200
    return response.json()["data"]["candidates"]


def _role_payload(
    run_index: int,
    request_id: str,
    title: str,
    *,
    config: dict | None = None,
) -> dict:
    return _payload(
        run_index,
        request_id,
        title,
        ("Python", "RAG", "Agent"),
        ("构建智能体应用",),
        companies=("acme", "globex", "initech"),
        config=config,
    )


def test_first_cluster_creates_candidate():
    _create(_role_payload(0, "first-run", "AI Agent Developer"))
    items = _candidates()
    assert len(items) == 1
    candidate = items[0]
    assert candidate["status"] == "weak_signal"
    assert candidate["first_seen_window_id"] == "2026-03"
    assert candidate["last_seen_window_id"] == "2026-03"
    assert candidate["age"] == 1
    assert candidate["identity_similarity"] == 1.0
    assert candidate["definition"]
    assert candidate["evidence"]


def test_same_role_next_run_maps_to_same_candidate():
    first = _create(_role_payload(0, "same-role-1", "AI Agent Developer"))
    second = _create(_role_payload(1, "same-role-2", "AI Agent Developer"))
    items = _candidates()
    assert len(items) == 1
    candidate = items[0]
    assert candidate["candidate_id"]
    assert candidate["last_seen_window_id"] == "2026-06"
    assert candidate["age"] == 2
    assert candidate["identity_similarity"] > 0.55
    assert candidate["previous_cluster_ids"] == [first["clusters"][0]["cluster_id"]]
    assert second["clusters"][0]["cluster_id"] == candidate["current_cluster_id"]
    assert candidate["identity_profile"]["member_evidence_ids"]
    assert candidate["identity_profile"]["member_dedup_cluster_ids"] == []
    assert candidate["identity_profile"]["member_template_cluster_ids"]
    trajectory = client.get(
        f"/api/v1/candidates/{candidate['candidate_id']}/trajectory",
        headers=HEADERS,
    ).json()["data"]["trajectory"]
    decision = trajectory[-1]["match_evidence"]
    assert decision["decision_version"] == SELECTED_DECISION_VERSION
    assert decision["config_version"] == SELECTED_CONFIG_VERSION
    assert decision["semantic_status"] == "unavailable"
    assert decision["decision_basis"]
    assert decision["components"]["sample_overlap"] == 0.0
    assert decision["components"]["dedup_cluster_overlap"] is None
    assert decision["components"]["template_cluster_overlap"] == 1.0


def test_verified_content_hash_drives_persisted_dedup_membership():
    digest = "sha256:" + "a" * 64
    first = _role_payload(0, "hash-role-1", "AI Agent Developer")
    second = _role_payload(1, "hash-role-2", "AI Agent Developer")
    for payload in (first, second):
        for snapshot in payload["snapshots"]:
            snapshot["content_hash"] = digest
    _create(first)
    _create(second)
    candidate = _candidates()[0]
    assert candidate["identity_profile"]["member_dedup_cluster_ids"]
    trajectory = client.get(
        f"/api/v1/candidates/{candidate['candidate_id']}/trajectory",
        headers=HEADERS,
    ).json()["data"]["trajectory"]
    assert trajectory[-1]["match_evidence"]["components"][
        "dedup_cluster_overlap"
    ] == 1.0


def test_unverified_content_hash_is_rejected_instead_of_fabricating_dedup():
    payload = _role_payload(0, "bad-hash", "AI Agent Developer")
    payload["snapshots"][0]["content_hash"] = "version-1"
    response = client.post("/api/v1/discovery-runs", json=payload, headers=HEADERS)
    assert response.status_code == 422
    assert "content_hash must be a sha256: digest" in response.text


def test_title_rename_keeps_identity_when_skills_and_responsibilities_match():
    _create(_role_payload(0, "rename-1", "AI Agent Developer"))
    _create(_role_payload(1, "rename-2", "Agent Engineer"))
    items = _candidates()
    assert len(items) == 1
    candidate = items[0]
    assert candidate["canonical_title"] == "AI Agent Developer"
    assert candidate["display_title"] == "Agent Engineer"
    assert candidate["identity_similarity"] > 0.55


def test_completely_different_cluster_creates_new_candidate():
    _create(_role_payload(0, "different-1", "AI Agent Developer"))
    _create(
        _payload(
            1,
            "different-2",
            "Java 后端工程师",
            ("Java", "Spring"),
            ("后端服务开发",),
            companies=("acme", "globex", "initech"),
        )
    )
    items = _candidates()
    assert len(items) == 2
    assert len({item["candidate_id"] for item in items}) == 2
    newest = max(items, key=lambda item: item["last_seen_window_id"])
    assert newest["display_title"] == "Java 后端工程师"
    assert newest["status"] == "weak_signal"


def test_weak_signal_promotes_to_incubating():
    _create(_role_payload(0, "incubating-1", "AI Agent Developer", config=LOW_THRESHOLDS))
    _create(_role_payload(1, "incubating-2", "AI Agent Developer", config=LOW_THRESHOLDS))
    candidate = _candidates()[0]
    assert candidate["status"] == "incubating"
    assert candidate["identity_stability"] >= 2


def test_incubating_promotes_to_emerging_candidate():
    for index, request_id in enumerate(("emerging-1", "emerging-2", "emerging-3")):
        _create(_role_payload(index, request_id, "AI Agent Developer", config=LOW_THRESHOLDS))
    candidate = _candidates()[0]
    assert candidate["status"] == "emerging_candidate"
    assert candidate["age"] == 3


def test_emerging_candidate_promotes_to_stable_emerging_role():
    for index, request_id in enumerate(
        ("stable-1", "stable-2", "stable-3", "stable-4")
    ):
        _create(_role_payload(index, request_id, "AI Agent Developer", config=LOW_THRESHOLDS))
    candidate = _candidates()[0]
    assert candidate["status"] == "stable_emerging_role"
    assert candidate["age"] == 4


def test_stable_emerging_role_does_not_auto_promote_to_official_position():
    for index, request_id in enumerate(
        ("official-1", "official-2", "official-3", "official-4", "official-5")
    ):
        _create(_role_payload(index, request_id, "AI Agent Developer", config=LOW_THRESHOLDS))
    candidate = _candidates()[0]
    assert candidate["status"] == "stable_emerging_role"
    assert candidate["age"] == 5


def test_multi_window_without_support_marks_dead():
    _create(_role_payload(0, "dead-1", "AI Agent Developer"))
    _create(
        _payload(
            1,
            "dead-2",
            "Java 后端工程师",
            ("Java", "Spring"),
            ("后端服务开发",),
            companies=("acme", "globex", "initech"),
        )
    )
    items = _candidates()
    dead = next(item for item in items if item["display_title"] == "AI Agent Developer")
    assert dead["status"] == "dead"
    assert len(items) == 2
    response = client.get(
        f"/api/v1/candidates/{dead['candidate_id']}/trajectory",
        headers=HEADERS,
    )
    assert response.status_code == 200


def test_noise_candidate_is_not_promoted():
    noisy_config = {
        "noise_min_windows": 2,
        "noise_max_support": 2,
        "noise_max_companies": 1,
        "noise_max_emergence": 0.99,
    }
    low_quality = _payload(
        0,
        "noise-1",
        "模糊岗位",
        ("Python",),
        ("临时任务",),
        companies=("one-company",),
        config=noisy_config,
        snapshot_count=2,
    )
    _create(low_quality)
    second = _payload(
        1,
        "noise-2",
        "模糊岗位",
        ("Python",),
        ("临时任务",),
        companies=("one-company",),
        config=noisy_config,
        snapshot_count=2,
    )
    _create(second)
    candidate = _candidates()[0]
    assert candidate["status"] == "noise"
    assert candidate["display_title"] == "模糊岗位"


def test_trajectory_order_and_statuses():
    for index, request_id in enumerate(("traj-1", "traj-2", "traj-3")):
        _create(_role_payload(index, request_id, "AI Agent Developer", config=LOW_THRESHOLDS))
    candidate_id = _candidates()[0]["candidate_id"]
    response = client.get(
        f"/api/v1/candidates/{candidate_id}/trajectory",
        headers=HEADERS,
    )
    assert response.status_code == 200
    steps = response.json()["data"]["trajectory"]
    assert [step["window_id"] for step in steps] == ["2026-03", "2026-06", "2026-09"]
    assert [step["status"] for step in steps] == [
        "weak_signal",
        "incubating",
        "emerging_candidate",
    ]
    assert len({step["run_id"] for step in steps}) == 3
    assert all(step["cluster_id"] for step in steps)
    assert all(step["emergence_score"] is not None for step in steps)
    assert all(step["support_count"] >= 1 for step in steps)
    assert all(step["company_count"] >= 1 for step in steps)


def test_identity_matching_is_deterministic():
    current = CandidateIdentitySpec(
        titles=frozenset({"AI Agent Developer"}),
        skills=frozenset({"python", "rag"}),
        responsibilities=frozenset({"build agents"}),
        member_jd_ids=frozenset({"jd-1"}),
    )
    candidates = (
        CandidateIdentitySpec(
            candidate_id="c1",
            titles=frozenset({"Agent Engineer"}),
            skills=frozenset({"python", "rag"}),
            responsibilities=frozenset({"build agents"}),
            member_jd_ids=frozenset({"jd-2"}),
        ),
    )
    first = match_candidate_identity(current, candidates, {})
    second = match_candidate_identity(current, tuple(reversed(candidates)), {})
    assert first == second
    assert first.matched is True
    assert first.identity_similarity > 0.55


def test_explicit_linker_version_selection_preserves_v1_replay():
    current = CandidateIdentitySpec(
        titles=frozenset({"Agent Engineer"}),
        skills=frozenset({"python", "rag"}),
        responsibilities=frozenset({"build agents"}),
    )
    previous = CandidateIdentitySpec(
        candidate_id="candidate-1",
        titles=frozenset({"AI Agent Developer"}),
        skills=frozenset({"python", "rag"}),
        responsibilities=frozenset({"build agents"}),
    )
    replay = select_candidate_identity(
        current,
        (previous,),
        {"candidate_identity_linker_version": LEGACY_DECISION_VERSION},
    )
    assert replay == match_candidate_identity(current, (previous,), {})
    assert replay.decision_version == LEGACY_DECISION_VERSION

    selected = select_candidate_identity(current, (previous,), {})
    assert selected.matched is True
    assert selected.decision_version == SELECTED_DECISION_VERSION
    assert selected.config_version == SELECTED_CONFIG_VERSION

    with pytest.raises(ValueError, match="thresholds are locked by D14"):
        select_candidate_identity(
            current,
            (previous,),
            {"title_threshold": 0.16},
        )


def test_selected_linker_rename_false_split_false_merge_and_semantic_unavailable():
    historical = CandidateIdentitySpec(
        candidate_id="agent-role",
        titles=frozenset({"AI Agent Developer"}),
        skills=frozenset({"Python", "RAG", "Agent"}),
        responsibilities=frozenset({"build reliable agent workflows"}),
        evidence_titles=frozenset({"AI Agent Developer"}),
        evidence_skills=frozenset({"Python", "RAG", "Agent"}),
        evidence_responsibilities=frozenset({"build reliable agent workflows"}),
    )
    renamed = CandidateIdentitySpec(
        titles=frozenset({"Intelligent Automation Engineer"}),
        skills=frozenset({"Python", "RAG", "LLM"}),
        responsibilities=frozenset({"build reliable agent workflow systems"}),
        evidence_titles=frozenset({"Intelligent Automation Engineer"}),
        evidence_skills=frozenset({"Python", "RAG", "LLM"}),
        evidence_responsibilities=frozenset(
            {"build reliable agent workflow systems"}
        ),
    )
    recovered = select_candidate_identity(renamed, (historical,), {})
    assert recovered.matched is True
    assert recovered.candidate_id == "agent-role"
    assert "majority_skills_and_responsibility_midpoint" in recovered.decision_basis
    assert recovered.semantic_status == "unavailable"
    assert recovered.components.semantic_similarity is None

    unrelated = CandidateIdentitySpec(
        titles=frozenset({"Java Backend Engineer"}),
        skills=frozenset({"Java", "Spring"}),
        responsibilities=frozenset({"maintain payment services"}),
        evidence_titles=frozenset({"Java Backend Engineer"}),
        evidence_skills=frozenset({"Java", "Spring"}),
        evidence_responsibilities=frozenset({"maintain payment services"}),
    )
    separated = select_candidate_identity(unrelated, (historical,), {})
    assert separated.matched is False
    assert separated.decision_basis == ("no_rule_matched",)


def test_selected_linker_uses_traceable_membership_components_without_imputation():
    current = CandidateIdentitySpec(
        titles=frozenset({"unrelated current title"}),
        skills=frozenset(),
        responsibilities=frozenset(),
        member_evidence_ids=frozenset({"fact:new"}),
        member_dedup_cluster_ids=frozenset({"dedup-shared"}),
        member_template_cluster_ids=frozenset({"template-current"}),
    )
    previous = CandidateIdentitySpec(
        candidate_id="candidate-evidence",
        titles=frozenset({"historical title"}),
        skills=frozenset(),
        responsibilities=frozenset(),
        member_evidence_ids=frozenset({"fact:old"}),
        member_dedup_cluster_ids=frozenset({"dedup-shared"}),
        member_template_cluster_ids=frozenset({"template-previous"}),
    )
    match = select_candidate_identity(current, (previous,), {})
    assert match.matched is True
    assert match.decision_basis == ("membership_overlap_positive",)
    assert match.components.sample_overlap == 0.0
    assert match.components.dedup_cluster_overlap == 1.0
    assert match.components.template_cluster_overlap == 0.0
    assert match.components.membership_overlap == 1.0


def test_evidence_and_lineage_are_not_lost():
    first_run = _create(_role_payload(0, "evidence-1", "AI Agent Developer"))
    second_run = _create(_role_payload(1, "evidence-2", "AI Agent Developer"))
    candidate_id = _candidates()[0]["candidate_id"]
    trajectory = client.get(
        f"/api/v1/candidates/{candidate_id}/trajectory",
        headers=HEADERS,
    ).json()["data"]["trajectory"]
    evidence = trajectory[-1]["evidence"]
    assert evidence.get("evidence_jd_ids")
    assert evidence.get("algorithm_version")

    detail = client.get(
        f"/api/v1/candidates/{candidate_id}",
        headers=HEADERS,
    ).json()["data"]
    assert detail["latest_observation"]["evidence"] == evidence

    graph = client.get(
        f"/api/v1/discovery-runs/{second_run['run_id']}/lineage-graph",
        headers=HEADERS,
    ).json()["data"]
    events = {edge["event"] for edge in graph["edges"]}
    assert "continue" in events
    assert first_run["run_id"] != second_run["run_id"]


def test_candidate_filters_and_detail_404():
    _create(_role_payload(0, "filter-1", "AI Agent Developer"))
    _create(_role_payload(1, "filter-2", "AI Agent Developer", config=LOW_THRESHOLDS))
    incubating = _candidates(status="incubating")
    assert len(incubating) == 1
    by_window = _candidates(window_id="2026-06")
    assert len(by_window) == 1
    candidate_id = incubating[0]["candidate_id"]
    by_id = _candidates(candidate_id=candidate_id)
    assert len(by_id) == 1
    assert by_id[0]["candidate_id"] == candidate_id
    assert client.get(f"/api/v1/candidates/{candidate_id}", headers=HEADERS).status_code == 200
    assert (
        client.get("/api/v1/candidates/not-a-candidate", headers=HEADERS).status_code == 404
    )


def test_selected_linker_abstains_when_rule_margin_is_at_the_boundary():
    previous = CandidateIdentitySpec(
        candidate_id="agent-role",
        titles=frozenset({"AI Agent Developer"}),
        skills=frozenset({"python", "rag"}),
        responsibilities=frozenset({"build agents"}),
        evidence_titles=frozenset({"AI Agent Developer"}),
        evidence_skills=frozenset({"python", "rag"}),
        evidence_responsibilities=frozenset({"build agents"}),
    )
    current = CandidateIdentitySpec(
        titles=frozenset({"AI Agent Developer"}),
        skills=frozenset({"python"}),
        responsibilities=frozenset({"build agents"}),
        evidence_titles=frozenset({"AI Agent Developer"}),
        evidence_skills=frozenset({"python"}),
        evidence_responsibilities=frozenset({"build agents"}),
    )
    match = select_candidate_identity(current, (previous,), {})
    assert match.matched is True
    assert match.abstain is True
    assert identity_decision(match) == "review_required"
    assert match.margin == 0.0
    assert "majority_skills_and_responsibility_midpoint" in match.decision_basis


def test_review_required_creates_provisional_candidate_with_readable_certificate():
    abstain_config = {
        **LOW_THRESHOLDS,
        "identity_abstention_margin": 1.0,
        "identity_abstention_conflict_gap": 1.0,
    }
    _create(_role_payload(0, "abstain-1", "AI Agent Developer", config=abstain_config))
    _create(_role_payload(1, "abstain-2", "AI Agent Developer", config=abstain_config))

    items = _candidates()
    assert len(items) == 2
    first_candidate = next(
        item for item in items if item["last_seen_window_id"] == "2026-03"
    )
    second_candidate = next(
        item for item in items if item["last_seen_window_id"] == "2026-06"
    )
    assert second_candidate["candidate_id"] != first_candidate["candidate_id"]

    detail = client.get(
        f"/api/v1/candidates/{second_candidate['candidate_id']}",
        headers=HEADERS,
    ).json()["data"]
    certificate = detail["candidate"]["identity_certificate"]
    assert certificate["decision"] == "review_required"
    assert certificate["candidate_id"] == second_candidate["candidate_id"]
    assert certificate["closest_candidate_id"] == first_candidate["candidate_id"]
    assert certificate["margin"] is not None
    assert certificate["schema_version"] == "candidate-continuity-certificate.v1"

    trajectory = client.get(
        f"/api/v1/candidates/{second_candidate['candidate_id']}/trajectory",
        headers=HEADERS,
    ).json()["data"]["trajectory"]
    decision = trajectory[-1]["match_evidence"]
    assert decision["identity_decision"] == "review_required"
    assert decision["closest_candidate_id"] == first_candidate["candidate_id"]
    assert decision["continuity_certificate"]["decision"] == "review_required"
    assert decision["config_version"] == SELECTED_CONFIG_VERSION
    assert decision["semantic_status"] == "unavailable"
