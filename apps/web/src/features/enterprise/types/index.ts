export type EnterpriseProfile={
  enterprise_id:string;
  owner_user_id:string;
  enterprise_name:string;
  industry:string|null;
  scale:string|null;
  location:string|null;
  description:string|null;
  status:string;
  created_at:string|null;
  updated_at:string|null;
};

export type SalaryUnit='year'|'month'|'day';

export type EnterpriseJob={
  enterprise_job_id:string;
  enterprise_id:string;
  title:string;
  standard_position_id:string|null;
  jd_text:string|null;
  headcount:number;
  location:string|null;
  employment_type:string|null;
  salary_min:number|null;
  salary_max:number|null;
  salary_unit:SalaryUnit;
  status:string;
  created_at:string|null;
  updated_at:string|null;
};

export type EnterpriseJobInput={
  enterprise_id:string;
  title:string;
  standard_position_id?:string;
  jd_text?:string;
  headcount:number;
  location?:string;
  employment_type?:string;
  salary_min?:number;
  salary_max?:number;
  salary_unit?:SalaryUnit;
  status:'draft'|'published';
};

export type PublishedEnterpriseJob={
  enterprise_job_id:string;
  enterprise_name:string;
  title:string;
  jd_text:string|null;
  headcount:number;
  location:string|null;
  employment_type:string|null;
  salary_min:number|null;
  salary_max:number|null;
  salary_unit:SalaryUnit;
  status:'published';
};

export type SkillWeight={
  id:string;
  enterprise_job_id:string;
  skill_id:string;
  weight:number;
  is_required:boolean;
  is_bonus:boolean;
};

export type CandidateSubmission={
  submission_id:string;
  resume_id:string;
  resume_display_name:string;
  enterprise_job_id:string;
  enterprise_id:string;
  status:string;
  created_at:string|null;
  updated_at:string|null;
  parse_status:string;
  validated_cv_snapshot_id:string|null;
  skill_count:number;
  matchable:boolean;
  matchable_reason:string;
};

export type PersonalCandidateSubmission={
  submission_id:string;
  resume_id:string;
  status:'submitted'|'revoked';
  created_at:string|null;
  updated_at:string|null;
};

export type CandidateApplicationOption={
  resume_id:string;
  resume_display_name:string;
  validated_cv_snapshot_id:string|null;
  eligible:boolean;
  eligibility_reason:'eligible'|'validated_cv_snapshot_missing'|'validated_cv_snapshot_not_matchable';
  submission:PersonalCandidateSubmission|null;
};

export type EnterpriseMatchEvaluation={
  evaluation_id:string;
  task_id?:string|null;
  resume_id:string;
  position_id?:string;
  status:string;
  provider?:string;
  lineage?:Record<string,unknown>;
  created_at:string|null;
  updated_at:string|null;
};

export type EnterpriseMatchBatch={
  enterprise_job_id:string;
  implementation_status:string;
  items:Array<{
    submission_id:string;
    resume_id:string;
    status:string;
    task_id:string|null;
    evaluation_id:string|null;
    error_code:string|null;
    error_message:string|null;
  }>;
};

export type EnterpriseMatchTask={
  task_id:string;
  status:'pending'|'running'|'succeeded'|'failed';
  evaluation_id?:string|null;
  error_code?:string|null;
  error_message?:string|null;
};

export type CandidateBoardCoverage={
  matched:number;
  total:number;
  coverage:number|null;
};

export type CandidateBoardEvidence={
  count:number;
  samples:string[];
};

export type CandidateBoardStrength={
  dimension:string;
  message:string;
  evidence_count:number;
};

export type CandidateBoardRisk={
  kind:string;
  message:string;
  evidence_count:number;
};

export type CandidateBoardDecision={
  decision_id:string;
  decision:'fit'|'unfit';
  decided_by:string;
  evaluation_id:string|null;
  task_id:string|null;
  algorithm_version:string|null;
  reason_code:string|null;
  reason_text:string|null;
  created_at:string|null;
  updated_at:string|null;
};

export type CandidateBoardEvaluationSnapshot={
  evaluation_id:string;
  task_id:string|null;
  algorithm_version:string|null;
  evaluated_at:string|null;
  overall_score:number|null;
  required_coverage:CandidateBoardCoverage|null;
  critical_gap_count:number;
  critical_gaps:string[];
  stale_reason_codes:string[];
};

export type CandidateBoardEvaluationDelta={
  current:CandidateBoardEvaluationSnapshot;
  previous:CandidateBoardEvaluationSnapshot;
  overall_score_delta:number|null;
  required_coverage_delta:number|null;
  critical_gap_count_delta:number;
  stale_reasons_changed:boolean;
};

export type CandidateBoardItem={
  submission_id:string;
  resume_id:string;
  candidate_display_name:string;
  candidate_status:'submitted'|'revoked';
  evaluation_id:string|null;
  evaluation_status:'never_matched'|'pending'|'running'|'failed'|'succeeded'|'stale'|'needs_rematch'|'revoked';
  task_id:string|null;
  error_code:string|null;
  error_message:string|null;
  overall_score:number|null;
  match_confidence:number|null;
  recommendation_level:string|null;
  stale:boolean;
  required_coverage:CandidateBoardCoverage|null;
  critical_gap_count:number;
  critical_gaps:string[];
  evidence:CandidateBoardEvidence|null;
  strengths:CandidateBoardStrength[];
  risks:CandidateBoardRisk[];
  rank:number|null;
  decision:CandidateBoardDecision|null;
  evaluation_delta:CandidateBoardEvaluationDelta|null;
};

export type CandidateDecisionBoard={
  enterprise_job_id:string;
  total:number;
  ranked_count:number;
  items:CandidateBoardItem[];
};

export type DecisionAuditMetric={
  numerator:number;
  denominator:number;
  rate:number|null;
};

export type RecruiterDecisionAuditCase={
  evaluation_id:string;
  task_id:string|null;
  resume_id:string;
  cv_profile:{resume_id:string;validated_cv_snapshot_id:string|null;profile_version:string|null};
  position:{position_id:string|null;profile_version:string|null};
  recruiter_decision:Record<string,unknown>|null;
  decision_count:number;
  reason_code:string|null;
  reason_text:string|null;
  operator:string|null;
  formal_score:number|null;
  formal_recommendation:string|null;
  formal_direction:string|null;
  critical_gap_count:number;
  critical_gap:boolean;
  classifications:string[];
  version_consistent:boolean;
  evaluation_version:Record<string,unknown>;
  algorithm_identity:Record<string,unknown>;
  evaluated_at:string|null;
  decided_at:string|null;
  historical:boolean;
};

export type RecruiterDecisionAudit={
  enterprise_job_id:string;
  audit_config:{version:string;high_definition:string;low_definition:string;scope:'audit_only'};
  metrics:{
    overall_agreement_rate:DecisionAuditMetric;
    high_score_rejection_rate:DecisionAuditMetric;
    low_score_acceptance_rate:DecisionAuditMetric;
    critical_gap_disagreement_rate:DecisionAuditMetric;
  };
  reason_code_distribution:Array<{reason_code:string;count:number}>;
  coverage:{
    evaluation_count:number;
    paired_decision_count:number;
    missing_decision_count:number;
    missing_reason_count:number;
    version_mismatch_count:number;
    duplicate_decision_count:number;
    unavailable_evaluation_count:number;
  };
  cases:RecruiterDecisionAuditCase[];
};

export type RecruiterDecisionAuditCaseReplay=RecruiterDecisionAuditCase&{
  decision_history:Array<Record<string,unknown>>;
  formal_evaluation:Record<string,unknown>;
};
