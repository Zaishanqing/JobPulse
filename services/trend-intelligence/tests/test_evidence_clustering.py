from datetime import datetime, timezone

from app.domain.evidence import cluster_snapshots, normalize_url, quality_weight
from app.domain.market import SourceRecord, StoredSnapshot


NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)


def snapshot(
    identifier: str,
    *,
    title: str,
    content: str,
    url: str,
    metadata: dict | None = None,
) -> StoredSnapshot:
    return StoredSnapshot(
        identifier,
        SourceRecord(
            source="policy",
            external_id=identifier,
            source_version="fixed-demo.v1",
            title=title,
            content=content,
            url=url,
            published_at=NOW,
            metadata=metadata or {},
        ),
    )


def test_url_normalization_removes_tracking_without_changing_identity():
    assert normalize_url("HTTPS://www.Example.com/news/?utm_source=x&id=7#part") == (
        "https://example.com/news?id=7"
    )


def test_reposts_and_near_duplicate_titles_form_one_independent_event():
    values = [
        snapshot(
            "a",
            title="企业加速采用 Python 人工智能平台",
            content="企业在生产环境中加速采用 Python 人工智能平台，并扩大招聘。" * 4,
            url="https://example.com/news/1?utm_source=feed",
        ),
        snapshot(
            "b",
            title="企业正加速采用 Python 人工智能平台",
            content="企业在生产环境中加速采用 Python 人工智能平台，并扩大招聘。" * 4,
            url="https://mirror.test/repost/9",
        ),
        snapshot(
            "c",
            title="另一项独立政策事件",
            content="监管机构发布了另一项独立政策事件的完整正文。" * 4,
            url="https://gov.test/policy/2",
        ),
    ]
    clusters = cluster_snapshots(values)
    assert sorted(len(item.snapshots) for item in clusters) == [1, 2]


def test_title_only_and_estimated_date_are_retained_with_explicit_penalty():
    value = snapshot(
        "low-quality",
        title="Python 技能需求上升",
        content="Python 技能需求上升",
        url="https://example.test/title-only",
        metadata={"date_precision": "estimated", "content_completeness": "title_only"},
    )
    weight, warnings = quality_weight(value)
    assert weight == 0.45
    assert warnings == ("estimated_publish_date", "title_only")
