from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone

import httpx

from app.infrastructure.sources import AclSource, ArxivSource, CvfSource, FundingSource, GithubSource, PolicySource

UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 12, 31, tzinfo=UTC)


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def configured(source, credibility_store):
    source.configure(credibility_store.payloads(credibility_store.active_versions()))
    return source


def test_arxiv_adapter_parses_atom_records(credibility_store):
    atom = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
    <id>https://arxiv.org/abs/2601.00001</id><title>Large Language Model Safety</title>
    <summary>A study of large language model alignment.</summary><published>2026-01-12T00:00:00Z</published>
    <category term="cs.AI" /></entry></feed>"""
    source = configured(
        ArxivSource(client(lambda request: httpx.Response(200, text=atom)), limit=10),
        credibility_store,
    )
    records = source.collect(START, END)
    assert records[0].external_id == "2601.00001"
    assert records[0].source == "arxiv"
    assert records[0].source_version == "arxiv.atom.v1"


def test_cvf_and_acl_adapters_parse_conference_pages(credibility_store):
    def handler(request):
        if "openaccess" in request.url.host:
            if request.url.path.endswith("Test.html"):
                return httpx.Response(200, text='<meta name="citation_publication_date" content="2026-01-12">')
            return httpx.Response(200, text='<dt class="ptitle"><a href="/content/CVPR2026/html/Test.html">Transformer Vision Systems</a></dt>')
        if request.url.path == "/2026.acl-long.1/":
            return httpx.Response(200, text='<meta name="citation_publication_date" content="2026-01-13">')
        return httpx.Response(200, text='<a href="/2026.acl-long.1/">Large Language Models for Language</a>')

    shared = client(handler)
    cvf = configured(CvfSource(shared, limit=1), credibility_store).collect(START, END)
    acl = configured(AclSource(shared, limit=1), credibility_store).collect(START, END)
    assert cvf[0].metadata["conference"] == "CVPR"
    assert acl[0].external_id == "2026.acl-long.1"


def test_cvf_and_acl_include_boundaries_and_exclude_future_records(credibility_store):
    papers = [
        ("Start Boundary Paper", "start", "2026-01-01T00:00:00Z"),
        ("End Boundary Paper", "end", "2026-12-31T00:00:00Z"),
        ("Future Dated Paper", "future", "2027-01-01T00:00:00Z"),
    ]

    def handler(request):
        slug = request.url.path.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")
        detail = next((date for _, name, date in papers if name == slug), None)
        acl_match = next(
            (
                date
                for index, (_, _, date) in enumerate(papers, start=1)
                if slug == f"2026.acl-long.{index}"
            ),
            None,
        )
        detail = detail or acl_match
        if detail:
            return httpx.Response(200, text=f'<meta name="citation_publication_date" content="{detail}">')
        if "openaccess" in request.url.host:
            if not request.url.path.startswith("/CVPR2026"):
                return httpx.Response(404)
            links = "".join(
                f'<dt class="ptitle"><a href="/content/CVPR2026/html/{name}.html">{title}</a></dt>'
                for title, name, _ in papers
            )
        else:
            if request.url.path != "/events/acl-2026/":
                return httpx.Response(404)
            links = "".join(
                f'<a href="/2026.acl-long.{index}/">{title}</a>'
                for index, (title, _, _) in enumerate(papers, start=1)
            )
        return httpx.Response(200, text=links)

    shared = client(handler)
    cvf = configured(CvfSource(shared, limit=3), credibility_store).collect(START, END)
    acl = configured(AclSource(shared, limit=3), credibility_store).collect(START, END)
    assert [record.published_at for record in cvf] == [START, END]
    assert [record.published_at for record in acl] == [START, END]


def test_cvf_and_acl_use_conference_fallback_date(credibility_store):
    def handler(request):
        if request.url.path.endswith(".html") or request.url.path == "/2026.acl-long.1/":
            return httpx.Response(200, text="<html><body>No exact publication date</body></html>")
        if "openaccess" in request.url.host:
            return httpx.Response(200, text='<dt class="ptitle"><a href="/content/CVPR2026/html/missing.html">Missing Date Vision Paper</a></dt>')
        return httpx.Response(200, text='<a href="/2026.acl-long.1/">Missing Date Language Paper</a>')

    shared = client(handler)
    cvf = configured(CvfSource(shared, limit=1), credibility_store).collect(START, END)
    acl = configured(AclSource(shared, limit=1), credibility_store).collect(START, END)
    assert len(cvf) == 1
    assert cvf[0].published_at == datetime(2026, 6, 15, tzinfo=UTC)
    assert "estimated_publish_date" in cvf[0].metadata.get("quality_flags", [])
    assert len(acl) == 1
    assert acl[0].published_at == datetime(2026, 7, 1, tzinfo=UTC)
    assert "estimated_publish_date" in acl[0].metadata.get("quality_flags", [])


def test_cvf_and_acl_isolate_a_disconnected_detail_request(credibility_store):
    def handler(request):
        if request.url.path.endswith("broken.html") or request.url.path == "/2026.acl-long.1/":
            raise httpx.RemoteProtocolError("peer disconnected")
        if request.url.path.endswith("healthy.html") or request.url.path == "/2026.acl-long.2/":
            return httpx.Response(200, text='<meta name="citation_publication_date" content="2026-07-01"><div id="abstract">A sufficiently complete healthy paper abstract for trend analysis.</div>')
        if "openaccess" in request.url.host:
            if request.url.path != "/CVPR2026":
                return httpx.Response(404)
            return httpx.Response(200, text='''
                <dt class="ptitle"><a href="/content/CVPR2026/html/broken.html">Broken Vision Paper</a></dt>
                <dt class="ptitle"><a href="/content/CVPR2026/html/healthy.html">Healthy Vision Paper</a></dt>
            ''')
        if request.url.path == "/events/acl-2026/":
            return httpx.Response(200, text='''
                <a href="/2026.acl-long.1/">Broken Language Paper</a>
                <a href="/2026.acl-long.2/">Healthy Language Paper</a>
            ''')
        return httpx.Response(404)

    shared = client(handler)
    cvf = configured(CvfSource(shared, limit=2), credibility_store).collect(START, END)
    acl = configured(AclSource(shared, limit=2), credibility_store).collect(START, END)

    assert [record.title for record in cvf] == ["Healthy Vision Paper"]
    assert [record.title for record in acl] == ["Healthy Language Paper"]
    assert "partial_source_failure" in cvf[0].metadata["quality_flags"]
    assert "partial_source_failure" in acl[0].metadata["quality_flags"]


def test_policy_and_funding_adapters_parse_real_contract_shapes(credibility_store):
    policy_data = {"searchVO": {"catMap": {"国务院": {"listVO": [{"url": "https://gov.example/p1", "title": "人工智能产业发展政策", "pubtimeStr": "2026.03.01", "summary": "推动人工智能大模型发展", "puborg": "国务院"}]}}}}
    rss = """<rss><channel><item><title>「量子公司」完成A轮融资</title>
    <description>量子计算芯片研发</description><link>https://36kr.com/p/1</link>
    <pubDate>Mon, 02 Mar 2026 00:00:00 +0000</pubDate></item></channel></rss>"""

    def handler(request):
        if "gov.cn" in request.url.host:
            return httpx.Response(200, json=policy_data)
        return httpx.Response(200, text=rss)

    shared = client(handler)
    policies = configured(PolicySource(shared), credibility_store).collect(START, END)
    funding = configured(FundingSource(shared), credibility_store).collect(START, END)
    assert len(policies) == 1
    assert policies[0].metadata["industry_domains"] == ["人工智能"]
    assert {"量子计算", "集成电路"} <= set(funding[0].metadata["industry_domains"])


def test_funding_merges_multiple_sources(credibility_store):
    rss1 = """<rss><channel><item><title>「AI公司」完成天使轮融资</title>
    <description>AI大模型研发</description><link>https://36kr.com/p/1</link>
    <pubDate>Mon, 02 Mar 2026 00:00:00 +0000</pubDate></item></channel></rss>"""
    rss2 = """<rss><channel><item><title>「芯片公司」完成A轮融资</title>
    <description>芯片设计</description><link>https://example.com/p/2</link>
    <pubDate>Tue, 03 Mar 2026 00:00:00 +0000</pubDate></item></channel></rss>"""
    rss3 = """<rss><channel><item><title>「芯片公司」完成A轮融资</title>
    <description>芯片设计</description><link>https://example.com/p/2</link>
    <pubDate>Tue, 03 Mar 2026 00:00:00 +0000</pubDate></item></channel></rss>"""

    endpoints_iter = iter([rss1, rss2, rss3])

    def handler(request):
        return httpx.Response(200, text=next(endpoints_iter))

    source = configured(
        FundingSource(client(handler), endpoints=["https://36kr.com/feed", "https://example.com/feed"]),
        credibility_store,
    )
    records = source.collect(START, END)
    assert len(records) == 2  # third is duplicate URL, deduplicated
    urls = {r.url for r in records}
    assert urls == {"https://36kr.com/p/1", "https://example.com/p/2"}


def test_github_adapter_parses_gharchive_gzip_events(credibility_store):
    events = "\n".join(
        json.dumps(item)
        for item in [
            {"type": "WatchEvent", "repo": {"name": "org/llm-tools"}},
            {"type": "ForkEvent", "repo": {"name": "org/llm-tools"}},
        ]
    ).encode()
    payload = gzip.compress(events)
    source = configured(
        GithubSource(client(lambda request: httpx.Response(200, content=payload)), hours=1),
        credibility_store,
    )
    # Use a 1-hour window so the dynamic calculation matches the old fixed-hours behavior.
    hour_start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    hour_end = datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)
    records = source.collect(hour_start, hour_end)
    assert records[0].metadata["signal_value"] == 2
    assert records[0].metadata["industry_domains"] == ["人工智能"]


def test_arxiv_pagination_loops(credibility_store):
    page1 = """<feed xmlns="http://www.w3.org/2005/Atom">
    <opensearch:totalResults xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">3</opensearch:totalResults>
    <entry>
    <id>https://arxiv.org/abs/2601.00001</id><title>Paper One</title>
    <summary>Abstract one.</summary><published>2026-01-12T00:00:00Z</published>
    <category term="cs.AI" /></entry></feed>"""
    page2 = """<feed xmlns="http://www.w3.org/2005/Atom">
    <entry>
    <id>https://arxiv.org/abs/2601.00002</id><title>Paper Two</title>
    <summary>Abstract two.</summary><published>2026-01-13T00:00:00Z</published>
    <category term="cs.CL" /></entry></feed>"""
    page3 = """<feed xmlns="http://www.w3.org/2005/Atom"></feed>"""

    responses = iter([httpx.Response(200, text=page1), httpx.Response(200, text=page2), httpx.Response(200, text=page3)])

    def handler(request):
        return next(responses)

    source = configured(
        ArxivSource(client(handler), limit=10),
        credibility_store,
    )
    records = source.collect(START, END)
    assert len(records) == 2
    assert records[0].external_id == "2601.00001"
    assert records[1].external_id == "2601.00002"


def test_cvf_and_acl_extract_abstract_from_detail_page(credibility_store):
    cvf_detail = """<html><head>
    <meta name="citation_publication_date" content="2026-06-15">
    </head><body><div id="abstract">We propose a novel vision transformer architecture for real-time object detection.</div></body></html>"""
    acl_detail = """<html><head>
    <meta name="citation_publication_date" content="2026-07-01">
    </head><body><div class="abstract">This paper presents a new approach to multilingual machine translation using large language models.</div></body></html>"""

    def handler(request):
        if request.url.path.endswith(".html") or request.url.path == "/2026.acl-long.1/":
            if "openaccess" in request.url.host:
                return httpx.Response(200, text=cvf_detail)
            return httpx.Response(200, text=acl_detail)
        if "openaccess" in request.url.host:
            return httpx.Response(200, text='<dt class="ptitle"><a href="/content/CVPR2026/html/paper.html">Vision Paper</a></dt>')
        return httpx.Response(200, text='<a href="/2026.acl-long.1/">Language Paper</a>')

    shared = client(handler)
    cvf = configured(CvfSource(shared, limit=1), credibility_store).collect(START, END)
    acl = configured(AclSource(shared, limit=1), credibility_store).collect(START, END)
    assert len(cvf) == 1
    assert "transformer architecture" in cvf[0].content.lower()
    assert cvf[0].content != cvf[0].title
    assert len(acl) == 1
    assert "multilingual machine translation" in acl[0].content.lower()
    assert acl[0].content != acl[0].title


def test_github_uses_dynamic_window(credibility_store):
    events = "\n".join(
        json.dumps({"type": "WatchEvent", "repo": {"name": "org/llm-tools"}})
        for _ in range(2)
    ).encode()
    payload = gzip.compress(events)

    requested_hours = []

    def handler(request):
        requested_hours.append(request.url.path)
        return httpx.Response(200, content=payload)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 4, tzinfo=UTC)  # ~72 hour window
    source = GithubSource(client(handler), max_hours=168)
    source.configure(credibility_store.payloads(credibility_store.active_versions()))
    records = source.collect(start, end)
    assert len(records) > 0
    assert 70 <= len(requested_hours) <= 75  # ~72 hours rounded


def test_github_flags_partial_window(credibility_store):
    events = "\n".join(
        json.dumps({"type": "WatchEvent", "repo": {"name": "org/llm-tools"}})
        for _ in range(2)
    ).encode()
    payload = gzip.compress(events)

    def handler(request):
        return httpx.Response(200, content=payload)

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)  # 31 day window > 7 day max
    source = GithubSource(client(handler), max_hours=168)
    source.configure(credibility_store.payloads(credibility_store.active_versions()))
    records = source.collect(start, end)
    flags = records[0].metadata.get("quality_flags", [])
    assert "github_partial_window" in flags


def test_github_search_mode_backfills_historical_window(credibility_store):
    """search_mode 用 GitHub Search API 按 created 日期范围回溯，支持多周历史窗口。"""
    requested_queries = []

    def handler(request):
        # 从 URL query 提取 q 参数，返回 total_count 模拟新增仓库数
        q = request.url.params.get("q", "")
        requested_queries.append(q)
        if "llm" in q:
            return httpx.Response(200, json={"total_count": 3888})
        return httpx.Response(200, json={"total_count": 0})

    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 7, tzinfo=UTC)
    source = GithubSource(client(handler), search_mode=True)
    source.configure(credibility_store.payloads(credibility_store.active_versions()))
    records = source.collect(start, end)

    # 每个 github_topic 发一次请求
    topics = credibility_store.payloads(credibility_store.active_versions())["github_topics"]
    assert len(requested_queries) == len(topics)
    # 所有 query 都带 created 日期范围（可回溯）
    assert all("created:2026-07-01..2026-07-07" in q for q in requested_queries)
    # llm topic 有 3888 新增仓库信号
    llm = next(r for r in records if "llm" in r.external_id)
    assert llm.metadata["signal_value"] == 3888
    assert llm.metadata["new_repositories"] == 3888
    assert llm.source_version == "github.search.v1"


def test_github_search_mode_handles_rate_limit(credibility_store):
    """search_mode 遇到 403 rate limit 时保留失败信号，不中断。"""
    def handler(request):
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 7, tzinfo=UTC)
    source = GithubSource(client(handler), search_mode=True)
    source.configure(credibility_store.payloads(credibility_store.active_versions()))
    records = source.collect(start, end)
    topics = credibility_store.payloads(credibility_store.active_versions())["github_topics"]
    assert len(records) == len(topics)
    assert all(r.metadata.get("rate_limited") for r in records)
    assert all("github_rate_limited" in r.metadata.get("quality_flags", []) for r in records)
