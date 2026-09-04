from __future__ import annotations

from datetime import datetime, timezone
import gzip
import io
import json

import httpx
import pytest

from app.acquisition.infrastructure.connectors import (
    AclConnector,
    CvfConnector,
    FundingConnector,
    GithubConnector,
)


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 2, 1, tzinfo=UTC)
CONFIGURATIONS = {
    "domain_dictionary": {"人工智能": ["artificial intelligence", "人工智能"]},
    "policy_keywords": {"queries": ["人工智能"]},
}


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_cvf_connector_uses_mock_http_and_returns_records():
    pages = {
        "https://openaccess.thecvf.com/CVPR2026": (
            '<html><body><dl>'
            '<dt class="ptitle"><a href="/content/CVPR2026/papers/1234">Test CVF Paper</a></dt>'
            '</dl></body></html>'
        ),
        "https://openaccess.thecvf.com/content/CVPR2026/papers/1234": (
            '<html><head>'
            '<meta name="citation_publication_date" content="2026-01-15"/>'
            '<meta name="citation_abstract" content="A novel approach to computer vision using deep learning methods for image recognition and object detection tasks."/>'
            '</head></html>'
        ),
        "https://openaccess.thecvf.com/ICCV2026": '<html><body><dl></dl></body></html>',
        "https://openaccess.thecvf.com/ECCV2026": '<html><body><dl></dl></body></html>',
        "https://openaccess.thecvf.com/CVPR2025": '<html><body><dl></dl></body></html>',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        return httpx.Response(200, text=pages.get(url, "<html><body></body></html>"))

    connector = CvfConnector(client(handler), CONFIGURATIONS, default_limit=10)
    records = connector.fetch(
        {
            "id": "source-cvf",
            "source_type": "cvf",
            "endpoint_config": {"limit": 10},
            "rate_limit_rps": 10.0,
        },
        START,
        END,
    )

    assert len(records) >= 1
    assert records[0].raw_content["source"] == "cvf"
    assert records[0].raw_content["title"] == "Test CVF Paper"


def test_acl_connector_uses_mock_http_and_returns_records():
    pages = {
        "https://aclanthology.org/events/acl-2026/": (
            '<html><body><a href="/2026.acl-long.1/">Test ACL Paper on NLP</a></body></html>'
        ),
        "https://aclanthology.org/2026.acl-long.1/": (
            '<html><head>'
            '<meta name="citation_publication_date" content="2026-01-20"/>'
            '<meta name="citation_abstract" content="We present a novel transformer architecture for machine translation and text summarization achieving state-of-the-art results."/>'
            '</head></html>'
        ),
        "https://aclanthology.org/events/emnlp-2026/": '<html><body></body></html>',
        "https://aclanthology.org/events/naacl-2026/": '<html><body></body></html>',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        return httpx.Response(200, text=pages.get(url, "<html><body></body></html>"))

    connector = AclConnector(client(handler), CONFIGURATIONS, default_limit=10)
    records = connector.fetch(
        {
            "id": "source-acl",
            "source_type": "acl",
            "endpoint_config": {"limit": 10},
            "rate_limit_rps": 10.0,
        },
        START,
        END,
    )

    assert len(records) >= 1
    assert records[0].raw_content["source"] == "acl"
    assert records[0].raw_content["title"] == "Test ACL Paper on NLP"


def test_funding_connector_uses_mock_http_and_filters_funding_keywords():
    def handler(request: httpx.Request) -> httpx.Response:
        del request
        rss_xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel>
        <item>
          <title>AI Startup Completes A轮 融资</title>
          <description>An AI startup raised $50M in Series A funding round.</description>
          <link>https://36kr.com/p/12345</link>
          <pubDate>Wed, 15 Jan 2026 10:00:00 GMT</pubDate>
        </item>
        <item>
          <title>Regular Industry News</title>
          <description>Regular market update with no funding keywords.</description>
          <link>https://36kr.com/p/99999</link>
          <pubDate>Wed, 15 Jan 2026 12:00:00 GMT</pubDate>
        </item>
        </channel></rss>"""
        return httpx.Response(200, text=rss_xml)

    connector = FundingConnector(client(handler), CONFIGURATIONS)
    records = connector.fetch(
        {
            "id": "source-funding",
            "source_type": "funding",
            "endpoint_config": {},
            "rate_limit_rps": 10.0,
        },
        START,
        END,
    )

    assert len(records) == 1
    assert records[0].raw_content["source"] == "funding"
    assert "A轮" in records[0].raw_content["title"]


def test_github_connector_uses_mock_http_and_aggregates_events():
    event_lines = [
        json.dumps({"type": "WatchEvent", "repo": {"name": "huggingface/transformers"}, "created_at": "2026-01-15T10:00:00Z"}),
        json.dumps({"type": "ForkEvent", "repo": {"name": "huggingface/transformers"}, "created_at": "2026-01-15T11:00:00Z"}),
        json.dumps({"type": "WatchEvent", "repo": {"name": "tensorflow/tensorflow"}, "created_at": "2026-01-15T12:00:00Z"}),
        json.dumps({"type": "PushEvent", "repo": {"name": "huggingface/transformers"}, "created_at": "2026-01-15T13:00:00Z"}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as stream:
            stream.write("\n".join(event_lines).encode("utf-8"))
        return httpx.Response(200, content=buf.getvalue())

    github_configs = {
        **CONFIGURATIONS,
        "github_topics": {
            "transformers": "artificial intelligence",
            "tensorflow": "artificial intelligence",
        },
    }
    connector = GithubConnector(client(handler), github_configs)
    records = connector.fetch(
        {
            "id": "source-github",
            "source_type": "github",
            "endpoint_config": {"hours": 1},
            "rate_limit_rps": 10.0,
        },
        datetime(2026, 1, 15, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 15, 1, 0, tzinfo=UTC),
    )

    assert len(records) == 2
    assert all(record.raw_content["source"] == "github" for record in records)
    assert any("transformers" in record.raw_content["title"] for record in records)


def test_github_connector_rejects_non_boolean_search_mode():
    connector = GithubConnector(client(lambda request: httpx.Response(200)), {})

    with pytest.raises(ValueError, match="search_mode must be boolean"):
        connector.fetch(
            {
                "id": "source-github",
                "source_type": "github",
                "endpoint_config": {"search_mode": "false"},
                "rate_limit_rps": 10.0,
            },
            START,
            END,
        )
