"""Explicit competition-demo-v1 preflight, load, verify, and cleanup CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from jobgraph_contracts.demo_manifest import (  # noqa: E402
    COMPETITION_DEMO_V1,
    CompetitionDemoManifestV1,
)


MANIFEST_PATH = ROOT / "config" / COMPETITION_DEMO_V1 / "manifest.json"
DEFAULT_STATE_PATH = ROOT / "data" / COMPETITION_DEMO_V1 / "runtime-map.json"
RAG_TENANT_REF = "jobgraph-platform-public"
RAG_PERMISSION_SCOPE = "platform:public"
DEMO_MATCHING_RESULT_ALIAS = "matching-demo-001"
DEMO_POSITION_PROFILE_ALIAS = "position-profile-ai-app-engineer"
FOUNDATION_RESOURCE_TYPES = {
    "position_family",
    "source_jd",
    "source_cv",
}
PENDING_RESOURCE_TYPES = {
    "extracted_jd_bundle",
    "published_jd_fact",
    "graph_version",
    "position_profile",
    "cv_extraction_response",
    "validated_cv_snapshot",
    "cv_match_profile",
    "position_match_profile",
    "matching_evaluation",
    "trend_report",
    "discovery_snapshot",
    "rag_response",
}


class DemoCommandError(RuntimeError):
    def __init__(self, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def load_manifest(path: Path = MANIFEST_PATH) -> CompetitionDemoManifestV1:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        manifest = CompetitionDemoManifestV1.model_validate(payload)
    except Exception as exc:
        raise DemoCommandError("MANIFEST_INVALID", str(exc)) from exc
    if manifest.dataset_version != COMPETITION_DEMO_V1:
        raise DemoCommandError(
            "DATASET_VERSION_UNSUPPORTED",
            f"expected {COMPETITION_DEMO_V1}, got {manifest.dataset_version}",
        )
    return manifest


def load_inputs(
    manifest: CompetitionDemoManifestV1, manifest_path: Path = MANIFEST_PATH
) -> dict[str, dict[str, Any]]:
    inputs: dict[str, dict[str, Any]] = {}
    for item in (*manifest.jds, *manifest.cvs):
        path = (manifest_path.parent / item.input_path).resolve()
        if manifest_path.parent.resolve() not in path.parents:
            raise DemoCommandError(
                "INPUT_PATH_INVALID", f"{item.alias} escapes the dataset directory"
            )
        if not path.is_file():
            raise DemoCommandError("INPUT_NOT_FOUND", f"{item.alias}: {path}")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoCommandError(
                "INPUT_INVALID", f"{item.alias} is not a readable JSON input"
            ) from exc
        if payload.get("alias") != item.alias:
            raise DemoCommandError(
                "INPUT_ALIAS_MISMATCH", f"{path.name} does not describe {item.alias}"
            )
        inputs[item.alias] = payload
    return inputs


@dataclass(frozen=True)
class ServiceSpec:
    name: str
    base_url_env: str
    default_base_url: str
    readiness_path: str
    dependency_group: str


SERVICES = (
    ServiceSpec(
        "main",
        "MAIN_SYSTEM_BASE_URL",
        "http://127.0.0.1:8000",
        "/readiness",
        "required_for_foundation",
    ),
    ServiceSpec(
        "knowledge_graph",
        "KNOWLEDGE_GRAPH_PUBLIC_BASE_URL",
        "http://127.0.0.1:8003",
        "/readiness",
        "required_for_batch_2",
    ),
    ServiceSpec(
        "jd_extraction",
        "JD_EXTRACTION_PUBLIC_BASE_URL",
        "http://127.0.0.1:8005",
        "/readiness",
        "required_for_batch_2",
    ),
    ServiceSpec(
        "cv_extraction",
        "CV_EXTRACTION_PUBLIC_BASE_URL",
        "http://127.0.0.1:8006",
        "/readiness",
        "required_for_batch_2",
    ),
    ServiceSpec(
        "matching",
        "MATCHING_SERVICE_PUBLIC_BASE_URL",
        "http://127.0.0.1:8010",
        "/health/ready",
        "optional_later",
    ),
    ServiceSpec(
        "trend",
        "TREND_INTELLIGENCE_PUBLIC_BASE_URL",
        "http://127.0.0.1:8004",
        "/readiness",
        "optional_later",
    ),
    ServiceSpec(
        "discovery",
        "EMERGING_DISCOVERY_PUBLIC_BASE_URL",
        "http://127.0.0.1:8002",
        "/readiness",
        "optional_later",
    ),
    ServiceSpec(
        "embedding",
        "EMBEDDING_SERVICE_PUBLIC_BASE_URL",
        "http://127.0.0.1:8001",
        "/ready",
        "optional_later",
    ),
    ServiceSpec(
        "qdrant",
        "QDRANT_PUBLIC_BASE_URL",
        "http://127.0.0.1:6333",
        "/readyz",
        "optional_later",
    ),
)


class DemoGateway(Protocol):
    def preflight(self) -> dict[str, Any]: ...

    def ensure_position(self, manifest: CompetitionDemoManifestV1) -> dict[str, Any]: ...

    def ensure_source_jd(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def ensure_source_cv(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def run_jd_chain(self, alias: str, version_id: str) -> dict[str, Any]: ...

    def run_cv_chain(self, alias: str, cv_extraction_task_id: str) -> dict[str, Any]: ...

    def build_graph_version(
        self,
        *,
        position_id: str,
        window_start: str,
        window_end: str,
        version_name: str,
        version_number: int | None,
    ) -> dict[str, Any]: ...

    def run_matching(
        self, *, resume_id: str, target_id: str, graph_version: str
    ) -> dict[str, Any]: ...

    def fetch_jd_evidence(
        self,
        *,
        jd_id: str,
        business_object_id: str,
        graph_version: str,
        source_jd_id: str,
        source_jd_version_id: str,
        dataset_tag: str,
    ) -> list[dict[str, Any]]: ...

    def fetch_cv_evidence(
        self,
        *,
        snapshot_id: str,
        business_object_id: str,
        graph_version: str,
        source_cv_id: str,
        source_cv_version_id: str,
        dataset_tag: str,
    ) -> list[dict[str, Any]]: ...

    def index_rag(self, items: list[dict[str, Any]]) -> dict[str, Any]: ...

    def delete_rag(self, *, tenant_ref: str, permission_scope: str) -> dict[str, Any]: ...

    def query_rag(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def remove_position(self, position_id: str) -> dict[str, Any]: ...


class HttpDemoGateway:
    def __init__(self, timeout: float = 600.0) -> None:
        self.timeout = timeout
        self.main_url = os.getenv("MAIN_SYSTEM_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.kg_url = os.getenv("KNOWLEDGE_GRAPH_BASE_URL", "http://127.0.0.1:8003").rstrip("/")
        self._main_token: str | None = None
        self._main_personal_token: str | None = None

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        token: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise DemoCommandError(
                        "HTTP_RESPONSE_INVALID",
                        f"{method} {url} returned non-JSON content",
                    ) from exc
        except DemoCommandError:
            raise
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise DemoCommandError(
                "HTTP_REQUEST_FAILED",
                f"{method} {url} returned {exc.code}",
                raw,
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise DemoCommandError(
                "SERVICE_UNAVAILABLE", f"{method} {url}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise DemoCommandError("HTTP_RESPONSE_INVALID", f"{url} returned non-object JSON")
        return payload

    def _probe(self, url: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raise DemoCommandError(
                "HTTP_REQUEST_FAILED", f"GET {url} returned {exc.code}"
            ) from exc
        except (OSError, TimeoutError) as exc:
            raise DemoCommandError("SERVICE_UNAVAILABLE", f"GET {url}: {exc}") from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"status": "available", "body": raw.strip()}
        return payload if isinstance(payload, dict) else {"status": "available"}

    @staticmethod
    def _data(payload: dict[str, Any]) -> Any:
        if payload.get("code") not in (0, 200, None):
            raise DemoCommandError(
                "UPSTREAM_REJECTED", str(payload.get("message") or "upstream rejected request"), payload
            )
        return payload.get("data", payload)

    def _main_auth(self) -> str:
        if self._main_token:
            return self._main_token
        username = os.getenv("COMPETITION_DEMO_MAIN_USERNAME")
        password = os.getenv("COMPETITION_DEMO_MAIN_PASSWORD")
        if not username or not password:
            raise DemoCommandError(
                "MAIN_CREDENTIALS_MISSING",
                "COMPETITION_DEMO_MAIN_USERNAME and COMPETITION_DEMO_MAIN_PASSWORD are required",
            )
        payload = self._request(
            "POST",
            f"{self.main_url}/api/v1/auth/login",
            body={"username": username, "password": password},
        )
        data = self._data(payload)
        self._main_token = str(data["access_token"])
        return self._main_token

    def _main_personal_auth(self) -> str:
        if self._main_personal_token:
            return self._main_personal_token
        username = os.getenv("COMPETITION_DEMO_MAIN_PERSONAL_USERNAME")
        password = os.getenv("COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD")
        if not username or not password:
            raise DemoCommandError(
                "MAIN_PERSONAL_CREDENTIALS_MISSING",
                "COMPETITION_DEMO_MAIN_PERSONAL_USERNAME and "
                "COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD are required",
            )
        payload = self._request(
            "POST",
            f"{self.main_url}/api/v1/auth/login",
            body={"username": username, "password": password},
        )
        data = self._data(payload)
        self._main_personal_token = str(data["access_token"])
        return self._main_personal_token

    def preflight(self) -> dict[str, Any]:
        checks: dict[str, dict[str, Any]] = {
            "required_for_foundation": {},
            "required_for_batch_2": {},
            "optional_later": {},
        }
        for spec in SERVICES:
            base_url = os.getenv(spec.base_url_env, spec.default_base_url).rstrip("/")
            try:
                payload = self._probe(f"{base_url}{spec.readiness_path}")
                checks[spec.dependency_group][spec.name] = {
                    "status": "available",
                    "url": base_url,
                    "response": payload,
                }
            except DemoCommandError as exc:
                checks[spec.dependency_group][spec.name] = {
                    "status": "unavailable",
                    "url": base_url,
                    "error_code": exc.code,
                }
        configuration_names = {
            "required_for_foundation": (
                "COMPETITION_DEMO_MAIN_USERNAME",
                "COMPETITION_DEMO_MAIN_PASSWORD",
                "COMPETITION_DEMO_MAIN_PERSONAL_USERNAME",
                "COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD",
            ),
            "required_for_batch_2": (
                "DEEPSEEK_API_KEY",
                "DEEPSEEK_BASE_URL",
                "CV_EXTRACTION_PROVIDER",
                "CV_EXTRACTION_MODEL",
                "JD_EXTRACTION_INTERNAL_TOKEN",
                "CV_EXTRACTION_INTERNAL_TOKEN",
                "KNOWLEDGE_GRAPH_SERVICE_USERNAME",
                "KNOWLEDGE_GRAPH_SERVICE_PASSWORD",
            ),
            "optional_later": (),
        }
        configuration = {
            group: {
                name: ("present" if os.getenv(name) else "missing")
                for name in names
            }
            for group, names in configuration_names.items()
        }
        missing_foundation_services = [
            name
            for name, check in checks["required_for_foundation"].items()
            if check["status"] != "available"
        ]
        missing_foundation_configuration = [
            name
            for name, status in configuration["required_for_foundation"].items()
            if status == "missing"
        ]
        result = {
            "runtime_mode": os.getenv("ENVIRONMENT", "development"),
            "compose_profiles": [
                item.strip()
                for item in os.getenv("COMPOSE_PROFILES", "").split(",")
                if item.strip()
            ],
            "service_groups": checks,
            "configuration_groups": configuration,
            "missing_required_for_foundation": {
                "services": missing_foundation_services,
                "configuration": missing_foundation_configuration,
            },
            "pending_required_for_batch_2": {
                "services": [
                    name
                    for name, check in checks["required_for_batch_2"].items()
                    if check["status"] != "available"
                ],
                "configuration": [
                    name
                    for name, status in configuration["required_for_batch_2"].items()
                    if status == "missing"
                ],
            },
        }
        if missing_foundation_services or missing_foundation_configuration:
            raise DemoCommandError(
                "PREFLIGHT_REQUIRED_DEPENDENCY_MISSING",
                "foundation service or configuration is unavailable",
                result,
            )
        return result

    def ensure_position(self, manifest: CompetitionDemoManifestV1) -> dict[str, Any]:
        token = self._main_auth()
        items = self._data(self._request("GET", f"{self.main_url}/api/v1/positions", token=token))
        position_code = manifest.position_family.position_alias
        for item in items:
            if item.get("position_code") == position_code:
                return {"business_id": item["position_id"], "created": False}
        body = {
            "position_code": position_code,
            "position_name": manifest.position_family.family_name,
            "taxonomy_family_code": manifest.position_family.alias,
            "taxonomy_family_name": manifest.position_family.family_name,
            "status": "existing",
        }
        created = self._data(
            self._request("POST", f"{self.main_url}/api/v1/positions", body=body, token=token)
        )
        return {"business_id": created["position_id"], "created": True}

    def ensure_source_jd(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._main_auth()
        raw_text = str(payload["raw_text"])
        body = {
            "schema_version": "crawler-jd-v1",
            "source_record_id": payload["source_record_id"],
            "source_platform": payload["source_type"],
            "job_title_raw": payload["job_title"],
            "company_name_raw": payload["company_name"],
            "region_raw": payload["region"],
            "publish_time_raw": payload["publish_time"],
            "crawl_time": datetime.now(UTC).isoformat(),
            "raw_text": raw_text,
            "raw_payload": {
                "dataset_version": COMPETITION_DEMO_V1,
                "demo_only": True,
                "alias": alias,
            },
            "text_canonicalization_version": "identity.v1",
            "source_version": payload.get("input_version", "competition-demo-input.v1"),
        }
        data = self._data(
            self._request("POST", f"{self.main_url}/api/v1/source-jds/import", body=body, token=token)
        )
        return {
            "business_id": data["source_jd_id"],
            "version_id": data["source_jd_version_id"],
            "created": bool(data["created_source"] or data["created_version"]),
        }

    def ensure_source_cv(self, alias: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._main_personal_auth()
        data = self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/internal/source-cvs/import-and-extract",
                body={
                    "source_record_id": payload["source_record_id"],
                    "raw_text": payload["raw_text"],
                    "source_platform": "competition_demo",
                },
                token=token,
            )
        )
        return {
            "business_id": data["source_cv_id"],
            "version_id": data["source_cv_version_id"],
            "created": bool(data.get("created_source") or data.get("created_version")),
            "status": data.get("task_status"),
            "cv_extraction_task_id": data.get("cv_extraction_task_id"),
        }

    def run_jd_chain(self, alias: str, version_id: str) -> dict[str, Any]:
        """Run the real JD extraction -> review -> publication -> KG sync chain."""
        token = self._main_auth()
        task = self._ensure_jd_extraction_task(alias, version_id, token)
        task_id = str(task["id"])
        if task.get("status") == "failed":
            self._data(
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/extraction-tasks/{task_id}/retry",
                    body={},
                    token=token,
                )
            )
        run = self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/extraction-tasks/{task_id}/run",
                body={},
                token=token,
            )
        )
        if run.get("status") != "succeeded":
            raise DemoCommandError(
                "EXTRACTION_TASK_FAILED",
                f"{alias} extraction task did not succeed",
                {
                    "task_id": task_id,
                    "status": run.get("status"),
                    "error_code": run.get("last_error_code"),
                    "error_message": run.get("last_error_message"),
                },
            )
        draft = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                draft = self._data(
                    self._request(
                        "POST",
                        f"{self.main_url}/api/v1/extraction-tasks/{task_id}/import-draft",
                        body={},
                        token=token,
                    )
                )
                break
            except DemoCommandError as exc:
                if (
                    exc.code != "HTTP_REQUEST_FAILED"
                    or "409" not in str(exc)
                    or "validation_pending" not in str(exc.details or "")
                ):
                    raise
                time.sleep(5)
        if draft is None:
            raise DemoCommandError(
                "VALIDATION_TIMEOUT",
                f"{alias} data validation did not complete in time",
                None,
            )
        parse_result_id = str(draft["parse_result_id"])
        review_task_id = draft.get("review_task_id")
        if review_task_id:
            self._approve_review_task(str(review_task_id), token)
        publication = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            self._approve_validation_reviews(token)
            try:
                publication = self._data(
                    self._request(
                        "POST",
                        f"{self.main_url}/api/v1/jd-parse-results/{parse_result_id}/publish",
                        body={},
                        token=token,
                    )
                )
                break
            except DemoCommandError as exc:
                if (
                    exc.code != "HTTP_REQUEST_FAILED"
                    or "409" not in str(exc)
                    or "validation_review_pending" not in str(exc.details or "")
                ):
                    raise
                time.sleep(5)
        if publication is None:
            raise DemoCommandError(
                "VALIDATION_REVIEW_TIMEOUT",
                f"{alias} validation review did not complete in time",
                None,
            )
        document_id = str(
            publication.get("document_id")
            or publication.get("jd_id")
            or draft.get("jd_id")
            or ""
        )
        sync = {}
        if document_id:
            sync = self._data(
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/integrations/knowledge-graph/jds/{document_id}/sync",
                    body={},
                    token=token,
                )
            )
        return {
            "alias": alias,
            "extraction_task_id": task_id,
            "parse_result_id": parse_result_id,
            "jd_id": str(draft.get("jd_id") or publication.get("jd_id") or ""),
            "document_id": document_id,
            "publication_id": str(
                publication.get("publication_id")
                or publication.get("published_fact_id")
                or ""
            ),
            "provider": run.get("provider"),
            "execution_mode": run.get("extraction_mode") or "llm",
            "kg_sync_status": sync.get("sync_status"),
            "knowledge_graph_id": sync.get("knowledge_graph_id"),
        }

    def run_cv_chain(self, alias: str, cv_extraction_task_id: str) -> dict[str, Any]:
        """Run the real CV extraction -> review -> confirmed snapshot chain."""
        token = self._main_personal_auth()
        run = None
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            try:
                run = self._data(
                    self._request(
                        "POST",
                        f"{self.main_url}/api/v1/cv-extraction-tasks/{cv_extraction_task_id}/run",
                        body={},
                        token=token,
                    )
                )
                break
            except DemoCommandError as exc:
                if exc.code != "HTTP_REQUEST_FAILED" or "409" not in str(exc):
                    raise
                status = self._data(
                    self._request(
                        "GET",
                        f"{self.main_url}/api/v1/cv-extraction-tasks/{cv_extraction_task_id}",
                        token=token,
                    )
                )
                if status.get("status") == "succeeded":
                    run = status
                    break
                if status.get("status") == "failed":
                    raise DemoCommandError(
                        "CV_EXTRACTION_TASK_FAILED",
                        f"{alias} cv extraction task did not succeed",
                        {
                            "task_id": cv_extraction_task_id,
                            "status": status.get("status"),
                            "error_code": status.get("last_error_code"),
                            "error_message": status.get("last_error_message"),
                        },
                    )
                time.sleep(5)
        if run is None:
            raise DemoCommandError(
                "CV_EXTRACTION_TASK_TIMEOUT",
                f"{alias} cv extraction did not complete in time",
                None,
            )
        if run.get("status") != "succeeded":
            raise DemoCommandError(
                "CV_EXTRACTION_TASK_FAILED",
                f"{alias} cv extraction task did not succeed",
                {
                    "task_id": cv_extraction_task_id,
                    "status": run.get("status"),
                    "error_code": run.get("last_error_code"),
                    "error_message": run.get("last_error_message"),
                },
            )
        review = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/cv-extraction-tasks/{cv_extraction_task_id}/review",
                token=token,
            )
        )
        review_id = review.get("review_id")
        if not review_id:
            raise DemoCommandError(
                "CV_REVIEW_MISSING",
                f"{alias} cv extraction task has no review context",
                {"task_id": cv_extraction_task_id},
            )
        try:
            confirmed = self._data(
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/cv-extraction-tasks/{cv_extraction_task_id}/confirm",
                    body={
                        "expected_review_id": str(review_id),
                        "idempotency_key": f"competition-demo-v1:{alias}",
                    },
                    token=token,
                )
            )
        except DemoCommandError as exc:
            if "409" not in str(exc):
                raise
            task = self._data(
                self._request(
                    "GET",
                    f"{self.main_url}/api/v1/cv-extraction-tasks/{cv_extraction_task_id}",
                    token=token,
                )
            )
            snapshot_id = task.get("latest_validated_cv_snapshot_id")
            if not snapshot_id:
                raise
            confirmed = {
                "snapshot_id": snapshot_id,
                "resume_id": task.get("resume_id"),
            }
        return {
            "alias": alias,
            "cv_extraction_task_id": cv_extraction_task_id,
            "validated_cv_snapshot_id": str(confirmed["snapshot_id"]),
            "resume_id": str(confirmed["resume_id"]),
            "provider": run.get("provider"),
        }

    def build_graph_version(
        self,
        *,
        position_id: str,
        window_start: str,
        window_end: str,
        version_name: str,
        version_number: int | None,
    ) -> dict[str, Any]:
        """Build, review, and publish one GraphVersion through the main portal."""
        token = self._main_auth()
        existing = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/portal/admin/knowledge-graph/positions/{position_id}/versions",
                token=token,
            )
        )
        for version in existing if isinstance(existing, list) else []:
            if not isinstance(version, dict):
                continue
            if version.get("version_name") == version_name:
                version_id = version.get("id") or version.get("version_id")
                if isinstance(version_id, int):
                    return {
                        "graph_version_id": version_id,
                        "position_id": self.resolve_kg_position_id(position_id),
                        "version_name": version_name,
                        "reused": True,
                    }
        built = self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/integrations/knowledge-graph/positions/{position_id}/build",
                body={
                    "window_start": window_start,
                    "window_end": window_end,
                    "minimum_effective_weight": 0.05,
                    "minimum_valid_samples": 1,
                },
                token=token,
            )
        )
        run_payload = built.get("build_run") if isinstance(built.get("build_run"), dict) else built
        build_run_id = run_payload.get("build_run_id")
        if build_run_id is None:
            job_id = run_payload.get("job_id")
            if not isinstance(job_id, int):
                raise DemoCommandError(
                    "KG_BUILD_RESPONSE_INVALID",
                    "knowledge graph build did not return a build_run_id",
                    built,
                )
            build_run_id = self._poll_kg_build_job(job_id, position_id, token)
        build_run_id = int(build_run_id)
        kg_position_id = str(built.get("knowledge_graph_position_id") or position_id)
        published = None
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            reviews = self._data(
                self._request(
                    "GET",
                    f"{self.main_url}/api/v1/portal/admin/knowledge-graph/review-tasks?page_size=100",
                    token=token,
                )
            )
            self._approve_kg_reviews(build_run_id, reviews, token)
            try:
                published = self._data(
                    self._request(
                        "POST",
                        f"{self.main_url}/api/v1/portal/admin/knowledge-graph/build-runs/{build_run_id}/publish",
                        body={
                            "reason": "competition-demo-v1 window publication",
                            "version_name": version_name,
                            "version_number": version_number,
                            "release_notes": (
                                "Real published JD facts for the competition-demo-v1 window."
                            ),
                        },
                        token=token,
                    )
                )
                break
            except DemoCommandError as exc:
                if (
                    exc.code != "HTTP_REQUEST_FAILED"
                    or "409" not in str(exc)
                    or "validation_review_pending" not in str(exc.details or "")
                ):
                    raise
                time.sleep(5)
        if published is None:
            raise DemoCommandError(
                "KG_PUBLISH_REVIEW_TIMEOUT",
                "knowledge graph publish review did not complete in time",
                None,
            )
        graph_version_id = (
            published.get("graph_version_id")
            or published.get("id")
            or published.get("version_id")
        )
        if graph_version_id is None:
            raise DemoCommandError(
                "KG_PUBLISH_RESPONSE_INVALID",
                "knowledge graph publish did not return a graph_version_id",
                published,
            )
        return {
            "graph_version_id": int(graph_version_id),
            "build_run_id": build_run_id,
            "position_id": kg_position_id,
            "version_name": version_name,
            "version_number": version_number,
        }

    def resolve_kg_position_id(self, position_id: str) -> str:
        token = self._main_auth()
        mappings = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/portal/admin/knowledge-graph/mappings"
                f"?entity_type=position&query={position_id}",
                token=token,
            )
        )
        for item in mappings if isinstance(mappings, list) else []:
            if isinstance(item, dict) and item.get("knowledge_graph_id"):
                return str(item["knowledge_graph_id"])
        return position_id

    def run_matching(
        self, *, resume_id: str, target_id: str, graph_version: str
    ) -> dict[str, Any]:
        """Create the formal matching evaluation for the demo CV and position."""
        token = self._main_personal_auth()
        task = self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/matches/tasks",
                body={
                    "resume_id": resume_id,
                    "target_type": "standard_position",
                    "target_id": target_id,
                    "generate_learning_path": False,
                },
                token=token,
                extra_headers={
                    "Idempotency-Key": f"competition-demo-v1:{resume_id}:{target_id}",
                },
            )
        )
        if task.get("status") not in {"succeeded", "completed"}:
            raise DemoCommandError(
                "MATCHING_TASK_FAILED",
                "matching evaluation did not complete",
                {
                    "task_id": task.get("task_id"),
                    "status": task.get("status"),
                    "error_code": task.get("error_code"),
                    "error_message": task.get("error_message"),
                },
            )
        versions = task.get("versions") if isinstance(task.get("versions"), dict) else {}
        return {
            "task_id": task.get("task_id"),
            "evaluation_id": task.get("evaluation_id"),
            "cv_profile_version": versions.get(
                "cv_source_version", versions.get("cv_source")
            ),
            "position_profile_version": versions.get(
                "position_source_version", versions.get("position_source")
            ),
            "graph_version": graph_version,
        }

    def fetch_jd_evidence(
        self,
        *,
        jd_id: str,
        business_object_id: str,
        graph_version: str,
        source_jd_id: str,
        source_jd_version_id: str,
        dataset_tag: str,
    ) -> list[dict[str, Any]]:
        token = self._main_auth()
        data = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/jds/{jd_id}/parse-result",
                token=token,
            )
        )
        extraction = data.get("extraction_result")
        if not isinstance(extraction, dict):
            raise DemoCommandError(
                "JD_EVIDENCE_MISSING",
                f"{jd_id} has no extraction result to index",
                {"jd_id": jd_id},
            )
        return self._evidence_items(
            extraction,
            business_object_type="standard_position",
            business_object_id=business_object_id,
            evidence_type="jd_evidence",
            source_object_type="source_jd",
            source_object_id=source_jd_id,
            source_document_id=jd_id,
            source_version=source_jd_version_id,
            graph_version=graph_version,
            dataset_tag=dataset_tag,
        )

    def fetch_cv_evidence(
        self,
        *,
        snapshot_id: str,
        business_object_id: str,
        graph_version: str,
        source_cv_id: str,
        source_cv_version_id: str,
        dataset_tag: str,
    ) -> list[dict[str, Any]]:
        token = self._main_personal_auth()
        data = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/validated-cv-snapshots/{snapshot_id}",
                token=token,
            )
        )
        extraction = data.get("extraction_payload")
        if not isinstance(extraction, dict):
            raise DemoCommandError(
                "CV_EVIDENCE_MISSING",
                f"{snapshot_id} has no extraction payload to index",
                {"snapshot_id": snapshot_id},
            )
        return self._evidence_items(
            extraction,
            business_object_type="cv_profile",
            business_object_id=business_object_id,
            evidence_type="cv_evidence",
            source_object_type="source_cv",
            source_object_id=source_cv_id,
            source_document_id=source_cv_id,
            source_version=source_cv_version_id,
            graph_version=graph_version,
            dataset_tag=dataset_tag,
        )

    def index_rag(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        token = self._main_auth()
        data = self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/rag/evidence/index",
                body={"items": items},
                token=token,
            )
        )
        return {
            "contract_version": data.get("contract_version"),
            "indexed_count": int(data.get("indexed_count", len(items))),
        }

    def delete_rag(self, *, tenant_ref: str, permission_scope: str) -> dict[str, Any]:
        token = self._main_auth()
        return self._data(
            self._request(
                "DELETE",
                f"{self.main_url}/api/v1/rag/evidence",
                body={
                    "tenant_ref": tenant_ref,
                    "permission_scope": permission_scope,
                },
                token=token,
            )
        )

    def query_rag(self, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._main_auth()
        return self._data(
            self._request(
                "POST",
                f"{self.main_url}/api/v1/rag/evidence",
                body=payload,
                token=token,
            )
        )

    def remove_position(self, position_id: str) -> dict[str, Any]:
        token = self._main_auth()
        data = self._data(
            self._request("DELETE", f"{self.main_url}/api/v1/positions/{position_id}", token=token)
        )
        return dict(data)

    def _ensure_jd_extraction_task(
        self, alias: str, version_id: str, token: str
    ) -> dict[str, Any]:
        try:
            created = self._data(
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/source-jd-versions/{version_id}/extraction-tasks"
                    "?extraction_mode=llm",
                    body={},
                    token=token,
                )
            )
            return created
        except DemoCommandError as exc:
            if exc.code != "HTTP_REQUEST_FAILED" or "409" not in str(exc):
                raise
        page = self._data(
            self._request(
                "GET",
                f"{self.main_url}/api/v1/extraction-tasks?source_jd_version_id={version_id}",
                token=token,
            )
        )
        items = page.get("items") if isinstance(page, dict) else []
        for item in items or []:
            if isinstance(item, dict) and item.get("extraction_mode") == "llm":
                return item
        raise DemoCommandError(
            "EXTRACTION_TASK_MISSING",
            f"{alias} has no reusable llm extraction task",
            {"version_id": version_id},
        )

    def _approve_review_task(self, review_task_id: str, token: str) -> None:
        try:
            self._request(
                "POST",
                f"{self.main_url}/api/v1/review-tasks/{review_task_id}/claim",
                body={},
                token=token,
            )
        except DemoCommandError as exc:
            if "409" not in str(exc) and "422" not in str(exc):
                raise
        try:
            self._request(
                "POST",
                f"{self.main_url}/api/v1/review-tasks/{review_task_id}/approve",
                body={"review_comment": "competition-demo-v1 approved"},
                token=token,
            )
        except DemoCommandError as exc:
            if "409" not in str(exc) and "422" not in str(exc):
                raise

    def _approve_validation_reviews(self, token: str) -> None:
        payload = self._request(
            "GET",
            f"{self.main_url}/api/v1/review-tasks"
            "?source_system=main-system"
            "&task_kind=data_validation_report"
            "&status=pending"
            "&page_size=100",
            token=token,
        )
        data = payload.get("data", payload)
        items = data if isinstance(data, list) else (data.get("items") or [])
        for item in items:
            if isinstance(item, dict):
                task_id = item.get("task_id") or item.get("id")
                if task_id:
                    self._approve_review_task(str(task_id), token)

    def _approve_kg_reviews(
        self, build_run_id: int, reviews: Any, token: str
    ) -> None:
        for review in reviews or []:
            if not isinstance(review, dict):
                continue
            if int(review.get("build_run_id") or 0) != build_run_id:
                continue
            task_id = review.get("task_id") or review.get("id")
            status = review.get("status")
            if status not in {"pending", "claimed", "approved"}:
                raise DemoCommandError(
                    "KG_REVIEW_NOT_ACTIONABLE",
                    "knowledge graph review task is not actionable",
                    {"task_id": task_id, "status": status},
                )
            if status in {"pending", "claimed"}:
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/portal/admin/knowledge-graph/"
                    f"review-tasks/{task_id}/claim",
                    body={"reason": "competition-demo-v1 graph review"},
                    token=token,
                )
                self._request(
                    "POST",
                    f"{self.main_url}/api/v1/portal/admin/knowledge-graph/"
                    f"review-tasks/{task_id}/approve",
                    body={"reason": "competition-demo-v1 graph review"},
                    token=token,
                )

    def _poll_kg_build_job(self, job_id: int, position_id: str, token: str) -> int:
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            data = self._data(
                self._request(
                    "GET",
                    f"{self.main_url}/api/v1/portal/admin/knowledge-graph/build-jobs/{job_id}",
                    token=token,
                )
            )
            status = str(data.get("status") or "")
            if status == "failed":
                raise DemoCommandError(
                    "KG_BUILD_JOB_FAILED",
                    f"knowledge graph build job failed: {data.get('error')}",
                    data,
                )
            run_id = data.get("build_run_id")
            if status == "succeeded" and isinstance(run_id, int):
                return run_id
            time.sleep(5)
        raise DemoCommandError(
            "KG_BUILD_JOB_TIMEOUT",
            f"knowledge graph build job {job_id} timed out",
            {"position_id": position_id},
        )

    @staticmethod
    def _evidence_items(
        payload: Any,
        *,
        business_object_type: str,
        business_object_id: str,
        evidence_type: str,
        source_object_type: str,
        source_object_id: str,
        source_document_id: str,
        source_version: str,
        graph_version: str,
        dataset_tag: str,
    ) -> list[dict[str, Any]]:
        raw: list[dict[str, Any]] = []

        def visit(current: Any) -> None:
            if isinstance(current, dict):
                for key, child in current.items():
                    if key == "evidence":
                        if isinstance(child, dict):
                            raw.append(dict(child))
                        elif isinstance(child, list):
                            raw.extend(
                                dict(item)
                                for item in child
                                if isinstance(item, dict)
                            )
                    else:
                        visit(child)
            elif isinstance(current, list):
                for child in current:
                    visit(child)

        visit(payload)
        items: list[dict[str, Any]] = []
        for index, evidence in enumerate(raw):
            quote = str(evidence.get("quote") or "").strip()
            if not quote:
                continue
            start = (
                evidence.get("start")
                if isinstance(evidence.get("start"), int)
                else 0
            )
            end = (
                evidence.get("end")
                if isinstance(evidence.get("end"), int)
                else len(quote)
            )
            items.append(
                {
                    "evidence_id": (
                        f"{dataset_tag}:{evidence_type}:{source_object_id}:{index}"
                    ),
                    "business_object_type": business_object_type,
                    "business_object_id": business_object_id,
                    "evidence_type": evidence_type,
                    "source_object_type": source_object_type,
                    "source_object_id": source_object_id,
                    "source_document_id": source_document_id,
                    "source_version": source_version,
                    "text": quote,
                    "quote": quote,
                    "location_start": start,
                    "location_end": end,
                    "occurrence_index": int(
                        evidence.get("occurrence_index", 0) or 0
                    ),
                    "alignment": str(
                        evidence.get("alignment") or "unresolved"
                    ),
                    "graph_version": graph_version,
                    "tenant_ref": RAG_TENANT_REF,
                    "permission_scope": RAG_PERMISSION_SCOPE,
                }
            )
        return items


class CompetitionDemoLoader:
    def __init__(
        self,
        gateway: DemoGateway,
        *,
        manifest_path: Path = MANIFEST_PATH,
        state_path: Path = DEFAULT_STATE_PATH,
    ) -> None:
        self.gateway = gateway
        self.manifest_path = manifest_path
        self.state_path = state_path

    def preflight(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        inputs = load_inputs(manifest, self.manifest_path)
        service_result = self.gateway.preflight()
        return {
            "phase": "foundation",
            "status": "foundation_ready",
            "dataset_version": manifest.dataset_version,
            "contract_version": manifest.contract_version,
            "implementation_status": manifest.implementation_status,
            "input_count": len(inputs),
            "capabilities": {
                "foundation_loader": "implemented",
                "real_jd_cv_llm": "pending_batch_2",
                "semantic_shadow": "pending_batch_3",
                "evidence_rag": "bff_and_retrieval_implemented_pending_real_run",
            },
            **service_result,
        }

    def load(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        inputs = load_inputs(manifest, self.manifest_path)
        self.gateway.preflight()
        previous = self._read_state(required=False)
        state = (
            previous
            if previous is not None
            and previous.get("phase") == "foundation"
            and previous.get("status") != "removed"
            else self._new_state(manifest, inputs)
        )
        try:
            position = self.gateway.ensure_position(manifest)
            self._record(state, manifest.position_family.alias, "position_family", position)
            expected_family_aliases = [
                item.alias
                for item in manifest.expected_resources
                if item.resource_type == "position_family"
            ]
            for alias in expected_family_aliases:
                self._record(
                    state,
                    alias,
                    "position_family",
                    {"business_id": position["business_id"], "created": False},
                )
            self._record(
                state,
                manifest.position_family.position_alias,
                "standard_position",
                {"business_id": position["business_id"], "created": position["created"]},
            )
            for item in manifest.jds:
                result = self.gateway.ensure_source_jd(item.alias, inputs[item.alias])
                resource_alias = self._relation_target(manifest, item.alias, "maps_to_source_jd")
                self._record(state, item.alias, "jd_input", {"business_id": item.alias, "created": False})
                self._record(state, resource_alias, "source_jd", result)
            cv = manifest.cvs[0]
            cv_result = self.gateway.ensure_source_cv(cv.alias, inputs[cv.alias])
            cv_resource_alias = self._relation_target(manifest, cv.alias, "maps_to_source_cv")
            self._record(state, cv.alias, "cv_input", {"business_id": cv.alias, "created": False})
            self._record(state, cv_resource_alias, "source_cv", cv_result)
            for window in manifest.trend_windows:
                self._record(
                    state,
                    window.alias,
                    "trend_window",
                    {
                        "business_id": window.alias,
                        "created": False,
                        "start": window.start.isoformat(),
                        "end": window.end.isoformat(),
                        "graph_version_alias": window.graph_version_alias,
                    },
                )
            state["pending_resources"] = self._pending_resources(manifest)
            state["phase"] = "foundation"
            state["status"] = "foundation_loaded"
            state["updated_at"] = datetime.now(UTC).isoformat()
            self._write_state(state)
        except DemoCommandError as exc:
            state["status"] = "partial_failure"
            state["last_error"] = {"code": exc.code, "message": str(exc), "details": exc.details}
            state["updated_at"] = datetime.now(UTC).isoformat()
            self._write_state(state)
            raise
        return self.verify()

    def run_batch2(self) -> dict[str, Any]:
        """Run the real JD/CV -> KG -> Matching chain and record batch_2_results."""
        manifest = load_manifest(self.manifest_path)
        state = self._read_state(required=True)
        if state.get("status") == "removed":
            raise DemoCommandError(
                "DATASET_NOT_LOADED",
                f"{COMPETITION_DEMO_V1} was removed and must be loaded again",
            )
        self.gateway.preflight()
        resources = state.get("resources", {})
        results = dict(state.get("batch_2_results") or {})
        try:
            for item in manifest.jds:
                if item.alias in results:
                    continue
                source = resources[
                    self._relation_target(manifest, item.alias, "maps_to_source_jd")
                ]
                results[item.alias] = self.gateway.run_jd_chain(
                    item.alias, str(source["version_id"])
                )
            cv = manifest.cvs[0]
            if cv.alias not in results:
                source = resources[
                    self._relation_target(manifest, cv.alias, "maps_to_source_cv")
                ]
                results[cv.alias] = self.gateway.run_cv_chain(
                    cv.alias, str(source["cv_extraction_task_id"])
                )
            position_id = str(
                resources[manifest.position_family.position_alias]["business_id"]
            )
            for graph in manifest.graph_versions:
                if graph.alias in results:
                    continue
                window = self._window_for_graph(manifest, graph.alias)
                results[graph.alias] = self.gateway.build_graph_version(
                    position_id=position_id,
                    window_start=window.start.isoformat(),
                    window_end=window.end.isoformat(),
                    version_name=graph.version_name,
                    version_number=graph.version_number,
                )
            if DEMO_POSITION_PROFILE_ALIAS not in results:
                latest_graph = results.get(manifest.published_position.graph_version_alias) or {}
                results[DEMO_POSITION_PROFILE_ALIAS] = {
                    "position_id": str(
                        latest_graph.get("position_id") or position_id
                    ),
                    "graph_version_id": latest_graph.get("graph_version_id"),
                }
            matching_result = results.get(DEMO_MATCHING_RESULT_ALIAS)
            if DEMO_MATCHING_RESULT_ALIAS not in results or not (
                matching_result.get("cv_profile_version")
                and matching_result.get("position_profile_version")
            ):
                cv_result = results[cv.alias]
                results[DEMO_MATCHING_RESULT_ALIAS] = self.gateway.run_matching(
                    resume_id=str(cv_result["resume_id"]),
                    target_id=self.gateway.resolve_kg_position_id(position_id),
                    graph_version=manifest.published_position.graph_version_alias,
                )
            state["batch_2_results"] = results
            state["phase"] = "batch_2"
            state["status"] = "batch_2_loaded"
            state["updated_at"] = datetime.now(UTC).isoformat()
            self._write_state(state)
        except DemoCommandError as exc:
            state["batch_2_results"] = results
            state["phase"] = "batch_2"
            state["status"] = "batch_2_partial_failure"
            state["last_error"] = {
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
            }
            state["updated_at"] = datetime.now(UTC).isoformat()
            self._write_state(state)
            raise
        return self.verify()

    def index_rag(self) -> dict[str, Any]:
        """Index the demo's formal JD/CV Evidence into the Evidence RAG collection."""
        manifest = load_manifest(self.manifest_path)
        state = self._read_state(required=True)
        results = state.get("batch_2_results")
        if not isinstance(results, dict) or not results:
            raise DemoCommandError(
                "BATCH_2_REQUIRED",
                "run-batch2 must complete before Evidence RAG indexing",
            )
        resources = state.get("resources", {})
        position_id = str(
            resources[manifest.position_family.position_alias]["business_id"]
        )
        items: list[dict[str, Any]] = []
        for item in manifest.jds:
            jd_result = results.get(item.alias)
            if not isinstance(jd_result, dict):
                raise DemoCommandError(
                    "BATCH_2_REQUIRED",
                    f"{item.alias} has no recorded JD chain result",
                )
            source = resources[
                self._relation_target(manifest, item.alias, "maps_to_source_jd")
            ]
            items.extend(
                self.gateway.fetch_jd_evidence(
                    jd_id=str(jd_result["jd_id"]),
                    business_object_id=position_id,
                    graph_version=self._graph_version_name_for_jd(manifest, item.alias),
                    source_jd_id=str(source["business_id"]),
                    source_jd_version_id=str(source["version_id"]),
                    dataset_tag=COMPETITION_DEMO_V1,
                )
            )
        cv = manifest.cvs[0]
        cv_result = results.get(cv.alias)
        if not isinstance(cv_result, dict):
            raise DemoCommandError(
                "BATCH_2_REQUIRED",
                f"{cv.alias} has no recorded CV chain result",
            )
        cv_source = resources[
            self._relation_target(manifest, cv.alias, "maps_to_source_cv")
        ]
        items.extend(
            self.gateway.fetch_cv_evidence(
                snapshot_id=str(cv_result["validated_cv_snapshot_id"]),
                business_object_id=str(cv_result["resume_id"]),
                graph_version=self._graph_version_name_for_jd(manifest, manifest.jds[-1].alias),
                source_cv_id=str(cv_source["business_id"]),
                source_cv_version_id=str(cv_source["version_id"]),
                dataset_tag=COMPETITION_DEMO_V1,
            )
        )
        indexed = self.gateway.index_rag(items)
        state["rag_index"] = {
            **indexed,
            "tenant_ref": RAG_TENANT_REF,
            "permission_scope": RAG_PERMISSION_SCOPE,
            "item_count": len(items),
            "graph_version": self._graph_version_name_for_jd(
                manifest, manifest.jds[-1].alias
            ),
            "business_object_id": position_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        state["status"] = "rag_indexed"
        state["updated_at"] = datetime.now(UTC).isoformat()
        self._write_state(state)
        return self.verify()

    def query_rag(self) -> dict[str, Any]:
        """Run the demo success and insufficient-evidence RAG queries."""
        manifest = load_manifest(self.manifest_path)
        state = self._read_state(required=True)
        if not isinstance(state.get("rag_index"), dict):
            raise DemoCommandError(
                "RAG_INDEX_REQUIRED",
                "index-rag must complete before RAG queries",
            )
        results = dict(state.get("batch_2_results") or {})
        resources = state.get("resources", {})
        position_id = str(
            resources[manifest.position_family.position_alias]["business_id"]
        )
        graph_version = self._graph_version_name_for_jd(
            manifest, manifest.jds[-1].alias
        )
        success = self.gateway.query_rag(
            {
                "contract_version": "evidence-rag-query.v1",
                "business_object": {
                    "object_type": "standard_position",
                    "object_id": position_id,
                },
                "query_text": (
                    "候选人是否具备该岗位要求的核心技能？请只依据提供的 Evidence 原文回答。"
                ),
                "evidence_types": ["jd_evidence", "cv_evidence", "matching_evidence"],
                "graph_version": graph_version,
            }
        )
        if success.get("status") != "answered":
            raise DemoCommandError(
                "RAG_SUCCESS_CASE_FAILED",
                "the demo success query did not produce an answered status",
                success,
            )
        cv = manifest.cvs[0]
        insufficient = self.gateway.query_rag(
            {
                "contract_version": "evidence-rag-query.v1",
                "business_object": {
                    "object_type": "cv_profile",
                    "object_id": str(results[cv.alias]["resume_id"]),
                },
                "query_text": "候选人是否持有注册会计师（CPA）证书？",
                # The demo CV carries only cv_evidence; this object has no
                # matching Evidence, so retrieval must refuse to answer.
                "evidence_types": ["matching_evidence"],
                "graph_version": graph_version,
            }
        )
        if insufficient.get("status") != "insufficient_evidence":
            raise DemoCommandError(
                "RAG_INSUFFICIENT_CASE_FAILED",
                "the demo insufficient-evidence query did not refuse",
                insufficient,
            )
        results["rag-response-success"] = {
            "trace_id": success["trace_id"],
            "status": "answered",
            "provider": success.get("provider"),
            "model": success.get("model"),
            "reference_count": len(success.get("references") or []),
        }
        results["rag-response-insufficient"] = {
            "trace_id": insufficient["trace_id"],
            "status": "insufficient_evidence",
            "provider": insufficient.get("provider"),
            "error_code": (insufficient.get("error") or {}).get("code"),
        }
        state["batch_2_results"] = results
        state["status"] = "rag_ready"
        state["updated_at"] = datetime.now(UTC).isoformat()
        self._write_state(state)
        return self.verify()

    def verify(self) -> dict[str, Any]:
        manifest = load_manifest(self.manifest_path)
        inputs = load_inputs(manifest, self.manifest_path)
        state = self._read_state(required=True)
        if state.get("status") == "removed":
            raise DemoCommandError(
                "DATASET_NOT_LOADED",
                f"{COMPETITION_DEMO_V1} was removed and must be loaded again",
            )
        errors: list[str] = []
        if state.get("dataset_version") != manifest.dataset_version:
            errors.append("dataset version mismatch")
        if state.get("phase") not in {"foundation", "batch_2"}:
            errors.append("runtime phase mismatch")
        resources = state.get("resources", {})
        required_aliases = {
            manifest.position_family.alias,
            manifest.position_family.position_alias,
            *(item.alias for item in manifest.jds),
            *(item.alias for item in manifest.cvs),
            *(item.alias for item in manifest.trend_windows),
            *(
                item.alias
                for item in manifest.expected_resources
                if item.resource_type in FOUNDATION_RESOURCE_TYPES
            ),
        }
        missing = sorted(alias for alias in required_aliases if alias not in resources)
        if missing:
            errors.append("missing resources: " + ", ".join(missing))
        pending_resources = state.get("pending_resources", {})
        completed_resources = self._completed_batch_2_resources(
            manifest, state.get("batch_2_results")
        )
        for alias in completed_resources:
            pending_resources.pop(alias, None)
        state["completed_resources"] = completed_resources
        batch2_complete = all(
            alias in completed_resources
            for alias in self._batch_2_aliases(manifest)
        )
        state["batch_2_status"] = (
            "completed"
            if batch2_complete
            else "partial"
            if completed_resources
            else "not_started"
        )
        rag_index = state.get("rag_index")
        rag_status = "not_started"
        if isinstance(rag_index, dict) and int(rag_index.get("indexed_count", 0) or 0) > 0:
            rag_status = "indexed"
            if (
                "rag-response-success" in completed_resources
                and "rag-response-insufficient" in completed_resources
            ):
                rag_status = "ready"
        state["rag_status"] = rag_status
        expected_pending_aliases = {
            item.alias
            for item in manifest.expected_resources
            if item.resource_type in PENDING_RESOURCE_TYPES
        }
        missing_pending = sorted(
            expected_pending_aliases
            - set(pending_resources)
            - set(completed_resources)
        )
        if missing_pending:
            errors.append("missing pending resources: " + ", ".join(missing_pending))
        for relation in manifest.relations:
            if relation.relation_type in {
                "maps_to_source_jd",
                "maps_to_source_cv",
                "time_range_for_graph_version",
            } and (
                relation.source not in resources
                or relation.target
                not in resources | pending_resources | completed_resources
            ):
                errors.append(f"relation endpoint missing: {relation.alias}")
        if errors:
            raise DemoCommandError("VERIFY_FAILED", "competition demo verification failed", errors)
        state["status"] = "foundation_verified"
        state["verified_at"] = datetime.now(UTC).isoformat()
        self._write_state(state)
        return {
            "phase": "foundation",
            "status": "foundation_verified",
            "dataset_version": manifest.dataset_version,
            "resource_count": len(resources),
            "batch_2_status": state["batch_2_status"],
            "rag_status": rag_status,
            "completed_resources": list(completed_resources.values()),
            "pending_resources": list(pending_resources.values()),
            "input_count": len(inputs),
            "mapping": resources,
        }

    def remove_demo_only(self) -> dict[str, Any]:
        state = self._read_state(required=True)
        if state.get("dataset_version") != COMPETITION_DEMO_V1:
            raise DemoCommandError("CLEANUP_SCOPE_MISMATCH", "state does not belong to competition-demo-v1")
        resources = state.get("resources", {})
        position = resources.get("position-ai-application-engineer")
        deleted: list[dict[str, Any]] = []
        retained: list[dict[str, Any]] = []
        if position and position.get("created"):
            deleted.append(self.gateway.remove_position(str(position["business_id"])))
        if isinstance(state.get("rag_index"), dict):
            deleted.append(
                {
                    "scope": "evidence_rag",
                    "tenant_ref": RAG_TENANT_REF,
                    "permission_scope": RAG_PERMISSION_SCOPE,
                    "deleted": self.gateway.delete_rag(
                        tenant_ref=RAG_TENANT_REF,
                        permission_scope=RAG_PERMISSION_SCOPE,
                    ),
                }
            )
        for alias, item in resources.items():
            if item.get("resource_type") in {"source_jd", "source_cv"}:
                retained.append(
                    {
                        "alias": alias,
                        "business_id": item.get("business_id"),
                        "reason": "owner service exposes no dataset-scoped destructive API or resource is immutable",
                    }
                )
        state["status"] = "removed"
        state["removed_at"] = datetime.now(UTC).isoformat()
        state["deleted_resources"] = deleted
        state["retained_history"] = retained
        self._write_state(state)
        return {
            "status": "removed",
            "dataset_version": COMPETITION_DEMO_V1,
            "deleted_resources": deleted,
            "retained_history": retained,
            "non_demo_resources_touched": 0,
        }

    def _new_state(
        self,
        manifest: CompetitionDemoManifestV1,
        inputs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "mapping_version": "competition-demo-runtime-map.v1",
            "dataset_version": manifest.dataset_version,
            "demo_only": True,
            "phase": "foundation",
            "status": "loading",
            "input_paths": {
                item.alias: item.input_path for item in (*manifest.jds, *manifest.cvs)
            },
            "resources": {},
            "pending_resources": self._pending_resources(manifest),
            "created_at": datetime.now(UTC).isoformat(),
        }

    @staticmethod
    def _pending_resources(
        manifest: CompetitionDemoManifestV1,
    ) -> dict[str, dict[str, Any]]:
        return {
            item.alias: {
                "alias": item.alias,
                "resource_type": item.resource_type,
                "owner_service": item.owner_service,
                "status": "pending",
                "dataset_version": manifest.dataset_version,
            }
            for item in manifest.expected_resources
            if item.resource_type in PENDING_RESOURCE_TYPES
        }

    @staticmethod
    def _completed_batch_2_resources(
        manifest: CompetitionDemoManifestV1,
        results: Any,
    ) -> dict[str, dict[str, Any]]:
        if not isinstance(results, dict):
            return {}
        bindings = {
            "extracted-jd-001": ("jd-demo-001", "extraction_task_id"),
            "extracted-jd-002": ("jd-demo-002", "extraction_task_id"),
            "extracted-jd-003": ("jd-demo-003", "extraction_task_id"),
            "published-jd-fact-001": ("jd-demo-001", "publication_id"),
            "published-jd-fact-002": ("jd-demo-002", "publication_id"),
            "published-jd-fact-003": ("jd-demo-003", "publication_id"),
            "graph-2025-q4": ("graph-2025-q4", "graph_version_id"),
            "graph-2026-q1": ("graph-2026-q1", "graph_version_id"),
            "graph-2026-q2": ("graph-2026-q2", "graph_version_id"),
            "position-profile-ai-app-engineer": (
                "position-profile-ai-app-engineer",
                "position_id",
            ),
            "cv-extraction-demo-001": ("cv-demo-001", "cv_extraction_task_id"),
            "validated-cv-snapshot-demo-001": (
                "cv-demo-001",
                "validated_cv_snapshot_id",
            ),
            "cv-match-profile-demo-001": (
                "matching-demo-001",
                "cv_profile_version",
            ),
            "position-match-profile-ai-app-engineer": (
                "matching-demo-001",
                "position_profile_version",
            ),
            "matching-evaluation-ai-app-engineer": (
                "matching-demo-001",
                "evaluation_id",
            ),
            "rag-response-success": (
                "rag-response-success",
                "trace_id",
            ),
            "rag-response-insufficient": (
                "rag-response-insufficient",
                "trace_id",
            ),
        }
        specs = {item.alias: item for item in manifest.expected_resources}
        completed: dict[str, dict[str, Any]] = {}
        for alias, (result_alias, id_field) in bindings.items():
            result = results.get(result_alias)
            resource_id = result.get(id_field) if isinstance(result, dict) else None
            spec = specs.get(alias)
            if resource_id is None or spec is None:
                continue
            completed[alias] = {
                "alias": alias,
                "resource_type": spec.resource_type,
                "owner_service": spec.owner_service,
                "status": "completed",
                "dataset_version": manifest.dataset_version,
                "business_id": resource_id,
            }
        return completed

    @staticmethod
    def _batch_2_aliases(
        manifest: CompetitionDemoManifestV1,
    ) -> set[str]:
        bindings = {
            "extracted-jd-001",
            "extracted-jd-002",
            "extracted-jd-003",
            "published-jd-fact-001",
            "published-jd-fact-002",
            "published-jd-fact-003",
            "graph-2025-q4",
            "graph-2026-q1",
            "graph-2026-q2",
            "position-profile-ai-app-engineer",
            "cv-extraction-demo-001",
            "validated-cv-snapshot-demo-001",
            "cv-match-profile-demo-001",
            "position-match-profile-ai-app-engineer",
            "matching-evaluation-ai-app-engineer",
            "rag-response-success",
            "rag-response-insufficient",
        }
        specs = {item.alias for item in manifest.expected_resources}
        return bindings & specs

    @staticmethod
    def _window_for_graph(
        manifest: CompetitionDemoManifestV1, graph_alias: str
    ):
        matches = [
            window
            for window in manifest.trend_windows
            if window.graph_version_alias == graph_alias
        ]
        if len(matches) != 1:
            raise DemoCommandError(
                "MANIFEST_WINDOW_INVALID",
                f"{graph_alias} must map to exactly one trend window",
            )
        return matches[0]

    @staticmethod
    def _graph_version_name_for_jd(
        manifest: CompetitionDemoManifestV1, jd_alias: str
    ) -> str:
        indexes = {
            item.alias: index for index, item in enumerate(manifest.jds)
        }
        index = indexes.get(jd_alias)
        if index is None or index >= len(manifest.graph_versions):
            raise DemoCommandError(
                "MANIFEST_GRAPH_BINDING_INVALID",
                f"{jd_alias} has no graph version binding",
            )
        return manifest.graph_versions[index].version_name

    @staticmethod
    def _record(
        state: dict[str, Any], alias: str, resource_type: str, result: dict[str, Any]
    ) -> None:
        state["resources"][alias] = {
            "resource_type": resource_type,
            "dataset_version": COMPETITION_DEMO_V1,
            "demo_only": True,
            **result,
        }

    @staticmethod
    def _relation_target(
        manifest: CompetitionDemoManifestV1, source: str, relation_type: str
    ) -> str:
        matches = [
            item.target
            for item in manifest.relations
            if item.source == source and item.relation_type == relation_type
        ]
        if len(matches) != 1:
            raise DemoCommandError(
                "MANIFEST_RELATION_INVALID",
                f"{source} must have exactly one {relation_type} relation",
            )
        return matches[0]

    def _read_state(self, *, required: bool) -> dict[str, Any] | None:
        if not self.state_path.is_file():
            if required:
                raise DemoCommandError(
                    "DATASET_NOT_LOADED",
                    f"{COMPETITION_DEMO_V1} has no runtime mapping",
                )
            return None
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DemoCommandError("RUNTIME_MAP_INVALID", str(exc)) from exc

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def _result(command: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "command": command,
        "ok": True,
        "generated_at": datetime.now(UTC).isoformat(),
        **payload,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("preflight")
    for command in (
        "load",
        "verify",
        "remove-demo-only",
        "run-batch2",
        "index-rag",
        "query-rag",
    ):
        child = subparsers.add_parser(command)
        child.add_argument("--dataset", required=True, choices=(COMPETITION_DEMO_V1,))
    parser.add_argument("--state-path", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(argv)
    loader = CompetitionDemoLoader(HttpDemoGateway(), state_path=args.state_path)
    try:
        if args.command == "preflight":
            payload = loader.preflight()
        elif args.command == "load":
            payload = loader.load()
        elif args.command == "verify":
            payload = loader.verify()
        elif args.command == "run-batch2":
            payload = loader.run_batch2()
        elif args.command == "index-rag":
            payload = loader.index_rag()
        elif args.command == "query-rag":
            payload = loader.query_rag()
        else:
            payload = loader.remove_demo_only()
        print(json.dumps(_result(args.command, payload), ensure_ascii=False, indent=2))
        return 0
    except DemoCommandError as exc:
        print(
            json.dumps(
                {
                    "command": args.command,
                    "ok": False,
                    "error": {
                        "code": exc.code,
                        "message": str(exc),
                        "details": exc.details,
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
