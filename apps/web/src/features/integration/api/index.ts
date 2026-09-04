import {api} from '../../../shared/api';

export type IntegrationStatus={status:string;service?:string;upstream_trace_id?:string|null};
export type JDSyncStatus={entity_type:string;main_system_id:string;knowledge_graph_id:string|null;sync_status:string;last_trace_id:string|null};
export type JDSyncResult=JDSyncStatus&{idempotent:boolean};
export type WorkflowStage={status:string;id:string|null;count?:number;error:{code?:string|null;message?:string|null}|null;details?:Record<string,unknown>&{review_flags?:Array<Record<string,unknown>>}};
export type WorkflowAction={code:string;method:'POST'|'PUT'|'PATCH'|'DELETE';endpoint:string;permission:string;authorized:boolean;enabled:boolean;reason:string|null;input?:{required:string[]}};
export type WorkflowChain={entity_id:string;source:WorkflowStage;extraction:WorkflowStage;validation:WorkflowStage;draft:WorkflowStage;review:WorkflowStage;publication:WorkflowStage;outbox:WorkflowStage;knowledge_graph:WorkflowStage;discovery:WorkflowStage;matching:WorkflowStage;actions:WorkflowAction[]};
export type WorkflowStatus={jd:WorkflowChain|null;cv:WorkflowChain|null};
export type ResumeParseResult={resume_id:string;education:Array<Record<string,unknown>>;projects:Array<Record<string,unknown>>;internships:Array<Record<string,unknown>>;skills:Array<Record<string,unknown>>;certificates:Array<Record<string,unknown>>;competitions:Array<Record<string,unknown>>;parse_confidence:number;need_review:boolean};
export type PortalPosition={position_id:string;position_name:string};

export const getIntegrationStatus=()=>api<IntegrationStatus>('/integrations/knowledge-graph/status');
export const getJDSyncStatus=(documentId:string)=>api<JDSyncStatus>(`/integrations/knowledge-graph/jds/${encodeURIComponent(documentId)}/status`);
export const syncJD=(documentId:string)=>api<JDSyncResult>(`/integrations/knowledge-graph/jds/${encodeURIComponent(documentId)}/sync`,{method:'POST',body:JSON.stringify({})});
export const getWorkflowStatus=(jdId:string,cvTaskId:string)=>{
  const params=new URLSearchParams();if(jdId.trim())params.set('jd_id',jdId.trim());if(cvTaskId.trim())params.set('cv_task_id',cvTaskId.trim());
  return api<WorkflowStatus>(`/portal/admin/integration-status?${params.toString()}`);
};
export const executeWorkflowAction=(action:WorkflowAction,body?:Record<string,unknown>)=>api<unknown>(action.endpoint,{method:action.method,...(body?{body:JSON.stringify(body)}:{})});
export const getResumeParseResult=(resumeId:string)=>api<ResumeParseResult>(`/resumes/${encodeURIComponent(resumeId)}/parse-result`);
export const updateResumeParseResult=(resumeId:string,value:ResumeParseResult)=>api<ResumeParseResult>(`/resumes/${encodeURIComponent(resumeId)}/parse-result`,{method:'PUT',body:JSON.stringify(value)});
export const listPortalPositions=()=>api<PortalPosition[]>('/positions');
