import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {App} from 'antd';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach,expect,test,vi} from 'vitest';
import {SystemNoticeHost} from '../../../shared/components/States';
import {CandidateDecisionBoard} from './CandidateDecisionBoard';
import type {CandidateBoardItem} from '../types';

vi.mock('../api',()=>({
  getCandidateDecisionBoard:vi.fn(),
  decideCandidate:vi.fn(),
}));

import {decideCandidate,getCandidateDecisionBoard} from '../api';

const mockGet=vi.mocked(getCandidateDecisionBoard);
const mockDecide=vi.mocked(decideCandidate);

const item=(overrides:Partial<CandidateBoardItem>={}):CandidateBoardItem=>({
  submission_id:'sub-1',
  resume_id:'resume-1',
  candidate_display_name:'张三',
  candidate_status:'submitted',
  evaluation_id:'eval-1',
  evaluation_status:'succeeded',
  task_id:'task-1',
  error_code:null,
  error_message:null,
  overall_score:86.4,
  match_confidence:0.9,
  recommendation_level:'strong_match',
  stale:false,
  required_coverage:{matched:8,total:9,coverage:0.8889},
  critical_gap_count:1,
  critical_gaps:['skill_docker（required_skill_missing）'],
  evidence:{count:2,samples:['5 years Python']},
  strengths:[{dimension:'required_skills',message:'Python 经验与岗位必备技能完全匹配',evidence_count:1}],
  risks:[{kind:'missing_required',message:'Docker（missing）',evidence_count:0}],
  rank:1,
  decision:null,
  evaluation_delta:null,
  ...overrides,
});

const renderBoard=()=>render(
  <App><SystemNoticeHost/><MemoryRouter><CandidateDecisionBoard jobId="job-1"/></MemoryRouter></App>
);

beforeEach(()=>{
  cleanup();
  vi.clearAllMocks();
  mockGet.mockResolvedValue({enterprise_job_id:'job-1',total:1,ranked_count:1,items:[item()]});
  mockDecide.mockResolvedValue({decision_id:'decision-1'});
});

test('决策板渲染排名分数必备覆盖关键缺口与证据',async()=>{
  renderBoard();
  expect(await screen.findByText('张三')).toBeInTheDocument();
  expect(screen.getByText('1')).toBeInTheDocument(); // rank
  expect(screen.getByText('86.4')).toBeInTheDocument();
  expect(screen.getByText('8/9 · 89%')).toBeInTheDocument();
  expect(screen.getByText('1 项')).toBeInTheDocument();
  expect(screen.queryByRole('columnheader',{name:'证据'})).not.toBeInTheDocument();
  expect(screen.queryByText('5 years Python')).not.toBeInTheDocument();
  expect(screen.queryByText(/required_skill_missing/)).not.toBeInTheDocument();
  expect(mockGet).toHaveBeenCalledWith('job-1');
});

test('空候选池显示暂无候选投递',async()=>{
  mockGet.mockResolvedValue({enterprise_job_id:'job-1',total:0,ranked_count:0,items:[]});
  renderBoard();
  expect(await screen.findByText('暂无候选投递')).toBeInTheDocument();
});

test('有候选但未匹配显示尚未运行正式匹配',async()=>{
  mockGet.mockResolvedValue({enterprise_job_id:'job-1',total:1,ranked_count:0,items:[item({evaluation_status:'never_matched',evaluation_id:null,overall_score:null,rank:null})]});
  renderBoard();
  expect(await screen.findByText('尚未运行正式匹配')).toBeInTheDocument();
  expect(screen.getAllByText('尚未匹配').length).toBeGreaterThan(0);
  expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
});

test('pending running failed 状态明确展示且不显示正式分数',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:3,ranked_count:0,
    items:[
      item({resume_id:'r-pending',candidate_display_name:'排队候选人',evaluation_status:'pending',evaluation_id:'e-pending',overall_score:null,rank:null}),
      item({resume_id:'r-running',candidate_display_name:'评估中候选人',evaluation_status:'running',evaluation_id:'e-running',overall_score:null,rank:null}),
      item({resume_id:'r-failed',candidate_display_name:'失败候选人',evaluation_status:'failed',evaluation_id:'e-failed',error_code:'MATCHING_TIMEOUT',error_message:'匹配服务响应超时，请稍后重试。',overall_score:null,rank:null}),
    ],
  });
  renderBoard();
  expect(await screen.findByText('排队中')).toBeInTheDocument();
  expect(screen.getByText('匹配中')).toBeInTheDocument();
  expect(screen.getByText('匹配失败')).toBeInTheDocument();
  expect(screen.getByText('处理失败')).toBeInTheDocument();
  expect(screen.queryByText('MATCHING_TIMEOUT')).not.toBeInTheDocument();
  expect(screen.getByText('匹配服务响应超时，请稍后重试。')).toBeInTheDocument();
  expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(3);
  expect(screen.queryByText('86.4')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'适配'})).not.toBeInTheDocument();
});

test('失败详情展示稳定错误码与真实错误信息',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:1,ranked_count:0,
    items:[item({evaluation_status:'failed',error_code:'REMOTE_REJECTED',error_message:'Upstream rejected incompatible payload',overall_score:null,rank:null})],
  });
  renderBoard();
  fireEvent.click(await screen.findByRole('button',{name:/查看/}));
  expect((await screen.findAllByText('处理失败')).length).toBeGreaterThan(0);
  expect(screen.queryByText('REMOTE_REJECTED')).not.toBeInTheDocument();
  expect(screen.queryByText('Upstream rejected incompatible payload')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'适配'})).not.toBeInTheDocument();
});

test('stale 与 needs_rematch 明确展示且禁止决策和比较',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:2,ranked_count:0,
    items:[
      item({
        resume_id:'r-stale',candidate_display_name:'过期候选人',evaluation_status:'stale',stale:true,overall_score:96,rank:1,
        decision:{decision_id:'old-decision',decision:'fit',decided_by:'reviewer',evaluation_id:'eval-1',task_id:'task-1',algorithm_version:'v1',reason_code:'requirements_met',reason_text:'旧依据',created_at:null,updated_at:null},
      }),
      item({resume_id:'r-rematch',candidate_display_name:'待重匹配候选人',evaluation_status:'needs_rematch',stale:false,overall_score:94,rank:2}),
    ],
  });
  renderBoard();
  expect(await screen.findByText('评估已过期')).toBeInTheDocument();
  expect(screen.getByText('需要重新匹配')).toBeInTheDocument();
  expect(screen.getAllByText('不参与排名')).toHaveLength(2);
  expect(screen.queryByText('96.0')).not.toBeInTheDocument();
  expect(screen.queryByText('94.0')).not.toBeInTheDocument();
  expect(screen.queryByText('已适配')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'适配'})).not.toBeInTheDocument();
  const checkboxes=screen.getAllByRole('checkbox');
  expect(checkboxes[1]).toBeDisabled();
  expect(checkboxes[2]).toBeDisabled();
});

test('候选抽屉展示匹配摘要优势与风险并复用完整报告链接',async()=>{
  renderBoard();
  fireEvent.click(await screen.findByRole('button',{name:/查看/}));
  expect(await screen.findByText('候选详情 · 张三')).toBeInTheDocument();
  expect(screen.getByText('综合得分')).toBeInTheDocument();
  expect(screen.getByText('Python 经验与岗位必备技能完全匹配')).toBeInTheDocument();
  expect(screen.getByText(/缺少必备技能/)).toBeInTheDocument();
  const reportLink=screen.getByRole('link',{name:/查看完整匹配报告/});
  expect(reportLink).toHaveAttribute('href','/enterprise/recruitment/reports/eval-1');
});

test('决策板直接标记适配并刷新',async()=>{
  renderBoard();
  fireEvent.click(await screen.findByRole('button',{name:'适配'}));
  expect(await screen.findByText('记录适配决策依据')).toBeInTheDocument();
  fireEvent.mouseDown(screen.getByLabelText('决策原因'));
  fireEvent.click(await screen.findByText('核心要求满足'));
  fireEvent.change(screen.getByLabelText('决策说明'),{target:{value:'核心经验满足岗位要求'}});
  fireEvent.click(screen.getByRole('button',{name:'确认决策'}));
  await waitFor(()=>expect(mockDecide).toHaveBeenCalledWith('job-1','resume-1','eval-1','fit','requirements_met','核心经验满足岗位要求'));
  await waitFor(()=>expect(mockGet).toHaveBeenCalledTimes(2));
});

test('决策板直接标记不适配并刷新',async()=>{
  renderBoard();
  fireEvent.click(await screen.findByRole('button',{name:'不适配'}));
  fireEvent.click(await screen.findByRole('button',{name:'确认决策'}));
  await waitFor(()=>expect(mockDecide).toHaveBeenCalledWith('job-1','resume-1','eval-1','unfit',undefined,undefined));
  await waitFor(()=>expect(mockGet).toHaveBeenCalledTimes(2));
});

test('候选抽屉回读 rationale 与正式 Evaluation delta',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:1,ranked_count:1,
    items:[item({
      decision:{decision_id:'decision-1',decision:'fit',decided_by:'reviewer-1',evaluation_id:'eval-1',task_id:'task-1',algorithm_version:'v2',reason_code:'requirements_met',reason_text:'核心经验满足岗位要求',created_at:'2026-08-12T10:00:00Z',updated_at:'2026-08-12T10:00:00Z'},
      evaluation_delta:{
        current:{evaluation_id:'eval-1',task_id:'task-1',algorithm_version:'v2',evaluated_at:'2026-08-12T09:00:00Z',overall_score:86.4,required_coverage:{matched:8,total:9,coverage:0.8889},critical_gap_count:1,critical_gaps:['Docker'],stale_reason_codes:[]},
        previous:{evaluation_id:'eval-0',task_id:'task-0',algorithm_version:'v1',evaluated_at:'2026-08-11T09:00:00Z',overall_score:80,required_coverage:{matched:7,total:9,coverage:0.7778},critical_gap_count:2,critical_gaps:['Docker','Kubernetes'],stale_reason_codes:[]},
        overall_score_delta:6.4,required_coverage_delta:0.1111,critical_gap_count_delta:-1,stale_reasons_changed:false,
      },
    })],
  });
  renderBoard();
  fireEvent.click(await screen.findByRole('button',{name:/查看/}));
  expect(await screen.findByText('核心经验满足岗位要求')).toBeInTheDocument();
  expect(screen.getByText('评估变化')).toBeInTheDocument();
  expect(screen.getByText('上一版评估 → 当前评估')).toBeInTheDocument();
  expect(screen.getByText(/80.0.*86.4.*\+6.4/)).toBeInTheDocument();
});

test('决策板 API 错误展示失败状态',async()=>{
  mockGet.mockRejectedValue({status:500,message:'匹配服务不可用'});
  renderBoard();
  expect(await screen.findByText('匹配服务不可用')).toBeInTheDocument();
});

test('横向比较候选展示指标矩阵与优势风险',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:2,ranked_count:2,
    items:[
      item({submission_id:'sub-a',resume_id:'resume-a',candidate_display_name:'候选人A',overall_score:91,required_coverage:{matched:9,total:9,coverage:1},critical_gap_count:0,rank:1}),
      item({submission_id:'sub-b',resume_id:'resume-b',candidate_display_name:'候选人B',overall_score:84,required_coverage:{matched:7,total:9,coverage:0.7778},critical_gap_count:2,rank:2}),
    ],
  });
  renderBoard();
  await screen.findByText('候选人A');
  const checkboxes=screen.getAllByRole('checkbox');
  fireEvent.click(checkboxes[1]);
  fireEvent.click(checkboxes[2]);
  fireEvent.click(screen.getByRole('button',{name:/比较候选/}));
  expect(await screen.findByText('候选横向比较')).toBeInTheDocument();
  expect(screen.getAllByText('必备技能覆盖').length).toBeGreaterThan(0);
  expect(screen.getAllByText('9/9 · 100%').length).toBeGreaterThan(0);
  expect(screen.getAllByText('关键缺口').length).toBeGreaterThan(0);
  expect(screen.getAllByText('0 项').length).toBeGreaterThan(0);
  expect(screen.getAllByText('2 项').length).toBeGreaterThan(0);
  expect(screen.getAllByText('证据').length).toBeGreaterThan(0);
  expect(screen.getAllByText('缺少必备：').length).toBeGreaterThan(0);
});

test('已撤销投递不可决策且不参与排名',async()=>{
  mockGet.mockResolvedValue({
    enterprise_job_id:'job-1',total:2,ranked_count:1,
    items:[
      item({resume_id:'resume-active',candidate_display_name:'正常候选',rank:1}),
      item({resume_id:'resume-revoked',submission_id:'sub-revoked',candidate_display_name:'已撤销候选',candidate_status:'revoked',evaluation_status:'revoked',rank:null}),
    ],
  });
  renderBoard();
  await screen.findByText('正常候选');
  expect(screen.getByText('投递已撤销')).toBeInTheDocument();
  expect(screen.getByText('不可决策')).toBeInTheDocument();
  expect(screen.getAllByText('—')[0]).toBeInTheDocument(); // revoked rank
});
