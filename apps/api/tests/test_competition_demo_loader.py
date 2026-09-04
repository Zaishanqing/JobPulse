from __future__ import annotations

import json

import pytest

from scripts.competition_demo import (
    MANIFEST_PATH,
    CompetitionDemoLoader,
    DemoCommandError,
    HttpDemoGateway,
    load_inputs,
    load_manifest,
)


class FakeGateway:
    def __init__(self, *, fail_cv_once: bool = False) -> None:
        self.positions: dict[str, int] = {}
        self.jds: dict[str, int] = {}
        self.cvs: dict[str, int] = {}
        self.fail_cv_once = fail_cv_once
        self.fail_jd_chain_aliases: set[str] = set()
        self.removed_positions: list[str] = []
        self.indexed_rag_calls: list[list[dict]] = []
        self.deleted_rag_scopes: list[tuple[str, str]] = []
        self.query_rag_calls: list[dict] = []

    def preflight(self):
        return {
            "runtime_mode": "test",
            "compose_profiles": [],
            "service_groups": {
                "required_for_foundation": {"main": {"status": "available"}},
                "required_for_batch_2": {},
                "optional_later": {},
            },
            "configuration_groups": {},
            "missing_required_for_foundation": {
                "services": [],
                "configuration": [],
            },
        }

    def ensure_position(self, manifest):
        alias = manifest.position_family.position_alias
        created = alias not in self.positions
        self.positions.setdefault(alias, 101)
        return {"business_id": str(self.positions[alias]), "created": created}

    def ensure_source_jd(self, alias, payload):
        del payload
        created = alias not in self.jds
        self.jds.setdefault(alias, 200 + len(self.jds) + 1)
        return {
            "business_id": f"source-jd-{self.jds[alias]}",
            "version_id": f"source-jd-version-{self.jds[alias]}",
            "created": created,
        }

    def ensure_source_cv(self, alias, payload):
        del payload
        if self.fail_cv_once:
            self.fail_cv_once = False
            raise DemoCommandError("CV_IMPORT_FAILED", "injected failure")
        created = alias not in self.cvs
        self.cvs.setdefault(alias, 301)
        return {
            "business_id": "source-cv-301",
            "version_id": "source-cv-version-301",
            "created": created,
            "status": "pending",
            "cv_extraction_task_id": "cv-task-301",
        }

    def run_jd_chain(self, alias, version_id):
        del version_id
        if alias in self.fail_jd_chain_aliases:
            raise DemoCommandError("EXTRACTION_TASK_FAILED", f"{alias} injected failure")
        return {
            "alias": alias,
            "extraction_task_id": f"extract-{alias}",
            "parse_result_id": f"parse-{alias}",
            "jd_id": f"jd-{alias}",
            "document_id": f"jd-{alias}",
            "publication_id": f"publication-{alias}",
            "provider": "deepseek",
            "execution_mode": "llm",
            "kg_sync_status": "synced",
            "knowledge_graph_id": f"kg-jd-{alias}",
        }

    def run_cv_chain(self, alias, cv_extraction_task_id):
        del cv_extraction_task_id
        return {
            "alias": alias,
            "cv_extraction_task_id": f"cv-task-{alias}",
            "validated_cv_snapshot_id": f"snapshot-{alias}",
            "resume_id": f"resume-{alias}",
            "provider": "deepseek",
        }

    def build_graph_version(
        self, *, position_id, window_start, window_end, version_name, version_number
    ):
        del window_start, window_end, version_number
        return {
            "graph_version_id": int(version_name[-1]),
            "build_run_id": 100 + int(version_name[-1]),
            "position_id": position_id,
            "version_name": version_name,
        }

    def run_matching(self, *, resume_id, target_id, graph_version):
        del resume_id, target_id, graph_version
        return {
            "task_id": "matching-task-1",
            "evaluation_id": "evaluation-1",
            "cv_profile_version": "cv-profile-v1",
            "position_profile_version": "position-profile-v1",
            "graph_version": "2026-q2",
        }

    def resolve_kg_position_id(self, position_id: str) -> str:
        return f"kg-position-{position_id}"

    def fetch_jd_evidence(self, **kwargs):
        return [
            {
                "evidence_id": f"demo:{kwargs['source_jd_id']}:jd:0",
                "business_object_type": "standard_position",
                "business_object_id": kwargs["business_object_id"],
                "evidence_type": "jd_evidence",
                "source_object_type": "source_jd",
                "source_object_id": kwargs["source_jd_id"],
                "source_document_id": kwargs["jd_id"],
                "source_version": kwargs["source_jd_version_id"],
                "text": "编写 Python 后端模块并维护单元测试。",
                "quote": "编写 Python 后端模块并维护单元测试。",
                "location_start": 0,
                "location_end": 22,
                "occurrence_index": 0,
                "alignment": "exact",
                "graph_version": kwargs["graph_version"],
                "tenant_ref": "jobgraph-platform-public",
                "permission_scope": "platform:public",
            }
        ]

    def fetch_cv_evidence(self, **kwargs):
        return [
            {
                "evidence_id": f"demo:{kwargs['source_cv_id']}:cv:0",
                "business_object_type": "cv_profile",
                "business_object_id": kwargs["business_object_id"],
                "evidence_type": "cv_evidence",
                "source_object_type": "source_cv",
                "source_object_id": kwargs["source_cv_id"],
                "source_document_id": kwargs["source_cv_id"],
                "source_version": kwargs["source_cv_version_id"],
                "text": "编写 Python 后端模块并维护单元测试。",
                "quote": "编写 Python 后端模块并维护单元测试。",
                "location_start": 0,
                "location_end": 22,
                "occurrence_index": 0,
                "alignment": "exact",
                "graph_version": kwargs["graph_version"],
                "tenant_ref": "jobgraph-platform-public",
                "permission_scope": "platform:public",
            }
        ]

    def index_rag(self, items):
        self.indexed_rag_calls.append(items)
        return {
            "contract_version": "evidence-rag-index.v1",
            "indexed_count": len(items),
        }

    def delete_rag(self, *, tenant_ref, permission_scope):
        self.deleted_rag_scopes.append((tenant_ref, permission_scope))
        return {"deleted": True}

    def query_rag(self, payload):
        self.query_rag_calls.append(payload)
        if "CPA" in payload["query_text"]:
            return {
                "contract_version": "evidence-rag-response.v1",
                "status": "insufficient_evidence",
                "provider": "evidence_rag",
                "model": "unavailable",
                "model_version": "unavailable",
                "trace_id": "trace-insufficient",
                "error": {"code": "EVIDENCE_NOT_FOUND", "message": "no active Evidence"},
                "permission": {
                    "user_id": "demo",
                    "tenant_ref": "jobgraph-platform-public",
                    "permission_scope": "platform:public",
                },
            }
        return {
            "contract_version": "evidence-rag-response.v1",
            "status": "answered",
            "answer": "候选人具备核心技能。",
            "references": [
                {
                    "evidence_id": "demo:source-jd-201:jd:0",
                    "source_object_type": "source_jd",
                    "source_object_id": "source-jd-201",
                    "source_document_id": "jd-jd-demo-001",
                    "quote": "编写 Python 后端模块并维护单元测试。",
                    "location_start": 0,
                    "location_end": 22,
                    "occurrence_index": 0,
                    "alignment": "exact",
                    "graph_version": "2026-q2",
                    "source_version": "source-jd-version-201",
                    "tenant_ref": "jobgraph-platform-public",
                    "permission_scope": "platform:public",
                }
            ],
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "model_version": "deepseek-evidence-rag-answer.v1",
            "trace_id": "trace-success",
            "permission": {
                "user_id": "demo",
                "tenant_ref": "jobgraph-platform-public",
                "permission_scope": "platform:public",
            },
        }

    def remove_position(self, position_id):
        self.removed_positions.append(position_id)
        return {"position_id": position_id, "deleted": True}


def test_manifest_and_versioned_inputs_are_valid():
    manifest = load_manifest()
    inputs = load_inputs(manifest)

    assert manifest.implementation_status == "loadable_foundation"
    assert set(inputs) == {
        "jd-demo-001",
        "jd-demo-002",
        "jd-demo-003",
        "cv-demo-001",
    }
    assert [inputs[f"jd-demo-00{index}"]["publish_time"] for index in range(1, 4)] == [
        "2025-11-15T08:00:00Z",
        "2026-02-15T08:00:00Z",
        "2026-05-15T08:00:00Z",
    ]


def test_http_gateway_passes_business_publish_time_to_source_jd(monkeypatch):
    gateway = HttpDemoGateway()
    monkeypatch.setattr(gateway, "_main_auth", lambda: "token")
    captured = {}

    def request(method, url, *, body=None, token=None):
        captured.update({"method": method, "url": url, "body": body, "token": token})
        return {
            "code": 0,
            "data": {
                "source_jd_id": "source-jd",
                "source_jd_version_id": "source-jd-version",
                "created_source": True,
                "created_version": True,
            },
        }

    monkeypatch.setattr(gateway, "_request", request)
    payload = load_inputs(load_manifest())["jd-demo-001"]

    gateway.ensure_source_jd("jd-demo-001", payload)

    assert captured["body"]["publish_time_raw"] == "2025-11-15T08:00:00Z"


def test_preflight_keeps_later_dependencies_pending_without_blocking_foundation(
    monkeypatch,
):
    for name in (
        "COMPETITION_DEMO_MAIN_USERNAME",
        "COMPETITION_DEMO_MAIN_PASSWORD",
        "COMPETITION_DEMO_MAIN_PERSONAL_USERNAME",
        "COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD",
    ):
        monkeypatch.setenv(name, "present-for-test")
    gateway = HttpDemoGateway()

    def probe(url):
        if url == "http://127.0.0.1:8000/readiness":
            return {"status": "ready"}
        raise DemoCommandError("SERVICE_UNAVAILABLE", url)

    monkeypatch.setattr(gateway, "_probe", probe)

    result = gateway.preflight()

    assert result["missing_required_for_foundation"] == {
        "services": [],
        "configuration": [],
    }
    assert {"knowledge_graph", "jd_extraction", "cv_extraction"} <= set(
        result["pending_required_for_batch_2"]["services"]
    )


def test_preflight_fails_when_foundation_credentials_are_missing(monkeypatch):
    for name in (
        "COMPETITION_DEMO_MAIN_USERNAME",
        "COMPETITION_DEMO_MAIN_PASSWORD",
        "COMPETITION_DEMO_MAIN_PERSONAL_USERNAME",
        "COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    gateway = HttpDemoGateway()
    monkeypatch.setattr(gateway, "_probe", lambda url: {"status": "ready"})

    with pytest.raises(DemoCommandError) as exc_info:
        gateway.preflight()

    assert exc_info.value.code == "PREFLIGHT_REQUIRED_DEPENDENCY_MISSING"
    assert set(
        exc_info.value.details["missing_required_for_foundation"]["configuration"]
    ) == {
        "COMPETITION_DEMO_MAIN_USERNAME",
        "COMPETITION_DEMO_MAIN_PASSWORD",
        "COMPETITION_DEMO_MAIN_PERSONAL_USERNAME",
        "COMPETITION_DEMO_MAIN_PERSONAL_PASSWORD",
    }


def test_input_path_escape_fails_closed(tmp_path):
    dataset = tmp_path / "competition-demo-v1"
    dataset.mkdir()
    manifest_payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for item in (*manifest_payload["jds"], *manifest_payload["cvs"]):
        source = MANIFEST_PATH.parent / item["input_path"]
        target = dataset / item["input_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    outside = tmp_path / "outside.json"
    outside.write_text('{"alias":"jd-demo-001"}', encoding="utf-8")
    manifest_payload["jds"][0]["input_path"] = "../outside.json"
    manifest_path = dataset / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest_payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(DemoCommandError, match="escapes") as exc_info:
        load_inputs(load_manifest(manifest_path), manifest_path)

    assert exc_info.value.code == "INPUT_PATH_INVALID"


def test_load_is_idempotent_and_verify_marks_graph_versions_pending(tmp_path):
    gateway = FakeGateway()
    loader = CompetitionDemoLoader(gateway, state_path=tmp_path / "runtime-map.json")

    first = loader.load()
    second = loader.load()

    assert first["status"] == "foundation_verified"
    assert second["status"] == "foundation_verified"
    assert {
        item["alias"]
        for item in first["pending_resources"]
        if item["resource_type"] == "graph_version"
    } == {"graph-2025-q4", "graph-2026-q1", "graph-2026-q2"}
    assert len(gateway.positions) == 1
    assert len(gateway.jds) == 3
    assert len(gateway.cvs) == 1


def test_verify_projects_only_recorded_batch_2_resources_as_completed(tmp_path):
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(FakeGateway(), state_path=state_path)
    loader.load()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["batch_2_results"] = {
        "jd-demo-001": {
            "extraction_task_id": "extract-1",
            "publication_id": "publication-1",
        },
        "graph-2025-q4": {"graph_version_id": 1},
        "graph-2026-q2": {"graph_version_id": 3},
        "position-profile-ai-app-engineer": {"position_id": "position-kg-1"},
        "cv-demo-001": {
            "cv_extraction_task_id": "cv-task-1",
            "validated_cv_snapshot_id": "snapshot-1",
        },
        "matching-demo-001": {
            "task_id": "matching-task-1",
            "evaluation_id": "evaluation-1",
            "cv_profile_version": "cv-profile-v1",
            "position_profile_version": "position-profile-v1",
        },
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    verified = loader.verify()

    completed = {
        item["alias"]: item for item in verified["completed_resources"]
    }
    pending = {item["alias"] for item in verified["pending_resources"]}
    assert verified["batch_2_status"] == "partial"
    assert completed["extracted-jd-001"]["business_id"] == "extract-1"
    assert completed["published-jd-fact-001"]["business_id"] == "publication-1"
    assert completed["graph-2025-q4"]["business_id"] == 1
    assert (
        completed["position-profile-ai-app-engineer"]["business_id"]
        == "position-kg-1"
    )
    assert completed["validated-cv-snapshot-demo-001"]["business_id"] == "snapshot-1"
    assert completed["cv-match-profile-demo-001"]["business_id"] == "cv-profile-v1"
    assert (
        completed["position-match-profile-ai-app-engineer"]["business_id"]
        == "position-profile-v1"
    )
    assert (
        completed["matching-evaluation-ai-app-engineer"]["business_id"]
        == "evaluation-1"
    )
    assert "cv-match-profile-demo-001" not in pending
    assert "matching-evaluation-ai-app-engineer" not in pending


def test_run_batch2_records_chain_and_marks_completed(tmp_path):
    gateway = FakeGateway()
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()

    verified = loader.run_batch2()

    completed = {
        item["alias"]: item for item in verified["completed_resources"]
    }
    assert verified["batch_2_status"] == "partial"
    assert verified["rag_status"] == "not_started"
    assert {alias for alias in completed if alias.startswith("extracted-jd-")} == {
        "extracted-jd-001",
        "extracted-jd-002",
        "extracted-jd-003",
    }
    assert completed["graph-2026-q2"]["business_id"] == 2
    assert completed["matching-evaluation-ai-app-engineer"]["business_id"] == "evaluation-1"
    assert completed["validated-cv-snapshot-demo-001"]["business_id"] == "snapshot-cv-demo-001"


def test_run_batch2_partial_failure_preserves_results_and_retry_completes(tmp_path):
    gateway = FakeGateway()
    gateway.fail_jd_chain_aliases.add("jd-demo-003")
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()

    with pytest.raises(DemoCommandError, match="injected failure"):
        loader.run_batch2()

    partial = json.loads(state_path.read_text(encoding="utf-8"))
    assert partial["status"] == "batch_2_partial_failure"
    assert "jd-demo-001" in partial["batch_2_results"]
    assert "jd-demo-003" not in partial["batch_2_results"]

    gateway.fail_jd_chain_aliases.clear()
    verified = loader.run_batch2()
    assert verified["batch_2_status"] == "partial"
    assert verified["completed_resources"]


def test_index_rag_requires_batch2(tmp_path):
    gateway = FakeGateway()
    loader = CompetitionDemoLoader(gateway, state_path=tmp_path / "runtime-map.json")
    loader.load()

    with pytest.raises(DemoCommandError) as exc_info:
        loader.index_rag()

    assert exc_info.value.code == "BATCH_2_REQUIRED"


def test_index_rag_and_query_rag_reach_ready(tmp_path):
    gateway = FakeGateway()
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()
    loader.run_batch2()

    indexed = loader.index_rag()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["rag_index"]["item_count"] == 4
    assert state["rag_index"]["tenant_ref"] == "jobgraph-platform-public"
    assert indexed["rag_status"] == "indexed"

    verified = loader.query_rag()
    assert verified["rag_status"] == "ready"
    assert verified["batch_2_status"] == "completed"
    completed = {item["alias"]: item for item in verified["completed_resources"]}
    assert completed["rag-response-success"]["business_id"] == "trace-success"
    assert (
        completed["rag-response-insufficient"]["business_id"]
        == "trace-insufficient"
    )
    assert len(gateway.query_rag_calls) == 2


def test_remove_demo_only_deletes_rag_scope(tmp_path):
    gateway = FakeGateway()
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()
    loader.run_batch2()
    loader.index_rag()

    result = loader.remove_demo_only()

    assert result["status"] == "removed"
    assert gateway.deleted_rag_scopes == [("jobgraph-platform-public", "platform:public")]
    assert any(item.get("scope") == "evidence_rag" for item in result["deleted_resources"])


def test_partial_failure_persists_inventory_and_retry_completes(tmp_path):
    gateway = FakeGateway(fail_cv_once=True)
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)

    with pytest.raises(DemoCommandError, match="injected failure"):
        loader.load()

    partial = json.loads(state_path.read_text(encoding="utf-8"))
    assert partial["status"] == "partial_failure"
    assert "source-jd-001" in partial["resources"]
    assert "source-cv-demo-001" not in partial["resources"]

    verified = loader.load()
    assert verified["status"] == "foundation_verified"


def test_verify_rejects_missing_relation_endpoint(tmp_path):
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(FakeGateway(), state_path=state_path)
    loader.load()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    del state["resources"]["window-2026-q1"]
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(DemoCommandError) as exc_info:
        loader.verify()

    assert exc_info.value.code == "VERIFY_FAILED"
    assert "missing resources" in str(exc_info.value.details)


def test_remove_demo_only_deletes_only_registered_position(tmp_path):
    gateway = FakeGateway()
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()

    result = loader.remove_demo_only()

    assert result["status"] == "removed"
    assert gateway.removed_positions == ["101"]
    assert result["non_demo_resources_touched"] == 0
    assert {item["alias"] for item in result["retained_history"]} >= {
        "source-jd-001",
        "source-cv-demo-001",
    }


def test_verify_after_cleanup_reports_dataset_not_loaded(tmp_path):
    gateway = FakeGateway()
    state_path = tmp_path / "runtime-map.json"
    loader = CompetitionDemoLoader(gateway, state_path=state_path)
    loader.load()
    loader.remove_demo_only()

    with pytest.raises(DemoCommandError) as exc_info:
        loader.verify()

    assert exc_info.value.code == "DATASET_NOT_LOADED"

    reloaded = loader.load()
    assert reloaded["status"] == "foundation_verified"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "deleted_resources" not in state
