from __future__ import annotations

import ast
import importlib
import inspect
from dataclasses import fields, replace
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from app import main as main_module
from app.api.dependencies.container import get_application_container
from app.application_container import ApplicationContainer
from app.bootstrap import container as bootstrap
from app.core.config import settings
from app.main import app, create_app


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_STATE_NAMES = {
    "database",
    "db",
    "engine",
    "session",
    "session_factory",
    "uow",
    "repository",
    "settings",
    "runtime",
}


def _request(application) -> Request:
    return Request(
        {
            "type": "http",
            "app": application,
            "method": "GET",
            "path": "/",
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_lifespan_state_exposes_only_application_container():
    application = create_app(application_container=app.state.container)

    assert application.state._state == {}
    with TestClient(application):
        assert application.state._state == {"container": app.state.container}
        assert isinstance(application.state.container, ApplicationContainer)
        assert FORBIDDEN_STATE_NAMES.isdisjoint(application.state._state)

        request = _request(application)
        with pytest.raises(AttributeError):
            _ = request.app.state.database
        with pytest.raises(AttributeError):
            _ = request.app.state.runtime
        assert get_application_container(request) is app.state.container

    assert application.state._state == {}


def test_application_container_has_only_high_level_public_fields():
    container = app.state.container
    public_fields = {field.name: getattr(container, field.name) for field in fields(container)}

    assert public_fields
    assert FORBIDDEN_STATE_NAMES.isdisjoint(public_fields)
    assert not hasattr(container, "get")
    for name, value in public_fields.items():
        module = type(value).__module__
        assert module.startswith(("app.application", "app.contexts")), (name, module)
        assert not module.startswith(("app.infrastructure", "sqlalchemy"))
        assert not any(
            marker in type(value).__name__.casefold()
            for marker in ("database", "repository", "unitofwork", "session")
        )


def test_formal_api_dependencies_only_read_the_application_container():
    dependency_root = ROOT / "app" / "api" / "dependencies"
    dependency_files = [
        path for path in dependency_root.glob("*.py") if path.name != "__init__.py"
    ]
    forbidden_imports = ("sqlalchemy", "app.infrastructure", "app.core.database")
    violations = {
        str(path.relative_to(ROOT)): sorted(
            module
            for module in _imports(path)
            if module.startswith(forbidden_imports)
        )
        for path in dependency_files
    }
    assert {path: modules for path, modules in violations.items() if modules} == {}

    state_readers = [
        path
        for path in dependency_files
        if "request.app.state" in path.read_text(encoding="utf-8")
    ]
    assert state_readers == [dependency_root / "container.py"]

    request = _request(app)
    returned = []
    for path in dependency_files:
        module = importlib.import_module(
            ".".join(path.relative_to(ROOT).with_suffix("").parts)
        )
        for name, function in inspect.getmembers(module, inspect.isfunction):
            parameters = tuple(inspect.signature(function).parameters.values())
            if (
                name.startswith("get_")
                and len(parameters) == 1
                and parameters[0].name == "request"
            ):
                returned.append((name, function(request)))

    assert returned
    for name, value in returned:
        module = type(value).__module__
        assert module.startswith(("app.application", "app.contexts", "app.application_container")), (
            name,
            module,
        )
        assert not module.startswith(("app.infrastructure", "sqlalchemy"))


def test_runtime_is_created_once_closed_and_isolated_per_app(monkeypatch):
    source_container = app.state.container
    created = []
    closed = []

    class FakeRuntime:
        def __init__(self):
            self.container = replace(source_container)
            created.append(self)

        def close(self):
            closed.append(self)

    monkeypatch.setattr(main_module, "_build_runtime", lambda _settings: FakeRuntime())
    first = create_app()
    second = create_app()

    assert created == []
    with TestClient(first):
        assert len(created) == 1
        first_container = first.state.container
        assert closed == []
    assert closed == [created[0]]

    with TestClient(second):
        assert len(created) == 2
        second_container = second.state.container
        assert second_container is not first_container
    assert closed == created


def test_runtime_builder_disposes_database_when_container_startup_fails(monkeypatch):
    class FakeDatabase:
        disposed = False

        def dispose(self):
            self.disposed = True

    database = FakeDatabase()
    monkeypatch.setattr(bootstrap, "create_database", lambda _url: database)

    def fail_container(*_args, **_kwargs):
        raise RuntimeError("container startup failed")

    monkeypatch.setattr(bootstrap, "_build_application_container", fail_container)
    with pytest.raises(RuntimeError, match="container startup failed"):
        bootstrap._build_runtime(settings)
    assert database.disposed is True


def test_container_injection_does_not_build_or_expose_runtime(monkeypatch):
    def forbidden_builder(_settings):
        raise AssertionError("runtime must not be built for container injection")

    monkeypatch.setattr(main_module, "_build_runtime", forbidden_builder)
    application = create_app(application_container=app.state.container)
    with TestClient(application):
        assert application.state._state == {"container": app.state.container}
