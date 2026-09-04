import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from uuid import uuid4

from app.config import ROOT
from app.models import GraphBuildRun, GraphVersion, StandardPosition
from tests.factories import valid_build

def publish(client,db,auth_headers):
    build=valid_build(client,db,auth_headers()); response=client.post(f'/api/v1/graph/build-runs/{build["build_run_id"]}/publish',json={},headers=auth_headers()); assert response.status_code==200
    return db.get(GraphVersion,response.json()["data"]["version_id"])

def test_published_version_cannot_update_or_delete(client,db,auth_headers):
    version=publish(client,db,auth_headers); version.version_name="tampered"
    with pytest.raises(ValueError): db.commit()
    db.rollback(); version=db.get(GraphVersion,version.id); db.delete(version)
    with pytest.raises(ValueError): db.commit()
    db.rollback(); assert db.get(GraphVersion,version.id) is not None

def test_duplicate_version_number_and_rollback_history(client,db,auth_headers,users):
    version=publish(client,db,auth_headers); duplicate=GraphVersion(position_id=version.position_id,build_run_id=version.build_run_id,version_number=version.version_number,version_name="duplicate",snapshot=version.snapshot,source_version=version.source_version,algorithm_version=version.algorithm_version,normalization_map_version=version.normalization_map_version,published_by=users["admin"].id)
    db.add(duplicate)
    with pytest.raises(IntegrityError): db.commit()
    db.rollback(); response=client.post(f"/api/v1/positions/{version.position_id}/graph/versions/{version.id}/rollback",json={"reason":"restore immutable version"},headers=auth_headers())
    assert response.status_code==200 and response.json()["data"]["version_number"]==2
    assert len(db.scalars(select(GraphVersion)).all())==2


@pytest.fixture
def migrated_version_db(monkeypatch):
    temp_root = ROOT / ".test-artifacts" / "version-immutability"
    temp_root.mkdir(parents=True, exist_ok=True)
    database = temp_root / f"immutable-{uuid4().hex}.db"
    url = f"sqlite:///{database.as_posix()}"
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    command.upgrade(config, "head")
    engine = create_engine(url)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    position = StandardPosition(
        position_id="POS_IMMUTABLE",
        name="不可变测试岗位",
        category_code="TEST",
    )
    db.add(position)
    db.flush()
    build = GraphBuildRun(
        position_id=position.position_id,
        status="published",
        config_snapshot={},
        summary={},
    )
    db.add(build)
    db.flush()
    version = GraphVersion(
        position_id=position.position_id,
        build_run_id=build.id,
        version_number=1,
        version_name="v1",
        snapshot={"skills": [{"skill_id": "ORIGINAL"}]},
        source_version="test-source-v1",
        algorithm_version="test-v1",
        normalization_map_version="test-v1",
    )
    db.add(version)
    db.commit()
    yield db, version.id
    db.close()
    engine.dispose()
    for path in temp_root.glob(f"{database.name}*"):
        path.unlink(missing_ok=True)


def test_graph_version_core_update_rejected(migrated_version_db):
    db, version_id = migrated_version_db
    with pytest.raises(IntegrityError):
        db.execute(
            update(GraphVersion)
            .where(GraphVersion.id == version_id)
            .values(snapshot={"mutated": True})
        )
        db.commit()
    db.rollback()
    assert db.get(GraphVersion, version_id).snapshot == {
        "skills": [{"skill_id": "ORIGINAL"}]
    }

    with pytest.raises(IntegrityError):
        db.query(GraphVersion).filter(GraphVersion.id == version_id).update(
            {GraphVersion.version_name: "bulk-mutated"},
            synchronize_session=False,
        )
        db.commit()
    db.rollback()
    assert db.get(GraphVersion, version_id).version_name == "v1"


def test_graph_version_delete_rejected(migrated_version_db):
    db, version_id = migrated_version_db
    with pytest.raises(IntegrityError):
        db.execute(delete(GraphVersion).where(GraphVersion.id == version_id))
        db.commit()
    db.rollback()
    assert db.get(GraphVersion, version_id) is not None
