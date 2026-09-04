"""Test shared source identity helpers and parse_crawl_time (task 02)."""
import pytest
from jobgraph_contracts.source_identity import (
    build_source_key,
    normalize_source_record_id,
    parse_crawl_time,
)
from datetime import datetime, timezone, timedelta


class TestBuildSourceKey:
    def test_composite_key(self):
        assert build_source_key("boss_zhipin", "enc123") == "boss_zhipin:enc123"

    def test_record_id_is_normalized(self):
        assert build_source_key("boss_zhipin", "  enc123  ") == "boss_zhipin:enc123"

    def test_empty_record_id_rejected(self):
        with pytest.raises(ValueError):
            build_source_key("boss_zhipin", "")

    def test_empty_platform_rejected(self):
        with pytest.raises(ValueError):
            build_source_key("", "enc123")

    def test_normalize_strips_whitespace(self):
        assert normalize_source_record_id("  enc123  ") == "enc123"

    def test_normalize_rejects_empty(self):
        with pytest.raises(ValueError):
            normalize_source_record_id("")


class TestParseCrawlTime:
    CST = timezone(timedelta(hours=8))

    def test_utc_passes(self):
        dt = parse_crawl_time("2026-07-20T14:30:00+00:00")
        assert dt.tzinfo is not None

    def test_offset_converts_to_utc(self):
        dt = parse_crawl_time("2026-07-20T22:30:00+08:00")
        assert dt.utcoffset() == timedelta(0)  # converted to UTC
        assert dt.hour == 14  # 22-8=14

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="required"):
            parse_crawl_time("")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="required"):
            parse_crawl_time(None)

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError, match="ISO 8601"):
            parse_crawl_time("not-a-date")

    def test_naive_raises(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            parse_crawl_time("2026-07-20T14:30:00")

    def test_datetime_object_passes(self):
        dt = datetime(2026, 7, 20, 14, 30, 0, tzinfo=timezone.utc)
        result = parse_crawl_time(dt)
        assert result == dt


class TestParseCrawlTimeDoesNotFabricate:
    """P1-2: parse_crawl_time never fabricates or substitutes another timestamp."""
    def test_no_now_fallback(self):
        # The function does not have a now() fallback — it raises
        with pytest.raises(ValueError):
            parse_crawl_time("")
