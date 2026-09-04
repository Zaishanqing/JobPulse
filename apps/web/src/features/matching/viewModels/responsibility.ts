import type {
  EvaluationReport,
  MatchingMethod,
  ResponsibilityResult,
} from '../types';

export type ResponsibilityViewModel={
  finalStatus:string;
  statusLabel:string;
  evidence:ResponsibilityResult['candidate_evidence'];
  confidence:number|null;
  mode:MatchingMethod;
  semanticDiagnostics?:{
    semanticVerification:string;
    evidenceStrength:string|null;
    decisionConfidence:string|null;
  };
};

const matchingLabels:Record<MatchingMethod,string>={
  rule:'规则匹配',
  semantic_verified:'智能语义匹配',
  unknown:'匹配方式待确认',
};

const responsibilityLabels:Record<string,string>={
  matched:'匹配',
  partial:'部分匹配',
  insufficient_evidence:'证据不足',
  not_observed:'暂未发现',
  uncertain:'待确认',
  unknown:'待确认',
  unresolved:'尚未解析',
};

export const matchingMethodLabel=(method:MatchingMethod|undefined|null)=>matchingLabels[method||'unknown'];

export const responsibilityStatusLabel=(status:string|null|undefined)=>responsibilityLabels[status||'unknown'];

export const isFormalResponsibility=(item:ResponsibilityResult)=>Boolean(
  item.ce_score!==undefined
  || item.retrieval_score!==undefined
  || item.threshold_margin!==undefined
  || (item.top_candidates?.length??0)>0
);

export const evidenceStrengthLabel=(value:number|null|undefined)=>{
  if(value===null||value===undefined)return null;
  if(value>=0.55)return '较高';
  if(value>=0.35)return '中等';
  return '较低';
};

export const decisionConfidenceLabel=(value:number|null|undefined)=>{
  if(value===null||value===undefined)return null;
  return value>=0.1?'较高':'接近判断边界';
};

export const semanticVerificationLabel=(item:ResponsibilityResult)=>{
  if(item.status_detail==='matched'||item.status_detail==='partial')return '通过';
  return '未通过';
};

export const buildResponsibilityViewModel=(item:ResponsibilityResult):ResponsibilityViewModel=>{
  const mode: MatchingMethod=isFormalResponsibility(item)?'semantic_verified':'rule';
  const finalStatus=item.status_detail||item.match_status||'unknown';
  const viewModel:ResponsibilityViewModel={
    finalStatus,
    statusLabel:responsibilityStatusLabel(finalStatus),
    evidence:item.candidate_evidence||[],
    confidence:item.confidence??null,
    mode,
  };
  if(mode==='semantic_verified'){
    viewModel.semanticDiagnostics={
      semanticVerification:semanticVerificationLabel(item),
      evidenceStrength:evidenceStrengthLabel(item.retrieval_score),
      decisionConfidence:decisionConfidenceLabel(item.threshold_margin),
    };
  }
  return viewModel;
};

export const resolveMatchingMethod=(
  report:Pick<EvaluationReport,'matching_method'|'evaluation'>,
):MatchingMethod=>{
  if(report.matching_method&&report.matching_method!=='unknown')return report.matching_method;
  const hasFormal=(report.evaluation.responsibility_results||[]).some(isFormalResponsibility);
  return hasFormal?'semantic_verified':'rule';
};

export const overallScoreText=(value:number|null|undefined)=>{
  if(value===null||value===undefined)return '暂无法评分';
  return `${Math.round(value)} 分`;
};

const referenceStatusLabels:Record<string,string>={
  pending:'等待中',
  running:'匹配中',
  succeeded:'已完成',
  failed:'失败',
  cancelled:'已取消',
  current:'当前结果',
  stale:'结果已过期',
  completed:'已完成',
};

export const referenceStatusLabel=(value:string|null|undefined,stale:boolean=false)=>{
  if(stale)return referenceStatusLabels.stale;
  return referenceStatusLabels[value||'']||'待确认';
};
