from app.api.fact_mappers import extraction_facts
from app.application.mappers import (
    ExtractionMapper,
    GraphSnapshotCompatibilityMapper,
    GraphSnapshotMapper,
    NormalizationMapper,
)
from app.infrastructure.sqlalchemy.structured_fact_mappers import extraction_schema
from app.schemas.extraction import JDExtractionResult


def test_payload_mappers_do_not_mutate_audit_inputs():
    payload = {"requirements": [{"text": "original"}]}
    extraction = ExtractionMapper.to_response(payload)
    normalization = NormalizationMapper.to_response(payload)
    extraction["requirements"][0]["text"] = "changed"
    normalization["requirements"].append({"text": "new"})
    assert payload == {"requirements": [{"text": "original"}]}


def test_new_snapshot_has_one_canonical_field_per_concept():
    snapshot = GraphSnapshotMapper.to_current({
        "skill_relations": [{"skill_id": "PY"}],
        "responsibilities": [{"text": "build"}],
        "algorithm_metadata": {"version": "v1"},
        "skills": [{"skill_id": "OLD"}],
        "task_profile": [{"text": "old"}],
        "algorithm_config": {"version": "old"},
    })
    assert snapshot["skill_relations"][0]["skill_id"] == "PY"
    assert not ({"skills", "task_profile", "algorithm_config"} & snapshot.keys())


def test_old_snapshot_is_read_through_compatibility_mapper():
    current = GraphSnapshotCompatibilityMapper.to_current({
        "skills": [{"skill_id": "PY"}],
        "task_profile": [{"text": "build"}],
        "algorithm_config": {"version": "v1"},
    })
    assert current == {
        "skill_relations": [{"skill_id": "PY"}],
        "responsibilities": [{"text": "build"}],
        "algorithm_metadata": {"version": "v1"},
    }


def test_structured_non_skill_requirements_survive_kg_fact_mapping():
    evidence = {"source_id": "JD1", "quote": "结构化要求"}
    source = JDExtractionResult.model_validate(
        {
            "document_id": "JD1",
            "requirements": [
                {
                    "requirement_id": "EDU1",
                    "kind": "education",
                    "modality": "required",
                    "evidence": evidence,
                    "text": "本科及以上，计算机相关专业",
                    "minimum_degree": "bachelor",
                    "majors": ["计算机相关专业"],
                },
                {
                    "requirement_id": "EXP1",
                    "kind": "experience",
                    "modality": "required",
                    "evidence": evidence,
                    "text": "3-5 年 Python 后端经验",
                    "minimum_years": 3,
                    "maximum_years": 5,
                },
                {
                    "requirement_id": "CERT1",
                    "kind": "certificate",
                    "modality": "preferred",
                    "evidence": evidence,
                    "text": "持有软考证书优先",
                    "certificates": ["软考证书"],
                },
                {
                    "requirement_id": "SOFT1",
                    "kind": "soft_skill",
                    "modality": "required",
                    "evidence": evidence,
                    "text": "具备沟通协作能力",
                    "skills": ["沟通", "协作"],
                },
            ],
        }
    )

    restored = extraction_schema(extraction_facts(source)).model_dump(mode="json")
    requirements = {item["kind"]: item for item in restored["requirements"]}

    assert requirements["education"]["minimum_degree"] == "bachelor"
    assert requirements["education"]["majors"] == ["计算机相关专业"]
    assert requirements["experience"]["minimum_years"] == 3
    assert requirements["experience"]["maximum_years"] == 5
    assert requirements["certificate"]["certificates"] == ["软考证书"]
    assert requirements["soft_skill"]["skills"] == ["沟通", "协作"]
