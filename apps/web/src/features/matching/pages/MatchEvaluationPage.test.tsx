import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import type {DimensionScore,Evidence,GapAnalysis,ResponsibilityResult} from '../types';
import {planningText,ResponsibilityDetailList,WhatIfWorkbench} from './MatchEvaluationPage';

vi.mock('echarts',()=>({
  init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()}),
}));

afterEach(()=>{cleanup();vi.unstubAllGlobals()});

test('learning plan text replaces internal UUIDs with the resolved ability name',()=>{
  const id='770c5e11-8e31-49e8-a7a5-5ce17427c00d';
  expect(planningText(`${id} 达到可独立使用的能力证据`,'视觉语言模型')).toBe('视觉语言模型达到可独立使用的能力证据');
  expect(planningText(`对应岗位描述要求 standard-position: skill: ${id}`,'视觉语言模型')).toBe('对应岗位描述要求视觉语言模型');
});

const evidence:Evidence={
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

const base:ResponsibilityResult={
  requirement_id:'responsibility:1',
  position_requirement:'负责后端服务可靠性',
  candidate_experience:'负责后端服务可靠性建设',
  match_status:'matched',
  position_evidence:[],
  candidate_evidence:[evidence],
  reason_code:'RESPONSIBILITY_MATCHED',
  confidence:0.9,
  match_type:'semantic',
};

test('formal mode shows one common card with Chinese semantic diagnostics',()=>{
  render(<ResponsibilityDetailList items={[{
    ...base,
    status_detail:'partial',
    ce_score:1.2,
    retrieval_score:0.61,
    threshold_margin:0.13,
    top_candidates:[{experience_id:'exp:1',text:'负责后端服务可靠性建设',retrieval_score:0.61,ce_score:1.2,threshold_margin:0.13,evidence_refs:[evidence]}],
  }]}/>);
  expect(screen.getByText('负责后端服务可靠性')).toBeInTheDocument();
  expect(screen.getByText('部分匹配')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/负责后端服务可靠性/}));
  expect(screen.getByText('智能语义验证')).toBeInTheDocument();
  expect(screen.getByText('语义验证：通过')).toBeInTheDocument();
  expect(screen.getByText('证据相关度：较高')).toBeInTheDocument();
  expect(screen.queryByText('matched')).not.toBeInTheDocument();
  expect(screen.queryByText('partial')).not.toBeInTheDocument();
});

test('rule mode reuses the same card and does not render semantic placeholders',()=>{
  render(<ResponsibilityDetailList items={[base]}/>);
  expect(screen.getByText('匹配')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/负责后端服务可靠性/}));
  expect(screen.getAllByText('负责后端服务可靠性建设').length).toBeGreaterThan(0);
  expect(screen.queryByText('智能语义验证')).not.toBeInTheDocument();
  expect(screen.queryByText('语义验证：')).not.toBeInTheDocument();
});

test('what-if shows three passive route cards with radar and action combination',()=>{
  const action={
    action_id:'action-project',
    action_type:'add_project_experience' as const,
    skill_id:null,
    canonical_name:'补充项目实践',
    target_requirement_ids:['responsibility:1'],
    responsibilities:['负责项目交付'],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
    milestone_status:'planned',
  };
  const gap:GapAnalysis={
    generation_status:'completed',
    prioritized_gaps:[],
    learning_path:[],
    counterfactual_suggestions:[],
    candidate_actions:[action],
    learning_routes:[
      {route_type:'fastest_employment',action_ids:['action-project'],total_cost_hours:12,baseline_score:92.1569,modeled_final_score:93.3953,modeled_score_delta:1.2384,final_score:93.3953,projected_match_gain:1.2384,confidence_gain:0,target_reachable:false,final_recommendation:'insufficient_information',remaining_blocker_ids:[],path_refs:[],algorithm_version:'learning-route-enumeration.v2'},
      {route_type:'budget_max_gain',action_ids:['action-project'],total_cost_hours:26,baseline_score:92.1569,modeled_final_score:94.6336,modeled_score_delta:2.4767,final_score:94.6336,projected_match_gain:2.4767,confidence_gain:0,target_reachable:false,final_recommendation:'insufficient_information',remaining_blocker_ids:[],path_refs:[],algorithm_version:'learning-route-enumeration.v2'},
      {route_type:'foundation_first',action_ids:['action-project'],total_cost_hours:15.6,baseline_score:92.1569,modeled_final_score:93.3953,modeled_score_delta:1.2384,final_score:93.3953,projected_match_gain:1.2384,confidence_gain:0,target_reachable:false,final_recommendation:'insufficient_information',remaining_blocker_ids:[],path_refs:[],algorithm_version:'learning-route-enumeration.v2'},
    ],
    minimal_action_set:{status:'unreachable',source_evaluation_id:'evaluation-1',selected_action_ids:[],deferred_action_ids:['action-project'],action_costs:[],minimum_action_count:0,total_cost_hours:0,budget_used_hours:0,target_reachable:false,covered_requirement_ids:[],evidence_refs:[],path_refs:[],unreachable_reason_codes:['TARGET_STATE_UNREACHED'],search_status:'exact_bounded',algorithm_version:'minimal-action-set.v3'} as unknown as NonNullable<GapAnalysis['minimal_action_set']>,
    skill_path_decisions:[],
  };
  const dimensionScores:DimensionScore[]=[
    {dimension:'required_skills',score:80,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
    {dimension:'capability_level',score:70,confidence:.8,configured_weight:.2,effective_weight:.2,applicable_count:1,scored_count:1,uncertain_count:0},
    {dimension:'hard_conditions',score:100,confidence:1,configured_weight:.1,effective_weight:.1,applicable_count:1,scored_count:1,uncertain_count:0},
  ];
  render(<WhatIfWorkbench evaluationId="evaluation-1" gap={gap} dimensionScores={dimensionScores}/>);
  expect(screen.queryByText('最小可行行动集')).not.toBeInTheDocument();
  expect(document.querySelectorAll('.match-route-card')).toHaveLength(3);
  expect(screen.queryByRole('button',{name:'选择路线'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'模拟所选行动'})).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(screen.getAllByText('预计投入时间')).toHaveLength(3);
  expect(screen.getAllByText('补充项目实践')).toHaveLength(3);
  expect(document.querySelectorAll('.match-route-radar')).toHaveLength(3);
  expect(screen.queryByText(/预计提升 3 分/)).not.toBeInTheDocument();
});

test('what-if route action combination resolves concrete requirement names',()=>{
  const action={
    action_id:'learn-vllm',
    action_type:'add_skill' as const,
    skill_id:'skill:vllm',
    canonical_name:null,
    target_requirement_ids:['required:skill:vllm'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  };
  const gap:GapAnalysis={
    generation_status:'completed',
    prioritized_gaps:[],
    learning_path:[],
    counterfactual_suggestions:[],
    candidate_actions:[action],
    learning_routes:[{
      route_type:'budget_max_gain',
      action_ids:['learn-vllm'],
      total_cost_hours:12,
      baseline_score:68,
      modeled_final_score:81,
      modeled_score_delta:13,
      final_score:81,
      projected_match_gain:13,
      confidence_gain:null,
      target_reachable:true,
      final_recommendation:'potential_match',
      remaining_blocker_ids:[],
      path_refs:[],
      algorithm_version:'learning-route-enumeration.v2',
    }],
    skill_path_decisions:[],
  };
  render(<WhatIfWorkbench evaluationId="evaluation-1" gap={gap} dimensionScores={[]} actionName={candidate=>candidate.action_type==='add_skill'?'大模型推理引擎 vLLM':candidate.canonical_name||'候选行动'}/>);
  expect(screen.getByText('大模型推理引擎 vLLM')).toBeInTheDocument();
  expect(screen.queryByText('岗位能力要求')).not.toBeInTheDocument();
});

test('what-if route card renders scenario radar comparison and improvement advice',()=>{
  const action={
    action_id:'learn-python',
    action_type:'add_skill' as const,
    skill_id:'skill_python',
    canonical_name:null,
    target_requirement_ids:['required:skill_python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  };
  const baseline:DimensionScore[]=[
    {dimension:'required_skills',score:80,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
    {dimension:'capability_level',score:70,confidence:.8,configured_weight:.2,effective_weight:.2,applicable_count:1,scored_count:1,uncertain_count:0},
    {dimension:'hard_conditions',score:100,confidence:1,configured_weight:.1,effective_weight:.1,applicable_count:1,scored_count:1,uncertain_count:0},
  ];
  const scenario:DimensionScore[]=[
    {dimension:'required_skills',score:92,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
    {dimension:'capability_level',score:70,confidence:.8,configured_weight:.2,effective_weight:.2,applicable_count:1,scored_count:1,uncertain_count:0},
    {dimension:'hard_conditions',score:100,confidence:1,configured_weight:.1,effective_weight:.1,applicable_count:1,scored_count:1,uncertain_count:0},
  ];
  const gap:GapAnalysis={
    generation_status:'completed',
    prioritized_gaps:[],
    learning_path:[],
    counterfactual_suggestions:[],
    candidate_actions:[action],
    learning_routes:[{
      route_type:'budget_max_gain',
      action_ids:['learn-python'],
      total_cost_hours:12,
      baseline_score:68,
      modeled_final_score:81,
      modeled_score_delta:13,
      final_score:81,
      projected_match_gain:13,
      confidence_gain:null,
      target_reachable:true,
      final_recommendation:'potential_match',
      remaining_blocker_ids:[],
      path_refs:[],
      scenario_dimension_scores:scenario,
      algorithm_version:'learning-route-enumeration.v2',
    }],
    skill_path_decisions:[],
  };
  render(<WhatIfWorkbench evaluationId="evaluation-1" gap={gap} dimensionScores={baseline} actionName={candidate=>candidate.canonical_name||'Python'}/>);
  expect(screen.getByText('提升建议')).toBeInTheDocument();
  expect(screen.getByText(/围绕「Python」等学习行动/)).toBeInTheDocument();
  expect(screen.getByText(/重点补强岗位点名的核心技能/)).toBeInTheDocument();
});

test('what-if radar keeps baseline when route lacks scenario dimension scores',()=>{
  const action={
    action_id:'learn-python',
    action_type:'add_skill' as const,
    skill_id:'skill_python',
    canonical_name:'Python',
    target_requirement_ids:['required:skill_python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  };
  const baseline:DimensionScore[]=[
    {dimension:'required_skills',score:80,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
  ];
  const gap:GapAnalysis={
    generation_status:'completed',
    prioritized_gaps:[],
    learning_path:[],
    counterfactual_suggestions:[],
    candidate_actions:[action],
    learning_routes:[{
      route_type:'budget_max_gain',
      action_ids:['learn-python'],
      total_cost_hours:12,
      baseline_score:68,
      modeled_final_score:81,
      modeled_score_delta:13,
      final_score:81,
      projected_match_gain:13,
      confidence_gain:null,
      target_reachable:true,
      final_recommendation:'potential_match',
      remaining_blocker_ids:[],
      path_refs:[],
      algorithm_version:'learning-route-enumeration.v2',
    }],
    skill_path_decisions:[],
  };
  vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response(JSON.stringify({
    code:0,
    message:'success',
    trace_id:'trace-1',
    data:{
      generation_status:'completed',
      scenario_id:'scenario-1',
      baseline_evaluation:null,
      scenario_evaluation:null,
      projected_evaluation:null,
      algorithm_version:'counterfactual-profile.v2',
    },
  }),{status:200,headers:{'Content-Type':'application/json'}}))));
  render(<WhatIfWorkbench evaluationId="evaluation-1" gap={gap} dimensionScores={baseline}/>);
  expect(screen.queryByText('当前没有可评分的维度，暂不生成雷达图')).not.toBeInTheDocument();
  expect(screen.getByText('必备技能')).toBeInTheDocument();
});

test('what-if route falls back to projected what-if scores for legacy reports',async()=>{
  const action={
    action_id:'learn-python',
    action_type:'add_skill' as const,
    skill_id:'skill_python',
    canonical_name:'Python',
    target_requirement_ids:['required:skill_python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  };
  const baseline:DimensionScore[]=[
    {dimension:'required_skills',score:80,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
    {dimension:'capability_level',score:70,confidence:.8,configured_weight:.2,effective_weight:.2,applicable_count:1,scored_count:1,uncertain_count:0},
    {dimension:'hard_conditions',score:100,confidence:1,configured_weight:.1,effective_weight:.1,applicable_count:1,scored_count:1,uncertain_count:0},
  ];
  const gap:GapAnalysis={
    generation_status:'completed',
    prioritized_gaps:[],
    learning_path:[],
    counterfactual_suggestions:[],
    candidate_actions:[action],
    learning_routes:[{
      route_type:'budget_max_gain',
      action_ids:['learn-python'],
      total_cost_hours:12,
      baseline_score:68,
      modeled_final_score:81,
      modeled_score_delta:13,
      final_score:81,
      projected_match_gain:13,
      confidence_gain:null,
      target_reachable:true,
      final_recommendation:'potential_match',
      remaining_blocker_ids:[],
      path_refs:[],
      algorithm_version:'learning-route-enumeration.v2',
    }],
    skill_path_decisions:[],
  };
  vi.stubGlobal('fetch',vi.fn(()=>Promise.resolve(new Response(JSON.stringify({
    code:0,
    message:'success',
    trace_id:'trace-1',
    data:{
      generation_status:'completed',
      scenario_id:'scenario-1',
      baseline_evaluation:null,
      scenario_evaluation:null,
      projected_evaluation:{
        evaluation_id:'evaluation-1',
        cv_profile_id:null,
        position_profile_id:null,
        algorithm_version:'deterministic-matching.v5',
        evaluation_status:'completed',
        hard_constraint_results:[],
        skill_results:[],
        responsibility_results:[],
        project_results:[],
        scenario_results:[],
        summary:null,
        final_match_result:{
          overall_score:81,
          match_confidence:.9,
          recommendation_level:'potential_match',
          hard_gate_status:'passed',
          dimension_scores:[
            {dimension:'required_skills',score:92,confidence:.9,configured_weight:.4,effective_weight:.4,applicable_count:2,scored_count:2,uncertain_count:0},
            {dimension:'capability_level',score:70,confidence:.8,configured_weight:.2,effective_weight:.2,applicable_count:1,scored_count:1,uncertain_count:0},
            {dimension:'hard_conditions',score:100,confidence:1,configured_weight:.1,effective_weight:.1,applicable_count:1,scored_count:1,uncertain_count:0},
          ],
          explanation:'',
          algorithm_version:'explainable-scoring.v4',
          scoring_config_version:'scoring-config.v3',
          cv_profile_id:null,
          position_profile_id:null,
          input_evaluation_algorithm_version:'deterministic-matching.v5',
          source_evaluation_id:'evaluation-1',
          cv_taxonomy_version:'',
          cv_derivation_version:'',
          position_taxonomy_version:'',
          position_graph_version:'',
          position_quality_snapshot_id:'',
        },
      },
      actions:[action],
      baseline_score:68,
      modeled_final_score:81,
      modeled_score_delta:13,
      projected_if_completed:true,
      projected_actions:[action],
      projected_score:81,
      projected_score_delta:13,
      algorithm_version:'counterfactual-profile.v2',
    },
  }),{status:200,headers:{'Content-Type':'application/json'}}))));
  render(<WhatIfWorkbench evaluationId="evaluation-1" gap={gap} dimensionScores={baseline}/>);
  expect(await screen.findByText(/重点补强岗位点名的核心技能/)).toBeInTheDocument();
  expect(screen.queryByText('当前没有可评分的维度，暂不生成雷达图')).not.toBeInTheDocument();
});
