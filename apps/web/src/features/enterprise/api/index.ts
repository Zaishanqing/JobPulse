import {api} from '../../../shared/api';
import type {CatalogSkill} from '../../../shared/api';
import type {
  CandidateDecisionBoard,
  RecruiterDecisionAudit,
  RecruiterDecisionAuditCaseReplay,
  EnterpriseJob,
  EnterpriseJobInput,
  EnterpriseMatchBatch,
  EnterpriseMatchEvaluation,
  EnterpriseMatchTask,
  EnterpriseProfile,
  PublishedEnterpriseJob,
  CandidateSubmission,
  CandidateApplicationOption,
  SkillWeight,
} from '../types';

const id=(value:string)=>encodeURIComponent(value);

export const getMyEnterprise=()=>api<EnterpriseProfile|null>('/enterprises/me');
export const listSkillCategories=()=>api<Array<{category:string;skills:CatalogSkill[]}>>('/skills/domain-tree');
export const createEnterprise=(payload:{enterprise_name:string;industry?:string;scale?:string;location?:string;description?:string})=>
  api<{enterprise_id:string;enterprise_name:string;status:string}>('/enterprises',{method:'POST',body:JSON.stringify(payload)});

export const listEnterpriseJobs=()=>api<EnterpriseJob[]>('/enterprise-jobs');
export const listPublishedEnterpriseJobs=()=>api<PublishedEnterpriseJob[]>('/published-enterprise-jobs');
export const getPublishedEnterpriseJob=(jobId:string)=>
  api<PublishedEnterpriseJob>(`/published-enterprise-jobs/${id(jobId)}`);
export const listCandidateSubmissionOptions=(jobId:string)=>
  api<CandidateApplicationOption[]>(`/enterprise-jobs/${id(jobId)}/candidate-submission-options`);
export const submitCandidate=(jobId:string,resumeId:string)=>
  api<Record<string,unknown>>(`/enterprise-jobs/${id(jobId)}/candidate-submissions`,{
    method:'POST',body:JSON.stringify({resume_id:resumeId}),
  });
export const revokeCandidateSubmission=(jobId:string,resumeId:string)=>
  api<Record<string,unknown>>(`/enterprise-jobs/${id(jobId)}/candidate-submissions/${id(resumeId)}/revoke`,{method:'PUT'});
export const createEnterpriseJob=(payload:EnterpriseJobInput)=>
  api<EnterpriseJob>('/enterprise-jobs',{method:'POST',body:JSON.stringify(payload)});
export const deleteEnterpriseJob=(jobId:string)=>
  api<{enterprise_job_id:string;deleted:boolean}>(`/enterprise-jobs/${id(jobId)}`,{method:'DELETE'});
export const changeEnterpriseJobStatus=(jobId:string,action:'publish'|'pause'|'resume'|'cancel')=>
  api<EnterpriseJob>(`/enterprise-jobs/${id(jobId)}/${action}`,{method:'PUT'});

export const getSkillWeights=(jobId:string)=>api<SkillWeight[]>(`/enterprise-jobs/${id(jobId)}/skill-weights`);
export const saveSkillWeights=(jobId:string,weights:Array<Omit<SkillWeight,'id'|'enterprise_job_id'>>)=>
  api<{enterprise_job_id:string;updated_count:number;weights:SkillWeight[]}>(`/enterprise-jobs/${id(jobId)}/skill-weights`,{
    method:'PUT',
    body:JSON.stringify({weights}),
  });

export const listEnterpriseMatchEvaluations=(jobId:string)=>
  api<EnterpriseMatchEvaluation[]>(`/enterprise-jobs/${id(jobId)}/match-reports`);
export const listCandidateSubmissions=(jobId:string)=>
  api<CandidateSubmission[]>(`/enterprise-jobs/${id(jobId)}/candidate-submissions`);
export const matchEnterpriseSubmissions=(jobId:string,submissionIds:string[])=>
  api<EnterpriseMatchBatch>(`/enterprise-jobs/${id(jobId)}/match-submissions`,{
    method:'POST',
    body:JSON.stringify({submission_ids:submissionIds}),
  });
export const getEnterpriseMatchTask=(taskId:string)=>
  api<EnterpriseMatchTask>(`/matches/tasks/${id(taskId)}`);
export const decideCandidate=(
  jobId:string,
  resumeId:string,
  evaluationId:string,
  decision:'fit'|'unfit',
  reasonCode?:string,
  reasonText?:string,
)=>{
  const query=new URLSearchParams({evaluation_id:evaluationId});
  if(reasonCode)query.set('reason_code',reasonCode);
  if(reasonText)query.set('reason_text',reasonText);
  return api<Record<string,unknown>>(`/enterprise-jobs/${id(jobId)}/candidates/${id(resumeId)}/mark-${decision}?${query.toString()}`,{
    method:'POST',
  });
};

export const getCandidateDecisionBoard=(jobId:string)=>
  api<CandidateDecisionBoard>(`/enterprise-jobs/${id(jobId)}/candidate-decision-board`);
export const getRecruiterDecisionAudit=(jobId:string)=>
  api<RecruiterDecisionAudit>(`/enterprise-jobs/${id(jobId)}/decision-audit`);
export const replayRecruiterDecisionAuditCase=(jobId:string,evaluationId:string)=>
  api<RecruiterDecisionAuditCaseReplay>(`/enterprise-jobs/${id(jobId)}/decision-audit/cases/${id(evaluationId)}`);
