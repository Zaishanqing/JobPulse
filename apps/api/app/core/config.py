import os
from secrets import token_urlsafe
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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

LEGACY_KNOWLEDGE_GRAPH_PASSWORD_ENV_NAMES = (
    "KNOWLEDGE_GRAPH_PASSWORD",
    "KG_SERVICE_PASSWORD",
)


def parse_declared_boolean(value: object) -> bool:
    """Accept only bool or an exact, case-insensitive true/false string."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.casefold()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ValueError(
        "value must be a boolean or an exact true/false string without whitespace"
    )


DeclaredBoolean = Annotated[bool, BeforeValidator(parse_declared_boolean)]


class Settings(BaseSettings):
    ENVIRONMENT: Literal["development", "test", "production"] = "development"
    APP_NAME: str = "岗位能力图谱与动态演化分析系统"
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    DATABASE_URL: str = (
        "postgresql+psycopg://jobgraph_main:jobgraph-main-local-password"
        "@localhost:5432/jobgraph_main"
    )
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)

    LLM_PROVIDER: Literal["disabled"] = "disabled"
    OCR_PROVIDER: Literal["disabled", "tesseract"] = "disabled"
    DOCUMENT_PARSER_PROVIDER: Literal["plain_text_local"] = "plain_text_local"
    EMBEDDING_PROVIDER: Literal["deterministic_local"] = "deterministic_local"
    EMBEDDING_DIMENSION: int = Field(default=16, ge=4, le=4096)
    VECTOR_STORE_PROVIDER: Literal["memory"] = "memory"
    GRAPH_STORE_PROVIDER: Literal["memory"] = "memory"
    TASK_QUEUE_PROVIDER: Literal["database_sync"] = "database_sync"
    FILE_STORAGE_PROVIDER: Literal["local"] = "local"
    RAG_RETRIEVER_PROVIDER: Literal["keyword_local"] = "keyword_local"
    TREND_CRAWLER_PROVIDER: Literal["disabled"] = "disabled"

    NORMALIZATION_SEMANTIC_ENABLED: DeclaredBoolean = False
    NORMALIZATION_EMBEDDING_URL: str = "http://embedding-service:8000"
    NORMALIZATION_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    NORMALIZATION_EMBEDDING_REVISION: str = (
        "5617a9f61b028005a4858fdac845db406aefb181"
    )
    NORMALIZATION_EMBEDDING_DIMENSION: int = Field(default=1024, ge=4, le=4096)
    NORMALIZATION_EMBEDDING_TIMEOUT_SECONDS: float = Field(default=5, gt=0, le=120)

    RAG_EVIDENCE_ENABLED: DeclaredBoolean = False
    RAG_EVIDENCE_EMBEDDING_URL: str = "http://embedding-service:8000"
    RAG_EVIDENCE_EMBEDDING_MODEL: str = "BAAI/bge-m3"
    RAG_EVIDENCE_EMBEDDING_REVISION: str = (
        "5617a9f61b028005a4858fdac845db406aefb181"
    )
    RAG_EVIDENCE_EMBEDDING_DIMENSION: int = Field(default=1024, ge=4, le=4096)
    RAG_EVIDENCE_QDRANT_URL: str = "http://qdrant:6333"
    RAG_EVIDENCE_COLLECTION: str = "evidence_rag_bge_m3_5617a9f_v1"
    RAG_EVIDENCE_TIMEOUT_SECONDS: float = Field(default=10, gt=0, le=120)
    RAG_EVIDENCE_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    RAG_EVIDENCE_RETRY_BACKOFF_SECONDS: float = Field(default=0.1, ge=0, le=30)
    RAG_EVIDENCE_LLM_MODEL: str = "deepseek-v4-flash"
    RAG_EVIDENCE_LLM_ALGORITHM_VERSION: str = "deepseek-evidence-rag-answer.v1"
    RAG_EVIDENCE_LLM_TIMEOUT_SECONDS: int = Field(default=120, ge=1, le=600)
    RAG_EVIDENCE_MAX_CONTEXT_CHARS: int = Field(default=8000, ge=256, le=24000)
    RAG_EVIDENCE_MULTI_OBJECT_MAX_HITS: int = Field(default=40, ge=1, le=300)
    RAG_EVIDENCE_MULTI_OBJECT_MAX_CONTEXT_CHARS: int = Field(
        default=24000, ge=256, le=30000
    )
    RAG_EVIDENCE_TOP_K: int = Field(default=5, ge=1, le=20)
    RAG_EVIDENCE_MIN_SCORE: float = Field(default=0.0, ge=0, le=1)

    DATA_VALIDATION_MODE: Literal["off", "observe", "enforce"] = "off"
    DATA_VALIDATION_WORKER_POLL_SECONDS: float = Field(default=1, gt=0, le=60)
    CV_EXTRACTION_ENABLED: DeclaredBoolean = False
    CV_EXTRACTION_BASE_URL: str | None = None
    CV_EXTRACTION_INTERNAL_TOKEN: str | None = None
    CV_EXTRACTION_CONNECT_TIMEOUT_SECONDS: float = Field(default=3, gt=0, le=60)
    CV_EXTRACTION_READ_TIMEOUT_SECONDS: float = Field(default=300, gt=0, le=600)
    CV_EXTRACTION_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=20)
    CV_EXTRACTION_PROVIDER: str = "deepseek"
    CV_EXTRACTION_MODEL: str = "deepseek-v4-flash"
    CV_EXTRACTION_PROMPT_VERSION: str = "cv-prompt.v1"
    CV_EXTRACTION_SCHEMA_VERSION: str = "2.4"
    CV_EXTRACTION_NORMALIZATION_VERSION: str = "2.0"
    CV_EXTRACTION_TAXONOMY_VERSION: str | None = None
    CV_EXTRACTION_VALIDATION_POLICY_VERSION: str = "cv-validation-policy.v2"
    CV_EXTRACTION_WORKER_ENABLED: bool = False
    CV_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS: float = Field(default=1, gt=0, le=60)
    CV_EXTRACTION_WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    CV_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS: float = Field(default=300, gt=1, le=3600)
    CV_EXTRACTION_STALE_RECOVERY_INTERVAL_SECONDS: float = Field(default=30, gt=0, le=600)
    DISCOVERY_ALLOW_LEGACY_REVIEWED: DeclaredBoolean = False

    KNOWLEDGE_GRAPH_ENABLED: bool = False
    KNOWLEDGE_GRAPH_BASE_URL: str = "http://knowledge-graph-backend:8000"
    KNOWLEDGE_GRAPH_TIMEOUT_SECONDS: float = Field(default=20, gt=0, le=120)
    KNOWLEDGE_GRAPH_SERVICE_USERNAME: str = "integration_developer"
    KNOWLEDGE_GRAPH_SERVICE_PASSWORD: str = "development-kg-service-password-only"
    KNOWLEDGE_GRAPH_RAG_DATABASE_URL: str | None = None

    MATCHING_SERVICE_ENABLED: DeclaredBoolean = False
    MATCHING_VECTOR_INDEX_ENABLED: DeclaredBoolean = False
    MATCHING_SERVICE_BASE_URL: str | None = None
    MATCHING_SERVICE_ISSUER: str = "jobgraph-main"
    MATCHING_SERVICE_AUDIENCE: str = "matching-service"
    MATCHING_SERVICE_SIGNING_KEY: str | None = None
    # What-if and evidence-deletion recompute the complete evaluation.  Real
    # position profiles can contain dozens of requirements, so the generic
    # five-second HTTP budget caused the main BFF to retry a still-running POST
    # and return 503 even though Matching completed the calculation.
    MATCHING_SERVICE_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=120)
    MATCHING_SERVICE_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    MATCHING_SERVICE_RETRY_BACKOFF_SECONDS: float = Field(default=0.1, ge=0, le=30)
    MATCHING_UPSTREAM_SERVICE_TOKEN: str | None = None

    OUTBOX_IDLE_SLEEP_SECONDS: float = Field(default=0.25, gt=0, le=60)
    OUTBOX_WORKER_ID: str | None = None
    OUTBOX_DISPATCH_ONCE: bool = False
    KG_OUTBOX_WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    KG_OUTBOX_POLL_INTERVAL_SECONDS: float = Field(default=1, gt=0, le=60)
    KG_OUTBOX_LEASE_SECONDS: int = Field(default=60, ge=2, le=3600)
    KG_OUTBOX_MAX_ATTEMPTS: int = Field(default=5, ge=1, le=100)

    EMERGING_DISCOVERY_ENABLED: bool = False
    EMERGING_DISCOVERY_BASE_URL: str = "http://emerging-discovery:8000"
    # A formal 214-JD rolling run performs several CPU BGE-M3 batches before
    # clustering; keep the client bounded, but do not abort a healthy run at
    # the old interactive-only 120-second ceiling.
    EMERGING_DISCOVERY_TIMEOUT_SECONDS: float = Field(default=20, gt=0, le=1800)
    EMERGING_DISCOVERY_INTERNAL_TOKEN: str = "development-emerging-discovery-token-change-me"
    EMERGING_CONCLUSION_MANIFEST_PATH: str | None = None

    TREND_INTELLIGENCE_ENABLED: DeclaredBoolean = False
    TREND_INTELLIGENCE_BASE_URL: str = "http://trend-intelligence:8000"
    TREND_INTELLIGENCE_TIMEOUT_SECONDS: float = Field(default=20, gt=0, le=120)
    TREND_INTELLIGENCE_INTERNAL_TOKEN: str | None = None
    TREND_INTELLIGENCE_MAX_RETRIES: int = Field(default=2, ge=0, le=10)
    TREND_INTELLIGENCE_RETRY_BACKOFF_SECONDS: float = Field(default=0.1, ge=0, le=30)
    TREND_INTELLIGENCE_ALGORITHM_VERSION: str = "market-prediction-v1"
    TREND_INTELLIGENCE_FORMULA_VERSION: str = "multi-source-emergence-v1"
    TREND_SKILL_ALGORITHM_VERSION: str = "position-skill-trend-v1"
    TREND_SKILL_FORMULA_VERSION: str = "multi-source-skill-growth-v1"
    TREND_SKILL_CONFIG_VERSION: str = "position-skill-trend-config-v1"
    TREND_INTELLIGENCE_TEST_ADAPTER_ENABLED: DeclaredBoolean = False
    TREND_PUBLICATION_MIN_SOURCE_COVERAGE: float = Field(default=0.6, ge=0, le=1)
    TREND_PUBLICATION_HIGH_RISK_FLAGS: str = "high_risk,blocking"
    TREND_ANALYSIS_SYNC_POLL_SECONDS: float = Field(default=2, gt=0, le=60)

    ACQUISITION_ENABLED: DeclaredBoolean = True
    CRAWLER_BASE_URL: str = "http://crawler:8000"
    CRAWLER_INTERNAL_TOKEN: str = "local-crawler-internal-token-0123456789abcdef"
    CRAWLER_CONNECT_TIMEOUT_SECONDS: float = Field(default=3, gt=0, le=60)
    CRAWLER_READ_TIMEOUT_SECONDS: float = Field(default=120, gt=0, le=600)
    ACQUISITION_BUNDLE_DIR: str = "bundles"
    ACQUISITION_POLL_INTERVAL_SECONDS: float = Field(default=1, gt=0, le=60)
    ACQUISITION_TIMEOUT_SECONDS: float = Field(default=3600, gt=0, le=86400)
    ACQUISITION_STALE_AFTER_SECONDS: float = Field(default=3600, gt=0, le=86400)
    ACQUISITION_EXTRACTION_MODE: Literal["llm", "rule"] = "rule"

    JD_EXTRACTION_BASE_URL: str | None = None
    JD_EXTRACTION_INTERNAL_TOKEN: str | None = None
    JD_EXTRACTION_CONNECT_TIMEOUT_SECONDS: float = Field(default=3, gt=0, le=60)
    JD_EXTRACTION_READ_TIMEOUT_SECONDS: float = Field(default=120, gt=0, le=600)
    JD_EXTRACTION_MAX_ATTEMPTS: int = Field(default=3, ge=1, le=20)
    JD_EXTRACTION_WORKER_ENABLED: bool = False
    JD_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS: float = Field(default=1, gt=0, le=60)
    JD_EXTRACTION_WORKER_CONCURRENCY: int = Field(default=2, ge=1, le=32)
    JD_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS: float = Field(default=300, gt=1, le=3600)
    JD_EXTRACTION_STALE_RECOVERY_INTERVAL_SECONDS: float = Field(
        default=30, gt=0, le=600
    )

    # Local development gets a fresh process-local secret instead of a
    # predictable repository secret. Production must provide an explicit key.
    JWT_SECRET_KEY: str = Field(default_factory=lambda: token_urlsafe(48))
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60, gt=0, le=1440)
    # Explicit competition/demo-only escape hatch. Public internal-role
    # registration remains disabled unless an operator opts in.
    ALLOW_DEMO_ADMIN_REGISTRATION: DeclaredBoolean = False

    # A repository-level dotenv also contains credentials for sibling services.
    # Ignore keys outside this settings model while still validating every
    # declared main-backend setting and production secret below.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if self.KG_OUTBOX_LEASE_SECONDS <= self.KG_OUTBOX_POLL_INTERVAL_SECONDS:
            raise ValueError(
                "KG outbox lease must exceed its poll interval"
            )
        if (
            self.JD_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS
            <= self.JD_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS
        ):
            raise ValueError(
                "JD extraction worker lease timeout must exceed its poll interval"
            )
        if (
            self.CV_EXTRACTION_WORKER_LEASE_TIMEOUT_SECONDS
            <= self.CV_EXTRACTION_WORKER_POLL_INTERVAL_SECONDS
        ):
            raise ValueError(
                "CV extraction worker lease timeout must exceed its poll interval"
            )
        extraction_token = (self.JD_EXTRACTION_INTERNAL_TOKEN or "").strip()
        if extraction_token and (
            len(extraction_token) < 32
            or extraction_token.casefold() in UNSAFE_SERVICE_PASSWORDS
            or any(
                marker in extraction_token.casefold()
                for marker in ("change-me", "placeholder", "default", "development")
            )
        ):
            raise ValueError("JD_EXTRACTION_INTERNAL_TOKEN must be a strong internal token")
        cv_token = (self.CV_EXTRACTION_INTERNAL_TOKEN or "").strip()
        if cv_token and (
            len(cv_token) < 32
            or cv_token.casefold() in UNSAFE_SERVICE_PASSWORDS
            or any(
                marker in cv_token.casefold()
                for marker in ("change-me", "placeholder", "default", "development")
            )
        ):
            raise ValueError("CV_EXTRACTION_INTERNAL_TOKEN must be a strong internal token")
        if self.CV_EXTRACTION_ENABLED and (
            not (self.CV_EXTRACTION_BASE_URL or "").strip() or not cv_token
        ):
            raise ValueError(
                "CV extraction URL and internal token are required when enabled"
            )
        matching_key = (self.MATCHING_SERVICE_SIGNING_KEY or "").strip()
        upstream_token = (self.MATCHING_UPSTREAM_SERVICE_TOKEN or "").strip()
        if self.MATCHING_SERVICE_ENABLED and (
            not (self.MATCHING_SERVICE_BASE_URL or "").strip()
            or len(matching_key) < 32
            or len(upstream_token) < 32
        ):
            raise ValueError(
                "matching-service URL, signing key and upstream service token are required when enabled"
            )
        trend_token = (self.TREND_INTELLIGENCE_INTERNAL_TOKEN or "").strip()
        if self.TREND_INTELLIGENCE_ENABLED and (
            not self.TREND_INTELLIGENCE_BASE_URL.strip() or not trend_token
        ):
            raise ValueError(
                "trend-intelligence URL and internal token are required when enabled"
            )
        crawler_token = (self.CRAWLER_INTERNAL_TOKEN or "").strip()
        if self.ACQUISITION_ENABLED and (
            not crawler_token
            or len(crawler_token) < 32
            or crawler_token.casefold() in UNSAFE_SERVICE_PASSWORDS
            or any(
                marker in crawler_token.casefold()
                for marker in ("change-me", "placeholder", "default", "development")
            )
        ):
            raise ValueError(
                "CRAWLER_INTERNAL_TOKEN must be a strong internal token when acquisition is enabled"
            )
        if self.ENVIRONMENT != "production":
            if self.ENVIRONMENT != "test" and not self.DATABASE_URL.startswith(
                "postgresql+psycopg://"
            ):
                raise ValueError(
                    "DATABASE_URL must use PostgreSQL with the psycopg driver outside tests"
                )
            return self
        legacy_names = [name for name in LEGACY_JWT_ENV_NAMES if os.getenv(name, "").strip()]
        if legacy_names:
            raise ValueError(
                "Unsupported JWT secret environment variables in production: "
                + ", ".join(legacy_names)
            )
        legacy_password_names = [
            name
            for name in LEGACY_KNOWLEDGE_GRAPH_PASSWORD_ENV_NAMES
            if os.getenv(name, "").strip()
        ]
        if legacy_password_names:
            raise ValueError(
                "Unsupported knowledge graph password environment variables in production: "
                + ", ".join(legacy_password_names)
            )
        if "JWT_SECRET_KEY" not in self.model_fields_set:
            raise ValueError("JWT_SECRET_KEY must be explicitly configured in production")
        jwt_secret = self.JWT_SECRET_KEY.strip()
        if len(jwt_secret) < 32:
            raise ValueError("JWT_SECRET_KEY must contain at least 32 characters in production")
        if jwt_secret.casefold() in UNSAFE_JWT_SECRETS:
            raise ValueError("JWT_SECRET_KEY is an unsafe placeholder value")
        internal_token = self.EMERGING_DISCOVERY_INTERNAL_TOKEN.strip()
        if len(internal_token) < 32:
            raise ValueError(
                "EMERGING_DISCOVERY_INTERNAL_TOKEN must contain at least 32 characters in production"
            )
        if (
            internal_token.casefold() in UNSAFE_JWT_SECRETS
            or "placeholder" in internal_token.casefold()
        ):
            raise ValueError("EMERGING_DISCOVERY_INTERNAL_TOKEN is an unsafe placeholder value")
        if "KNOWLEDGE_GRAPH_SERVICE_PASSWORD" not in self.model_fields_set:
            raise ValueError(
                "KNOWLEDGE_GRAPH_SERVICE_PASSWORD must be explicitly configured in production"
            )
        knowledge_graph_password = self.KNOWLEDGE_GRAPH_SERVICE_PASSWORD.strip()
        lowered_password = knowledge_graph_password.casefold()
        if len(knowledge_graph_password) < 16:
            raise ValueError(
                "KNOWLEDGE_GRAPH_SERVICE_PASSWORD must contain at least 16 characters in production"
            )
        if lowered_password in UNSAFE_SERVICE_PASSWORDS or any(
            marker in lowered_password
            for marker in ("change-me", "placeholder", "default", "development")
        ):
            raise ValueError(
                "KNOWLEDGE_GRAPH_SERVICE_PASSWORD is an unsafe placeholder or historical password"
            )
        if not self.MATCHING_SERVICE_ENABLED:
            raise ValueError("MATCHING_SERVICE_ENABLED must be true in production")
        if not self.DATABASE_URL.startswith("postgresql+psycopg://"):
            raise ValueError(
                "DATABASE_URL must use PostgreSQL with the psycopg driver outside tests"
            )
        return self


settings = Settings()
