from __future__ import annotations

from sqlalchemy import select

from app.core.database import Base, create_database
from app.models.enterprise import Enterprise
from app.models.jd import JobDescription
from app.models.resume import Resume
from app.models.standard_position import StandardPosition
from app.models.user import User
from scripts.seed_bootstrap_accounts import seed


def test_bootstrap_seed_creates_only_login_identities(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bootstrap.db'}"
    database = create_database(database_url)
    Base.metadata.create_all(database.engine)
    database.dispose()

    assert seed(database_url) == {"configured_accounts": 3, "created": 3}
    assert seed(database_url) == {"configured_accounts": 3, "created": 0}

    database = create_database(database_url)
    try:
        with database.session_factory() as session:
            assert len(session.scalars(select(User)).all()) == 3
            assert session.scalars(select(Enterprise)).all() == []
            assert session.scalars(select(JobDescription)).all() == []
            assert session.scalars(select(Resume)).all() == []
            assert session.scalars(select(StandardPosition)).all() == []
    finally:
        database.dispose()
