import {cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,test,vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {EmergingList} from './EmergingList';
import {resetEmergingCacheForTests} from '../cache';

const api=vi.hoisted(()=>(
  {listEmergingAssets:vi.fn(),listRecentPositionSignals:vi.fn()}
));
vi.mock('../api',()=>api);
vi.mock('../../auth/AuthContext',()=>({
  useAuth:()=>({user:{user_id:'user-1',username:'admin',role:'admin',permissions:[]}}),
}));

afterEach(()=>{cleanup();resetEmergingCacheForTests();vi.clearAllMocks()});

test('公开列表同时展示近期岗位信号与新兴岗位资产',async()=>{
  api.listEmergingAssets.mockResolvedValue([{
    emerging_id:'EM_1',cluster_id:'CL_1',position_name:'Agentic RAG 应用工程师',
    core_responsibilities:[],required_skills:[],bonus_skills:[],industry_scenarios:[],
    germination_score:null,score_dimensions:{},evidence_jd_ids:[],status:'discovered',support_jd_count:12,
  }]);
  api.listRecentPositionSignals.mockResolvedValue({
    signals:[
      {signal_id:'data-agent',position_name:'Data Agent 研发工程师',representative_title:'AI Agent 研发工程师(Data Agent)-【数据平台】',skills:['Multi-Agent','RAG'],observed_at:'2026-08-01',source_jd_ids:['JD_DATA'],source_count:1,projection_version:'recent-position-signals.v1'},
      {signal_id:'ai-coding',position_name:'AI Coding 算法工程师',representative_title:'AI Coding算法工程师/专家-Dev Infra',skills:['Coding Agent','LLM 评测','数据合成'],observed_at:'2026-08-07',source_jd_ids:['JD_CODING'],source_count:1,projection_version:'recent-position-signals.v1'},
      {signal_id:'agentic-ops',position_name:'Agentic Ops 研发工程师',representative_title:'大模型Agentic Ops研发工程师-基础技术',skills:['AIOps','可观测性'],observed_at:'2026-08-01',source_jd_ids:['JD_OPS'],source_count:1,projection_version:'recent-position-signals.v1'},
      {signal_id:'self-evolving-agent',position_name:'Agent 自进化算法工程师',representative_title:'Agent自进化算法工程师-AI Platform',skills:['Agent 评测','归因分析','自动优化'],observed_at:'2026-08-07',source_jd_ids:['JD_EVOLVE'],source_count:1,projection_version:'recent-position-signals.v1'},
      {signal_id:'ai-for-science-agent',position_name:'AI for Science Agent 平台工程师',representative_title:'Agent平台工程师-AI for Science',skills:['Auto Research','Agent Runtime','多 Agent 协作'],observed_at:'2026-08-07',source_jd_ids:['JD_SCIENCE'],source_count:1,projection_version:'recent-position-signals.v1'},
    ],
    observed_from:'2026-08-01',observed_to:'2026-08-07',source_contract:'published-jd-fact.v2',projection_version:'recent-position-signals.v1',
  });
  const firstRender=render(<MemoryRouter><EmergingList/></MemoryRouter>);

  expect(await screen.findByText('Data Agent 研发工程师')).toBeInTheDocument();
  expect(screen.getByText('AI Coding 算法工程师')).toBeInTheDocument();
  expect(screen.getByText('Agentic Ops 研发工程师')).toBeInTheDocument();
  expect(screen.getByText('Agent 自进化算法工程师')).toBeInTheDocument();
  expect(screen.getByText('AI for Science Agent 平台工程师')).toBeInTheDocument();
  expect(screen.getByText('Agentic RAG 应用工程师')).toBeInTheDocument();
  expect(screen.getByText('5 个近期方向')).toBeInTheDocument();
  expect(screen.getByText('1 个岗位')).toBeInTheDocument();
  expect(screen.getByText('12 份 JD')).toBeInTheDocument();
  expect(screen.queryByText(/2026\s*年\s*8\s*月/)).not.toBeInTheDocument();
  expect(screen.queryByText(/已完成稳定性评估/)).not.toBeInTheDocument();
  expect(screen.queryByText(/进入稳定候选后才会开放完整详情/)).not.toBeInTheDocument();
  expect(screen.queryByText(/非概率/)).not.toBeInTheDocument();
  expect(api.listRecentPositionSignals).toHaveBeenCalledTimes(1);

  const publishedHeading=screen.getByRole('heading',{name:'新兴岗位发现结果'});
  const recentHeading=screen.getByRole('heading',{name:'近期岗位信号'});
  expect(publishedHeading.closest('.emerging-list-section-head')).toBeInTheDocument();
  expect(recentHeading.closest('.emerging-list-section-head')).toBeInTheDocument();
  expect(Boolean(
    publishedHeading.compareDocumentPosition(recentHeading)&Node.DOCUMENT_POSITION_FOLLOWING,
  )).toBe(true);

  firstRender.unmount();
  render(<MemoryRouter><EmergingList/></MemoryRouter>);
  expect(screen.getByText('Data Agent 研发工程师')).toBeInTheDocument();
  expect(api.listEmergingAssets).toHaveBeenCalledTimes(1);
  expect(api.listRecentPositionSignals).toHaveBeenCalledTimes(1);
});
