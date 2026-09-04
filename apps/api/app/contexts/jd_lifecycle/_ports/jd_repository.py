from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Literal, Protocol, TypeAlias

from app.contexts.catalog import SkillRepository
from app.domain.jd import Document, NormalizationResult
from app.domain.jd_skill_catalog import CatalogAlias, CatalogIdentity, CatalogSkill
from app.domain.json_types import JsonObject, JsonValue as JsonValue, freeze_json_object
from app.domain.jd_policies import JDParseCommand
from app.domain.tasks import TASK_TRANSITIONS, TaskStatus


FileExtractionStatus: TypeAlias = Literal["completed", "failed"]
def _validate_unit_interval(value: float, *, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if not 0 <= value <= 1:
        raise ValueError(f"{field} must be between 0 and 1")


@dataclass(frozen=True)
class Actor:
    id: str
    role: str


@dataclass(frozen=True)
class JDSummaryDTO:
    total: int
    awaiting_review: int
    reviewed: int
    published: int
    failed: int


@dataclass(frozen=True)
class FileUpload:
    filename: str
    content_type: str | None
    content: bytes


@dataclass(frozen=True)
class FileAssetDTO:
    id: str
    owner_user_id: str
    filename: str
    content_type: str | None
    path: str
    size: int
    purpose: str | None


@dataclass(frozen=True)
class FileTextExtractionResult:
    status: FileExtractionStatus
    text: str
    provider: str
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str):
            raise ValueError("File extraction status must be a string")
        if self.status not in ("completed", "failed"):
            raise ValueError(f"Unsupported file extraction status: {self.status}")
        if not isinstance(self.text, str):
            raise ValueError("File extraction text must be a string")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("File extraction provider must not be empty")
        if self.error_code is not None and not isinstance(self.error_code, str):
            raise ValueError("File extraction error_code must be a string or null")
        if self.error_message is not None and not isinstance(self.error_message, str):
            raise ValueError("File extraction error_message must be a string or null")
        if self.status == "completed":
            if not self.text.strip():
                raise ValueError("Completed file extraction must contain text")
            if self.error_code is not None or self.error_message is not None:
                raise ValueError("Completed file extraction must not contain errors")
            return
        if self.text:
            raise ValueError("Failed file extraction must not contain text")
        if not isinstance(self.error_code, str) or not self.error_code.strip():
            raise ValueError("Failed file extraction must contain error code and message")
        if not isinstance(self.error_message, str) or not self.error_message.strip():
            raise ValueError("Failed file extraction must contain error code and message")


@dataclass(frozen=True)
class JDCreateCommand:
    source_type: str
    source_name: str | None
    enterprise_id: str | None
    title: str
    raw_text: str
    cleaned_text: str | None = None
    publish_date: date | None = None
    url: str | None = None
    file_id: str | None = None
    parse_status: str = "pending"
    input_extraction_status: str = "not_required"
    input_provider: str | None = None
    input_error_code: str | None = None
    input_error_message: str | None = None


@dataclass(frozen=True)
class JDParseStatusUpdate:
    parse_status: str


@dataclass(frozen=True)
class JDRawTextUpdate:
    raw_text: str
    parse_status: str
    input_extraction_status: str
    input_provider: str | None
    input_error_code: str | None
    input_error_message: str | None


@dataclass(frozen=True)
class JDCopyRiskUpdate:
    copy_risk_score: float

    def __post_init__(self) -> None:
        _validate_unit_interval(self.copy_risk_score, field="copy_risk_score")


@dataclass(frozen=True)
class JDInflationUpdate:
    inflation_score: float

    def __post_init__(self) -> None:
        _validate_unit_interval(self.inflation_score, field="inflation_score")


@dataclass(frozen=True)
class JDDownweightUpdate:
    is_downweighted: bool


JDUpdateCommand: TypeAlias = (
    JDParseStatusUpdate
    | JDRawTextUpdate
    | JDCopyRiskUpdate
    | JDInflationUpdate
    | JDDownweightUpdate
)


@dataclass(frozen=True)
class JDDTO:
    id: str
    source_type: str
    source_name: str | None
    enterprise_id: str | None
    title: str
    raw_text: str
    publish_date: date | None
    url: str | None
    file_id: str | None
    parse_status: str
    input_extraction_status: str
    input_provider: str | None
    input_error_code: str | None
    input_error_message: str | None
    copy_risk_score: float | None
    inflation_score: float | None
    is_downweighted: bool
    created_at: datetime | None
    updated_at: datetime | None
    source_jd_id: str | None = None
    source_jd_version_id: str | None = None
    extraction_task_id: str | None = None
    cleaned_text: str | None = None
    source_platform: str | None = None

    def __post_init__(self) -> None:
        if self.copy_risk_score is not None:
            _validate_unit_interval(self.copy_risk_score, field="copy_risk_score")
        if self.inflation_score is not None:
            _validate_unit_interval(self.inflation_score, field="inflation_score")


@dataclass(frozen=True)
class JDSkillDTO:
    raw_skill: str
    normalized_skill_id: str | None
    confidence: float
    resolution_status: str | None = None

    def __post_init__(self) -> None:
        _validate_unit_interval(self.confidence, field="confidence")


@dataclass(frozen=True)
class JDLegacyFields:
    position_title: str | None
    responsibilities: tuple[str, ...]
    required_skills: tuple[JDSkillDTO, ...]
    bonus_skills: tuple[JDSkillDTO, ...]
    education: str | None
    experience: str | None
    industry: str | None
    tools: tuple[str, ...]


@dataclass(frozen=True)
class JDParseResultDTO:
    id: str
    jd_id: str
    position_title: str | None
    responsibilities: tuple[str, ...]
    required_skills: tuple[JDSkillDTO, ...]
    bonus_skills: tuple[JDSkillDTO, ...]
    education: str | None
    experience: str | None
    industry: str | None
    tools: tuple[str, ...]
    business_scenarios: tuple[str, ...]
    parse_confidence: float
    need_review: bool
    extraction_result: JsonObject | None
    normalized_result: JsonObject | None
    execution_metadata: JsonObject | None
    schema_version: str
    normalization_schema_version: str
    workflow_status: str
    created_at: datetime | None
    updated_at: datetime | None

    def __post_init__(self) -> None:
        _validate_unit_interval(self.parse_confidence, field="parse_confidence")
        if self.extraction_result is not None:
            object.__setattr__(self, "extraction_result", freeze_json_object(
                self.extraction_result, field="extraction_result"
            ))
        if self.normalized_result is not None:
            object.__setattr__(self, "normalized_result", freeze_json_object(
                self.normalized_result, field="normalized_result"
            ))
        if self.execution_metadata is not None:
            object.__setattr__(self, "execution_metadata", freeze_json_object(
                self.execution_metadata, field="execution_metadata"
            ))


@dataclass(frozen=True)
class JDPublicationDTO:
    id: str
    parse_result_id: str
    jd_id: str
    source_jd_id: str | None
    source_jd_version_id: str | None
    extraction_task_id: str | None
    document_id: str
    schema_version: str
    normalization_schema_version: str
    idempotency_key: str
    snapshot_payload: JsonObject
    outbox_event_id: str
    outbox_status: str
    published_by: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "snapshot_payload",
            freeze_json_object(self.snapshot_payload, field="snapshot_payload"),
        )


@dataclass(frozen=True)
class TaskDTO:
    id: str
    task_type: str
    status: TaskStatus
    progress: float
    input_payload: JsonObject
    result_payload: JsonObject
    result_reference: str | None
    error_code: str | None
    error_message: str | None
    created_by: str | None
    attempt_count: int
    log_entries: tuple["TaskLogDTO", ...]
    created_at: datetime | None
    updated_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in TASK_TRANSITIONS:
            raise ValueError(f"Unsupported task status: {self.status}")
        _validate_unit_interval(self.progress, field="progress")
        if isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int):
            raise ValueError("attempt_count must be an integer")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        if self.error_code is not None and not isinstance(self.error_code, str):
            raise ValueError("error_code must be a string or null")
        if self.error_message is not None and not isinstance(self.error_message, str):
            raise ValueError("error_message must be a string or null")
        object.__setattr__(self, "input_payload", freeze_json_object(
            self.input_payload, field="input_payload"
        ))
        object.__setattr__(self, "result_payload", freeze_json_object(
            self.result_payload, field="result_payload"
        ))


@dataclass(frozen=True)
class TaskLogDTO:
    status: TaskStatus
    at: str
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in TASK_TRANSITIONS:
            raise ValueError(f"Unsupported task log status: {self.status}")
        if not isinstance(self.at, str) or not self.at:
            raise ValueError("Task log at must be a non-empty string")
        if self.message is not None and not isinstance(self.message, str):
            raise ValueError("Task log message must be a string or null")


@dataclass(frozen=True)
class JDParseResultCreateCommand:
    jd_id: str
    legacy: JDLegacyFields
    business_scenarios: tuple[str, ...]
    parse_confidence: float
    need_review: bool
    extraction_result: JsonObject | None = None
    normalized_result: JsonObject | None = None
    execution_metadata: JsonObject | None = None
    schema_version: str = "v2"
    normalization_schema_version: str = "v2"
    workflow_status: str = "draft"

    def __post_init__(self) -> None:
        _validate_unit_interval(self.parse_confidence, field="parse_confidence")
        if self.extraction_result is not None:
            object.__setattr__(self, "extraction_result", freeze_json_object(
                self.extraction_result, field="extraction_result"
            ))
        if self.normalized_result is not None:
            object.__setattr__(self, "normalized_result", freeze_json_object(
                self.normalized_result, field="normalized_result"
            ))
        if self.execution_metadata is not None:
            object.__setattr__(self, "execution_metadata", freeze_json_object(
                self.execution_metadata, field="execution_metadata"
            ))


@dataclass(frozen=True)
class JDParseResultReviewUpdate:
    parse_confidence: float | None = None
    need_review: bool | None = None
    workflow_status: str | None = None

    def __post_init__(self) -> None:
        if self.parse_confidence is not None:
            _validate_unit_interval(self.parse_confidence, field="parse_confidence")


@dataclass(frozen=True)
class JDParseResultVersionedUpdate:
    legacy: JDLegacyFields
    extraction_result: JsonObject
    normalized_result: JsonObject
    schema_version: str
    normalization_schema_version: str
    need_review: bool
    workflow_status: str
    execution_metadata: JsonObject | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "extraction_result", freeze_json_object(
            self.extraction_result, field="extraction_result"
        ))
        object.__setattr__(self, "normalized_result", freeze_json_object(
            self.normalized_result, field="normalized_result"
        ))
        if self.execution_metadata is not None:
            object.__setattr__(self, "execution_metadata", freeze_json_object(
                self.execution_metadata, field="execution_metadata"
            ))


@dataclass(frozen=True)
class JDParseResultResetUpdate:
    need_review: bool = True
    workflow_status: str = "draft"


JDParseResultUpdateCommand: TypeAlias = (
    JDParseResultReviewUpdate | JDParseResultVersionedUpdate | JDParseResultResetUpdate
)


@dataclass(frozen=True)
class JDCreated:
    jd_id: str
    parse_status: str
    created_at: datetime | None


@dataclass(frozen=True)
class JDBatch:
    items: tuple[JDDTO, ...]


@dataclass(frozen=True)
class JDParseBatch:
    items: tuple[JDParseResultDTO, ...]


@dataclass(frozen=True)
class SimilarJD:
    jd_id: str
    similarity: float
    source_name: str | None
    text_overlap: float = 0.0
    skill_overlap: float = 0.0
    length_similarity: float = 0.0

    def __post_init__(self) -> None:
        _validate_unit_interval(self.similarity, field="similarity")
        _validate_unit_interval(self.text_overlap, field="text_overlap")
        _validate_unit_interval(self.skill_overlap, field="skill_overlap")
        _validate_unit_interval(self.length_similarity, field="length_similarity")


@dataclass(frozen=True)
class DuplicateCheckResult:
    jd_id: str
    copy_risk_score: float
    similar_jds: tuple[SimilarJD, ...]
    recommended_action: str
    reason: str

    def __post_init__(self) -> None:
        _validate_unit_interval(self.copy_risk_score, field="copy_risk_score")


@dataclass(frozen=True)
class DuplicateCheckBatch:
    items: tuple[DuplicateCheckResult, ...]


@dataclass(frozen=True)
class AbnormalSkill:
    skill_id: str
    skill_name: str
    reason: str


@dataclass(frozen=True)
class InflationCheckResult:
    jd_id: str
    inflation_score: float
    abnormal_skills: tuple[AbnormalSkill, ...]
    recommended_action: str
    mismatch_reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_unit_interval(self.inflation_score, field="inflation_score")


@dataclass(frozen=True)
class InflationCheckBatch:
    items: tuple[InflationCheckResult, ...]


@dataclass(frozen=True)
class SkillReviewResult:
    jd_id: str
    parse_result_id: str
    skill_id: str
    raw_skill: str
    normalized_skill_id: str | None
    abnormal: bool
    abnormal_reason: str | None
    review_status: str
    implementation_status: str


@dataclass(frozen=True)
class SkillCatalogMappingResult:
    jd_id: str
    parse_result_id: str
    source_name: str
    requirement_id: str | None
    skill_id: str | None
    canonical_name: str | None
    resolution_status: str
    closed_blocking_flags: int
    review_status: str


@dataclass(frozen=True)
class JDExportFile:
    filename: str
    media_type: str
    content_base64: str
    worksheets: tuple[str, ...]


class JDExportPort(Protocol):
    def export(
        self, document: Document, normalization: NormalizationResult
    ) -> JDExportFile: ...


@dataclass(frozen=True)
class JDSchemaBundle:
    document: Document
    normalization: NormalizationResult


@dataclass(frozen=True)
class JDSchemaPersistence:
    extraction_payload: JsonObject
    normalization_payload: JsonObject
    schema_version: str
    normalization_schema_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "extraction_payload", freeze_json_object(
            self.extraction_payload, field="extraction_payload"
        ))
        object.__setattr__(self, "normalization_payload", freeze_json_object(
            self.normalization_payload, field="normalization_payload"
        ))


@dataclass(frozen=True)
class JDSchemaView:
    extraction_result: JsonObject | None
    normalized_result: JsonObject | None
    extraction_status: str
    normalization_status: str

    def __post_init__(self) -> None:
        if self.extraction_result is not None:
            object.__setattr__(self, "extraction_result", freeze_json_object(
                self.extraction_result, field="extraction_result"
            ))
        if self.normalized_result is not None:
            object.__setattr__(self, "normalized_result", freeze_json_object(
                self.normalized_result, field="normalized_result"
            ))


class JDSchemaPort(Protocol):
    def build(self, document_id: str, raw_text: str, fallback_title: str) -> JDSchemaBundle: ...
    def load(
        self,
        extraction_payload: JsonObject,
        normalization_payload: JsonObject,
        *,
        schema_version: str | None = None,
        normalization_schema_version: str | None = None,
    ) -> JDSchemaBundle: ...
    def edit(
        self,
        extraction_payload: JsonObject,
        normalization_payload: JsonObject | None,
        *,
        schema_version: str | None = None,
        normalization_schema_version: str | None = None,
    ) -> JDSchemaBundle: ...
    def persist(self, bundle: JDSchemaBundle) -> JDSchemaPersistence: ...
    def legacy(self, bundle: JDSchemaBundle, fallback_title: str) -> JDLegacyFields: ...
    def validate_publishable(self, bundle: JDSchemaBundle) -> None: ...
    def view(
        self,
        extraction_payload: JsonObject | None,
        normalization_payload: JsonObject | None,
        *,
        schema_version: str,
        normalization_schema_version: str,
    ) -> JDSchemaView: ...


class JDRepository(Protocol):
    def owned_enterprise_ids(self, user_id: str) -> list[str]: ...

    def create_jd(self, command: JDCreateCommand) -> JDDTO: ...

    def list_jds(self, enterprise_ids: list[str] | None = None) -> list[JDDTO]: ...

    def list_jds_page(
        self,
        enterprise_ids: list[str] | None,
        *,
        offset: int,
        limit: int,
        query: str | None,
        sort: str = "created_desc",
    ) -> tuple[list[JDDTO], int]: ...

    def summarize_jds(self, enterprise_ids: list[str] | None) -> JDSummaryDTO: ...

    def get_jd(self, jd_id: str) -> JDDTO | None: ...

    def update_jd(self, jd_id: str, command: JDUpdateCommand) -> JDDTO: ...

    def get_parse_result(self, jd_id: str) -> JDParseResultDTO | None: ...

    def list_parse_results(self, jd_ids: list[str]) -> list[JDParseResultDTO]: ...

    def get_parse_result_by_id(
        self, parse_result_id: str
    ) -> JDParseResultDTO | None: ...

    def create_parse_result(self, command: JDParseResultCreateCommand) -> JDParseResultDTO: ...

    def update_parse_result(
        self, parse_result_id: str, command: JDParseResultUpdateCommand
    ) -> JDParseResultDTO: ...

    def upsert_parse_result(
        self, jd_id: str, command: JDParseResultCreateCommand
    ) -> JDParseResultDTO: ...

    def all_other_jds(self, jd_id: str) -> list[JDDTO]: ...

    def delete_jd(self, jd_id: str) -> None: ...

    def deprecate_jd(self, jd_id: str) -> None: ...

    def update_cleaned_text(self, jd_id: str, cleaned_text: str) -> None: ...

    def flush(self) -> None: ...


class JDPublicationRepository(Protocol):
    def get_by_parse_result(
        self, parse_result_id: str
    ) -> JDPublicationDTO | None: ...

    def add(
        self,
        parse_result_id: str,
        *,
        published_by: str,
        published_by_role: str,
        validation_lineage: JsonObject,
    ) -> JDPublicationDTO: ...


@dataclass(frozen=True)
class ValidationPublicationGateDecision:
    decision: str
    code: str
    validation_task_id: str | None = None
    validation_report_id: str | None = None
    validation_snapshot_id: str | None = None
    governance_review_task_id: str | None = None
    conclusion: str | None = None
    policy_binding_version: str | None = None
    bundle_id: str | None = None


class ValidationPublicationGatePort(Protocol):
    def evaluate(
        self,
        *,
        jd_id: str,
        parse_result_id: str,
    ) -> ValidationPublicationGateDecision: ...


class JDReviewTaskPort(Protocol):
    def ensure_active(
        self,
        parse_result_id: str,
        *,
        reason: str,
        priority: Literal["normal", "high"] = "normal",
    ) -> str: ...

    def approve_active(
        self,
        parse_result_id: str,
        *,
        task_id: str | None = None,
        actor_id: str,
        actor_role: str,
        comment: str | None = None,
    ) -> None: ...


class FileRepository(Protocol):
    def save_upload(
        self, actor: Actor, upload: FileUpload, *, purpose: str
    ) -> FileAssetDTO: ...


class FileTextExtractor(Protocol):
    def extract_text(
        self, file_asset: FileAssetDTO, *, use_ocr: bool
    ) -> FileTextExtractionResult: ...


class TaskRepository(Protocol):
    def get_task(self, task_id: str) -> TaskDTO | None: ...

    def create_succeeded_task(
        self,
        actor: Actor,
        task_type: str,
        *,
        input_payload: JsonObject,
        result_payload: JsonObject,
        result_reference: str | None,
        task_id: str | None = None,
        execution_metadata: JsonObject | None = None,
    ) -> TaskDTO: ...

    def create_succeeded_parse_task(
        self,
        actor: Actor,
        command: JDParseCommand,
        result: JDParseResultDTO,
        schema_view: JDSchemaView,
    ) -> TaskDTO: ...


class JDUoW(Protocol):
    jds: JDRepository
    publications: JDPublicationRepository
    files: FileRepository
    file_text_extractor: FileTextExtractor
    tasks: TaskRepository
    skills: SkillRepository
    validation_publication_gate: ValidationPublicationGatePort
    review_tasks: JDReviewTaskPort

    def __enter__(self) -> "JDUoW": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def catalog_entries(
        self,
    ) -> tuple[tuple[CatalogSkill, ...], tuple[CatalogAlias, ...]]: ...

    def catalog_identity(self) -> CatalogIdentity: ...

    def position_catalog_identity(self) -> CatalogIdentity: ...

    def position_catalog_entry(
        self, position_id: str
    ) -> tuple[
        str,
        str,
        str,
        str | None,
        str | None,
        tuple[str, ...],
        str,
        str,
    ] | None: ...

    def stage_validation_for_parse_result(self, parse_result_id: str) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
