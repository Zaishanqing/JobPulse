from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text

from app.core.database import Base
from app.models.user import utc_now


ENTERPRISE_JOB_STATUSES = ("draft", "published", "paused", "cancelled")
ENTERPRISE_JOB_SALARY_UNITS = ("year", "month", "day")


class EnterpriseJob(Base):
    __tablename__ = "enterprise_jobs"
    __table_args__ = (
        CheckConstraint(
            f"status in {ENTERPRISE_JOB_STATUSES}",
            name="ck_enterprise_jobs_status_allowed",
        ),
        CheckConstraint(
            f"salary_unit in {ENTERPRISE_JOB_SALARY_UNITS}",
            name="ck_enterprise_jobs_salary_unit_allowed",
        ),
        CheckConstraint("headcount >= 0", name="ck_enterprise_jobs_headcount_nonnegative"),
    )

    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    enterprise_id = Column(
        String(36),
        ForeignKey("enterprises.id"),
        nullable=False,
        index=True,
    )
    title = Column(String(255), nullable=False)
    standard_position_id = Column(String(64), nullable=True)
    jd_text = Column(Text, nullable=True)
    requirement_graph = Column(JSON, nullable=True)
    headcount = Column(Integer, nullable=False, default=1)
    location = Column(String(128), nullable=True)
    employment_type = Column(String(64), nullable=True)
    salary_min = Column(Integer, nullable=True)
    salary_max = Column(Integer, nullable=True)
    salary_unit = Column(String(16), nullable=False, default="month", server_default="month")
    status = Column(String(32), nullable=False, default="draft")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
    )
