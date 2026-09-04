from __future__ import annotations

import gzip
import io
from copy import deepcopy
from datetime import datetime
from typing import Mapping

import httpx

from app.acquisition.domain.acquisition import RawRecord
from app.acquisition.infrastructure.rate_limiter import RateLimiter
from app.acquisition.ports.connectors import Connector
from app.domain.market import SourceRecord
from app.infrastructure.sources import ArxivSource, PolicySource


class ConnectorRegistry:
    def __init__(self, connectors: Mapping[str, Connector] | None = None) -> None:
        self._connectors = {
            source_type.lower(): connector
            for source_type, connector in (connectors or {}).items()
        }

    def register(self, source_type: str, connector: Connector) -> None:
        self._connectors[source_type.lower()] = connector

    def resolve(self, source_type: str) -> Connector | None:
        return self._connectors.get(source_type.lower())


def _endpoint_config(source: Mapping[str, object]) -> dict[str, object]:
    value = source.get("endpoint_config")
    return dict(value) if isinstance(value, Mapping) else {}


def _to_raw_record(record: SourceRecord) -> RawRecord:
    raw_content = {
        "source": record.source,
        "source_version": record.source_version,
        "title": record.title,
        "content": record.content,
        "url": record.url,
        "published_at": record.published_at.isoformat(),
        "metadata": dict(record.metadata),
    }
    return RawRecord(
        external_id=record.external_id,
        raw_content=raw_content,
        captured_at=record.captured_at,
        metadata={
            "source_reference_url": record.url,
            "source_version": record.source_version,
        },
    )


class ArxivConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
        *,
        default_limit: int = 200,
    ) -> None:
        self.client = client
        self.configurations = configurations
        self.default_limit = default_limit

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        limiter = RateLimiter(float(source.get("rate_limit_rps") or 1.0), burst=1)
        adapter = ArxivSource(
            self.client,
            limit=int(config.get("limit", self.default_limit)),
            before_request=limiter.acquire,
        )
        if config.get("endpoint"):
            adapter.endpoint = str(config["endpoint"])
        categories = config.get("categories")
        if isinstance(categories, list) and categories:
            adapter.categories = tuple(str(item) for item in categories)
        adapter.configure(deepcopy(dict(self.configurations)))
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]


class PolicyConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
    ) -> None:
        self.client = client
        self.configurations = configurations

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        limiter = RateLimiter(float(source.get("rate_limit_rps") or 1.0), burst=1)
        adapter = PolicySource(
            self.client,
            per_query=int(config.get("per_query", 20)),
            before_request=limiter.acquire,
        )
        if config.get("endpoint"):
            adapter.endpoint = str(config["endpoint"])
        configurations = deepcopy(dict(self.configurations))
        queries = config.get("queries")
        if isinstance(queries, list) and queries:
            configurations["policy_keywords"] = {"queries": [str(item) for item in queries]}
        adapter.configure(configurations)
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]


class FixedSnapshotConnector:
    """Explicit test/demo connector; never registered by production assembly."""

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        del window_start, window_end
        config = _endpoint_config(source)
        records = config.get("records")
        if not isinstance(records, list) or not records:
            raise RuntimeError(f"acquisition source {source.get('id')} has no fixed snapshot records")
        result: list[RawRecord] = []
        for item in records:
            if not isinstance(item, Mapping):
                raise ValueError("fixed snapshot records must be JSON objects")
            captured_at = item.get("captured_at")
            if isinstance(captured_at, str):
                captured_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
            result.append(RawRecord(
                external_id=str(item.get("external_id", "")),
                raw_content=dict(item.get("raw_content") or {}),
                content_type=str(item.get("content_type", "json")),
                captured_at=captured_at if isinstance(captured_at, datetime) else None,
                metadata=dict(item.get("metadata") or {}),
            ))
        return result


from app.infrastructure.sources import CvfSource


class CvfConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
        *,
        default_limit: int = 500,
    ) -> None:
        self.client = client
        self.configurations = configurations
        self.default_limit = default_limit

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        adapter = CvfSource(
            self.client,
            limit=int(config.get("limit", self.default_limit)),
        )
        adapter.configure(deepcopy(dict(self.configurations)))
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]


from app.infrastructure.sources import AclSource


class AclConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
        *,
        default_limit: int = 500,
    ) -> None:
        self.client = client
        self.configurations = configurations
        self.default_limit = default_limit

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        adapter = AclSource(
            self.client,
            limit=int(config.get("limit", self.default_limit)),
        )
        adapter.configure(deepcopy(dict(self.configurations)))
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]


from app.infrastructure.sources import FundingSource


class FundingConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
    ) -> None:
        self.client = client
        self.configurations = configurations

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        endpoints = config.get("endpoints")
        adapter = FundingSource(
            self.client,
            endpoints=list(endpoints) if isinstance(endpoints, list) and endpoints else None,
        )
        adapter.configure(deepcopy(dict(self.configurations)))
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]


from app.infrastructure.sources import GithubSource


class GithubConnector:
    def __init__(
        self,
        client: httpx.Client,
        configurations: Mapping[str, Mapping[str, object]],
    ) -> None:
        self.client = client
        self.configurations = configurations

    def fetch(
        self,
        source: Mapping[str, object],
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawRecord]:
        config = _endpoint_config(source)
        search_mode = config.get("search_mode", False)
        if not isinstance(search_mode, bool):
            raise ValueError("search_mode must be boolean")
        adapter = GithubSource(
            self.client,
            hours=int(config.get("hours", 3)),
            max_hours=int(config.get("max_hours", 168)),
            search_mode=search_mode,
        )
        adapter.configure(deepcopy(dict(self.configurations)))
        return [_to_raw_record(record) for record in adapter.collect(window_start, window_end)]
