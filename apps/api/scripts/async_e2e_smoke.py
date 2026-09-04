"""Run the real cross-service asynchronous acceptance path.

This script intentionally fails when a required profile, worker, or upstream is
not configured. It is not a projection/health smoke and must only report green
after every task reaches a terminal ``succeeded`` state.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


BASE_URL = os.environ.get("ASYNC_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
USERNAME = os.environ.get("ASYNC_E2E_USERNAME", "demo_admin")
PASSWORD = os.environ.get("ASYNC_E2E_PASSWORD", "password123")
CV_USERNAME = os.environ.get("ASYNC_E2E_CV_USERNAME", "")
CV_PASSWORD = os.environ.get("ASYNC_E2E_CV_PASSWORD", "")
POSITION_ID = os.environ.get("ASYNC_E2E_POSITION_ID", "BACKEND_ENGINEER")
TIMEOUT_SECONDS = int(os.environ.get("ASYNC_E2E_TIMEOUT_SECONDS", "600"))
POLL_SECONDS = float(os.environ.get("ASYNC_E2E_POLL_SECONDS", "2"))
EVIDENCE_PATH = Path(
    os.environ.get("ASYNC_E2E_EVIDENCE_PATH", "reports/async-e2e-latest.json")
)


class AsyncE2EError(RuntimeError):
    """A required asynchronous acceptance step failed."""


def request(
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AsyncE2EError(f"{method} {path} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise AsyncE2EError(f"{method} {path} was unreachable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise AsyncE2EError(f"{method} {path} returned an invalid envelope: {payload!r}")
    return payload


def data(payload: dict[str, Any], endpoint: str) -> Any:
    if "data" not in payload:
        raise AsyncE2EError(f"{endpoint} returned no data")
    return payload["data"]


def login(username: str, password: str) -> tuple[str, dict[str, Any]]:
    endpoint = "/api/v1/auth/login"
    values = data(request(endpoint, method="POST", body={"username": username, "password": password}), endpoint)
    if not isinstance(values, dict) or not isinstance(values.get("access_token"), str):
        raise AsyncE2EError(f"{endpoint} did not return an access token")
    token = values["access_token"]
    me = data(request("/api/v1/auth/me", token=token), "/api/v1/auth/me")
    if not isinstance(me, dict) or me.get("username") != username:
        raise AsyncE2EError(f"authenticated identity mismatch for {username!r}")
    return token, me


def wait_for(
    name: str,
    fetch: Callable[[], dict[str, Any]],
    status: Callable[[dict[str, Any]], str | None],
    *,
    success: frozenset[str] = frozenset({"succeeded"}),
    failure: frozenset[str] = frozenset({"failed", "cancelled"}),
) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_SECONDS
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = fetch()
        current = status(last)
        if current in success:
            return last
        if current in failure:
            raise AsyncE2EError(f"{name} reached terminal failure {current}: {last!r}")
        time.sleep(POLL_SECONDS)
    raise AsyncE2EError(f"{name} did not finish within {TIMEOUT_SECONDS}s: {last!r}")


def require_dict(value: Any, endpoint: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AsyncE2EError(f"{endpoint} returned a non-object data payload")
    return value


def import_draft_after_validation(task_id: str, token: str) -> dict[str, Any]:
    """Wait for the validation worker before importing an enforced draft."""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return require_dict(
                data(
                    request(
                        f"/api/v1/extraction-tasks/{task_id}/import-draft",
                        token=token,
                        method="POST",
                    ),
                    "JD draft import",
                ),
                "JD draft import",
            )
        except AsyncE2EError as exc:
            if "validation_pending" not in str(exc):
                raise
            time.sleep(POLL_SECONDS)
    raise AsyncE2EError(
        f"JD validation did not become importable within {TIMEOUT_SECONDS}s: {task_id}"
    )


def main() -> int:
    admin_token, admin = login(USERNAME, PASSWORD)
    if admin.get("role") not in {"admin", "developer", "reviewer"}:
        raise AsyncE2EError("ASYNC_E2E_USERNAME must have enterprise/JD and integration permissions")
    if not CV_USERNAME or not CV_PASSWORD:
        raise AsyncE2EError(
            "set ASYNC_E2E_CV_USERNAME and ASYNC_E2E_CV_PASSWORD for a personal_user CV path"
        )
    cv_token, cv_user = login(CV_USERNAME, CV_PASSWORD)
    if cv_user.get("role") != "personal_user":
        raise AsyncE2EError("ASYNC_E2E_CV_USERNAME must be a personal_user")

    evidence: dict[str, Any] = {
        "schema_version": "jobpulse-async-e2e.v1",
        "base_url": BASE_URL,
        "position_id": POSITION_ID,
        "started_at": date.today().isoformat(),
        "steps": {},
    }

    source_jd = require_dict(
        data(
            request(
                "/api/v1/source-jds/import",
                token=admin_token,
                method="POST",
                body={
                    "source_record_id": f"async-e2e-{uuid4().hex}",
                    "source_platform": "company_career",
                    "source_url": "https://example.invalid/jobpulse-async-e2e",
                    "job_title_raw": f"Async E2E Backend Engineer {uuid4().hex[:8]}",
                    "company_name_raw": "JobPulse E2E",
                    "crawl_time": date.today().isoformat() + "T00:00:00Z",
                    "raw_text": "负责 Python FastAPI 服务开发，使用 Docker 和 PostgreSQL，建设可观测异步任务链。",
                    "raw_payload": {"source": "jobpulse-async-e2e"},
                    "text_canonicalization_version": "text-canonicalization-v1",
                    "source_version": "1",
                },
            ),
            "/api/v1/source-jds/import",
        ),
        "/api/v1/source-jds/import",
    )
    version_id = str(source_jd["source_jd_version_id"])
    extraction = require_dict(
        data(
            request(
                f"/api/v1/source-jd-versions/{version_id}/extraction-tasks?extraction_mode=rule",
                token=admin_token,
                method="POST",
            ),
            "JD extraction create",
        ),
        "JD extraction create",
    )
    extraction_id = str(extraction["id"])
    extraction_final = wait_for(
        "JD extraction",
        lambda: require_dict(data(request(f"/api/v1/extraction-tasks/{extraction_id}", token=admin_token), "JD extraction get"), "JD extraction get"),
        lambda item: str(item.get("status")),
    )
    draft = import_draft_after_validation(extraction_id, admin_token)
    jd_id = str(draft["jd_id"])
    parsed = require_dict(
        data(request(f"/api/v1/jds/{jd_id}/parse", token=admin_token, method="POST", body={"extraction_mode": "rule"}), "JD parse"),
        "JD parse",
    )
    confirmed = require_dict(
        data(request(f"/api/v1/jds/{jd_id}/parse-result/confirm", token=admin_token, method="POST"), "JD confirm"),
        "JD confirm",
    )
    publication = require_dict(
        data(request(f"/api/v1/jds/{jd_id}/parse-result/publish", token=admin_token, method="POST"), "JD publish"),
        "JD publish",
    )
    evidence["steps"]["jd"] = {
        "source_jd_version_id": version_id,
        "extraction_task_id": extraction_id,
        "extraction": extraction_final,
        "draft": draft,
        "parse_task": parsed,
        "confirmed": confirmed,
        "publication": publication,
    }

    cv = require_dict(
        data(
            request(
                "/api/v1/internal/source-cvs/import-and-extract",
                token=cv_token,
                method="POST",
                body={
                    "source_record_id": f"async-e2e-{uuid4().hex}",
                    "source_platform": "async_e2e",
                    "raw_text": "张三\nPython 后端工程师\n负责 FastAPI、PostgreSQL 和 Docker 项目开发。\n本科，计算机科学。",
                },
            ),
            "CV import",
        ),
        "CV import",
    )
    cv_task_id = str(cv["cv_extraction_task_id"])
    cv_final = wait_for(
        "CV extraction",
        lambda: require_dict(data(request(f"/api/v1/cv-extraction-tasks/{cv_task_id}", token=cv_token), "CV task get"), "CV task get"),
        lambda item: str(item.get("status")),
    )
    if cv_final.get("confirmation_status") != "confirmed":
        review = require_dict(data(request(f"/api/v1/cv-extraction-tasks/{cv_task_id}/review", token=cv_token), "CV review"), "CV review")
        review_id = str(review.get("review_id"))
        confirmation = require_dict(
            data(
                request(
                    f"/api/v1/cv-extraction-tasks/{cv_task_id}/confirm",
                    token=cv_token,
                    method="POST",
                    body={
                        "expected_review_id": review_id,
                        "idempotency_key": f"async-e2e-confirm-{cv_task_id}",
                        "field_decisions": [],
                    },
                ),
                "CV confirm",
            ),
            "CV confirm",
        )
        cv_final = require_dict(data(request(f"/api/v1/cv-extraction-tasks/{cv_task_id}", token=cv_token), "CV task final"), "CV task final")
    else:
        confirmation = None
    resume_id = str(cv_final.get("resume_id") or (confirmation or {}).get("resume_id") or "")
    if not resume_id:
        raise AsyncE2EError(f"CV chain finished without resume_id: {cv_final!r}")
    evidence["steps"]["cv"] = {"task_id": cv_task_id, "task": cv_final, "confirmation": confirmation, "resume_id": resume_id}

    document_id = str(publication.get("document_id") or publication.get("jd_id") or jd_id)
    kg_sync = require_dict(
        data(request(f"/api/v1/integrations/knowledge-graph/jds/{document_id}/sync", token=admin_token, method="POST"), "KG JD sync"),
        "KG JD sync",
    )
    kg_status = wait_for(
        "KG JD sync",
        lambda: require_dict(data(request(f"/api/v1/integrations/knowledge-graph/jds/{document_id}/status", token=admin_token), "KG JD status"), "KG JD status"),
        lambda item: str(item.get("sync_status")),
        success=frozenset({"synced", "published", "succeeded"}),
    )
    kg_build = require_dict(
        data(request(f"/api/v1/integrations/knowledge-graph/positions/{POSITION_ID}/build", token=admin_token, method="POST", body={"minimum_valid_samples": 1}), "KG build"),
        "KG build",
    )
    build_run_id = str((kg_build.get("build_run") or {}).get("build_run_id") or kg_build.get("build_run_id") or "")
    if not build_run_id:
        raise AsyncE2EError(f"KG build did not return build_run_id: {kg_build!r}")
    kg_build_final = wait_for(
        "KG position build",
        lambda: require_dict(data(request(f"/api/v1/integrations/knowledge-graph/build-runs/{build_run_id}", token=admin_token), "KG build status"), "KG build status"),
        lambda item: str((item.get("result") or {}).get("status") or item.get("status") or ""),
        success=frozenset({"succeeded", "published", "complete"}),
    )
    evidence["steps"]["knowledge_graph"] = {"jd_sync": kg_sync, "jd_status": kg_status, "build": kg_build, "build_final": kg_build_final}

    match = require_dict(
        data(request("/api/v1/matches/tasks", token=cv_token, method="POST", body={"resume_id": resume_id, "target_type": "standard_position", "target_id": POSITION_ID, "use_enterprise_weights": False, "generate_learning_path": False}), "Matching create"),
        "Matching create",
    )
    match_id = str(match.get("task_id") or match.get("id") or "")
    if not match_id:
        raise AsyncE2EError(f"Matching create did not return task id: {match!r}")
    match_final = wait_for(
        "Matching task",
        lambda: require_dict(data(request(f"/api/v1/matches/tasks/{match_id}", token=cv_token), "Matching get"), "Matching get"),
        lambda item: str(item.get("canonical_status") or item.get("status") or ""),
    )
    evidence["steps"]["matching"] = {"task_id": match_id, "task": match_final}

    trend = require_dict(
        data(request(f"/api/v1/positions/{POSITION_ID}/trend-analysis/tasks", token=admin_token, method="POST", body={"time_window_start": (date.today() - timedelta(days=84)).isoformat(), "time_window_end": date.today().isoformat()}), "Trend create"),
        "Trend create",
    )
    trend_id = str(trend.get("task_id") or trend.get("id") or "")
    if not trend_id:
        raise AsyncE2EError(f"Trend create did not return task id: {trend!r}")
    trend_final = wait_for(
        "Trend task",
        lambda: require_dict(data(request(f"/api/v1/trend-analysis/tasks/{trend_id}", token=admin_token), "Trend get"), "Trend get"),
        lambda item: str(item.get("canonical_status") or item.get("status") or ""),
    )
    evidence["steps"]["trend"] = {"task_id": trend_id, "task": trend_final}

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"async E2E passed: JD/CV/publication/KG/matching/trend; evidence={EVIDENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
