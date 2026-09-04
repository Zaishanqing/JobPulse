"""Test crawl_time parsing and MySQL round-trip (Block 3)."""
import pytest
from datetime import datetime, timezone, timedelta
from jobgraph_contracts.source_identity import parse_crawl_time

CST = timezone(timedelta(hours=8))
UTC = timezone.utc


class TestParseCrawlTime:
    def test_utc_passes(self):
        dt = parse_crawl_time("2026-07-20T14:30:00+00:00")
        assert dt.tzinfo is not None

    def test_offset_converts_to_utc(self):
        dt = parse_crawl_time("2026-07-20T22:30:00+08:00")
        assert dt.utcoffset() == timedelta(0)
        assert dt.hour == 14

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="required"):
            parse_crawl_time("")

    def test_naive_raises(self):
        with pytest.raises(ValueError, match="timezone-aware"):
            parse_crawl_time("2026-07-20T14:30:00")

    def test_now_not_used(self):
        """parse_crawl_time raises, never returns datetime.now()."""
        with pytest.raises(ValueError):
            parse_crawl_time("")


class TestMySQLRoundTrip:
    def test_write_utc_read_utc(self):
        """Write timezone-aware UTC → to naive MySQL → read back → restore tz."""
        original = datetime(2026, 7, 20, 14, 30, 0, tzinfo=UTC)
        utc = parse_crawl_time(original)
        db_value = utc.replace(tzinfo=None)
        # Simulate reading from MySQL DATETIME (naive)
        read_back = db_value.replace(tzinfo=UTC)
        assert read_back.isoformat() == "2026-07-20T14:30:00+00:00"


class TestNoFallback:
    def test_rejects_none(self):
        with pytest.raises(ValueError):
            parse_crawl_time(None)

    def test_rejects_invalid(self):
        with pytest.raises(ValueError, match="ISO 8601"):
            parse_crawl_time("not-a-date")
