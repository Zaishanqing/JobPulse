import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {App} from 'antd';
import {beforeEach,expect,test,vi} from 'vitest';
import {MemoryRouter,useLocation} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {SystemNoticeHost} from '../../../shared/components/States';
import {EnterpriseRecruitment} from './EnterpriseRecruitment';

vi.mock('../components/CandidateDecisionBoard',()=>({CandidateDecisionBoard:({jobId}:{jobId:string})=><div data-testid="decision-board">{jobId}</div>}));
vi.mock('../../positions/api',()=>({listPublishedPositions:vi.fn().mockResolvedValue([])}));
vi.mock('../api',()=>({
  changeEnterpriseJobStatus:vi.fn(),
  createEnterprise:vi.fn(),
  createEnterpriseJob:vi.fn(),
  deleteEnterpriseJob:vi.fn(),
  decideCandidate:vi.fn(),
  getMyEnterprise:vi.fn(),
  getEnterpriseMatchTask:vi.fn(),
  getSkillWeights:vi.fn(),
  listCandidateSubmissions:vi.fn(),
  listEnterpriseJobs:vi.fn(),
  listEnterpriseMatchEvaluations:vi.fn(),
  listSkillCategories:vi.fn(),
  matchEnterpriseSubmissions:vi.fn(),
  saveSkillWeights:vi.fn(),
}));

import {createEnterpriseJob,deleteEnterpriseJob,getEnterpriseMatchTask,getMyEnterprise,getSkillWeights,listCandidateSubmissions,listEnterpriseJobs,listEnterpriseMatchEvaluations,listSkillCategories,matchEnterpriseSubmissions,saveSkillWeights} from '../api';

const mockProfile=vi.mocked(getMyEnterprise);
const mockCreateJob=vi.mocked(createEnterpriseJob);
const mockDeleteJob=vi.mocked(deleteEnterpriseJob);
const mockTask=vi.mocked(getEnterpriseMatchTask);
const mockJobs=vi.mocked(listEnterpriseJobs);
const mockWeights=vi.mocked(getSkillWeights);
const mockReports=vi.mocked(listEnterpriseMatchEvaluations);
const mockSubmissions=vi.mocked(listCandidateSubmissions);
const mockSkillCategories=vi.mocked(listSkillCategories);
const mockSaveWeights=vi.mocked(saveSkillWeights);
const mockMatch=vi.mocked(matchEnterpriseSubmissions);

const job={
  enterprise_job_id:'JOB_1',enterprise_id:'ENT_1',title:'韧性测试岗位',standard_position_id:null,jd_text:'测试岗位',headcount:1,
  location:'上海',employment_type:'full_time',salary_min:null,salary_max:null,salary_unit:'month' as const,status:'published',created_at:null,updated_at:null,
};
const submission={
  submission_id:'SUB_1',resume_id:'RES_1',resume_display_name:'候选人成功数据',enterprise_job_id:'JOB_1',enterprise_id:'ENT_1',status:'submitted',
  created_at:null,updated_at:null,parse_status:'completed',validated_cv_snapshot_id:'SNAPSHOT_1',skill_count:3,matchable:true,matchable_reason:'可匹配',
};
const secondSubmission={...submission,submission_id:'SUB_2',resume_id:'RES_2',resume_display_name:'候选人二'};
const evaluation=(overrides:Record<string,unknown>={})=>({evaluation_id:'EVAL_1',task_id:'TASK_1',resume_id:'RES_1',status:'succeeded',provider:'matching-service',created_at:null,updated_at:'2026-08-13T10:00:00Z',...overrides});
function LocationProbe(){
  const location=useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}
const renderPage=()=>render(<MemoryRouter><App><SystemNoticeHost/><EnterpriseRecruitment/><LocationProbe/></App></MemoryRouter>);
const openCandidates=()=>fireEvent.click(screen.getByText('候选评估'));
const selectCandidateAndRun=async()=>{
  openCandidates();
  fireEvent.click(await screen.findByRole('checkbox'));
  fireEvent.click(screen.getByRole('button',{name:/运行候选评估/}));
};

beforeEach(()=>{
  cleanup();
  vi.clearAllMocks();
  mockProfile.mockResolvedValue({enterprise_id:'ENT_1',owner_user_id:'USER_1',enterprise_name:'测试企业',industry:null,scale:null,location:null,description:null,status:'active',created_at:null,updated_at:null});
  mockJobs.mockResolvedValue([job]);
  mockWeights.mockResolvedValue([]);
  mockReports.mockResolvedValue([]);
  mockSubmissions.mockResolvedValue([]);
  mockSkillCategories.mockResolvedValue([{category:'AI',skills:[{skill_id:'skill_rag',skill_name:'RAG',category:'AI',description:null},{skill_id:'skill_python',skill_name:'Python',category:'AI',description:null}]}]);
  mockCreateJob.mockResolvedValue(job);
  mockSaveWeights.mockResolvedValue({enterprise_job_id:'JOB_1',updated_count:1,weights:[]});
  mockTask.mockResolvedValue({task_id:'TASK_1',status:'succeeded',evaluation_id:'EVAL_1'});
  mockDeleteJob.mockResolvedValue({enterprise_job_id:'JOB_1',deleted:true});
});

test('创建岗位抽屉支持按领域批量添加技能权重并随岗位一起保存',async()=>{
  renderPage();
  fireEvent.click(await screen.findByRole('button',{name:/创建招聘岗位/}));
  const drawer=await screen.findByRole('dialog');
  fireEvent.change(within(drawer).getByLabelText('岗位名称'),{target:{value:'新岗位'}});
  fireEvent.mouseDown(within(drawer).getByText('选择领域'));
  fireEvent.click((await screen.findAllByText('AI')).at(-1)!);
  expect(within(drawer).getByText('AI')).toBeInTheDocument();
  const boxes=within(drawer).getAllByRole('combobox');
  fireEvent.mouseDown(boxes.at(-1)!);
  fireEvent.click((await screen.findAllByText('RAG')).at(-1)!);
  fireEvent.click(await screen.findByRole('button',{name:/添加所选技能/}));
  expect(within(drawer).getByText('RAG')).toBeInTheDocument();
  expect(within(drawer).queryByText('skill_rag')).not.toBeInTheDocument();
  fireEvent.click(within(drawer).getByRole('button',{name:/创建岗位/}));
  await waitFor(()=>expect(mockCreateJob).toHaveBeenCalledWith(expect.objectContaining({
    enterprise_id:'ENT_1',
    salary_unit:'month',
  })));
  expect(mockSaveWeights).toHaveBeenCalledWith('JOB_1',[{
    skill_id:'skill_rag',
    weight:.5,
    is_required:true,
    is_bonus:false,
  }]);
});

test('确认后删除招聘记录并刷新岗位列表',async()=>{
  mockJobs.mockResolvedValueOnce([job]).mockResolvedValueOnce([]);
  renderPage();
  expect(await screen.findByRole('button',{name:/删除记录/})).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/删除记录/}));
  fireEvent.click(await screen.findByRole('button',{name:'确认删除'}));
  await waitFor(()=>expect(mockDeleteJob).toHaveBeenCalledWith('JOB_1'));
  expect(await screen.findByText('暂无招聘岗位')).toBeInTheDocument();
});

test('三个资源真实返回空数组时分别显示暂无数据',async()=>{
  renderPage();
  expect(await screen.findByText('企业技能权重')).toBeInTheDocument();
  const settings=screen.getByText('企业技能权重').closest<HTMLElement>('.enterprise-job-settings')!;
  expect(await within(settings).findByText('暂无数据')).toBeInTheDocument();
  openCandidates();
  const candidates=document.querySelector<HTMLElement>('.enterprise-candidate-view')!;
  expect(await within(candidates).findAllByText('暂无数据')).toHaveLength(2);
  expect(screen.getByText(/暂无候选人投递/)).toBeInTheDocument();
  expect(screen.getByText(/尚无候选人评估记录/)).toBeInTheDocument();
});

test('403 显示无权限并保留接口信息与 trace_id',async()=>{
  mockWeights.mockRejectedValue(new ApiError(403,'技能权重仅招聘管理员可见','trace-forbidden'));
  renderPage();
  expect(await screen.findByText('无权限')).toBeInTheDocument();
  expect(screen.getByText('技能权重仅招聘管理员可见')).toBeInTheDocument();
  expect(screen.getByText(/trace_id: trace-forbidden/)).toBeInTheDocument();
  const settings=screen.getByText('企业技能权重').closest<HTMLElement>('.enterprise-job-settings')!;
  expect(within(settings).queryByText('暂无数据')).not.toBeInTheDocument();
});

test('503 显示上游不可用而不是空状态',async()=>{
  mockReports.mockRejectedValue(new ApiError(503,'Matching 服务维护中','trace-upstream'));
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  expect(await screen.findByText('上游不可用')).toBeInTheDocument();
  expect(screen.getByText('Matching 服务维护中')).toBeInTheDocument();
  expect(screen.getByText(/trace_id: trace-upstream/)).toBeInTheDocument();
});

test('单区域失败不影响另外两个区域成功展示',async()=>{
  mockWeights.mockResolvedValue([{id:'W_1',enterprise_job_id:'JOB_1',skill_id:'skill_typescript',weight:.9,is_required:true,is_bonus:false}]);
  mockReports.mockRejectedValue(new ApiError(500,'评估记录读取失败','trace-report'));
  mockSubmissions.mockResolvedValue([submission]);
  renderPage();
  expect(await screen.findByText('skill_typescript')).toBeInTheDocument();
  openCandidates();
  expect(await screen.findByText('候选人成功数据')).toBeInTheDocument();
  expect(screen.getByText('加载失败')).toBeInTheDocument();
  expect(screen.getByText('评估记录读取失败')).toBeInTheDocument();
});

test('局部重试只重新请求失败区域并恢复展示',async()=>{
  mockSubmissions
    .mockRejectedValueOnce(new ApiError(503,'候选投递服务暂不可用','trace-retry'))
    .mockResolvedValueOnce([submission]);
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  await waitFor(()=>expect(screen.getByText('上游不可用')).toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:/重试/}));
  expect(await screen.findByText('候选人成功数据')).toBeInTheDocument();
  await waitFor(()=>expect(mockSubmissions).toHaveBeenCalledTimes(2));
  expect(mockWeights).toHaveBeenCalledTimes(1);
  expect(mockReports).toHaveBeenCalledTimes(1);
});

test('queued 和 running 只显示当前处理中状态，不显示为完成',async()=>{
  mockSubmissions.mockResolvedValue([submission,secondSubmission]);
  mockMatch.mockResolvedValue({enterprise_job_id:'JOB_1',implementation_status:'matching_service_async',items:[
    {submission_id:'SUB_1',resume_id:'RES_1',status:'created',task_id:'TASK_QUEUED',evaluation_id:'EVAL_QUEUED',error_code:null,error_message:null},
    {submission_id:'SUB_2',resume_id:'RES_2',status:'reconciling',task_id:'TASK_RUNNING',evaluation_id:'EVAL_RUNNING',error_code:null,error_message:null},
  ]});
  mockTask.mockImplementation(()=>new Promise(()=>undefined));
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  const checkboxes=await screen.findAllByRole('checkbox');
  fireEvent.click(checkboxes[0]);
  fireEvent.click(checkboxes[1]);
  fireEvent.click(screen.getByRole('button',{name:/运行候选评估/}));
  const taskPanel=await screen.findByLabelText('本次评估任务状态');
  expect(within(taskPanel).getByText('排队中')).toBeInTheDocument();
  expect(within(taskPanel).getByText('评估中')).toBeInTheDocument();
  expect(within(taskPanel).queryByText('已完成')).not.toBeInTheDocument();
});

test('task succeeded 后才加载并展示最新 evaluation',async()=>{
  mockSubmissions.mockResolvedValue([submission]);
  mockReports.mockResolvedValueOnce([]).mockResolvedValueOnce([evaluation({evaluation_id:'EVAL_LATEST'})]);
  mockMatch.mockResolvedValue({enterprise_job_id:'JOB_1',implementation_status:'matching_service_async',items:[
    {submission_id:'SUB_1',resume_id:'RES_1',status:'created',task_id:'TASK_1',evaluation_id:null,error_code:null,error_message:null},
  ]});
  mockTask.mockResolvedValue({task_id:'TASK_1',status:'succeeded',evaluation_id:'EVAL_LATEST'});
  renderPage();
  await screen.findByText('企业技能权重');
  await selectCandidateAndRun();
  expect((await screen.findAllByText('已完成')).length).toBeGreaterThan(0);
  expect(mockReports).toHaveBeenCalledTimes(2);
  expect(screen.queryByText('简历记录 · 评估记录')).not.toBeInTheDocument();
  expect(screen.queryByText('matching-service')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:/查看正式报告/})).toBeEnabled();
  expect(screen.getByRole('button',{name:/^check\s*适配$/})).toBeEnabled();
});

test('已有评估中记录会查询任务状态并刷新为已完成',async()=>{
  mockSubmissions.mockResolvedValue([submission]);
  mockReports
    .mockResolvedValueOnce([evaluation({evaluation_id:'EVAL_RUNNING',task_id:'TASK_RUNNING',status:'running'})])
    .mockResolvedValueOnce([evaluation({evaluation_id:'EVAL_RUNNING',task_id:'TASK_RUNNING',status:'succeeded'})]);
  mockTask.mockResolvedValue({task_id:'TASK_RUNNING',status:'succeeded',evaluation_id:'EVAL_RUNNING'});
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  expect(await screen.findByText('评估中')).toBeInTheDocument();
  await waitFor(()=>expect(mockTask).toHaveBeenCalledWith('TASK_RUNNING'),{timeout:3000});
  expect(await screen.findByText('已完成',undefined,{timeout:3000})).toBeInTheDocument();
  expect(mockReports).toHaveBeenCalledTimes(2);
});

test('候选池不把投递状态误显示为失败并隐藏重复技术文案和技能数量',async()=>{
  mockSubmissions.mockResolvedValue([submission]);
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  expect(await screen.findByText('候选人成功数据')).toBeInTheDocument();
  expect(screen.queryByText('3 项技能')).not.toBeInTheDocument();
  expect(screen.queryByText('失败')).not.toBeInTheDocument();
  expect(screen.queryByText('候选记录')).not.toBeInTheDocument();
  expect(screen.queryByText(/仅选择候选人/)).not.toBeInTheDocument();
  expect(screen.getAllByText('可匹配')).toHaveLength(1);
});

test('岗位设置、候选池与决策板切换时复用预加载的决策板',async()=>{
  renderPage();
  await screen.findByText('企业技能权重');
  const board=screen.getByTestId('decision-board');
  expect(board.closest('.enterprise-candidate-view')).toHaveAttribute('hidden');
  openCandidates();
  expect(screen.getByTestId('decision-board')).toBe(board);
  expect(board.parentElement).toHaveAttribute('hidden');
  fireEvent.click(screen.getByText('决策板'));
  expect(screen.getByTestId('decision-board')).toBe(board);
  expect(board.parentElement).not.toHaveAttribute('hidden');
});

test('企业端正式报告跳转到企业匹配报告路由',async()=>{
  mockSubmissions.mockResolvedValue([submission]);
  mockReports.mockResolvedValue([evaluation({evaluation_id:'EVAL_ENTERPRISE'})]);
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  fireEvent.click(await screen.findByRole('button',{name:/查看正式报告/}));
  expect(screen.getByTestId('location-probe')).toHaveTextContent('/enterprise/recruitment/reports/EVAL_ENTERPRISE');
});

test('最新 failed 不被同候选历史 succeeded 覆盖并关闭决策入口',async()=>{
  mockSubmissions.mockResolvedValue([submission]);
  mockReports.mockResolvedValue([
    evaluation({evaluation_id:'EVAL_FAILED',task_id:'TASK_FAILED',status:'failed',updated_at:'2026-08-13T11:00:00Z'}),
    evaluation({evaluation_id:'EVAL_OLD',task_id:'TASK_OLD',status:'succeeded',updated_at:'2026-08-13T09:00:00Z'}),
  ]);
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  expect(await screen.findByText('失败')).toBeInTheDocument();
  expect(screen.queryByText(/EVAL_OLD/)).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:/^check\s*适配$/})).toBeDisabled();
  expect(screen.getByRole('button',{name:/不适配/})).toBeDisabled();
});

test('批量提交部分候选成功、部分失败时分别保留真实状态与错误',async()=>{
  mockSubmissions.mockResolvedValue([submission,secondSubmission]);
  mockMatch.mockResolvedValue({enterprise_job_id:'JOB_1',implementation_status:'matching_service_async',items:[
    {submission_id:'SUB_1',resume_id:'RES_1',status:'created',task_id:'TASK_1',evaluation_id:'EVAL_1',error_code:null,error_message:null},
    {submission_id:'SUB_2',resume_id:'RES_2',status:'rejected',task_id:null,evaluation_id:null,error_code:'CV_PROFILE_NOT_FOUND',error_message:'resume has no validated CV snapshot'},
  ]});
  mockTask.mockResolvedValue({task_id:'TASK_1',status:'succeeded',evaluation_id:'EVAL_1'});
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  const checkboxes=await screen.findAllByRole('checkbox');
  fireEvent.click(checkboxes[0]);
  fireEvent.click(checkboxes[1]);
  fireEvent.click(screen.getByRole('button',{name:/运行候选评估/}));
  const taskPanel=await screen.findByLabelText('本次评估任务状态');
  expect(await within(taskPanel).findByText('已完成')).toBeInTheDocument();
  expect(within(taskPanel).getByText('失败')).toBeInTheDocument();
  expect(within(taskPanel).getByText('处理失败')).toBeInTheDocument();
  expect(within(taskPanel).getByText('系统处理失败，请稍后重试。')).toBeInTheDocument();
});

test('只有当前 latest succeeded evaluation 开放决策按钮',async()=>{
  mockSubmissions.mockResolvedValue([submission,secondSubmission]);
  mockReports.mockResolvedValue([
    evaluation({resume_id:'RES_1',evaluation_id:'EVAL_RUNNING',status:'running'}),
    evaluation({resume_id:'RES_2',evaluation_id:'EVAL_SUCCESS',task_id:'TASK_2',status:'succeeded'}),
  ]);
  renderPage();
  await screen.findByText('企业技能权重');
  openCandidates();
  await screen.findByText('评估中');
  const rows=[...document.querySelectorAll<HTMLElement>('.enterprise-candidate-list > .enterprise-report-row')];
  expect(within(rows[0]!).getByRole('button',{name:/^check\s*适配$/})).toBeDisabled();
  expect(within(rows[1]!).getByRole('button',{name:/^check\s*适配$/})).toBeEnabled();
});
