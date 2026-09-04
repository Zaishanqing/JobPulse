import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {MemoryRouter,Route,Routes} from 'react-router-dom';
import {EmergingDetail} from './EmergingDetail';

const api=vi.hoisted(()=>({
  getPublishedEmerging:vi.fn(),
}));
vi.mock('../api',()=>({...api,getEmergingDisplay:api.getPublishedEmerging}));
vi.mock('../../auth/AuthContext',()=>({useAuth:()=>({can:()=>false,user:null})}));

afterEach(()=>{cleanup();vi.clearAllMocks()});

const scoreComponents=[
  {name:'growth',normalized_value:.7,weight:.18,contribution:.126,business_meaning:'growth'},
  {name:'cross_window_persistence',normalized_value:.8,weight:.16,contribution:.128,business_meaning:'persistence'},
  {name:'enterprise_coverage',normalized_value:.6,weight:.12,contribution:.072,business_meaning:'coverage'},
  {name:'source_diversity',normalized_value:.6,weight:.12,contribution:.072,business_meaning:'diversity'},
  {name:'standard_position_distance',normalized_value:.5,weight:.18,contribution:.09,business_meaning:'distance'},
  {name:'evidence_quality',normalized_value:.8,weight:.12,contribution:.096,business_meaning:'quality'},
  {name:'result_stability',normalized_value:.8,weight:.12,contribution:.096,business_meaning:'stability'},
];
const diagnosticFeatures={
  standard_position_distance:{scored:false,method:'nearest_standard_position_skill_jaccard_v2',maximum_skill_similarity:.6},
  skill_novelty_diagnostic:{scored:false,value:.5},
  legacy_dimension_inputs:{scored:false,skill_combo_novelty:.5,distance_from_existing_positions:.5},
  penalty_diagnostics:{scored:false,single_platform_noise_penalty:0},
};
const detail={
  emerging_id:'EM_TEST',cluster_id:'CL_1',position_name:'测试新兴岗位',core_responsibilities:['设计连接企业知识库与业务工具的 Agentic RAG 工作流','负责第 17 类业务场景的智能体评测、追踪与上线'],required_skills:[{skill_name:'Python',support_jd_count:4,support_source_count:4,support_enterprise_count:4,evidence:[{source_jd_id:'JD_SOURCE_1',original_text_snippet:'熟练掌握 Python，能够开发智能体工作流',data_source:'公开招聘样本',window_id:'2026-08-01'}]}],bonus_skills:[{raw_skill:'向量数据库',support_jd_count:4,support_source_count:4,support_enterprise_count:4}],industry_scenarios:['人工智能','智能客服'],germination_score:.68,score_dimensions:{skill_combo_novelty:.5,distance_from_existing_positions:.5},evidence_jd_ids:['demo-recent-agent-v6-jd-001'],field_evidence:{position_summary:{content:'设计连接企业知识库与业务工具的 Agentic RAG 工作流'},core_responsibilities:{items:[{content:'开发智能体',evidence:[{source_jd_id:'JD_SOURCE_1',original_text_snippet:'负责企业知识库接入、智能体编排、评测与持续优化'}]}]},required_skills:{items:[{content:'Python',evidence:[{source_jd_id:'JD_SOURCE_1',original_text_snippet:'熟练掌握 Python，能够开发智能体工作流'}]}]}},published_snapshot:{definition:{field_evidence:{position_summary:{content:'设计连接企业知识库与业务工具的 Agentic RAG 工作流'}}}},status:'published',germination_assessment:{
    formula_version:'emerging-score.v4',
    score_components:scoreComponents,
    diagnostic_features:diagnosticFeatures,
    evidence_package:{emergence_index:{dimensions:scoreComponents}},
    decision_reason:'ranking index satisfied',
  },
};

const renderDetail=()=>render(
  <MemoryRouter initialEntries={['/emerging/EM_TEST']}>
    <Routes><Route path="/emerging/:emergingId" element={<EmergingDetail/>}/></Routes>
  </MemoryRouter>
);

test('详情保留岗位定义并移除诊断特征和旧评分分解',async()=>{
  api.getPublishedEmerging.mockResolvedValue(detail);
  renderDetail();

  expect(await screen.findByText('岗位定义')).toBeInTheDocument();
  expect(screen.queryByText(/非概率/)).not.toBeInTheDocument();
  expect(screen.queryByText('基于公开招聘样本，企业名称已按业务类型匿名展示。')).not.toBeInTheDocument();
  expect(screen.queryByText('版本化可复现实验样本')).not.toBeInTheDocument();
  expect(screen.queryByText('发布状态')).not.toBeInTheDocument();
  expect(screen.queryByText('评估结论')).not.toBeInTheDocument();
  expect(screen.queryByText('证据规模')).not.toBeInTheDocument();
  const responsibilityColumn=screen.getByRole('heading',{name:'核心职责'}).closest('section') as HTMLElement;
  expect(within(responsibilityColumn).getByText('建立智能体效果评测与运行监控机制，推动应用稳定上线并持续优化')).toBeInTheDocument();
  expect(within(responsibilityColumn).queryByText('设计连接企业知识库与业务工具的 Agentic RAG 工作流')).not.toBeInTheDocument();
  expect(screen.getAllByText('设计连接企业知识库与业务工具的 Agentic RAG 工作流')).toHaveLength(1);
  expect(within(responsibilityColumn).queryByText(/第\s*17\s*类业务场景/)).not.toBeInTheDocument();
  const definitionColumn=screen.getByRole('heading',{name:'岗位定义'}).closest('section') as HTMLElement;
  expect(within(definitionColumn).getByText('岗位名称')).toBeInTheDocument();
  expect(within(definitionColumn).getByText('测试新兴岗位')).toBeInTheDocument();
  const skillRequirementCard=screen.getByText('技能要求',{selector:'.ant-card-head-title'}).closest('.profile') as HTMLElement;
  expect(within(skillRequirementCard).getByText('向量数据库')).toBeInTheDocument();
  expect(within(skillRequirementCard).getAllByText('4 份 JD').length).toBeGreaterThanOrEqual(2);
  fireEvent.click(within(skillRequirementCard).getByRole('button',{name:'查看证据上下文'}));
  expect(screen.getByText('熟练掌握 Python，能够开发智能体工作流')).toBeInTheDocument();
  expect(screen.getByText('公开招聘样本')).toBeInTheDocument();
  expect(document.querySelector('.evidence-drawer mark')).toHaveTextContent('熟练掌握 Python，能够开发智能体工作流');
  expect(screen.getByText('负责企业知识库接入、智能体编排、评测与持续优化')).toBeInTheDocument();
  expect(within(skillRequirementCard).getByText('加分技能',{selector:'.skill-requirement-tag-bonus'})).toBeInTheDocument();
  expect(screen.queryByText('加分技能',{selector:'.ant-card-head-title'})).not.toBeInTheDocument();
  const scenarioColumn=screen.getByRole('heading',{name:'典型行业应用场景'}).closest('section') as HTMLElement;
  expect(within(scenarioColumn).getByText('人工智能')).toBeInTheDocument();
  expect(within(scenarioColumn).getByText('智能客服')).toBeInTheDocument();
});

test('详情展示技能证据计数、企业覆盖和生命周期评估',async()=>{
  api.getPublishedEmerging.mockResolvedValue({
    ...detail,
    field_evidence:{
      required_skills:{
        content:['Python'],
        items:[{content:'Python',support_jd_count:4,support_source_count:4,support_enterprise_count:4,confidence:1}],
      },
      representative_enterprises:{content:{'演示企业·17':1,'演示企业·18':1,'演示企业·19':1,'演示企业·20':1}},
      candidate_lifecycle:{
        status:'stable_emerging_role',
        observed_window_ids:['2026-07-27..2026-07-29@recent-jd-202608','2026-07-30..2026-08-01@recent-jd-202608','2026-08-02..2026-08-04@recent-jd-202608','2026-08-05..2026-08-07@recent-jd-202608','2026-08-08..2026-08-08@recent-jd-202608'],
      },
    },
    germination_assessment:{
      germination_score:.81,
      formula_version:'emerging-score.v4',
      evidence_summary:{
        score_components:scoreComponents,
        diagnostic_features:{...diagnosticFeatures,standard_position_comparison:{nearest_standard_position:'LLM_ALGORITHM_ENGINEER',comprehensive_similarity:.166667,new_skills:['agent','rag'],shared_skills:['python']}},
        emergence_index:{dimensions:Object.fromEntries(scoreComponents.map(item=>[item.name,item]))},
      },
      qualification_basis:'candidate_lifecycle',
      decision_reason:'candidate lifecycle reached stable_emerging_role across 5 JD publish-date windows',
    },
  });
  renderDetail();

  expect(await screen.findByText('岗位定义')).toBeInTheDocument();
  expect(screen.queryByText('81%')).not.toBeInTheDocument();
  expect(screen.queryByText('时间窗口')).not.toBeInTheDocument();
  expect(screen.queryByText('评估模型')).not.toBeInTheDocument();
  expect(screen.queryByText(/已在 5 个独立时间窗口持续出现/)).not.toBeInTheDocument();
  const skillCard=screen.getByText('技能要求',{selector:'.ant-card-head-title'}).closest('.profile') as HTMLElement;
  expect(within(skillCard).getAllByText('4 份 JD').length).toBeGreaterThanOrEqual(2);
  expect(within(skillCard).getAllByText('4 个来源').length).toBeGreaterThanOrEqual(2);
  expect(within(skillCard).getAllByText('4 家企业').length).toBeGreaterThanOrEqual(2);
  expect(within(skillCard).getByText('必备技能',{selector:'.ant-tag-success'})).toBeInTheDocument();
  expect(within(skillCard).getByText('加分技能',{selector:'.skill-requirement-tag-bonus'})).toBeInTheDocument();
  expect(within(skillCard).queryByText('未提供')).not.toBeInTheDocument();
  const enterpriseColumn=screen.getByRole('heading',{name:'企业覆盖'}).closest('section') as HTMLElement;
  expect(within(enterpriseColumn).getByText('共覆盖 4 家代表企业')).toBeInTheDocument();
  expect(within(enterpriseColumn).getByText('企业知识管理服务商 · 1 份 JD')).toBeInTheDocument();
  expect(within(enterpriseColumn).getByText('智能客服解决方案商 · 1 份 JD')).toBeInTheDocument();
  expect(within(enterpriseColumn).queryByText(/演示企业/)).not.toBeInTheDocument();
  expect(screen.queryByText('诊断特征')).not.toBeInTheDocument();
  expect(screen.getByText('达到稳定新兴岗位标准')).toBeInTheDocument();
});

test('诊断特征不再展示',async()=>{
  api.getPublishedEmerging.mockResolvedValue(detail);
  renderDetail();

  expect(await screen.findByText('岗位定义')).toBeInTheDocument();
  expect(screen.queryByText('诊断特征')).not.toBeInTheDocument();
  expect(screen.queryByText('与最近标准岗位相似度')).not.toBeInTheDocument();
});

test('岗位概述使用发布定义快照而不是候选阶段旧值',async()=>{
  api.getPublishedEmerging.mockResolvedValue({
    ...detail,
    field_evidence:{position_summary:{content:'候选阶段旧概述'}},
    published_snapshot:{definition:{field_evidence:{position_summary:{content:'生成并发布的岗位概述'}}}},
  });
  renderDetail();

  expect(await screen.findByText('生成并发布的岗位概述')).toBeInTheDocument();
  expect(screen.queryByText('候选阶段旧概述')).not.toBeInTheDocument();
});

test('旧版 score_dimensions 不再作为评分项或原始 JSON 暴露',async()=>{
  const legacyDetail={
    ...detail,
    germination_assessment:{
      formula_version:'emerging-score.v2',
      dimensions:{skill_combo_novelty:.5,distance_from_existing_positions:.5,cluster_growth_rate:.7},
    },
  };
  api.getPublishedEmerging.mockResolvedValue(legacyDetail);
  renderDetail();

  expect(await screen.findByText('岗位定义')).toBeInTheDocument();

  expect(screen.queryByText('诊断特征')).not.toBeInTheDocument();
  expect(screen.queryByText(/skill_combo_novelty/)).not.toBeInTheDocument();
});

test('技能要求每页 10 条，并兼容企业名称数组',async()=>{
  api.getPublishedEmerging.mockResolvedValue({
    ...detail,
    required_skills:Array.from({length:11},(_,index)=>({
      skill_name:`技能 ${index+1}`,
      support_jd_count:1,
      support_source_count:1,
      evidence:[{original_text_snippet:`技能 ${index+1} 的招聘证据`,data_source:'公开招聘样本',window_id:'2026-08-01'}],
    })),
    bonus_skills:[],
    field_evidence:{
      ...detail.field_evidence,
      representative_enterprises:{content:['京东','美团']},
    },
  });
  renderDetail();

  const skillCard=(await screen.findByText('技能要求',{selector:'.ant-card-head-title'})).closest('.profile') as HTMLElement;
  expect(within(skillCard).getByText('技能 10')).toBeInTheDocument();
  expect(within(skillCard).queryByText('技能 11')).not.toBeInTheDocument();
  expect(within(skillCard).getByTitle('2')).toBeInTheDocument();
  const enterpriseColumn=screen.getByRole('heading',{name:'企业覆盖'}).closest('section') as HTMLElement;
  expect(within(enterpriseColumn).getByText('共覆盖 2 家代表企业')).toBeInTheDocument();
  expect(within(enterpriseColumn).getByText('京东')).toBeInTheDocument();
  expect(within(enterpriseColumn).getByText('美团')).toBeInTheDocument();
});
