import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {beforeEach,expect,test,vi} from 'vitest';
import Root,{EvidenceViewer} from './App';
import {resetDemoOverviewCacheForTests} from './features/demo/pages/DemoOverview';
import {resetEmergingCacheForTests} from './features/emerging/cache';
import {resetMatchingWorkbenchCacheForTests} from './features/matching/pages/MatchingWorkbench';
import type {EvaluationReport} from './features/matching/types';

vi.mock('./GraphView',()=>({GraphView:({relations,onSelect}:{relations:Array<{skill_id:string;canonical_name:string}>;onSelect?:(skillId:string)=>void})=> <div aria-label="岗位技能关系图">{relations.map(item=><button key={item.skill_id} onClick={()=>onSelect?.(item.skill_id)}>{item.canonical_name}</button>)}</div>}));
vi.mock('echarts',()=>({init:()=>({setOption:vi.fn(),resize:vi.fn(),dispose:vi.fn()})}));
const response=(data:unknown,status=200)=>Promise.resolve({ok:status<400,status,statusText:status<400?'OK':'error',json:async()=>({code:status<400?0:status*100+1,message:status<400?'success':'API 失败',data,details:{},trace_id:'req_test'})});
const publicPermissions=['catalog.read_published','emerging.read_published','evidence.read_public','trend.published.read'];
const adminPermissions=[...publicPermissions,'account.manage','kg.build.manage','kg.normalization.manage','kg.review.manage','kg.version.manage','emerging.discovery.manage','emerging.candidate.manage','emerging.publish.manage','catalog.promote.manage','integration.status.view','integration.cv.retry','integration.jd.retry','integration.outbox.requeue','integration.worker.run','jd.create','jd.parse','jd.publish','resume.parse.manage','resume.profile.generate','matching.run','learning_path.create','trend.run.manage','trend.source.manage','trend.review.manage','trend.publish.manage'];
const reviewerPermissions=[...publicPermissions,'kg.normalization.manage','kg.review.manage','integration.status.view','trend.review.manage'];
const developerPermissions=[...publicPermissions,'integration.status.view','integration.cv.retry','integration.jd.retry','integration.outbox.requeue','integration.worker.run','jd.create','jd.parse','jd.publish','resume.parse.manage','resume.profile.generate','matching.run','learning_path.create','trend.run.manage','trend.source.manage','trend.review.manage','trend.publish.manage'];
const personalPermissions=[...publicPermissions,'resume.parse.manage','resume.profile.generate','matching.run','learning_path.create'];
const enterprisePermissions=[...publicPermissions,'jd.create','jd.parse'];
const rolePermissions:Record<string,string[]>={admin:adminPermissions,reviewer:reviewerPermissions,developer:developerPermissions,personal_user:personalPermissions,enterprise_user:enterprisePermissions};
const user=(role='personal_user',permissions=rolePermissions[role]||[])=>({user_id:'user-1',username:role,role,permissions});
function authenticatedFetch(role='personal_user',permissions?:string[]){return vi.fn((url:string)=>{
  if(url.endsWith('/api/v1/auth/me'))return response(user(role,permissions??rolePermissions[role]??[]));
  if(url.endsWith('/api/v1/portal/positions'))return response([]);
  if(url.endsWith('/api/v1/portal/emerging-assets'))return response([]);
  if(url.endsWith('/api/v1/portal/emerging-position-signals'))return response({signals:[],observed_from:null,observed_to:null,source_contract:'published-jd-fact.v2',projection_version:'recent-position-signals.v1'});
  return response([]);
})}
function buildRouteFetch(){return vi.fn((url:string)=>{if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));if(url.includes('/api/v1/portal/admin/catalog/positions'))return response(catalogPositionPage([{position_id:'POS_AI',position_name:'AI 岗位',source_emerging_position_id:null,status:'published',graph_onboarding_status:'mapped',created_at:null,updated_at:null}]));return response([])})}
const catalogPositionPage=(items:Array<Record<string,unknown>>)=>({items,pagination:{page:1,page_size:10,total:items.length,total_pages:items.length?1:0},filters:{domains:[]},sort:{by:'name',order:'asc'}});
const portalDemoTask=(updates:Record<string,unknown>={})=>({
  task_id:'demo-task-1',task_type:'matching',object_type:'standard_position',object_id:'position-1',service:'matching-service',
  status:'succeeded',progress:1,error:null,result_reference:'/api/v1/matches/reports/evaluation-1',
  created_at:'2026-08-05T08:00:00Z',updated_at:'2026-08-05T09:00:00Z',...updates,
});
const formalExperimentFixture=()=>({
  experiment_id:'EXP-EMERGE-01-CROSSWINDOW-V3.2-20260823',
  algorithm_version:'EMERGE-v3.2',
  status:'accepted',
  stage2_unit:'occupation_cluster',
  evidence_level:'short_window',
  window_semantics:'12 days / 6 dates; no long-term market-growth claim',
  source_results_sha256:'8b6f35b1d77d7bbe7317892578b1478a8adae4158199a859caae260047b093ec',
  cluster_counts:{total_clusters:2811,clusters_with_stage1_evaluated:2752,clusters_without_annotation:59,clusters_eligible_for_stage2:2021,clusters_with_any_temporal_evidence:2080},
  stage2_distribution_over_eligible:{insufficient_evidence:1310,not_emerging:562,weak_emerging_signal:139,emerging:10},
  coverage:{independent_posting_persistence:280,enterprise_diffusion:252,source_diffusion:77,structural_evolution:41,short_window_growth:280,re_observation_only:1800},
  stage1_regression:{matched:127,total:127,distribution:{insufficient_evidence:52,renaming:36,hybridization:6,specialization:30,unexplained_structural_novelty:3}},
  acceptance_gates:{passed:7,total:7},
  emerging_clusters:[{cluster_key:'aiagent研发',canonical_title:'AI Agent研发工程师(大数据运维治理方向)',stage1_relation:'hybridization',postings:2,enterprises:2,sources:1}],
  validation_boundary:'Rule-assisted reference labels are not independent expert Gold.',
});
const formalClustersFixture=()=>[
  {
    cluster_key:'aiagent研发',
    canonical_title:'AI Agent研发工程师(大数据运维治理方向)',
    stage1_relation:'hybridization',
    representative:true,
    eligible:true,
    state:'emerging',
    ablation_states:{baseline:'emerging',no_enterprise_diffusion:'weak_emerging_signal',no_structural_evolution:'emerging',no_temporal:'weak_emerging_signal'},
    counts:{observations:4,distinct_dates:2,independent_postings:2,enterprises:2,sources:1,content_hash_count:2},
    growth:{available:true,growth_delta:0,per_window:[{date:'2026-08-01',distinct_postings:2},{date:'2026-08-07',distinct_postings:2}]},
    structural_changed:false,
    evidence_refs:['京东','小红书','playwright','167276','小红书_156'],
    definition:{
      position_name:'AI Agent研发工程师(大数据运维治理方向)',
      position_summary:'AI Agent研发工程师(大数据运维治理方向)：正式实验 EMERGE v3.2 识别的岗位混合化方向新兴岗位。',
      core_responsibilities:['负责生产平台核心模块的功能开发','负责数据治理与运维体系建设'],
      required_skills:[{raw_skill:'Java',normalized_skill_id:'LANG_JAVA',confidence:1},{raw_skill:'SQL',normalized_skill_id:'LANG_SQL',confidence:1}],
      bonus_skills:[],
      industry_scenarios:[],
      distinguishing_features:['Java','SQL'],
      representative_enterprises:['京东','小红书'],
      growth_trajectory:['2026-08-01：2 条独立发布','2026-08-07：2 条独立发布'],
      field_evidence:{},
    },
  },
  {
    cluster_key:'可观测性技术研发基础技术',
    canonical_title:'可观测性技术研发-基础技术',
    stage1_relation:'hybridization',
    representative:true,
    eligible:true,
    state:'emerging',
    ablation_states:{baseline:'emerging',no_enterprise_diffusion:'weak_emerging_signal',no_structural_evolution:'emerging',no_temporal:'weak_emerging_signal'},
    counts:{observations:4,distinct_dates:2,independent_postings:2,enterprises:2,sources:1,content_hash_count:2},
    growth:{available:true,growth_delta:0,per_window:[]},
    structural_changed:false,
    evidence_refs:['京东'],
  },
  {
    cluster_key:'普通岗位',
    canonical_title:'普通岗位',
    stage1_relation:'insufficient_evidence',
    representative:false,
    eligible:false,
    state:'insufficient_evidence',
    ablation_states:{baseline:'insufficient_evidence',no_enterprise_diffusion:'insufficient_evidence',no_structural_evolution:'insufficient_evidence',no_temporal:'insufficient_evidence'},
    counts:{observations:1,distinct_dates:1,independent_postings:1,enterprises:1,sources:1,content_hash_count:1},
    growth:{available:false,growth_delta:0,per_window:[]},
    structural_changed:false,
    evidence_refs:[],
  },
  {
    cluster_key:'7633312076320426246',
    canonical_title:'7633312076320426246',
    stage1_relation:'insufficient_evidence',
    representative:false,
    eligible:false,
    state:'insufficient_evidence',
    ablation_states:{baseline:'insufficient_evidence',no_enterprise_diffusion:'insufficient_evidence',no_structural_evolution:'insufficient_evidence',no_temporal:'insufficient_evidence'},
    counts:{observations:1,distinct_dates:1,independent_postings:1,enterprises:1,sources:1,content_hash_count:1},
    growth:{available:false,growth_delta:0,per_window:[]},
    structural_changed:false,
    evidence_refs:['7633312076320426246'],
  },
];

function clickTaskDetailRow(...fragments:string[]){
  const rows=within(screen.getByLabelText('任务详情')).getAllByRole('row');
  const row=rows.find(candidate=>fragments.every(fragment=>candidate.textContent?.includes(fragment)));
  expect(row,`未找到包含 ${fragments.join('、')} 的任务行`).toBeTruthy();
  fireEvent.click(row!);
}

beforeEach(()=>{cleanup();localStorage.clear();resetDemoOverviewCacheForTests();resetEmergingCacheForTests();resetMatchingWorkbenchCacheForTests();window.history.pushState({},'', '/');vi.restoreAllMocks();vi.unstubAllGlobals()});

async function expandNavGroups(){
  for(const title of Array.from(document.querySelectorAll('.ant-menu-submenu-title[aria-expanded="false"]'))){
    fireEvent.click(title);
  }
}

test('未登录用户看到带装饰背景的公开首页并可进入登录页',async()=>{
  const fetchMock=vi.fn();vi.stubGlobal('fetch',fetchMock);render(<Root/>);
  expect(await screen.findByRole('heading',{name:/持续感知职业变化/})).toBeInTheDocument();
  expect(within(screen.getByRole('banner')).getByRole('link',{name:'登录使用'})).toHaveAttribute('href','/login');
  expect(screen.getByRole('link',{name:'功能介绍'})).toHaveAttribute('href','/features');
  expect(screen.getByRole('link',{name:'用户手册'})).toHaveAttribute('href','/user-guide.html?v=20260901');
  expect(screen.getByRole('link',{name:'用户手册'})).toHaveAttribute('target','_blank');
  expect(screen.getByText('98%')).toBeInTheDocument();
  expect(screen.queryByText('JobPulse 工作方式')).not.toBeInTheDocument();
  await waitFor(()=>expect(document.querySelector('.home-hero__background')).toBeTruthy());
  expect(fetchMock).not.toHaveBeenCalled();
});

test('登录页演示身份一键填充账号',async()=>{
  const fetchMock=vi.fn();vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/login'}/>);
  expect(await screen.findByRole('link',{name:'返回首页'})).toHaveAttribute('href','/');
  expect(screen.getByRole('link',{name:'功能介绍'})).toHaveAttribute('href','/features');
  fireEvent.click(await screen.findByRole('button',{name:/管理员/}));
  expect(screen.getByLabelText('用户名')).toHaveValue('demo_admin');
  expect(screen.getByLabelText('密码')).toHaveValue('password123');
  expect(fetchMock).not.toHaveBeenCalled();
});

test('普通用户使用主系统 token 访问公开模块且看不到管理导航',async()=>{
  localStorage.setItem('main_access_token','token');const fetchMock=authenticatedFetch();vi.stubGlobal('fetch',fetchMock);render(<Root/>);
  expect(await screen.findByText('没有匹配的已发布岗位')).toBeInTheDocument();
  expect(screen.getAllByText('岗位全景')).toHaveLength(2);expect(screen.getByText('新兴岗位')).toBeInTheDocument();
  expect(screen.queryByText('图谱构建')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/auth/me',expect.anything());
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/positions',expect.anything());
});

test('admin 看到全部管理菜单',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('admin'));render(<Root/>);
  await waitFor(()=>expect(screen.getAllByText('岗位全景')).toHaveLength(2));
  await expandNavGroups();
  for(const label of ['数据同步','模型服务配置','图谱构建','审核中心','图谱版本管理','新兴岗位发现','新兴岗位候选'])expect(screen.getByText(label)).toBeInTheDocument();
  // 归一化审核已并入审核中心 Tab，不再是侧边栏独立入口
  expect(screen.queryByText('归一化审核')).not.toBeInTheDocument();
});

test('admin 路由可访问 /admin/build',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',buildRouteFetch());render(<Root initialPath={'/admin/build'}/>);
  await waitFor(()=>expect(screen.queryByText('无权访问')).not.toBeInTheDocument());
});

test('任务中心默认请求业务总览 Endpoint',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([portalDemoTask()]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/tasks'}/>);
  expect((await screen.findAllByText('岗位匹配')).length).toBeGreaterThan(0);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/admin/demo-tasks',expect.anything());
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/tasks'))).toBe(false);
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/extraction-tasks?'))).toBe(false);
});

test('任务中心按类型状态和业务对象发送三种筛选 URL',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.includes('/api/v1/portal/admin/demo-tasks'))return response([portalDemoTask()]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/tasks'}/>);
  await screen.findAllByText('岗位匹配');
  fireEvent.mouseDown(screen.getByLabelText('任务类型'));
  fireEvent.click((await screen.findAllByText('岗位匹配')).at(-1)!);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching'))).toBe(true));
  fireEvent.mouseDown(screen.getByLabelText('任务状态'));
  fireEvent.click((await screen.findAllByText('失败')).at(-1)!);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching&status=failed'))).toBe(true));
  fireEvent.mouseDown(screen.getByLabelText('业务对象'));
  fireEvent.click((await screen.findAllByText('标准岗位')).at(-1)!);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching&status=failed&object_id=position-1'))).toBe(true));
});

test('任务中心忽略晚返回的旧筛选请求',async()=>{
  localStorage.setItem('main_access_token','token');
  type TestResponse=Awaited<ReturnType<typeof response>>;
  let resolveOld!:(value:TestResponse)=>void;
  let resolveNew!:(value:TestResponse)=>void;
  const oldRequest=new Promise<TestResponse>(resolve=>{resolveOld=resolve});
  const newRequest=new Promise<TestResponse>(resolve=>{resolveNew=resolve});
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([portalDemoTask({object_id:'initial-position'})]);
    if(url.endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching'))return oldRequest;
    if(url.endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching&status=failed'))return newRequest;
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/tasks'}/>);
  await screen.findAllByText('岗位匹配');
  fireEvent.click((await screen.findByText('岗位匹配',{selector:'strong'})).closest('tr')!);
  expect(within(screen.getByLabelText('任务详情')).getAllByText('标准岗位').length).toBeGreaterThan(0);

  fireEvent.mouseDown(screen.getByLabelText('任务类型'));
  fireEvent.click((await screen.findAllByText('岗位匹配')).at(-1)!);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching'))).toBe(true));

  fireEvent.mouseDown(screen.getByLabelText('任务状态'));
  fireEvent.click((await screen.findAllByText('失败')).at(-1)!);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/demo-tasks?task_type=matching&status=failed'))).toBe(true));

  resolveNew(await response([portalDemoTask({task_id:'new-task',object_id:'new-position',status:'failed',progress:0,error:{code:'NEW_FILTER',message:'新筛选结果'},result_reference:null})]));
  await waitFor(()=>expect(within(screen.getByLabelText('任务详情')).getAllByText('失败').length).toBeGreaterThan(0));

  resolveOld(await response([portalDemoTask({task_id:'old-task',object_id:'old-position'})]));
  await Promise.resolve();
  await Promise.resolve();
  expect(within(screen.getByLabelText('任务详情')).queryByText('已完成')).not.toBeInTheDocument();
  expect(within(screen.getByLabelText('任务详情')).getAllByText('失败').length).toBeGreaterThan(0);
});

test('任务中心分别展示 pending running succeeded failed 和 cancelled',async()=>{
  localStorage.setItem('main_access_token','token');
  const tasks=[
    portalDemoTask({task_id:'pending-task',status:'pending',progress:0,result_reference:null}),
    portalDemoTask({task_id:'running-task',status:'running',progress:.5,result_reference:null}),
    portalDemoTask({task_id:'succeeded-task'}),
    portalDemoTask({task_id:'failed-task',status:'failed',progress:0,error:{code:'MATCH_FAILED',message:'匹配执行失败'},result_reference:null}),
    portalDemoTask({task_id:'cancelled-task',object_id:'cancelled-position',status:'cancelled',progress:0,result_reference:null}),
  ];
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user('admin',adminPermissions)):url.endsWith('/api/v1/portal/admin/demo-tasks')?response(tasks):response([])));
  render(<Root initialPath={'/tasks'}/>);
  await screen.findAllByText('岗位匹配');
  fireEvent.click((await screen.findByText('岗位匹配',{selector:'strong'})).closest('tr')!);
  await waitFor(()=>expect(screen.getAllByText('等待中').length).toBeGreaterThan(0));
  expect(screen.getAllByText('运行中').length).toBeGreaterThan(0);
  expect(screen.getAllByText('已完成').length).toBeGreaterThan(0);
  expect(screen.getAllByText('失败').length).toBeGreaterThan(0);
  expect(screen.getAllByText('已取消').length).toBeGreaterThan(0);
  clickTaskDetailRow('标准岗位','已取消');
  expect(await screen.findByText('任务已取消')).toBeInTheDocument();
});

test('任务中心失败详情显示中文说明且隐藏内部错误码',async()=>{
  localStorage.setItem('main_access_token','token');
  const failed=portalDemoTask({task_id:'failed-task',status:'failed',progress:0,error:{code:'MATCH_FAILED',message:'上游匹配服务不可用'},result_reference:null});
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user('admin',adminPermissions)):url.endsWith('/api/v1/portal/admin/demo-tasks')?response([failed]):response([])));
  render(<Root initialPath={'/tasks'}/>);
  await screen.findAllByText('岗位匹配');
  fireEvent.click((await screen.findByText('岗位匹配',{selector:'strong'})).closest('tr')!);
  clickTaskDetailRow('标准岗位','失败');
  expect(screen.queryByText('MATCH_FAILED')).not.toBeInTheDocument();
  expect((await screen.findAllByText('任务执行失败')).length).toBeGreaterThan(0);
  expect(screen.getAllByText('上游匹配服务不可用').length).toBeGreaterThan(0);
});

test('任务中心成功任务通过解析器跳转正式结果',async()=>{
  localStorage.setItem('main_access_token','token');
  const succeeded=portalDemoTask({task_id:'discovery-task',task_type:'discovery',object_type:'discovery_run',object_id:'run-1',service:'discovery-service',result_reference:'discovery_run:run-1'});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([succeeded]);
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/portal/admin/discovery-formal-experiment'))return response(formalExperimentFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-formal-experiment/clusters'))return response(formalClustersFixture());
    return response([]);
  }));
  render(<Root initialPath={'/tasks'}/>);
  fireEvent.click((await screen.findByText('新兴岗位发现',{selector:'strong'})).closest('tr')!);
  clickTaskDetailRow('已完成');
  fireEvent.click(await screen.findByRole('button',{name:/查看结果/}));
  // 跳转后页面会继续加载运行数据并整体重渲染，用 waitFor 重复查询避免拿到已卸载的标题节点
  await waitFor(()=>expect(screen.getByRole('heading',{name:'新兴岗位发现'})).toBeInTheDocument());
});

test('新兴岗位发现展示正式发现结果与岗位簇明细',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-formal-experiment'))return response(formalExperimentFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-formal-experiment/clusters'))return response(formalClustersFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/discovery"/>);

  expect(await screen.findByText('AI Agent研发工程师(大数据运维治理方向)')).toBeInTheDocument();
  expect(screen.getByText('正式发现的 10 个新兴岗位簇')).toBeInTheDocument();
  expect(screen.getByText('识别尚未标准化、但已形成独立职责与技能结构的市场新岗位。')).toBeInTheDocument();
  expect(screen.queryByText(/正式实验|第 3\.2 版|全部岗位簇判定/)).not.toBeInTheDocument();
  expect(screen.queryByText('Rule-assisted reference labels are not independent expert Gold.')).not.toBeInTheDocument();
  expect(screen.queryByText('尚未执行本次复现；下方先展示冻结实验的预期结果')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'导入正式发现结果到发布链路'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'复核正式发现'})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'查看 10 个新兴岗位簇'})).not.toBeInTheDocument();
  expect(screen.queryByText('复现检查')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/portal/admin/discovery-runs'))).toBe(false);
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/portal/admin/discovery-candidates'))).toBe(false);

  fireEvent.click(await screen.findByRole('tab',{name:'岗位簇明细'}));
  expect(await screen.findByPlaceholderText('搜索岗位名称')).toBeInTheDocument();
  expect(screen.getByText('当前显示 3 个已命名岗位簇')).toBeInTheDocument();
  expect(screen.queryByText('7633312076320426246')).not.toBeInTheDocument();
  expect(screen.getAllByText('新兴').length).toBeGreaterThan(0);
  const clusterTitles=await screen.findAllByText('AI Agent研发工程师(大数据运维治理方向)');
  fireEvent.click(clusterTitles.find(node=>node.tagName==='STRONG')!.closest('tr')!);
  expect(await screen.findByText('消融判定')).toBeInTheDocument();
  expect(await screen.findByText('岗位定义')).toBeInTheDocument();
  expect(screen.getByText('负责生产平台核心模块的功能开发')).toBeInTheDocument();
  expect(screen.getAllByText('Java').length).toBeGreaterThan(0);
  expect(screen.getByText('去企业扩散：弱信号')).toBeInTheDocument();
  expect(screen.getAllByText('京东').length).toBeGreaterThan(0);
  expect(screen.queryByText('167276')).not.toBeInTheDocument();
  expect(screen.queryByText('小红书_156')).not.toBeInTheDocument();
  expect(screen.queryByText('aiagent研发')).not.toBeInTheDocument();
});

const candidateLifecycleFixture=()=>({
  candidates:[
    {
      candidate_id:'cand-stable',
      status:'stable_emerging_role',
      first_seen_window_id:'W1',
      last_seen_window_id:'W4',
      age:4,
      current_cluster_id:'CL_STABLE',
      previous_cluster_ids:['CL1','CL2','CL3'],
      canonical_title:'AI Agent Developer',
      display_title:'Agent Engineer',
      definition:{position_name:'Agent Engineer',required_skills:[{raw_skill:'Python'},{raw_skill:'RAG'}]},
      identity_profile:{titles:['AI Agent Developer','Agent Engineer'],skills:['python','rag','agent'],responsibilities:['构建智能体应用'],member_jd_ids:['JD1','JD2','JD3'],observed_window_ids:['W1','W2','W3','W4'],semantic_centroid:[0.1,0.2]},
      evidence:{sample_count:5},
      support_count:5,
      company_coverage:3,
      skill_similarity:.9,
      responsibility_similarity:.85,
      title_similarity:.8,
      membership_overlap:.7,
      identity_similarity:.92,
      novelty_score:.6,
      emergence_score:.78,
      identity_stability:4,
      created_at:null,
      updated_at:null,
    },
    {
      candidate_id:'cand-weak',
      status:'weak_signal',
      first_seen_window_id:'W4',
      last_seen_window_id:'W4',
      age:1,
      current_cluster_id:'CL_NEW',
      previous_cluster_ids:[],
      canonical_title:'提示词工程师',
      display_title:'提示词工程师',
      definition:{},
      identity_profile:{titles:['提示词工程师'],skills:['prompt'],responsibilities:['设计提示词'],member_jd_ids:['JD9'],observed_window_ids:['W4'],semantic_centroid:[]},
      evidence:{},
      support_count:2,
      company_coverage:1,
      skill_similarity:null,
      responsibility_similarity:null,
      title_similarity:null,
      membership_overlap:null,
      identity_similarity:1,
      novelty_score:.4,
      emergence_score:.31,
      identity_stability:1,
      created_at:null,
      updated_at:null,
    },
    {
      candidate_id:'cand-dead',
      status:'dead',
      first_seen_window_id:'W2',
      last_seen_window_id:'W5',
      age:4,
      current_cluster_id:'CL_DEAD',
      previous_cluster_ids:['CL_X1','CL_X2'],
      canonical_title:'旧岗位',
      display_title:'旧岗位',
      definition:{},
      identity_profile:{titles:['旧岗位'],skills:['legacy'],responsibilities:['维护'],member_jd_ids:['JD-D1'],observed_window_ids:['W2','W3','W4','W5'],semantic_centroid:[]},
      evidence:{},
      support_count:1,
      company_coverage:1,
      skill_similarity:null,
      responsibility_similarity:null,
      title_similarity:null,
      membership_overlap:null,
      identity_similarity:.8,
      novelty_score:.2,
      emergence_score:.1,
      identity_stability:2,
      created_at:null,
      updated_at:null,
    },
  ],
  filters:{status:null,candidate_id:null,window_id:null},
});

const matchEvidence=(overrides:Record<string,unknown>={})=>({
  matched:true,
  closest_candidate_id:'cand-stable',
  identity_similarity:.82,
  threshold:.6,
  components:{title_similarity:.75,skill_similarity:.91,responsibility_similarity:.86,membership_overlap:.42,semantic_similarity:.89},
  decision_reason:'identity_similarity 0.82 >= threshold 0.6; title 0.75; skills 0.91; responsibilities 0.86; membership overlap 0.42; semantic cosine',
  decision_version:'candidate-identity-v1',
  ...overrides,
});

const candidateTrajectoryFixture=()=>({
  candidate_id:'cand-stable',
  trajectory:[
    {observation_id:'obs-1',candidate_id:'cand-stable',run_id:'run-1',cluster_id:'CL1',cluster_name:'AI Agent 岗位簇',window_id:'W1',title:'AI Agent Developer',status:'weak_signal',emergence_score:.42,support_count:2,company_count:1,identity_similarity:1,skill_similarity:.9,responsibility_similarity:.85,title_similarity:.8,membership_overlap:.7,semantic_similarity:null,evidence:{sample_count:2},match_evidence:matchEvidence({matched:false,closest_candidate_id:null,identity_similarity:1,components:{title_similarity:1,skill_similarity:1,responsibility_similarity:1,membership_overlap:1,semantic_similarity:null},decision_reason:'first observation creates candidate; no historical candidate matched'}),created_at:null},
    {observation_id:'obs-2',candidate_id:'cand-stable',run_id:'run-2',cluster_id:'CL2',cluster_name:'AI Agent 开发岗位簇',window_id:'W2',title:'AI Agent 开发工程师',status:'incubating',emergence_score:.55,support_count:3,company_count:2,identity_similarity:.88,skill_similarity:.9,responsibility_similarity:.85,title_similarity:.8,membership_overlap:.6,semantic_similarity:.93,evidence:{sample_count:3},match_evidence:matchEvidence(),created_at:null},
    {observation_id:'obs-3',candidate_id:'cand-stable',run_id:'run-3',cluster_id:'CL3',cluster_name:'智能体应用岗位簇',window_id:'W3',title:'智能体应用工程师',status:'emerging_candidate',emergence_score:.66,support_count:4,company_count:3,identity_similarity:.9,skill_similarity:.92,responsibility_similarity:.88,title_similarity:.85,membership_overlap:.7,semantic_similarity:null,evidence:{sample_count:4},match_evidence:matchEvidence({identity_similarity:.79,components:{title_similarity:.8,skill_similarity:.9,responsibility_similarity:.85,membership_overlap:.5,semantic_similarity:null},decision_reason:'identity_similarity 0.79 >= threshold 0.6; semantic unavailable, weights renormalized'}),created_at:null},
    {observation_id:'obs-4',candidate_id:'cand-stable',run_id:'run-4',cluster_id:'CL_STABLE',cluster_name:'Agent Engineer 岗位簇',window_id:'W4',title:'Agent Engineer',status:'stable_emerging_role',emergence_score:.78,support_count:5,company_count:3,identity_similarity:.92,skill_similarity:.9,responsibility_similarity:.85,title_similarity:.8,membership_overlap:.7,semantic_similarity:.95,evidence:{sample_count:5},match_evidence:matchEvidence({identity_similarity:.92}),created_at:null},
  ],
});

test.skip('候选生命周期列表渲染候选、状态、窗口与技能',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  expect(await screen.findByText('Agent Engineer')).toBeInTheDocument();
  expect(screen.getByText('稳定新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('弱信号')).toBeInTheDocument();
  expect(screen.getByText('第 1 批观测')).toBeInTheDocument();
  expect(screen.getAllByText('第 4 批观测').length).toBeGreaterThan(0);
  expect(screen.getByText('Python')).toBeInTheDocument();
  expect(screen.getByText('3 份')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/admin/discovery-candidates',expect.anything());
});

test.skip('候选生命周期不展示技能 UUID 并给出可读占位',async()=>{
  localStorage.setItem('main_access_token','token');
  const fixture=candidateLifecycleFixture();
  const stableCandidate=fixture.candidates[0] as unknown as Record<string,unknown>;
  stableCandidate.display_title='生成式 AI 平台工程师';
  stableCandidate.definition={position_name:'生成式 AI 平台工程师',required_skills:[{raw_skill:'09ad977b-188d-45d2-a666-5f39ca00601c'}]};
  stableCandidate.identity_profile={...stableCandidate.identity_profile as Record<string,unknown>,skills:['6558475f-c90a-4a8f-834b-21227383fd32']};
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(fixture);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  const row=(await screen.findByText('生成式 AI 平台工程师')).closest('tr')!;
  expect(within(row).getByText('待识别')).toBeInTheDocument();
  expect(within(row).queryByText('09ad977b-188d-45d2-a666-5f39ca00601c')).not.toBeInTheDocument();
  expect(within(row).queryByText('6558475f-c90a-4a8f-834b-21227383fd32')).not.toBeInTheDocument();
});

test.skip('候选生命周期点击候选展示多窗口演化时间线',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return response(candidateTrajectoryFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));

  expect(await screen.findByText(/候选生命周期 · Agent Engineer/)).toBeInTheDocument();
  for(const label of ['弱信号','孵化中','新兴候选','稳定新兴岗位'])expect(await screen.findAllByText(label)).not.toHaveLength(0);
  expect(screen.queryByText(/run-[1-4]/)).not.toBeInTheDocument();
  expect(screen.queryByText(/CL_STABLE/)).not.toBeInTheDocument();
  expect(screen.getAllByText('Agent Engineer 岗位簇').length).toBeGreaterThan(0);
  expect(screen.getAllByText('第 4 批观测').length).toBeGreaterThan(0);
  expect(screen.getByText('进入审核（创建新兴岗位）')).toBeInTheDocument();
  expect(screen.getByText('生命周期状态链：')).toBeInTheDocument();
});

test.skip('候选生命周期当前 Cluster 未投影时进入审核不可用',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return response(candidateTrajectoryFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));

  expect(await screen.findByText(/候选生命周期 · Agent Engineer/)).toBeInTheDocument();
  expect(screen.getByText('当前岗位簇尚未投影到主系统')).toBeInTheDocument();
});

test.skip('候选生命周期空状态与 API 失败状态',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response({candidates:[],filters:{status:null,candidate_id:null,window_id:null}});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);
  // 页面上多个面板共用一个空态文案，候选生命周期为空时至少出现一处「暂无数据」
  expect((await screen.findAllByText('暂无数据')).length).toBeGreaterThan(0);

  cleanup();
  // 第二个渲染模拟全新会话：清空新兴岗位页面缓存，否则 5 分钟内的缓存会直接命中、不再触发 502
  resetEmergingCacheForTests();
  const failingFetch=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(null,502);
    return response([]);
  });
  vi.stubGlobal('fetch',failingFetch);
  render(<Root initialPath="/admin/emerging"/>);
  expect(await screen.findByText('上游服务不可用')).toBeInTheDocument();
  expect(screen.getByText('API 失败')).toBeInTheDocument();
});

test.skip('候选生命周期非 stable 候选进入审核按钮禁用',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  const weakRow=(await screen.findByText('提示词工程师')).closest('tr')!;
  expect(within(weakRow).getByRole('button',{name:'进入审核'})).toBeDisabled();

  const deadRow=screen.getByText('旧岗位').closest('tr')!;
  expect(within(deadRow).getByRole('button',{name:'进入审核'})).toBeDisabled();
});

test('新兴岗位候选页只展示治理记录，不再加载发现候选板块',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/emerging-positions'))return response([]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  expect(await screen.findByText('暂无进入治理的候选')).toBeInTheDocument();
  expect(screen.queryByRole('heading',{name:'发现候选'})).not.toBeInTheDocument();
  expect(screen.queryByText('提示词工程师')).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/portal/admin/discovery-candidates'))).toBe(false);
});

test('新兴岗位治理发布后只更新当前行，版本弹窗不暴露内部标识',async()=>{
  localStorage.setItem('main_access_token','token');
  let status='approved';
  const candidate={
    emerging_id:'6dcbf198-a47e-4cd4-894b-b052ac70e29b',cluster_id:'cluster-internal',position_name:'系统架构专家',
    core_responsibilities:['负责图形系统架构'],required_skills:[{raw_skill:'系统架构'}],bonus_skills:[],industry_scenarios:['图形系统'],
    germination_score:1,score_dimensions:{},evidence_jd_ids:['JD_1'],status,
  };
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/emerging-positions')&&(!options?.method||options.method==='GET'))return response([{...candidate,status}]);
    if(url.endsWith('/definition-versions'))return response([{
      version_id:'6dcbf198-a47e-4cd4-894b-b052ac70e29b',emerging_id:candidate.emerging_id,
      snapshot:{position_summary:'负责图形系统架构',core_responsibilities:['负责图形系统架构'],required_skills:[{raw_skill:'系统架构'}],evidence_jd_ids:['JD_1']},
      selected:true,created_by:'6dcbf198-a47e-4cd4-894b-b052ac70e29b',created_at:'2026-09-01T07:25:33.648327+00:00',implementation_status:'database_persisted_definition_snapshot',
    }]);
    if(url.endsWith(`/${candidate.emerging_id}/publish`)&&options?.method==='POST'){
      status='published';
      return response({...candidate,status});
    }
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  expect(await screen.findByText('已审核')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /版\s*本/ }));
  expect(await screen.findByText('负责图形系统架构')).toBeInTheDocument();
  expect(screen.getByText('第 1 版')).toBeInTheDocument();
  expect(screen.getByText('已保存，可恢复')).toBeInTheDocument();
  expect(screen.queryByText('6dcbf198-a47e-4cd4-894b-b052ac70e29b')).not.toBeInTheDocument();
  expect(screen.queryByText('database_persisted_definition_snapshot')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'知道了'}));
  fireEvent.click(screen.getByRole('button', { name: /发\s*布/ }));
  expect(await screen.findByText('已发布')).toBeInTheDocument();
  expect(fetchMock.mock.calls.filter(([url,options])=>String(url).endsWith('/api/v1/emerging-positions')&&(!(options as RequestInit|undefined)?.method||(options as RequestInit).method==='GET'))).toHaveLength(1);
});

test('生成定义返回并展示概述、职责、技能与业务场景',async()=>{
  localStorage.setItem('main_access_token','token');
  const candidate={
    emerging_id:'EM_GENERATE',cluster_id:'CL_GENERATE',position_name:'系统架构专家(图形系统)',
    core_responsibilities:['旧职责'],required_skills:[{raw_skill:'C++'}],bonus_skills:[],industry_scenarios:[],
    germination_score:1,score_dimensions:{},evidence_jd_ids:['JD_1'],field_evidence:{},status:'published',
  };
  const generated={
    ...candidate,status:'pending_review',
    core_responsibilities:['负责图形系统架构设计与功能研发'],
    required_skills:[{raw_skill:'C++'},{raw_skill:'图形渲染'}],
    bonus_skills:[{raw_skill:'性能分析'}],
    industry_scenarios:['图形系统架构与功能研发','图形系统性能优化与疑难攻关'],
    field_evidence:{position_summary:{content:'负责图形系统架构、性能优化与工程落地。'}},
  };
  vi.stubGlobal('fetch',vi.fn((url:string,options?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/emerging-positions')&&(!options?.method||options.method==='GET'))return response([candidate]);
    if(url.endsWith('/EM_GENERATE/generate-definition')&&options?.method==='POST')return response(generated);
    if(url.endsWith('/EM_GENERATE')&&options?.method==='PUT')return response({...generated,position_name:'图形系统架构工程师',field_evidence:{position_summary:{content:'负责图形系统架构设计、性能优化与工程交付。'}}});
    return response([]);
  }));
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByRole('button',{name:'生成定义'}));
  expect(await screen.findByText('负责图形系统架构、性能优化与工程落地。')).toBeInTheDocument();
  expect(screen.getByText('图形系统架构与功能研发')).toBeInTheDocument();
  expect(screen.getByText('图形系统性能优化与疑难攻关')).toBeInTheDocument();
  expect(screen.getByText('性能分析')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('岗位名称'),{target:{value:'图形系统架构工程师'}});
  fireEvent.change(screen.getByLabelText('岗位概述'),{target:{value:'负责图形系统架构设计、性能优化与工程交付。'}});
  fireEvent.click(screen.getByRole('button',{name:'保存优化'}));
  await waitFor(()=>expect(fetch).toHaveBeenCalledWith('/api/v1/emerging-positions/EM_GENERATE',expect.objectContaining({method:'PUT'})));
  expect(await screen.findByText('图形系统架构工程师')).toBeInTheDocument();
});

test.skip('候选生命周期 stable 且 Cluster 已映射时可进入治理并调用新 BFF',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  const stableRow=(await screen.findByText('Agent Engineer')).closest('tr')!;
  const button=within(stableRow).getByRole('button',{name:'进入审核'});
  expect(button).toBeEnabled();
  fireEvent.click(button);

  await waitFor(()=>expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/admin/discovery-candidates/cand-stable/enter-governance',expect.objectContaining({method:'POST'})));
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/emerging-positions/from-cluster/CL_STABLE'))).toBe(false);
});

test.skip('候选生命周期 Cluster API 失败时显示无法确认而不是不可映射',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response(null,502);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return response(candidateTrajectoryFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));
  expect(await screen.findByText('无法确认岗位簇映射状态')).toBeInTheDocument();
  expect(screen.queryByText('当前岗位簇尚未投影到主系统')).not.toBeInTheDocument();
});

test.skip('候选生命周期身份连续性解释展示 match_evidence',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return response(candidateTrajectoryFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));
  await screen.findByText(/候选生命周期 · Agent Engineer/);

  const headers=await screen.findAllByText('身份连续性解释');
  for(const header of headers)fireEvent.click(header);

  expect(screen.getAllByText('身份相似度').length).toBeGreaterThan(0);
  expect(screen.getAllByText(/阈值 0.600/).length).toBeGreaterThan(0);
  expect(screen.getAllByText('候选名称').length).toBeGreaterThan(0);
  expect(screen.getAllByText('技能相似度').length).toBeGreaterThan(0);
  expect(screen.getAllByText('职责相似度').length).toBeGreaterThan(0);
  expect(screen.getAllByText('簇成员重合').length).toBeGreaterThan(0);
  expect(screen.getAllByText('语义相似度').length).toBeGreaterThan(0);
  expect(screen.getAllByText('不可用').length).toBeGreaterThan(0);
  expect(screen.getAllByText('综合相似度达到判定阈值，沿用同一候选岗位。').length).toBeGreaterThan(0);
  expect(screen.queryByText(/identity_similarity/)).not.toBeInTheDocument();
  // semantic 真实嵌套值来自 match_evidence.components.semantic_similarity（优先于 observation 顶层）
  expect(screen.getAllByText('0.890').length).toBeGreaterThan(0);
});

test.skip('候选生命周期当前状态 dead 但轨迹最后为 emerging 时补充当前状态',async()=>{
  localStorage.setItem('main_access_token','token');
  const deadTrajectory={candidate_id:'cand-dead',trajectory:[candidateTrajectoryFixture().trajectory[2]]};
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-dead/trajectory'))return response(deadTrajectory);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('旧岗位'));
  expect(await screen.findByText(/候选生命周期 · 旧岗位/)).toBeInTheDocument();
  expect(screen.getByText('当前：已消亡')).toBeInTheDocument();
  expect(screen.getByText(/当前状态 已消亡 发生于最近一次岗位簇观测之后/)).toBeInTheDocument();
});

test.skip('候选生命周期 trajectory 竞态不覆盖当前候选',async()=>{
  localStorage.setItem('main_access_token','token');
  let resolveStable:(value:unknown)=>void=()=>{};
  const stablePromise=new Promise(resolve=>{resolveStable=resolve});
  const weakTrajectory={candidate_id:'cand-weak',trajectory:[{...candidateTrajectoryFixture().trajectory[0],candidate_id:'cand-weak',run_id:'run-weak',cluster_id:'CL_NEW',window_id:'W4',title:'提示词工程师',status:'weak_signal'}]};
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return stablePromise;
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-weak/trajectory'))return response(weakTrajectory);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));
  fireEvent.click(await screen.findByText('提示词工程师'));
  expect(await screen.findByText(/候选生命周期 · 提示词工程师/)).toBeInTheDocument();

  resolveStable({ok:true,status:200,statusText:'OK',json:async()=>({code:0,message:'success',data:candidateTrajectoryFixture(),trace_id:'req_stale'})});
  await waitFor(()=>expect(screen.queryByText(/候选生命周期 · Agent Engineer/)).not.toBeInTheDocument());
  expect(screen.getByText(/候选生命周期 · 提示词工程师/)).toBeInTheDocument();
});

test.skip('候选生命周期 identity semantic 缺省时回退 observation 顶层值',async()=>{
  localStorage.setItem('main_access_token','token');
  const trajectory=candidateTrajectoryFixture();
  (trajectory.trajectory[1].match_evidence.components as Record<string,unknown>).semantic_similarity=null;
  trajectory.trajectory[1].semantic_similarity=.93;
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    if(url.endsWith('/api/v1/position-clusters'))return response([{cluster_id:'CL_STABLE'}]);
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates'))return response(candidateLifecycleFixture());
    if(url.endsWith('/api/v1/portal/admin/discovery-candidates/cand-stable/trajectory'))return response(trajectory);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/admin/emerging"/>);

  fireEvent.click(await screen.findByText('Agent Engineer'));
  await screen.findByText(/候选生命周期 · Agent Engineer/);
  for(const header of await screen.findAllByText('身份连续性解释'))fireEvent.click(header);

  expect(await screen.findByText('0.930')).toBeInTheDocument();
});

test('任务中心无结果引用时显示解析原因',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user('admin',adminPermissions)):url.endsWith('/api/v1/portal/admin/demo-tasks')?response([portalDemoTask({result_reference:null})]):response([])));
  render(<Root initialPath={'/tasks'}/>);
  fireEvent.click((await screen.findByText('岗位匹配',{selector:'strong'})).closest('tr')!);
  clickTaskDetailRow('标准岗位','已完成');
  expect(await screen.findByText('结果引用格式未冻结')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/查看结果/})).not.toBeInTheDocument();
});

test('任务中心切换执行明细后请求任务 Endpoint 并保留重试与结果跳转',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([]);
    if(url.endsWith('/api/v1/tasks'))return response([
      {task_id:'task-1',task_type:'jd_parse',status:'completed',canonical_status:'succeeded',progress:1,input_payload:{jd_id:'JD_1'},result_payload:{},result_reference:'jd_parse_result:PARSE_1',error_code:null,error_message:null,created_by:'admin',attempt_count:1,logs:[],created_at:null,updated_at:'2026-07-30T01:00:00Z',started_at:null,finished_at:null,execution_mode:'synchronous_local'},
      {task_id:'ops-1',task_type:'outbox_replay',status:'completed',canonical_status:'succeeded',progress:1,input_payload:{},result_payload:{},result_reference:null,error_code:null,error_message:null,created_by:'admin',attempt_count:1,logs:[],created_at:null,updated_at:'2026-07-30T03:00:00Z',started_at:null,finished_at:null,execution_mode:'synchronous_local'},
    ]);
    if(url.includes('/api/v1/extraction-tasks?'))return response({items:[{id:'extract-1',source_jd_version_id:'version-1',status:'failed',provider:'rule-provider',request_id:'request-1',attempt_count:1,max_attempts:3,started_at:null,finished_at:null,last_error_code:'provider_error',last_error_message:'抽取服务暂不可用',retryable:true,bundle_payload:null,claimed_by:null,lease_expires_at:null,heartbeat_at:null,created_at:'2026-07-30T00:00:00Z',updated_at:'2026-07-30T02:00:00Z'}],total:1,page:1,page_size:100});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/tasks'}/>);
  fireEvent.click(await screen.findByText('执行明细'));
  expect((await screen.findAllByText('岗位描述结构化抽取')).length).toBeGreaterThan(0);
  expect(screen.getByText('岗位描述解析')).toBeInTheDocument();
  expect(screen.queryByText('outbox_replay')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/取消任务|运行抽取|重新排队/})).not.toBeInTheDocument();
  fireEvent.click((await screen.findByText('岗位描述解析',{selector:'strong'})).closest('tr')!);
  clickTaskDetailRow('岗位描述解析','已完成');
  expect(await screen.findByRole('button',{name:/查看结果/})).toBeInTheDocument();
  fireEvent.click((await screen.findByText('岗位描述结构化抽取',{selector:'strong'})).closest('tr')!);
  clickTaskDetailRow('岗位描述结构化抽取','失败');
  expect(await screen.findByText('抽取服务暂不可用')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/重试抽取/})).toBeInTheDocument();
},20000);

test('任务中心无权限时不请求 Demo Endpoint',async()=>{
  localStorage.setItem('main_access_token','token');
  const permissions=reviewerPermissions.filter(permission=>permission!=='integration.status.view');
  const fetchMock=vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user('reviewer',permissions)):response([]));
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/tasks'}/>);
  expect(await screen.findByText('当前账户缺少业务任务总览查看权限。')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/portal/admin/demo-tasks'))).toBe(false);
});

test('Trend 任务缺少 result_reference 时从报告 ID 恢复结果链接',async()=>{
  localStorage.setItem('main_access_token','token');
  const skill={skill_id:'SKILL_RAG',skill_name:'RAG',category:'AI',weight:.8,confidence:.9,importance_level:'core',trend_score:.2,evidence_count:2};
  const graph={position_id:'POS_FE',position_name:'前端工程师',graph_version:'GV_9',skills:[skill],relations:[],core_responsibilities:[],industry_scenarios:[]};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/tasks'))return response([{task_id:'TREND_TASK',task_type:'trend_analysis',status:'succeeded',canonical_status:'succeeded',progress:1,input_payload:{position_id:'POS_FE'},result_payload:{position_id:'POS_FE',report_id:'REPORT_RECOVER'},result_reference:null,error_code:null,error_message:null,created_by:'admin',attempt_count:1,logs:[],created_at:null,updated_at:'2026-08-04T01:00:00Z',started_at:null,finished_at:null,execution_mode:'remote_async_polling'}]);
    if(url.includes('/api/v1/extraction-tasks?'))return response({items:[],total:0,page:1,page_size:100});
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_FE',name:'前端工程师',category_code:'TECH',current_version_id:9,current_version_number:1,sample_count:1,skill_count:1,published_at:null,release_id:null,quality_state:'ready'}]);
    if(url.endsWith('/api/v1/positions/POS_FE/trend-reports'))return response({schema_version:'trend-delivery.v1',items:[{report_id:'REPORT_RECOVER',position_id:'POS_FE',graph_version:'GV_9',time_window_start:'2026-01-01',time_window_end:'2026-06-30',current_graph:graph,skill_weight_distribution:{},new_skills:[],rising_skills:[],declining_skills:[],replaced_skills:[],skill_combo_shifts:[],risks:[],summary:'已恢复的 Trend 报告',status:'published',analysis_mode:'remote_multi_source',provider:'trend_intelligence_http',provider_run_id:'RUN_RECOVER',source_coverage:1,missing_sources:[],quality_flags:[],evidence_references:[]}],pagination:{page:1,page_size:20,total:1,total_pages:1},filters:{},sort:{by:'created_at',order:'desc'},not_found_ids:[]});
    return response([]);
  }));
  render(<Root initialPath="/tasks"/>);
  fireEvent.click(await screen.findByText('执行明细'));
  fireEvent.click((await screen.findAllByText('趋势情报分析',{selector:'strong'}))[0].closest('tr')!);
  clickTaskDetailRow('趋势情报分析','已完成');
  fireEvent.click(await screen.findByRole('button',{name:/查看结果/}));
  expect(await screen.findByText('已恢复的趋势报告')).toBeInTheDocument();
});

test('reviewer 菜单完全由权限集合决定',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('reviewer'));render(<Root/>);
  await waitFor(()=>expect(screen.getAllByText('岗位全景')).toHaveLength(2));
  await expandNavGroups();
  expect(screen.getByText('新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('审核中心')).toBeInTheDocument();
  expect(screen.getByText('JD 数据中心')).toBeInTheDocument();
  expect(screen.getByText('任务中心')).toBeInTheDocument();
  expect(screen.getByText('能力演化')).toBeInTheDocument();
  expect(screen.getByText('数据同步')).toBeInTheDocument();
  expect(screen.getByText('评估与反馈')).toBeInTheDocument();
  expect(screen.queryByText('图谱构建')).not.toBeInTheDocument();
  expect(screen.queryByText('图谱版本管理')).not.toBeInTheDocument();
  expect(screen.queryByText('新兴岗位发现')).not.toBeInTheDocument();
  expect(screen.queryByText('新兴岗位候选')).not.toBeInTheDocument();
});

test('reviewer 路由可访问 /admin/normalize',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('reviewer'));render(<Root initialPath={'/admin/normalize'}/>);
  await waitFor(()=>expect(screen.queryByText('无权访问')).not.toBeInTheDocument());
});

test('reviewer 路由可访问 /admin/review',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('reviewer'));render(<Root initialPath={'/admin/review'}/>);
  await waitFor(()=>expect(screen.queryByText('无权访问')).not.toBeInTheDocument());
  // 归一化审核作为审核中心的同级 Tab 出现
  expect(screen.getByRole('tab',{name:'归一化审核'})).toBeInTheDocument();
});

test('reviewer 路由 /admin/build 返回无权访问',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('reviewer'));render(<Root initialPath={'/admin/build'}/>);
  expect(await screen.findByText('无权访问')).toBeInTheDocument();
  expect(screen.getByText('当前账户缺少访问此页面所需权限。')).toBeInTheDocument();
});

test('developer 看不到管理菜单',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('developer'));render(<Root/>);
  await waitFor(()=>expect(screen.getAllByText('岗位全景')).toHaveLength(2));
  await expandNavGroups();
  expect(screen.getByText('新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('JD 数据中心')).toBeInTheDocument();
  expect(screen.getByText('任务中心')).toBeInTheDocument();
  expect(screen.getByText('能力演化')).toBeInTheDocument();
  expect(screen.getByText('数据同步')).toBeInTheDocument();
  expect(screen.getByText('评估与反馈')).toBeInTheDocument();
  expect(screen.queryByText('图谱构建')).not.toBeInTheDocument();
  expect(screen.queryByText('归一化审核')).not.toBeInTheDocument();
  expect(screen.queryByText('审核中心')).not.toBeInTheDocument();
  expect(screen.queryByText('图谱版本管理')).not.toBeInTheDocument();
  expect(screen.queryByText('新兴岗位发现')).not.toBeInTheDocument();
  expect(screen.queryByText('新兴岗位候选')).not.toBeInTheDocument();
});

test('developer 路由 /admin/review 返回无权访问',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('developer'));render(<Root initialPath={'/admin/review'}/>);
  expect(await screen.findByText('无权访问')).toBeInTheDocument();
  expect(screen.getByText('当前账户缺少访问此页面所需权限。')).toBeInTheDocument();
});

test('personal_user 看不到管理菜单',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('personal_user'));render(<Root/>);
  await waitFor(()=>expect(screen.getAllByText('岗位全景')).toHaveLength(2));
  await expandNavGroups();
  expect(screen.getByText('新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('岗位匹配')).toBeInTheDocument();
  expect(screen.queryByText('能力演化')).not.toBeInTheDocument();
  for(const label of ['数据同步','图谱构建','归一化审核','审核中心','图谱版本管理','新兴岗位发现','新兴岗位候选'])expect(screen.queryByText(label)).not.toBeInTheDocument();
});

test('enterprise_user 看不到管理菜单',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('enterprise_user'));render(<Root/>);
  await waitFor(()=>expect(screen.getAllByText('岗位全景')).toHaveLength(2));
  await expandNavGroups();
  expect(screen.getByText('新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('JD 数据中心')).toBeInTheDocument();
  expect(screen.getByText('招聘工作台')).toBeInTheDocument();
  expect(screen.queryByText('任务中心')).not.toBeInTheDocument();
  expect(screen.queryByText('能力演化')).not.toBeInTheDocument();
  expect(screen.queryByText('岗位匹配')).not.toBeInTheDocument();
  for(const label of ['数据同步','图谱构建','归一化审核','审核中心','图谱版本管理','新兴岗位发现','新兴岗位候选'])expect(screen.queryByText(label)).not.toBeInTheDocument();
});

test('personal_user 路由 /admin/build 返回无权访问',async()=>{
  localStorage.setItem('main_access_token','token');vi.stubGlobal('fetch',authenticatedFetch('personal_user'));render(<Root initialPath={'/admin/build'}/>);
  expect(await screen.findByText('无权访问')).toBeInTheDocument();
  expect(screen.getByText('当前账户缺少访问此页面所需权限。')).toBeInTheDocument();
});

test('企业招聘工作台展示岗位、技能权重与候选评估',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('enterprise_user'));
    if(url.endsWith('/api/v1/enterprises/me'))return response({enterprise_id:'ENT_1',owner_user_id:'user-1',enterprise_name:'示例科技',industry:'软件',scale:'100–499 人',location:'上海',description:null,status:'active',created_at:null,updated_at:null});
    if(url.endsWith('/api/v1/enterprise-jobs'))return response([{enterprise_job_id:'JOB_1',enterprise_id:'ENT_1',title:'大模型应用工程师',standard_position_id:'POS_AI',jd_text:'负责 RAG 应用与服务开发。',headcount:2,location:'上海',employment_type:'full_time',salary_min:25000,salary_max:40000,salary_unit:'month',status:'published',created_at:null,updated_at:'2026-07-30T01:00:00Z'}]);
    if(url.endsWith('/api/v1/enterprise-jobs/JOB_1/skill-weights'))return response([{id:'W_1',enterprise_job_id:'JOB_1',skill_id:'skill_rag',weight:.8,is_required:true,is_bonus:false}]);
    if(url.endsWith('/api/v1/enterprise-jobs/JOB_1/candidate-submissions'))return response([{submission_id:'SUB_1',resume_id:'RES_1',resume_display_name:'候选人 A',enterprise_job_id:'JOB_1',enterprise_id:'ENT_1',resume_owner_user_id:'USER_1',status:'submitted',created_at:null,updated_at:null,parse_status:'completed',validated_cv_snapshot_id:'CV_SNAPSHOT_1',skill_count:5,matchable:true,matchable_reason:'可匹配'}]);
    if(url.endsWith('/api/v1/enterprise-jobs/JOB_1/match-reports'))return response([{evaluation_id:'EVAL_1',task_id:'TASK_1',resume_id:'RES_1',position_id:'enterprise_job:JOB_1',target_type:'enterprise_job',status:'succeeded',provider:'matching-service',lineage:{},created_at:null,updated_at:'2026-07-30T02:00:00Z'}]);
    if(url.endsWith('/api/v1/enterprise-jobs/JOB_1/candidate-decision-board'))return response({enterprise_job_id:'JOB_1',total:0,ranked_count:0,items:[]});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/enterprise/recruitment'}/>);
  expect(await screen.findByText('企业招聘工作台')).toBeInTheDocument();
  expect((await screen.findAllByText('大模型应用工程师')).length).toBeGreaterThan(0);
  expect(await screen.findByText('skill_rag')).toBeInTheDocument();
  fireEvent.click(screen.getByText('候选评估'));
  expect((await screen.findAllByText('候选人 A')).length).toBeGreaterThan(0);
  expect(await screen.findByText('已完成')).toBeInTheDocument();
  expect(screen.queryByText('简历记录 · 评估记录')).not.toBeInTheDocument();
  expect(screen.queryByText('matching-service')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:/^check\s*适配$/})).toBeInTheDocument();
});

test('管理员评估与反馈页运行数据集并展示治理队列',async()=>{
  localStorage.setItem('main_access_token','token');
  const dataset={dataset_id:'DATASET_1',dataset_type:'jd',name:'JD 解析基准集',description:'回归样本',payload:{items:[]},created_at:'2026-07-30T00:00:00Z',updated_at:'2026-07-30T00:00:00Z'};
  const report={report_id:'EVAL_REPORT_1',report_type:'jd_parse',dataset_id:'DATASET_1',metrics:{jd_parse_accuracy:.9,evaluated_count:10,error_count:1,skipped_count:0},error_cases:[{case_id:'case_009',type:'mismatch',description:'技能字段不一致'}],evaluation_status:'completed',algorithm_version:'jd-rule-eval-v1',config_snapshot:{comparison:'normalized_exact'},evaluated_count:10,error_count:1,implementation_status:'data_driven_rule_evaluation',created_at:null,updated_at:null};
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/evaluation/datasets'))return response([dataset]);
    if(url.endsWith('/api/v1/feedback'))return response([{feedback_id:'FB_1',feedback_type:'match_report',user_id:'PERSONAL_1',payload:{summary:'匹配报告遗漏项目经验'},status:'pending_review',created_at:'2026-07-30T01:00:00Z',updated_at:null,implementation_status:'database_persisted_review_queue'}]);
    if(url.endsWith('/api/v1/evaluation/jd-parse/run')&&init?.method==='POST')return response(report);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/governance/evaluation'}/>);
  expect(await screen.findByText('质量评估与反馈治理')).toBeInTheDocument();
  expect(await screen.findByText('JD 解析基准集')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/运行当前数据集/}));
  expect(await screen.findByText('90%')).toBeInTheDocument();
  fireEvent.click(screen.getByText('反馈治理'));
  expect(await screen.findByText('匹配报告遗漏项目经验')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/开始处理/})).toBeInTheDocument();
});

test('匹配工作台只列已验证快照简历并在页面切换后复用缓存',async()=>{
  localStorage.setItem('main_access_token','token');
  const reference={evaluation_id:'MATCH_1',task_id:'TASK_1',resume_id:'RES_1',position_id:'POS_AI',target_type:'standard_position',status:'current',provider:'matching-service',lineage:{},created_at:'2026-07-30T01:00:00Z',updated_at:'2026-07-30T01:00:00Z'};
  const fetchMock=vi.fn((url:string,_init?:RequestInit)=>{
    void _init;
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([
      {resume_id:'RES_1',display_name:'已验证简历',source_type:'file',raw_text:'Python',parse_status:'completed',implementation_status:'validated_snapshot',validated_cv_snapshot_id:'CV_SNAPSHOT_1',created_at:null,updated_at:null},
      {resume_id:'RES_2',display_name:'未验证简历',source_type:'file',raw_text:'',parse_status:'parsed',implementation_status:'parsed',validated_cv_snapshot_id:null,created_at:null,updated_at:null},
    ]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_name:'大模型应用开发工程师',status:'existing'})]);
    if(url.includes('/api/v1/matches/rankings?'))return response({
      resume_id:'RES_1',validated_cv_snapshot_id:'CV_SNAPSHOT_1',algorithm_version:'coarse-skill-coverage.v1',status:'completed',total:1,completed:1,
      items:[{rank:1,position_id:'POS_AI',position_name:'大模型应用开发工程师',score:88,score_source:'formal',calculation_status:'completed',evaluation_id:'MATCH_1',task_id:'TASK_1'}],
    });
    if(url.endsWith('/api/v1/matches/reports'))return response([reference]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  const firstRender=render(<Root initialPath={'/matching?resumeId=RES_2'}/>);
  expect(await screen.findByText('个人岗位匹配')).toBeInTheDocument();
  expect(screen.queryByText('开始岗位匹配')).not.toBeInTheDocument();
  expect(screen.queryByText('从一份已验证简历出发，查看它与目标岗位的正式匹配报告。')).not.toBeInTheDocument();
  expect((await screen.findAllByText('大模型应用开发工程师')).length).toBeGreaterThan(0);
  expect(await screen.findByText('岗位匹配排名')).toBeInTheDocument();
  expect(screen.getByRole('columnheader',{name:'排名'})).toBeInTheDocument();
  expect(screen.getByRole('columnheader',{name:'岗位'})).toBeInTheDocument();
  expect(screen.getByRole('columnheader',{name:'匹配度'})).toBeInTheDocument();
  expect(screen.getByRole('columnheader',{name:'计算状态'})).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByRole('button',{name:/运行匹配/})).toBeEnabled());
  expect(screen.queryByRole('button',{name:/上传简历/})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/使用示例简历/})).not.toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/tasks'))).toBe(false);
  expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/api/v1/matches/rankings')&&(init as RequestInit|undefined)?.method==='POST')).toBe(false);
  expect(screen.getByRole('button',{name:/刷新/})).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/positions'))).toBe(true);
  firstRender.unmount();
  render(<Root initialPath={'/matching?resumeId=RES_1'}/>);
  expect((await screen.findAllByText('个人岗位匹配')).length).toBeGreaterThan(0);
  expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/api/v1/resumes/me'))).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/api/v1/matches/positions'))).toHaveLength(1);
  expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/api/v1/matches/reports'))).toHaveLength(1);
  // 排名在后台可能仍在生成，每次进入页面都会重新拉取最新状态，目录类数据仍走缓存。
  expect(fetchMock.mock.calls.filter(([url])=>String(url).includes('/api/v1/matches/rankings?'))).toHaveLength(2);
});

test('匹配排名仅在手动点击后生成并支持取消',async()=>{
  localStorage.setItem('main_access_token','token');
  let rankingStatus:'ready'|'running'|'cancelled'='ready';
  const rankingResponse=()=>({
    resume_id:'RES_1',validated_cv_snapshot_id:'CV_SNAPSHOT_1',algorithm_version:'coarse-skill-coverage.v1',status:rankingStatus,total:1,completed:0,
    items:[{rank:1,position_id:'POS_AI',position_name:'大模型应用开发工程师',score:62,score_source:'coarse',calculation_status:'preliminary',evaluation_id:null,task_id:null}],
  });
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/rankings/cancel')&&init?.method==='POST'){
      rankingStatus='cancelled';
      return response(rankingResponse());
    }
    if(url.endsWith('/api/v1/matches/rankings')&&init?.method==='POST'){
      rankingStatus='running';
      return response(rankingResponse());
    }
    if(url.includes('/api/v1/matches/rankings?'))return response(rankingResponse());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/matching"/>);

  const generate=await screen.findByRole('button',{name:/生成排名/});
  expect(screen.getByRole('columnheader',{name:'排名'})).toBeInTheDocument();
  expect(screen.getByText('初步排序')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/api/v1/matches/rankings')&&(init as RequestInit|undefined)?.method==='POST')).toBe(false);
  fireEvent.click(generate);
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/api/v1/matches/rankings')&&(init as RequestInit|undefined)?.method==='POST')).toBe(true));

  fireEvent.click(await screen.findByRole('button',{name:/取消生成/}));
  fireEvent.click(await screen.findByRole('button',{name:'确认取消'}));
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/rankings/cancel'))).toBe(true));
  expect(await screen.findByRole('button',{name:/继续生成/})).toBeInTheDocument();
});

test('匹配排名失败状态不直接展示内部英文错误码',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.includes('/api/v1/matches/rankings?'))return response({
      resume_id:'RES_1',validated_cv_snapshot_id:'CV_SNAPSHOT_1',algorithm_version:'coarse-skill-coverage.v3',status:'completed',total:1,completed:0,
      items:[{rank:1,position_id:'POS_AI',position_name:'大模型应用开发工程师',score:62,score_source:'coarse',calculation_status:'failed',evaluation_id:'MATCH_1',task_id:'TASK_1',error_code:'MATCHING_RESULT_SCORE_MISSING'}],
    });
    return response([]);
  }));

  render(<Root initialPath="/matching"/>);

  expect(await screen.findByText('评分失败')).toBeInTheDocument();
  expect(screen.queryByText(/MATCHING_RESULT_SCORE_MISSING/)).not.toBeInTheDocument();
});

test('匹配岗位选择器只展示后端判定可匹配的岗位并忽略不可用路由参数',async()=>{
  localStorage.setItem('main_access_token','token');
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([
      matchPosition({position_id:'POS_READY',position_name:'可匹配岗位'}),
      matchPosition({position_id:'POS_NO_PROFILE',position_name:'无正式画像岗位',matchable:false,reason:'POSITION_PROFILE_UNAVAILABLE',blockers:['POSITION_PROFILE_UNAVAILABLE'],position_graph_version:null}),
      matchPosition({position_id:'POS_DEPRECATED',position_name:'已弃用岗位',lifecycle_status:'deprecated',matchable:false,reason:'POSITION_DEPRECATED',blockers:['POSITION_DEPRECATED'],position_graph_version:null}),
      matchPosition({position_id:'POS_GRAPH_PENDING',position_name:'图谱未就绪岗位',matchable:false,reason:'POSITION_GRAPH_VERSION_UNAVAILABLE',blockers:['POSITION_GRAPH_VERSION_UNAVAILABLE'],position_graph_version:null}),
    ]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    return response([]);
  }));
  render(<Root initialPath="/matching?positionId=POS_NO_PROFILE"/>);

  expect((await screen.findAllByText('可匹配岗位')).length).toBeGreaterThan(0);
  expect(screen.queryByText('无正式画像岗位')).not.toBeInTheDocument();
  expect(screen.queryByText('已弃用岗位')).not.toBeInTheDocument();
  expect(screen.queryByText('图谱未就绪岗位')).not.toBeInTheDocument();
  await waitFor(()=>expect(calls.some(url=>url.includes('position_id=POS_READY'))).toBe(true));
  expect(calls.some(url=>url.includes('position_id=POS_NO_PROFILE'))).toBe(false);
});

test('后端未返回可匹配岗位时显示空状态且不请求 preflight',async()=>{
  localStorage.setItem('main_access_token','token');
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([
      matchPosition({position_id:'POS_DEPRECATED',lifecycle_status:'deprecated',matchable:false,reason:'POSITION_DEPRECATED',blockers:['POSITION_DEPRECATED'],position_graph_version:null}),
    ]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    return response([]);
  }));
  render(<Root initialPath="/matching?positionId=POS_DEPRECATED"/>);

  expect(await screen.findByText('暂无可匹配岗位')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/运行匹配/})).toBeDisabled();
  expect(calls.some(url=>url.includes('/api/v1/matches/preflight?'))).toBe(false);
});

test('没有已验证简历时只显示去简历中心按钮且不能运行匹配',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([{resume_id:'RES_2',display_name:'未验证简历',source_type:'file',raw_text:'',parse_status:'parsed',implementation_status:'parsed',validated_cv_snapshot_id:null,created_at:null,updated_at:null}]);
    if(url.endsWith('/api/v1/matches/positions'))return new Promise(()=>{});
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    return response([]);
  }));
  render(<Root initialPath={'/matching'}/>);
  expect(await screen.findByText('还没有已验证简历')).toBeInTheDocument();
  expect(screen.getByText('岗位目录正在后台预加载，简历确认后无需重新等待。')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'前往我的简历'})).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/运行匹配/})).toBeDisabled();
});

test('个人简历中心展示已上传简历、技能证据和历史匹配',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,_init?:RequestInit)=>{
    void _init;
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([{
      resume_id:'RES_PROFILE_1',
      display_name:'大模型应用开发简历',
      original_filename:'candidate.pdf',
      source_type:'file',
      file_id:'FILE_1',
      raw_text:'Python FastAPI RAG 项目经历',
      parse_status:'completed',
      input_extraction_status:'completed',
      implementation_status:'validated_snapshot',
      validated_cv_snapshot_id:'CV_SNAPSHOT_PROFILE_1',
      created_at:'2026-07-30T00:00:00Z',
      updated_at:'2026-07-30T00:00:00Z',
    }]);
    if(url.endsWith('/api/v1/resumes/RES_PROFILE_1/parse-result'))return response({
      parse_result_id:'PARSE_PROFILE_1',
      resume_id:'RES_PROFILE_1',
      education:[],
      projects:[],
      internships:[],
      skills:[],
      certificates:[],
      competitions:[],
      parse_confidence:.91,
      need_review:false,
    });
    if(url.endsWith('/api/v1/resumes/RES_PROFILE_1/skill-profile'))return response({
      resume_id:'RES_PROFILE_1',
      skills:[{resume_skill_id:'RS_PROFILE_1',resume_id:'RES_PROFILE_1',skill_id:'skill_python',raw_skill:'Python',confidence:.93,evidence:'项目接口开发',proficiency:null}],
    });
    if(url.endsWith('/api/v1/matches/reports'))return response([{
      evaluation_id:'REPORT_PROFILE_1',
      resume_id:'RES_PROFILE_1',
      target_id:'POS_AI',
      overall_score:62,
      status:'current',
      created_at:'2026-07-30T01:00:00Z',
      updated_at:'2026-07-30T01:00:00Z',
    }]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes'}/>);

  expect((await screen.findAllByText('我的简历')).length).toBeGreaterThan(0);
  expect((await screen.findAllByText('大模型应用开发简历')).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/candidate\.pdf/).length).toBeGreaterThan(0);
  expect(screen.queryByText('项目接口开发')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('技能画像'));
  expect(await screen.findByText('项目接口开发')).toBeInTheDocument();
  expect(screen.getByText('1 份报告')).toBeInTheDocument();
  expect(screen.getByText('当前简历的匹配记录')).toBeInTheDocument();
  expect(screen.getByText('原始简历概览')).toBeInTheDocument();
  expect(screen.getByText('教育经历')).toBeInTheDocument();
  expect(screen.getByText('结构化快照未返回教育条目')).toBeInTheDocument();
  expect(screen.getByText('原始文件：candidate.pdf')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:/进入岗位匹配/})).toBeInTheDocument();
  // 有历史匹配报告的简历同样允许删除，后端会级联清理报告引用
  expect(screen.getByRole('button',{name:/删除/})).toBeEnabled();
});

test('个人简历中心可以删除没有匹配记录的简历',async()=>{
  localStorage.setItem('main_access_token','token');
  let deleted=false;
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response(deleted?[]:[resumeRecord()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/resumes/RES_1')&&init?.method==='DELETE'){
      deleted=true;
      return response({resume_id:'RES_1',deleted:true});
    }
    if(url.endsWith('/api/v1/resumes/RES_1/parse-result'))return response({parse_result_id:'PARSE_1',resume_id:'RES_1',education:[],projects:[],internships:[],skills:[],certificates:[],competitions:[],parse_confidence:.9,need_review:false});
    if(url.endsWith('/api/v1/resumes/RES_1/skill-profile'))return response({resume_id:'RES_1',skills:[]});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes'}/>);

  expect((await screen.findAllByText('测试简历')).length).toBeGreaterThan(0);
  const deleteButton=await screen.findByRole('button',{name:/删除/});
  expect(deleteButton).toBeEnabled();
  fireEvent.click(deleteButton);
  const deletionWarning=await screen.findByText('删除后无法恢复。');
  const popover=deletionWarning.closest('.ant-popover') as HTMLElement;
  fireEvent.click(within(popover).getByRole('button',{name:/删\s*除/}));

  await waitFor(()=>expect(deleted).toBe(true));
  expect(await screen.findByText('还没有简历')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/api/v1/resumes/RES_1')&&(init as RequestInit|undefined)?.method==='DELETE')).toBe(true);
});

const cvTask=(updates:Record<string,unknown>={})=>({
  task_id:'TASK_CV_1',
  source_cv_version_id:'VERSION_1',
  owner_id:'owner-1',
  request_id:'request-1',
  execution_id:null,
  execution_metadata:{provider:'test-provider',model:'test-model',normalization_version:'2.0',taxonomy_version:'sha256:taxonomy'},
  status:'succeeded',
  processing_stage:'review_pending',
  attempt_count:1,
  max_attempts:3,
  last_error_code:null,
  last_error_message:null,
  retryable:false,
  claimed_by:null,
  lease_expires_at:null,
  heartbeat_at:null,
  next_attempt_at:null,
  finished_at:null,
  validation_conclusion:'pass',
  validation_report_payload:null,
  validation_task_id:'vt-1',
  validation_report_id:'vr-1',
  resume_id:null,
  created_at:null,
  updated_at:null,
  review_payload:null,
  review_id:'review-1',
  confirmation_status:'pending',
  latest_validated_cv_snapshot_id:null,
  confirmed_at:null,
  confirmed_by:null,
  review_revision:0,
  confirmation_idempotency_key:null,
  confirmation_idempotency_id:null,
  ...updates,
});
const cvReview=(updates:Record<string,unknown>={})=>({
  task_id:'TASK_CV_1',
  source_cv_id:'SOURCE_1',
  source_cv_version_id:'VERSION_1',
  status:'succeeded',
  confirmation_status:'pending',
  review_id:'review-1',
  review_revision:0,
  source_text:'熟练使用 Python',
  source_file_id:null,
  content_type:null,
  ocr_layout:null,
  reviewable_fields:[{
    field_id:'skill-1',
    field_type:'skill',
    section:'skills',
    item_id:'skill-1',
    field_path:'name',
    field_label:'技能',
    original_value:'Python',
    suggested_value:'Python',
    evidence:{source_document_id:'VERSION_1',source_id:'src-1',quote:'熟练使用 Python',start:0,end:11,alignment:'exact',occurrence_index:0},
    flag_codes:[],
  }],
  review_flags:[],
  validation:{conclusion:'pass',policy_version:'cv-validation-policy.v2',validation_task_id:'vt-1',validation_report_id:'vr-1',blocking_reasons:[]},
  ...updates,
});
const cvConfirmation={snapshot_id:'SNAPSHOT_1',snapshot_revision:1,resume_id:'RES_1',task_id:'TASK_CV_1',supersedes_snapshot_id:null,idempotency_key:'confirm-TASK_CV_1'};
const resumeRecord=(updates:Record<string,unknown>={})=>({
  resume_id:'RES_1',
  display_name:'测试简历',
  original_filename:'resume.pdf',
  source_type:'file',
  file_id:'FILE_1',
  raw_text:'熟练使用 Python',
  parse_status:'completed',
  input_extraction_status:'completed',
  implementation_status:'validated_snapshot',
  validated_cv_snapshot_id:'SNAPSHOT_1',
  created_at:null,
  updated_at:null,
  ...updates,
});

const readyMatchPreflight={
  ready:true,
  cv_snapshot_ready:true,
  cv_profile_ready:true,
  position_profile_ready:true,
  blockers:[],
  validated_cv_snapshot_id:'CV_SNAPSHOT_1',
  position_graph_version:'GRAPH_V1',
};
const matchPosition=(updates:Record<string,unknown>={})=>({
  position_id:'POS_AI',position_name:'AI 工程师',taxonomy_family_name:'技术',status:'published',
  lifecycle_status:'active',matchable:true,reason:'MATCHABLE',blockers:[],position_graph_version:'GRAPH_V1',...updates,
});

const matchReportFixture=(updates:Record<string,unknown>={}):EvaluationReport=>{
  const candidateEvidence={
    source_object_type:'validated_cv_snapshot',
    source_object_id:'snapshot-1',
    source_document_id:'version-1',
    source_fragment_id:'snapshot:1',
    quote:'熟练使用 Python',
    start:0,
    end:10,
    alignment:'exact',
    occurrence_index:0,
    version:{validated_cv_snapshot_id:'snapshot-1',source_cv_version_id:'version-1',resume_id:'resume-1',position_id:null,graph_version:null,source_jd_version_id:null,evaluation_id:null},
    result_reference:'validated_cv_snapshot:snapshot-1#evidence:snapshot:1:0-10',
  };
  const positionEvidence={
    ...candidateEvidence,
    source_object_type:'position_profile',
    source_object_id:'position-1',
    source_document_id:'position-1',
    quote:'需要 Python 与 SQL',
    version:{validated_cv_snapshot_id:null,source_cv_version_id:null,resume_id:null,position_id:'position-1',graph_version:'GRAPH_V1',source_jd_version_id:'jd-1',evaluation_id:null},
    result_reference:'position_profile:position-1#evidence:kg:1:0-14',
  };
  const gapEvidence={
    ...candidateEvidence,
    source_object_type:'matching_evidence',
    source_object_id:'evaluation-1',
    source_document_id:'evaluation-1',
    quote:'Gap Evidence',
    version:{validated_cv_snapshot_id:null,source_cv_version_id:null,resume_id:null,position_id:null,graph_version:null,source_jd_version_id:null,evaluation_id:'evaluation-1'},
    result_reference:'matching_evidence:evaluation-1#evidence:snapshot:1:0-6',
  };
  return {
    evaluation_id:'evaluation-1',
    task_id:'task-1',
    status:'current',
    stale:false,
    stale_reason_codes:[],
    evaluation:{
      evaluation_id:'evaluation-1',
      evaluation_status:'completed',
      algorithm_version:'deterministic-matching.v5',
      cv_profile_id:'CV_1',
      cv_profile_version:'v1',
      position_profile_id:'POS_1',
      position_profile_version:'v1',
      hard_constraint_results:[{
        requirement_id:'req-education',
        constraint_type:'education',
        status:'pass',
        required_value:'本科',
        candidate_value:'本科',
        position_evidence:[positionEvidence],
        candidate_evidence:[candidateEvidence],
        reason_code:'OK',
        confidence:.9,
      }],
      skill_results:[{
        requirement_id:'req-python',
        skill_id:'skill_python',
        skill_name:'Python',
        match_status:'matched',
        position_evidence:[positionEvidence],
        candidate_evidence:[candidateEvidence],
        reason_code:'MATCHED',
        confidence:.93,
      }],
      responsibility_results:[],
      project_results:[],
      scenario_results:[],
      summary:{hard_constraint_pass_count:1,hard_constraint_fail_count:0,required_skill_matched_count:1,required_skill_missing_count:0,bonus_skill_matched_count:0,bonus_skill_missing_count:0,coverage_denominator_policy:'exclude_unknown_unresolved_and_not_required'},
      final_match_result:{
        overall_score:68,
        match_confidence:.9,
        recommendation_level:'potential_match',
        hard_gate_status:'passed',
        dimension_scores:[{dimension:'required_skills',score:80,confidence:.9,configured_weight:.3,effective_weight:.3,applicable_count:1,scored_count:1,uncertain_count:0}],
        score_contributions:[],
        strengths:[],
        gaps:[],
        uncertain_items:[],
        explanation:'六维匹配评估',
        algorithm_version:'deterministic-matching.v5',
        scoring_config_version:'scoring-config.v1',
        cv_profile_id:'CV_1',
        position_profile_id:'POS_1',
        position_graph_version:'GRAPH_V1',
      },
    },
    gap_analysis:{
      generation_status:'completed',
      result_status:'completed',
      prioritized_gaps:[{
        gap_type:'required_skill_missing',
        requirement_id:'req-python',
        skill_id:'skill_rag',
        current_level:'beginner',
        target_level:'advanced',
        priority:'high',
        priority_score:72,
        reason_codes:['MISSING_SKILL'],
        evidence:[gapEvidence],
      }],
      learning_path:[{
        step_order:1,
        target_skill_id:'skill_rag',
        objective:'完成 RAG 项目',
        prerequisite_skill_ids:[],
        basis:['Gap 分析'],
        estimated_hours:8,
        cost_source_type:'heuristic',
        cost_source_ref:'gap-learning-hours.v1',
        estimate_status:'estimated',
        completion_criteria:['交付检索服务'],
        source_requirement_ids:['req-python'],
        reason_codes:['MISSING_SKILL'],
      }],
      counterfactual_suggestions:[],
      algorithm_version:'learning.v3',
    },
    versions:{
      position_graph_version:'GRAPH_V1',
      evaluation_algorithm_version:'deterministic-matching.v5',
      scoring_config_version:'scoring-config.v1',
    },
    lineage:{
      resume_id:'resume-1',
      position_id:'position-1',
      validated_cv_snapshot_id:'CV_SNAPSHOT_1',
      provider:'matching-service',
      method:'deterministic_explainable',
      algorithm_versions:{evaluation:'deterministic-matching.v5'},
    },
    created_at:null,
    updated_at:null,
    ...updates,
  } as unknown as EvaluationReport;
};

test('CV 上传使用真实 upload-and-extract 并完成稳定确认',async()=>{
  localStorage.setItem('main_access_token','token');
  let confirmCalls=0;
  const fetchMock=vi.fn((url:string,_init?:RequestInit)=>{
    void _init;
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return response({source_cv_id:'SOURCE_1',source_cv_version_id:'VERSION_1',cv_extraction_task_id:'TASK_CV_1',created_source:true,created_version:true,created_task:true,task_status:'pending',text_extraction_status:'completed',extraction_method:'pdf_text',extraction_provider:'pymupdf',source_file_id:null});
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/confirm')){
      confirmCalls+=1;
      return response(cvConfirmation);
    }
    if(url.endsWith('/api/v1/resumes/RES_1/parse-result'))return response({parse_result_id:'PARSE_1',resume_id:'RES_1',education:[],projects:[],internships:[],skills:[],certificates:[],competitions:[],parse_confidence:.9,need_review:false});
    if(url.endsWith('/api/v1/resumes/RES_1/skill-profile'))return response({resume_id:'RES_1',skills:[{resume_skill_id:'RS_1',resume_id:'RES_1',skill_id:'skill_python',raw_skill:'Python',confidence:.9,evidence:'熟练使用 Python',proficiency:null}]});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes'}/>);

  await screen.findByRole('button',{name:/上传简历/});
  const fileInput=document.querySelector('input[type=file]')!;
  fireEvent.change(fileInput,{target:{files:[new File(['pdf'],'resume.pdf',{type:'application/pdf'})]}});
  expect(await screen.findByText('字段证据审核')).toBeInTheDocument();
  expect(screen.getAllByText('熟练使用 Python').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText('技能画像'));
  expect(screen.getByText('程度未说明')).toBeInTheDocument();
  expect(screen.queryByText('90%')).not.toBeInTheDocument();
  expect(screen.getByText(/原文区间 0-11 · 精确匹配/)).toBeInTheDocument();
  const uploadCall=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))!;
  expect((uploadCall[1]?.body as FormData).get('use_ocr')).not.toBe('true');
  expect(uploadCall[1]?.method).toBe('POST');
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/resumes/file')||String(url).endsWith('/api/v1/resumes/image'))).toBe(false);

  fireEvent.mouseDown(screen.getByLabelText('skill-1 决策'));
  fireEvent.click(await screen.findByText('接受'));
  fireEvent.click(screen.getByRole('button',{name:'确认并生成快照'}));

  await waitFor(()=>expect(confirmCalls).toBe(1));
  expect(screen.queryByText('快照已确认')).not.toBeInTheDocument();
  expect(screen.queryByText(/ValidatedCVSnapshot 修订/)).not.toBeInTheDocument();
  const confirmCall=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/confirm'))!;
  const payload=JSON.parse(String((confirmCall[1] as RequestInit).body));
  expect(payload.idempotency_key).toBe('confirm-TASK_CV_1');
  expect(payload.expected_review_id).toBe('review-1');
  expect(payload.field_decisions[0]).toMatchObject({field_id:'skill-1',decision:'accept',evidence_quote:'熟练使用 Python',evidence_start:0,evidence_end:11});
  expect(payload.normalization_version).toBe('2.0');
  expect(payload.taxonomy_version).toBe('sha256:taxonomy');
  expect(screen.getByRole('button',{name:/进入岗位匹配/})).toBeInTheDocument();
});

test('CV 审核不向前端展示内部 warning 明细',async()=>{
  localStorage.setItem('main_access_token','token');
  const repeatedFlags=[1,2,3].map(index=>({
    code:'unknown_skill_proficiency',
    severity:'warning',
    rule_scope:'item',
    message:'声明技能的熟练度无法从原文判定。',
    suggested_action:null,
    item_id:`skill-${index}`,
  }));
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask({validation_conclusion:'warn'}));
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview({
      review_flags:repeatedFlags,
      validation:{conclusion:'warn',policy_version:'cv-validation-policy.v2',validation_task_id:'vt-1',validation_report_id:'vr-1',blocking_reasons:[]},
    }));
    return response([]);
  }));

  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);

  expect(await screen.findByText('Python')).toBeInTheDocument();
  expect(screen.queryByText('验证提示')).not.toBeInTheDocument();
  expect(screen.queryByText('unknown_skill_proficiency')).not.toBeInTheDocument();
  expect(screen.queryByText('声明技能的熟练度无法从原文判定。')).not.toBeInTheDocument();
});

test('CV 上传期间展示阶段进度条',async()=>{
  localStorage.setItem('main_access_token','token');
  let resolveUpload!:(value:Awaited<ReturnType<typeof response>>)=>void;
  const uploadResponse=new Promise<Awaited<ReturnType<typeof response>>>(resolve=>{resolveUpload=resolve});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return uploadResponse;
    return response([]);
  }));

  render(<Root initialPath={'/profile/resumes'}/>);
  await screen.findByRole('button',{name:/上传简历/});
  fireEvent.change(document.querySelector('input[type=file]')!,{
    target:{files:[new File(['resume'],'resume.txt',{type:'text/plain'})]},
  });

  expect(await screen.findByText('正在识别图片文字')).toBeInTheDocument();
  expect(screen.getByText(/正在读取文件并生成可抽取文本/)).toBeInTheDocument();
  // 进度数字走 requestAnimationFrame 平滑动画，负载高时帧回调会延后，放宽等待窗口
  await waitFor(()=>expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow','14'),{timeout:4000});

  await act(async()=>{
    resolveUpload(await response({
      source_cv_id:'SOURCE_1',source_cv_version_id:'VERSION_1',cv_extraction_task_id:'TASK_CV_1',
      created_source:true,created_version:true,created_task:true,task_status:'failed',
      text_extraction_status:'completed',extraction_method:'plain_text',extraction_provider:'builtin',source_file_id:null,
    }));
  });
});

test('上传区不再展示 PDF 模式选择且 PDF 走正式文本抽取路径',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,_init?:RequestInit)=>{
    void _init;
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return response({source_cv_id:'SOURCE_1',source_cv_version_id:'VERSION_1',cv_extraction_task_id:'TASK_CV_1',created_source:true,created_version:true,created_task:true,task_status:'running',text_extraction_status:'completed',extraction_method:'ocr',extraction_provider:'tesseract',source_file_id:null});
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes'}/>);

  await screen.findByRole('button',{name:/上传简历/});
  expect(screen.queryByText('PDF 解析方式')).not.toBeInTheDocument();
  expect(screen.queryByText('OCR 扫描件')).not.toBeInTheDocument();
  const fileInput=document.querySelector('input[type=file]')!;
  expect(fileInput).toHaveAttribute('accept','.pdf,.docx,.txt,.png,.jpg,.jpeg');
  fireEvent.change(fileInput,{target:{files:[new File(['pdf'],'resume.pdf',{type:'application/pdf'})]}});

  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))).toBe(true));
  const uploadCall=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))!;
  expect(uploadCall[1]?.method).toBe('POST');
  expect((uploadCall[1]?.body as FormData).get('use_ocr')).not.toBe('true');
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/resumes/file')||String(url).includes('/api/v1/resumes/image'))).toBe(false);
});

test('图片上传附带 use_ocr 且刷新 cvTaskId 只恢复不重新上传',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,_init?:RequestInit)=>{
    void _init;
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return response({source_cv_id:'SOURCE_1',source_cv_version_id:'VERSION_1',cv_extraction_task_id:'TASK_CV_1',created_source:true,created_version:true,created_task:true,task_status:'running',text_extraction_status:'completed',extraction_method:'ocr',extraction_provider:'paddle',source_file_id:null});
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);

  expect(await screen.findByText('字段证据审核')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))).toBe(false);

  cleanup();
  render(<Root initialPath={'/profile/resumes'}/>);
  await screen.findByRole('button',{name:/上传简历/});
  const fileInput=document.querySelector('input[type=file]')!;
  fireEvent.change(fileInput,{target:{files:[new File(['png'],'scan.png',{type:'image/png'})]}});
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))).toBe(true));
  const uploadCall=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/api/v1/source-cvs/upload-and-extract'))!;
  const form=uploadCall[1]?.body as FormData;
  expect(uploadCall[1]?.method).toBe('POST');
  expect(form.get('use_ocr')).toBe('true');
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/resumes/file')||String(url).includes('/api/v1/resumes/image'))).toBe(false);
});

test('CV 失败终态展示真实错误且不生成审核面板',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask({status:'failed',last_error_code:'CV_EXTRACTION_FAILED',last_error_message:'抽取服务不可用'}));
    return response([]);
  }));
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  // 任务卡片已移除，终态失败只展示用户可读的错误信息，不透出内部错误码
  expect(await screen.findByText('CV 抽取失败')).toBeInTheDocument();
  expect(screen.getByText('抽取服务不可用')).toBeInTheDocument();
  expect(screen.queryByText('字段证据审核')).not.toBeInTheDocument();
});

test('CV 自动重试中间态不显示失败并持续轮询',async()=>{
  localStorage.setItem('main_access_token','token');
  let polls=0;
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')){
      polls+=1;
      if(polls===1)return response(cvTask({status:'failed',retryable:true,attempt_count:1,last_error_code:'CV_EXTRACTION_PROVIDER_TIMEOUT'}));
      return response(cvTask());
    }
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  expect(await screen.findByText('正在自动重试')).toBeInTheDocument();
  expect(screen.getByText(/已自动排队下一次尝试/)).toBeInTheDocument();
  expect(screen.queryByText('抽取失败')).not.toBeInTheDocument();
  expect(screen.queryByText('CV_EXTRACTION_PROVIDER_TIMEOUT')).not.toBeInTheDocument();
  expect(await screen.findByText('字段证据审核')).toBeInTheDocument();
  expect(polls).toBeGreaterThan(1);
});

test('CV 任务达到轮询上限时保留状态并显示刷新',async()=>{
  let timerId=0;
  vi.spyOn(window,'setTimeout').mockImplementation(((handler:TimerHandler)=>{
    timerId+=1;
    if(typeof handler==='function')handler();
    return timerId;
  }) as typeof window.setTimeout);
  try{
    localStorage.setItem('main_access_token','token');
    const fetchMock=vi.fn((url:string)=>{
      if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
      if(url.endsWith('/api/v1/resumes/me'))return response([]);
      if(url.endsWith('/api/v1/matches/reports'))return response([]);
      if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_RUNNING')&&!url.endsWith('/review'))return response(cvTask({task_id:'TASK_RUNNING',status:'running'}));
      return response([]);
    });
    vi.stubGlobal('fetch',fetchMock);
    render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_RUNNING'}/>);
    await act(async()=>{await Promise.resolve()});
    expect(screen.getByText('任务仍在处理中')).toBeInTheDocument();
    expect(screen.getByText(/不会判定失败/)).toBeInTheDocument();
    expect(screen.getByRole('button',{name:/刷新状态/})).toBeInTheDocument();
    expect(screen.queryByText('抽取失败')).not.toBeInTheDocument();
  }finally{
    vi.restoreAllMocks();
  }
});

test('CV 上传失败不调用旧简历端点',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return response(null,503);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes'}/>);
  await screen.findByRole('button',{name:/上传简历/});
  const fileInput=document.querySelector('input[type=file]')!;
  fireEvent.change(fileInput,{target:{files:[new File(['bad'],'bad.pdf',{type:'application/pdf'})]}});
  expect(await screen.findByText('API 失败')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/resumes/file')||String(url).endsWith('/api/v1/resumes/image'))).toBe(false);
});

test('CV 抽取未启用时展示真实恢复指引',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/source-cvs/upload-and-extract'))return Promise.resolve({
      ok:false,
      status:409,
      statusText:'Conflict',
      json:async()=>({
        code:409,
        message:'CV extraction is disabled',
        data:null,
        details:{error_code:'CV_EXTRACTION_CONFLICT'},
        trace_id:'req_cv_disabled',
      }),
    });
    return response([]);
  }));
  render(<Root initialPath={'/profile/resumes'}/>);
  await screen.findByRole('button',{name:/上传简历/});
  const fileInput=document.querySelector('input[type=file]')!;
  fireEvent.change(fileInput,{target:{files:[new File(['pdf'],'resume.pdf',{type:'application/pdf'})]}});
  expect(await screen.findByText('简历解析服务未启用。请联系管理员启用后重试。')).toBeInTheDocument();
  expect(screen.queryByText('该简历已有历史匹配报告，为保留证据链不能删除。')).not.toBeInTheDocument();
});

test('CV 审核阻断门禁禁止确认',async()=>{
  localStorage.setItem('main_access_token','token');
  const twoFields=cvReview({reviewable_fields:[
    {field_id:'skill-1',field_type:'name',section:'skills',item_id:'skill-1',field_path:'name',field_label:'技能',original_value:'Python',suggested_value:'Python',evidence:{source_document_id:'VERSION_1',source_id:'src-1',quote:'Python',start:0,end:6,alignment:'exact',occurrence_index:0},flag_codes:[]},
    {field_id:'education-1',field_type:'degree',section:'education',item_id:'education-1',field_path:'degree',field_label:'学位',original_value:'本科',suggested_value:'本科',evidence:{source_document_id:'VERSION_1',source_id:'src-2',quote:'本科',start:7,end:9,alignment:'exact',occurrence_index:0},flag_codes:[]},
  ]});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(twoFields);
    return response([]);
  }));
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  const confirmButton=await screen.findByRole('button',{name:'确认并生成快照'});
  expect(confirmButton).toBeDisabled();
  fireEvent.mouseDown(screen.getByLabelText('skill-1 决策'));
  fireEvent.click(await screen.findByText('接受'));
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeDisabled();
  fireEvent.mouseDown(screen.getByLabelText('education-1 决策'));
  fireEvent.click((await screen.findAllByText('接受')).at(-1)!);
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeEnabled();
});

test('CV validation block 与 correct 规则阻止确认',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask({validation_conclusion:'block'}));
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview({validation:{conclusion:'block',policy_version:'cv-validation-policy.v2',validation_task_id:'vt-1',validation_report_id:'vr-1',blocking_reasons:['CV_BLOCK']}}));
    return response([]);
  }));
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  expect(await screen.findByText('验证阻断')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeDisabled();
  expect(screen.getByText('CV_BLOCK')).toBeInTheDocument();

  cleanup();
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review'))return response(cvReview({validation:{conclusion:'warn',policy_version:'cv-validation-policy.v2',validation_task_id:'vt-1',validation_report_id:'vr-1',blocking_reasons:[]}}));
    return response([]);
  }));
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  await screen.findByRole('button',{name:'确认并生成快照'});
  fireEvent.mouseDown(screen.getByLabelText('skill-1 决策'));
  fireEvent.click(await screen.findByText('修正'));
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('skill-1 修正值'),{target:{value:'Python 3'}});
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText('skill-1 修正原因'),{target:{value:'版本更精确'}});
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeEnabled();
});

test('CV 确认 409 时清空决策并重新读取 Review',async()=>{
  localStorage.setItem('main_access_token','token');
  let reviewCalls=0;
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1')&&!url.endsWith('/review'))return response(cvTask());
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/review')){reviewCalls+=1;return response(cvReview())}
    if(url.endsWith('/api/v1/cv-extraction-tasks/TASK_CV_1/confirm'))return response({code:409,message:'确认冲突'},409);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);
  await screen.findByText('字段证据审核');
  fireEvent.mouseDown(screen.getByLabelText('skill-1 决策'));
  fireEvent.click(await screen.findByText('接受'));
  fireEvent.click(screen.getByRole('button',{name:'确认并生成快照'}));
  expect(await screen.findByText('确认指纹或载荷已变化，请重新审核')).toBeInTheDocument();
  expect(screen.queryByText('审核未完成')).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'确认并生成快照'})).toBeDisabled();
  expect(reviewCalls).toBe(2);
});

test('趋势情报页展示报告证据且不展示发布阻塞提示，并恢复分析结果',async()=>{
  localStorage.setItem('main_access_token','token');
  const skill={skill_id:'SKILL_REACT',skill_name:'React',category:'software_engineering',weight:.82,confidence:.91,importance_level:'core',trend_score:.18,evidence_count:7};
  const graph={position_id:'POS_FE',position_name:'前端工程师',graph_version:'GV_2',skills:[skill],relations:[],core_responsibilities:['交付前端应用'],industry_scenarios:['互联网']};
  const trendDetail={skill_id:skill.skill_id,skill_name:skill.skill_name,trend_score:.18,evidence_count:7,growth_rate:.24,trend_direction:'rising',current_window_signal:8,historical_window_signal:5,evidence_references:['SNAP_1'],quality_flags:['low_sample'],score_explanation:{source_contributions:{github:.6,policy:.4}}};
  const report={report_id:'REPORT_2',position_id:'POS_FE',graph_version:'GV_2',time_window_start:'2026-01-01',time_window_end:'2026-06-30',current_graph:graph,skill_weight_distribution:{core:[skill]},new_skills:[],rising_skills:[skill],declining_skills:[],replaced_skills:[],skill_combo_shifts:[{from_combo:[],to_combo:['Transformer'],reason:'Remote multi-source skill combination shift'}],risks:[{risk_type:'证据覆盖',level:'medium',reason:'需补充跨来源证据'}],summary:'React 权重在当前多源窗口内上升。',status:'draft',analysis_mode:'remote_multi_source',provider:'trend_intelligence_http',provider_run_id:'RUN_2',algorithm_version:'trend.v3',formula_version:'growth.v2',skill_catalog_version:'catalog.v8',source_coverage:1,missing_sources:[],quality_flags:[],evidence_references:['SNAP_1'],unresolved_terms:['待处理术语'],skill_trends:[trendDetail],review_status:null,review_task_id:null,publication_gate:{applicable:true,eligible:false,blockers:['REVIEW_NOT_APPROVED']},created_at:'2026-07-30T01:00:00Z',updated_at:'2026-07-30T01:00:00Z'};
  const remoteReport={...report,report_id:'REPORT_3',analysis_mode:'remote_multi_source',provider:'trend_intelligence_http',provider_run_id:'RUN_3',source_coverage:.8,missing_sources:['patent'],summary:'真实多源分析已完成。',created_at:'2026-07-30T02:00:00Z'};
  let remoteReady=false;
  let reviewStage=0;
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('developer',developerPermissions));
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_FE',name:'前端工程师',category_code:'TECH',current_version_id:2,current_version_number:1,sample_count:10,skill_count:1,published_at:null,release_id:null,quality_state:'ready'}]);
    if(url.endsWith('/api/v1/positions/POS_FE/trend-analysis/tasks')&&init?.method==='POST')return response({task_id:'TASK_3',canonical_status:'running',progress:.2,result_payload:{},error_code:null,error_message:null});
    if(url.endsWith('/api/v1/trend-analysis/tasks/TASK_3')){remoteReady=true;return response({task_id:'TASK_3',canonical_status:'succeeded',progress:1,result_payload:{report_id:'REPORT_3'},error_code:null,error_message:null})}
    if(url.endsWith('/api/v1/review-tasks')&&init?.method==='POST'){reviewStage=1;return response({task_id:'REVIEW_3',object_type:'trend_report',object_id:'REPORT_3',status:'pending',reviewer_id:null,review_comment:null})}
    if(url.endsWith('/api/v1/review-tasks/REVIEW_3/claim')){reviewStage=2;return response({task_id:'REVIEW_3',object_type:'trend_report',object_id:'REPORT_3',status:'claimed',reviewer_id:'developer',review_comment:null})}
    if(url.endsWith('/api/v1/review-tasks/REVIEW_3/approve')){reviewStage=3;return response({task_id:'REVIEW_3',object_type:'trend_report',object_id:'REPORT_3',status:'approved',reviewer_id:'developer',review_comment:'已核验'})}
    if(url.endsWith('/api/v1/trend-reports/REPORT_3/publish'))return response({...remoteReport,status:'published',review_status:'approved',review_task_id:'REVIEW_3',publication_gate:{applicable:true,eligible:true,blockers:[]}});
    if(url.endsWith('/api/v1/positions/POS_FE/trend-reports')){const governed={...remoteReport,review_status:reviewStage===0?null:reviewStage===1?'pending':reviewStage===2?'claimed':'approved',review_task_id:reviewStage?'REVIEW_3':null,publication_gate:{applicable:true,eligible:reviewStage===3,blockers:reviewStage===3?[]:['REVIEW_NOT_APPROVED']}};return response({
      schema_version:'trend-delivery.v1',items:remoteReady?[governed,report]:[report],
      pagination:{page:1,page_size:20,total:remoteReady?2:1,total_pages:1},
      filters:{position_id:'POS_FE'},sort:{by:'created_at',order:'desc'},not_found_ids:[],
    })}
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/analysis/trends?positionId=POS_UNPUBLISHED'}/>);
  expect(await screen.findByRole('heading',{name:'趋势情报'})).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/portal/positions/POS_UNPUBLISHED/graph'))).toBe(false);
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/portal/positions/POS_FE/graph'))).toBe(false);
  const positionToolbar=document.querySelector('.evolution-position-toolbar');
  const analysisToolbar=document.querySelector('.analysis-toolbar');
  expect(positionToolbar).toBeTruthy();
  expect(analysisToolbar).toBeNull();
  expect(document.querySelector('.snapshot-identity .anticon')).not.toBeInTheDocument();
  expect((await screen.findAllByText('趋势分析服务 · 远程多源分析')).length).toBeGreaterThan(0);
  expect(screen.getByText('React 权重在当前多源窗口内上升。')).toBeInTheDocument();
  expect(screen.queryByText('需补充跨来源证据')).not.toBeInTheDocument();
  expect(screen.getByText('软件工程')).toBeInTheDocument();
  expect(document.querySelectorAll('.trend-change-card')).toHaveLength(1);
  const trendCard=screen.getByRole('button',{name:'查看 React 技能趋势明细'});
  expect(within(trendCard).getByText('上升')).toBeInTheDocument();
  expect(within(trendCard).queryByText('新增')).not.toBeInTheDocument();
  expect(within(trendCard).getByText('增长 +24.0%')).toBeInTheDocument();
  expect(within(trendCard).getByText('趋势分 0.18')).toBeInTheDocument();
  expect(within(trendCard).queryByText(/首份趋势报告/)).not.toBeInTheDocument();
  expect(screen.queryByText('替代关系与技能组合')).not.toBeInTheDocument();
  fireEvent.click(trendCard);
  const trendDialog=await screen.findByRole('dialog');
  expect(within(trendDialog).getByText('React · 技能趋势明细')).toBeInTheDocument();
  expect(within(trendDialog).getByText('当前窗口信号')).toBeInTheDocument();
  expect(within(trendDialog).getByText('历史窗口信号')).toBeInTheDocument();
  expect(within(trendDialog).getByText('1 条')).toBeInTheDocument();
  expect(within(trendDialog).getByText(/首份趋势报告，没有上一报告/)).toBeInTheDocument();
  expect(within(trendDialog).queryByText('证据说明')).not.toBeInTheDocument();
  expect(within(trendDialog).queryByText('技能级质量提示')).not.toBeInTheDocument();
  expect(screen.getByText('分析依据')).toBeInTheDocument();
  expect(screen.queryByText('待归一化表达')).not.toBeInTheDocument();
  expect(screen.queryByText('trend.v3')).not.toBeInTheDocument();
  expect(screen.queryByText('catalog.v8')).not.toBeInTheDocument();
  expect(screen.queryByText('SNAP_1')).not.toBeInTheDocument();
  expect(screen.queryByText(/尚未完成人工审核/)).not.toBeInTheDocument();
  expect(screen.queryByText('当前报告暂不可发布')).not.toBeInTheDocument();
  expect(screen.queryByText(/仍有未归一化技能待处理/)).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'发布报告'})).toBeDisabled();
  expect(screen.getByRole('button',{name:/运行新分析/})).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:/运行新分析/}));
  expect(await screen.findByText('趋势情报分析完成，报告已生成')).toBeInTheDocument();
  expect((await screen.findAllByText('趋势分析服务 · 远程多源分析')).length).toBeGreaterThan(0);
  expect(screen.getByText('其他来源')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/trend-analysis/tasks/TASK_3'))).toBe(true);
  const createReview=await screen.findByRole('button',{name:/创建审核任务/});await waitFor(()=>expect(createReview).toBeEnabled());fireEvent.click(createReview);
  const claimReview=await screen.findByRole('button',{name:/领取审核/});await waitFor(()=>expect(claimReview).toBeEnabled());fireEvent.click(claimReview);
  const approveReview=await screen.findByRole('button',{name:/审核通过/});await waitFor(()=>expect(approveReview).toBeEnabled());fireEvent.click(approveReview);
  const publishReport=await screen.findByRole('button',{name:/发布报告/});await waitFor(()=>expect(publishReport).not.toHaveClass('ant-btn-loading'));fireEvent.click(publishReport);
  expect((await screen.findAllByText('已发布')).length).toBeGreaterThan(0);
},30000);

test('趋势报告请求未完成时展示加载态而不是首次运行空态',async()=>{
  localStorage.setItem('main_access_token','token');
  type TestResponse=Awaited<ReturnType<typeof response>>;
  let resolveReports!:(value:TestResponse)=>void;
  const pendingReports=new Promise<TestResponse>(resolve=>{resolveReports=resolve});
  const skill={skill_id:'SKILL_REACT',skill_name:'React',category:'software_engineering',weight:.8,confidence:.9,importance_level:'core',trend_score:.1,evidence_count:3};
  const graph={position_id:'POS_FE',position_name:'前端工程师',graph_version:'2',skills:[skill],relations:[],core_responsibilities:[],industry_scenarios:[]};
  const report={report_id:'TREND_DELAYED',position_id:'POS_FE',graph_version_id:'2',time_window_start:'2026-01-01',time_window_end:'2026-06-30',current_graph:graph,skill_weight_distribution:{},new_skills:[],rising_skills:[],declining_skills:[],replaced_skills:[],skill_combo_shifts:[],risks:[],summary:'已有趋势报告已加载。',status:'draft',analysis_mode:'remote_multi_source',provider:'trend_intelligence_http',provider_run_id:'RUN_DELAYED',source_coverage:1,missing_sources:[],quality_flags:[],evidence_references:[],unresolved_terms:[],skill_trends:[],review_status:null,review_task_id:null,publication_gate:{applicable:true,eligible:false,blockers:['REVIEW_NOT_APPROVED']},created_at:'2026-07-30T01:00:00Z',updated_at:'2026-07-30T01:00:00Z'};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin'));
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_FE',name:'前端工程师',category_code:'TECH',current_version_id:2,current_version_number:1,sample_count:3,skill_count:1,published_at:null,release_id:null,quality_state:'ready'}]);
    if(url.endsWith('/api/v1/portal/positions/POS_FE/graph'))return response({position_id:'POS_FE',position:{position_id:'POS_FE',name:'前端工程师',category_code:'TECH'},skill_relations:[{relation_id:1,skill_id:'SKILL_REACT',canonical_name:'React',category_code:'software_engineering',category_name:'软件工程',weight:.8,confidence:.9,importance_level:'core',primary_modality:'required',modality_distribution:{required:1},trend_score:.1,metrics:{support_document_count:3,support_count:3,trusted_evidence_ratio:1,unknown_ratio:0}}],requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:3},view_type:'published',version_id:2});
    if(url.endsWith('/api/v1/positions/POS_FE/trend-reports'))return pendingReports;
    return response([]);
  }));
  render(<Root initialPath="/analysis/trends"/>);
  expect(await screen.findByText('正在加载趋势报告')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'运行首次分析'})).not.toBeInTheDocument();
  await act(async()=>resolveReports(await response({schema_version:'trend-delivery.v1',items:[report],pagination:{page:1,page_size:20,total:1,total_pages:1},filters:{},sort:{by:'created_at',order:'desc'},not_found_ids:[]})));
  expect(await screen.findByText('已有趋势报告已加载。')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'运行首次分析'})).not.toBeInTheDocument();
});

test('reviewer 可按 main 的角色路由查看并操作趋势报告',async()=>{
  localStorage.setItem('main_access_token','token');
  const skill={skill_id:'SKILL_REACT',skill_name:'React',category:'前端',weight:.8,confidence:.9,importance_level:'core',trend_score:.1,evidence_count:3};
  const graph={position_id:'POS_FE',position_name:'前端工程师',graph_version:'2',skills:[skill],relations:[],core_responsibilities:[],industry_scenarios:[]};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('reviewer',reviewerPermissions));
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_FE',name:'前端工程师',category_code:'TECH',current_version_id:2,current_version_number:1,sample_count:3,skill_count:1,published_at:null,release_id:null,quality_state:'ready'}]);
    if(url.endsWith('/api/v1/positions/POS_FE/trend-reports'))return response({schema_version:'trend-delivery.v1',items:[{report_id:'TREND_1',position_id:'POS_FE',graph_version_id:'2',time_window_start:null,time_window_end:null,current_graph:graph,skill_weight_distribution:{},new_skills:[],rising_skills:[],declining_skills:[],replaced_skills:[],skill_combo_shifts:[],risks:[],summary:'只读演化结果',status:'draft',analysis_mode:'remote_multi_source',provider:'trend_intelligence_http',provider_run_id:null,source_coverage:1,missing_sources:[],quality_flags:[],evidence_references:[],created_at:null,updated_at:null}],pagination:{page:1,page_size:20,total:1,total_pages:1},filters:{},sort:{by:'created_at',order:'desc'},not_found_ids:[]});
    return response([]);
  }));
  render(<Root initialPath="/analysis/trends"/>);
  expect(await screen.findByText('只读演化结果')).toBeInTheDocument();
  expect(screen.getByText('当前账号仅可查看演化结果')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/运行新分析|运行首次分析/})).not.toBeInTheDocument();
  expect(screen.getByRole('button',{name:'创建审核任务'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'发布报告'})).not.toBeInTheDocument();
});

test('有 trend.run.manage 但图谱数据未就绪时不能运行分析',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin'));
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_EMPTY',name:'空图谱岗位',category_code:'TECH',current_version_id:2,current_version_number:1,sample_count:0,skill_count:0,published_at:null,release_id:null,quality_state:'thin'}]);
    if(url.endsWith('/api/v1/positions/POS_EMPTY/trend-reports'))return response({schema_version:'trend-delivery.v1',items:[],pagination:{page:1,page_size:20,total:0,total_pages:0},filters:{position_id:'POS_EMPTY'},sort:{by:'created_at',order:'desc'},not_found_ids:[]});
    return response([]);
  }));
  render(<Root initialPath="/analysis/trends"/>);
  expect(await screen.findByText('分析数据尚未就绪')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'运行首次分析'})).toBeDisabled();
  expect(screen.getByRole('button',{name:/运行新分析/})).toBeDisabled();
});

test('能力演化报告加载跨图谱版本时间线并在切换岗位后重新加载',async()=>{
  localStorage.setItem('main_access_token','token');
  const relationFor=(weight:number)=>({relation_id:1,skill_id:'SKILL_PY',canonical_name:'Python',category_code:'LANG',category_name:'语言',weight,confidence:.9,importance_level:'core',primary_modality:'required',modality_distribution:{required:1},trend_score:.1,metrics:{support_document_count:3,support_count:3,trusted_evidence_ratio:1,unknown_ratio:0}});
  const eventsFor=(id:string,from:number,to:number)=>({position_id:id,from_version_id:from,to_version_id:to,event_type:null,versions:[{id:from,version_number:from,version_name:`图谱 v${from}`,build_run_id:from,release_id:null,rollback_from_version_id:null,created_at:`2026-0${from}-01T00:00:00Z`},{id:to,version_number:to,version_name:`图谱 v${to}`,build_run_id:to,release_id:null,rollback_from_version_id:null,is_current:true,created_at:`2026-0${to}-15T00:00:00Z`}],version_pairs:[{from_version_id:from,to_version_id:to}],events:[{event_id:`evt-${from}-${to}-skill_emergence-001`,event_type:'skill_emergence',position_id:id,from_version:from,to_version:to,source_entities:[],target_entities:[{skill_id:'SKILL_PY',canonical_name:'Python',category_code:'LANG',weight:.55,confidence:.9,importance_level:'core',primary_modality:'required',statistics:{support_document_count:3}}],confidence:.91,magnitude:.74,evidence:{lineage:{position_id:id,from_version_id:from,to_version_id:to},source_relations:[],target_relations:[{skill_id:'SKILL_PY',canonical_name:'Python',category_code:'LANG',weight:.55,confidence:.9,importance_level:'core',primary_modality:'required',statistics:{support_document_count:3}}],source:'graph_version_snapshot_diff'},reason:`skill Python (SKILL_PY) emerged: weight 0.4 -> 0.55`,detector_version:'position-evolution-events-v1',created_at:`2026-0${to}-15T00:00:00Z`,metrics:{before_weight:.4,after_weight:.55,delta:.15},metadata:{atomic_signals:['skill_emergence']}}],count:1});
  const capabilityFor=(id:string,name:string,from:number,to:number)=>{
    const collection=eventsFor(id,from,to);
    const relation=relationFor(.55);
    return {schema_version:'capability-evolution.v1',position_id:id,frames:collection.versions.map((version,index)=>({...version,snapshot:{position:{position_id:id,name,category_code:'TECH'},skill_relations:[{...relation,weight:index===0 ? .4 : .55,metrics:{...relation.metrics,support_document_count:index===0?2:3}}]}})),comparisons:[{from_version_id:from,to_version_id:to,added:[],removed:[],changed:[{skill_id:'SKILL_PY',changed_fields:['weight'],change_sources:['graph_version_snapshot']}],summary:{added:0,removed:0,changed:1,support_changed:1,context_changed:0},context_change_fields:[]}],events:collection.events,comparison_count:1,event_count:1,frame_count:2};
  };
  const calls:string[]=[];
  const fetchMock=vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/positions'))return response([
      {position_id:'POS_AI',name:'AI 工程师',category_code:'TECH',current_version_id:2,current_version_number:1,sample_count:3,skill_count:1,published_at:null,release_id:null,quality_state:'ready'},
      {position_id:'POS_ML',name:'ML 工程师',category_code:'TECH',current_version_id:4,current_version_number:2,sample_count:3,skill_count:1,published_at:null,release_id:null,quality_state:'ready'},
    ]);
    if(url.includes('/api/v1/portal/admin/knowledge-graph/positions/POS_AI/capability-evolution'))return response(capabilityFor('POS_AI','AI 工程师',1,2));
    if(url.includes('/api/v1/portal/admin/knowledge-graph/positions/POS_ML/capability-evolution'))return response(capabilityFor('POS_ML','ML 工程师',3,4));
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/analysis/evolution"/>);
  expect(await screen.findByText('新技能出现')).toBeInTheDocument();
  expect(screen.getAllByText('Python').length).toBeGreaterThan(0);
  expect(screen.getAllByText('91%').length).toBeGreaterThan(0);
  expect(screen.getByText('74%')).toBeInTheDocument();
  expect(screen.getByText('V1 → V2')).toBeInTheDocument();
  expect(calls.some(url=>url.includes('/api/v1/portal/admin/knowledge-graph/positions/POS_AI/capability-evolution'))).toBe(true);
  expect(calls.some(url=>url.includes('/api/v1/portal/positions/POS_AI/graph'))).toBe(false);
  expect(document.querySelector('.analysis-toolbar')).toBeNull();
  // list 已包含完整 Event，禁止初始化后逐个调用 detail（无 N+1）
  expect(calls.some(url=>/evolution-events\/evt-/.test(url))).toBe(false);
  // 切换岗位后直接重新加载版本对比。
  fireEvent.mouseDown(screen.getAllByRole('combobox')[0]);
  fireEvent.click(await screen.findByText('ML 工程师'));
  await waitFor(()=>expect(calls.some(url=>url.includes('/api/v1/portal/admin/knowledge-graph/positions/POS_ML/capability-evolution'))).toBe(true));
  expect(calls.some(url=>url.includes('/api/v1/portal/positions/POS_ML/graph'))).toBe(false);
  expect(await screen.findByText('新技能出现')).toBeInTheDocument();
  expect(screen.getByText('V3 → V4')).toBeInTheDocument();
},30000);

test('岗位列表只通过主后端 portal 读取已发布数据',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):url.endsWith('/api/v1/portal/positions')?response([{position_id:'main-pos-1',knowledge_graph_position_id:'kg-pos-9',name:'后端工程师',category_code:'TECH',current_version_id:37,current_version_number:2}]):response([]));
  vi.stubGlobal('fetch',fetchMock);render(<Root/>);
  expect(await screen.findByText('后端工程师')).toBeInTheDocument();expect(screen.getByText('当前发布版本 #2')).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('knowledge-graph'))).toBe(false);
});

test('Evidence 响应由主后端组合权威原文与图谱坐标',async()=>{
  const relation={relation_id:5,skill_id:'SKILL_PYTHON',canonical_name:'Python',category_code:'LANG',weight:.8,confidence:.9,importance_level:'core',primary_modality:'required' as const,modality_distribution:{required:1},trend_score:null,metrics:{support_document_count:1,support_count:1,trusted_evidence_ratio:1,unknown_ratio:0}};
  const support=[{support_id:1,document_id:'JD1',requirement_id:'r1',modality:'required',evidence:{id:1,quote:'Python',start:2,end:8,alignment:'exact',occurrence_index:0},original_requirement:{text:'会 Python'},normalized_skill:{id:1,skill_id:'SKILL_PYTHON',canonical_name:'Python',source_name:'Python',resolution_status:'resolved'},source:{document_id:'JD1',raw_text:'会用Python开发'}}];
  const fetchMock=vi.fn((url:string)=>url.endsWith('/api/v1/portal/evidence/relations/5')?response(support):response([]));
  vi.stubGlobal('fetch',fetchMock);render(<EvidenceViewer relation={relation} onClose={()=>undefined}/>);
  expect(await screen.findByText('Python',{selector:'mark'})).toBeInTheDocument();
  expect(screen.getByText('编程语言')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
});

test('新兴岗位公开列表与图谱共享同一登录和导航',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):url.endsWith('/api/v1/portal/emerging-assets')?response([{emerging_id:'formal:11',cluster_id:'11',position_name:'生成式 AI 应用工程师',core_responsibilities:[],required_skills:[],bonus_skills:[],industry_scenarios:[],germination_score:.82,score_dimensions:{},evidence_jd_ids:['JD1'],support_jd_count:1,status:'discovered'}]):url.endsWith('/api/v1/portal/emerging-position-signals')?response({signals:[],observed_from:null,observed_to:null,source_contract:'published-jd-fact.v2',projection_version:'recent-position-signals.v1'}):response([]));
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath={'/emerging'}/>);
  expect(await screen.findByText('生成式 AI 应用工程师')).toBeInTheDocument();expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/emerging-assets',expect.anything());
});

test('失效的主系统 token 被清除并返回公开首页',async()=>{
  localStorage.setItem('main_access_token','expired');vi.stubGlobal('fetch',vi.fn(()=>response(null,401)));render(<Root/>);
  expect(await screen.findByRole('heading',{name:/持续感知职业变化/})).toBeInTheDocument();
  await waitFor(()=>expect(document.querySelector('.home-hero__background')).toBeTruthy());
  await waitFor(()=>expect(localStorage.getItem('main_access_token')).toBeNull());
});

test('已登录会话在业务请求返回 401 时清理身份并进入登录页',async()=>{
  localStorage.setItem('main_access_token','expired-during-session');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response(null,401);
    return response([]);
  }));

  render(<Root initialPath={'/profile/resumes?cvTaskId=TASK_CV_1'}/>);

  expect(await screen.findByLabelText('账号登录')).toBeInTheDocument();
  expect(localStorage.getItem('main_access_token')).toBeNull();
});

test('/admin/build 无参数时只加载岗位，并通过构建记录按钮按 URL 岗位加载构建记录',async()=>{
  localStorage.setItem('main_access_token','token');window.history.pushState({},'', '/admin/build');
  const calls:string[]=[];
  const fetchMock=vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.includes('/api/v1/portal/admin/catalog/positions'))return response(catalogPositionPage([{position_id:'POS_AI',position_name:'AI 岗位',source_emerging_position_id:null,status:'published',graph_onboarding_status:'mapped',created_at:null,updated_at:null}]));
    if(url.endsWith('/api/v1/portal/admin/knowledge-graph/positions/POS_AI/build-runs'))return response([{id:5,build_version:1,status:'succeeded',summary:{included_samples:3}}]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root/>);
  await screen.findByRole('button',{name:'构建记录'});
  expect(calls.some(url=>url.includes('/build-runs'))).toBe(false);
  fireEvent.click(await screen.findByRole('button',{name:'构建记录'}));
  await waitFor(()=>expect(window.location.pathname).toBe('/admin/build/records'));
  await waitFor(()=>expect(window.location.search).toBe('?positionId=POS_AI'));
  expect(await screen.findByText('构建完成')).toBeInTheDocument();
});

test('VersionWorkbench 从 URL 加载并在同一组件切换岗位',async()=>{
  localStorage.setItem('main_access_token','token');window.history.pushState({},'', '/admin/versions?positionId=POS_A');
  const calls:string[]=[];
  const versionsA=[{id:1,version_number:1,rollback_from_version_id:null,created_at:'2026-01-01'},{id:2,version_number:2,rollback_from_version_id:null,created_at:'2026-02-01'}];
  const fetchMock=vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.includes('/api/v1/portal/admin/catalog/positions'))return response(catalogPositionPage([
      {position_id:'POS_A',position_name:'岗位 A',source_emerging_position_id:null,status:'published',graph_onboarding_status:'mapped',created_at:null,updated_at:null},
      {position_id:'POS_B',position_name:'岗位 B',source_emerging_position_id:null,status:'published',graph_onboarding_status:'mapped',created_at:null,updated_at:null},
    ]));
    if(url.includes('/api/v1/portal/admin/knowledge-graph/positions/POS_A/versions/diff'))return response({added:[],removed:[],changed:[],context_changes:{},evidence_changes:[{skill_id:'SKILL_A',before:[],after:[{quote:'DIFF_POS_A'}]}]});
    if(url.endsWith('/api/v1/portal/admin/knowledge-graph/positions/POS_A/versions'))return response(versionsA);
    if(url.endsWith('/api/v1/portal/admin/knowledge-graph/positions/POS_B/versions'))return response([{id:3,version_number:1,rollback_from_version_id:null,created_at:'2026-03-01'}]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root/>);
  await waitFor(()=>expect(calls).toContain('/api/v1/portal/admin/knowledge-graph/positions/POS_A/versions'));
  const searchInput=await screen.findByPlaceholderText('搜索岗位名称');
  fireEvent.change(searchInput,{target:{value:'岗位'}});
  fireEvent.keyDown(searchInput,{key:'Enter',code:'Enter'});
  const versionButtons=await waitFor(()=>{
    const buttons=screen.getAllByRole('button',{name:'版本管理'});
    expect(buttons.length).toBeGreaterThanOrEqual(2);
    return buttons;
  });
  fireEvent.click(versionButtons[1]);
  await waitFor(()=>expect(window.location.search).toBe('?positionId=POS_B'));
  await waitFor(()=>expect(calls).toContain('/api/v1/portal/admin/knowledge-graph/positions/POS_B/versions'));
  expect(calls.filter(url=>url.endsWith('/positions/POS_B/versions')).length).toBe(1);
});

test('草稿页面使用 build_run_id 查询且保留编码后的岗位路径',async()=>{
  localStorage.setItem('main_access_token','token');
  const calls:string[]=[];
  const graph={position_id:'POS A',position:{position_id:'POS A',name:'空格岗位',category_code:'TECH'},skill_relations:[],requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:1},view_type:'draft',draft_id:9,build_run_id:42,base_version_id:7,build_info:{build_run_id:42,build_version:3,status:'draft',window_start:null,window_end:null,config_snapshot:{},summary:{included_samples:1},created_at:'2026-07-29'}};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{calls.push(String(url));if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));if(url.endsWith('/api/v1/portal/admin/knowledge-graph/drafts/42/graph'))return response(graph);return response([])}));
  render(<Root initialPath="/positions/POS%20A?buildRunId=42"/>);
  expect(await screen.findByText(/尚未发布/)).toBeInTheDocument();
  expect(calls).toContain('/api/v1/portal/admin/knowledge-graph/drafts/42/graph');
  expect(calls.some(url=>url.includes('/drafts/9/graph'))).toBe(false);
});

test.each(['personal_user','enterprise_user','reviewer'])('%s 可只读浏览岗位图谱且看不到 KG 治理编辑入口',async role=>{
  localStorage.setItem('main_access_token','token');
  const calls:string[]=[];
  const graph={position_id:'POS_FE',position:{position_id:'POS_FE',name:'前端工程师',category_code:'TECH'},skill_relations:[{relation_id:1,skill_id:'SKILL_REACT',canonical_name:'React',category_code:'FRONTEND',category_name:'前端',weight:.8,confidence:.9,importance_level:'core',primary_modality:'required',modality_distribution:{required:1},metrics:{support_document_count:3,support_count:3,trusted_evidence_ratio:1,unknown_ratio:0}}],requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:3},view_type:'published',version_id:2};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{calls.push(String(url));if(url.endsWith('/api/v1/auth/me'))return response(user(role));if(url.endsWith('/api/v1/portal/positions/POS_FE/graph'))return response(graph);return response([])}));
  render(<Root initialPath="/positions/POS_FE?buildRunId=42"/>);
  expect(await screen.findByText('前端工程师能力图谱')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/列\s*表/})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'React'}));
  expect(screen.queryByRole('button',{name:/编辑/})).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:/构建草稿/})).not.toBeInTheDocument();
  expect(calls.some(url=>url.includes('/portal/admin/knowledge-graph/drafts/'))).toBe(false);
});

test('点击技能节点可打开图谱功能区',async()=>{
  localStorage.setItem('main_access_token','token');
  const graph={position_id:'POS_GRAPH_VIEW',position:{position_id:'POS_GRAPH_VIEW',name:'前端工程师',category_code:'TECH'},skill_relations:[{relation_id:1,skill_id:'SKILL_REACT',canonical_name:'React',category_code:'FRONTEND',category_name:'前端',classifications:[{facet:'concept_class',code:'technology',name_zh:'技术实体',is_primary:true},{facet:'technology_kind',code:'framework',name_zh:'框架',is_primary:true}],weight:.8,confidence:.9,importance_level:'core',primary_modality:'required',modality_distribution:{required:1},metrics:{support_document_count:3,support_count:3,trusted_evidence_ratio:1,unknown_ratio:0}}],requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:3},view_type:'published',version_id:37,build_info:{build_run_id:42,build_version:2,base_build_version:1,status:'published',window_start:null,window_end:null,config_snapshot:{},summary:{included_samples:3},created_at:'2026-08-25'}};
  const explanation={relation_id:1,position_id:'POS_GRAPH_VIEW',skill_id:'SKILL_REACT',statistics:{supporting_jd_count:3,deduplicated_jd_count:3,enterprise_count:2,source_count:2},sources:[],evidence:[],weight_basis:{auto:.8,manual:null,final:.8},confidence_basis:{auto:.9,manual:null,final:.9},quality_impact:{raw_frequency:.7,adjusted_frequency:.8},manual_modification_history:[],version_id:2,is_current:true};
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user('admin',adminPermissions)):url.endsWith('/api/v1/portal/positions/POS_GRAPH_VIEW/graph')?response(graph):url.includes('/api/v1/portal/admin/knowledge-graph/relations/1/explanation')?response(explanation):response([])));
  render(<Root initialPath="/positions/POS_GRAPH_VIEW"/>);
  await screen.findByText('前端工程师能力图谱');
  expect(screen.getByText('岗位画像明细')).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:'技能（1）'})).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:'职责（0）'})).toBeInTheDocument();
  expect(screen.getByRole('tab',{name:'招聘与用工信息（0）'})).toBeInTheDocument();
  expect(screen.getByText(/正式发布版本.*2/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('技术栈',{exact:true}));
  expect(screen.getByText('技术栈视图')).toBeInTheDocument();
  expect(screen.getByText('框架')).toBeInTheDocument();
  fireEvent.click(screen.getByText('能力级别',{exact:true}));
  expect(screen.getByText('能力级别视图')).toBeInTheDocument();
  expect(screen.getAllByText('核心').length).toBeGreaterThan(0);
  fireEvent.click(screen.getByText('关系图',{exact:true}));
  const graphViewSelect=screen.getByRole('combobox',{name:'图谱视图'});
  expect(graphViewSelect.closest('.ant-select')).toHaveTextContent('逐层探索');
  fireEvent.mouseDown(graphViewSelect);
  await screen.findByText('技能全景');
  expect([...document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option-content')].map(option=>option.textContent)).toEqual(['逐层探索','技能全景','层级树']);
  fireEvent.click(screen.getByRole('button',{name:'React'}));
  fireEvent.click(await screen.findByRole('button',{name:'查看解释'}));
  expect((await screen.findAllByText('React · 关系解释')).length).toBeGreaterThan(0);
  expect(screen.getAllByRole('button',{name:/确\s*定/}).length).toBeGreaterThan(0);
});

test('岗位图谱紧凑展示要求通胀摘要并按需展开诊断',async()=>{
  localStorage.setItem('main_access_token','token');
  const graph={position_id:'POS_GRAPH_INFLATION',position:{position_id:'POS_GRAPH_INFLATION',name:'后端工程师',category_code:'TECH'},skill_relations:[],requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:2},view_type:'published',version_id:37};
  const requirementInflation={
    position_id:'POS_GRAPH_INFLATION',graph_version:'37',graph_version_id:37,
    requirement_inflation:{
      algorithm_version:'requirement-strength-calibration.v1',scope:'required_skills',
      summary:{jd_count:2,total_required_requirement_count:5,market_supported_count:2,enterprise_specific_count:2,inflation_risk_count:1,jd_risk_level_counts:{low:1,medium:1,high:0}},
      jd_diagnostics:[{
        document_id:'JD-001',enterprise_name:'示例企业',source_name:'招聘平台',required_skill_count:3,inflation_risk_skill_count:1,inflation_ratio:1/3,risk_level:'medium',
        requirements:[{
          requirement_id:'r1',skill_id:'SKILL_TF',skill_name:'TensorFlow',evidence_id:1,jd_modality:'required',market_status:'inflation_risk',inflation_risk:true,
          reason_codes:['LOW_MARKET_REQUIRED_PREVALENCE','INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT'],
          market:{support_ratio:.02,supporting_jd_count:1,required_supporting_jd_count:1,required_prevalence:.02,required_purity:1,enterprise_count:1,source_count:1,leave_one_out_required_jd_count:0,leave_one_out_enterprise_count:0,leave_one_out_source_count:0},
        }],
      }],
    },
  };
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):url.endsWith('/api/v1/portal/positions/POS_GRAPH_INFLATION/graph')?response(graph):url.endsWith('/api/v1/portal/positions/POS_GRAPH_INFLATION/requirement-inflation')?response(requirementInflation):response([])));
  render(<Root initialPath="/positions/POS_GRAPH_INFLATION"/>);
  expect(await screen.findByText('岗位要求校准')).toBeInTheDocument();
  expect(screen.getByText('1 项')).toBeInTheDocument();
  expect(screen.getByText('1 份')).toBeInTheDocument();
  expect(screen.queryByText('中风险')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'查看诊断'}));
  expect(await screen.findByText('JD 1')).toBeInTheDocument();
  expect(screen.queryByText('JD-001')).not.toBeInTheDocument();
  expect(screen.queryByText('示例企业')).not.toBeInTheDocument();
  expect(screen.queryByText('招聘平台')).not.toBeInTheDocument();
  expect(await screen.findByText('TensorFlow')).toBeInTheDocument();
  expect(screen.getByText(/同类 JD 必备率 2%/)).toBeInTheDocument();
  expect(screen.getByText(/跨企业支持不足/)).toBeInTheDocument();
});

test('映射管理可搜索候选并确认未映射技能',async()=>{
  localStorage.setItem('main_access_token','token');
  const calls:Array<{url:string;init?:RequestInit}>=[];
  vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit)=>{
    calls.push({url:String(url),init});
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.includes('/api/v1/portal/admin/knowledge-graph/mappings?entity_type=position'))return response([]);
    if(url.includes('/api/v1/portal/admin/knowledge-graph/mappings?entity_type=skill'))return response([{entity_type:'skill',main_system_id:'main-skill-1',source_name:'Python',knowledge_graph_id:null,sync_status:'unmapped',last_error_code:null,last_error_message:null,last_trace_id:null,updated_at:null}]);
    if(url.includes('/mapping-candidates?entity_type=skill'))return response([{entity_type:'skill',knowledge_graph_id:'KG_PY',name:'Python',status:'active'}]);
    if(url.includes('/mappings/skill/main-skill-1'))return response({entity_type:'skill',main_system_id:'main-skill-1',source_name:'Python',knowledge_graph_id:'KG_PY',sync_status:'confirmed'});
    return response([]);
  }));
  render(<Root initialPath="/admin/mappings"/>);
  expect((await screen.findAllByText('岗位与技能对应关系')).length).toBeGreaterThan(0);
  fireEvent.mouseDown((await screen.findAllByRole('combobox'))[0]);fireEvent.click(await screen.findByText('技能名称对应'));
  fireEvent.click(await screen.findByRole('button',{name:'选择对应项'}));
  const boxes=await screen.findAllByRole('combobox');fireEvent.mouseDown(boxes.at(-1)!);fireEvent.click((await screen.findAllByText('Python')).at(-1)!);
  fireEvent.click(screen.getByRole('button',{name:'确认对应关系'}));
  await waitFor(()=>expect(calls.some(call=>call.url.includes('/mappings/skill/main-skill-1')&&call.init?.method==='PUT')).toBe(true));
},30000);

test('匹配报告页展示血缘版本、正式分数、Gap、双方 Evidence 回跳与 stale',async()=>{
  localStorage.setItem('main_access_token','token');
  const report=matchReportFixture({status:'stale',stale:true,stale_reason_codes:['INPUT_FINGERPRINT_CHANGED']});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(report);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_id:'position-1',position_name:'AI推理优化工程师'})]);
    return response([]);
  }));
  render(<Root initialPath={'/matching/reports/evaluation-1'}/>);
  expect(await screen.findByText('岗位匹配报告')).toBeInTheDocument();
  await waitFor(()=>expect(screen.getByText('这份报告需要重新计算')).toBeInTheDocument());
  expect(screen.getByText('68')).toBeInTheDocument();
  expect(screen.getByText('得分原因')).toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'学习路径'})).toBeInTheDocument();
  expect(screen.getByText(/全部技能差距（1）/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/技术与审计信息/}));
  expect(await screen.findByText('GRAPH_V1')).toBeInTheDocument();
  expect(screen.getByText('简历或岗位信息已更新')).toBeInTheDocument();
  expect(screen.getAllByText('确定性可解释匹配（第 5 版）').length).toBeGreaterThan(0);
  expect(screen.getByText('匹配计算服务')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'重新匹配'})).toBeInTheDocument();
  expect(screen.queryByText('岗位目标要求')).not.toBeInTheDocument();
},30000);

test('匹配报告突出综合匹配度并将差距与行动分数显示为整数',async()=>{
  localStorage.setItem('main_access_token','token');
  const report=matchReportFixture();
  report.gap_analysis.prioritized_gaps[0].priority_score=64.8529;
  report.gap_analysis.candidate_actions=[{
    action_id:'action-diffusion',
    action_type:'add_project_experience',
    skill_id:null,
    canonical_name:'Diffusio综合实践',
    target_requirement_ids:['req-python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'gap-learning-hours.v1',
    estimated_score_delta:5.2521,
    deliverable:'完成 diffution 模型实践',
    acceptance_criteria:['提交可运行结果'],
  }];
  report.gap_analysis.learning_routes=[{
    route_type:'budget_max_gain',
    action_ids:['action-diffusion'],
    total_cost_hours:12,
    baseline_score:68,
    modeled_final_score:81.6554,
    modeled_score_delta:13.6554,
    final_score:81.6554,
    projected_match_gain:13.6554,
    confidence_gain:null,
    target_reachable:true,
    final_recommendation:'potential_match',
    remaining_blocker_ids:[],
    path_refs:[],
    algorithm_version:'learning-route.v1',
  }];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(report);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_id:'position-1',position_name:'AI推理优化工程师'})]);
    return response([]);
  }));

  render(<Root initialPath={'/matching/reports/evaluation-1'}/>);

  expect(await screen.findByText('岗位匹配报告')).toBeInTheDocument();
  expect(await screen.findByRole('heading',{name:'人工智能推理优化工程师'})).toBeInTheDocument();
  expect(screen.getByLabelText('综合匹配度 68 分')).toBeInTheDocument();
  expect(screen.getByText('匹配评价')).toBeInTheDocument();
  expect(screen.getByText(/具备一定匹配基础|能力积累|部分能力交集/)).toBeInTheDocument();
  expect(screen.queryByText(/本次结论为/)).not.toBeInTheDocument();
  expect(screen.queryByText('维度说明')).not.toBeInTheDocument();
  for(const label of ['总览','能力匹配','差距与行动','证据与可信度'])expect(screen.getByRole('button',{name:label})).toBeInTheDocument();
  expect(screen.getByText('65')).toBeInTheDocument();
  expect(screen.queryByText('64.8529')).not.toBeInTheDocument();
  expect(screen.queryByText('预计 +5')).not.toBeInTheDocument();
  expect(screen.queryByText(/预计提升 14/)).not.toBeInTheDocument();
  expect(screen.getAllByText(/Diffusion综合实践/).length).toBeGreaterThan(0);
  expect(screen.queryByText(/diffution/i)).not.toBeInTheDocument();
});

test('假设分析路线行动组合显示具体岗位要求而非占位文本',async()=>{
  localStorage.setItem('main_access_token','token');
  const report=matchReportFixture();
  report.gap_analysis.candidate_actions=[{
    action_id:'learn-python',
    action_type:'add_skill',
    skill_id:'skill_python',
    canonical_name:null,
    target_requirement_ids:['req-python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:12,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  },{
    action_id:'project-python',
    action_type:'add_project_experience',
    skill_id:null,
    canonical_name:null,
    target_requirement_ids:['req-python'],
    responsibilities:[],
    business_scenarios:[],
    path_refs:[],
    estimated_hours:24,
    requires_action_ids:[],
    supersedes_action_ids:[],
    cost_model:'cost-band.v1',
  }];
  report.gap_analysis.learning_routes=[{
    route_type:'budget_max_gain',
    action_ids:['learn-python','project-python'],
    total_cost_hours:36,
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
  }];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(report);
    if(url.endsWith('/api/v1/matches/positions'))return response([]);
    return response([]);
  }));
  render(<Root initialPath="/matching/reports/evaluation-1"/>);
  await screen.findByText('岗位匹配报告');
  expect(await screen.findByText('Python 语言 + Python 语言')).toBeInTheDocument();
  expect(screen.queryByText('岗位能力要求')).not.toBeInTheDocument();
  expect(screen.queryByText('当前没有可评分的维度，暂不生成雷达图')).not.toBeInTheDocument();
  expect(screen.queryByText('服务未返回可用维度评分')).not.toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'模拟所选行动'})).not.toBeInTheDocument();
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

test('匹配报告页只提交正式评分使用的关键 Evidence 并展示删除重算结果',async()=>{
  localStorage.setItem('main_access_token','token');
  const report=matchReportFixture();
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1/evidence-deletions')&&init?.method==='POST')return response({
      generation_status:'completed',
      deletion_run_id:'deletion-run-1',
      deletion_kind:'critical',
      deleted_evidence_source_ids:['snapshot:1'],
      critical_evidence_source_ids:['snapshot:1'],
      noncritical_evidence_source_ids:[],
      explanation_factors:[],
      baseline_evaluation:null,
      ablated_evaluation:null,
      baseline_gap_analysis:null,
      ablated_gap_analysis:null,
      baseline_score:68,
      ablated_score:42,
      retained_only_score:68,
      score_delta:-26,
      dimension_deltas:[],
      baseline_hard_gate_status:'passed',
      ablated_hard_gate_status:'passed',
      hard_gate_delta:null,
      added_gap_ids:['required_skill_missing:req-python'],
      removed_gap_ids:[],
      added_action_ids:['learn-req-python'],
      removed_action_ids:[],
      comprehensiveness:.3824,
      sufficiency:1,
      unsupported_reason_rate:0,
      faithfulness_status:'faithful',
      baseline_evaluation_id:'evaluation-1',
      cv_profile_version:'v1',
      position_profile_version:'v1',
      scoring_algorithm_version:'deterministic-scoring.v1',
      scoring_config_version:'scoring-config.v1',
      classification_policy_version:'explanation-factor-policy.v1',
      stability_threshold_points:1,
      hypothetical:true,
      algorithm_version:'evidence-deletion-recompute.v1',
    });
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(report);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);

  render(<Root initialPath={'/matching/reports/evaluation-1'}/>);
  expect(await screen.findByText('岗位匹配报告')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/技术与审计信息/}));
  expect(await screen.findByText('解释忠实度证据删除测试')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox',{name:'关键证据 1'}));
  fireEvent.click(screen.getByRole('button',{name:'运行删除重算'}));

  expect((await screen.findAllByText('已记录')).length).toBeGreaterThan(0);
  expect(screen.getByText('解释与证据一致')).toBeInTheDocument();
  const request=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/evidence-deletions'));
  expect(request).toBeTruthy();
  expect(JSON.parse(String((request?.[1] as RequestInit).body))).toEqual({
    deletion_kind:'critical',
    evidence_source_ids:['snapshot:1'],
  });
});

test('未知 Evidence 来源不猜路由并显示 Contract 原因',async()=>{
  localStorage.setItem('main_access_token','token');
  const unknownEvidence={
    evidence_id:'evidence-unknown',
    source_object_type:'skill_relation',
    source_object_id:'relation-1',
    source_document_id:'graph-1',
    quote:'关系证据',
    location_start:0,
    location_end:4,
    alignment:'exact',
    occurrence_index:0,
    graph_version_id:null,
    graph_version:'GRAPH_V1',
    business_version:null,
    source_version:'GRAPH_V1',
    tenant_ref:'tenant-a',
    permission_scope:'personal:user-1',
  };
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/rag/evidence'))return response({
      contract_version:'evidence-rag-response.v1',
      status:'answered',
      answer:'关系证据来自冻结图谱。',
      references:[unknownEvidence],
      provider:'main-system-bff',
      model:'deepseek-v4-flash',
      model_version:'deepseek-evidence-rag-answer.v1',
      trace_id:'trace-unknown-evidence',
      error:null,
      explanation_only:true,
      graph_version_id:null,
      graph_version:'GRAPH_V1',
      business_version:null,
      permission:{user_id:'user-1',tenant_ref:'tenant-a',permission_scope:'personal:user-1',assembled_by:'main-system-bff'},
    });
    if(url.endsWith('/api/v1/rag/evidence/citations/resolve'))throw new Error('CITATION_UNSUPPORTED');
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/evidence/assistant?objectType=matching_evaluation&objectId=evaluation-1&objectVersion=GRAPH_V1&versionKind=graph_version&evidenceTypes=matching_evidence&returnTo=%2Fmatching%2Freports%2Fevaluation-1"/>);
  fireEvent.change(await screen.findByLabelText('问题'),{target:{value:'关系证据来自哪里？'}});
  fireEvent.click(screen.getByRole('button',{name:/发送/}));
  fireEvent.click(await screen.findByRole('button',{name:/查看全部来源/}));
  expect(await screen.findByText('关系证据')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'查看上下文'})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('来源上下文')).not.toBeInTheDocument();
  expect(screen.getByText('关系证据')).toBeInTheDocument();
  expect(screen.queryByText('CITATION_UNSUPPORTED')).not.toBeInTheDocument();
  expect(screen.queryAllByText('查看原文并定位证据').length).toBe(0);
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/rag/evidence',expect.objectContaining({method:'POST'}));
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/citations/resolve'))).toBe(false);
},30000);

test('匹配任务创建一次并恢复成功后进入唯一 Evaluation 页',async()=>{
  localStorage.setItem('main_access_token','token');
  let createCalls=0;
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/tasks')&&init?.method==='POST'){
      createCalls+=1;
      return response({task_id:'MATCH_TASK_1',status:'pending',progress:0,result_reference:null,error_code:null,error_message:null});
    }
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1'))return response({task_id:'MATCH_TASK_1',status:'succeeded',progress:100,evaluation_id:'evaluation-1',result_reference:'matching_evaluation:evaluation-1',result_payload:{evaluation_id:'evaluation-1'},error_code:null,error_message:null});
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/matching'}/>);
  const runButton=await screen.findByRole('button',{name:/运行匹配/});
  await waitFor(()=>expect(runButton).toBeEnabled());
  fireEvent.click(runButton);
  expect(await screen.findByText('岗位匹配报告')).toBeInTheDocument();
  expect(createCalls).toBe(1);
  const createCall=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/api/v1/matches/tasks')&&(init as RequestInit).method==='POST')!;
  expect((createCall[1] as RequestInit).headers).toBeTruthy();
  const headers=(createCall[1] as RequestInit).headers as Headers;
  expect(headers.get('Idempotency-Key')).toMatch(/^personal-run:RES_1:POS_AI:[0-9a-f-]{36}$/i);
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/tasks/MATCH_TASK_1'))).toBe(true);
});

test('无效匹配报告编号显示可见错误页且不请求 null 资源',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/learning-paths'))return response([]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/matching/reports/null"/>);
  expect(await screen.findByRole('heading',{name:'匹配报告加载失败'})).toBeInTheDocument();
  expect(screen.getByText('匹配报告编号无效，请返回岗位匹配重新选择报告。')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'返回岗位匹配'})).toBeInTheDocument();
  expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/reports/null'))).toBe(false);
});

test('匹配任务失败终态显示真实错误且不跳转',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/tasks')&&init?.method==='POST')return response({task_id:'MATCH_TASK_1',status:'failed',progress:0,error_code:'MATCH_FAILED',error_message:'匹配服务不可用',result_reference:null});
    return response([]);
  }));
  render(<Root initialPath={'/matching'}/>);
  const runButton=await screen.findByRole('button',{name:/运行匹配/});
  await waitFor(()=>expect(runButton).toBeEnabled());
  fireEvent.click(runButton);
  expect((await screen.findAllByText('匹配服务不可用')).length).toBeGreaterThan(0);
  expect(screen.queryByText('岗位匹配报告')).not.toBeInTheDocument();
});

test('匹配任务长时间运行时持续自动跟踪并保留手动刷新入口',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition()]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/tasks')&&init?.method==='POST')return response({task_id:'MATCH_TASK_1',status:'running',progress:0,error_code:null,error_message:null});
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1'))return response({task_id:'MATCH_TASK_1',status:'running',progress:0,error_code:null,error_message:null});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/matching'}/>);
  const runButton=await screen.findByRole('button',{name:/运行匹配/});
  await waitFor(()=>expect(runButton).toBeEnabled());
  let timerId=0;
  vi.spyOn(window,'setTimeout').mockImplementation(((handler:TimerHandler)=>{
    timerId+=1;
    if(typeof handler==='function')handler();
    return timerId;
  }) as typeof window.setTimeout);
  try{
    fireEvent.click(runButton);
    await act(async()=>{await Promise.resolve()});
    expect(screen.getByText('匹配任务')).toBeInTheDocument();
    expect(screen.getByText('系统正在自动跟踪进度，完成后将自动打开匹配报告，无需手动刷新。')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:/刷新匹配状态/})).toBeInTheDocument();
    // 轮询不再在 20 次后停止，而是持续跟踪直到任务到达终态。
    expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith('/api/v1/matches/tasks/MATCH_TASK_1')).length).toBeGreaterThan(20);
    expect(screen.queryByText('岗位匹配报告')).not.toBeInTheDocument();
  }finally{
    vi.restoreAllMocks();
  }
});

test('运行中的匹配任务提供放弃并重新运行入口',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_name:'大模型算法工程师'})]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1/restart')&&init?.method==='POST')return response({task_id:'MATCH_TASK_2',status:'pending',progress:0,error_code:null,error_message:null});
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1'))return response({task_id:'MATCH_TASK_1',status:'running',progress:0,error_code:null,error_message:null});
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_2'))return response({task_id:'MATCH_TASK_2',status:'pending',progress:0,error_code:null,error_message:null});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/matching?resumeId=resume-1&positionId=POS_AI&matchTaskId=MATCH_TASK_1'}/>);
  expect(await screen.findByRole('button',{name:'放弃任务'})).toBeInTheDocument();
  expect(screen.getAllByText('匹配任务')).toHaveLength(1);
  expect((screen.getAllByText('数据处理中')).length).toBeGreaterThan(0);
  expect(screen.queryByText('任务记录：已创建')).not.toBeInTheDocument();
  const restart=await screen.findByRole('button',{name:'放弃并重新运行'});
  fireEvent.click(restart);
  fireEvent.click(await screen.findByRole('button',{name:'放弃并重跑'}));
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/matches/tasks/MATCH_TASK_1/restart'))).toBe(true));
  expect((await screen.findAllByText('等待中')).length).toBeGreaterThan(0);
  expect(screen.queryByText('任务记录：已创建')).not.toBeInTheDocument();
});

test('放弃匹配后清除任务卡片和任务URL',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/resumes/me'))return response([resumeRecord()]);
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_name:'大模型算法工程师'})]);
    if(url.endsWith('/api/v1/matches/reports'))return response([]);
    if(url.includes('/api/v1/matches/preflight?'))return response(readyMatchPreflight);
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1/abandon')&&init?.method==='POST')return response({task_id:'MATCH_TASK_1',status:'failed',progress:0,error_code:'TASK_ABANDONED_BY_USER',error_message:'task was abandoned by the user before restart'});
    if(url.endsWith('/api/v1/matches/tasks/MATCH_TASK_1'))return response({task_id:'MATCH_TASK_1',status:'running',progress:0,error_code:null,error_message:null});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath={'/matching?resumeId=resume-1&positionId=POS_AI&matchTaskId=MATCH_TASK_1'}/>);
  fireEvent.click(await screen.findByRole('button',{name:'放弃任务'}));
  fireEvent.click(await screen.findByRole('button',{name:'确认放弃'}));
  await waitFor(()=>expect(screen.queryByText('匹配任务')).not.toBeInTheDocument());
  expect(window.location.search).not.toContain('matchTaskId');
  expect(await screen.findByText('准备匹配「大模型算法工程师」')).toBeInTheDocument();
});

test('学习路径失败显示 Contract 错误且空路径显示真实空状态',async()=>{
  localStorage.setItem('main_access_token','token');
  const failed=matchReportFixture();
  failed.gap_analysis.error_code='LEARNING_PATH_NOT_REQUESTED';
  failed.gap_analysis.error_message=null;
  failed.gap_analysis.learning_path=[];
  vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(failed);
    if(url.endsWith('/api/v1/learning-paths')&&init?.method==='POST')return Promise.resolve({
      ok:false,
      status:503,
      statusText:'error',
      json:async()=>({code:50301,message:'Gap 服务不可用',data:null,details:{},trace_id:'req_gap_failed'}),
    });
    return response([]);
  }));
  render(<Root initialPath={'/matching/reports/evaluation-1'}/>);
  fireEvent.click(await screen.findByRole('button',{name:'按预算优化'}));
  expect(await screen.findByText('学习路线生成失败')).toBeInTheDocument();
  expect(screen.getByText('Gap 服务不可用')).toBeInTheDocument();
  expect(screen.queryByText('完成 RAG 项目')).not.toBeInTheDocument();

  cleanup();
  const empty=matchReportFixture();
  empty.gap_analysis.learning_path=[];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(empty);
    return response([]);
  }));
  render(<Root initialPath={'/matching/reports/evaluation-1'}/>);
  expect(await screen.findByText('本次匹配暂未生成学习建议')).toBeInTheDocument();
});

test('失败的学习路径记录显示真实原因且不会被当作成功规划',async()=>{
  localStorage.setItem('main_access_token','token');
  const rejected={
    ...learningPathFixture('learning-path:rejected',40),
    status:'rejected',
    stages:[],
    gap_analysis:{
      generation_status:'rejected',
      error_code:'LEARNING_PATH_PROFILE_INVALID',
      error_message:'both profile contracts are required',
      prioritized_gaps:[],
      learning_path:[],
    },
  };
  vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_id:'position-1',position_name:'RAG 工程师'})]);
    if(url.endsWith('/api/v1/learning-paths/learning-path%3Arejected'))return response(rejected);
    if(url.endsWith('/api/v1/learning-paths')&&init?.method==='POST')return Promise.resolve({
      ok:false,
      status:409,
      statusText:'Conflict',
      json:async()=>({
        code:409,
        message:'LEARNING_PATH_PROFILE_INVALID',
        data:null,
        details:{error_code:'LEARNING_PATH_PROFILE_INVALID',message:'both profile contracts are required'},
        trace_id:'req_test',
      }),
    });
    if(url.endsWith('/api/v1/learning-paths'))return response([rejected]);
    return response([]);
  }));

  render(<Root initialPath="/matching/reports/evaluation-1?pathId=learning-path%3Arejected"/>);

  expect(await screen.findByText('该学习路径生成失败')).toBeInTheDocument();
  expect(screen.getByText('简历或岗位画像格式不兼容，无法生成学习路径。')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'按预算优化'}));
  expect(await screen.findByText('学习路线生成失败')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'按预算优化'})).toBeInTheDocument();
});

const learningPathFixture=(pathId:string,budget:number,evaluationId='evaluation-1')=>({
  path_id:pathId,
  evaluation_id:evaluationId,
  target_position_id:'position-1',
  time_budget_hours:budget,
  learning_goal:null,
  status:'completed',
  provider:'matching-service',
  stages:[],
  gap_analysis:{
    generation_status:'completed',
    learning_path:[{
      step_order:1,target_skill_id:'skill_rag',objective:`完成 ${budget} 小时 RAG 训练`,prerequisite_skill_ids:[],
      prerequisite_states:[],basis:['Gap 分析'],estimated_hours:budget,planning_status:'ready',
    }],
  },
  algorithm_versions:{evaluation:'deterministic-matching.v5',learning_path:'learning.v3'},
  data_versions:{validated_cv_snapshot_id:'CV_SNAPSHOT_1',position_graph_version:'GRAPH_V1'},
  created_at:'2026-08-13T08:00:00Z',
  updated_at:'2026-08-13T08:00:00Z',
});

test('学习路径支持创建并按不同预算重新规划',async()=>{
  localStorage.setItem('main_access_token','token');
  const paths=[learningPathFixture('learning-path:old',12)];
  const createBudgets:number[]=[];
  const fetchMock=vi.fn(async(url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    if(url.endsWith('/api/v1/matches/positions'))return response([matchPosition({position_id:'position-1',position_name:'RAG 工程师'})]);
    if(url.endsWith('/api/v1/learning-paths')&&init?.method==='POST'){
      const payload=JSON.parse(String(init.body)) as {time_budget_hours:number;target_position_id:string};
      createBudgets.push(payload.time_budget_hours);
      expect(payload.target_position_id).toBe('position-1');
      const path=learningPathFixture(`learning-path:new-${createBudgets.length}`,payload.time_budget_hours);
      paths.unshift(path);
      return response(path);
    }
    if(url.endsWith('/api/v1/learning-paths'))return response(paths);
    const pathMatch=url.match(/\/api\/v1\/learning-paths\/(learning-path%3Anew-\d+)$/);
    if(pathMatch)return response(paths.find(item=>item.path_id===decodeURIComponent(pathMatch[1])));
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/matching/reports/evaluation-1"/>);

  expect(await screen.findByRole('heading',{name:'学习路径'})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('时间预算（小时）'),{target:{value:'24'}});
  fireEvent.click(screen.getByRole('button',{name:'按预算优化'}));
  expect(await screen.findByRole('heading',{name:'学习建议'})).toBeInTheDocument();
  expect((await screen.findAllByText(/24 小时/)).length).toBeGreaterThan(0);

  fireEvent.change(screen.getByLabelText('时间预算（小时）'),{target:{value:'48'}});
  fireEvent.click(screen.getByRole('button',{name:'按预算优化'}));
  await waitFor(()=>expect(createBudgets).toEqual([24,48]));
  expect((await screen.findAllByText(/48 小时/)).length).toBeGreaterThan(0);
  expect(screen.queryByText(/历史方案/)).not.toBeInTheDocument();
});

test('学习路径按 pathId 刷新恢复并导出',async()=>{
  localStorage.setItem('main_access_token','token');
  const current=learningPathFixture('learning-path:current',36);
  const createObjectURL=vi.fn(()=> 'blob:learning-path');
  const revokeObjectURL=vi.fn();
  vi.stubGlobal('URL',{...URL,createObjectURL,revokeObjectURL});
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>undefined);
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    if(url.endsWith('/api/v1/matches/positions'))return response([]);
    if(url.endsWith('/api/v1/learning-paths'))return response([]);
    if(url.endsWith('/api/v1/learning-paths/learning-path%3Acurrent/export'))return response({format:'json',learning_path:current});
    if(url.endsWith('/api/v1/learning-paths/learning-path%3Acurrent'))return response(current);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/matching/reports/evaluation-1?pathId=learning-path%3Acurrent"/>);

  expect(await screen.findByRole('heading',{name:'学习建议'})).toBeInTheDocument();
  expect(screen.getAllByText(/36 小时/).length).toBeGreaterThan(0);
  fireEvent.click(screen.getByRole('button',{name:/导出/}));
  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/learning-paths/learning-path%3Acurrent/export'))).toBe(true));
  expect(createObjectURL).toHaveBeenCalled();
});

test('学习路径导出失败显示明确错误',async()=>{
  localStorage.setItem('main_access_token','token');
  const current=learningPathFixture('learning-path:current',36);
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    if(url.endsWith('/api/v1/learning-paths/learning-path%3Acurrent/export'))return response(null,503);
    if(url.endsWith('/api/v1/learning-paths/learning-path%3Acurrent'))return response(current);
    if(url.endsWith('/api/v1/learning-paths'))return response([current]);
    return response([]);
  }));
  render(<Root initialPath="/matching/reports/evaluation-1?pathId=learning-path%3Acurrent"/>);
  fireEvent.click(await screen.findByRole('button',{name:/导出/}));
  expect(await screen.findByText('学习路径导出失败')).toBeInTheDocument();
});

test('学习路径恢复和生成区分无权限、过期与不存在',async()=>{
  localStorage.setItem('main_access_token','token');
  const stale=matchReportFixture({stale:true,stale_reason_codes:['INPUT_FINGERPRINT_CHANGED']});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(stale);
    if(url.endsWith('/api/v1/learning-paths/forbidden'))return response(null,403);
    return response([]);
  }));
  render(<Root initialPath="/matching/reports/evaluation-1?pathId=forbidden"/>);
  expect(await screen.findByText('无权访问学习路径')).toBeInTheDocument();
  expect(screen.getByText('建议需要更新')).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'按预算优化'})).toBeDisabled();

  cleanup();
  const deferred=matchReportFixture();
  deferred.gap_analysis.error_code='LEARNING_PATH_NOT_REQUESTED';
  vi.stubGlobal('fetch',vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user());
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(deferred);
    if(url.endsWith('/api/v1/learning-paths')&&init?.method==='POST')return response(null,404);
    return response([]);
  }));
  render(<Root initialPath="/matching/reports/evaluation-1"/>);
  fireEvent.click(await screen.findByRole('button',{name:'按预算优化'}));
  expect(await screen.findByText('来源评估不存在')).toBeInTheDocument();
});

test('JD 详情仅展示解析服务返回的真实 Evidence',async()=>{
  localStorage.setItem('main_access_token','token');
  const jd={jd_id:'JD_1',title:'RAG 工程师',raw_text:'原文里还有一段不应被猜作证据的描述',source_type:'text',source_name:'企业官网',parse_status:'parsed',input_extraction_status:'completed',input_error_message:null,created_at:null,updated_at:null};
  const parsed={parse_result_id:'PARSE_1',jd_id:'JD_1',position_title:'RAG 工程师',responsibilities:['构建检索服务'],required_skills:[{raw_skill:'Python'}],bonus_skills:[],education:null,experience:null,industry:null,parse_confidence:.91,need_review:false,workflow_status:'parsed',extraction_result:{responsibilities:[{value:'构建检索服务',evidence:{quote:'负责构建可追溯的 RAG 检索服务',source_id:'JD_1'}}]},normalized_result:{}};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/jds/summary'))return response({total:1,awaiting_review:1,reviewed:0,published:0,failed:0});
    if(url.includes('/api/v1/jds/page?'))return response({items:[jd],total:1,offset:0,limit:20});
    if(url.endsWith('/api/v1/jds/JD_1/parse-result'))return response(parsed);
    if(url.endsWith('/api/v1/jds/JD_1'))return response(jd);
    return response([]);
  }));
  render(<Root initialPath="/data/jds"/>);
  expect(await screen.findByText(/负责构建可追溯的 RAG 检索服务/)).toBeInTheDocument();
  expect(screen.queryByText('原文里还有一段不应被猜作证据的描述')).not.toBeInTheDocument();
});

test('LLM 服务不可用时不自动切换解析模式，并隐藏模式说明小字',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/extraction-modes/readiness'))return response({jd:{rule:{ready:true,provider:'rule_based_jd_extraction',requires_review:true},llm:{ready:false,provider:'http_jd_extraction',error_code:'extraction_unavailable'}}});
    if(url.endsWith('/api/v1/jds/summary'))return response({total:0,awaiting_review:0,reviewed:0,published:0,failed:0});
    if(url.includes('/api/v1/jds/page?'))return response({items:[],total:0,offset:0,limit:20});
    return response([]);
  }));
  render(<Root initialPath="/data/jds"/>);
  await screen.findByRole('heading',{name:'JD 数据中心'});
  expect(await screen.findByText('选择解析模式')).toBeInTheDocument();
  expect(screen.queryByText('LLM 当前不可用，请启动或重启模型抽取服务后刷新页面再试。')).not.toBeInTheDocument();
  expect(screen.queryByText('勾选后可批量解析所选 JD。')).not.toBeInTheDocument();
  expect(screen.getByText('LLM').closest('.ant-segmented-item')).toHaveClass('ant-segmented-item-selected');
  expect(screen.getByRole('button',{name:/批量解析/})).toBeDisabled();
  fireEvent.click(screen.getByText('规则'));
  expect(screen.getByText('规则').closest('.ant-segmented-item')).toHaveClass('ant-segmented-item-selected');
});

test('管理员可配置并测试模型服务，API Key 不会由读取接口回显',async()=>{
  localStorage.setItem('main_access_token','token');
  const requests:Array<{url:string;method:string;body:Record<string,unknown>}>=[];
  const fetchMock=vi.fn((url:string,init?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/system/model-service-config')&&(!init?.method||init.method==='GET'))return response({provider:'deepseek',base_url:'https://api.deepseek.com',model:'deepseek-chat',api_key_configured:false,version:1,updated_at:null});
    if(url.endsWith('/api/v1/system/model-service-config')&&init?.method==='PUT'){
      requests.push({url,method:'PUT',body:JSON.parse(String(init.body))});
      return response({provider:'deepseek',base_url:'https://api.deepseek.com',model:'deepseek-chat',api_key_configured:true,version:2,updated_at:null});
    }
    if(url.endsWith('/api/v1/system/model-service-config/test')&&init?.method==='POST'){
      requests.push({url,method:'POST',body:JSON.parse(String(init.body))});
      return response({status:'available',message:'连接成功'});
    }
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath="/admin/model-service"/>);
  expect(await screen.findByRole('heading',{name:'模型服务配置'})).toBeInTheDocument();
  expect(screen.getByText('未配置')).toBeInTheDocument();
  expect(screen.queryByText(/保存后仅显示配置状态|已保存密钥/)).not.toBeInTheDocument();
  // 配置读取和 loading 结束不在同一帧，等表单真正渲染出来再操作
  fireEvent.change(await screen.findByLabelText('API Key'),{target:{value:'sk-test-model-key'}});
  fireEvent.click(screen.getByRole('button',{name:'测试连接'}));
  await waitFor(()=>expect(requests.some(item=>item.method==='POST'&&item.body.api_key==='sk-test-model-key')).toBe(true));
  fireEvent.click(screen.getByRole('button',{name:'保存配置'}));
  await waitFor(()=>expect(requests.some(item=>item.method==='PUT'&&item.body.api_key==='sk-test-model-key')).toBe(true));
  expect(await screen.findByText('已配置')).toBeInTheDocument();
  expect(screen.getByLabelText('API Key')).toHaveValue('');
});

test.each([
  ['enterprise_user',enterprisePermissions,true],
  ['reviewer',reviewerPermissions,false],
  ['admin',adminPermissions,true],
  ['developer',developerPermissions,true],
] as const)('%s 的 /auth/me permission 与 JD 创建、解析入口一致',async(role,permissions,canWriteJD)=>{
  localStorage.setItem('main_access_token','token');
  const jd={jd_id:'JD_PERMISSION_1',title:'权限回归 JD',raw_text:'负责 Python 开发',source_type:'text',source_name:'企业官网',parse_status:'pending',input_extraction_status:'completed',input_error_message:null,created_at:null,updated_at:null};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user(role,permissions));
    if(url.endsWith('/api/v1/jds/summary'))return response({total:1,awaiting_review:1,reviewed:0,published:0,failed:0});
    if(url.includes('/api/v1/jds/page?'))return response({items:[jd],total:1,offset:0,limit:20});
    if(url.endsWith('/api/v1/jds/JD_PERMISSION_1/parse-result'))return response(null,404);
    if(url.endsWith('/api/v1/jds/JD_PERMISSION_1'))return response(jd);
    return response([]);
  }));
  render(<Root initialPath="/data/jds"/>);
  await screen.findByRole('heading',{name:'JD 数据中心'});
  expect(screen.queryByRole('button',{name:/导入 JD/})!==null).toBe(canWriteJD);
  expect((await screen.findAllByText('权限回归 JD')).length).toBeGreaterThan(0);
  await waitFor(()=>expect(screen.queryByRole('button',{name:'开始解析'})!==null).toBe(canWriteJD));
});

test('integration.jd.retry 不再授予 JD 创建或解析入口',async()=>{
  localStorage.setItem('main_access_token','token');
  const jd={jd_id:'JD_RETRY_ONLY',title:'集成重试权限 JD',raw_text:'Python',source_type:'text',source_name:null,parse_status:'pending',input_extraction_status:'completed',input_error_message:null,created_at:null,updated_at:null};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('reviewer',[...reviewerPermissions,'integration.jd.retry']));
    if(url.endsWith('/api/v1/jds/summary'))return response({total:1,awaiting_review:1,reviewed:0,published:0,failed:0});
    if(url.includes('/api/v1/jds/page?'))return response({items:[jd],total:1,offset:0,limit:20});
    if(url.endsWith('/api/v1/jds/JD_RETRY_ONLY/parse-result'))return response(null,404);
    if(url.endsWith('/api/v1/jds/JD_RETRY_ONLY'))return response(jd);
    return response([]);
  }));
  render(<Root initialPath="/data/jds"/>);
  await screen.findByRole('heading',{name:'JD 数据中心'});
  expect(screen.queryByRole('button',{name:/导入 JD/})).not.toBeInTheDocument();
  expect((await screen.findAllByText('集成重试权限 JD')).length).toBeGreaterThan(0);
  expect(screen.queryByRole('button',{name:'开始解析'})).not.toBeInTheDocument();
});

test('公开新兴岗位详情展示定义与演化信息并移除旧评分口径、诊断和代表证据板块',async()=>{
  localStorage.setItem('main_access_token','token');
  const detail={emerging_id:'EM_1',cluster_id:'CL_1',position_name:'生成式 AI 产品工程师',core_responsibilities:['交付智能体应用'],required_skills:[{skill_name:'Prompt Engineering',required_level:'advanced'}],bonus_skills:[],industry_scenarios:['企业服务'],germination_score:.82,score_dimensions:{growth:.7,result_stability:.8,enterprise_coverage:.6},evidence_jd_ids:['JD_1'],field_evidence:{position_summary:{content:'连接产品需求与生成式 AI 工程落地。',evidence:[{quote:'负责生成式 AI 产品的方案和工程交付',source_jd_id:'JD_1'}]},representative_enterprises:{content:{示例科技:3}},growth_trajectory:{content:[{window_id:'2026-Q1',member_count:4},{window_id:'2026-Q2',member_count:9}]}},published_snapshot:{},status:'published',germination_assessment:{formula_version:'emerging-score.v2',dimensions:{growth:.7,result_stability:.8,enterprise_coverage:.6}}};
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):url.endsWith('/api/v1/portal/emerging-positions/EM_1')?response(detail):response([])));
  render(<Root initialPath="/emerging/EM_1"/>);
  expect(await screen.findByRole('heading',{name:'岗位定义'})).toBeInTheDocument();
  expect(screen.getByText('演化时间线')).toBeInTheDocument();
  expect(screen.getByText('2026-Q1')).toBeInTheDocument();
  expect(screen.getByText('观测到 4 份岗位样本')).toBeInTheDocument();
  expect(screen.queryByText('评估模型')).not.toBeInTheDocument();
  expect(screen.queryByText('诊断特征')).not.toBeInTheDocument();
  expect(screen.queryByText('代表证据')).not.toBeInTheDocument();
  expect(document.querySelector('pre')).toBeNull();
});

test('公开新兴岗位详情为空时显示明确空态',async()=>{
  localStorage.setItem('main_access_token','token');
  const detail={emerging_id:'EM_EMPTY',cluster_id:'CL_0',position_name:'待补充岗位',core_responsibilities:[],required_skills:[],bonus_skills:[],industry_scenarios:[],germination_score:null,score_dimensions:{},evidence_jd_ids:[],field_evidence:{},published_snapshot:{},status:'published'};
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):url.endsWith('/api/v1/portal/emerging-positions/EM_EMPTY')?response(detail):response([])));
  render(<Root initialPath="/emerging/EM_EMPTY"/>);
  expect(await screen.findByText('暂无职责数据')).toBeInTheDocument();
  expect(screen.getByText('暂无技能要求')).toBeInTheDocument();
  expect(screen.getByText('暂无演化时间线')).toBeInTheDocument();
  expect(screen.queryByText('代表证据')).not.toBeInTheDocument();
});

test('管理员可在新兴岗位详情人工优化各板块并立即进入重新审核',async()=>{
  localStorage.setItem('main_access_token','token');
  const detail={emerging_id:'EM_EDIT',cluster_id:'CL_EDIT',position_name:'智能体工程师',core_responsibilities:['开发智能体应用'],required_skills:[{raw_skill:'Python'}],bonus_skills:[],industry_scenarios:['企业服务'],germination_score:1,score_dimensions:{},evidence_jd_ids:['JD_1'],field_evidence:{position_summary:{content:'负责企业智能体应用研发。'},representative_enterprises:{content:['示例企业']},growth_trajectory:{content:['2026-08-01：2 条独立发布']}},published_snapshot:{},status:'published'};
  const updated={...detail,position_name:'企业智能体应用工程师',status:'pending_review',field_evidence:{...detail.field_evidence,position_summary:{content:'负责企业智能体产品研发与持续优化。'}}};
  const fetchMock=vi.fn((url:string,options?:RequestInit)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/emerging-positions/EM_EDIT'))return response(detail);
    if(url.endsWith('/api/v1/emerging-positions/EM_EDIT')&&options?.method==='PUT')return response(updated);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/emerging/EM_EDIT"/>);

  const definitionColumn=(await screen.findByRole('heading',{name:'岗位定义'})).closest('section') as HTMLElement;
  fireEvent.click(within(definitionColumn).getByRole('button',{name:'人工优化'}));
  expect(screen.getByLabelText('岗位名称')).toBeInTheDocument();
  expect(screen.queryByLabelText('核心职责（每行一条）')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('必备技能')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('岗位名称'),{target:{value:'企业智能体应用工程师'}});
  fireEvent.change(screen.getByLabelText('岗位概述'),{target:{value:'负责企业智能体产品研发与持续优化。'}});
  fireEvent.click(screen.getByRole('button',{name:'保存优化'}));

  await waitFor(()=>expect(fetchMock).toHaveBeenCalledWith('/api/v1/emerging-positions/EM_EDIT',expect.objectContaining({method:'PUT'})));
  expect(await screen.findByText('企业智能体应用工程师',{selector:'h2'})).toBeInTheDocument();
  expect(screen.queryByText('待审核')).not.toBeInTheDocument();
  const scenarioColumn=screen.getByRole('heading',{name:'典型行业应用场景'}).closest('section') as HTMLElement;
  fireEvent.click(within(scenarioColumn).getByRole('button',{name:'人工优化'}));
  expect(screen.getByLabelText('典型行业应用场景')).toBeInTheDocument();
  expect(screen.queryByLabelText('岗位名称')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('核心职责（每行一条）')).not.toBeInTheDocument();
});

test('公开新兴岗位详情请求失败时显示失败状态',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>url.endsWith('/api/v1/auth/me')?response(user()):response(null,503)));
  render(<Root initialPath="/emerging/EM_FAILED"/>);
  expect(await screen.findByText('API 失败')).toBeInTheDocument();
});

test('演示总览有权限时读取统一 Demo Task',async()=>{
  localStorage.setItem('main_access_token','token');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([portalDemoTask({status:'running',progress:.4,result_reference:null})]);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('当前运行状态')).toBeInTheDocument();
  expect(await screen.findByText('岗位匹配')).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/portal/admin/demo-tasks',expect.anything());
});

test('演示总览分别展示 running failed succeeded 和 cancelled',async()=>{
  localStorage.setItem('main_access_token','token');
  const tasks=[
    portalDemoTask({task_id:'running-demo',status:'running',progress:.5,result_reference:null}),
    portalDemoTask({task_id:'failed-demo',status:'failed',progress:0,error:{code:'DEMO_FAILED',message:'演示任务执行失败'},result_reference:null}),
    portalDemoTask({task_id:'succeeded-demo'}),
    portalDemoTask({task_id:'cancelled-demo',status:'cancelled',progress:0,result_reference:null}),
  ];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response(tasks);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect((await screen.findAllByText('岗位匹配')).length).toBeGreaterThan(0);
  expect(screen.getByText('成功')).toBeInTheDocument();
  expect(screen.getByText('失败')).toBeInTheDocument();
  expect(screen.getByText('/ 4')).toBeInTheDocument();
  expect(screen.queryByText('演示任务执行失败')).not.toBeInTheDocument();
});

test('演示总览成功任务通过解析器跳转结果',async()=>{
  localStorage.setItem('main_access_token','token');
  const task=portalDemoTask({task_id:'discovery-demo',task_type:'discovery',object_type:'discovery_run',object_id:'run-demo',service:'discovery-service',result_reference:'discovery_run:run-demo'});
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([task]);
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([]);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  fireEvent.click(await screen.findByRole('button',{name:/查看详情/}));
  expect(await screen.findByRole('heading',{name:'任务中心'})).toBeInTheDocument();
  expect(screen.getAllByText('新兴岗位发现').length).toBeGreaterThan(0);
});

test('演示总览 Demo Endpoint 失败但业务 lanes 正常',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response(null,503);
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_OK',name:'正常岗位',category_code:'TECH',current_version_id:1,sample_count:3,skill_count:2,published_at:null,release_id:null,quality_state:'ready'}]);
    if(url.endsWith('/api/v1/jds/summary'))return response({total:1,awaiting_review:0,reviewed:1,published:1,failed:0});
    if(url.endsWith('/api/v1/positions/POS_OK/trend-reports'))return response({schema_version:'trend-delivery.v1',items:[],pagination:{page:1,page_size:20,total:0,total_pages:0},filters:{},sort:{by:'created_at',order:'desc'},not_found_ids:[]});
    if(url.endsWith('/api/v1/portal/admin/discovery-runs')||url.endsWith('/api/v1/portal/emerging-positions'))return response([]);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('任务状态读取失败')).toBeInTheDocument();
  expect(await screen.findByText('1 个已发布岗位')).toBeInTheDocument();
  expect(screen.getByText('1 份岗位数据')).toBeInTheDocument();
});

test('演示总览无权限时不请求 Demo Endpoint',async()=>{
  localStorage.setItem('main_access_token','token');
  const permissions=reviewerPermissions.filter(permission=>permission!=='integration.status.view');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('reviewer',permissions));
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath="/demo"/>);
  expect(await screen.findByRole('heading',{name:'演示总览'})).toBeInTheDocument();
  expect(screen.queryByText('当前运行状态')).not.toBeInTheDocument();
  expect(screen.queryByText('当前账户缺少 integration.status.view，无法读取演示任务状态。')).not.toBeInTheDocument();
  expect(document.querySelector('.demo-task-status')).toBeNull();
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/portal/admin/demo-tasks'))).toBe(false);
});

test('演示总览手动刷新只重新加载 tasks',async()=>{
  localStorage.setItem('main_access_token','token');
  const calls:string[]=[];
  const fetchMock=vi.fn((url:string)=>{
    calls.push(String(url));
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/admin/demo-tasks'))return response([]);
    if(url.endsWith('/api/v1/jds/summary'))return response({total:0,awaiting_review:0,reviewed:0,published:0,failed:0});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);render(<Root initialPath="/demo"/>);
  await screen.findByText('暂无演示任务');
  await screen.findByText('尚无已发布岗位');
  // antd v6 的 loading 按钮处于 disabled 状态，等初次任务加载结束、按钮可点击后再手动刷新
  await waitFor(()=>expect(screen.getByRole('button',{name:/刷新任务状态/})).toBeEnabled());
  const taskCalls=calls.filter(url=>url.endsWith('/api/v1/portal/admin/demo-tasks')).length;
  const businessCalls=calls.filter(url=>!url.endsWith('/api/v1/auth/me')&&!url.endsWith('/api/v1/portal/admin/demo-tasks')).length;
  fireEvent.click(screen.getByRole('button',{name:/刷新任务状态/}));
  await waitFor(()=>expect(calls.filter(url=>url.endsWith('/api/v1/portal/admin/demo-tasks')).length).toBe(taskCalls+1));
  expect(calls.filter(url=>!url.endsWith('/api/v1/auth/me')&&!url.endsWith('/api/v1/portal/admin/demo-tasks')).length).toBe(businessCalls);
});

test('演示总览业务资源为空时保持原空状态',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/portal/positions')||url.endsWith('/api/v1/portal/emerging-positions')||url.endsWith('/api/v1/resumes/me')||url.endsWith('/api/v1/matches/reports'))return response([]);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('尚无已发布岗位')).toBeInTheDocument();
  expect(screen.getByText('尚无公开新兴岗位')).toBeInTheDocument();
  expect(screen.getByText('尚无简历')).toBeInTheDocument();
  expect(screen.getByText('尚无匹配评估')).toBeInTheDocument();
});

test('演示总览从真实 JD、能力演化、Discovery 与 Evidence 资源恢复管理链',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/positions'))return response([{position_id:'POS_1',name:'无报告岗位',category_code:'TECH',current_version_id:2,sample_count:8,skill_count:3,published_at:'2026-08-01',release_id:'REL_2',quality_state:'ready'},{position_id:'POS_2',name:'智能体工程师',category_code:'TECH',current_version_id:3,sample_count:12,skill_count:5,published_at:'2026-08-01',release_id:'REL_3',quality_state:'ready'}]);
    if(url.endsWith('/api/v1/jds/summary'))return response({total:8,awaiting_review:2,reviewed:5,published:4,failed:1});
    if(url.endsWith('/api/v1/portal/admin/knowledge-graph/positions/POS_1/capability-evolution'))return response({schema_version:'capability-evolution.v1',position_id:'POS_1',frames:[{id:2,version_number:2,version_name:'当前版本',build_run_id:2,release_id:'REL_2',rollback_from_version_id:null,created_at:'2026-08-01',dependencies:{source_time_window:{start:'2026-08-01',end:'2026-08-01'}},snapshot:{position_id:'POS_1',position:{position_id:'POS_1',name:'无报告岗位',category_code:'TECH'},skill_relations:[]}}],comparisons:[],events:[],frame_count:1,comparison_count:0,event_count:0});
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response([{run_id:'DISC_1',status:'succeeded',algorithm_version:'discovery.v2',time_window_start:'2026-01-01',time_window_end:'2026-06-30',cluster_count:3,sample_count:20}]);
    if(url.endsWith('/api/v1/portal/emerging-positions'))return response([{emerging_id:'EM_1',cluster_id:'CL_1',position_name:'生成式 AI 产品工程师',core_responsibilities:[],required_skills:[],bonus_skills:[],industry_scenarios:[],germination_score:.8,score_dimensions:{},evidence_jd_ids:['JD_1','JD_2'],status:'published'}]);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('8 份岗位数据 · 1 份失败')).toBeInTheDocument();
  expect(screen.getByText('2 份等待审核')).toBeInTheDocument();
  expect(screen.getByText('最近一次运行已完成')).toBeInTheDocument();
  expect(screen.getByText('已引用 2 份岗位数据')).toBeInTheDocument();
  const evolutionLink=screen.getByText('无报告岗位 可查看能力演化').closest('a.demo-step');
  expect(evolutionLink).toHaveAttribute('href','/analysis/evolution?positionId=POS_1');
  expect(screen.getAllByText('仅个人工作区可访问')).toHaveLength(5);
  fireEvent.click(evolutionLink!);
  expect(await screen.findByRole('heading',{name:'岗位能力演化'})).toBeInTheDocument();
});

test('演示总览从 Resume 与 Evaluation 恢复差距、学习路径和版本',async()=>{
  localStorage.setItem('main_access_token','token');
  const evaluation={evaluation_id:'EVAL_DEMO',task_id:'TASK_DEMO',status:'current',stale:false,stale_reason_codes:[],evaluation:{evaluation_id:'EVAL_DEMO',evaluation_status:'completed',algorithm_version:'matching.v7',cv_profile_id:'CV_DEMO',position_profile_id:'POS_DEMO',hard_constraint_results:[],skill_results:[],responsibility_results:[],project_results:[],scenario_results:[],summary:{},final_match_result:{overall_score:72,match_confidence:.8,recommendation_level:'potential',hard_gate_status:'passed',dimension_scores:[],strengths:[],gaps:[{dimension:'skills',result_id:'G1',reason_code:'missing',message:'缺少 RAG'}],uncertain_items:[],explanation:'',algorithm_version:'matching.v7',scoring_config_version:'config.v2',cv_profile_id:'CV_DEMO',position_profile_id:'POS_DEMO',position_graph_version:'GRAPH_V4'}},gap_analysis:{prioritized_gaps:[{skill_id:'RAG'}],learning_path:[{step_order:1,target_skill_id:'RAG',objective:'完成检索项目'}],algorithm_version:'learning.v3'},versions:{},lineage:{resume_id:'RES_DEMO',position_id:'POS_1'},created_at:null,updated_at:null};
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/portal/positions')||url.endsWith('/api/v1/portal/emerging-positions'))return response([]);
    if(url.endsWith('/api/v1/resumes/me'))return response([{resume_id:'RES_DEMO',display_name:'比赛简历',source_type:'text',raw_text:'',parse_status:'completed',implementation_status:'validated_snapshot',validated_cv_snapshot_id:'CV_SNAPSHOT_DEMO',created_at:null,updated_at:null}]);
    if(url.endsWith('/api/v1/matches/reports'))return response([{evaluation_id:'EVAL_DEMO',task_id:'TASK_DEMO',resume_id:'RES_DEMO',position_id:'POS_1',target_type:'standard_position',status:'current',provider:'matching-service',lineage:{},created_at:null,updated_at:null}]);
    if(url.endsWith('/api/v1/matches/reports/EVAL_DEMO'))return response(evaluation);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('1 份简历已确认')).toBeInTheDocument();
  expect(screen.getByText('最近一次评估当前有效')).toBeInTheDocument();
  expect(screen.getByText('1 项结构化差距')).toBeInTheDocument();
  expect(screen.getByText('1 个学习阶段')).toBeInTheDocument();
  expect(screen.getAllByText('当前账号无权限')).toHaveLength(4);
});

test('演示总览不会把进行中且无 evaluation_id 的最新记录跳转为 null 报告',async()=>{
  localStorage.setItem('main_access_token','token');
  const completedEvaluation={evaluation_id:'EVAL_COMPLETED',task_id:'TASK_COMPLETED',status:'current',stale:false,stale_reason_codes:[],evaluation:{evaluation_id:'EVAL_COMPLETED',evaluation_status:'completed',algorithm_version:'matching.v7',cv_profile_id:'CV_DEMO',position_profile_id:'POS_DEMO',hard_constraint_results:[],skill_results:[],responsibility_results:[],project_results:[],scenario_results:[],summary:{},final_match_result:{overall_score:72,match_confidence:.8,recommendation_level:'potential',hard_gate_status:'passed',dimension_scores:[],strengths:[],gaps:[{dimension:'skills',result_id:'G1',reason_code:'missing',message:'缺少 RAG'}],uncertain_items:[],explanation:'',algorithm_version:'matching.v7',scoring_config_version:'config.v2',cv_profile_id:'CV_DEMO',position_profile_id:'POS_DEMO',position_graph_version:'GRAPH_V4'}},gap_analysis:{prioritized_gaps:[{skill_id:'RAG'}],learning_path:[{step_order:1,target_skill_id:'RAG',objective:'完成检索项目'}],algorithm_version:'learning.v3'},versions:{},lineage:{resume_id:'RES_DEMO',position_id:'POS_1'},created_at:null,updated_at:null};
  const calls:string[]=[];
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    calls.push(url);
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/portal/positions')||url.endsWith('/api/v1/portal/emerging-positions'))return response([]);
    if(url.endsWith('/api/v1/resumes/me'))return response([{resume_id:'RES_DEMO',display_name:'比赛简历',source_type:'text',raw_text:'',parse_status:'completed',implementation_status:'validated_snapshot',validated_cv_snapshot_id:'CV_SNAPSHOT_DEMO',created_at:null,updated_at:null}]);
    if(url.endsWith('/api/v1/matches/reports'))return response([
      {evaluation_id:null,task_id:'TASK_RUNNING',resume_id:'RES_DEMO',position_id:'POS_RUNNING',target_type:'standard_position',status:'running',provider:'matching-service',lineage:{},created_at:'2026-08-30T01:11:48+08:00',updated_at:'2026-08-30T01:11:49+08:00'},
      {evaluation_id:'EVAL_COMPLETED',task_id:'TASK_COMPLETED',resume_id:'RES_DEMO',position_id:'POS_1',target_type:'standard_position',status:'current',provider:'matching-service',lineage:{},created_at:'2026-08-29T01:00:00+08:00',updated_at:'2026-08-29T01:00:00+08:00'},
    ]);
    if(url.endsWith('/api/v1/matches/reports/EVAL_COMPLETED'))return response(completedEvaluation);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect(await screen.findByText('最近一次评估处理中')).toBeInTheDocument();
  expect(screen.getByText('1 项结构化差距')).toBeInTheDocument();
  expect(screen.getByText('1 个学习阶段')).toBeInTheDocument();
  const evaluationLink=screen.getByText('最近一次评估处理中').closest('a.demo-step');
  expect(evaluationLink).toHaveAttribute('href','/matching?resumeId=RES_DEMO&positionId=POS_RUNNING&matchTaskId=TASK_RUNNING');
  const gapLink=screen.getByText('1 项结构化差距').closest('a.demo-step');
  expect(gapLink).toHaveAttribute('href','/matching?resumeId=RES_DEMO&positionId=POS_RUNNING');
  expect(calls.some(url=>url.endsWith('/api/v1/matches/reports/null'))).toBe(false);
});

test('演示总览保留真实失败信息且不显示固定成功状态',async()=>{
  localStorage.setItem('main_access_token','token');
  vi.stubGlobal('fetch',vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/portal/positions'))return response(null,503);
    if(url.endsWith('/api/v1/jds/summary'))return response(null,500);
    if(url.endsWith('/api/v1/portal/admin/discovery-runs'))return response(null,502);
    if(url.endsWith('/api/v1/portal/emerging-positions'))return response(null,503);
    return response([]);
  }));
  render(<Root initialPath="/demo"/>);
  expect((await screen.findAllByText('API 失败')).length).toBeGreaterThanOrEqual(6);
  expect(screen.getAllByText('读取失败').length).toBeGreaterThanOrEqual(6);
});

test('证据问答页只调用 RAG BFF 并在当前页面定位来源',async()=>{
  localStorage.setItem('main_access_token','token');
  localStorage.removeItem('jobpulse.rag.chat.sessions.v1');
  const ragAnswered={
    contract_version:'evidence-rag-response.v1',
    status:'answered',
    answer:'候选人满足核心技能要求。',
    references:[{
      evidence_id:'evidence-1',
      source_object_type:'matching_evidence',
      source_object_id:'evaluation-1',
      source_document_id:'evaluation-report-1',
      quote:'候选人满足核心技能要求。',
      location_start:0,
      location_end:14,
      occurrence_index:0,
      alignment:'exact',
      graph_version_id:null,
      graph_version:'GRAPH_V1',
      business_version:null,
      source_version:'evaluation-1',
      tenant_ref:'tenant-a',
      permission_scope:'personal:user-1',
    }],
    provider:'main-system-bff',
    model:'deepseek-v4-flash',
    model_version:'deepseek-evidence-rag-answer.v1',
    trace_id:'trace-1',
    error:null,
    explanation_only:true,
    graph_version_id:null,
    graph_version:'GRAPH_V1',
    business_version:null,
    permission:{user_id:'user-1',tenant_ref:'tenant-a',permission_scope:'personal:user-1',assembled_by:'main-system-bff'},
  };
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('admin',adminPermissions));
    if(url.endsWith('/api/v1/rag/evidence'))return response(ragAnswered);
    if(url.endsWith('/api/v1/rag/evidence/citations/resolve'))return response({
      contract_version:'evidence-citation-resolution.v1',
      target_route:'/matching/reports/evaluation-1?citationEvidenceId=evidence-1&citationSourceVersion=evaluation-1&citationGraphVersion=GRAPH_V1',
      resource_id:'evaluation-1',version_id:'GRAPH_V1',evidence_id:'evidence-1',start:0,end:14,
      highlight_text:'候选人满足核心技能要求。',source_object_type:'matching_evidence',source_object_id:'evaluation-1',
      source_document_id:'evaluation-report-1',source_version:'evaluation-1',graph_version_id:null,graph_version:'GRAPH_V1',business_version:null,
    });
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/evidence/assistant?objectType=matching_evaluation&objectId=evaluation-1&objectVersion=GRAPH_V1&versionKind=graph_version&evidenceTypes=matching_evidence&returnTo=%2Fmatching%2Freports%2Fevaluation-1"/>);

  expect(await screen.findByRole('button',{name:'新建对话'})).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'候选人是否满足要求？'}});
  fireEvent.click(screen.getByRole('button',{name:/发送/}));

  expect((await screen.findAllByText('候选人满足核心技能要求。')).length).toBeGreaterThan(0);
  fireEvent.click(await screen.findByRole('button',{name:/查看全部来源/}));
  expect(await screen.findByText('来源证据')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'查看上下文'})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('来源上下文')).not.toBeInTheDocument();
  expect(screen.getAllByText('候选人满足核心技能要求。').length).toBeGreaterThan(0);
  expect(screen.queryByRole('link',{name:'查看原文并定位证据'})).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/rag/evidence',expect.objectContaining({method:'POST'}));
  expect(fetchMock.mock.calls.some(([url])=>String(url).includes('/api/v1/evidence/retrieve')||String(url).includes('/api/v1/evidence/generate'))).toBe(false);
},30000);

test('匹配报告页提供带冻结参数的证据问答入口',async()=>{
  localStorage.setItem('main_access_token','token');
  localStorage.removeItem('jobpulse.rag.chat.sessions.v1');
  const fetchMock=vi.fn((url:string)=>{
    if(url.endsWith('/api/v1/auth/me'))return response(user('personal_user',personalPermissions));
    if(url.endsWith('/api/v1/matches/reports/evaluation-1'))return response(matchReportFixture());
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<Root initialPath="/matching/reports/evaluation-1"/>);

  expect(await screen.findByText('岗位匹配报告')).toBeInTheDocument();
  expect(screen.queryByText('GRAPH_V1')).not.toBeInTheDocument();
  expect(screen.getAllByText('证据问答').length).toBeGreaterThan(0);
  const entry=await screen.findByRole('button',{name:/证据问答/});
  fireEvent.click(entry);

  expect(await screen.findByRole('button',{name:'新建对话'})).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/matches/reports/evaluation-1',expect.anything());
});
