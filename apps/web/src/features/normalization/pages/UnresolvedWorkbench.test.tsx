import {App as AntApp} from 'antd';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {beforeEach,expect,test,vi} from 'vitest';

import {SystemNoticeHost} from '../../../shared/components/States';
import {UnresolvedWorkbench} from './UnresolvedWorkbench';


const unresolved={
  id:'unresolved-1',parse_result_id:'parse-1',jd_id:'jd-1',jd_title:'后端工程师',
  source_name:'Py',requirement_id:'req-1',reason:'目录未精确命中',source_type:'jd',
  source_name_label:'测试 JD',raw_text:'负责 Python 后端服务开发',
};
const catalog=[
  {skill_id:'python',skill_name:'Python',category:'编程语言',description:null},
  {skill_id:'java',skill_name:'Java',category:'编程语言',description:null},
];
const suggestions=[{
  skill_id:'python',skill_name:'Python',category:'编程语言',rank:1,lexical_score:.98,
  semantic_score:.91,combined_score:.9485,matched_alias:'Py',
  reasons:['技能别名精确命中','语义相似'],semantic_available:true,
}];
const response=(data:unknown,status=200)=>Promise.resolve({
  ok:status<400,status,statusText:status<400?'OK':'error',
  json:async()=>({code:status<400?0:1,message:status<400?'success':'推荐服务异常',data,trace_id:'test'}),
});

beforeEach(()=>{cleanup();localStorage.clear();vi.restoreAllMocks()});

test('打开匹配 Modal 请求 Top-K，选择推荐后仍调用原人工确认接口',async()=>{
  const fetchMock=vi.fn((...args:[string,RequestInit?])=>{
    const [url]=args;
    if(url.endsWith('/api/v1/review-tasks/unresolved-skills'))return response([unresolved]);
    if(url.endsWith('/api/v1/skills'))return response(catalog);
    if(url.endsWith('/api/v1/skills/normalization-suggestions'))return response(suggestions);
    if(url.endsWith('/api/v1/jd-parse-results/parse-1/skill-catalog-mappings'))return response({});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<AntApp><SystemNoticeHost/><UnresolvedWorkbench/></AntApp>);

  fireEvent.click(await screen.findByRole('button',{name:'匹配标准技能'}));
  expect(await screen.findByText('Hybrid 排序')).toBeInTheDocument();
  expect(screen.getByText('0.949')).toBeInTheDocument();
  expect(screen.getByText('alias：Py')).toBeInTheDocument();
  fireEvent.click(screen.getByText('Python',{selector:'strong'}).closest('button')!);
  fireEvent.click(screen.getByRole('button',{name:'确认映射并保存'}));

  await waitFor(()=>expect(fetchMock.mock.calls.some(([url,init])=>
    String(url).endsWith('/api/v1/jd-parse-results/parse-1/skill-catalog-mappings')&&
    JSON.parse(String((init as RequestInit).body)).target_skill_id==='python'
  )).toBe(true));
  const suggestionCall=fetchMock.mock.calls.find(([url])=>String(url).endsWith('/api/v1/skills/normalization-suggestions'))!;
  expect(JSON.parse(String((suggestionCall[1] as RequestInit).body))).toMatchObject({raw_skill:'Py',top_k:5});
});

test('推荐失败时保留手动搜索与仅保留原文流程',async()=>{
  const fetchMock=vi.fn((...args:[string,RequestInit?])=>{
    const [url]=args;
    if(url.endsWith('/api/v1/review-tasks/unresolved-skills'))return response([unresolved]);
    if(url.endsWith('/api/v1/skills'))return response(catalog);
    if(url.endsWith('/api/v1/skills/normalization-suggestions'))return response(null,503);
    if(url.endsWith('/api/v1/jd-parse-results/parse-1/skill-catalog-exclusions'))return response({});
    return response([]);
  });
  vi.stubGlobal('fetch',fetchMock);
  render(<AntApp><SystemNoticeHost/><UnresolvedWorkbench/></AntApp>);

  fireEvent.click(await screen.findByRole('button',{name:'匹配标准技能'}));
  expect(await screen.findByText('候选推荐暂不可用')).toBeInTheDocument();
  expect(screen.getByPlaceholderText('输入技能名称或类别')).toHaveValue('Py');
  fireEvent.click(screen.getByRole('button',{name:'没有合适技能，仅保留原文'}));
  const dialog=(await screen.findAllByRole('dialog')).at(-1)!;
  fireEvent.change(within(dialog).getByLabelText('排除原因'),{target:{value:'不是可映射技能'}});
  fireEvent.click(within(dialog).getByRole('button',{name:'确认不进入下游'}));

  await waitFor(()=>expect(fetchMock.mock.calls.some(([url])=>String(url).endsWith('/api/v1/jd-parse-results/parse-1/skill-catalog-exclusions'))).toBe(true));
});
