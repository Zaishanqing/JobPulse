import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {App} from 'antd';
import {afterEach,expect,test,vi} from 'vitest';
import {MemoryRouter,Route,Routes,useLocation} from 'react-router-dom';
import {PublishedJobBrowser} from './PublishedJobBrowser';
import {PublishedJobDetail} from './PublishedJobDetail';
import {EnterpriseRecruitment} from './EnterpriseRecruitment';
import {buildMenuItems} from '../../../app/adminNavConfig';
import {SystemNoticeHost} from '../../../shared/components/States';

const api=vi.hoisted(()=>({
  listPublishedEnterpriseJobs:vi.fn(),
  getPublishedEnterpriseJob:vi.fn(),
  listCandidateSubmissionOptions:vi.fn(),
  submitCandidate:vi.fn(),
  revokeCandidateSubmission:vi.fn(),
  getMyEnterprise:vi.fn(),
  listEnterpriseJobs:vi.fn(),
  listSkillCategories:vi.fn(),
  createEnterprise:vi.fn(),
  createEnterpriseJob:vi.fn(),
  changeEnterpriseJobStatus:vi.fn(),
  getSkillWeights:vi.fn(),
  saveSkillWeights:vi.fn(),
  listEnterpriseMatchEvaluations:vi.fn(),
  listCandidateSubmissions:vi.fn(),
  matchEnterpriseSubmissions:vi.fn(),
  decideCandidate:vi.fn(),
  getCandidateDecisionBoard:vi.fn(),
  getRecruiterDecisionAudit:vi.fn(),
  replayRecruiterDecisionAuditCase:vi.fn(),
}));
const matchingApi=vi.hoisted(()=>({
  getMatchPreflight:vi.fn().mockResolvedValue({ready:true,cv_snapshot_ready:true,cv_profile_ready:true,position_profile_ready:true,blockers:[],validated_cv_snapshot_id:'snapshot-1',position_graph_version:'graph-v1'}),
  createEnterpriseJobMatchTask:vi.fn(),
  getMatchTask:vi.fn(),
}));
vi.mock('../api',()=>api);
vi.mock('../../matching/api',()=>matchingApi);
vi.mock('../../positions/api',()=>({listPublishedPositions:vi.fn().mockResolvedValue([])}));
vi.mock('../components/CandidateDecisionBoard',()=>({CandidateDecisionBoard:()=>null}));

afterEach(()=>{cleanup();vi.clearAllMocks()});
const detail=()=>render(<MemoryRouter initialEntries={['/jobs/job-published']}><App><SystemNoticeHost/><Routes><Route path="/jobs/:jobId" element={<PublishedJobDetail/>}/></Routes></App></MemoryRouter>);
function LocationProbe(){
  const location=useLocation();
  return <span data-testid="location-probe">{location.pathname}</span>;
}

test('企业岗位入口只出现在 personal_user 导航',()=>{
  const hasNavKey=(items:ReturnType<typeof buildMenuItems>,key:string)=>items.some(item=>item!==null&&item!==undefined&&typeof item==='object'&&'key' in item&&(item.key===key||('children' in item&&Array.isArray(item.children)&&item.children.some(child=>child!==null&&child!==undefined&&typeof child==='object'&&'key' in child&&child.key===key))));
  const personal=buildMenuItems(()=>false,'personal_user');
  const enterprise=buildMenuItems(()=>false,'enterprise_user');
  expect(hasNavKey(personal,'/jobs')).toBe(true);
  expect(hasNavKey(enterprise,'/jobs')).toBe(false);
});

test('personal_user 页面只展示公开 API 返回的 published 岗位',async()=>{
  api.listPublishedEnterpriseJobs.mockResolvedValue([{
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'RAG 应用工程师',jd_text:'负责检索增强生成应用开发。',headcount:2,
    location:'武汉',employment_type:'full_time',salary_min:15000,salary_max:25000,salary_unit:'month',status:'published',
  }]);
  render(<MemoryRouter><PublishedJobBrowser/></MemoryRouter>);

  expect(await screen.findByRole('heading',{name:'RAG 应用工程师'})).toBeInTheDocument();
  expect(screen.getByText('已发布 · 招聘中')).toBeInTheDocument();
  expect(screen.queryByText('草稿')).not.toBeInTheDocument();
  expect(screen.getByText('投递前置条件')).toBeInTheDocument();
  expect(screen.getByRole('link',{name:/查看详情/})).toHaveAttribute('href','/jobs/job-published');
});

test('岗位详情显示 published 状态和投递前置条件',async()=>{
  api.getPublishedEnterpriseJob.mockResolvedValue({
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'RAG 应用工程师',jd_text:'负责检索增强生成应用开发。',headcount:2,
    location:'武汉',employment_type:'full_time',salary_min:15000,salary_max:25000,salary_unit:'month',status:'published',
  });
  api.listCandidateSubmissionOptions.mockResolvedValue([]);
  detail();

  expect(await screen.findByText('已发布 · 招聘中')).toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'投递此岗位'})).toBeInTheDocument();
  expect(screen.getByText(/已生成验证快照/)).toBeInTheDocument();
  expect(screen.getAllByText('不满足前置条件').length).toBeGreaterThan(0);
  expect(await screen.findByText(/完成解析、技能画像与验证快照/)).toBeInTheDocument();
});

test('投递成功后重新读取后端并展示已投递',async()=>{
  api.getPublishedEnterpriseJob.mockResolvedValue({
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'后端工程师',jd_text:null,headcount:1,
    location:null,employment_type:'full_time',salary_min:null,salary_max:null,salary_unit:'month',status:'published',
  });
  const ready={resume_id:'resume-1',resume_display_name:'候选简历',validated_cv_snapshot_id:'snapshot-1',eligible:true,eligibility_reason:'eligible',submission:null};
  api.listCandidateSubmissionOptions.mockResolvedValueOnce([ready]).mockResolvedValueOnce([{...ready,submission:{submission_id:'submission-1',resume_id:'resume-1',status:'submitted',created_at:null,updated_at:null}}]);
  api.submitCandidate.mockResolvedValue({});
  detail();

  fireEvent.click(await screen.findByRole('button',{name:'确认投递'}));
  await waitFor(()=>expect(api.submitCandidate).toHaveBeenCalledWith('job-published','resume-1'));
  expect(await screen.findByText('已投递')).toBeInTheDocument();
  expect(api.listCandidateSubmissionOptions).toHaveBeenCalledTimes(2);
});

test('岗位详情使用已验证 CV 与企业 JD 发起正式匹配并打开报告',async()=>{
  api.getPublishedEnterpriseJob.mockResolvedValue({
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'后端工程师',jd_text:'负责 Python 服务开发。',headcount:1,
    location:null,employment_type:'full_time',salary_min:null,salary_max:null,salary_unit:'month',status:'published',
  });
  api.listCandidateSubmissionOptions.mockResolvedValue([{resume_id:'resume-1',resume_display_name:'候选简历',validated_cv_snapshot_id:'snapshot-1',eligible:true,eligibility_reason:'eligible',submission:null}]);
  matchingApi.createEnterpriseJobMatchTask.mockResolvedValue({task_id:'task-1',status:'succeeded',progress:1,evaluation_id:'evaluation-1'});
  render(<MemoryRouter initialEntries={['/jobs/job-published']}><App><SystemNoticeHost/><Routes><Route path="/jobs/:jobId" element={<PublishedJobDetail/>}/><Route path="/matching/reports/:evaluationId" element={<div>正式报告已打开</div>}/></Routes></App><LocationProbe/></MemoryRouter>);

  expect(await screen.findByText('CV × JD 正式匹配')).toBeInTheDocument();
  await waitFor(()=>expect(matchingApi.getMatchPreflight).toHaveBeenCalledWith('resume-1','job-published','enterprise_job'));
  fireEvent.click(screen.getByRole('button',{name:/查看正式匹配报告/}));
  await waitFor(()=>expect(matchingApi.createEnterpriseJobMatchTask).toHaveBeenCalledWith('resume-1','job-published',expect.any(String)));
  expect(await screen.findByText('正式报告已打开')).toBeInTheDocument();
  expect(screen.getByTestId('location-probe')).toHaveTextContent('/matching/reports/evaluation-1');
});

test('页面加载从后端恢复已投递状态并支持撤销后恢复已撤销',async()=>{
  api.getPublishedEnterpriseJob.mockResolvedValue({
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'后端工程师',jd_text:null,headcount:1,
    location:null,employment_type:'full_time',salary_min:null,salary_max:null,salary_unit:'month',status:'published',
  });
  const submitted={resume_id:'resume-1',resume_display_name:'候选简历',validated_cv_snapshot_id:'snapshot-1',eligible:true,eligibility_reason:'eligible',submission:{submission_id:'submission-1',resume_id:'resume-1',status:'submitted',created_at:null,updated_at:null}};
  api.listCandidateSubmissionOptions.mockResolvedValueOnce([submitted]).mockResolvedValueOnce([{...submitted,submission:{...submitted.submission,status:'revoked'}}]);
  api.revokeCandidateSubmission.mockResolvedValue({});
  detail();

  expect(await screen.findByText('已投递')).toBeInTheDocument();
  expect(api.submitCandidate).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button',{name:'撤销投递'}));
  await waitFor(()=>expect(api.revokeCandidateSubmission).toHaveBeenCalledWith('job-published','resume-1'));
  expect(await screen.findByText('已撤销')).toBeInTheDocument();
  expect(await screen.findByRole('button',{name:'重新投递'})).toBeInTheDocument();
});

test('投递失败展示明确状态且保留重试入口',async()=>{
  api.getPublishedEnterpriseJob.mockResolvedValue({
    enterprise_job_id:'job-published',enterprise_name:'示例科技',title:'后端工程师',jd_text:null,headcount:1,
    location:null,employment_type:'full_time',salary_min:null,salary_max:null,salary_unit:'month',status:'published',
  });
  api.listCandidateSubmissionOptions.mockResolvedValue([{resume_id:'resume-1',resume_display_name:'候选简历',validated_cv_snapshot_id:'snapshot-1',eligible:true,eligibility_reason:'eligible',submission:null}]);
  api.submitCandidate.mockRejectedValue(new Error('投递服务暂不可用'));
  detail();

  fireEvent.click(await screen.findByRole('button',{name:'确认投递'}));
  expect(await screen.findByText('投递失败')).toBeInTheDocument();
  expect(screen.getByText('投递服务暂不可用')).toBeInTheDocument();
  expect(await screen.findByRole('button',{name:'确认投递'})).toBeEnabled();
});

test('企业管理页继续显示 draft 为草稿',async()=>{
  api.getMyEnterprise.mockResolvedValue({enterprise_id:'enterprise-1',enterprise_name:'示例科技'});
  api.listSkillCategories.mockResolvedValue([]);
  api.listEnterpriseJobs.mockResolvedValue([{
    enterprise_job_id:'job-draft',enterprise_id:'enterprise-1',title:'待发布岗位',standard_position_id:null,jd_text:'内部草稿',headcount:1,
    location:null,employment_type:null,salary_min:null,salary_max:null,salary_unit:'month',status:'draft',created_at:null,updated_at:null,
  }]);
  api.getSkillWeights.mockResolvedValue([]);
  api.listEnterpriseMatchEvaluations.mockResolvedValue([]);
  api.listCandidateSubmissions.mockResolvedValue([]);
  render(<MemoryRouter><EnterpriseRecruitment/></MemoryRouter>);

  expect((await screen.findAllByText('待发布岗位')).length).toBeGreaterThan(0);
  expect(screen.getAllByText('草稿').length).toBeGreaterThan(0);
  expect(screen.getByRole('button',{name:/发布岗位$/})).toBeInTheDocument();
});
