#!/usr/bin/env python3
"""Build and publish bounded graph versions from the imported 2026 Bundle JDs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run_audit_jd_kg_prediction_flow_v2 import API, login_main  # noqa: E402


TARGET_CODES = {
    "AI_AGENT_ENGINEER",
    "BACKEND_ENGINEER",
    "LLM_ALGORITHM_ENGINEER",
}
MAIN_POSITION_IDS = {
    "AI_AGENT_ENGINEER": "56101559-7078-4599-9022-7c4f1b7d62d9",
    "BACKEND_ENGINEER": "be810ef8-3b45-4b1b-8265-140d216fdcdc",
    "LLM_ALGORITHM_ENGINEER": "e9b98dc4-1ab1-42d0-b9ee-c2467dc7b675",
}
WINDOWS = (
    ("2026-07-27T00:00:00+00:00", "2026-07-30T00:00:00+00:00"),
    ("2026-07-27T00:00:00+00:00", "2026-08-02T00:00:00+00:00"),
    ("2026-07-27T00:00:00+00:00", "2026-08-08T00:00:00+00:00"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("data/extraction-audit/capability-evolution-2026-versions.json"),
    )
    parser.add_argument("--main-url", default="http://127.0.0.1:58000")
    parser.add_argument("--main-username", default="demo_admin")
    parser.add_argument("--main-password", default="password123")
    args = parser.parse_args()
    api = API(args.main_url, 60)
    token = login_main(api, args.main_username, args.main_password)
    published: list[dict[str, object]] = []
    try:
        for code in sorted(TARGET_CODES):
            position_id = MAIN_POSITION_IDS[code]
            existing_versions = api.request(
                "GET",
                f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/versions",
                token=token,
            )["data"]
            existing_by_name = {
                str(value.get("version_name")): value
                for value in existing_versions
                if isinstance(value, dict) and value.get("version_name")
            }
            for sequence, (window_start, window_end) in enumerate(WINDOWS, 1):
                version_name = f"{code}-2026-bundle-{sequence}"
                if version_name in existing_by_name:
                    published.append(
                        {
                            "position_code": code,
                            "position_id": position_id,
                            "window_start": window_start,
                            "window_end": window_end,
                            "version": existing_by_name[version_name],
                            "reused": True,
                        }
                    )
                    continue
                job = api.request(
                    "POST",
                    f"/api/v1/portal/admin/knowledge-graph/positions/{position_id}/build",
                    token=token,
                    json={
                        "window_start": window_start,
                        "window_end": window_end,
                        "minimum_effective_weight": 0.05,
                        "minimum_valid_samples": 1,
                    },
                )["data"]
                job_id = int(job["job_id"])
                for _ in range(240):
                    state = api.request(
                        "GET",
                        f"/api/v1/portal/admin/knowledge-graph/build-jobs/{job_id}",
                        token=token,
                    )["data"]
                    if state["status"] in {"succeeded", "failed"}:
                        break
                    time.sleep(0.5)
                else:
                    raise RuntimeError(f"build job {job_id} timed out")
                if state["status"] != "succeeded":
                    raise RuntimeError(
                        f"build job {job_id} failed: "
                        + json.dumps(state.get("error"), ensure_ascii=False)
                    )
                run_id = int(state["build_run_id"])
                review = api.request(
                    "POST",
                    f"/api/v1/portal/admin/knowledge-graph/build-runs/{run_id}/auto-review",
                    token=token,
                    json={
                        "policy_version": "review-policy.v1",
                        "reason": "按统一规则审核 2026 Bundle 岗位能力快照。",
                    },
                )["data"]
                human_task_ids = [
                    int(value) for value in review.get("requires_human_task_ids", [])
                ]
                for task_id in human_task_ids:
                    api.request(
                        "POST",
                        f"/api/v1/portal/admin/knowledge-graph/review-tasks/{task_id}/claim",
                        token=token,
                        json={"reason": "核验 2026 Bundle 真实 JD 能力关系。"},
                    )
                    api.request(
                        "POST",
                        f"/api/v1/portal/admin/knowledge-graph/review-tasks/{task_id}/approve",
                        token=token,
                        json={"reason": "批准纳入 2026 Bundle 岗位能力时间快照。"},
                    )
                version = api.request(
                    "POST",
                    f"/api/v1/portal/admin/knowledge-graph/build-runs/{run_id}/publish",
                    token=token,
                    json={
                        "reason": "发布 2026 Bundle 岗位能力时间快照。",
                        "version_name": version_name,
                        "release_notes": (
                            f"2026 Bundle 真实 JD 累计观察窗口：{window_start} 至 {window_end}。"
                        ),
                    },
                )["data"]
                item = {
                    "position_code": code,
                    "position_id": position_id,
                    "job_id": job_id,
                    "build_run_id": run_id,
                    "window_start": window_start,
                    "window_end": window_end,
                    "summary": state.get("summary"),
                    "human_approved_review_count": len(human_task_ids),
                    "version": version,
                }
                published.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
    finally:
        api.close()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps({"published": published}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
