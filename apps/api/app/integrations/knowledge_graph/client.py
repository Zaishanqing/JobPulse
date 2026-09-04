from __future__ import annotations

from threading import Lock
from typing import Any

import httpx

from app.core.request_context import get_trace_id
from app.integrations.knowledge_graph.exceptions import (
    KnowledgeGraphError,
    KnowledgeGraphUnavailable,
)
from app.integrations.knowledge_graph.schemas import UpstreamEnvelope


class KnowledgeGraphClient:
    """The only HTTP boundary between the main backend and the KG service."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        timeout_seconds: float = 20,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.username = username
        self.password = password
        self._token: str | None = None
        self._token_lock = Lock()
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _headers(
        self,
        *,
        authenticated: bool,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> dict[str, str]:
        trace_id = get_trace_id()
        headers = {"X-Trace-Id": trace_id, "X-Request-ID": trace_id}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._get_token()}"
        if actor_id:
            headers["X-Main-User-Id"] = actor_id
        if actor_role:
            headers["X-Main-User-Role"] = actor_role
        return headers

    def _get_token(self, *, force_refresh: bool = False) -> str:
        with self._token_lock:
            if self._token and not force_refresh:
                return self._token
            envelope = self._request(
                "POST",
                "/api/v1/auth/token",
                json={"username": self.username, "password": self.password},
                authenticated=False,
            )
            token = (envelope.data or {}).get("access_token")
            if not isinstance(token, str) or not token:
                raise KnowledgeGraphUnavailable(
                    "Knowledge graph authentication returned no access token",
                    error_code="knowledge_graph_auth_invalid",
                    trace_id=envelope.trace_id,
                )
            self._token = token
            return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        authenticated: bool = True,
        actor_id: str | None = None,
        actor_role: str | None = None,
        refresh_allowed: bool = True,
    ) -> UpstreamEnvelope:
        attempts = 2 if method.upper() == "GET" else 1
        response = None
        for attempt in range(attempts):
            try:
                response = self._http.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=self._headers(
                        authenticated=authenticated,
                        actor_id=actor_id,
                        actor_role=actor_role,
                    ),
                )
                break
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 < attempts:
                    continue
                raise KnowledgeGraphUnavailable(
                    "Knowledge graph service is unavailable",
                    details={"reason": type(exc).__name__},
                    trace_id=get_trace_id(),
                ) from exc
        assert response is not None
        try:
            envelope = UpstreamEnvelope.model_validate(response.json())
        except (ValueError, TypeError) as exc:
            raise KnowledgeGraphUnavailable(
                "Knowledge graph returned an invalid response envelope",
                details={"upstream_status": response.status_code},
                trace_id=response.headers.get("X-Trace-Id"),
            ) from exc
        envelope.response_headers = {
            name: response.headers[name]
            for name in ("X-Total-Count", "X-Page", "X-Page-Size")
            if name in response.headers
        }
        if response.status_code == 401 and authenticated and refresh_allowed:
            self._get_token(force_refresh=True)
            return self._request(
                method,
                path,
                json=json,
                params=params,
                authenticated=True,
                actor_id=actor_id,
                actor_role=actor_role,
                refresh_allowed=False,
            )
        if response.status_code >= 400 or envelope.code != 0:
            status_code = response.status_code
            if status_code >= 500:
                status_code = 503
            upstream_error_code = (
                envelope.details.get("error_code")
                if isinstance(envelope.details, dict)
                else None
            )
            raise KnowledgeGraphError(
                envelope.message,
                status_code=status_code,
                error_code=(
                    str(upstream_error_code)
                    if isinstance(upstream_error_code, str)
                    and upstream_error_code
                    else f"knowledge_graph_{response.status_code}"
                ),
                details=envelope.details,
                trace_id=envelope.trace_id,
            )
        return envelope

    def readiness(self) -> UpstreamEnvelope:
        return self._request("GET", "/readiness", authenticated=False)

    def import_document(self, payload: dict, **actor: str) -> UpstreamEnvelope:
        document_id = payload["document_id"]
        return self._request(
            "PUT", f"/api/v1/integrations/jds/{document_id}", json=payload, **actor
        )

    def import_published_fact_v3(self, payload: dict, **actor: str) -> UpstreamEnvelope:
        return self._request(
            "POST", "/api/v3/integrations/published-jd-facts", json=payload, **actor
        )

    def upsert_skill_snapshot(
        self, skill_id: str, payload: dict, **actor: str
    ) -> UpstreamEnvelope:
        return self._request(
            'PUT',
            f'/api/v2/integrations/catalog/skills/{skill_id}',
            json=payload,
            **actor,
        )

    def import_extraction(
        self, document_id: str, payload: dict, **actor: str
    ) -> UpstreamEnvelope:
        return self._request(
            "POST", f"/api/v1/jds/{document_id}/extraction-result/import",
            json=payload, **actor,
        )

    def align_extraction(self, document_id: str, **actor: str) -> UpstreamEnvelope:
        return self._request(
            "POST", f"/api/v1/jds/{document_id}/extraction-result/align", **actor
        )

    def import_normalization(
        self, document_id: str, payload: dict, **actor: str
    ) -> UpstreamEnvelope:
        return self._request(
            "POST", f"/api/v1/jds/{document_id}/normalized-result/import",
            json=payload, **actor,
        )

    def assess_quality(self, document_id: str, **actor: str) -> UpstreamEnvelope:
        return self._request(
            "POST", f"/api/v1/jds/{document_id}/duplicate-check", json={}, **actor
        )

    def list_positions(self) -> list[dict]:
        return self._request("GET", "/api/v1/integrations/positions").data or []

    def list_skills(self) -> list[dict]:
        return self._request("GET", "/api/v1/skills").data or []

    def replay_formal_emergence_v32(self) -> UpstreamEnvelope:
        return self._request(
            "POST",
            "/api/v1/integrations/emergence/v3.2/formal-replay",
            json={},
        )

    def skill_relations(self, position_id: str, **actor: str) -> UpstreamEnvelope:
        return self._request(
            "GET",
            f"/api/v1/integrations/positions/{position_id}/skill-relations",
            **actor,
        )

    def build_graph(self, position_id: str, payload: dict, **actor: str) -> UpstreamEnvelope:
        return self._request(
            "POST", f"/api/v1/positions/{position_id}/graph/build", json=payload, **actor
        )

    def build_runs(self, position_id: str) -> UpstreamEnvelope:
        return self._request("GET", f"/api/v1/positions/{position_id}/graph/build-runs")

    def build_run(self, run_id: str) -> UpstreamEnvelope:
        return self._request("GET", f"/api/v1/graph/build-runs/{run_id}")

    def graph(self, position_id: str) -> UpstreamEnvelope:
        return self._request("GET", f"/api/v1/positions/{position_id}/graph")

    def position_profile(
        self, position_id: str, *, graph_version_id: int | None = None
    ) -> UpstreamEnvelope:
        params = {
            "contract_version": "position-profile.v3",
            "view": "published",
        }
        if graph_version_id is not None:
            params["graph_version_id"] = graph_version_id
        return self._request(
            "GET", f"/api/v1/position-profiles/{position_id}", params=params
        )

    def position_profiles_batch(
        self,
        position_ids: list[str],
        *,
        graph_version_ids: dict[str, int] | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> UpstreamEnvelope:
        return self._request(
            "POST",
            "/api/v1/position-profiles/batch",
            json={
                "position_ids": position_ids,
                "contract_version": "position-profile.v3",
                "graph_version_ids": graph_version_ids or {},
                "view": "published",
                "draft_ids": {},
                "page": page,
                "page_size": page_size,
            },
        )

    def skill_relations_batch(self, skill_ids: tuple[str, ...]) -> UpstreamEnvelope:
        return self._request(
            "POST",
            "/api/v1/skill-relations/batch",
            json={"skill_ids": list(skill_ids)},
        )

    def register_dependency_reference(
        self,
        *,
        consumer_system: str,
        reference_type: str,
        reference_id: str,
        graph_version_id: int,
        metadata: dict | None = None,
    ) -> UpstreamEnvelope:
        return self._request(
            "POST",
            "/api/v1/dependency-references",
            json={
                "consumer_system": consumer_system,
                "reference_type": reference_type,
                "reference_id": reference_id,
                "graph_version_id": graph_version_id,
                "metadata": metadata or {},
            },
        )

    def versions(self, position_id: str) -> UpstreamEnvelope:
        return self._request("GET", f"/api/v1/positions/{position_id}/graph/versions")

    def relation_evidence(self, relation_id: str) -> UpstreamEnvelope:
        return self._request("GET", f"/api/v1/relations/{relation_id}/evidence")

    def portal_call(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        params: dict | None = None,
        actor_id: str | None = None,
        actor_role: str | None = None,
    ) -> UpstreamEnvelope:
        return self._request(
            method,
            path,
            json=payload,
            params=params,
            actor_id=actor_id,
            actor_role=actor_role,
        )
