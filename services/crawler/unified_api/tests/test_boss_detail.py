"""Test Boss detail extraction (P0-3/P0-4/P0-5)."""
import json
import pytest
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Fake page for testing (no real browser needed)
# ---------------------------------------------------------------------------

class FakeListener:
    """Stable listener fake that records lifecycle order."""

    def __init__(self, parent, response=None):
        self._parent = parent
        self._response = response

    def reset(self):
        self._parent.events.append("listener.reset")

    def stop(self):
        self._parent.events.append("listener.stop")

    def start(self, target):
        self._parent.events.append(("listener.start", target))
        self._parent._listen_started.append(target)

    def wait(self, timeout=None):
        self._parent.events.append(("listener.wait", timeout))
        self._parent._listen_waited += 1
        if isinstance(self._response, Exception):
            raise self._response
        if self._response:
            mock_resp = MagicMock()
            mock_resp.response.body = self._response
            return mock_resp
        return None


class FakePage:
    """Mimics one persistent DrissionPage detail tab for testing."""
    def __init__(self, html="", text_map=None, api_response=None):
        self.html = html
        self._text_map = text_map or {}
        self._api_response = api_response
        self._listen_started = []
        self._listen_waited = 0
        self._got_urls = []
        self._get_timeout = None
        self.events = []
        self.url = ""  # for navigation identity check
        self.listen = FakeListener(self, api_response)

    def get(self, url, timeout=None):
        self.events.append(("page.get", url, timeout))
        self._got_urls.append(url)
        self._get_timeout = timeout
        self.url = url  # simulate navigation

    def ele(self, selector, timeout=None):
        text = self._text_map.get(selector, "")
        if text:
            mock = MagicMock()
            mock.text = text
            mock.__bool__ = lambda s: True
            return mock
        return None

    def eles(self, selector):
        return []

    def run_js(self, js):
        pass

def _make_fake_api_page(jd_text="Detailed JD content about Python development " * 5, job_id="enc123"):
    resp = json.dumps({
        "zpData": {"jobDetail": {"encryptJobId": job_id, "jobDescription": jd_text}}
    })
    return FakePage(api_response=resp)


def _make_fake_dom_page(jd_text="Detailed JD content about Python development " * 5):
    return FakePage(text_map={".job-detail-content": jd_text})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBossDetailAPI:
    def test_api_success(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev", list_payload={"kw": "java"})
        assert result.status == "completed"
        assert "Python" in result.raw_text
        assert result.detail_extraction_method == "api"

    def test_api_identity_mismatch_rejected(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page(job_id="wrong_id")
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev")
        assert result.status == "failed"

    def test_api_welfare_disguised_rejected(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        jd = "五险一金 年终奖 带薪年假 零食下午茶 免费咖啡 定期体检 " * 10  # 100+ chars
        resp = json.dumps({
            "zpData": {"jobDetail": {"encryptJobId": "enc123", "jobDescription": jd}}
        })
        page = FakePage(api_response=resp)
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev", benefits_raw="五险一金 年终奖 带薪年假")
        assert result.status == "failed"
        assert result.error_code == "detail_text_too_short"

    def test_listener_started_before_navigate(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                              job_title_raw="Python Dev")
        assert len(page._listen_started) > 0

    def test_api_listener_lifecycle_order(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        fetch_boss_job_detail(
            page,
            source_record_id="enc123",
            encrypt_job_id="enc123",
            job_title_raw="Python Dev",
        )
        assert page.events[:5] == [
            "listener.reset",
            ("listener.start", [
                "job/detail", "job/detail.json", "zpgeek/job/detail", "job/card"
            ]),
            ("page.get", page._got_urls[0], 30),
            ("listener.wait", 30),
            "listener.reset",
        ]

    @pytest.mark.parametrize(
        ("current_url", "expected_code"),
        [
            ("https://www.zhipin.com/web/user/login", "detail_navigation_blocked"),
            ("https://www.zhipin.com/job_detail/other.html", "detail_navigation_mismatch"),
        ],
    )
    def test_blocked_or_mismatched_url_skips_sources(self, current_url, expected_code):
        from unified_api.services.boss_detail import fetch_boss_job_detail

        class RedirectedPage(FakePage):
            def get(self, url, timeout=None):
                super().get(url, timeout)
                self.url = current_url

        page = RedirectedPage(
            text_map={".job-detail-content": "valid content " * 30},
            api_response=_make_fake_api_page()._api_response,
        )
        result = fetch_boss_job_detail(
            page,
            source_record_id="enc123",
            encrypt_job_id="enc123",
            job_title_raw="Python Dev",
        )
        assert result.error_code == expected_code
        assert not any(event[0] == "listener.wait" for event in page.events if isinstance(event, tuple))
        assert "listener.reset" == page.events[-1]

    def test_listener_exception_still_resets_and_uses_dom(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = FakePage(
            text_map={".job-detail-content": "Responsible for backend delivery. " * 8},
            api_response=TimeoutError("listener timeout"),
        )
        result = fetch_boss_job_detail(
            page,
            source_record_id="enc123",
            encrypt_job_id="enc123",
            job_title_raw="Python Dev",
        )
        assert result.status == "completed"
        assert result.detail_extraction_method == "dom"
        assert page.events[-1] == "listener.reset"

    def test_timeout_passed_to_get(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                              job_title_raw="Python Dev", timeout_seconds=15)
        assert page._get_timeout == 15


class TestBossDetailDOM:
    def test_dom_fallback_success(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        jd = "Responsible for designing and implementing REST APIs " * 5
        page = FakePage(text_map={".job-detail-content": jd})
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev")
        assert result.status == "completed"
        assert result.detail_extraction_method == "dom"

    def test_dom_skills_list_rejected(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        # Build a text that is 95% skill words, 5% other — should trigger overlap check
        skills = "Python Java Go Docker Kubernetes AWS GCP Azure React Vue"
        jd = (skills + " ") * 12  # 120 words, all skills
        page = FakePage(text_map={".job-detail-content": jd})
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev",
                                       skills_raw="Python Java Go Docker Kubernetes AWS GCP Azure React Vue")
        # skills_raw covers all words in jd_text, so non-skill words < 15
        assert result.status == "failed"


class TestValidateBossRawText:
    def test_valid_jd_passes(self):
        from unified_api.services.boss_detail import validate_boss_raw_text
        jd = "Responsible for backend API development and database design. " * 5
        ok, reason = validate_boss_raw_text(jd, job_title="Python Dev")
        assert ok

    def test_benefits_only_rejected(self):
        from unified_api.services.boss_detail import validate_boss_raw_text
        jd = "五险一金 年终奖 带薪年假 零食下午茶 免费咖啡 弹性工作 " * 10
        ok, _ = validate_boss_raw_text(jd, benefits_raw="五险一金 年终奖 带薪年假")
        assert not ok

    def test_title_only_rejected(self):
        from unified_api.services.boss_detail import validate_boss_raw_text
        ok, _ = validate_boss_raw_text("Python Dev", job_title="Python Dev")
        assert not ok

    def test_too_short_rejected(self):
        from unified_api.services.boss_detail import validate_boss_raw_text
        ok, reason = validate_boss_raw_text("short text")
        assert not ok
        assert "too short" in reason


class TestBossDetailProvenance:
    def test_completed_has_list_payload(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev", list_payload={"kw": "java"})
        assert result.status == "completed"
        assert "list_payload" in result.raw_payload

    def test_failed_has_list_payload(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = FakePage()  # no API, no DOM
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev", list_payload={"kw": "java"},
                                       max_retries=1, sleep_fn=lambda x: None)
        assert result.status == "failed"
        assert "list_payload" in result.raw_payload

    def test_completed_has_extraction_method(self):
        from unified_api.services.boss_detail import fetch_boss_job_detail
        page = _make_fake_api_page()
        result = fetch_boss_job_detail(page, source_record_id="enc123", encrypt_job_id="enc123",
                                       job_title_raw="Python Dev")
        assert result.detail_extraction_method == "api"
        assert result.raw_payload.get("detail_extraction_method") == "api"


class TestSanitizeRawPayload:
    def test_removes_cookies(self):
        from unified_api.services.boss_detail import sanitize_raw_payload
        payload = {"data": {"cookie": "secret", "cookies": "secret2", "jobTitle": "Engineer"}}
        result = sanitize_raw_payload(payload)
        assert "cookie" not in result["data"]
        assert "cookies" not in result["data"]
        assert result["data"]["jobTitle"] == "Engineer"

    def test_recursive(self):
        from unified_api.services.boss_detail import sanitize_raw_payload
        payload = {"a": {"b": {"token": "x", "name": "y"}}}
        result = sanitize_raw_payload(payload)
        assert result["a"]["b"].get("name") == "y"
        assert "token" not in result["a"]["b"]
