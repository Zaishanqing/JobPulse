import {describe,expect,it} from 'vitest';
import type {EvaluationReport,ResponsibilityResult} from '../types';
import {
  buildResponsibilityViewModel,
  matchingMethodLabel,
  overallScoreText,
  referenceStatusLabel,
  resolveMatchingMethod,
  responsibilityStatusLabel,
} from './responsibility';

type EvidenceLike=ResponsibilityResult['candidate_evidence'][number];

const evidence:EvidenceLike={
  source_object_type:'validated_cv_snapshot',
  source_object_id:'snapshot-1',
  source_document_id:'version-1',
  source_fragment_id:'snapshot:1',
  quote:'负责后端服务可靠性建设',
  start:0,
  end:12,
  alignment:'exact',
  occurrence_index:0,
  version:{validated_cv_snapshot_id:'snapshot-1',source_cv_version_id:'version-1',resume_id:'resume-1',position_id:null,graph_version:null,source_jd_version_id:null,evaluation_id:null},
  result_reference:'validated_cv_snapshot:snapshot-1#evidence:snapshot:1:0-12',
};

const baseResponsibility:ResponsibilityResult={
  requirement_id:'responsibility:1',
  position_requirement:'负责后端服务可靠性',
  match_status:'matched',
  position_evidence:[],
  candidate_evidence:[evidence],
  reason_code:'RESPONSIBILITY_MATCHED',
  confidence:0.9,
  match_type:'semantic',
};

const report=(updates:Partial<EvaluationReport>={}):EvaluationReport=>({
  evaluation_id:'evaluation-1',
  status:'current',
  stale:false,
  stale_reason_codes:[],
  gap_analysis:{generation_status:'completed',prioritized_gaps:[],learning_path:[],counterfactual_suggestions:[],candidate_actions:[],learning_routes:[],skill_path_decisions:[]},
  versions:{},
  lineage:null,
  created_at:null,
  updated_at:null,
  evaluation:{
    evaluation_id:'evaluation-1',
    cv_profile_id:null,
    position_profile_id:null,
    algorithm_version:'deterministic-matching.v8',
    evaluation_status:'completed',
    hard_constraint_results:[],
    skill_results:[],
    responsibility_results:[],
    project_results:[],
    scenario_results:[],
    summary:null,
    final_match_result:null,
  },
  ...updates,
});

describe('responsibility view model',()=>{
  it('keeps product labels in Chinese and never leaks internal status names',()=>{
    expect(matchingMethodLabel('rule')).toBe('规则匹配');
    expect(matchingMethodLabel('semantic_verified')).toBe('智能语义匹配');
    expect(responsibilityStatusLabel('partial')).toBe('部分匹配');
    expect(responsibilityStatusLabel('insufficient_evidence')).toBe('证据不足');
    expect(responsibilityStatusLabel('not_observed')).toBe('暂未发现');
  });

  it('builds a formal view model with product-level semantic diagnostics',()=>{
    const item:ResponsibilityResult={
      ...baseResponsibility,
      status_detail:'partial',
      ce_score:1.2,
      retrieval_score:0.61,
      threshold_margin:0.13,
      top_candidates:[{experience_id:'exp:1',text:'负责后端服务可靠性建设',retrieval_score:0.61,ce_score:1.2,threshold_margin:0.13,evidence_refs:[evidence]}],
    };
    const view=buildResponsibilityViewModel(item);
    expect(view.mode).toBe('semantic_verified');
    expect(view.statusLabel).toBe('部分匹配');
    expect(view.semanticDiagnostics).toEqual({
      semanticVerification:'通过',
      evidenceStrength:'较高',
      decisionConfidence:'较高',
    });
  });

  it('does not show semantic diagnostics in rule mode',()=>{
    const view=buildResponsibilityViewModel(baseResponsibility);
    expect(view.mode).toBe('rule');
    expect(view.semanticDiagnostics).toBeUndefined();
  });

  it('resolves a stable method and produces a Chinese score-only presentation',()=>{
    expect(resolveMatchingMethod(report({
      matching_method:'semantic_verified',
      evaluation:{...report().evaluation,responsibility_results:[]},
    }))).toBe('semantic_verified');
    expect(overallScoreText(null)).toBe('暂无法评分');
    expect(overallScoreText(82.4)).toBe('82 分');
    expect(referenceStatusLabel('stale')).toBe('结果已过期');
  });
});
