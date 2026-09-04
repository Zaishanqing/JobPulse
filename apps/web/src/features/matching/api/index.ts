import {api,apiBlob} from '../../../shared/api';
import type {
  CVConfirmationResult,
  CVConfirmPayload,
  CVExtractionImport,
  CVExtractionTask,
  CVReview,
  EnterpriseJob,
  EvidenceDeletionResult,
  EvaluationReport,
  LearningPath,
  MatchPosition,
  MatchPreflight,
  MatchRanking,
  MatchReference,
  MatchTask,
  ResumeParseResult,
  ResumeRecord,
  ResumeSkill,
  ValidatedCVSnapshot,
  WhatIfAction,
  WhatIfResult,
} from '../types';

export type {
  Evidence,
  EvaluationReport,
  LearningPath,
  MatchReference,
  MatchTask,
  SemanticCandidate,
  SemanticRetrievalEvidence,
} from '../types';

const id=(value:string)=>encodeURIComponent(value);
export const listMyResumes=()=>api<ResumeRecord[]>('/resumes/me');
export const renameResume=(resumeId:string,displayName:string)=>api<ResumeRecord>(`/resumes/${id(resumeId)}`,{
  method:'PATCH',
  body:JSON.stringify({display_name:displayName}),
});
export const deleteResume=(resumeId:string)=>api<{resume_id:string;deleted:boolean}>(`/resumes/${id(resumeId)}`,{method:'DELETE'});
export const uploadResume=(file:File)=>{
  const form=new FormData();
  form.append('file',file);
  const endpoint=file.type.startsWith('image/')?'image':'file';
  return api<ResumeRecord>(`/resumes/${endpoint}`,{method:'POST',body:form});
};
export const uploadSourceCV=(file:File,useOcr=false)=>{
  const form=new FormData();
  form.append('file',file);
  if(useOcr||file.type.startsWith('image/'))form.append('use_ocr','true');
  return api<CVExtractionImport>('/source-cvs/upload-and-extract',{method:'POST',body:form});
};
export const getCVExtractionTask=(taskId:string)=>api<CVExtractionTask>(`/cv-extraction-tasks/${id(taskId)}`);
export const retryCVExtractionTask=(taskId:string)=>api<CVExtractionTask>(`/cv-extraction-tasks/${id(taskId)}/retry`,{method:'POST'});
export const reextractCVSourceVersion=(taskId:string)=>api<CVExtractionTask>(`/cv-extraction-tasks/${id(taskId)}/reextract`,{method:'POST'});
export const getCVExtractionReview=(taskId:string)=>api<CVReview>(`/cv-extraction-tasks/${id(taskId)}/review`);
export const getValidatedCVSnapshot=(snapshotId:string)=>api<ValidatedCVSnapshot>(`/validated-cv-snapshots/${id(snapshotId)}`);
export const reviseValidatedCVSnapshot=(snapshotId:string,payload:CVConfirmPayload)=>api<CVConfirmationResult>(`/validated-cv-snapshots/${id(snapshotId)}/revisions`,{method:'POST',body:JSON.stringify(payload)});
export const getCVSourcePreview=(fileId:string)=>apiBlob(`/files/${id(fileId)}/preview`);
export const confirmCVExtraction=(taskId:string,payload:CVConfirmPayload)=>api<CVConfirmationResult>(`/cv-extraction-tasks/${id(taskId)}/confirm`,{method:'POST',body:JSON.stringify(payload)});
export const parseResume=(resumeId:string)=>api<MatchTask>(`/resumes/${id(resumeId)}/parse`,{method:'POST'});
export const getResumeParseResult=(resumeId:string)=>api<ResumeParseResult>(`/resumes/${id(resumeId)}/parse-result`);
export const confirmResume=(resumeId:string)=>api<ResumeParseResult>(`/resumes/${id(resumeId)}/parse-result/confirm`,{method:'POST'});
export const getResumeSkillProfile=(resumeId:string)=>api<{resume_id:string;skills:ResumeSkill[]}>(`/resumes/${id(resumeId)}/skill-profile`);
export const generateResumeSkillProfile=(resumeId:string)=>api<{resume_id:string;skills:ResumeSkill[]}>(`/resumes/${id(resumeId)}/skill-profile`,{method:'POST'});

export const listMatchPositions=()=>api<MatchPosition[]>('/matches/positions');
export const listEnterpriseJobs=()=>api<EnterpriseJob[]>('/enterprise-jobs');
export const listMatchEvaluations=()=>api<MatchReference[]>('/matches/reports');
export const getMatchRanking=(resumeId:string)=>api<MatchRanking>(`/matches/rankings?resume_id=${id(resumeId)}`);
export const startMatchRanking=(resumeId:string)=>api<MatchRanking>('/matches/rankings',{
  method:'POST',
  body:JSON.stringify({resume_id:resumeId}),
});
export const cancelMatchRanking=(resumeId:string)=>api<MatchRanking>('/matches/rankings/cancel',{
  method:'POST',
  body:JSON.stringify({resume_id:resumeId}),
});
export const getMatchPreflight=(resumeId:string,positionId:string,targetType:'standard_position'|'enterprise_job'='standard_position')=>api<MatchPreflight>(`/matches/preflight?resume_id=${id(resumeId)}&position_id=${id(positionId)}&target_type=${id(targetType)}`);
const evaluationCacheTtlMs=2*60*1000;
const evaluationCache=new Map<string,{value:EvaluationReport;expiresAt:number}>();
const evaluationRequests=new Map<string,Promise<EvaluationReport>>();
const evaluationCacheEnabled=typeof navigator==='undefined'||!navigator.userAgent.toLowerCase().includes('jsdom');
let evaluationCacheSession:string|null|undefined;

const ensureEvaluationCacheSession=()=>{
  const session=localStorage.getItem('main_access_token');
  if(evaluationCacheSession!==undefined&&evaluationCacheSession!==session){
    evaluationCache.clear();
    evaluationRequests.clear();
  }
  evaluationCacheSession=session;
  return session;
};

export const getMatchEvaluation=(evaluationId:string)=>{
  // 历史报告体积较大。同一会话内短时间重复进入详情页时复用已解析对象，
  // 同时合并悬停预取和页面加载产生的并发请求，避免重复下载与 JSON 解析。
  if(!evaluationCacheEnabled)return api<EvaluationReport>(`/matches/reports/${id(evaluationId)}`);
  // 登录身份变化后清空缓存，禁止不同账号在同一浏览器会话内复用报告对象。
  const requestSession=ensureEvaluationCacheSession();
  const cached=evaluationCache.get(evaluationId);
  if(cached&&cached.expiresAt>Date.now())return Promise.resolve(cached.value);
  if(cached)evaluationCache.delete(evaluationId);
  const pending=evaluationRequests.get(evaluationId);
  if(pending)return pending;
  const request=api<EvaluationReport>(`/matches/reports/${id(evaluationId)}`)
    .then(value=>{
      if(localStorage.getItem('main_access_token')===requestSession){
        evaluationCache.set(evaluationId,{value,expiresAt:Date.now()+evaluationCacheTtlMs});
      }
      return value;
    })
    .finally(()=>{
      if(evaluationRequests.get(evaluationId)===request)evaluationRequests.delete(evaluationId);
    });
  evaluationRequests.set(evaluationId,request);
  return request;
};

export const prefetchMatchEvaluation=(evaluationId:string)=>{
  // 预取失败不抢占历史列表的错误提示；真正进入报告时仍由详情页展示失败状态。
  void getMatchEvaluation(evaluationId).catch(()=>undefined);
};
export const evaluateMatchWhatIf=(evaluationId:string,actions:WhatIfAction[])=>api<WhatIfResult>(`/matches/reports/${id(evaluationId)}/what-if`,{
  method:'POST',
  body:JSON.stringify({actions}),
});
export const evaluateEvidenceDeletion=(evaluationId:string,deletionKind:'critical'|'noncritical',evidenceSourceIds:string[])=>api<EvidenceDeletionResult>(`/matches/reports/${id(evaluationId)}/evidence-deletions`,{
  method:'POST',
  body:JSON.stringify({deletion_kind:deletionKind,evidence_source_ids:evidenceSourceIds}),
});
export const personalRunIdempotencyKey=(resumeId:string,targetId:string,runId:string)=>`personal-run:${resumeId}:${targetId}:${runId}`;
export const createMatchTask=(resumeId:string,targetId:string,runId:string)=>api<MatchTask>('/matches/tasks',{
  method:'POST',
  headers:{'Idempotency-Key':personalRunIdempotencyKey(resumeId,targetId,runId)},
  body:JSON.stringify({resume_id:resumeId,target_type:'standard_position',target_id:targetId,use_enterprise_weights:false,generate_learning_path:true}),
});
export const createEnterpriseJobMatchTask=(resumeId:string,jobId:string,runId:string)=>api<MatchTask>('/matches/tasks',{
  method:'POST',
  headers:{'Idempotency-Key':personalRunIdempotencyKey(resumeId,`enterprise-job:${jobId}`,runId)},
  body:JSON.stringify({resume_id:resumeId,target_type:'enterprise_job',target_id:jobId,use_enterprise_weights:true,generate_learning_path:true}),
});
export const getMatchTask=(taskId:string)=>api<MatchTask>(`/matches/tasks/${id(taskId)}`);
export const restartMatchTask=(taskId:string,runId:string)=>api<MatchTask>(`/matches/tasks/${id(taskId)}/restart`,{
  method:'POST',
  headers:{'Idempotency-Key':`personal-restart:${taskId}:${runId}`},
});
export const abandonMatchTask=(taskId:string)=>api<MatchTask>(`/matches/tasks/${id(taskId)}/abandon`,{method:'POST'});
export const createLearningPath=(evaluationId:string,targetPositionId?:string,timeBudgetHours?:number)=>api<LearningPath>('/learning-paths',{
  method:'POST',
  body:JSON.stringify({evaluation_id:evaluationId,...(targetPositionId?{target_position_id:targetPositionId}:{}),...(timeBudgetHours!==undefined?{time_budget_hours:timeBudgetHours}:{})}),
});
export const listLearningPaths=()=>api<LearningPath[]>('/learning-paths');
export const getLearningPath=(pathId:string)=>api<LearningPath>(`/learning-paths/${id(pathId)}`);
export const exportLearningPath=(pathId:string)=>api<{format:string;learning_path:LearningPath}>(`/learning-paths/${id(pathId)}/export`);
