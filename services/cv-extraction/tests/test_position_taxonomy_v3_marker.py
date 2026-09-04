from src.match_features import classify_role


def test_default_role_feature_requires_position_taxonomy_v3_review() -> None:
    classification = classify_role(
        "后端开发工程师",
        {"position_taxonomy_version": "position-taxonomy.v3.0.0"},
    )

    assert classification == {
        "resolution_status": "unresolved",
        "resolution_source": "position-taxonomy-v3-review-required",
        "source_title": "后端开发工程师",
        "taxonomy_version": "position-taxonomy.v3.0.0",
    }
