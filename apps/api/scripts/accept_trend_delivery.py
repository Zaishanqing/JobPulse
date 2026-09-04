"""Run the backend trend-report acceptance flow against a running Jobgraph API."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(base_url: str, token: str, method: str, path: str, body=None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - explicit operator URL
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed: HTTP {exc.code}: {detail}") from exc
    if result.get("code") != 0:
        raise RuntimeError(f"{method} {path} failed: {result}")
    return result["data"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", required=True, help="Bearer token with trend run permission")
    parser.add_argument("--review-token", help="Bearer token with trend review/publish permission")
    parser.add_argument("--position-id", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=300)
    args = parser.parse_args()
    review_token = args.review_token or args.token

    today = date.today()
    run = request_json(
        args.base_url,
        args.token,
        "POST",
        f"/api/v1/positions/{args.position_id}/trend-analysis/tasks",
        {
            "time_window_start": (today - timedelta(days=180)).isoformat(),
            "time_window_end": today.isoformat(),
        },
    )
    deadline = time.monotonic() + args.timeout_seconds
    while run.get("canonical_status") not in {"succeeded", "failed", "cancelled"}:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"trend run {run['task_id']} did not finish")
        time.sleep(1)
        run = request_json(
            args.base_url,
            args.token,
            "GET",
            f"/api/v1/trend-analysis/tasks/{run['task_id']}",
        )
    if run.get("canonical_status") != "succeeded":
        raise RuntimeError(f"remote trend run did not succeed: {run}")

    report_id = run.get("report_id") or (run.get("result_payload") or {}).get("report_id")
    if not report_id:
        raise RuntimeError(f"successful run did not project a report: {run}")
    projected = request_json(
        args.base_url,
        args.token,
        "GET",
        f"/api/v1/trend-reports/{report_id}",
    )
    review = request_json(
        args.base_url,
        review_token,
        "POST",
        "/api/v1/review-tasks",
        {"object_type": "trend_report", "object_id": report_id, "reason": "backend acceptance"},
    )
    request_json(
        args.base_url,
        review_token,
        "POST",
        f"/api/v1/review-tasks/{review['task_id']}/claim",
    )
    request_json(
        args.base_url,
        review_token,
        "POST",
        f"/api/v1/review-tasks/{review['task_id']}/approve",
        {"review_comment": "backend acceptance passed"},
    )
    published = request_json(
        args.base_url,
        review_token,
        "POST",
        f"/api/v1/trend-reports/{report_id}/publish",
    )
    if published["status"] != "published" or not published["publication_gate"]["eligible"]:
        raise RuntimeError(f"publication verification failed: {published}")
    print(json.dumps({
        "run": run,
        "projection": projected,
        "review_task_id": review["task_id"],
        "published_report": published,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
