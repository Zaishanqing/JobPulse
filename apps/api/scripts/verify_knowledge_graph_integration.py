"""Black-box verification for the main-backend -> knowledge-graph service boundary."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx


class VerificationFailure(RuntimeError):
    pass


def step(message: str) -> None:
    print(f"[VERIFY] {message}", flush=True)


def request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    token: str | None = None,
    expected: int = 200,
    **kwargs,
) -> tuple[dict, httpx.Response]:
    headers = dict(kwargs.pop("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = client.request(method, path, headers=headers, **kwargs)
    try:
        body = response.json()
    except ValueError as exc:
        raise VerificationFailure(
            f"{method} {path} returned non-JSON HTTP {response.status_code}"
        ) from exc
    if response.status_code != expected:
        raise VerificationFailure(
            f"{method} {path}: expected HTTP {expected}, got "
            f"{response.status_code}: {body}"
        )
    if expected < 400 and body.get("code") != 0:
        raise VerificationFailure(f"{method} {path}: non-zero envelope: {body}")
    return body, response


def wait_ready(client: httpx.Client, path: str, timeout: int = 120) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            response = client.get(path)
            if response.status_code == 200 and response.json().get("code") == 0:
                return
            last_error = f"HTTP {response.status_code}"
        except (httpx.HTTPError, ValueError) as exc:
            last_error = str(exc)
        time.sleep(2)
    raise VerificationFailure(f"readiness timeout for {path}: {last_error}")


def docker_compose(*args: str) -> None:
    completed = subprocess.run(
        ["docker", "compose", *args], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    if completed.returncode:
        raise VerificationFailure(
            f"docker compose {' '.join(args)} failed:\n{completed.stdout}"
        )


def verify(args: argparse.Namespace) -> None:
    main = httpx.Client(base_url=args.main_url, timeout=30)
    kg = httpx.Client(base_url=args.kg_url, timeout=30)
    try:
        step("检查双方 readiness")
        wait_ready(main, "/readiness")
        wait_ready(kg, "/readiness")

        step("登录主系统管理员")
        body, _ = request(
            main, "POST", "/api/v1/auth/login",
            json={"username": args.main_username, "password": args.main_password},
        )
        main_token = body["data"]["access_token"]

        suffix = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        raw_text = (
            "岗位名称：Python 后端开发工程师\n"
            "岗位职责：负责后端服务开发。\n"
            "任职要求：熟悉 Python，具备本科及以上学历，3 年以上经验。"
        )
        step("创建 Seed 中不存在的新 JD")
        body, _ = request(
            main, "POST", "/api/v1/jds/text", token=main_token,
            json={
                "source_type": "integration_verification",
                "source_name": f"blackbox-{suffix}",
                "title": "Python 后端开发工程师",
                "raw_text": raw_text,
            },
        )
        document_id = body["data"]["jd_id"]

        def parse_and_confirm() -> dict:
            request(
                main, "POST", f"/api/v1/jds/{document_id}/parse",
                token=main_token, json={},
            )
            parsed, _ = request(
                main, "GET", f"/api/v1/jds/{document_id}/parse-result",
                token=main_token,
            )
            positions, _ = request(
                main, "GET", "/api/v1/positions", token=main_token
            )
            target = next(
                (
                    item for item in positions["data"]
                    if item.get("position_code") == "BACKEND_ENGINEER"
                ),
                None,
            )
            if target is None:
                raise VerificationFailure("main v2 catalog lacks BACKEND_ENGINEER")
            parse_result_id = parsed["data"]["parse_result_id"]
            target_position_id = target.get("position_id") or target.get("id")
            request(
                main,
                "POST",
                f"/api/v1/jd-parse-results/{parse_result_id}/position-catalog-mapping",
                token=main_token,
                json={"target_position_id": target_position_id},
            )
            request(
                main, "POST", f"/api/v1/jds/{document_id}/parse-result/confirm",
                token=main_token,
            )
            result, _ = request(
                main, "GET", f"/api/v1/jds/{document_id}/parse-result",
                token=main_token,
            )
            return result["data"]

        step("生成并确认 V2 ExtractionResult 与 NormalizedResult")
        parsed = parse_and_confirm()
        if parsed["extraction_result"]["schema_version"] != "v2":
            raise VerificationFailure("main extraction is not v2")
        if parsed["normalized_result"]["schema_version"] != "v2":
            raise VerificationFailure("main normalization is not v2")
        request(
            main, "POST", f"/api/v1/jds/{document_id}/parse-result/publish",
            token=main_token,
        )

        classification = parsed["normalized_result"].get("job_classification") or {}
        classification_key = classification.get("position_id")
        if not classification_key or classification.get("position_code") != "BACKEND_ENGINEER":
            raise VerificationFailure("normalization has no confirmed v2 position identity")

        step("建立显式岗位 ID 映射")
        request(
            main, "PUT",
            "/api/v1/integrations/knowledge-graph/mappings/position/"
            + quote(str(classification_key), safe=""),
            token=main_token,
            json={"knowledge_graph_id": args.kg_position_id},
        )
        main_position_id = str(classification_key)

        step("从主系统同步 JD 到知识图谱")
        first, _ = request(
            main, "POST",
            f"/api/v1/integrations/knowledge-graph/jds/{document_id}/sync",
            token=main_token,
        )
        first_hash = first["data"]["payload_hash"]
        if first["data"]["knowledge_graph_id"] != document_id:
            raise VerificationFailure("external document ID mapping mismatch")

        step("查询同步状态并验证第二次同步幂等")
        sync_status, _ = request(
            main, "GET",
            f"/api/v1/integrations/knowledge-graph/jds/{document_id}/status",
            token=main_token,
        )
        if sync_status["data"]["sync_status"] != "synced":
            raise VerificationFailure(f"unexpected sync state: {sync_status}")
        second, _ = request(
            main, "POST",
            f"/api/v1/integrations/knowledge-graph/jds/{document_id}/sync",
            token=main_token,
        )
        if not second["data"]["idempotent"] or second["data"]["payload_hash"] != first_hash:
            raise VerificationFailure("second synchronization was not idempotent")

        step("修改 JD 后验证 payload_hash 变化")
        raw_text += "\n加分项：熟悉 Docker。"
        request(
            main, "PUT", f"/api/v1/jds/{document_id}/raw",
            token=main_token, json={"raw_text": raw_text},
        )
        parse_and_confirm()
        request(
            main, "POST", f"/api/v1/jds/{document_id}/parse-result/publish",
            token=main_token,
        )
        changed, _ = request(
            main, "POST",
            f"/api/v1/integrations/knowledge-graph/jds/{document_id}/sync",
            token=main_token,
        )
        if changed["data"]["payload_hash"] == first_hash:
            raise VerificationFailure("payload_hash did not change after JD edit")

        step("发起真实 GraphBuildRun")
        built, _ = request(
            main, "POST",
            f"/api/v1/integrations/knowledge-graph/positions/{main_position_id}/build",
            token=main_token, json={"minimum_valid_samples": 1},
        )
        build_run_id = built["data"]["build_run"]["build_run_id"]
        request(
            main, "GET",
            f"/api/v1/integrations/knowledge-graph/build-runs/{build_run_id}",
            token=main_token,
        )

        step("通过知识图谱公共 API 完成审核与发布")
        kg_login, _ = request(
            kg, "POST", "/api/v1/auth/token",
            json={"username": args.kg_admin_username, "password": args.kg_admin_password},
        )
        kg_token = kg_login["data"]["access_token"]
        reviews, _ = request(kg, "GET", "/api/v1/review-tasks", token=kg_token)
        for task in reviews["data"]:
            if task.get("build_run_id") != build_run_id:
                continue
            if task["status"] == "pending":
                request(
                    kg, "POST", f"/api/v1/review-tasks/{task['id']}/claim",
                    token=kg_token, json={"reason": "integration verification"},
                )
                request(
                    kg, "POST", f"/api/v1/review-tasks/{task['id']}/approve",
                    token=kg_token, json={"reason": "integration verification"},
                )
        request(
            kg, "POST", f"/api/v1/graph/build-runs/{build_run_id}/publish",
            token=kg_token,
            json={"version_name": f"integration-{suffix}",
                  "release_notes": "black-box integration verification"},
        )

        step("从主系统读取正式图谱、版本与关系 Evidence")
        graph, _ = request(
            main, "GET",
            f"/api/v1/integrations/knowledge-graph/positions/{main_position_id}/graph",
            token=main_token,
        )
        graph_result = graph["data"]["result"]
        relations = graph_result.get("skill_relations", [])
        if not relations:
            raise VerificationFailure("published graph contains no relations")
        request(
            main, "GET",
            f"/api/v1/integrations/knowledge-graph/positions/{main_position_id}/versions",
            token=main_token,
        )
        relation_id = relations[0].get("relation_id")
        if relation_id is None:
            raise VerificationFailure("published relation has no relation_id")
        request(
            main, "GET",
            f"/api/v1/integrations/knowledge-graph/relations/{relation_id}/evidence",
            token=main_token,
        )

        step("验证 trace_id 传播")
        trace_id = f"req_verify_{suffix[-12:]}"
        traced, response = request(
            main, "GET", "/api/v1/integrations/knowledge-graph/status",
            token=main_token, headers={"X-Request-ID": trace_id},
        )
        if response.headers.get("X-Request-ID") != trace_id:
            raise VerificationFailure("main response header did not preserve trace_id")
        if not traced["data"].get("upstream_trace_id"):
            raise VerificationFailure("KG upstream trace_id was not returned")

        if args.exercise_outage:
            step("停止知识图谱并验证主系统返回 503")
            docker_compose("stop", "knowledge-graph-backend")
            request(
                main, "GET", "/api/v1/integrations/knowledge-graph/status",
                token=main_token, expected=503,
            )
            step("重启知识图谱并验证持久数据和业务恢复")
            docker_compose("start", "knowledge-graph-backend")
            wait_ready(kg, "/readiness")
            recovered, _ = request(
                main, "GET",
                f"/api/v1/integrations/knowledge-graph/positions/{main_position_id}/graph",
                token=main_token,
            )
            if not recovered["data"]["result"].get("skill_relations"):
                raise VerificationFailure("published graph was lost after service restart")
        else:
            step("停服恢复测试未启用；使用 --exercise-outage 执行")

        step("全部已执行检查通过")
    finally:
        main.close()
        kg.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kg-url", default="http://127.0.0.1:8001")
    parser.add_argument("--main-username", default="demo_admin")
    parser.add_argument("--main-password", default="password123")
    parser.add_argument("--kg-admin-username", default="admin")
    parser.add_argument("--kg-admin-password", default="admin123")
    parser.add_argument("--kg-position-id", default="POS_BACKEND")
    parser.add_argument("--exercise-outage", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        verify(parse_args())
    except (VerificationFailure, httpx.HTTPError) as exc:
        print(f"[FAILED] {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
