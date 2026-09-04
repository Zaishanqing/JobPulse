import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {App} from 'antd';
import {MemoryRouter} from 'react-router-dom';
import {beforeEach,expect,test,vi} from 'vitest';
import {AuthProvider} from '../auth/AuthContext';
import {SystemNoticeHost} from '../../shared/components/States';
import {AcquisitionWorkbench} from './pages/AcquisitionWorkbench';
import type {AcquisitionJob,AcquisitionJobPage,AcquisitionSourceStatus} from './types';

const sources:AcquisitionSourceStatus[]=[
  {source:'boss',available:true,ready:false,login_required:true,reason:'Boss login is required on the crawler service'},
  {source:'liepin',available:true,ready:false,login_required:true,reason:'Liepin login is required on the crawler service'},
  {source:'feishu',available:true,ready:true,login_required:false,reason:null},
];
const job:AcquisitionJob={
  id:'job-1',requested_by:'user-1',source:'boss',keyword:'Java',city:'北京',pages:2,
  status:'import_failed',progress:0.9,crawler_task_id:'task-1',bundle_id:'bundle-1',
  bundle_file_name:'bundle.zip',bundle_hash:'abc',discovered_count:3,exported_count:3,
  imported_count:1,no_op_count:1,failed_count:1,import_batch_id:'batch-1',
  error_code:'ACQUISITION_IMPORT_FAILED',error_message:'import boom',retry_of_id:null,
  attempt:1,created_at:'2026-08-17T00:00:00Z',updated_at:'2026-08-17T00:00:00Z',
  started_at:'2026-08-17T00:00:00Z',finished_at:'2026-08-17T00:00:00Z',
};
const page:AcquisitionJobPage={items:[job],total:1,page:1,page_size:20};
const adminUser={user_id:'user-1',username:'admin',role:'admin',permissions:['acquisition.read','acquisition.job.manage']};

function response(data:unknown,status=200){
  return Promise.resolve({
    ok:status<400,status,statusText:status<400?'OK':'error',
    json:async()=>({code:status<400?0:status,message:status<400?'success':'error',data,details:{},trace_id:'test'}),
  });
}

beforeEach(()=>{
  cleanup();
  localStorage.clear();
  localStorage.setItem('main_access_token','token');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderPage(){
  return render(<MemoryRouter><App><SystemNoticeHost/><AuthProvider><AcquisitionWorkbench/></AuthProvider></App></MemoryRouter>);
}

test('renders sources with paste-cookie login and allows retry from failure',async()=>{
  const fetchMock=vi.fn((url:string, init?:RequestInit)=>{
    void init;
    if(url.endsWith('/api/v1/auth/me'))return response(adminUser);
    if(url.endsWith('/api/v1/acquisition/sources'))return response(sources);
    if(url.includes('/api/v1/acquisition/jobs?')||url.endsWith('/api/v1/acquisition/jobs'))return response(page);
    if(url.includes('/api/v1/acquisition/jobs/job-1'))return response(job);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderPage();

  expect((await screen.findAllByText('Boss 直聘', {}, {timeout:5000})).length).toBeGreaterThan(0);
  expect(screen.getAllByText('需要在采集服务端完成登录').length).toBe(2);
  expect(screen.queryByText('Boss login is required on the crawler service')).not.toBeInTheDocument();
  expect(screen.queryByText('Liepin login is required on the crawler service')).not.toBeInTheDocument();
  expect(screen.getAllByRole('button',{name:'粘贴 Cookie'}).length).toBe(2);
  expect(document.querySelectorAll('.acquisition-section-card')).toHaveLength(3);
  expect(document.querySelectorAll('.acquisition-source-grid > .acquisition-source-card')).toHaveLength(3);
  expect(document.querySelectorAll('.acquisition-source-card-action')).toHaveLength(3);
  expect(screen.getByRole('button',{name:'前往 JD 数据中心'})).toBeInTheDocument();
  expect(screen.getByText('创建采集任务')).toBeInTheDocument();
  expect(screen.getByText('Java')).toBeInTheDocument();

  fireEvent.click(screen.getByText('Java'));
  expect((await screen.findAllByText('采集任务执行失败')).length).toBeGreaterThan(0);
  expect(screen.queryByText('import boom')).not.toBeInTheDocument();
  expect(await screen.findByText('系统处理失败，请稍后重试。')).toBeInTheDocument();
  const retryButton=screen.getByRole('button',{name:/重试/});
  expect(retryButton).toBeEnabled();
  fireEvent.click(retryButton);

  await waitFor(()=>expect(fetchMock.mock.calls.some(([url,init])=>String(url).endsWith('/api/v1/acquisition/jobs/job-1/retry')&&(init as RequestInit)?.method==='POST')).toBe(true));
});

test('provenance uses state machine not id presence while crawling',async()=>{
  const crawlingJob:AcquisitionJob={...job,id:'job-crawling',status:'crawling',progress:0.2,crawler_task_id:'task-crawling',bundle_id:null,bundle_file_name:null,bundle_hash:null,imported_count:0,no_op_count:0,failed_count:0,import_batch_id:null,error_code:null,error_message:null};
  const crawlingPage:AcquisitionJobPage={items:[crawlingJob],total:1,page:1,page_size:20};
  const fetchMock=vi.fn((url:string, init?:RequestInit)=>{
    void init;
    if(url.endsWith('/api/v1/auth/me'))return response(adminUser);
    if(url.endsWith('/api/v1/acquisition/sources'))return response(sources);
    if(url.includes('/api/v1/acquisition/jobs?')||url.endsWith('/api/v1/acquisition/jobs'))return response(crawlingPage);
    if(url.includes('/api/v1/acquisition/jobs/job-crawling'))return response(crawlingJob);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderPage();
  fireEvent.click((await screen.findAllByText('Java'))[0]);
  const crawlerLabels=await screen.findAllByText('网页采集');
  expect(crawlerLabels.length).toBeGreaterThan(0);
  const crawlerRow=crawlerLabels
    .map(label=>label.closest('tr'))
    .find(row=>row?.textContent?.includes('进行中'));
  expect(crawlerRow?.textContent).toContain('进行中');
  expect(crawlerRow?.textContent).not.toContain('完成');
});

test('create form posts boss keyword/city task',async()=>{
  const fetchMock=vi.fn((url:string, init?:RequestInit)=>{
    void init;
    if(url.endsWith('/api/v1/auth/me'))return response(adminUser);
    if(url.endsWith('/api/v1/acquisition/sources'))return response(sources);
    if(url.endsWith('/api/v1/acquisition/jobs')&&(init as RequestInit)?.method==='POST')return response(job);
    if(url.endsWith('/api/v1/acquisition/jobs/job-1'))return response(job);
    if(url.includes('/api/v1/acquisition/jobs'))return response(page);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderPage();
  await screen.findAllByText('Boss 直聘');

  fireEvent.change(screen.getByPlaceholderText('关键词，如 Java'),{target:{value:'Python'}});
  fireEvent.change(screen.getByPlaceholderText('城市，如 北京'),{target:{value:'上海'}});
  fireEvent.click(screen.getByRole('button',{name:'创建任务'}));

  await waitFor(()=>{
    const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/api/v1/acquisition/jobs')&&(init as RequestInit)?.method==='POST');
    expect(call).toBeTruthy();
    const body=JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toMatchObject({source:'boss',keyword:'Python',city:'上海',pages:5});
  });
});

test('feishu create form hides keyword/city and posts defaults',async()=>{
  const fetchMock=vi.fn((url:string, init?:RequestInit)=>{
    void init;
    if(url.endsWith('/api/v1/auth/me'))return response(adminUser);
    if(url.endsWith('/api/v1/acquisition/sources'))return response(sources);
    if(url.endsWith('/api/v1/acquisition/jobs')&&(init as RequestInit)?.method==='POST')return response(job);
    if(url.endsWith('/api/v1/acquisition/jobs/job-1'))return response(job);
    if(url.includes('/api/v1/acquisition/jobs'))return response(page);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderPage();
  await screen.findAllByText('Boss 直聘');

  fireEvent.mouseDown(screen.getAllByRole('combobox')[0]);
  const option=await waitFor(()=>{
    const candidate=[...document.querySelectorAll<HTMLElement>('.ant-select-item-option')]
      .find(el=>el.textContent?.includes('飞书招聘'));
    if(!candidate)throw new Error('no feishu select option');
    return candidate;
  });
  fireEvent.click(option);

  await waitFor(()=>expect(screen.queryByPlaceholderText('关键词，如 Java')).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'创建任务'}));

  await waitFor(()=>{
    const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/api/v1/acquisition/jobs')&&(init as RequestInit)?.method==='POST');
    expect(call).toBeTruthy();
    const body=JSON.parse(String((call![1] as RequestInit).body));
    expect(body).toMatchObject({source:'feishu',keyword:'all',city:'全国',pages:1});
  });
});

test('paste boss cookies posts to cookies endpoint and refreshes sources',async()=>{
  const fetchMock=vi.fn((url:string, init?:RequestInit)=>{
    void init;
    if(url.endsWith('/api/v1/auth/me'))return response(adminUser);
    if(url.endsWith('/api/v1/acquisition/sources'))return response(sources);
    if(url.endsWith('/api/v1/acquisition/boss/cookies')&&(init as RequestInit)?.method==='POST')return response({saved:true,count:2,verified:true});
    if(url.includes('/api/v1/acquisition/jobs'))return response(page);
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  renderPage();
  await screen.findAllByText('Boss 直聘');

  fireEvent.click(screen.getAllByRole('button',{name:'粘贴 Cookie'})[0]);
  const textarea=await screen.findByPlaceholderText('[{"name":"wt2","value":"...","domain":".zhipin.com"}]');
  fireEvent.change(textarea,{target:{value:'[{"name":"wt2","value":"x"}]'}});
  fireEvent.click(screen.getByRole('button',{name:'保存并验证'}));

  await waitFor(()=>{
    const call=fetchMock.mock.calls.find(([url,init])=>String(url).endsWith('/api/v1/acquisition/boss/cookies')&&(init as RequestInit)?.method==='POST');
    expect(call).toBeTruthy();
    const body=JSON.parse(String((call![1] as RequestInit).body));
    expect(body.cookies).toEqual([{name:'wt2',value:'x'}]);
  });
});
