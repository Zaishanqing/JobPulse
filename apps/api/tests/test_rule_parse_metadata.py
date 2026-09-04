from __future__ import annotations

from app.api.jd_mapping import task_data
from app.contexts.jd_lifecycle import TaskDTO


def _task_dto(result_payload: dict) -> TaskDTO:
    return TaskDTO(
        id="task-1",
        task_type="jd_parse",
        status="succeeded",
        progress=1.0,
        input_payload={"jd_id": "jd-1", "extraction_mode": "rule"},
        result_payload=result_payload,
        result_reference=None,
        error_code=None,
        error_message=None,
        created_by="user-1",
        attempt_count=1,
        log_entries=(),
        created_at=None,
        updated_at=None,
        started_at=None,
        finished_at=None,
    )


def test_new_rule_parse_metadata_is_deterministic_and_mock_free():
    data = task_data(
        _task_dto(
            {
                "execution_mode": "rule",
                "implementation_status": "deterministic_rule_jd_parse",
                "provider": "local_rules",
                "rule_based": True,
                "review_only": True,
                "algorithm_version": "jd-rule-v1",
            }
        )
    )
    assert data["execution_mode"] == "rule"
    assert data["capability_implementation_status"] == "deterministic_rule_jd_parse"
    assert data["review_only"] is True
    assert "mock" not in data
    assert "mock_keyword_jd_parse" not in str(data)


def test_legacy_rule_parse_metadata_is_mapped_without_mutating_payload():
    data = task_data(
        _task_dto(
            {
                "implementation_status": "mock_keyword_jd_parse",
                "provider": "local_rules",
                "algorithm_version": "jd-rule-v1",
                "mock": True,
                "rule_based": True,
            }
        )
    )
    # API projection normalizes legacy metadata...
    assert data["execution_mode"] == "rule"
    assert data["capability_implementation_status"] == "deterministic_rule_jd_parse"
    assert data["review_only"] is True
    # ...while the persisted payload is left untouched for compatibility.
    assert data["mock"] is True
    assert data["result_payload"]["mock"] is True
    assert data["result_payload"]["implementation_status"] == "mock_keyword_jd_parse"


def test_successful_llm_parse_task_metadata_is_not_rule():
    from app.contexts.jd_lifecycle import (
        Actor,
        JDParseResultDTO,
        JDSchemaView,
    )
    from app.domain.jd_policies import JDParseCommand
    from app.infrastructure.jd_repository import SqlAlchemyTaskRepository
    from app.models.task_record import TaskRecord
    from tests.runtime_database import SessionLocal, reset_database_data

    reset_database_data()
    try:
        result = JDParseResultDTO(
            id="parse-llm-1",
            jd_id="jd-llm-1",
            position_title="Backend",
            responsibilities=(),
            required_skills=(),
            bonus_skills=(),
            education=None,
            experience=None,
            industry=None,
            tools=(),
            business_scenarios=(),
            parse_confidence=0.9,
            need_review=True,
            extraction_result=None,
            normalized_result=None,
            execution_metadata=None,
            schema_version="v1",
            normalization_schema_version="nv1",
            workflow_status="draft",
            created_at=None,
            updated_at=None,
        )
        schema_view = JDSchemaView(
            extraction_result=None,
            normalized_result=None,
            extraction_status="missing",
            normalization_status="missing",
        )
        command = JDParseCommand(extraction_mode="llm", model="deepseek-test")
        with SessionLocal() as session:
            task = SqlAlchemyTaskRepository(session).create_succeeded_parse_task(
                Actor(id="user-1", role="admin"),
                command,
                result,
                schema_view,
            )
            session.commit()
            row = session.get(TaskRecord, task.id)
            payload = row.result_payload
        assert payload["execution_mode"] == "llm"
        assert payload["implementation_status"] == "llm_jd_parse"
        assert payload["rule_based"] is False
        assert payload["provider"] == "deepseek-test"
        assert "review_only" not in payload
    finally:
        reset_database_data()
