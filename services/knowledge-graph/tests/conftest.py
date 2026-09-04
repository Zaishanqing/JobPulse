import os
os.environ["DATABASE_URL"]="sqlite://"
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
from app.main import app
from app.auth import create_token, hash_password
from app.models import User
from app import models  # noqa

@pytest.fixture
def db():
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    @event.listens_for(engine,"connect")
    def enable_foreign_keys(connection,_record): connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine); session=sessionmaker(bind=engine,expire_on_commit=False)()
    yield session; session.close(); Base.metadata.drop_all(engine); engine.dispose()
@pytest.fixture
def client(db):
    from app.application import BuildGraphUseCase, BuildJobUseCase
    from app.application.build_job_runner import BuildJobRunner
    from app.infrastructure.sqlalchemy import SqlAlchemyUnitOfWork

    original_factory = app.state.request_session_factory
    original_close = app.state.close_request_sessions
    original_inline = app.state.settings.build_jobs_inline
    original_runner = app.state.build_job_runner
    app.state.request_session_factory = lambda: db
    app.state.close_request_sessions = False
    app.state.settings.build_jobs_inline = True
    def uow_factory():
        return SqlAlchemyUnitOfWork(lambda: db, close_session=False)
    app.state.build_job_runner = BuildJobRunner(
        BuildJobUseCase(uow_factory), BuildGraphUseCase(uow_factory), "pytest-worker"
    )
    with TestClient(app) as c:
        yield c
    app.state.request_session_factory = original_factory
    app.state.close_request_sessions = original_close
    app.state.settings.build_jobs_inline = original_inline
    app.state.build_job_runner = original_runner

@pytest.fixture
def users(db, test_password_hash):
    values={}
    for role in ("personal_user","enterprise_user","reviewer","admin","developer"):
        user=User(username=role,password_hash=test_password_hash,role=role); db.add(user); values[role]=user
    db.commit(); return values

@pytest.fixture
def integration_service_user(db, test_password_hash):
    user=User(username="integration_developer",password_hash=test_password_hash,role="integration_service")
    db.add(user); db.commit(); return user

@pytest.fixture(scope="session")
def test_password_hash():
    return hash_password("secret")

@pytest.fixture
def auth_headers(users):
    def headers(role="admin"): return {"Authorization":f"Bearer {create_token(users[role], app.state.settings)}"}
    return headers

@pytest.fixture
def integration_service_headers(integration_service_user):
    return {"Authorization":f"Bearer {create_token(integration_service_user, app.state.settings)}"}
