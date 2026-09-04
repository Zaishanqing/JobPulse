from datetime import datetime, timezone

from app.domain.market import ExtractedTerm, SourceRecord, StoredSnapshot, weekly_term_trends
from app.infrastructure.keyword_extractor import YakeKeywordExtractor


def test_weekly_trend_uses_four_week_baseline_and_reports_growth():
    terms = []
    for week in ("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26"):
        terms.append(ExtractedTerm("snapshot", "large language model", 0.8, week, "test"))
    terms.extend(
        [ExtractedTerm(f"latest-{index}", "large language model", 0.8, "2026-02-02", "test") for index in range(3)]
    )
    trend = weekly_term_trends(terms)["large language model"]
    assert trend["count"] == 3.0
    assert trend["growth"] == 2.0
    assert trend["trend_status"] == "observed"
    assert trend["is_new_term"] is False


def test_weekly_trend_no_prior_window_single_count_is_insufficient_history():
    """无历史窗口 + 当前 count 1 → insufficient_history，growth 为 None。"""
    terms = [ExtractedTerm("s", "term", 0.8, "2026-02-02", "test")]
    trend = weekly_term_trends(terms)["term"]
    assert trend["growth"] is None
    assert trend["trend_status"] == "insufficient_history"
    assert trend["is_new_term"] is True


def test_weekly_trend_no_prior_window_count_two_is_insufficient_history():
    """无历史窗口 + 当前 count 2 → insufficient_history，不得输出 +100%。"""
    terms = [
        ExtractedTerm("s1", "term", 0.8, "2026-02-02", "test"),
        ExtractedTerm("s2", "term", 0.8, "2026-02-02", "test"),
    ]
    trend = weekly_term_trends(terms)["term"]
    assert trend["growth"] is None
    assert trend["trend_status"] == "insufficient_history"


def test_weekly_trend_one_prior_zero_count_is_newly_observed():
    """一个 prior window 中该 term count 0 + 当前 count 2 → newly_observed，不构造固定增长率。"""
    terms = [
        ExtractedTerm("other", "other_term", 0.8, "2026-01-26", "test"),
        ExtractedTerm("s1", "term", 0.8, "2026-02-02", "test"),
        ExtractedTerm("s2", "term", 0.8, "2026-02-02", "test"),
    ]
    trend = weekly_term_trends(terms)["term"]
    assert trend["growth"] is None
    assert trend["trend_status"] == "newly_observed"
    assert trend["is_new_term"] is True


def test_yake_extractor_returns_versioned_terms_linked_to_snapshot():
    record = SourceRecord(
        source="arxiv",
        external_id="paper-1",
        source_version="v1",
        title="Large language model alignment and safety evaluation",
        content="Large language model alignment requires robust safety evaluation and reliable benchmark design for deployment.",
        url="https://example.test/paper-1",
        published_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    terms = YakeKeywordExtractor(max_terms=5).extract(StoredSnapshot("snapshot-1", record))
    assert terms
    assert {term.snapshot_id for term in terms} == {"snapshot-1"}
    assert {term.extractor_version for term in terms} == {"yake.v1"}
