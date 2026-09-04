from datetime import datetime, timezone
from dataclasses import replace

from app.domain.graph_drafts import (
    GraphDraftCommand,
    GraphDraftFacts,
    GraphDraftVersionFact,
    decide_graph_draft,
)
from app.domain.published_facts import (
    PublishedFactIdentity,
    PublishedFactRecord,
    PublishedFactValidationFacts,
    decide_published_fact_import,
)
from app.domain.relation_editing import (
    RelationEditCommand,
    RelationEditFacts,
    decide_relation_edit,
)
from app.domain.review_tasks import (
    ReviewTaskCommand,
    ReviewTaskFacts,
    decide_review_task_transition,
)
from app.domain.skill_resolution import (
    NormalizedSkillTargetFact,
    SkillCatalogFact,
    SkillResolutionCommand,
    SkillResolutionFacts,
    SkillResolutionItemFact,
    decide_skill_resolution,
)
from tests.test_published_fact_ingestion import published_command


def test_published_fact_decision_distinguishes_idempotent_stale_and_conflict():
    fact = published_command().fact
    identity = PublishedFactIdentity(fact.source_system, fact.source_fact_id)
    same = PublishedFactRecord(
        fact.source_jd_id, identity, fact.source_fact_version, fact.source_version
    )
    idempotent = decide_published_fact_import(
        PublishedFactValidationFacts(fact, same, same)
    )
    assert idempotent.accepted and idempotent.idempotent

    current = PublishedFactRecord(fact.source_jd_id, identity, "2", "newer")
    stale_fact = replace(fact, source_fact_version="1")
    stale = decide_published_fact_import(
        PublishedFactValidationFacts(stale_fact, None, current)
    )
    assert stale.accepted and stale.stale

    conflict = decide_published_fact_import(
        PublishedFactValidationFacts(
            fact,
            PublishedFactRecord(fact.source_jd_id, identity, "1", "different"),
            None,
        )
    )
    assert not conflict.accepted
    assert conflict.rejection.error_code == "PUBLISHED_FACT_CONTENT_CONFLICT"


def test_skill_resolution_domain_builds_create_and_reject_plans():
    item = SkillResolutionItemFact(4, "JD_1", "skill", "Python", "open")
    target = NormalizedSkillTargetFact(8, "Python")
    create = decide_skill_resolution(
        SkillResolutionFacts(item, target, None, False),
        SkillResolutionCommand(
            "create_skill", 2, "verified", "trace", generated_skill_id="SK_NEW",
            category_code="TECH",
        ),
    )
    assert create.accepted
    assert create.plan.create_skill.skill_id == "SK_NEW"
    assert create.plan.target_item_status == "created_new_skill"

    reject = decide_skill_resolution(
        SkillResolutionFacts(item, None, None, False),
        SkillResolutionCommand("reject", 2, "invalid", "trace"),
    )
    assert reject.accepted
    assert reject.plan.target_item_status == "rejected"


def test_graph_draft_domain_selects_base_and_applies_compatibility_defaults():
    version = GraphDraftVersionFact(
        7,
        "POS_1",
        datetime(2026, 7, 1, tzinfo=timezone.utc),
        None,
        {},
        {"included_samples": 0},
    )
    decision = decide_graph_draft(
        GraphDraftFacts("POS_1", True, 7, version, None),
        GraphDraftCommand("POS_1"),
    )
    assert decision.accepted
    assert decision.plan.draft_key == "POS_1:7"
    assert decision.plan.config_snapshot["minimum_valid_samples"] == 1
    assert decision.plan.summary["included_samples"] == 1


def test_relation_edit_domain_calculates_manual_values_and_revision():
    facts = RelationEditFacts(
        3, True, 9, "POS_1", True, "POS_1", False, 4, "approved",
        0.4, None, 0.4, 0.7, None, 0.7, "supplementary", None,
        "supplementary",
    )
    decision = decide_relation_edit(
        facts,
        RelationEditCommand(
            3, 9, "POS_1", 4, "manual", frozenset({"weight"}), weight=0.9
        ),
    )
    assert decision.accepted
    assert decision.plan.final_weight == 0.9
    assert decision.plan.final_confidence == 0.7
    assert decision.plan.next_revision == 5
    assert decision.plan.target_status == "draft"


def test_review_task_domain_owns_transition_assignment_and_effect():
    pending = ReviewTaskFacts(2, "position_skill_relation", "3", 9, "pending", None)
    claim = decide_review_task_transition(
        pending, ReviewTaskCommand("claim", 5, "trace")
    )
    assert claim.accepted
    assert claim.plan.transition.target_assignee_id == 5

    claimed = ReviewTaskFacts(
        2, "position_skill_relation", "3", 9, "claimed", 5, {"old": True}
    )
    approve = decide_review_task_transition(
        claimed,
        ReviewTaskCommand("approve", 5, "trace", attributes={"checked": True}),
    )
    assert approve.accepted
    assert approve.plan.transition.target_status == "approved"
    assert approve.plan.effect.target_status == "approved"
    assert approve.plan.payload == {"old": True, "checked": True}

    wrong_actor = decide_review_task_transition(
        claimed, ReviewTaskCommand("reject", 6, "trace")
    )
    assert not wrong_actor.accepted
    assert wrong_actor.rejection.message == "review task belongs to another reviewer"
