import {api,type GovernanceReviewAction,type GovernanceReviewContext,type GovernanceReviewTask,type ReviewAction,type ReviewTask} from '../../../shared/api';
export const listReviewTasks=(status?:string)=>api<ReviewTask[]>(`/portal/admin/knowledge-graph/review-tasks${status?`?status=${status}`:''}`);
export const actOnReview=(taskId:number,action:ReviewAction,reason:string,payload?:Record<string,unknown>)=>api(`/portal/admin/knowledge-graph/review-tasks/${taskId}/${action}`,{method:'POST',body:JSON.stringify({reason,payload})});
export async function listGovernanceReviewTasks(
  options:{page?:number;pageSize?:number;status?:string}={},
):Promise<GovernanceReviewTask[]>{
  const {page=1,pageSize=20,status}=options;
  const query=new URLSearchParams({source_system:'main-system',page:String(page),page_size:String(pageSize)});
  if(status)query.set('status',status);
  return api<GovernanceReviewTask[]>(`/review-tasks?${query.toString()}`);
}
export const getGovernanceReviewSummary=()=>api<{pending:number;claimed:number;approved:number;rejected:number;modified:number}>('/review-tasks/summary');
export const getGovernanceReviewContext=(taskId:string)=>api<GovernanceReviewContext>(`/review-tasks/${encodeURIComponent(taskId)}/context`);
export const actOnGovernanceReview=(taskId:string,action:GovernanceReviewAction,reviewComment?:string)=>api<GovernanceReviewTask>(`/review-tasks/${taskId}/${action}`,{method:'POST',body:action==='claim'||action==='release'?undefined:JSON.stringify({review_comment:reviewComment})});
export const batchReviewTasks=(taskIds:string[],action:'claim'|'approve'|'reject',reason:string)=>api<{contract_version:string;action:string;task_ids:string[];statuses:Record<string,string>}>('/review-tasks/batch',{method:'POST',body:JSON.stringify({task_ids:taskIds,action,reason})});
export const mapReviewPosition=(parseResultId:string,payload:{target_position_id:string;career_level?:string|null;leadership_scope?:string|null;technology_focus_codes?:string[];industry_context_codes?:string[]})=>api(`/jd-parse-results/${encodeURIComponent(parseResultId)}/position-catalog-mapping`,{method:'POST',body:JSON.stringify(payload)});
export const publishReviewedJD=(parseResultId:string)=>api(`/jd-parse-results/${encodeURIComponent(parseResultId)}/publish`,{method:'POST'});
export const deprecateJD=(jdId:string)=>api(`/jds/${encodeURIComponent(jdId)}/deprecate`,{method:'POST'});
