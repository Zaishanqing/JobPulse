from dataclasses import asdict
import re
import unicodedata

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from app.api.upload_limits import UploadSizeLimitExceeded, read_upload
from app.core.config import settings
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.cv_ingestion import get_cv_ingestion_use_cases
from app.contexts.cv_ingestion import (
    CVExtractionBlocked,
    CVExtractionConflict,
    CVExtractionNotFound,
    CVConfirmationResult,
    CVFieldDecision,
    CVFileInputError,
    CVReviewConfirmation,
    CVReviewConflict,
    CVSnapshotNotFound,
    CVIngestionUseCases,
)
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.json_types import thaw_json_object
from app.domain.resumes import ResumeRuleViolation
from app.schemas.cv_contract import (
    CVConfirmationEnvelope,
    CVExtractionTaskEnvelope,
    CVReviewEnvelope,
    SourceCVImportEnvelope,
    ValidatedCVSnapshotEnvelope,
)


router = APIRouter(tags=["cv-ingestion"])


CV_REVIEW_FIELDS = {
    "personal_info": (
        ("expected_position", "目标岗位"), ("expected_location", "期望地点"),
        ("work_status", "求职状态"), ("available_date", "到岗时间"),
    ),
    "education": (
        ("school", "院校"), ("college", "学院"), ("major", "专业"),
        ("degree", "学位"), ("date.start", "入学时间"),
        ("date.end", "毕业时间"), ("gpa", "GPA"),
        ("gpa_scale", "GPA 满分"), ("location", "地点"),
        ("school_tag", "院校标签"),
    ),
    "work_experience": (
        ("company", "单位"), ("position", "职位"), ("department", "部门"),
        ("date.start", "开始时间"), ("date.end", "结束时间"),
        ("location", "地点"), ("work_type", "工作类型"),
    ),
    "project_experience": (
        ("name", "项目名称"), ("role", "角色"), ("affiliation", "所属单位"),
        ("date.start", "开始时间"), ("date.end", "结束时间"),
    ),
    "skills": (("name", "技能"), ("proficiency", "熟练度")),
    "languages": (("language", "语言"), ("proficiency", "熟练度")),
    "certificates": (
        ("name", "证书"), ("kind", "证书类型"),
        ("issuing_body", "颁发机构"), ("date", "取得时间"),
    ),
    "awards": (
        ("name", "奖项"), ("level", "级别"),
        ("issuing_body", "颁发机构"), ("date", "获奖时间"),
    ),
    "publications": (
        ("title", "论文标题"), ("venue", "发表 venue"),
        ("author_role", "作者身份"), ("author_order", "作者顺序"),
        ("status", "发表状态"), ("year", "年份"),
        ("date", "发表时间"), ("doi", "DOI"), ("url", "链接"),
    ),
    "patents": (
        ("title", "专利名称"), ("patent_number", "专利号"),
        ("status", "专利状态"), ("role", "发明人身份"),
        ("inventor_order", "发明人顺序"), ("year", "年份"),
        ("date", "申请或授权时间"),
    ),
    "research_outputs": (
        ("name", "成果名称"), ("output_type", "成果类型"),
        ("role", "承担角色"), ("date", "完成时间"), ("url", "链接"),
    ),
    "self_evaluation": (("content", "自我评价"),),
}


class SourceCVImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_record_id: str = Field(min_length=1, max_length=128)
    raw_text: str = Field(min_length=1)
    source_platform: str = Field(default="personal_resume", min_length=1, max_length=64)


class CVFieldDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_id: str = Field(min_length=1, max_length=128)
    field_type: str = Field(min_length=1, max_length=64)
    section: str = Field(min_length=1, max_length=64)
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    field_path: str | None = Field(default=None, min_length=1, max_length=128)
    decision: str = Field(pattern="^(accept|correct|unknown|remove)$")
    corrected_value: str | None = None
    correction_reason: str | None = None
    evidence_quote: str | None = None
    evidence_start: int | None = Field(default=None, ge=0)
    evidence_end: int | None = Field(default=None, ge=0)


class CVConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_review_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    field_decisions: list[CVFieldDecisionRequest] = Field(default_factory=list)
    normalization_version: str | None = Field(default=None, min_length=1, max_length=64)
    taxonomy_version: str | None = Field(default=None, min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)


def _task_data(task) -> dict:
    values = asdict(task)
    if task.execution_metadata is not None:
        values["execution_metadata"] = thaw_json_object(task.execution_metadata)
    if task.validation_report_payload is not None:
        values["validation_report_payload"] = thaw_json_object(
            task.validation_report_payload
        )
    if task.review_payload is not None:
        values["review_payload"] = thaw_json_object(task.review_payload)
    current_stage = (
        values.get("execution_metadata", {}).get("current_stage")
        if isinstance(values.get("execution_metadata"), dict)
        else None
    )
    if task.status == "failed":
        current_stage = "failed"
    elif task.status == "cancelled":
        current_stage = "cancelled"
    elif task.status == "succeeded":
        current_stage = (
            "succeeded"
            if task.confirmation_status == "confirmed"
            else "review_pending"
        )
    elif task.status == "pending":
        current_stage = "queued"
    values["processing_stage"] = current_stage or "extracting"
    return values


def _confirmation_data(result: CVConfirmationResult) -> dict:
    return asdict(result)


def _snapshot_data(snapshot) -> dict:
    values = asdict(snapshot)
    values["extraction_payload"] = thaw_json_object(snapshot.extraction_payload)
    values["normalized_payload"] = thaw_json_object(snapshot.normalized_payload)
    values["findings_payload"] = thaw_json_object(snapshot.findings_payload)
    values["execution_metadata"] = thaw_json_object(snapshot.execution_metadata)
    if snapshot.evidence_payload is not None:
        values["evidence_payload"] = thaw_json_object(snapshot.evidence_payload)
    if snapshot.field_decisions is not None:
        values["field_decisions"] = [
            thaw_json_object(item) for item in snapshot.field_decisions
        ]
    return values


def _review_evidence(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    quote = value.get("quote")
    if not isinstance(quote, str) or not quote:
        return None
    return {
        "source_document_id": str(value.get("source_document_id") or ""),
        "source_id": str(value.get("source_id") or ""),
        "quote": quote,
        "start": value.get("start") if isinstance(value.get("start"), int) else None,
        "end": value.get("end") if isinstance(value.get("end"), int) else None,
        "alignment": str(value.get("alignment") or "unresolved"),
        "occurrence_index": (
            value.get("occurrence_index")
            if isinstance(value.get("occurrence_index"), int)
            else None
        ),
    }


def _field_value(item: dict, field_path: str):
    value = item
    for segment in field_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(segment)
    return value


def _field_evidence(item: dict, field_path: str) -> dict | None:
    root_field = field_path.split(".", 1)[0]
    bindings = item.get("field_evidence")
    for binding in bindings if isinstance(bindings, list) else []:
        if isinstance(binding, dict) and binding.get("field_name") == root_field:
            evidence = _review_evidence(binding.get("evidence"))
            if evidence is not None:
                return evidence
    return _review_evidence(item.get("evidence"))


def _reviewable_fields(review: dict) -> list[dict]:
    extraction = review.get("extraction")
    extraction = extraction if isinstance(extraction, dict) else {}
    normalized = review.get("normalized")
    normalized = normalized if isinstance(normalized, dict) else {}
    raw_skills = normalized.get("normalized_skills")
    raw_skills = raw_skills if isinstance(raw_skills, list) else []
    skills_by_source = {
        item.get("source_item_id"): item
        for item in raw_skills
        if isinstance(item, dict)
    }
    flags = review.get("review_flags")
    flags = flags if isinstance(flags, list) else []
    fields: list[dict] = []
    for section, field_definitions in CV_REVIEW_FIELDS.items():
        values = extraction.get(section)
        if section == "personal_info":
            section_items = [values] if isinstance(values, dict) else []
        else:
            section_items = values if isinstance(values, list) else []
        for item in section_items:
            if not isinstance(item, dict):
                continue
            item_id = (
                "personal_info"
                if section == "personal_info"
                else item.get("entry_id") or item.get("item_id")
            )
            if not isinstance(item_id, str) or not item_id:
                continue
            for field_path, field_label in field_definitions:
                raw_value = _field_value(item, field_path)
                if raw_value is None or raw_value == "":
                    continue
                original_value = str(raw_value)
                suggested_value: str | None = None
                if section == "skills" and field_path == "name":
                    normalized_item = skills_by_source.get(item_id)
                    if isinstance(normalized_item, dict):
                        if normalized_item.get("resolution_status") == "resolved":
                            suggested_value = normalized_item.get("canonical_name")
                        elif normalized_item.get("resolution_status") == "unresolved":
                            suggested_value = normalized_item.get("source_name")
                fields.append(
                    {
                        "field_id": f"{item_id}:{field_path}",
                        "field_type": field_path.split(".")[-1],
                        "section": section,
                        "item_id": item_id,
                        "field_path": field_path,
                        "field_label": field_label,
                        "original_value": original_value,
                        "suggested_value": suggested_value,
                        "evidence": _field_evidence(item, field_path),
                        "flag_codes": [
                            str(flag.get("issue_type") or flag.get("code") or "")
                            for flag in flags
                            if isinstance(flag, dict) and flag.get("item_id") == item_id
                        ],
                    }
                )
    return fields


def _review_flags(review: dict) -> list[dict]:
    flags = review.get("review_flags")
    flags = flags if isinstance(flags, list) else []
    output: list[dict] = []
    for flag in flags:
        if not isinstance(flag, dict):
            continue
        description = flag.get("description")
        suggested_action = flag.get("suggested_action")
        item_id = flag.get("item_id")
        output.append(
            {
                "code": str(flag.get("issue_type") or flag.get("code") or "unknown"),
                "severity": str(flag.get("severity") or "review"),
                "rule_scope": (
                    flag.get("rule_scope")
                    if isinstance(flag.get("rule_scope"), str)
                    else None
                ),
                "message": description if isinstance(description, str) else None,
                "suggested_action": (
                    suggested_action
                    if isinstance(suggested_action, str)
                    else None
                ),
                "item_id": item_id if isinstance(item_id, str) else None,
            }
        )
    return output


def _missing_patent_review(
    review: dict,
    *,
    source_text: str | None,
    source_cv_version_id: str,
) -> tuple[dict, dict] | None:
    """Expose an auditable add/decline decision when OCR only mentions a patent."""
    extraction = review.get("extraction")
    extraction = extraction if isinstance(extraction, dict) else {}
    if isinstance(extraction.get("patents"), list) and extraction["patents"]:
        return None
    text = unicodedata.normalize("NFKC", source_text or "")
    match = re.search(r"专利|patent", text, flags=re.IGNORECASE)
    if match is None:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end < 0:
        line_end = len(text)
    start = max(line_start, match.start() - 80)
    end = min(line_end, match.end() + 120)
    quote = text[start:end].strip()
    stripped = text[start:end]
    leading = len(stripped) - len(stripped.lstrip())
    start += leading
    end = start + len(quote)
    item_id = "new_patent_001"
    evidence = {
        "source_document_id": source_cv_version_id,
        "source_id": "user_correction",
        "quote": quote,
        "start": start,
        "end": end,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    field = {
        "field_id": f"{item_id}:title",
        "field_type": "title",
        "section": "patents",
        "item_id": item_id,
        "field_path": "title",
        "field_label": "专利标题（待补录）",
        "original_value": None,
        "suggested_value": None,
        "evidence": evidence,
        "flag_codes": ["unstructured_patent_mention"],
    }
    flag = {
        "code": "unstructured_patent_mention",
        "severity": "review",
        "rule_scope": "patents",
        "message": "OCR 原文出现专利字样，但未形成可验证的专利对象。",
        "suggested_action": "结合原图补录标题并填写修正理由；信息不足时选择无法判断或移除。",
        "item_id": item_id,
    }
    return field, flag


_MISSING_EDUCATION_REVIEW_FIELDS = (
    ("school", "院校（待补录）"),
    ("degree", "学位（待补录）"),
    ("major", "专业（待补录）"),
    ("date.start", "入学时间（待补录）"),
    ("date.end", "毕业时间（待补录）"),
)


def _missing_education_review(
    review: dict,
    *,
    source_text: str | None,
    source_cv_version_id: str,
) -> tuple[list[dict], dict] | None:
    """Expose structured add/decline fields when validation flags missing education."""
    extraction = review.get("extraction")
    extraction = extraction if isinstance(extraction, dict) else {}
    education = extraction.get("education")
    if isinstance(education, list) and education:
        return None
    flags = review.get("review_flags")
    flags = flags if isinstance(flags, list) else []
    flagged = any(
        isinstance(flag, dict) and flag.get("issue_type") == "missing_education"
        for flag in flags
    )
    if not flagged:
        return None
    text = unicodedata.normalize("NFKC", source_text or "")
    match = re.search(r"教育|学历", text)
    if match is None:
        return None
    line_start = text.rfind("\n", 0, match.start()) + 1
    # 教育小节通常跨多行（学校、学位、起止时间各占一行），证据片段覆盖后续若干行，
    # 便于审核人核对原文并作为 user_correction 证据落库。
    end = min(len(text), match.end() + 200)
    quote = text[line_start:end].strip()
    stripped = text[line_start:end]
    leading = len(stripped) - len(stripped.lstrip())
    start = line_start + leading
    end = start + len(quote)
    item_id = "new_education_001"
    evidence = {
        "source_document_id": source_cv_version_id,
        "source_id": "user_correction",
        "quote": quote,
        "start": start,
        "end": end,
        "alignment": "exact",
        "occurrence_index": 0,
    }
    fields = [
        {
            "field_id": f"{item_id}:{field_path}",
            "field_type": field_path.split(".")[-1],
            "section": "education",
            "item_id": item_id,
            "field_path": field_path,
            "field_label": field_label,
            "original_value": None,
            "suggested_value": None,
            "evidence": evidence,
            "flag_codes": ["missing_education_supplement"],
        }
        for field_path, field_label in _MISSING_EDUCATION_REVIEW_FIELDS
    ]
    flag = {
        "code": "missing_education_supplement",
        "severity": "review",
        "rule_scope": "education",
        "message": "校验发现简历缺少教育经历，但原文中存在教育相关文本。",
        "suggested_action": "结合原图逐字段补录院校、学位等信息并填写修正理由；信息不足的字段选择无法判断或移除。",
        "item_id": item_id,
    }
    return fields, flag


def _blocking_reasons(task, review: dict) -> list[str]:
    reasons: list[str] = []
    if task.validation_report_payload is not None:
        report = thaw_json_object(task.validation_report_payload)
        findings = report.get("findings") if isinstance(report.get("findings"), list) else []
        reasons.extend(
            str(item["code"])
            for item in findings
            if isinstance(item, dict)
            and str(item.get("severity", "")).lower() in {"block", "blocking"}
        )
    flags = review.get("review_flags")
    flags = flags if isinstance(flags, list) else []
    reasons.extend(
        f"{flag.get('issue_type')}: {flag.get('description')}"
        for flag in flags
        if isinstance(flag, dict)
        and str(flag.get("severity", "")).lower() in {"block", "blocking"}
    )
    return reasons


def _review_data(result) -> dict:
    task = result.task
    values = asdict(task)
    if task.execution_metadata is not None:
        values["execution_metadata"] = thaw_json_object(task.execution_metadata)
    if task.validation_report_payload is not None:
        values["validation_report_payload"] = thaw_json_object(
            task.validation_report_payload
        )
    review: dict = {}
    if task.review_payload is not None:
        review = thaw_json_object(task.review_payload)
        if not isinstance(review, dict):
            review = {}
    report = values.get("validation_report_payload")
    report = report if isinstance(report, dict) else {}
    validation = None
    if task.validation_conclusion is not None:
        validation = {
            "conclusion": task.validation_conclusion,
            "policy_version": str(report.get("policy_version") or ""),
            "validation_task_id": task.validation_task_id,
            "validation_report_id": task.validation_report_id,
            "blocking_reasons": _blocking_reasons(task, review),
        }
    reviewable_fields = _reviewable_fields(review)
    review_flags = _review_flags(review)
    missing_patent = _missing_patent_review(
        review,
        source_text=result.source_text,
        source_cv_version_id=result.source_cv_version_id,
    )
    if missing_patent is not None:
        field, flag = missing_patent
        reviewable_fields.append(field)
        review_flags.append(flag)
    missing_education = _missing_education_review(
        review,
        source_text=result.source_text,
        source_cv_version_id=result.source_cv_version_id,
    )
    if missing_education is not None:
        fields, flag = missing_education
        reviewable_fields.extend(fields)
        review_flags.append(flag)
    return {
        "task_id": task.task_id,
        "source_cv_id": result.source_cv_id,
        "source_cv_version_id": result.source_cv_version_id,
        "status": task.status,
        "confirmation_status": task.confirmation_status,
        "review_id": task.review_id,
        "review_revision": task.review_revision,
        "source_text": result.source_text,
        "source_file_id": result.source_file_id,
        "content_type": result.content_type,
        "ocr_layout": (
            [thaw_json_object(item) for item in result.ocr_layout]
            if result.ocr_layout is not None
            else None
        ),
        "reviewable_fields": reviewable_fields,
        "review_flags": review_flags,
        "validation": validation,
    }


def _review_confirmation(payload: CVConfirmRequest) -> CVReviewConfirmation:
    return CVReviewConfirmation(
        expected_review_id=payload.expected_review_id,
        idempotency_key=payload.idempotency_key,
        field_decisions=tuple(
            CVFieldDecision(
                field_id=item.field_id,
                field_type=item.field_type,
                section=item.section,
                decision=item.decision,
                item_id=item.item_id,
                field_path=item.field_path,
                corrected_value=item.corrected_value,
                correction_reason=item.correction_reason,
                evidence_quote=item.evidence_quote,
                evidence_start=item.evidence_start,
                evidence_end=item.evidence_end,
            )
            for item in payload.field_decisions
        ),
        normalization_version=payload.normalization_version,
        taxonomy_version=payload.taxonomy_version,
        display_name=payload.display_name,
    )


def _cv_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, CVExtractionNotFound):
        return 404, "CV_EXTRACTION_TASK_NOT_FOUND"
    if isinstance(exc, CVSnapshotNotFound):
        return 404, "CV_SNAPSHOT_NOT_FOUND"
    if isinstance(exc, CVExtractionConflict):
        return 409, "CV_EXTRACTION_CONFLICT"
    if isinstance(exc, CVExtractionBlocked):
        return 409, "CV_EXTRACTION_BLOCKED"
    if isinstance(exc, CVReviewConflict):
        return 409, "CV_REVIEW_CONFLICT"
    if isinstance(exc, ResumeRuleViolation) and "personal" in str(exc):
        return 403, "CV_PERSONAL_ROLE_REQUIRED"
    return 422, "CV_REQUEST_INVALID"


def _raise(exc: Exception) -> None:
    status, code = _cv_error(exc)
    raise HTTPException(
        status_code=status,
        detail={
            "code": code,
            "error_code": code,
            "message": str(exc),
        },
    ) from exc


@router.post(
    "/internal/source-cvs/import-and-extract",
    response_model=SourceCVImportEnvelope,
)
def import_source_cv(
    payload: SourceCVImportRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    """Internal/test-only raw-text CV entry; external clients use the file upload endpoint."""
    try:
        result = use_cases.import_and_schedule(actor, **payload.model_dump())
    except (CVExtractionConflict, ResumeRuleViolation, ValueError) as exc:
        _raise(exc)
    return success_response(data=asdict(result))


@router.post("/source-cvs/upload-and-extract", response_model=SourceCVImportEnvelope)
async def upload_and_extract_source_cv(
    file: UploadFile = File(...),
    use_ocr: bool = Form(default=False),
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.upload_and_schedule(
            actor,
            filename=file.filename or "",
            content_type=file.content_type,
            content=await read_upload(file, settings.MAX_UPLOAD_SIZE_BYTES),
            use_ocr=use_ocr,
        )
    except UploadSizeLimitExceeded as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "CV_FILE_TOO_LARGE",
                "error_code": "CV_FILE_TOO_LARGE",
                "message": str(exc),
                "stage": "file_extraction",
            },
        ) from exc
    except CVFileInputError as exc:
        status = (
            413
            if exc.code == "CV_FILE_TOO_LARGE"
            else 415
            if exc.code in {"CV_FILE_TYPE_UNSUPPORTED", "CV_FILE_MIME_MISMATCH"}
            else 422
        )
        raise HTTPException(
            status_code=status,
            detail={
                "code": exc.code,
                "error_code": exc.code,
                "message": str(exc),
                "stage": "file_extraction",
            },
        ) from exc
    except (
        CVExtractionConflict,
        CVExtractionBlocked,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=asdict(result))


@router.post(
    "/source-cvs/demo-snapshots/{dataset_version}",
    response_model=CVConfirmationEnvelope,
)
def use_demo_cv_snapshot(
    dataset_version: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.import_demo_snapshot(
            actor, dataset_version=dataset_version
        )
    except (
        CVExtractionNotFound,
        CVExtractionConflict,
        CVExtractionBlocked,
        CVReviewConflict,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=_confirmation_data(result))


@router.post("/cv-extraction-tasks/{task_id}/run", response_model=CVExtractionTaskEnvelope)
def run_cv_extraction(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.retry(actor, task_id)
    except (
        CVExtractionNotFound,
        CVExtractionConflict,
        CVExtractionBlocked,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=_task_data(result))


@router.post("/cv-extraction-tasks/{task_id}/retry", response_model=CVExtractionTaskEnvelope)
def retry_cv_extraction(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    return run_cv_extraction(task_id, actor, use_cases)


@router.post("/cv-extraction-tasks/{task_id}/cancel", response_model=CVExtractionTaskEnvelope)
def cancel_cv_extraction(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.cancel(actor, task_id)
    except (CVExtractionNotFound, CVExtractionConflict, ResumeRuleViolation) as exc:
        _raise(exc)
    return success_response(data=_task_data(result))


@router.post("/cv-extraction-tasks/{task_id}/reextract", response_model=CVExtractionTaskEnvelope)
def reextract_cv_source_version(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.reextract(actor, task_id)
    except (
        CVExtractionNotFound,
        CVExtractionConflict,
        CVExtractionBlocked,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=_task_data(result))


@router.get("/cv-extraction-tasks/{task_id}", response_model=CVExtractionTaskEnvelope)
def get_cv_extraction(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.get(actor, task_id)
    except (CVExtractionNotFound, ResumeRuleViolation) as exc:
        _raise(exc)
    return success_response(data=_task_data(result))


@router.get("/resumes/{resume_id}/pending-cv-review")
def get_pending_cv_review_for_resume(
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    """Latest unconfirmed CV review for the resume's source, if any."""
    try:
        task = use_cases.pending_review_task_for_resume(actor, resume_id)
    except (CVExtractionBlocked, ResumeRuleViolation) as exc:
        _raise(exc)
    return success_response(data={"task": _task_data(task) if task else None})


@router.get(
    "/cv-extraction-tasks/{task_id}/review",
    response_model=CVReviewEnvelope,
)
def get_cv_extraction_review(
    task_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.get_review_context(actor, task_id)
    except (CVExtractionNotFound, ResumeRuleViolation) as exc:
        _raise(exc)
    return success_response(data=_review_data(result))


@router.post(
    "/cv-extraction-tasks/{task_id}/confirm",
    response_model=CVConfirmationEnvelope,
)
def confirm_cv_extraction(
    task_id: str,
    payload: CVConfirmRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.confirm(actor, task_id, _review_confirmation(payload))
    except (
        CVExtractionNotFound,
        CVReviewConflict,
        CVSnapshotNotFound,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=_confirmation_data(result))


@router.get(
    "/validated-cv-snapshots/{snapshot_id}",
    response_model=ValidatedCVSnapshotEnvelope,
)
def get_validated_cv_snapshot(
    snapshot_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.get_snapshot(actor, snapshot_id)
    except (CVSnapshotNotFound, ResumeRuleViolation) as exc:
        _raise(exc)
    return success_response(data=_snapshot_data(result))


@router.post(
    "/validated-cv-snapshots/{snapshot_id}/revisions",
    response_model=CVConfirmationEnvelope,
)
def revise_validated_cv_snapshot(
    snapshot_id: str,
    payload: CVConfirmRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: CVIngestionUseCases = Depends(get_cv_ingestion_use_cases),
):
    try:
        result = use_cases.create_revision(
            actor, snapshot_id, _review_confirmation(payload)
        )
    except (
        CVSnapshotNotFound,
        CVReviewConflict,
        ResumeRuleViolation,
        ValueError,
    ) as exc:
        _raise(exc)
    return success_response(data=_confirmation_data(result))
