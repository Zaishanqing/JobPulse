"""HTTP and in-memory adapters for the skill-relation Port."""

from __future__ import annotations

import time
from contextlib import suppress
from copy import deepcopy

import httpx
from pydantic import ValidationError

from app.domain.skill_relations import SkillRelation
from app.ports.upstream_contracts import UpstreamResponseError, UpstreamTimeoutError


class InMemorySkillRelationSource:
    def __init__(self, relations: tuple[SkillRelation, ...] = ()) -> None:
        self._relations = tuple(relations)

    def fetch_relations(self, skill_ids: tuple[str, ...]) -> tuple[SkillRelation, ...]:
        requested = frozenset(skill_ids)
        return tuple(
            deepcopy(item)
            for item in self._relations
            if item.source_skill_id in requested or item.target_skill_id in requested
        )


class HttpSkillRelationSource:
    def __init__(
        self,
        base_url: str,
        contract_path: str = "/api/v1/contracts/skill-relations/query",
        *,
        timeout_seconds: float = 5.0,
        service_token: str | None = None,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.1,
        health_url: str | None = None,
        expected_graph_version: str | None = None,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or retry_backoff_seconds < 0:
            raise ValueError("invalid graph source retry configuration")
        self._base_url = base_url.rstrip("/")
        self._url = f"{base_url.rstrip('/')}/{contract_path.strip('/')}"
        self._timeout = timeout_seconds
        self._service_token = service_token
        self._max_retries = max_retries
        self._backoff = retry_backoff_seconds
        self._health_url = health_url or f"{self._base_url}/health"
        self._expected_graph_version = expected_graph_version

    def fetch_relations(self, skill_ids: tuple[str, ...]) -> tuple[SkillRelation, ...]:
        request = {
            "contract_version": "skill-relation-query.v1",
            "skill_ids": sorted(set(skill_ids)),
        }
        response = self._request("POST", self._url, payload=request)
        try:
            payload = response.json()
        except ValueError as exc:
            raise UpstreamResponseError(None, "graph upstream returned invalid JSON") from exc
        if isinstance(payload, dict) and "data" in payload:
            payload = payload["data"]
        if not isinstance(payload, dict) or not isinstance(payload.get("relations"), list):
            raise UpstreamResponseError(None, "invalid skill relation contract")
        graph_version = payload.get("graph_version")
        if not isinstance(graph_version, str) or not graph_version.strip():
            raise UpstreamResponseError(None, "graph version is missing")
        if (
            self._expected_graph_version
            and self._expected_graph_version != "current"
            and graph_version != self._expected_graph_version
        ):
            raise UpstreamResponseError(None, "graph version is incompatible")
        relation_values = payload["relations"]
        try:
            relations = tuple(SkillRelation.model_validate(item) for item in relation_values)
        except ValidationError as exc:
            raise UpstreamResponseError(None, "invalid skill relation contract") from exc
        relation_ids = tuple(item.relation_id for item in relations)
        requested = frozenset(skill_ids)
        if len(relation_ids) != len(set(relation_ids)):
            raise UpstreamResponseError(None, "duplicate graph relation identity")
        if any(
            self._expected_graph_version
            and self._expected_graph_version != "current"
            and item.graph_version != self._expected_graph_version
            for item in relations
        ):
            raise UpstreamResponseError(None, "graph version is incompatible")
        if any(
            item.source_skill_id not in requested and item.target_skill_id not in requested
            for item in relations
        ):
            raise UpstreamResponseError(None, "graph relation does not reference the query")
        return relations

    def check_health(self) -> None:
        self._request("GET", self._health_url)

    def _request(
        self, method: str, url: str, *, payload: object | None = None
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        for attempt in range(self._max_retries + 1):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    response = (
                        client.post(url, json=payload, headers=headers)
                        if method == "POST"
                        else client.get(url, headers=headers)
                    )
                status_code = getattr(response, "status_code", 200)
                if (status_code == 429 or status_code >= 500) and attempt < self._max_retries:
                    self._wait(
                        attempt, getattr(response, "headers", {}).get("Retry-After")
                    )
                    continue
                response.raise_for_status()
                return response
            except httpx.TimeoutException as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise UpstreamTimeoutError("graph upstream timed out") from exc
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    self._wait(attempt)
                    continue
                raise UpstreamResponseError(None, "graph upstream is unavailable") from exc
            except httpx.HTTPStatusError as exc:
                raise UpstreamResponseError(
                    exc.response.status_code, "graph upstream rejected the request"
                ) from exc
        raise AssertionError("retry loop must return or raise")  # pragma: no cover

    def _wait(self, attempt: int, retry_after: str | None = None) -> None:
        delay = self._backoff * (2**attempt)
        if retry_after:
            with suppress(ValueError):
                delay = max(delay, min(float(retry_after), 60.0))
        if delay:
            time.sleep(delay)
