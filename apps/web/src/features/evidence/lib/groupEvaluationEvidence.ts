import type {Evidence,EvaluationReport} from '../../matching/types';

export interface EvaluationEvidenceGroups{
  all:Evidence[];
  candidate:Evidence[];
  position:Evidence[];
  gap:Evidence[];
  unresolved:Evidence[];
}

export function groupEvaluationEvidence(report:EvaluationReport):EvaluationEvidenceGroups{
  const rows:Evidence[]=[];
  const push=(items:Evidence[]|undefined)=>{if(items)for(const item of items)rows.push(item)};
  const evaluation=report.evaluation;
  for(const skill of evaluation.skill_results){
    push(skill.candidate_evidence);
    push(skill.position_evidence);
  }
  for(const item of evaluation.hard_constraint_results){
    push(item.candidate_evidence);
    push(item.position_evidence);
  }
  for(const item of [...evaluation.responsibility_results,...evaluation.project_results,...evaluation.scenario_results]){
    push(item.candidate_evidence);
    push(item.position_evidence);
  }
  for(const item of report.gap_analysis.prioritized_gaps)push(item.evidence);
  const all=rows.filter((item,index,items)=>items.findIndex(other=>other.result_reference===item.result_reference)===index);
  return {
    all,
    candidate:all.filter(item=>item.source_object_type==='validated_cv_snapshot'),
    position:all.filter(item=>item.source_object_type==='position_profile'),
    gap:all.filter(item=>item.source_object_type==='matching_evidence'),
    unresolved:all.filter(item=>!['validated_cv_snapshot','position_profile','matching_evidence'].includes(item.source_object_type)),
  };
}
