from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx

from app.acquisition.application.crawl_service import CrawlService
from app.acquisition.infrastructure.acquisition_store import SqlAlchemyAcquisitionStore
from app.acquisition.infrastructure.connectors import ArxivConnector, ConnectorRegistry, PolicyConnector


UTC = timezone.utc
CONFIGURATIONS = {
    "domain_dictionary": {
        "人工智能": ["人工智能", "artificial intelligence"],
    },
    "policy_keywords": {"queries": ["人工智能"]},
}


def _arxiv_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="""<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
      <opensearch:totalResults>1</opensearch:totalResults><entry>
      <id>https://arxiv.org/abs/2601.00001</id>
      <title>Artificial Intelligence for Industry</title>
      <summary>Production research record.</summary>
      <published>2026-01-10T00:00:00Z</published>
      <category term="cs.AI" /></entry></feed>""")


def _policy_response(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"searchVO": {"catMap": {"policy": {"listVO": [{
        "url": "https://www.gov.cn/zhengce/policy-1",
        "title": "人工智能产业发展政策",
        "summary": "推动人工智能产业发展",
        "pubtimeStr": "2026-01-12",
        "puborg": "国务院",
    }]}}}})


def _create_job(store, source_id: str):
    return store.create_crawl_job({
        "source_id": source_id,
        "window_start": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
        "window_end": datetime(2026, 2, 1, tzinfo=UTC).isoformat(),
        "max_retries": 0,
    })


def test_two_real_connectors_write_concurrently_and_duplicate_snapshot_is_idempotent(database):
    store = SqlAlchemyAcquisitionStore(database.sessions)
    arxiv_source = store.create_source({
        "name": "mock-http-arxiv",
        "source_type": "arxiv",
        "endpoint_config": {"limit": 20},
        "rate_limit_rps": 100.0,
        "compliance_policy": {"mode": "test_mock_http"},
    })
    policy_source = store.create_source({
        "name": "mock-http-policy",
        "source_type": "policy",
        "endpoint_config": {"queries": ["人工智能"], "per_query": 20},
        "rate_limit_rps": 100.0,
        "compliance_policy": {"mode": "test_mock_http"},
    })
    first_jobs = [
        _create_job(store, str(arxiv_source["id"])),
        _create_job(store, str(policy_source["id"])),
    ]
    with (
        httpx.Client(transport=httpx.MockTransport(_arxiv_response)) as arxiv_client,
        httpx.Client(transport=httpx.MockTransport(_policy_response)) as policy_client,
    ):
        service = CrawlService(store, registry=ConnectorRegistry({
            "arxiv": ArxivConnector(arxiv_client, CONFIGURATIONS),
            "policy": PolicyConnector(policy_client, CONFIGURATIONS),
        }))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(
                service.execute_job,
                [str(job["id"]) for job in first_jobs],
            ))
        assert [result["status"] for result in results] == ["succeeded", "succeeded"]
        assert all(result["new_snapshot_count"] == 1 for result in results)
        assert all(store.get_bundle_for_job(str(job["id"])) is not None for job in first_jobs)

        duplicate_job = _create_job(store, str(arxiv_source["id"]))
        duplicate = service.execute_job(str(duplicate_job["id"]))

    assert duplicate["status"] == "succeeded"
    assert duplicate["fetched_count"] == 1
    assert duplicate["new_snapshot_count"] == 0
    assert duplicate["duplicate_count"] == 1
    assert len(store.list_snapshots(str(arxiv_source["id"]), limit=10)) == 1
    assert len(store.list_snapshot_observations(str(duplicate_job["id"]))) == 1
