from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies.accounts import get_account_actor
from app.api.dependencies.candidates import get_candidate_use_cases
from app.api.evaluation_data import evaluation_reference_data, evaluation_report_data
from app.contexts.talent_acquisition import (
    CandidateBoardItem,
    CandidateApplicationOption,
    CandidateConflict,
    CandidateJobNotFound,
    CandidateResumeNotFound,
    CandidateSubmissionNotFound,
    EnterpriseMatchNotFound,
    JobNotFound,
    ManageCandidates,
    RecruitmentHandlers,
)
from app.api.dependencies.recruitment import get_recruitment_handlers
from app.core.response import success_response
from app.domain.accounts import AccountActor
from app.domain.candidates import CandidateRuleViolation
from app.contexts.talent_acquisition import (
    CandidateDecisionRecord,
    CandidateSubmissionRecord,
    DecisionAuditCase,
    DecisionAuditMetric,
    MatchingServiceReferenceRecord,
    PublishedJobRecord,
    RecruiterDecisionAudit,
)
from app.domain.errors import PermissionDenied
from app.contexts.matching_learning.matching_service import MatchingServiceError
from app.schemas.api_requests import CandidateSubmissionRequest, EnterpriseCandidateMatchRequest
from app.schemas.matching_bff import (
    CandidateBoardEnvelope,
    DecisionAuditEnvelope,
    DecisionAuditReplayEnvelope,
    CandidateMatchSubmissionEnvelope,
    MatchReportEnvelope,
    MatchReportListEnvelope,
)


router = APIRouter(prefix="/enterprise-jobs", tags=["enterprise-jobs"])
published_jobs_router = APIRouter(
    prefix="/published-enterprise-jobs", tags=["published-enterprise-jobs"]
)


def _published_job_data(item: PublishedJobRecord) -> dict[str, object]:
    return {
        "enterprise_job_id": item.job_id,
        "enterprise_name": item.enterprise_name,
        "title": item.title,
        "jd_text": item.jd_text,
        "headcount": item.headcount,
        "location": item.location,
        "employment_type": item.employment_type,
        "salary_min": item.salary_min,
        "salary_max": item.salary_max,
        "salary_unit": item.salary_unit,
        "status": item.status,
    }


@published_jobs_router.get("")
def list_published_enterprise_jobs(
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        jobs = handlers.published_jobs.list(actor)
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=[_published_job_data(job) for job in jobs])


@published_jobs_router.get("/{job_id}")
def get_published_enterprise_job(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    handlers: RecruitmentHandlers = Depends(get_recruitment_handlers),
):
    try:
        job = handlers.published_jobs.get(actor, job_id)
    except JobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionDenied as exc:
        _raise(exc)
    return success_response(data=_published_job_data(job))


def _raise(
    exc: (
        MatchingServiceError
        | CandidateJobNotFound
        | CandidateResumeNotFound
        | CandidateSubmissionNotFound
        | EnterpriseMatchNotFound
        | CandidateConflict
        | PermissionDenied
        | CandidateRuleViolation
    ),
) -> None:
    if isinstance(exc, MatchingServiceError):
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from exc
    if isinstance(
        exc,
        (
            CandidateJobNotFound,
            CandidateResumeNotFound,
            CandidateSubmissionNotFound,
            EnterpriseMatchNotFound,
        ),
    ):
        code = 404
    elif isinstance(exc, CandidateConflict):
        code = 409
    elif isinstance(exc, PermissionDenied):
        code = 403
    else:
        code = 422
    raise HTTPException(status_code=code, detail=str(exc)) from exc


def _submission_data(item: CandidateSubmissionRecord) -> dict[str, object]:
    return {
        "submission_id": item.submission_id,
        "resume_id": item.resume_id,
        "resume_display_name": item.display_name,
        "enterprise_job_id": item.job_id,
        "enterprise_id": item.enterprise_id,
        "resume_owner_user_id": item.resume_owner_id,
        "status": item.status,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "parse_status": item.parse_status,
        "validated_cv_snapshot_id": item.validated_cv_snapshot_id,
        "skill_count": item.skill_count,
        "matchable": (
            item.status == "submitted"
            and bool(item.validated_cv_snapshot_id)
            and item.skill_count > 0
        ),
        "matchable_reason": (
            "可匹配"
            if item.status == "submitted"
            and bool(item.validated_cv_snapshot_id)
            and item.skill_count > 0
            else "缺少已授权或已验证的简历快照"
            if not item.validated_cv_snapshot_id
            else "简历尚未生成技能画像"
            if item.skill_count == 0
            else "候选人投递已撤销"
        ),
        "implementation_status": "database_persisted_candidate_submission",
    }


def _application_option_data(item: CandidateApplicationOption) -> dict[str, object]:
    return {
        "resume_id": item.resume_id,
        "resume_display_name": item.display_name,
        "validated_cv_snapshot_id": item.validated_cv_snapshot_id,
        "eligible": item.eligible,
        "eligibility_reason": item.eligibility_reason,
        "submission": (
            {
                "submission_id": item.submission.submission_id,
                "resume_id": item.submission.resume_id,
                "status": item.submission.status,
                "created_at": (
                    item.submission.created_at.isoformat()
                    if item.submission.created_at
                    else None
                ),
                "updated_at": (
                    item.submission.updated_at.isoformat()
                    if item.submission.updated_at
                    else None
                ),
            }
            if item.submission
            else None
        ),
    }


def _decision_data(item: CandidateDecisionRecord) -> dict[str, object]:
    return {
        "decision_id": item.decision_id,
        "enterprise_job_id": item.job_id,
        "resume_id": item.resume_id,
        "candidate_status": item.decision,
        "decided_by": item.decided_by,
        "evaluation_id": item.evaluation_id,
        "task_id": item.task_id,
        "algorithm_version": item.algorithm_version,
        "reason_code": item.reason_code,
        "reason_text": item.reason_text,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "implementation_status": "database_persisted_candidate_decision",
    }


def _audit_metric_data(metric: DecisionAuditMetric) -> dict[str, object]:
    return {
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "rate": metric.rate,
    }


def _audit_decision_history_data(
    decisions: tuple[CandidateDecisionRecord, ...],
) -> list[dict[str, object]]:
    return [
        {**_decision_data(item), "decision": item.decision} for item in decisions
    ]


def _audit_case_data(case: DecisionAuditCase, *, replay: bool) -> dict[str, object]:
    report = evaluation_report_data(case.evaluation)
    evaluation = report.get("evaluation") or {}
    final = evaluation.get("final_match_result") or {}
    submission = case.submission
    selected = case.selected_decision
    data: dict[str, object] = {
        "evaluation_id": case.evaluation.evaluation_id,
        "task_id": case.evaluation.task_id or case.reference.task_id,
        "resume_id": case.reference.resume_id,
        "cv_profile": {
            "resume_id": case.reference.resume_id,
            "validated_cv_snapshot_id": case.evaluation.validated_cv_snapshot_id
            or (submission.validated_cv_snapshot_id if submission else None),
            "profile_version": case.reference.cv_profile_version or None,
        },
        "position": {
            "position_id": case.evaluation.position_id or case.reference.position_id,
            "profile_version": case.reference.position_profile_version or None,
        },
        "recruiter_decision": (
            {**_decision_data(selected), "decision": selected.decision}
            if selected
            else None
        ),
        "decision_count": len(case.decisions),
        "reason_code": selected.reason_code if selected else None,
        "reason_text": selected.reason_text if selected else None,
        "operator": selected.decided_by if selected else None,
        "formal_score": final.get("overall_score"),
        "formal_recommendation": final.get("recommendation_level"),
        "formal_direction": case.formal_direction,
        "critical_gap_count": case.critical_gap_count,
        "critical_gap": case.critical_gap_count > 0,
        "classifications": list(case.classifications),
        "version_consistent": case.version_consistent,
        "evaluation_version": {
            "schema_version": case.reference.schema_version,
            "source_version": case.reference.source_version,
            "taxonomy_version": case.reference.taxonomy_version,
            "graph_version": case.reference.graph_version,
            "versions": report.get("versions") or {},
        },
        "algorithm_identity": {
            "decision_algorithm_version": selected.algorithm_version if selected else None,
            "reference_algorithm_version": case.reference.algorithm_version,
            "evaluation_algorithm_version": evaluation.get("algorithm_version")
            or final.get("algorithm_version"),
            "algorithm_versions": (report.get("lineage") or {}).get(
                "algorithm_versions"
            ),
        },
        "evaluated_at": case.evaluation.updated_at
        or case.evaluation.created_at
        or (
            case.reference.updated_at.isoformat() if case.reference.updated_at else None
        )
        or (
            case.reference.created_at.isoformat() if case.reference.created_at else None
        ),
        "decided_at": (
            (selected.updated_at or selected.created_at).isoformat()
            if selected and (selected.updated_at or selected.created_at)
            else None
        ),
        "historical": case.historical,
    }
    if replay:
        data["decision_history"] = _audit_decision_history_data(case.decisions)
        data["formal_evaluation"] = report
    return data


def _audit_data(audit: RecruiterDecisionAudit) -> dict[str, object]:
    return {
        "enterprise_job_id": audit.enterprise_job_id,
        "audit_config": {
            "version": audit.config_version,
            "high_definition": "formal recommendation_level == strong_match",
            "low_definition": (
                "formal recommendation_level in [weak_match, not_recommended]"
            ),
            "scope": "audit_only",
        },
        "metrics": {
            "overall_agreement_rate": _audit_metric_data(audit.overall_agreement),
            "high_score_rejection_rate": _audit_metric_data(
                audit.high_score_rejection
            ),
            "low_score_acceptance_rate": _audit_metric_data(
                audit.low_score_acceptance
            ),
            "critical_gap_disagreement_rate": _audit_metric_data(
                audit.critical_gap_disagreement
            ),
        },
        "reason_code_distribution": [
            {"reason_code": code, "count": count}
            for code, count in audit.reason_code_distribution
        ],
        "coverage": {
            "evaluation_count": audit.evaluation_count,
            "paired_decision_count": audit.paired_decision_count,
            "missing_decision_count": audit.missing_decision_count,
            "missing_reason_count": audit.missing_reason_count,
            "version_mismatch_count": audit.version_mismatch_count,
            "duplicate_decision_count": audit.duplicate_decision_count,
            "unavailable_evaluation_count": audit.unavailable_evaluation_count,
        },
        "cases": [_audit_case_data(case, replay=False) for case in audit.cases],
    }


@router.post(
    "/{job_id}/match-submissions",
    response_model=CandidateMatchSubmissionEnvelope,
)
def match_enterprise_job_resumes(
    job_id: str,
    payload: EnterpriseCandidateMatchRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    submission_ids = payload.submission_ids
    try:
        outcome = use_cases.match(actor, job_id, submission_ids)
    except (
        CandidateJobNotFound,
        CandidateSubmissionNotFound,
        CandidateConflict,
        CandidateRuleViolation,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(
        data={
            "enterprise_job_id": outcome.job_id,
            "implementation_status": "matching_service_async",
            "items": [
                {
                    "submission_id": item.submission_id,
                    "resume_id": item.resume_id,
                    "status": item.status,
                    "task_id": item.task_id,
                    "evaluation_id": item.evaluation_id,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in outcome.items
            ],
        }
    )


@router.get("/{job_id}/candidate-submissions")
def list_candidate_submissions(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        items = use_cases.candidate_submissions(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=[_submission_data(item) for item in items])


@router.get("/{job_id}/candidate-submission-options")
def list_candidate_submission_options(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        items = use_cases.application_options(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=[_application_option_data(item) for item in items])


@router.post("/{job_id}/candidate-submissions")
def create_candidate_submission(
    job_id: str,
    payload: CandidateSubmissionRequest,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    resume_id = payload.resume_id
    try:
        item = use_cases.submit(actor, job_id, resume_id)
    except (
        CandidateJobNotFound,
        CandidateResumeNotFound,
        CandidateRuleViolation,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(data=_submission_data(item))


@router.put("/{job_id}/candidate-submissions/{resume_id}/revoke")
def revoke_candidate_submission(
    job_id: str,
    resume_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        item = use_cases.revoke(actor, job_id, resume_id)
    except (
        CandidateJobNotFound,
        CandidateResumeNotFound,
        CandidateSubmissionNotFound,
    ) as exc:
        _raise(exc)
    return success_response(data=_submission_data(item))


@router.get("/{job_id}/match-reports", response_model=MatchReportListEnvelope)
def list_enterprise_job_match_reports(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        items = use_cases.list_reports(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(
        data=[
            evaluation_reference_data(item)
            for item in items
            if isinstance(item, MatchingServiceReferenceRecord)
        ]
    )


@router.get(
    "/{job_id}/match-reports/{evaluation_id}",
    response_model=MatchReportEnvelope,
)
def get_enterprise_job_match_evaluation(
    job_id: str,
    evaluation_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        item = use_cases.get_report(actor, job_id, evaluation_id)
    except (
        CandidateJobNotFound,
        EnterpriseMatchNotFound,
        PermissionDenied,
        MatchingServiceError,
    ) as exc:
        _raise(exc)
    return success_response(data=evaluation_report_data(item))


def _board_report(item: CandidateBoardItem) -> dict[str, object] | None:
    if item.evaluation is None:
        return None
    return evaluation_report_data(item.evaluation)


def _evaluation_snapshot(evaluation, reference) -> dict[str, object]:
    report = evaluation_report_data(evaluation)
    evaluation_data = report.get("evaluation") or {}
    final = evaluation_data.get("final_match_result") or {}
    gap_count, gap_summaries = _board_critical_gaps(report)
    return {
        "evaluation_id": evaluation.evaluation_id,
        "task_id": evaluation.task_id or reference.task_id,
        "algorithm_version": evaluation_data.get("algorithm_version")
        or reference.algorithm_version,
        "evaluated_at": evaluation.updated_at
        or evaluation.created_at
        or (reference.updated_at.isoformat() if reference.updated_at else None)
        or (reference.created_at.isoformat() if reference.created_at else None),
        "overall_score": final.get("overall_score"),
        "required_coverage": _required_coverage(report),
        "critical_gap_count": gap_count,
        "critical_gaps": gap_summaries,
        "stale_reason_codes": list(evaluation.stale_reason_codes),
    }


def _evaluation_delta(item: CandidateBoardItem) -> dict[str, object] | None:
    if (
        item.evaluation is None
        or item.evaluation_reference is None
        or item.previous_evaluation is None
        or item.previous_evaluation_reference is None
    ):
        return None
    current = _evaluation_snapshot(item.evaluation, item.evaluation_reference)
    previous = _evaluation_snapshot(
        item.previous_evaluation, item.previous_evaluation_reference
    )

    def numeric_delta(key: str) -> float | None:
        current_value = current.get(key)
        previous_value = previous.get(key)
        if not isinstance(current_value, (int, float)) or not isinstance(
            previous_value, (int, float)
        ):
            return None
        return round(float(current_value) - float(previous_value), 4)

    current_coverage = current.get("required_coverage")
    previous_coverage = previous.get("required_coverage")
    coverage_delta = None
    if isinstance(current_coverage, dict) and isinstance(previous_coverage, dict):
        current_value = current_coverage.get("coverage")
        previous_value = previous_coverage.get("coverage")
        if isinstance(current_value, (int, float)) and isinstance(
            previous_value, (int, float)
        ):
            coverage_delta = round(float(current_value) - float(previous_value), 4)
    return {
        "current": current,
        "previous": previous,
        "overall_score_delta": numeric_delta("overall_score"),
        "required_coverage_delta": coverage_delta,
        "critical_gap_count_delta": int(current["critical_gap_count"])
        - int(previous["critical_gap_count"]),
        "stale_reasons_changed": current["stale_reason_codes"]
        != previous["stale_reason_codes"],
    }


def _required_coverage(report: dict[str, object]) -> dict[str, object] | None:
    evaluation = report.get("evaluation") or {}
    summary = evaluation.get("summary")
    if isinstance(summary, dict):
        matched = summary.get("required_skill_matched_count")
        missing = summary.get("required_skill_missing_count")
        if isinstance(matched, int) and isinstance(missing, int):
            total = matched + missing
            coverage = evaluation.get("required_skill_coverage")
            if coverage is None and total:
                coverage = round(matched / total, 4)
            return {"matched": matched, "total": total, "coverage": coverage}
    required = [
        item
        for item in (evaluation.get("skill_results") or [])
        if item.get("importance_level") == "required"
    ]
    if required:
        matched = sum(
            1 for item in required if item.get("match_status") == "matched"
        )
        total = len(required)
        coverage = evaluation.get("required_skill_coverage")
        if coverage is None and total:
            coverage = round(matched / total, 4)
        return {"matched": matched, "total": total, "coverage": coverage}
    coverage = evaluation.get("required_skill_coverage")
    if coverage is None:
        return None
    return {"matched": 0, "total": 0, "coverage": coverage}


def _board_evidence(report: dict[str, object]) -> dict[str, object]:
    evaluation = report.get("evaluation") or {}
    final = evaluation.get("final_match_result") or {}
    count = 0
    samples: list[str] = []
    for contribution in final.get("score_contributions") or []:
        for side in ("candidate_evidence", "position_evidence", "relation_evidence"):
            for evidence in contribution.get(side) or []:
                count += 1
                quote = evidence.get("quote")
                if quote and len(samples) < 3:
                    samples.append(quote)
    for strength in final.get("strengths") or []:
        for evidence in strength.get("evidence") or []:
            count += 1
            quote = evidence.get("quote")
            if quote and len(samples) < 3:
                samples.append(quote)
    return {"count": count, "samples": samples}


def _board_strengths(report: dict[str, object]) -> list[dict[str, object]]:
    evaluation = report.get("evaluation") or {}
    final = evaluation.get("final_match_result") or {}
    strengths: list[dict[str, object]] = []
    for item in (final.get("strengths") or [])[:6]:
        strengths.append(
            {
                "dimension": item.get("dimension") or "",
                "message": item.get("message") or "",
                "evidence_count": len(item.get("evidence") or []),
            }
        )
    return strengths


def _board_risks(report: dict[str, object]) -> list[dict[str, object]]:
    evaluation = report.get("evaluation") or {}
    gap_analysis = report.get("gap_analysis") or {}
    final = evaluation.get("final_match_result") or {}
    risks: list[dict[str, object]] = []
    for item in (final.get("gaps") or [])[:4]:
        risks.append(
            {
                "kind": "gap",
                "message": item.get("message") or "",
                "evidence_count": len(item.get("evidence") or []),
            }
        )
    for result in evaluation.get("skill_results") or []:
        if result.get("importance_level") != "required":
            continue
        status = result.get("match_status")
        name = (
            result.get("skill_name")
            or result.get("skill_id")
            or result.get("requirement_id")
            or "技能"
        )
        if status in ("missing", "unknown", "unresolved"):
            risks.append(
                {"kind": "missing_required", "message": f"{name}（{status}）", "evidence_count": 0}
            )
        elif status == "weak":
            risks.append(
                {"kind": "weak_requirement", "message": f"{name}（弱匹配）", "evidence_count": 0}
            )
        elif status == "declared_only":
            risks.append(
                {
                    "kind": "evidence_weakness",
                    "message": f"{name} 仅候选人自述，缺少可验证证据",
                    "evidence_count": 0,
                }
            )
    for result in evaluation.get("hard_constraint_results") or []:
        if result.get("status") in ("fail", "partial"):
            risks.append(
                {
                    "kind": "hard_constraint",
                    "message": (
                        f"{result.get('constraint_type')} 未满足："
                        f"{result.get('candidate_value') or '—'} vs "
                        f"{result.get('required_value') or '—'}"
                    ),
                    "evidence_count": 0,
                }
            )
    for gap in gap_analysis.get("prioritized_gaps") or []:
        if gap.get("priority") == "critical":
            risks.append(
                {
                    "kind": "critical_gap",
                    "message": (
                        f"{gap.get('skill_id') or gap.get('requirement_id')}（关键缺口）"
                    ),
                    "evidence_count": len(gap.get("evidence") or []),
                }
            )
    return risks[:10]


def _board_critical_gaps(report: dict[str, object]) -> tuple[int, list[str]]:
    gap_analysis = report.get("gap_analysis") or {}
    critical = [
        gap
        for gap in (gap_analysis.get("prioritized_gaps") or [])
        if gap.get("priority") == "critical"
    ]
    summaries: list[str] = []
    for gap in critical[:5]:
        label = gap.get("skill_id") or gap.get("requirement_id") or "关键缺口"
        reasons = "、".join(gap.get("reason_codes") or [])
        summaries.append(f"{label}（{reasons}）" if reasons else label)
    return len(critical), summaries


def _candidate_board_item(item: CandidateBoardItem) -> dict[str, object]:
    base: dict[str, object] = {
        "submission_id": item.submission_id,
        "resume_id": item.resume_id,
        "candidate_display_name": item.candidate_display_name,
        "candidate_status": item.candidate_status,
        "evaluation_id": item.evaluation_id,
        "evaluation_status": item.evaluation_status,
        "task_id": item.task_id,
        "error_code": item.error_code,
        "error_message": item.error_message,
        "overall_score": None,
        "match_confidence": None,
        "recommendation_level": None,
        "stale": item.evaluation_status == "stale",
        "required_coverage": None,
        "critical_gap_count": 0,
        "critical_gaps": [],
        "evidence": None,
        "strengths": [],
        "risks": [],
        "rank": None,
        "decision": None,
        "evaluation_delta": None,
    }
    report = _board_report(item)
    if report is not None:
        evaluation = report.get("evaluation") or {}
        final = evaluation.get("final_match_result")
        if isinstance(final, dict):
            base["overall_score"] = final.get("overall_score")
            base["match_confidence"] = final.get("match_confidence")
            base["recommendation_level"] = final.get("recommendation_level")
        base["stale"] = bool(report.get("stale"))
        base["required_coverage"] = _required_coverage(report)
        gap_count, gap_summaries = _board_critical_gaps(report)
        base["critical_gap_count"] = gap_count
        base["critical_gaps"] = gap_summaries
        base["evidence"] = _board_evidence(report)
        base["strengths"] = _board_strengths(report)
        base["risks"] = _board_risks(report)
        base["evaluation_delta"] = _evaluation_delta(item)
    if (
        item.decision is not None
        and item.evaluation_status == "succeeded"
        and item.decision.evaluation_id == item.evaluation_id
    ):
        base["decision"] = {
            "decision_id": item.decision.decision_id,
            "decision": item.decision.decision,
            "decided_by": item.decision.decided_by,
            "evaluation_id": item.decision.evaluation_id,
            "task_id": item.decision.task_id,
            "algorithm_version": item.decision.algorithm_version,
            "reason_code": item.decision.reason_code,
            "reason_text": item.decision.reason_text,
            "created_at": (
                item.decision.created_at.isoformat() if item.decision.created_at else None
            ),
            "updated_at": (
                item.decision.updated_at.isoformat() if item.decision.updated_at else None
            ),
        }
    return base


def _rank_board_items(items: list[dict[str, object]]) -> list[dict[str, object]]:
    eligible = [
        item
        for item in items
        if item["candidate_status"] == "submitted"
        and item["evaluation_status"] == "succeeded"
        and item["stale"] is False
    ]
    excluded = [item for item in items if item not in eligible]

    def sort_key(item: dict[str, object]) -> tuple:
        score = (
            float(item["overall_score"])
            if isinstance(item["overall_score"], (int, float))
            else -1.0
        )
        coverage = 0.0
        required = item["required_coverage"]
        if isinstance(required, dict) and isinstance(
            required.get("coverage"), (int, float)
        ):
            coverage = float(required["coverage"])
        gaps = int(item["critical_gap_count"] or 0)
        return (
            -score,
            -coverage,
            gaps,
            str(item["resume_id"]),
            str(item["submission_id"]),
        )

    eligible.sort(key=sort_key)
    for index, item in enumerate(eligible, start=1):
        item["rank"] = index
    for item in excluded:
        item["rank"] = None
    excluded.sort(key=lambda item: (str(item["resume_id"]), str(item["submission_id"])))
    return [*eligible, *excluded]


@router.get("/{job_id}/candidate-decision-board", response_model=CandidateBoardEnvelope)
def get_candidate_decision_board(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        board = use_cases.decision_board(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    items = [_candidate_board_item(item) for item in board.items]
    ranked = _rank_board_items(items)
    return success_response(
        data={
            "enterprise_job_id": board.enterprise_job_id,
            "total": len(ranked),
            "ranked_count": sum(1 for item in ranked if item["rank"] is not None),
            "items": ranked,
        }
    )


@router.get("/{job_id}/decision-audit", response_model=DecisionAuditEnvelope)
def get_recruiter_decision_audit(
    job_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        audit = use_cases.decision_audit(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    return success_response(data=_audit_data(audit))


@router.get(
    "/{job_id}/decision-audit/cases/{evaluation_id}",
    response_model=DecisionAuditReplayEnvelope,
)
def replay_recruiter_decision_audit_case(
    job_id: str,
    evaluation_id: str,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    try:
        audit = use_cases.decision_audit(actor, job_id)
    except (CandidateJobNotFound, PermissionDenied) as exc:
        _raise(exc)
    case = next(
        (item for item in audit.cases if item.evaluation.evaluation_id == evaluation_id),
        None,
    )
    if case is None:
        raise HTTPException(status_code=404, detail="DECISION_AUDIT_CASE_NOT_FOUND")
    return success_response(data=_audit_case_data(case, replay=True))


def _decide(
    job_id: str,
    resume_id: str,
    decision: str,
    actor: AccountActor,
    use_cases: ManageCandidates,
    evaluation_id: str | None = None,
    reason_code: str | None = None,
    reason_text: str | None = None,
):
    try:
        item = use_cases.decide(
            actor,
            job_id,
            resume_id,
            decision,
            evaluation_id=evaluation_id,
            reason_code=reason_code,
            reason_text=reason_text,
        )
    except (
        CandidateJobNotFound,
        CandidateResumeNotFound,
        CandidateConflict,
        CandidateRuleViolation,
        PermissionDenied,
    ) as exc:
        _raise(exc)
    return success_response(data=_decision_data(item))


@router.post("/{job_id}/candidates/{resume_id}/mark-fit")
def mark_candidate_fit(
    job_id: str,
    resume_id: str,
    evaluation_id: str | None = None,
    reason_code: str | None = None,
    reason_text: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    return _decide(
        job_id, resume_id, "fit", actor, use_cases, evaluation_id, reason_code, reason_text
    )


@router.post("/{job_id}/candidates/{resume_id}/mark-unfit")
def mark_candidate_unfit(
    job_id: str,
    resume_id: str,
    evaluation_id: str | None = None,
    reason_code: str | None = None,
    reason_text: str | None = None,
    actor: AccountActor = Depends(get_account_actor),
    use_cases: ManageCandidates = Depends(get_candidate_use_cases),
):
    return _decide(
        job_id,
        resume_id,
        "unfit",
        actor,
        use_cases,
        evaluation_id,
        reason_code,
        reason_text,
    )
