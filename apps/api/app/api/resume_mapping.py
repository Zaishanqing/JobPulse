from app.contexts.talent_acquisition import ParseResultRecord, ResumeRecord, ResumeSkillRecord


def resume_data(resume: ResumeRecord) -> dict[str, object]:
    fallback_name = (
        resume.original_filename
        or (
            f"简历 {resume.created_at:%Y-%m-%d}"
            if resume.created_at
            else "未命名简历"
        )
    )
    return {
        "resume_id": resume.resume_id,
        "user_id": resume.user_id,
        "source_type": resume.source_type,
        "file_id": resume.file_id,
        "display_name": resume.display_name or fallback_name,
        "original_filename": resume.original_filename,
        "raw_text": resume.raw_text,
        "parse_status": resume.parse_status,
        "input_extraction_status": resume.input_extraction_status,
        "input_provider": resume.input_provider,
        "input_error_code": resume.input_error_code,
        "input_error_message": resume.input_error_message,
        "source_cv_version_id": resume.source_cv_version_id,
        # 仅返回不可变快照标识，不暴露抽取载荷；前端据此区分
        # 旧的直接解析简历与可进入匹配链路的权威版本。
        "validated_cv_snapshot_id": resume.validated_cv_snapshot_id,
        "implementation_status": (
            "adapter_extracted_input"
            if resume.input_extraction_status == "completed"
            else "adapter_extraction_failed"
            if resume.input_extraction_status == "failed"
            else "direct_text_input"
        ),
        "created_at": resume.created_at.isoformat() if resume.created_at else None,
        "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
    }


def resume_parse_data(result: ParseResultRecord) -> dict[str, object]:
    return {
        "parse_result_id": result.parse_result_id,
        "resume_id": result.resume_id,
        "education": [dict(item) for item in result.education],
        "projects": [dict(item) for item in result.projects],
        "internships": [dict(item) for item in result.internships],
        "skills": [dict(item) for item in result.skills],
        "certificates": [dict(item) for item in result.certificates],
        "competitions": [dict(item) for item in result.competitions],
        "parse_confidence": result.parse_confidence,
        "need_review": result.need_review,
    }


def resume_skill_data(skill: ResumeSkillRecord) -> dict[str, object]:
    return {
        "resume_skill_id": skill.resume_skill_id,
        "resume_id": skill.resume_id,
        "skill_id": skill.skill_id,
        "raw_skill": skill.raw_skill,
        "confidence": skill.confidence,
        "evidence": skill.evidence,
        "proficiency": skill.proficiency,
    }
