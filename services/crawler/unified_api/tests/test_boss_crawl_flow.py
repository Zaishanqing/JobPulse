"""Boss list/detail isolation and two-page flow tests.

The production crawler uses one browser page for search pagination and a
separate tab for job details.  These fakes verify that invariant without a
network connection or native browser runtime.
"""

from types import SimpleNamespace

from unified_api.services.persistence import PersistenceResult


class FakePacket:
    def __init__(self, jobs):
        self.response = SimpleNamespace(body={"zpData": {"jobList": jobs}})


class FakeListListener:
    def __init__(self, pages):
        self._pages = iter(pages)
        self.targets = []

    def start(self, target):
        self.targets.append(target)

    def wait(self, timeout=None):
        try:
            return FakePacket(next(self._pages))
        except StopIteration:
            return None


class FakeDetailPage:
    def __init__(self):
        self.got_urls = []
        self.closed = False

    def get(self, url, timeout=None):
        self.got_urls.append(url)

    def close(self):
        self.closed = True


class FakeListPage:
    def __init__(self, pages, *, detail_page=None, new_tab_error=None):
        self.listen = FakeListListener(pages)
        self.detail_page = detail_page or FakeDetailPage()
        self.new_tab_error = new_tab_error
        self.got_urls = []
        self.url = ""
        self.quit_called = False

    def get(self, url, timeout=None):
        self.got_urls.append(url)
        self.url = url

    def eles(self, selector):
        return []

    def run_js(self, script):
        return None

    def new_tab(self):
        if self.new_tab_error:
            raise self.new_tab_error
        return self.detail_page

    def quit(self):
        self.quit_called = True


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _job(record_id):
    return {
        "encryptJobId": record_id,
        "jobName": "Python工程师",
        "brandName": "示例公司",
    }


def _install_browser_fake(monkeypatch, list_page):
    import sys

    class FakeChromiumOptions:
        def headless(self, _value):
            return self

        def set_user_data_path(self, _path):
            return self

        def set_local_port(self, _port):
            return self

        def set_browser_path(self, _path):
            return self

        def set_argument(self, _argument):
            return self

    monkeypatch.setitem(
        sys.modules,
        "DrissionPage",
        SimpleNamespace(
            ChromiumOptions=FakeChromiumOptions,
            ChromiumPage=lambda **_kwargs: list_page,
        ),
    )


def test_two_pages_keep_list_and_detail_contexts_isolated(monkeypatch):
    from unified_api.services import boss_service

    detail_page = FakeDetailPage()
    list_page = FakeListPage([[_job("page-1")], [_job("page-2")]], detail_page=detail_page)
    connection = FakeConnection()
    calls = []

    def fake_parse(job_data, *args, list_page=None, detail_tab=None, **kwargs):
        assert list_page is not detail_tab
        original_list_url = list_page.url
        detail_tab.get(f"https://detail/{job_data['encryptJobId']}")
        assert list_page.url == original_list_url
        calls.append((job_data["encryptJobId"], list_page, detail_tab))
        return PersistenceResult("boss_zhipin", job_data["encryptJobId"], "saved")

    _install_browser_fake(monkeypatch, list_page)
    monkeypatch.setattr(boss_service, "get_conn", lambda: connection)
    monkeypatch.setattr(boss_service, "_parse_and_save", fake_parse)
    monkeypatch.setattr(boss_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(boss_service.os.path, "exists", lambda _path: False)

    total = boss_service._run_drissionpage_spider("python", "北京", 2, "task", 1)

    assert total == 2
    assert [record_id for record_id, *_ in calls] == ["page-1", "page-2"]
    assert len(list_page.got_urls) == 1
    assert list_page.got_urls[0].startswith("https://www.zhipin.com/web/geek/jobs")
    assert detail_page.got_urls == ["https://detail/page-1", "https://detail/page-2"]
    assert detail_page.closed is True
    assert list_page.quit_called is True
    assert connection.closed is True


def test_detail_tab_creation_failure_never_falls_back_to_list_page(monkeypatch):
    from unified_api.services import boss_service

    list_page = FakeListPage([[_job("page-1")]], new_tab_error=RuntimeError("no tab"))
    connection = FakeConnection()
    observed_tabs = []

    def fake_parse(job_data, *args, detail_tab=None, **kwargs):
        observed_tabs.append(detail_tab)
        # Persisting the list record is still a successful database operation;
        # _parse_and_save records raw_text_status=unavailable for this branch.
        return PersistenceResult("boss_zhipin", job_data["encryptJobId"], "saved")

    _install_browser_fake(monkeypatch, list_page)
    monkeypatch.setattr(boss_service, "get_conn", lambda: connection)
    monkeypatch.setattr(boss_service, "_parse_and_save", fake_parse)
    monkeypatch.setattr(boss_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(boss_service.os.path, "exists", lambda _path: False)

    total = boss_service._run_drissionpage_spider("python", "北京", 1, "task", 1)

    assert total == 1
    assert observed_tabs == [None]
    assert len(list_page.got_urls) == 1
    assert list_page.quit_called is True


def test_failed_first_record_does_not_stop_second_record(monkeypatch):
    from unified_api.services import boss_service

    list_page = FakeListPage([[_job("invalid"), _job("valid")]])
    connection = FakeConnection()
    processed = []

    def fake_parse(job_data, *args, **kwargs):
        record_id = job_data["encryptJobId"]
        processed.append(record_id)
        if record_id == "invalid":
            return PersistenceResult(
                "boss_zhipin", record_id, "failed", "invalid_list_record", "invalid"
            )
        return PersistenceResult("boss_zhipin", record_id, "saved")

    _install_browser_fake(monkeypatch, list_page)
    monkeypatch.setattr(boss_service, "get_conn", lambda: connection)
    monkeypatch.setattr(boss_service, "_parse_and_save", fake_parse)
    monkeypatch.setattr(boss_service.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(boss_service.os.path, "exists", lambda _path: False)

    total = boss_service._run_drissionpage_spider("python", "北京", 1, "task", 1)

    assert processed == ["invalid", "valid"]
    assert total == 1
