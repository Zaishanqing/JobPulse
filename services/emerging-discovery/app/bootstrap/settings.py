import hmac
import os
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


UNSAFE_TOKENS = {"", "change-me", "changeme", "placeholder", "default", "secret"}
UNSAFE_SERVICE_PASSWORDS = UNSAFE_TOKENS | {
    "admin123",
    "development-kg-service-password-only",
    "integration_developer",
    "password",
    "reviewer123",
}
LEGACY_KNOWLEDGE_GRAPH_PASSWORD_ENV_NAMES = (
    "KNOWLEDGE_GRAPH_PASSWORD",
    "KG_SERVICE_PASSWORD",
)


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    DATABASE_URL: str = (
        "postgresql+psycopg://emerging_discovery:"
        "jobgraph-emerging-local-password@analytics-postgres:5432/emerging_discovery"
    )
    ALGORITHM_VERSION: str = "emerge-v3.2"
    FORMULA_VERSION: str = "emerge-v3.2"
    INTERNAL_SERVICE_TOKEN: str = "development-emerging-discovery-token-change-me"
    MAINTENANCE_TOKEN: str = "development-emerging-maintenance-token-change-me"
    POSITION_REFERENCE_PROVIDER: Literal["payload_fake", "knowledge_graph_http"] = "payload_fake"
    KNOWLEDGE_GRAPH_BASE_URL: str = "http://knowledge-graph-backend:8000"
    KNOWLEDGE_GRAPH_SERVICE_USERNAME: str = "integration_developer"
    KNOWLEDGE_GRAPH_SERVICE_PASSWORD: str = "development-kg-service-password-only"
    # Formal EMERGE requests evaluate tens to hundreds of occupation clusters.
    # Keep this separate from health/auth timeouts; the endpoint is synchronous
    # and fail-closed, so a 10-second default incorrectly rejects valid batches.
    KNOWLEDGE_GRAPH_TIMEOUT_SECONDS: float = 300.0
    EMBEDDING_BASE_URL: str = "http://embedding-service:8000"
    EMBEDDING_TIMEOUT_SECONDS: float = 120.0

    def __init__(self, **values):
        if (
            values.get("ENVIRONMENT") == "production"
            and values.get("POSITION_REFERENCE_PROVIDER") == "knowledge_graph_http"
            and len(str(values.get("INTERNAL_SERVICE_TOKEN", "")).strip()) >= 32
            and len(str(values.get("MAINTENANCE_TOKEN", "")).strip()) >= 32
            and "KNOWLEDGE_GRAPH_SERVICE_PASSWORD" not in values
        ):
            raise ValueError(
                "production KNOWLEDGE_GRAPH_SERVICE_PASSWORD must be explicitly configured"
            )
        super().__init__(**values)

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        database_url = make_url(self.DATABASE_URL)
        allowed_databases = {"emerging_discovery"}
        if self.ENVIRONMENT == "test":
            allowed_databases.add("emerging_discovery_test")
        if database_url.drivername != "postgresql+psycopg":
            raise ValueError("emerging discovery requires PostgreSQL via psycopg")
        if database_url.username != "emerging_discovery":
            raise ValueError("emerging discovery requires its dedicated database account")
        if database_url.database not in allowed_databases:
            raise ValueError("emerging discovery cannot access another service database")
        if self.ENVIRONMENT == "production":
            legacy_password_names = [
                name
                for name in LEGACY_KNOWLEDGE_GRAPH_PASSWORD_ENV_NAMES
                if os.getenv(name, "").strip()
            ]
            if legacy_password_names:
                raise ValueError(
                    "unsupported knowledge graph password aliases in production: "
                    + ", ".join(legacy_password_names)
                )
            token = self.INTERNAL_SERVICE_TOKEN.strip()
            if (
                len(token) < 32
                or token.casefold() in UNSAFE_TOKENS
                or "placeholder" in token.casefold()
            ):
                raise ValueError(
                    "production INTERNAL_SERVICE_TOKEN must be a strong non-placeholder value"
                )
            if self.POSITION_REFERENCE_PROVIDER != "knowledge_graph_http":
                raise ValueError("production cannot use a fake position reference provider")
            maintenance = self.MAINTENANCE_TOKEN.strip()
            if (
                len(maintenance) < 32
                or maintenance.casefold() in UNSAFE_TOKENS
                or "placeholder" in maintenance.casefold()
                or hmac.compare_digest(maintenance, token)
            ):
                raise ValueError(
                    "production MAINTENANCE_TOKEN must be strong and distinct from the service token"
                )
            if "KNOWLEDGE_GRAPH_SERVICE_PASSWORD" not in self.model_fields_set:
                raise ValueError(
                    "production KNOWLEDGE_GRAPH_SERVICE_PASSWORD must be explicitly configured"
                )
            knowledge_graph_password = self.KNOWLEDGE_GRAPH_SERVICE_PASSWORD.strip()
            lowered_password = knowledge_graph_password.casefold()
            if (
                len(knowledge_graph_password) < 16
                or lowered_password in UNSAFE_SERVICE_PASSWORDS
                or any(
                    marker in lowered_password
                    for marker in ("change-me", "placeholder", "default", "development")
                )
            ):
                raise ValueError(
                    "production KNOWLEDGE_GRAPH_SERVICE_PASSWORD must be a strong non-placeholder value"
                )
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
