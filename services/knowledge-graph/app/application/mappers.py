from __future__ import annotations

from copy import deepcopy

from app.domain.value_types import JsonValue, SerializedPayload

from app.application.errors import StructuredFactsIncompleteError


class ExtractionMapper:
    """Build the V2 DTO exclusively from authoritative structured extraction facts."""

    @staticmethod
    def to_response(payload: SerializedPayload) -> SerializedPayload:
        return deepcopy(payload)

    @staticmethod
    def from_structured_facts(
        *, document_id: str, job_title: SerializedPayload | None,
        responsibilities: list[SerializedPayload],
        requirements: list[SerializedPayload],
        company_facts: list[SerializedPayload],
        employment_facts: list[SerializedPayload],
        evidence_rows: list[SerializedPayload],
    ) -> SerializedPayload:
        evidence = {
            (row["owner_type"], row["owner_ref"]): deepcopy(row)
            for row in evidence_rows
        }

        def sourced(
            value: SerializedPayload, owner_type: str, owner_ref: str
        ) -> SerializedPayload:
            row = evidence.get((owner_type, owner_ref))
            if row is None:
                raise StructuredFactsIncompleteError(
                    f"structured extraction evidence missing: {owner_type}/{owner_ref}"
                )
            result = deepcopy(value)
            result.pop("evidence", None)
            result["evidence"] = {
                "source_id": document_id,
                **{key: row.get(key) for key in (
                    "quote", "start", "end", "alignment", "occurrence_index"
                )},
            }
            return result

        title = None
        if job_title is not None:
            title = sourced(job_title, "job_title", "job_title")
        return {
            "document_id": document_id,
            "job_title": title,
            "responsibilities": [
                sourced(item, "task", item["requirement_id"])
                for item in responsibilities
            ],
            "requirements": [
                sourced(item, "requirement", item["requirement_id"])
                for item in requirements
            ],
            "company_facts": [
                sourced(item, "company_fact", item["fact_id"])
                for item in company_facts
            ],
            "employment_facts": [
                sourced(item, "employment_fact", item["fact_id"])
                for item in employment_facts
            ],
        }


class NormalizationMapper:
    """Rebuilds the mutable normalization response from structured facts."""

    @staticmethod
    def to_response(payload: SerializedPayload) -> SerializedPayload:
        return deepcopy(payload)


class GraphSnapshotMapper:
    FIELDS = (
        "position_id", "position", "time_window", "sample_stats",
        "skill_relations", "requirement_profile", "responsibilities",
        "company_context", "employment_context", "evidence_summary",
        "algorithm_metadata", "normalization_metadata", "release_notes",
    )

    @classmethod
    def to_current(cls, snapshot: SerializedPayload) -> SerializedPayload:
        return {key: deepcopy(snapshot[key]) for key in cls.FIELDS if key in snapshot}


class GraphSnapshotCompatibilityMapper:
    """Reads historical aliases and emits only the current snapshot contract."""

    @staticmethod
    def to_current(snapshot: SerializedPayload) -> SerializedPayload:
        current = deepcopy(snapshot)
        if "skill_relations" not in current:
            current["skill_relations"] = deepcopy(current.get("skills", []))
        if "responsibilities" not in current:
            current["responsibilities"] = deepcopy(current.get("task_profile", []))
        if "algorithm_metadata" not in current:
            current["algorithm_metadata"] = deepcopy(current.get("algorithm_config", {}))
        for legacy in ("skills", "task_profile", "algorithm_config"):
            current.pop(legacy, None)
        return GraphSnapshotMapper.to_current(current)


class ResponseMapper:
    @staticmethod
    def envelope(data: JsonValue, trace_id: str) -> SerializedPayload:
        return {"code": 0, "message": "success", "data": data, "trace_id": trace_id}
