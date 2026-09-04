import {expect,test} from 'vitest';
import type {EvaluationReport} from '../../matching/types';
import {groupEvaluationEvidence} from './groupEvaluationEvidence';

function evidence(source_object_type:string,result_reference:string){
  return {
    source_object_type,
    source_object_id:`${source_object_type}-1`,
    source_document_id:'doc-1',
    source_fragment_id:'frag-1',
    quote:'证据原文',
    start:0,
    end:8,
    alignment:'exact',
    occurrence_index:0,
    version:{},
    result_reference,
  };
}

function report():EvaluationReport{
  const candidate=evidence('validated_cv_snapshot','candidate:1');
  const position=evidence('position_profile','position:1');
  const gap=evidence('matching_evidence','gap:1');
  const unknown=evidence('skill_relation','unknown:1');
  return {
    evaluation_id:'EVAL_1',
    stale:false,
    stale_reason_codes:[],
    evaluation:{
      evaluation_id:'EVAL_1',
      algorithm_version:'v1',
      evaluation_status:'completed',
      hard_constraint_results:[],
      skill_results:[{requirement_id:'r1',match_status:'matched',position_evidence:[position,candidate],candidate_evidence:[candidate,unknown],reason_code:'OK',confidence:.9}],
      responsibility_results:[],
      project_results:[],
      scenario_results:[],
      summary:null,
      final_match_result:null,
    },
    gap_analysis:{prioritized_gaps:[{requirement_id:'g1',gap_type:'required_skill_missing',priority:'high',priority_score:50,reason_codes:[],evidence:[gap]}]},
    versions:{},
    lineage:null,
    created_at:null,
    updated_at:null,
  } as unknown as EvaluationReport;
}

test('按来源类型分庭并去重',()=>{
  const groups=groupEvaluationEvidence(report());
  expect(groups.candidate.map(item=>item.result_reference)).toEqual(['candidate:1']);
  expect(groups.position.map(item=>item.result_reference)).toEqual(['position:1']);
  expect(groups.gap.map(item=>item.result_reference)).toEqual(['gap:1']);
  expect(groups.unresolved.map(item=>item.result_reference)).toEqual(['unknown:1']);
  expect(groups.all).toHaveLength(4);
});
