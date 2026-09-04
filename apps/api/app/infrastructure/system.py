from datetime import datetime, timezone

import httpx
from sqlalchemy import make_url, text

from app.core.config import Settings
from app.core.database import Database
from app.integrations.registry import IntegrationRegistry
from app.models.evaluation import EvaluationDataset, EvaluationReport
from app.models.system_config import SystemConfig
from app.contexts.platform import SystemConfigRecord
from app.domain.json_types import FrozenJsonObject, freeze_json_object
from sqlalchemy.orm import Session, sessionmaker


class SqlAlchemySystemStatusAdapter:
    def __init__(self, database: Database, settings: Settings, integrations: IntegrationRegistry) -> None:
        self._database = database
        self._settings = settings
        self._integrations = integrations

    def readiness(self) -> tuple[bool, FrozenJsonObject]:
        configuration = self._runtime_configuration()
        try:
            with self._database.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            return False, freeze_json_object({
                "status": "not_ready",
                "checks": {"database": {"ready": False}},
                "configuration": configuration,
            })
        extraction_ready, extraction_error = self._extraction_readiness()
        return True, freeze_json_object({
            "status": "ready",
            "checks": {
                "database": {"ready": True},
                "jd_extraction": {
                    "rule": {
                        "ready": True,
                        "provider": "rule_based_jd_extraction",
                        "requires_review": True,
                    },
                    "llm": {
                        "ready": extraction_ready,
                        "provider": "http_jd_extraction",
                        "optional": True,
                        "error_code": extraction_error,
                    },
                },
            },
            "configuration": configuration,
        })

    def _runtime_configuration(self) -> FrozenJsonObject:
        return freeze_json_object({
            "data_validation_mode": self._settings.DATA_VALIDATION_MODE,
            "crawler_data_exchange": "offline_bundle",
            "cv_extraction_enabled": self._settings.CV_EXTRACTION_ENABLED,
        })

    def _extraction_readiness(self) -> tuple[bool, str | None]:
        base_url = self._settings.JD_EXTRACTION_BASE_URL
        token = self._settings.JD_EXTRACTION_INTERNAL_TOKEN
        if not base_url or not token:
            return False, "extraction_not_configured"
        try:
            response = httpx.get(
                f"{base_url.rstrip('/')}/readiness",
                timeout=httpx.Timeout(
                    self._settings.JD_EXTRACTION_READ_TIMEOUT_SECONDS,
                    connect=self._settings.JD_EXTRACTION_CONNECT_TIMEOUT_SECONDS,
                ),
            )
            response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            return False, "extraction_unavailable"
        return True, None

    def database(self) -> FrozenJsonObject:
        with self._database.session_factory() as session:
            try:
                session.execute(text("select 1"))
                db_status, error = "ok", None
            except Exception as exc:  # pragma: no cover - reports the real provider failure
                db_status, error = "error", exc.__class__.__name__
            configured_url = make_url(self._settings.DATABASE_URL)
            redacted_url = (
                "sqlite:///<redacted>"
                if configured_url.get_backend_name() == "sqlite"
                else configured_url.render_as_string(hide_password=True)
            )
            return freeze_json_object({
                "status": db_status,
                "database_url": redacted_url,
                "dialect": session.bind.dialect.name if session.bind else None,
                "checks": {"select_1": db_status == "ok"},
                "counts": {
                    "evaluation_datasets": session.query(EvaluationDataset).count() if db_status == "ok" else None,
                    "evaluation_reports": session.query(EvaluationReport).count() if db_status == "ok" else None,
                },
                "error": error,
            })

    def vector_store(self) -> FrozenJsonObject:
        return freeze_json_object({"status": "local_fallback", **self._integrations.statuses()["vector_store"]})

    def model_services(self) -> FrozenJsonObject:
        statuses = self._integrations.statuses()
        return freeze_json_object({
            "status": "local_or_disabled",
            "services": {name: statuses[name] for name in ("llm", "embedding", "ocr", "document_parser", "evidence_retriever")},
            "message": "能力状态明确区分 disabled、规则实现与本地降级，不代表真实模型已接入。",
        })

    def overall(self) -> FrozenJsonObject:
        database = self.database()
        capabilities = self._integrations.statuses()
        return freeze_json_object({
            "status": "ok" if database["status"] == "ok" else "degraded",
            "app_name": self._settings.APP_NAME,
            "api_prefix": self._settings.API_V1_PREFIX,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "components": {
                "api": {"status": "ok"},
                "database": {"status": database["status"]},
                "vector_db": {"status": "local_fallback", **capabilities["vector_store"]},
                "model_services": {"status": "local_or_disabled"},
            },
            "capabilities": capabilities,
            "configuration": self._runtime_configuration(),
        })


class SqlAlchemySystemConfigRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, name: str) -> SystemConfigRecord | None:
        row = self._session.get(SystemConfig, name)
        return self._record(row) if row is not None else None

    def add_default(self, name: str, config) -> SystemConfigRecord:
        row = SystemConfig(name=name, config=dict(config))
        self._session.add(row)
        self._session.flush()
        return self._record(row)

    def update(self, name: str, config, updated_by: str) -> SystemConfigRecord:
        row = self._session.get(SystemConfig, name)
        if row is None:
            raise LookupError(name)
        row.config = dict(config)
        row.version += 1
        row.updated_by = updated_by
        self._session.flush()
        return self._record(row)

    @staticmethod
    def _record(row: SystemConfig) -> SystemConfigRecord:
        return SystemConfigRecord(row.name, row.config or {}, row.version, row.updated_by, row.updated_at)


class SqlAlchemySystemConfigUnitOfWork:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    def __enter__(self) -> "SqlAlchemySystemConfigUnitOfWork":
        self._session = self._session_factory()
        self.configs = SqlAlchemySystemConfigRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._session is None:
            return
        if exc_type is not None:
            self.rollback()
        self._session.close()

    def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("unit of work has not been entered")
        self._session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
