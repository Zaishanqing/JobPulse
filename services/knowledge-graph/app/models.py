from __future__ import annotations
from datetime import datetime, timezone
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def now():
    return datetime.now(timezone.utc)


class IdMixin:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class User(IdMixin, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="reviewer")


class JDDocument(IdMixin, Base):
    __tablename__ = "jd_documents"
    __table_args__ = (
        CheckConstraint(
            "NOT (is_synthetic = true AND fact_authority = 'authoritative')",
            name="ck_jd_documents_synthetic_not_authoritative",
        ),
    )
    document_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40), default="manual")
    source_name: Mapped[str | None] = mapped_column(String(120))
    enterprise_name: Mapped[str | None] = mapped_column(String(160))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_credibility: Mapped[float] = mapped_column(Float, default=1.0)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    source_system: Mapped[str] = mapped_column(
        String(40), default="knowledge-graph", server_default="knowledge-graph"
    )
    fact_authority: Mapped[str] = mapped_column(
        String(30), default="legacy_local", server_default="legacy_local", index=True
    )
    source_fact_id: Mapped[str | None] = mapped_column(String(80))
    source_fact_version: Mapped[str | None] = mapped_column(String(80))
    source_schema_version: Mapped[str | None] = mapped_column(String(30))
    source_version: Mapped[str | None] = mapped_column(String(64))


class PublishedFactImport(IdMixin, Base):
    __tablename__ = "published_fact_imports"
    __table_args__ = (UniqueConstraint("source_system", "source_fact_id", "source_fact_version"),)
    source_system: Mapped[str] = mapped_column(String(40))
    source_fact_id: Mapped[str] = mapped_column(String(80))
    source_fact_version: Mapped[str] = mapped_column(String(80))
    source_schema_version: Mapped[str] = mapped_column(String(30))
    source_version: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict] = mapped_column(JSON)


class PublishedFactLineageRecord(IdMixin, Base):
    __tablename__ = "published_fact_lineages"
    __table_args__ = (
        UniqueConstraint(
            "published_fact_import_id",
            name="uq_published_fact_lineage_import",
        ),
        Index(
            "ix_published_fact_lineages_import_id",
            "published_fact_import_id",
        ),
        CheckConstraint(
            "validation_conclusion IS NULL OR validation_conclusion IN ('pass', 'warn')",
            name="ck_published_fact_lineage_validation_conclusion",
        ),
        CheckConstraint(
            "catalog_status IS NULL OR catalog_status IN ('active', 'inactive')",
            name="ck_published_fact_lineage_catalog_status",
        ),
    )
    published_fact_import_id: Mapped[int] = mapped_column(ForeignKey("published_fact_imports.id"))
    lineage_lineage_version: Mapped[str] = mapped_column(String(64), index=True)
    data_validation_task_id: Mapped[str | None] = mapped_column(String(100))
    validation_report_id: Mapped[str | None] = mapped_column(String(100))
    validated_bundle_snapshot_id: Mapped[str | None] = mapped_column(String(100))
    validation_policy_version: Mapped[str | None] = mapped_column(String(80))
    validation_conclusion: Mapped[str | None] = mapped_column(String(10))
    bundle_lineage_version: Mapped[str | None] = mapped_column(String(128))
    catalog_source: Mapped[str | None] = mapped_column(String(100))
    catalog_version: Mapped[str | None] = mapped_column(String(80))
    catalog_source_version: Mapped[str | None] = mapped_column(String(64))
    catalog_effective_at: Mapped[str | None] = mapped_column(String(80))
    catalog_status: Mapped[str | None] = mapped_column(String(10))


class ReleaseImportBatch(IdMixin, Base):
    __tablename__ = "release_import_batches"
    __table_args__ = (
        UniqueConstraint("release_id"),
        Index("ix_release_import_batches_release_id", "release_id"),
    )
    release_id: Mapped[str] = mapped_column(String(128))
    manifest_hash: Mapped[str] = mapped_column(String(64), unique=True)
    manifest: Mapped[dict] = mapped_column(JSON)
    record_count: Mapped[int] = mapped_column(Integer)


class ReleaseImportItem(IdMixin, Base):
    __tablename__ = "release_import_items"
    __table_args__ = (
        UniqueConstraint("release_id", "ordinal"),
        UniqueConstraint("release_id", "source_system", "source_fact_id", "source_fact_version"),
    )
    release_id: Mapped[str] = mapped_column(
        ForeignKey("release_import_batches.release_id"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    source_system: Mapped[str] = mapped_column(String(40))
    source_fact_id: Mapped[str] = mapped_column(String(80))
    source_fact_version: Mapped[str] = mapped_column(String(80))
    source_version: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))


class PublishedFactReleaseLink(IdMixin, Base):
    __tablename__ = "published_fact_release_links"
    __table_args__ = (UniqueConstraint("published_fact_import_id", "release_id"),)
    published_fact_import_id: Mapped[int] = mapped_column(
        ForeignKey("published_fact_imports.id"), index=True
    )
    release_id: Mapped[str] = mapped_column(
        ForeignKey("release_import_batches.release_id"), index=True
    )


@event.listens_for(PublishedFactLineageRecord, "before_update")
def prevent_published_fact_lineage_update(_mapper, _connection, _target):
    raise ValueError("published fact lineage is immutable")


@event.listens_for(PublishedFactLineageRecord, "before_delete")
def prevent_published_fact_lineage_delete(_mapper, _connection, _target):
    raise ValueError("published fact lineage cannot be deleted")


class JDExtractionRecord(IdMixin, Base):
    __tablename__ = "jd_extraction_records"
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


@event.listens_for(JDExtractionRecord, "before_update")
def prevent_extraction_payload_update(_mapper, _connection, target):
    if inspect(target).attrs.payload.history.has_changes():
        raise ValueError("extraction audit payload is immutable")


class ExtractedJobTitle(IdMixin, Base):
    __tablename__ = "extracted_job_titles"
    __table_args__ = (UniqueConstraint("document_id"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    text: Mapped[str | None] = mapped_column(String(300))


class ExtractionEvidence(IdMixin, Base):
    __tablename__ = "extraction_evidence"
    __table_args__ = (UniqueConstraint("document_id", "owner_type", "owner_ref"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"), index=True)
    owner_type: Mapped[str] = mapped_column(String(40))
    owner_ref: Mapped[str] = mapped_column(String(80))
    quote: Mapped[str] = mapped_column(Text)
    start: Mapped[int | None] = mapped_column(Integer)
    end: Mapped[int | None] = mapped_column(Integer)
    alignment: Mapped[str] = mapped_column(String(30))
    occurrence_index: Mapped[int | None] = mapped_column(Integer)


class ExtractedTaskRequirement(IdMixin, Base):
    __tablename__ = "extracted_task_requirements"
    __table_args__ = (UniqueConstraint("document_id", "requirement_id"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    requirement_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class ExtractedCandidateRequirement(IdMixin, Base):
    __tablename__ = "extracted_candidate_requirements"
    __table_args__ = (UniqueConstraint("document_id", "requirement_id"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    requirement_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30))
    modality: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)


class ExtractedCompanyFact(IdMixin, Base):
    __tablename__ = "extracted_company_facts"
    __table_args__ = (UniqueConstraint("document_id", "fact_id"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    fact_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class ExtractedEmploymentFact(IdMixin, Base):
    __tablename__ = "extracted_employment_facts"
    __table_args__ = (UniqueConstraint("document_id", "fact_id"),)
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    fact_id: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)


class JDNormalizedRecord(IdMixin, Base):
    __tablename__ = "jd_normalized_records"
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    map_version: Mapped[str] = mapped_column(String(50))


class NormalizedJobClassification(IdMixin, Base):
    __tablename__ = "normalized_job_classifications"
    normalized_record_id: Mapped[int] = mapped_column(ForeignKey("jd_normalized_records.id"))
    position_id: Mapped[str | None] = mapped_column(String(80))
    source_title: Mapped[str | None] = mapped_column(String(200))
    resolution_status: Mapped[str] = mapped_column(String(30))


class NormalizedRequirementRecord(IdMixin, Base):
    __tablename__ = "normalized_requirement_records"
    __table_args__ = (UniqueConstraint("normalized_record_id", "requirement_id"),)
    normalized_record_id: Mapped[int] = mapped_column(ForeignKey("jd_normalized_records.id"))
    requirement_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(30))


class NormalizedSkillRecord(IdMixin, Base):
    __tablename__ = "normalized_skill_records"
    normalized_requirement_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_requirement_records.id")
    )
    source_name: Mapped[str] = mapped_column(String(200))
    skill_id: Mapped[str | None] = mapped_column(String(80))
    canonical_name: Mapped[str | None] = mapped_column(String(200))
    category_code: Mapped[str | None] = mapped_column(String(80), index=True)
    subcategory_code: Mapped[str | None] = mapped_column(String(80))
    resolution_status: Mapped[str] = mapped_column(String(30))
    resolution_source: Mapped[str] = mapped_column(
        String(30), default="unresolved", server_default="unresolved"
    )


class UnresolvedNormalizationItem(IdMixin, Base):
    __tablename__ = "unresolved_normalization_items"
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    source_name: Mapped[str] = mapped_column(String(200))
    item_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="open")
    reason: Mapped[str] = mapped_column(Text)
    resolution: Mapped[dict | None] = mapped_column(JSON)
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reason: Mapped[str | None] = mapped_column(Text)


class PositionCategory(IdMixin, Base):
    __tablename__ = "position_categories"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_code: Mapped[str | None] = mapped_column(String(80))


class StandardPosition(IdMixin, Base):
    __tablename__ = "standard_positions"
    position_id: Mapped[str] = mapped_column(String(100), unique=True)
    position_code: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    category_code: Mapped[str] = mapped_column(String(80), index=True)
    taxonomy_version: Mapped[str | None] = mapped_column(String(64), index=True)
    sample_support_status: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )
    status: Mapped[str] = mapped_column(String(30), default="active")
    current_version_id: Mapped[int | None] = mapped_column(Integer)


class SkillCategory(IdMixin, Base):
    __tablename__ = "skill_categories"
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    parent_code: Mapped[str | None] = mapped_column(String(80))


class Skill(IdMixin, Base):
    __tablename__ = "skills"
    skill_id: Mapped[str] = mapped_column(String(80), unique=True)
    canonical_name: Mapped[str] = mapped_column(String(150))
    category_code: Mapped[str | None] = mapped_column(String(80), index=True)
    subcategory_code: Mapped[str | None] = mapped_column(String(80))
    taxonomy_version: Mapped[str | None] = mapped_column(String(71), index=True)
    status: Mapped[str] = mapped_column(String(30), default="active")


class SkillTaxonomyNode(IdMixin, Base):
    __tablename__ = "skill_taxonomy_nodes"
    __table_args__ = (UniqueConstraint("facet", "code"),)
    facet: Mapped[str] = mapped_column(String(32), index=True)
    code: Mapped[str] = mapped_column(String(80))
    name_zh: Mapped[str] = mapped_column(String(120))
    name_en: Mapped[str | None] = mapped_column(String(120))


class SkillClassification(IdMixin, Base):
    __tablename__ = "skill_classifications"
    __table_args__ = (
        UniqueConstraint("skill_id", "taxonomy_node_id"),
        Index(
            "uq_kg_skill_classification_singleton",
            "skill_id",
            "facet",
            unique=True,
            sqlite_where=text("facet IN ('concept_class', 'technology_kind')"),
            postgresql_where=text("facet IN ('concept_class', 'technology_kind')"),
        ),
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"), index=True)
    taxonomy_node_id: Mapped[int] = mapped_column(ForeignKey("skill_taxonomy_nodes.id"))
    facet: Mapped[str] = mapped_column(String(32), index=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)


class SkillAlias(IdMixin, Base):
    __tablename__ = "skill_aliases"
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    alias: Mapped[str] = mapped_column(String(150), unique=True)


class JDQualityAssessment(IdMixin, Base):
    __tablename__ = "jd_quality_assessments"
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"), unique=True)
    duplicate_score: Mapped[float] = mapped_column(Float, default=0)
    copy_risk_score: Mapped[float] = mapped_column(Float, default=0)
    inflation_score: Mapped[float] = mapped_column(Float, default=0)
    effective_sample_weight: Mapped[float] = mapped_column(Float, default=1)
    assessed: Mapped[bool] = mapped_column(Boolean, default=True)


class DuplicateCluster(IdMixin, Base):
    __tablename__ = "duplicate_clusters"
    cluster_key: Mapped[str] = mapped_column(String(80))
    document_ids: Mapped[list] = mapped_column(JSON)
    score: Mapped[float] = mapped_column(Float)


class GraphBuildRun(IdMixin, Base):
    __tablename__ = "graph_build_runs"
    __table_args__ = (
        Index("uq_graph_build_run_id_position", "id", "position_id", unique=True),
        Index("uq_graph_build_run_active_draft_key", "active_draft_key", unique=True),
    )
    position_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE")
    )
    base_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    release_id: Mapped[str | None] = mapped_column(String(128), index=True)
    active_draft_key: Mapped[str | None] = mapped_column(String(180))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class GraphBuildJob(IdMixin, Base):
    __tablename__ = "graph_build_jobs"
    __table_args__ = (
        UniqueConstraint("job_key"),
        UniqueConstraint("build_run_id"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_graph_build_jobs_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND max_attempts >= 1 AND attempts <= max_attempts",
            name="ck_graph_build_jobs_attempts",
        ),
    )
    job_key: Mapped[str] = mapped_column(String(64), index=True)
    position_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    command: Mapped[dict] = mapped_column(JSON)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    build_run_id: Mapped[int | None] = mapped_column(ForeignKey("graph_build_runs.id"))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(100))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GraphBuildSample(IdMixin, Base):
    __tablename__ = "graph_build_samples"
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    included: Mapped[bool] = mapped_column(Boolean)
    exclusion_reasons: Mapped[list] = mapped_column(JSON, default=list)
    effective_weight: Mapped[float] = mapped_column(Float, default=0)


class PositionSkillSupport(IdMixin, Base):
    __tablename__ = "position_skill_supports"
    __table_args__ = (UniqueConstraint("build_run_id", "normalized_skill_id", "evidence_id"),)
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"))
    position_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE")
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    document_id: Mapped[str] = mapped_column(ForeignKey("jd_documents.document_id"))
    requirement_id: Mapped[str] = mapped_column(String(80))
    normalized_skill_id: Mapped[int] = mapped_column(ForeignKey("normalized_skill_records.id"))
    evidence_id: Mapped[int] = mapped_column(ForeignKey("extraction_evidence.id"))
    source_requirement_id: Mapped[int] = mapped_column(
        ForeignKey("extracted_candidate_requirements.id")
    )
    extraction_record_id: Mapped[int] = mapped_column(ForeignKey("jd_extraction_records.id"))
    modality: Mapped[str] = mapped_column(String(20))


class PositionSkillRelationDraft(IdMixin, Base):
    __tablename__ = "position_skill_relation_drafts"
    __table_args__ = (
        UniqueConstraint("build_run_id", "skill_id"),
        ForeignKeyConstraint(
            ["build_run_id", "position_id"],
            ["graph_build_runs.id", "graph_build_runs.position_id"],
            name="fk_relation_draft_build_position",
        ),
    )
    build_run_id: Mapped[int] = mapped_column(Integer)
    position_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE")
    )
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(30), default="candidate")
    metrics: Mapped[dict] = mapped_column(JSON)
    statistics: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    explanation: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    auto_weight: Mapped[float] = mapped_column(Float)
    manual_weight: Mapped[float | None] = mapped_column(Float)
    final_weight: Mapped[float] = mapped_column(Float)
    auto_confidence: Mapped[float] = mapped_column(Float)
    manual_confidence: Mapped[float | None] = mapped_column(Float)
    final_confidence: Mapped[float] = mapped_column(Float)
    auto_importance_level: Mapped[str] = mapped_column(String(20))
    manual_importance_level: Mapped[str | None] = mapped_column(String(20))
    final_importance_level: Mapped[str] = mapped_column(String(20))
    trend_score: Mapped[float | None] = mapped_column(Float)


class PositionRequirementAggregateDraft(IdMixin, Base):
    __tablename__ = "position_requirement_aggregate_drafts"
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"))
    kind: Mapped[str] = mapped_column(String(30))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(30), default="included", server_default="included"
    )


class PositionTaskAggregateDraft(IdMixin, Base):
    __tablename__ = "position_task_aggregate_drafts"
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        String(30), default="included", server_default="included"
    )


class ReviewTask(IdMixin, Base):
    __tablename__ = "review_tasks"
    __table_args__ = (
        Index(
            "ix_review_tasks_build_run_id_status",
            "build_run_id",
            "status",
        ),
        Index(
            "ix_review_tasks_object_type_build_run_id",
            "object_type",
            "build_run_id",
        ),
    )
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(80))
    build_run_id: Mapped[int | None] = mapped_column(ForeignKey("graph_build_runs.id"))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewTaskEvent(IdMixin, Base):
    __tablename__ = "review_task_events"
    task_id: Mapped[int] = mapped_column(ForeignKey("review_tasks.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(80))


class GraphVersion(IdMixin, Base):
    __tablename__ = "graph_versions"
    __table_args__ = (
        UniqueConstraint("position_id", "version_number"),
        UniqueConstraint("position_id", "version_name"),
        Index("uq_graph_version_build_run", "build_run_id", unique=True),
    )
    position_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE")
    )
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"), nullable=False)
    release_id: Mapped[str | None] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    version_name: Mapped[str] = mapped_column(String(80))
    snapshot: Mapped[dict] = mapped_column(JSON)
    source_version: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(50))
    normalization_map_version: Mapped[str] = mapped_column(String(50))
    published_fact_versions: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    skill_catalog_version: Mapped[str] = mapped_column(
        String(128), default="legacy-unspecified", server_default="legacy-unspecified"
    )
    mapping_snapshot_version: Mapped[str] = mapped_column(
        String(128), default="legacy-unspecified", server_default="legacy-unspecified"
    )
    normalization_algorithm_version: Mapped[str] = mapped_column(
        String(128), default="legacy-unspecified", server_default="legacy-unspecified"
    )
    build_config_version: Mapped[str] = mapped_column(
        String(128), default="legacy-unspecified", server_default="legacy-unspecified"
    )
    source_time_window: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    rollback_from_version_id: Mapped[int | None] = mapped_column(ForeignKey("graph_versions.id"))
    published_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(50))
    object_type: Mapped[str] = mapped_column(String(40))
    object_id: Mapped[str] = mapped_column(String(80))
    before_snapshot: Mapped[dict | None] = mapped_column(JSON)
    after_snapshot: Mapped[dict | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(String(80))


class AlgorithmConfig(IdMixin, Base):
    __tablename__ = "algorithm_configs"
    version: Mapped[str] = mapped_column(String(50), unique=True)
    payload: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BuildInputWatermarkRecord(IdMixin, Base):
    __tablename__ = "build_input_watermarks"
    __table_args__ = (
        UniqueConstraint("build_run_id"),
        CheckConstraint("validation_state IN ('present', 'absent')"),
        CheckConstraint(
            "(validation_state = 'present' AND validation_policy_version IS NOT NULL) "
            "OR (validation_state = 'absent' AND validation_policy_version IS NULL)"
        ),
        CheckConstraint("input_coverage >= 0 AND input_coverage <= 1"),
    )
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"), index=True)
    lineage_version: Mapped[str] = mapped_column(String(128))
    source_facts: Mapped[list] = mapped_column(JSON)
    observation_window_start: Mapped[str] = mapped_column(String(80))
    observation_window_end: Mapped[str] = mapped_column(String(80))
    catalog_snapshot_id: Mapped[str] = mapped_column(String(120))
    catalog_source_version: Mapped[str] = mapped_column(String(64))
    validation_state: Mapped[str] = mapped_column(String(10))
    validation_policy_version: Mapped[str | None] = mapped_column(Text)
    mapping_policy_version: Mapped[str] = mapped_column(String(100))
    aggregation_algorithm_version: Mapped[str] = mapped_column(String(100))
    normalized_config: Mapped[dict] = mapped_column(JSON)
    config_version: Mapped[str] = mapped_column(String(100), server_default="config-v1")
    input_coverage: Mapped[float] = mapped_column(Float)


class RelationClaimRecord(IdMixin, Base):
    __tablename__ = "relation_claims"
    __table_args__ = (
        UniqueConstraint("claim_id"),
        UniqueConstraint("graph_version_id", "support_id"),
        CheckConstraint("claim_kind IN ('observed', 'reviewed')"),
        CheckConstraint("source_kind IN ('published_fact', 'legacy_local')"),
    )
    claim_id: Mapped[str] = mapped_column(String(64), index=True)
    graph_version_id: Mapped[int] = mapped_column(ForeignKey("graph_versions.id"), index=True)
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"), index=True)
    support_id: Mapped[int] = mapped_column(ForeignKey("position_skill_supports.id"))
    subject_id: Mapped[str] = mapped_column(
        ForeignKey("standard_positions.position_id", onupdate="CASCADE")
    )
    predicate: Mapped[str] = mapped_column(String(50))
    object_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    claim_kind: Mapped[str] = mapped_column(String(30))
    source_kind: Mapped[str] = mapped_column(String(30))
    source_fact_id: Mapped[str] = mapped_column(String(100))
    source_fact_version: Mapped[str] = mapped_column(String(100))
    requirement_id: Mapped[str] = mapped_column(String(80))
    evidence_refs: Mapped[list] = mapped_column(JSON)
    validation_lineage_lineage_version: Mapped[str | None] = mapped_column(String(64))
    catalog_snapshot_lineage_version: Mapped[str] = mapped_column(String(64))
    mapping_policy_version: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[str] = mapped_column(String(80))
    lineage_version: Mapped[str] = mapped_column(String(64))


class MappingCandidateRecord(IdMixin, Base):
    __tablename__ = "mapping_candidates"
    __table_args__ = (
        UniqueConstraint("candidate_id"),
        CheckConstraint("status IN ('pending', 'accepted', 'rejected', 'no_match', 'superseded')"),
        CheckConstraint("revision >= 1"),
        CheckConstraint("priority >= 0 AND priority <= 1"),
    )
    candidate_id: Mapped[str] = mapped_column(String(80), index=True)
    source_expression: Mapped[str] = mapped_column(String(300), index=True)
    proposed_skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    signals: Mapped[dict] = mapped_column(JSON)
    priority: Mapped[float] = mapped_column(Float, index=True)
    model_version: Mapped[str] = mapped_column(String(100))
    index_version: Mapped[str] = mapped_column(String(100))
    mapping_policy_version: Mapped[str] = mapped_column(String(100))
    affected_contexts: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class MappingReviewDecisionRecord(IdMixin, Base):
    __tablename__ = "mapping_review_decisions"
    __table_args__ = (
        UniqueConstraint("candidate_id", "candidate_revision"),
        CheckConstraint("decision IN ('accept', 'reject', 'no_match', 'supersede')"),
        CheckConstraint(
            "(decision = 'supersede' AND replacement_candidate_id IS NOT NULL) "
            "OR (decision != 'supersede' AND replacement_candidate_id IS NULL)"
        ),
    )
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_candidates.candidate_id"), index=True
    )
    candidate_revision: Mapped[int] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(20))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[str] = mapped_column(String(80))
    effective_scope: Mapped[str] = mapped_column(String(120))
    replacement_candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("mapping_candidates.candidate_id")
    )


class EffectiveMappingRecord(IdMixin, Base):
    __tablename__ = "effective_mapping_records"
    __table_args__ = (UniqueConstraint("review_decision_id", "source_fact_id", "requirement_id"),)
    review_decision_id: Mapped[int] = mapped_column(
        ForeignKey("mapping_review_decisions.id"), index=True
    )
    supersedes_effective_mapping_id: Mapped[int | None] = mapped_column(
        ForeignKey("effective_mapping_records.id")
    )
    source_fact_id: Mapped[str] = mapped_column(String(100), index=True)
    requirement_id: Mapped[str] = mapped_column(String(80), index=True)
    source_expression: Mapped[str] = mapped_column(String(300))
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    policy_version: Mapped[str] = mapped_column(String(100))


class DownstreamDependencyReference(IdMixin, Base):
    __tablename__ = "downstream_dependency_references"
    __table_args__ = (
        UniqueConstraint("consumer_system", "reference_type", "reference_id", "graph_version_id"),
        CheckConstraint("consumer_system IN ('matching', 'trend', 'discovery')"),
    )
    consumer_system: Mapped[str] = mapped_column(String(30), index=True)
    reference_type: Mapped[str] = mapped_column(String(80))
    reference_id: Mapped[str] = mapped_column(String(120), index=True)
    graph_version_id: Mapped[int] = mapped_column(ForeignKey("graph_versions.id"), index=True)
    metadata_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class DependencyEvent(IdMixin, Base):
    __tablename__ = "dependency_events"
    __table_args__ = (UniqueConstraint("event_key"),)
    event_key: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[str] = mapped_column(String(100), index=True)
    change_kind: Mapped[str] = mapped_column(String(50))
    before_snapshot: Mapped[dict] = mapped_column(JSON)
    after_snapshot: Mapped[dict] = mapped_column(JSON)
    impact_snapshot: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    trace_id: Mapped[str] = mapped_column(String(80))


class DependencyAnalysisRunRecord(IdMixin, Base):
    __tablename__ = "dependency_analysis_runs"
    __table_args__ = (UniqueConstraint("build_run_id", "policy_hash"),)
    build_run_id: Mapped[int] = mapped_column(ForeignKey("graph_build_runs.id"), index=True)
    policy_hash: Mapped[str] = mapped_column(String(64))
    policy: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20))
    summary: Mapped[dict] = mapped_column(JSON)


class DependencyCandidateRecord(IdMixin, Base):
    __tablename__ = "dependency_candidates"
    __table_args__ = (
        UniqueConstraint("analysis_run_id", "prerequisite_skill_id", "advanced_skill_id"),
        CheckConstraint("claim_kind = 'inferred_candidate'"),
    )
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("dependency_analysis_runs.id"), index=True
    )
    prerequisite_skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    advanced_skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    metrics: Mapped[dict] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON)
    claim_kind: Mapped[str] = mapped_column(String(30), default="inferred_candidate")


class DependencyReviewDecisionRecord(IdMixin, Base):
    __tablename__ = "dependency_review_decisions"
    __table_args__ = (
        UniqueConstraint("dependency_candidate_id"),
        CheckConstraint("decision IN ('accept', 'reject')"),
    )
    dependency_candidate_id: Mapped[int] = mapped_column(
        ForeignKey("dependency_candidates.id"), index=True
    )
    decision: Mapped[str] = mapped_column(String(10))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[str] = mapped_column(String(80))


class GraphVersionDependencyRecord(IdMixin, Base):
    __tablename__ = "graph_version_dependencies"
    __table_args__ = (
        UniqueConstraint("graph_version_id", "prerequisite_skill_id", "advanced_skill_id"),
        CheckConstraint("claim_kind = 'reviewed'"),
    )
    graph_version_id: Mapped[int] = mapped_column(ForeignKey("graph_versions.id"), index=True)
    dependency_candidate_id: Mapped[int] = mapped_column(ForeignKey("dependency_candidates.id"))
    review_decision_id: Mapped[int] = mapped_column(ForeignKey("dependency_review_decisions.id"))
    prerequisite_skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    advanced_skill_id: Mapped[str] = mapped_column(ForeignKey("skills.skill_id"))
    metrics: Mapped[dict] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON)
    claim_kind: Mapped[str] = mapped_column(String(30), default="reviewed")
    policy_version: Mapped[str] = mapped_column(String(100))


class ProjectionManifestRecord(IdMixin, Base):
    __tablename__ = "projection_manifests"
    __table_args__ = (
        UniqueConstraint("graph_version_id", "projection_version"),
        CheckConstraint("node_count >= 0"),
        CheckConstraint("edge_count >= 0"),
    )
    graph_version_id: Mapped[int] = mapped_column(ForeignKey("graph_versions.id"), index=True)
    projection_version: Mapped[str] = mapped_column(String(100))
    watermark_lineage_version: Mapped[str] = mapped_column(String(64))
    node_count: Mapped[int] = mapped_column(Integer)
    edge_count: Mapped[int] = mapped_column(Integer)
    source_version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)


def _reject_innovation_immutable_update(_mapper, _connection, target):
    raise ValueError(f"{target.__class__.__name__} is immutable")


def _reject_innovation_immutable_delete(_mapper, _connection, target):
    raise ValueError(f"{target.__class__.__name__} cannot be deleted")


for _immutable_model in (
    BuildInputWatermarkRecord,
    RelationClaimRecord,
    MappingReviewDecisionRecord,
    DependencyAnalysisRunRecord,
    DependencyCandidateRecord,
    ProjectionManifestRecord,
    EffectiveMappingRecord,
    DependencyReviewDecisionRecord,
    GraphVersionDependencyRecord,
):
    event.listen(_immutable_model, "before_update", _reject_innovation_immutable_update)
    event.listen(_immutable_model, "before_delete", _reject_innovation_immutable_delete)


@event.listens_for(GraphVersion, "before_update")
def prevent_graph_version_update(_mapper, _connection, _target):
    raise ValueError("published graph versions are immutable")


@event.listens_for(GraphVersion, "before_delete")
def prevent_graph_version_delete(_mapper, _connection, _target):
    raise ValueError("published graph versions cannot be deleted")
