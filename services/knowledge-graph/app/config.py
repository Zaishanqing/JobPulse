from pathlib import Path
import os
from pydantic import BaseModel, model_validator

ROOT = Path(__file__).resolve().parents[1]

UNSAFE_JWT_SECRETS = {
    "",
    "change-this-knowledge-graph-jwt-secret",
    "change-this-in-production-change-me",
    "development-only-change-me",
    "change-me",
    "your-jwt-secret-key",
    "your-secret-key",
}

LEGACY_JWT_ENV_NAMES = (
    "JWT_SECRET",
    "KNOWLEDGE_GRAPH_JWT_SECRET",
    "SECRET_KEY",
)

UNSAFE_SERVICE_PASSWORDS = {
    "",
    "admin123",
    "change-me",
    "changeme",
    "default",
    "development-kg-service-password-only",
    "integration_developer",
    "password",
    "placeholder",
    "reviewer123",
    "secret",
}

LEGACY_SERVICE_PASSWORD_ENV_NAMES = (
    "KNOWLEDGE_GRAPH_PASSWORD",
    "KG_SERVICE_PASSWORD",
)

class Settings(BaseModel):
    catalog_writes_enabled: bool = True
    database_url: str = "sqlite:///./jobgraph.db"
    environment: str = "development"
    jwt_secret_key: str = "development-only-change-me"
    service_username: str = "integration_developer"
    service_password: str = "development-kg-service-password-only"
    algorithm_version: str = "weighted-v1"
    normalization_map_version: str = "main-capability-snapshot-v2"
    normalization_algorithm_version: str = "deterministic-normalization-v1"
    build_job_max_attempts: int = 3
    build_job_worker_id: str = "kg-build-worker"
    build_job_poll_seconds: float = 1.0
    build_jobs_inline: bool = False
    position_catalog_readiness_required: bool = False
    expected_position_catalog_count: int = 112

    @model_validator(mode="after")
    def validate_jwt_secret(self) -> "Settings":
        if self.environment.casefold() == "production":
            if "jwt_secret_key" not in self.model_fields_set:
                raise ValueError("JWT_SECRET_KEY must be configured in production")
            jwt_secret = self.jwt_secret_key.strip()
            if len(jwt_secret) < 32:
                raise ValueError(
                    "JWT_SECRET_KEY must contain at least 32 characters in production"
                )
            if jwt_secret.casefold() in UNSAFE_JWT_SECRETS:
                raise ValueError("JWT_SECRET_KEY is an unsafe placeholder value")
            if "service_password" not in self.model_fields_set:
                raise ValueError(
                    "KNOWLEDGE_GRAPH_SERVICE_PASSWORD must be configured in production"
                )
            service_password = self.service_password.strip()
            lowered_password = service_password.casefold()
            if len(service_password) < 16:
                raise ValueError(
                    "KNOWLEDGE_GRAPH_SERVICE_PASSWORD must contain at least 16 characters in production"
                )
            if (
                lowered_password in UNSAFE_SERVICE_PASSWORDS
                or any(marker in lowered_password for marker in ("change-me", "placeholder", "default", "development"))
            ):
                raise ValueError(
                    "KNOWLEDGE_GRAPH_SERVICE_PASSWORD is an unsafe placeholder or historical password"
                )
        return self

    @model_validator(mode='after')
    def validate_catalog_ownership(self) -> 'Settings':
        if self.environment.casefold() == 'production' and self.catalog_writes_enabled:
            raise ValueError(
                'CATALOG_WRITES_ENABLED must be false in integrated production'
            )
        return self

    @model_validator(mode="after")
    def validate_production_database(self) -> "Settings":
        if (
            self.environment.casefold() == "production"
            and not self.database_url.startswith(("postgresql://", "postgresql+psycopg://"))
        ):
            raise ValueError("production Knowledge Graph requires PostgreSQL")
        if self.build_job_max_attempts < 1:
            raise ValueError("BUILD_JOB_MAX_ATTEMPTS must be at least 1")
        if self.build_job_poll_seconds <= 0:
            raise ValueError("BUILD_JOB_POLL_SECONDS must be positive")
        if self.expected_position_catalog_count < 1:
            raise ValueError("EXPECTED_POSITION_CATALOG_COUNT must be positive")
        if self.environment.casefold() == "production" and self.build_jobs_inline:
            raise ValueError("BUILD_JOBS_INLINE must be false in production")
        return self

    @property
    def runs_build_jobs_inline(self) -> bool:
        return self.build_jobs_inline

    @property
    def uses_development_jwt_secret(self) -> bool:
        return self.jwt_secret_key == type(self).model_fields["jwt_secret_key"].default

    @classmethod
    def from_env(cls) -> "Settings":
        """Resolve process configuration at the composition root, not at import time."""
        formal = os.getenv("JWT_SECRET_KEY")
        environment = os.getenv(
            "ENVIRONMENT", cls.model_fields["environment"].default
        )
        legacy_names = [
            name for name in LEGACY_JWT_ENV_NAMES if os.getenv(name, "").strip()
        ]
        if environment.casefold() == "production" and legacy_names:
            raise ValueError(
                "Unsupported JWT secret environment variables in production: "
                + ", ".join(legacy_names)
            )
        legacy_password_names = [
            name for name in LEGACY_SERVICE_PASSWORD_ENV_NAMES if os.getenv(name, "").strip()
        ]
        if environment.casefold() == "production" and legacy_password_names:
            raise ValueError(
                "Unsupported knowledge graph password environment variables in production: "
                + ", ".join(legacy_password_names)
            )
        values = {
            "database_url": os.getenv(
                "DATABASE_URL", cls.model_fields["database_url"].default
            ),
            "environment": environment,
        }
        if formal is not None:
            values["jwt_secret_key"] = formal
        service_password = os.getenv("KNOWLEDGE_GRAPH_SERVICE_PASSWORD")
        if service_password is not None:
            values["service_password"] = service_password
        service_username = os.getenv("KNOWLEDGE_GRAPH_SERVICE_USERNAME")
        if service_username is not None:
            values["service_username"] = service_username
        if 'CATALOG_WRITES_ENABLED' in os.environ:
            values['catalog_writes_enabled'] = os.environ['CATALOG_WRITES_ENABLED']
        if "BUILD_JOB_MAX_ATTEMPTS" in os.environ:
            values["build_job_max_attempts"] = os.environ["BUILD_JOB_MAX_ATTEMPTS"]
        if "BUILD_JOB_WORKER_ID" in os.environ:
            values["build_job_worker_id"] = os.environ["BUILD_JOB_WORKER_ID"]
        if "BUILD_JOB_POLL_SECONDS" in os.environ:
            values["build_job_poll_seconds"] = os.environ["BUILD_JOB_POLL_SECONDS"]
        if "BUILD_JOBS_INLINE" in os.environ:
            values["build_jobs_inline"] = os.environ["BUILD_JOBS_INLINE"]
        if "POSITION_CATALOG_READINESS_REQUIRED" in os.environ:
            values["position_catalog_readiness_required"] = os.environ[
                "POSITION_CATALOG_READINESS_REQUIRED"
            ]
        if "EXPECTED_POSITION_CATALOG_COUNT" in os.environ:
            values["expected_position_catalog_count"] = os.environ[
                "EXPECTED_POSITION_CATALOG_COUNT"
            ]
        return cls(**values)

# Compatibility default for pure helpers. Runtime entry points use Settings.from_env().
settings = Settings()

PROVIDERS = {
    "relational_graph": {"status": "enabled", "implementation": "sqlalchemy"},
    "neo4j": {"status": "disabled"}, "llm": {"status": "disabled"},
    "ocr": {"status": "disabled"}, "vector_database": {"status": "disabled"},
    "redis": {"status": "disabled"}, "celery": {"status": "disabled"},
}
