from __future__ import annotations

import pytest

from app.contexts.acquisition.domain import (
    TERMINAL_STATUSES,
    can_retry,
    require_transition,
    AcquisitionTransitionConflict,
)


def test_valid_transitions():
    require_transition("pending", "crawling")
    require_transition("crawling", "exporting")
    require_transition("exporting", "verifying")
    require_transition("verifying", "importing")
    require_transition("importing", "completed")
    require_transition("crawling", "crawl_failed")
    require_transition("exporting", "export_failed")
    require_transition("verifying", "verify_failed")
    require_transition("importing", "import_failed")


def test_invalid_transition_from_terminal():
    with pytest.raises(AcquisitionTransitionConflict):
        require_transition("completed", "crawling")
    with pytest.raises(AcquisitionTransitionConflict):
        require_transition("crawl_failed", "running")
    with pytest.raises(AcquisitionTransitionConflict):
        require_transition("pending", "importing")


def test_terminal_statuses_are_terminal():
    for status in TERMINAL_STATUSES:
        assert can_retry(status) == (status in {"crawl_failed", "export_failed", "verify_failed", "import_failed"})


def test_retry_from_failure_allowed_but_not_completed_or_cancelled():
    assert can_retry("crawl_failed") is True
    assert can_retry("export_failed") is True
    assert can_retry("verify_failed") is True
    assert can_retry("import_failed") is True
    assert can_retry("completed") is False
    assert can_retry("cancelled") is False
    assert can_retry("pending") is False
