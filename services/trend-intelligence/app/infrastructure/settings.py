from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    INTERNAL_TOKEN: str = Field(min_length=16)
    MAX_UPLOAD_SIZE_BYTES: int = Field(default=10 * 1024 * 1024, gt=0)
    MAX_ATTEMPTS: int = Field(default=3, ge=1, le=100)
    WORKER_ID: str = "trend-intelligence-worker"
    WORKER_POLL_SECONDS: float = Field(default=1, gt=0, le=60)
    WORKER_LEASE_SECONDS: float = Field(default=60, gt=1, le=3600)
    WORKER_HEARTBEAT_SECONDS: float = Field(default=15, gt=0, le=1200)
    WORKER_RETRY_DELAY_SECONDS: float = Field(default=5, ge=0, le=3600)
    HTTP_TIMEOUT_SECONDS: float = Field(default=30, gt=0, le=300)
    HTTP_PROXY: str | None = None
    ARXIV_LIMIT: int = Field(default=200, ge=1, le=2000)
    CONFERENCE_LIMIT: int = Field(default=500, ge=1, le=5000)
    SOURCE_WORKERS: int = Field(default=6, ge=1, le=16)
    GITHUB_ARCHIVE_HOURS: int = Field(default=3, ge=1, le=168)
    GITHUB_ARCHIVE_MAX_HOURS: int = Field(default=168, ge=1, le=336)

    @field_validator("DATABASE_URL")
    @classmethod
    def require_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use PostgreSQL with the psycopg driver")
        return value

    model_config = SettingsConfigDict(
        env_prefix="TREND_INTELLIGENCE_",
        env_file=".env",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_worker_timing(self):
        if self.WORKER_HEARTBEAT_SECONDS >= self.WORKER_LEASE_SECONDS:
            raise ValueError("worker heartbeat interval must be shorter than the lease")
        return self
