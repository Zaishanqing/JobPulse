import {api,ApiError} from '../../../shared/api';
import type {GraphSnapshot,Position} from '../../../shared/api';
import type {CapabilityEvolution,EvolutionEvent,EvolutionEventCollection,StandardPosition,TrendAnalysisTask,TrendReport,TrendReportCollection,TrendReviewTask,TrendSkillDetail} from '../types';

const id=(value:string)=>encodeURIComponent(value);

/** Capability evolution is defined only for positions with a published graph. */
export function listPublishedPositions(){return api<Position[]>('/portal/positions')}
/** Catalog Admin API retained for the review workbench's explicit catalog workflow. */
export function listCatalogAdminStandardPositions(){return api<StandardPosition[]>('/positions')}
export function getPublishedGraph(positionId:string){return api<GraphSnapshot>(`/portal/positions/${id(positionId)}/graph`)}
type TrendReportWire=Omit<TrendReport,'graph_version_id'|'publishable'|'publication_blockers'|'skill_trends'>&{
  graph_version?:string|null;
  graph_version_id?:string|null;
  skill_trends?:TrendSkillDetail[];
  publication_gate?:{eligible?:boolean;blockers?:string[]};
};
type TrendReportCollectionWire=Omit<TrendReportCollection,'items'>&{items:TrendReportWire[]};

export function mapTrendReport(raw:TrendReportWire):TrendReport{
  const gate=raw.publication_gate;
  const graphSkills=raw.current_graph?.skills||[];
  const graphSkillById=new Map(graphSkills.map(item=>[item.skill_id,item]));
  const details=(raw.skill_trends||[]).map(item=>{
    const graphSkill=graphSkillById.get(item.skill_id);
    if(!graphSkill)throw new ApiError(502,`趋势明细中的技能 ${item.skill_id} 不属于当前岗位图谱`);
    return {...graphSkill,...item};
  });
  const byId=new Map(details.map(item=>[item.skill_id,item]));
  const enrich=(items:StandardPosition['required_skills'])=>items.map(item=>({...item,...byId.get(item.skill_id)}));
  return {
    ...raw,
    graph_version_id:raw.graph_version??raw.graph_version_id??null,
    current_graph:{...raw.current_graph,skills:enrich(raw.current_graph?.skills||[])},
    new_skills:enrich(raw.new_skills||[]),
    rising_skills:enrich(raw.rising_skills||[]),
    declining_skills:enrich(raw.declining_skills||[]),
    algorithm_version:raw.algorithm_version??null,
    formula_version:raw.formula_version??null,
    skill_catalog_version:raw.skill_catalog_version??null,
    unresolved_terms:raw.unresolved_terms||[],
    skill_trends:details,
    review_status:raw.review_status??null,
    review_task_id:raw.review_task_id??null,
    publishable:gate?.eligible??raw.status==='published',
    publication_blockers:gate?.blockers||[],
  };
}
export async function listTrendReports(positionId:string){
  const collection=await api<TrendReportCollectionWire>(`/positions/${id(positionId)}/trend-reports`);
  return collection.items.map(mapTrendReport);
}
export function createTrendAnalysis(positionId:string,timeWindowStart?:string,timeWindowEnd?:string){
  return api<TrendAnalysisTask>(`/positions/${id(positionId)}/trend-analysis/tasks`,{method:'POST',body:JSON.stringify({time_window_start:timeWindowStart||null,time_window_end:timeWindowEnd||null})});
}
export function getTrendAnalysisTask(taskId:string){return api<TrendAnalysisTask>(`/trend-analysis/tasks/${id(taskId)}`)}
export async function waitForTrendAnalysis(taskId:string){
  // 远程多源分析的真实运行时间可能超过 5 分钟；30 分钟内持续跟踪，避免把仍在运行的任务误报为失败。
  for(let attempt=0;attempt<1800;attempt+=1){
    const task=await getTrendAnalysisTask(taskId);
    if(task.canonical_status==='succeeded')return task;
    if(task.canonical_status==='failed'||task.canonical_status==='cancelled')throw new ApiError(409,task.error_message||`演化分析任务${task.canonical_status==='failed'?'失败':'已取消'}`);
    await new Promise(resolve=>window.setTimeout(resolve,1000));
  }
  throw new ApiError(408,'演化分析等待超时，请稍后在任务中心查看结果');
}
export async function publishTrendReport(reportId:string){return mapTrendReport(await api<TrendReportWire>(`/trend-reports/${id(reportId)}/publish`,{method:'POST'}))}
export function createTrendReview(reportId:string){return api<TrendReviewTask>('/review-tasks',{method:'POST',body:JSON.stringify({object_type:'trend_report',object_id:reportId,reason:'Trend 报告发布前人工复核'})})}
export function claimTrendReview(taskId:string){return api<TrendReviewTask>(`/review-tasks/${id(taskId)}/claim`,{method:'POST'})}
export function approveTrendReview(taskId:string){return api<TrendReviewTask>(`/review-tasks/${id(taskId)}/approve`,{method:'POST',body:JSON.stringify({review_comment:'已核验算法血缘、Evidence 与展示结果'})})}

export function listEvolutionEvents(positionId:string){return api<EvolutionEventCollection>(`/portal/admin/knowledge-graph/positions/${id(positionId)}/evolution-events`)}
/** Detail API 由 BFF 提供；list 响应已包含完整 Event（evidence/reason/metrics），主时间线不额外调用。 */
export function getEvolutionEvent(positionId:string,eventId:string){return api<EvolutionEvent>(`/portal/admin/knowledge-graph/positions/${id(positionId)}/evolution-events/${id(eventId)}`)}
export function getCapabilityEvolution(positionId:string){return api<CapabilityEvolution>(`/portal/admin/knowledge-graph/positions/${id(positionId)}/capability-evolution`)}
