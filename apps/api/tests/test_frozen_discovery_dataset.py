from app.infrastructure.discovery_datasets import (
    FROZEN_DISCOVERY_DATASET_ID,
    frozen_cluster_jds,
    frozen_discovery_rows,
    frozen_discovery_windows,
    list_frozen_discovery_facts,
)
from app.contexts.discovery import released_jd_contract


def test_frozen_dataset_reuses_all_preregistered_extraction_rows():
    rows = frozen_discovery_rows()
    facts = list_frozen_discovery_facts(FROZEN_DISCOVERY_DATASET_ID)

    assert len(rows) == len(facts) == 127
    assert {row["window_id"] for row in rows} == {
        "D5-SW-01",
        "D5-SW-02",
        "D5-SW-03",
        "D5-SW-04",
    }
    assert all(fact.review_status == "approved" for fact in facts)
    assert all(fact.consumption_path is None for fact in facts)
    assert all(fact.bundle_id == FROZEN_DISCOVERY_DATASET_ID for fact in facts)
    assert sum(len(fact.structured_data["responsibilities"]) for fact in facts) > 0
    assert sum(len(fact.structured_data["required_skills"]) for fact in facts) > 0
    payload = released_jd_contract(facts[0])
    assert payload["structured_data"]["date_source"] == "publish_date"
    assert not {
        "region",
        "role_code",
        "window_id",
        "dataset_version",
        "bundle_observations",
    } & set(payload["structured_data"])
    assert [
        (window.window_id, window.start.isoformat(), window.end.isoformat())
        for window in frozen_discovery_windows()
    ] == [
        ("D5-SW-01", "2026-07-16", "2026-07-20"),
        ("D5-SW-02", "2026-07-21", "2026-07-25"),
        ("D5-SW-03", "2026-07-26", "2026-07-30"),
        ("D5-SW-04", "2026-07-31", "2026-08-03"),
    ]


def test_frozen_dataset_cluster_members_remain_inspectable():
    facts = list_frozen_discovery_facts(FROZEN_DISCOVERY_DATASET_ID)
    records = frozen_cluster_jds([facts[0].jd_id, facts[-1].jd_id])

    assert [record.jd_id for record in records] == [facts[0].jd_id, facts[-1].jd_id]
    assert all(record.source_type == "frozen_experiment" for record in records)
    assert all(record.raw_text for record in records)
