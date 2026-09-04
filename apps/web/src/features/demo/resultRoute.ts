import type {PortalDemoTask} from './types';

type DemoTaskResultRoute={
  path:string|null;
  reason:string|null;
};

export type EvidenceReferenceRoute={
  path:string|null;
  reason:string|null;
};

const evidenceLocationParams=(evidence:{
  source_fragment_id?:string;
  quote?:string;
  start?:number|null;
  end?:number|null;
})=>{
  if(!evidence.source_fragment_id||evidence.start==null||evidence.end==null||evidence.end<=evidence.start)return null;
  return new URLSearchParams({
    sourceEvidenceId:evidence.source_fragment_id,
    sourceEvidenceStart:String(evidence.start),
    sourceEvidenceEnd:String(evidence.end),
    ...(evidence.quote?{sourceEvidenceQuote:evidence.quote}:{}),
  });
};

export function resolveEvidenceReference(evidence:{
  source_object_type:string;
  source_object_id:string;
  result_reference:string;
  source_fragment_id?:string;
  quote?:string;
  start?:number|null;
  end?:number|null;
  version:{resume_id?:string|null;position_id?:string|null;evaluation_id?:string|null};
}):EvidenceReferenceRoute{
  const reference=evidence.result_reference;
  if(!reference)return {path:null,reason:'服务未返回结果引用（result_reference）'};
  const location=evidenceLocationParams(evidence);
  if(!location)return {path:null,reason:'证据缺少可定位的原文区间'};
  if(evidence.source_object_type==='validated_cv_snapshot'){
    if(evidence.version?.resume_id){
      location.set('resumeId',evidence.version.resume_id);
      return {path:`/profile/resumes?${location.toString()}`,reason:null};
    }
    return {path:null,reason:'Snapshot 与 Resume 的前端关联尚未冻结'};
  }
  if(evidence.source_object_type==='position_profile'){
    if(evidence.version?.position_id)return {path:`/positions/${encodeURIComponent(evidence.version.position_id)}?${location.toString()}`,reason:null};
    return {path:null,reason:'Position Profile 前端关联尚未冻结'};
  }
  if(evidence.source_object_type==='matching_evidence'){
    return {path:null,reason:'当前证据没有可唯一定位的原始来源'};
  }
  return {path:null,reason:`来源类型 ${evidence.source_object_type} 前端关联尚未冻结`};
}

export function resolveDemoTaskResult(task:PortalDemoTask):DemoTaskResultRoute{
  if(task.status!=='succeeded'){
    return {path:null,reason:'任务尚未成功'};
  }

  const reference=task.result_reference;

  if(task.task_type==='matching'&&reference){
    const match=
      reference.match(/^\/api\/v1\/matches\/reports\/([^/]+)$/)
      ??reference.match(/^matching_evaluation:([^:]+)$/)
      ??reference.match(/^evaluation_report:([^:]+)$/);
    if(match)return {path:`/matching/reports/${encodeURIComponent(match[1])}`,reason:null};
  }

  if(task.task_type==='trend'&&reference){
    const match=reference.match(/^trend_report:([^:]+)$/);
    if(match){
      return {
        path:`/analysis/evolution?positionId=${encodeURIComponent(task.object_id)}&resultReference=${encodeURIComponent(reference)}`,
        reason:null,
      };
    }
    if(reference.match(/^trend-intelligence:[^:]+$/)){
      return {path:null,reason:'Trend Intelligence 结果页映射尚未冻结'};
    }
  }

  if(task.task_type==='discovery'&&reference){
    const match=reference.match(/^discovery_run:([^:]+)$/);
    if(match)return {path:`/admin/discovery?runId=${encodeURIComponent(match[1])}`,reason:null};
  }

  if(task.task_type==='cv_extraction'&&reference){
    const snapshot=reference.match(/^\/api\/v1\/validated-cv-snapshots\/([^/]+)$/);
    if(snapshot)return {path:null,reason:'Snapshot 与 Resume 的前端关联尚未冻结'};
  }

  if(task.task_type==='cv_extraction'&&reference){
    const extraction=reference.match(/^\/api\/v1\/cv-extraction-tasks\/([^/]+)$/);
    if(extraction)return {path:null,reason:'CV 任务恢复 Contract 尚未冻结'};
  }

  if(task.task_type==='jd_extraction'&&reference?.match(/^\/api\/v1\/extraction-tasks\/[^/]+$/)){
    return {path:null,reason:'Contract 只提供内部抽取任务引用，未提供前端业务结果'};
  }

  const internalTaskReference=
    (task.task_type==='trend'&&reference?.match(/^\/api\/v1\/(?:predicted-positions|trend-analysis)\/tasks\/[^/]+$/))
    ||(task.task_type==='discovery'&&reference?.match(/^\/api\/v1\/position-clusters\/tasks\/[^/]+$/))
    ||(task.task_type==='matching'&&reference?.match(/^\/api\/v1\/matches\/tasks\/[^/]+$/));
  if(internalTaskReference){
    return {path:null,reason:'Contract 只提供内部任务引用，未提供前端业务结果'};
  }

  return {path:null,reason:'结果引用格式未冻结'};
}
