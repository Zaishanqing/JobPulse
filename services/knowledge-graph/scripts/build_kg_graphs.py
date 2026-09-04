"""Build and publish KG graphs for every position with published facts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.database import create_database  # noqa: E402
from app.models import (  # noqa: E402
    NormalizedJobClassification,
    ReviewTask,
    StandardPosition,
)


BASE_URL = "http://127.0.0.1:8000"
REQUEST_TIMEOUT = 120.0


def now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers: dict[str, str] = {}
    data: bytes | None = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE_URL + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"{method} {path}: HTTP {exc.code}: {body[:500]}"
        ) from exc
    if body.get("code") != 0:
        raise RuntimeError(f"{method} {path}: non-zero envelope: {body}")
    return body["data"]


def login(settings: Settings) -> str:
    data = request(
        "POST",
        "/api/v1/auth/token",
        payload={
            "username": settings.service_username,
            "password": settings.service_password,
        },
    )
    return str(data["access_token"])


def resolve_build_run_id(
    token: str,
    build: dict[str, Any],
    timeout_seconds: float = 600.0,
) -> int:
    build_run_id = build.get("build_run_id")
    if build_run_id is not None:
        return int(build_run_id)
    job_id = build.get("job_id")
    if job_id is None:
        raise RuntimeError(
            f"graph build response carries neither build_run_id nor job_id: {build}"
        )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = request(
            "GET", f"/api/v1/graph/build-jobs/{job_id}", token=token
        )
        if job.get("build_run_id") is not None:
            return int(job["build_run_id"])
        if str(job.get("status")) == "failed":
            raise RuntimeError(f"graph build job {job_id} failed: {job.get('error')}")
        time.sleep(2)
    raise RuntimeError(f"graph build job {job_id} did not finish within {timeout_seconds}s")


def open_review_tasks_for_build(
    token: str, build_run_id: int
) -> list[dict[str, Any]]:
    open_tasks: list[dict[str, Any]] = []
    page = 1
    while True:
        tasks = request(
            "GET",
            f"/api/v1/review-tasks?page={page}&page_size=100",
            token=token,
        )
        if not tasks:
            break
        open_tasks.extend(
            task
            for task in tasks
            if int(task.get("build_run_id") or -1) == build_run_id
            and task.get("status") in {"pending", "claimed", "modified"}
        )
        if len(tasks) < 100:
            break
        page += 1
    return open_tasks


def review_task_ids_for_build(
    database: Any, build_run_id: int
) -> list[int]:
    with database.session_factory() as session:
        return [
            int(task_id)
            for task_id in session.scalars(
                select(ReviewTask.id).where(
                    ReviewTask.build_run_id == build_run_id,
                    ReviewTask.status.in_(("pending", "claimed", "modified")),
                )
            )
        ]


def review_and_publish_graph(
    database: Any,
    token: str,
    build_run_id: int,
    tag: str,
    kg_position_id: str,
) -> dict[str, Any]:
    handled: list[int] = []
    for _ in range(12):
        task_ids = review_task_ids_for_build(database, build_run_id)
        if not task_ids:
            break
        for task_id in task_ids:
            task_status = None
            task_payload: dict[str, Any] = {}
            task_object_type = ""
            with database.session_factory() as session:
                task = session.get(ReviewTask, task_id)
                if task is not None:
                    task_status = task.status
                    task_payload = dict(task.payload or {})
                    task_object_type = str(task.object_type)
            if task_status == "pending":
                request(
                    "POST",
                    f"/api/v1/review-tasks/{task_id}/claim",
                    token=token,
                    payload={"reason": "published facts graph build"},
                )
            reasons = set(task_payload.get("reasons") or ())
            if (
                task_object_type == "position_skill_relation"
                and "unknown_modality" in reasons
            ):
                # The publish gate explicitly forbids relations whose only
                # evidence has unknown modality. Exclude that relation through
                # the normal review transition instead of inventing metadata.
                request(
                    "POST",
                    f"/api/v1/review-tasks/{task_id}/reject",
                    token=token,
                    payload={
                        "reason": "excluded relation with unknown evidence modality"
                    },
                )
                handled.append(task_id)
                continue
            request(
                "POST",
                f"/api/v1/review-tasks/{task_id}/approve",
                token=token,
                payload={"reason": "published facts graph build"},
            )
            handled.append(task_id)

    gate = request(
        "GET",
        f"/api/v1/graph/build-runs/{build_run_id}/publish-gate",
        token=token,
    )
    if not gate.get("allowed"):
        raise RuntimeError(
            "graph publish gate is closed: " + json.dumps(gate, ensure_ascii=False)
        )
    published = request(
        "POST",
        f"/api/v1/graph/build-runs/{build_run_id}/publish",
        token=token,
        payload={
            "version_name": f"published-facts-{kg_position_id.lower()}-{tag}",
            "release_notes": "Built from published facts already synced through Outbox",
            "reason": "published facts graph build",
        },
    )
    return {
        "handled_review_task_ids": handled,
        "publish_gate": gate,
        "published_version": published,
    }


def build_position(
    database: Any,
    token: str,
    tag: str,
    position_id: str,
    minimum_effective_weight: float,
    minimum_valid_samples: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "position_id": position_id,
        "status": "pending",
    }
    try:
        build = request(
            "POST",
            f"/api/v1/positions/{position_id}/graph/build",
            token=token,
            payload={
                "minimum_effective_weight": minimum_effective_weight,
                "minimum_valid_samples": minimum_valid_samples,
            },
        )
        build_run_id = resolve_build_run_id(token, build)
        workflow = review_and_publish_graph(
            database, token, build_run_id, tag, position_id
        )
        result.update(
            {
                "status": "success",
                "build_run_id": build_run_id,
                "build": build,
                "workflow": workflow,
            }
        )
    except Exception as exc:  # noqa: BLE001 - report per-position failures
        result["status"] = "failed"
        result["error"] = str(exc)
        print(f"[GRAPH] {position_id} FAIL: {exc}", file=sys.stderr, flush=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--minimum-effective-weight", type=float, default=0.05)
    parser.add_argument("--minimum-valid-samples", type=int, default=1)
    parser.add_argument("--report", default="/tmp/kg-graph-build-report.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    global REQUEST_TIMEOUT
    REQUEST_TIMEOUT = args.timeout
    global BASE_URL
    BASE_URL = os.environ.get("KG_API_BASE_URL", BASE_URL).rstrip("/")

    settings = Settings.from_env()
    database = create_database(settings)
    with database.session_factory() as session:
        position_ids = sorted(
            str(value)
            for value in session.scalars(
                select(NormalizedJobClassification.position_id)
                .join(
                    StandardPosition,
                    StandardPosition.position_id
                    == NormalizedJobClassification.position_id,
                )
                .where(
                    NormalizedJobClassification.position_id.is_not(None),
                    NormalizedJobClassification.resolution_status.in_(
                        ("resolved", "manually_confirmed")
                    ),
                    StandardPosition.status == "active",
                    StandardPosition.taxonomy_version
                    == "position-taxonomy.v3.0.0",
                    StandardPosition.sample_support_status == "sufficient",
                )
                .distinct()
            )
        )
        already_published = {
            str(value)
            for value in session.scalars(
                select(StandardPosition.position_id).where(
                    StandardPosition.current_version_id.is_not(None)
                )
            )
        }
    position_ids = [value for value in position_ids if value not in already_published]

    if not position_ids:
        raise RuntimeError("no resolved job classifications found in KG")
    print(
        f"[GRAPH-BUILD] targets: {len(position_ids)} positions "
        f"(skipped {len(already_published)} already published)",
        flush=True,
    )
    if args.dry_run:
        print(json.dumps({"position_ids": position_ids}, ensure_ascii=False, indent=2))
        return 0

    tag = now_tag()
    token = login(settings)
    results: list[dict[str, Any]] = []
    try:
        if args.workers > 1:
            with ThreadPoolExecutor(
                max_workers=args.workers, thread_name_prefix="kg-graph-build"
            ) as executor:
                futures = [
                    executor.submit(
                        build_position,
                        database,
                        token,
                        tag,
                        position_id,
                        args.minimum_effective_weight,
                        args.minimum_valid_samples,
                    )
                    for position_id in position_ids
                ]
                for future in as_completed(futures):
                    results.append(future.result())
        else:
            for position_id in position_ids:
                results.append(
                    build_position(
                        database,
                        token,
                        tag,
                        position_id,
                        args.minimum_effective_weight,
                        args.minimum_valid_samples,
                    )
                )
    finally:
        database.engine.dispose()

    report = {
        "status": (
            "success"
            if all(item.get("status") == "success" for item in results)
            else "partial_success"
        ),
        "target_count": len(position_ids),
        "success_count": sum(
            item.get("status") == "success" for item in results
        ),
        "failed_count": sum(item.get("status") == "failed" for item in results),
        "results": results,
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {
            "status": report["status"],
            "target_count": report["target_count"],
            "success_count": report["success_count"],
            "failed_count": report["failed_count"],
        },
        ensure_ascii=False,
    ))
    return 0 if report["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
