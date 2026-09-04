"""Live local HTTP protocol tests for production upstream adapters."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.auth import AuthContext, derive_access_scope
from app.infrastructure.http_sources import HttpCVProfileSource
from app.infrastructure.relation_sources import HttpSkillRelationSource
from app.infrastructure.resource_authorization import (
    HttpApplicationGrantAdapter,
    HttpCVAuthorizationAdapter,
    HttpEnterpriseJobGrantAdapter,
)
from app.ports.upstream_contracts import UpstreamResponseError


class _ContractHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if status == 429:
            self.send_header("Retry-After", "0")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == "Bearer service-credential-opaque"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        server = self.server
        if server.unavailable:  # type: ignore[attr-defined]
            self._send(503, {"code": "unavailable"})
            return
        if self.path == "/health":
            self._send(200, {"status": "ready", "version": "contracts.v1"})
            return
        if not self._authorized():
            self._send(401, {"code": "unauthorized"})
            return
        if self.path == "/contracts/cv/cv-opaque":
            server.profile_calls += 1  # type: ignore[attr-defined]
            if server.profile_calls == 1:  # type: ignore[attr-defined]
                self._send(503, {"code": "retry"})
            else:
                self._send(200, {"contract_version": "probe.v1", "id": "cv-opaque"})
            return
        self._send(404, {"code": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        server = self.server
        if server.unavailable:  # type: ignore[attr-defined]
            self._send(503, {"code": "unavailable"})
            return
        if not self._authorized():
            self._send(401, {"code": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/graph":
            server.graph_calls += 1  # type: ignore[attr-defined]
            if server.graph_calls == 1:  # type: ignore[attr-defined]
                self._send(429, {"code": "rate_limited"})
                return
            graph_version = (
                "graph.wrong.v1"
                if server.bad_graph_version  # type: ignore[attr-defined]
                else "graph.external.v1"
            )
            relation = {
                "relation_id": "relation-opaque",
                "source_skill_id": "skill-python",
                "target_skill_id": "skill-fastapi",
                "relation_type": "related",
                "source_system": "graph-contract-service",
                "graph_version": graph_version,
                "confidence": 0.9,
                "evidence_refs": [],
            }
            relations = [relation]
            if server.duplicate_graph_identity:  # type: ignore[attr-defined]
                relations.append(dict(relation))
            self._send(
                200,
                {
                    "data": {
                        "graph_version": graph_version,
                        "relations": relations,
                    }
                },
            )
            return
        if self.path == "/auth/cv":
            server.cv_auth_calls += 1  # type: ignore[attr-defined]
            if server.cv_auth_calls == 1:  # type: ignore[attr-defined]
                self._send(503, {"code": "retry"})
                return
            self._send(200, {"data": {"authorized": payload.get("cv_id") == "cv-opaque"}})
            return
        if self.path == "/auth/grant":
            self._send(
                200,
                {
                    "data": {
                        "authorized": payload.get("position_id") == "position-active"
                    }
                },
            )
            return
        if self.path == "/auth/enterprise-grant":
            self._send(
                200,
                {
                    "data": {
                        "authorized": (
                            payload.get("enterprise_job_id") == "job-active"
                        )
                    }
                },
            )
            return
        self._send(404, {"code": "not_found"})

    def log_message(self, format: str, *args: object) -> None:
        return


@contextmanager
def _contract_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ContractHandler)
    server.base_url = f"http://127.0.0.1:{server.server_port}"  # type: ignore[attr-defined]
    server.unavailable = False  # type: ignore[attr-defined]
    server.profile_calls = 0  # type: ignore[attr-defined]
    server.graph_calls = 0  # type: ignore[attr-defined]
    server.cv_auth_calls = 0  # type: ignore[attr-defined]
    server.bad_graph_version = False  # type: ignore[attr-defined]
    server.duplicate_graph_identity = False  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _context() -> AuthContext:
    roles = frozenset({"candidate"})
    return AuthContext(
        subject_id="candidate-opaque",
        tenant_id="tenant-opaque",
        roles=roles,
        access_scope=derive_access_scope("candidate-opaque", "tenant-opaque", roles),
        token_id="token-opaque",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )


def test_live_http_profile_graph_and_authorization_retry_contracts():
    with _contract_service() as server:
        common = {
            "service_token": "service-credential-opaque",
            "max_retries": 1,
            "retry_backoff_seconds": 0,
            "timeout_seconds": 1,
        }
        profile = HttpCVProfileSource(
            server.base_url,  # type: ignore[attr-defined]
            "/contracts/cv",
            health_url=f"{server.base_url}/health",  # type: ignore[attr-defined]
            **common,
        )
        profile.check_health()
        assert profile.fetch_cv_profile("cv-opaque")["id"] == "cv-opaque"

        graph = HttpSkillRelationSource(
            server.base_url,  # type: ignore[attr-defined]
            "/graph",
            expected_graph_version="graph.external.v1",
            health_url=f"{server.base_url}/health",  # type: ignore[attr-defined]
            **common,
        )
        graph.check_health()
        relations = graph.fetch_relations(("skill-python",))
        assert relations[0].target_skill_id == "skill-fastapi"
        server.bad_graph_version = True  # type: ignore[attr-defined]
        with pytest.raises(UpstreamResponseError, match="graph version"):
            graph.fetch_relations(("skill-python",))
        server.bad_graph_version = False  # type: ignore[attr-defined]
        server.duplicate_graph_identity = True  # type: ignore[attr-defined]
        with pytest.raises(UpstreamResponseError, match="duplicate"):
            graph.fetch_relations(("skill-python",))
        server.duplicate_graph_identity = False  # type: ignore[attr-defined]

        owner = HttpCVAuthorizationAdapter(
            f"{server.base_url}/auth/cv",  # type: ignore[attr-defined]
            health_url=f"{server.base_url}/health",  # type: ignore[attr-defined]
            **common,
        )
        grant = HttpApplicationGrantAdapter(
            f"{server.base_url}/auth/grant",  # type: ignore[attr-defined]
            health_url=f"{server.base_url}/health",  # type: ignore[attr-defined]
            **common,
        )
        enterprise_grant = HttpEnterpriseJobGrantAdapter(
            f"{server.base_url}/auth/enterprise-grant",  # type: ignore[attr-defined]
            health_url=f"{server.base_url}/health",  # type: ignore[attr-defined]
            **common,
        )
        owner.check_health()
        grant.check_health()
        enterprise_grant.check_health()
        assert owner.is_owner(_context(), "cv-opaque") is True
        assert grant.has_active_grant(_context(), "cv-opaque", "position-active") is True
        assert grant.has_active_grant(_context(), "cv-opaque", "position-revoked") is False
        assert enterprise_grant.has_active_grant(
            _context(), "cv-opaque", "enterprise_job:job-active"
        ) is True
        assert enterprise_grant.has_active_grant(
            _context(), "cv-opaque", "enterprise_job:job-revoked"
        ) is False
        assert enterprise_grant.has_active_grant(
            _context(), "cv-opaque", "job-active"
        ) is True

        server.unavailable = True  # type: ignore[attr-defined]
        with pytest.raises(UpstreamResponseError) as unavailable:
            profile.fetch_cv_profile("cv-opaque")
        assert "service-credential-opaque" not in str(unavailable.value)
        with pytest.raises(UpstreamResponseError):
            owner.is_owner(_context(), "cv-opaque")
        server.unavailable = False  # type: ignore[attr-defined]
        assert profile.fetch_cv_profile("cv-opaque")["id"] == "cv-opaque"
        assert owner.is_owner(_context(), "cv-opaque") is True
