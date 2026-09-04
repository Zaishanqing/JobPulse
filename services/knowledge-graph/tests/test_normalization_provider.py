from sqlalchemy import event

from app.infrastructure.providers.normalization import Normalizer
from app.models import Skill, StandardPosition
from app.schemas.extraction import JDExtractionResult


def extraction(*, title="后端工程师", skill_names=("Python",)):
    requirements = [
        {
            "requirement_id": f"requirement-{index}",
            "kind": "skill",
            "modality": "required",
            "evidence": {"source_id": "NORMALIZATION_TEST", "quote": name},
            "items": [{"name": name}],
        }
        for index, name in enumerate(skill_names, start=1)
    ]
    return JDExtractionResult(
        document_id="NORMALIZATION_TEST",
        job_title=(
            {
                "text": title,
                "evidence": {
                    "source_id": "NORMALIZATION_TEST",
                    "quote": title,
                },
            }
            if title is not None
            else None
        ),
        requirements=requirements,
    )


def test_db_catalog_hits_active_rows_with_one_query_per_catalog(db):
    db.add_all(
        [
            StandardPosition(
                position_id="POS_BACKEND",
                name="后端工程师",
                category_code="TECH",
                status="active",
            ),
            Skill(
                skill_id="SKILL_PYTHON",
                canonical_name="Python",
                category_code="LANG",
                taxonomy_version="sha256:" + "a" * 64,
                status="active",
            ),
        ]
    )
    db.commit()
    statements = []

    @event.listens_for(db.get_bind(), "before_cursor_execute")
    def collect_statements(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    normalizer = Normalizer(session_factory=lambda: db)
    normalized = normalizer.normalize(extraction())
    normalizer.normalize(extraction(skill_names=("Python", "Python")))

    assert normalized.job_classification.position_id is None
    assert normalized.job_classification.classification_status == "catalog_gap"
    assert normalized.normalized_requirements[0].normalized_skills[0].skill_id == "SKILL_PYTHON"
    assert normalized.normalized_requirements[0].normalized_skills[0].resolution_source == "canonical_name"
    assert sum("FROM skills" in statement for statement in statements) == 1
    assert sum("FROM standard_positions" in statement for statement in statements) == 0


def test_db_catalog_ignores_inactive_rows_and_reports_misses(db):
    db.add_all(
        [
            StandardPosition(
                position_id="POS_INACTIVE",
                name="后端工程师",
                category_code="TECH",
                status="inactive",
            ),
            Skill(
                skill_id="SKILL_INACTIVE",
                canonical_name="Python",
                category_code="LANG",
                status="inactive",
            ),
        ]
    )
    db.commit()

    normalized = Normalizer(session_factory=lambda: db).normalize(extraction())

    assert normalized.job_classification.classification_status == "catalog_gap"
    assert normalized.normalized_requirements[0].normalized_skills[0].resolution_status == "unresolved"
    assert {item.reason for item in normalized.unresolved_items} == {
        "no exact normalized mapping",
        "authoritative position-taxonomy.v3 classification required",
    }


def test_db_catalog_name_collisions_are_unresolved_conflicts(db):
    db.add_all(
        [
            StandardPosition(
                position_id="POS_BACKEND_A",
                name="后端工程师",
                category_code="TECH",
                status="active",
            ),
            StandardPosition(
                position_id="POS_BACKEND_B",
                name=" 后端工程师 ",
                category_code="TECH",
                status="active",
            ),
            Skill(
                skill_id="SKILL_PYTHON_A",
                canonical_name="Python",
                category_code="LANG",
                status="active",
            ),
            Skill(
                skill_id="SKILL_PYTHON_B",
                canonical_name=" python ",
                category_code="LANG",
                status="active",
            ),
        ]
    )
    db.commit()

    normalized = Normalizer(session_factory=lambda: db).normalize(extraction())

    assert normalized.job_classification.position_id is None
    assert normalized.normalized_requirements[0].normalized_skills[0].skill_id is None
    skill_items = [
        item for item in normalized.unresolved_items if item.item_type == "skill"
    ]
    assert len(skill_items) == 1
    assert "catalog conflict" in skill_items[0].reason


def test_yaml_and_db_catalog_conflict_is_unresolved(db, tmp_path):
    yaml_path = tmp_path / "normalization_map.yaml"
    yaml_path.write_text(
        "version: conflict-test\nskills:\n"
        "  python: {skill_id: YAML_PYTHON, canonical_name: Python, category_code: LANG}\n"
        "positions: {}\n",
        encoding="utf-8",
    )
    db.add(
        Skill(
            skill_id="DB_PYTHON",
            canonical_name="Python",
            category_code="LANG",
            status="active",
        )
    )
    db.commit()

    normalized = Normalizer(
        path=yaml_path,
        session_factory=lambda: db,
    ).normalize(extraction(title=None))

    skill = normalized.normalized_requirements[0].normalized_skills[0]
    assert skill.skill_id is None
    assert normalized.unresolved_items[0].reason == (
        "normalization catalog conflict: multiple skill candidates"
    )


def test_yaml_normalized_name_collisions_are_unresolved(db, tmp_path):
    yaml_path = tmp_path / "normalization_map.yaml"
    yaml_path.write_text(
        "version: duplicate-test\nskills:\n"
        "  Python: {skill_id: YAML_PYTHON_A, canonical_name: Python, category_code: LANG}\n"
        "  ' python ': {skill_id: YAML_PYTHON_B, canonical_name: Python, category_code: LANG}\n"
        "positions: {}\n",
        encoding="utf-8",
    )

    normalized = Normalizer(
        path=yaml_path,
        session_factory=lambda: db,
    ).normalize(extraction(title=None))

    skill = normalized.normalized_requirements[0].normalized_skills[0]
    assert skill.skill_id is None
    assert normalized.unresolved_items[0].reason == (
        "normalization catalog conflict: multiple skill candidates"
    )


def test_catalog_cache_is_scoped_to_each_normalizer_instance(db):
    db.add(
        Skill(
            skill_id="SKILL_FIRST",
            canonical_name="Python",
            category_code="LANG",
            status="active",
        )
    )
    db.commit()
    first = Normalizer(session_factory=lambda: db)
    assert first.normalize(extraction(title=None)).normalized_requirements[0].normalized_skills[0].skill_id == "SKILL_FIRST"

    db.execute(
        Skill.__table__
        .update()
        .where(Skill.skill_id == "SKILL_FIRST")
        .values(status="inactive")
    )
    db.add(
        Skill(
            skill_id="SKILL_SECOND",
            canonical_name="Python",
            category_code="LANG",
            status="active",
        )
    )
    db.commit()
    second = Normalizer(session_factory=lambda: db)

    assert first.normalize(extraction(title=None)).normalized_requirements[0].normalized_skills[0].skill_id == "SKILL_FIRST"
    assert second.normalize(extraction(title=None)).normalized_requirements[0].normalized_skills[0].skill_id == "SKILL_SECOND"
