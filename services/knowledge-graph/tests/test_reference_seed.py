from app.config import Settings
from app.database import Base, create_database
from app.models import JDDocument, Skill
from scripts.seed_reference_data import seed


def test_reference_seed_is_idempotent_and_creates_no_demo_data(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'reference-seed.db'}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    database = create_database(Settings.from_env())
    Base.metadata.create_all(database.engine)
    database.engine.dispose()

    seed()
    seed()

    database = create_database(Settings.from_env())
    with database.session_factory() as session:
        assert session.query(JDDocument).count() == 0
        assert session.query(Skill).count() == 0
    database.engine.dispose()
