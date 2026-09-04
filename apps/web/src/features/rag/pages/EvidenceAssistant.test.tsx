import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,test,vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {App} from 'antd';
import type {EvidenceRAGResponseV1} from '../types';
import {ragHistoryStorageKey} from '../history';
import {EvidenceAssistant} from './EvidenceAssistant';
import {SystemNoticeHost} from '../../../shared/components/States';

const testAuth=vi.hoisted(()=>({
  user:null as {user_id:string;username:string;role:string;permissions:string[]}|null,
}));

vi.mock('../../auth/AuthContext',()=>({useAuth:()=>testAuth}));

const baseUrl='/evidence/assistant?objectType=matching_evaluation&objectId=evaluation-1&objectVersion=GRAPH_V1&versionKind=graph_version&evidenceTypes=matching_evidence,cv_evidence&returnTo=%2Fmatching%2Freports%2Fevaluation-1';

const testUser=(userId:string)=>({user_id:userId,username:userId,role:'personal_user',permissions:[]});

const permission={user_id:'user-1',tenant_ref:'tenant-a',permission_scope:'personal:user-1',assembled_by:'main-system-bff' as const};

const answered:EvidenceRAGResponseV1={
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
  version_scope:'single_object',
  graph_version_id:null,
  graph_version:'GRAPH_V1',
  business_version:null,
  permission,
};

const insufficient:EvidenceRAGResponseV1={
  ...answered,
  status:'insufficient_evidence',
  answer:null,
  references:[],
  error:{code:'EVIDENCE_NOT_FOUND',message:'当前版本范围内没有匹配的正式 Evidence。'},
};

const failed=(code:string,message:string):EvidenceRAGResponseV1=>({
  ...answered,
  status:'failed',
  answer:null,
  references:[],
  error:{code,message},
});

const citationResolved={
  contract_version:'evidence-citation-resolution.v1',
  target_route:'/matching/reports/evaluation-1?citationEvidenceId=evidence-1&citationSourceVersion=evaluation-1&citationGraphVersion=GRAPH_V1',
  resource_id:'evaluation-1',version_id:'GRAPH_V1',evidence_id:'evidence-1',start:0,end:14,
  highlight_text:'候选人满足核心技能要求。',source_object_type:'matching_evidence',source_object_id:'evaluation-1',
  source_document_id:'evaluation-report-1',source_version:'evaluation-1',graph_version_id:null,graph_version:'GRAPH_V1',business_version:null,
};

const answeredWithManySources:EvidenceRAGResponseV1={
  ...answered,
  references:[
    {...answered.references[0],evidence_id:'jd-evidence-1',source_object_type:'source_jd'},
    {...answered.references[0],evidence_id:'graph-evidence-1',source_object_type:'kg_skill_relation_evidence'},
    {...answered.references[0],evidence_id:'matching-evidence-1',source_object_type:'matching_evidence'},
    {...answered.references[0],evidence_id:'trend-evidence-1',source_object_type:'trend_evidence'},
  ],
};

const response=(data:unknown,status=200)=>({
  ok:status<400,
  status,
  statusText:status<400?'OK':'error',
  json:async()=>({code:status<400?0:1,message:status<400?'success':'failed',data,details:{},trace_id:'trace'}),
});

function deferred<T>(){
  let resolve:(value:T)=>void=()=>undefined;
  const promise=new Promise<T>(value=>{resolve=value});
  return {promise,resolve};
}

const pageElement=(url=baseUrl)=><App><SystemNoticeHost/><MemoryRouter initialEntries={[url]}><EvidenceAssistant/></MemoryRouter></App>;
const renderPage=(url=baseUrl)=>render(pageElement(url));

const askQuestion=async(text:string)=>{
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:text}});
  fireEvent.click(screen.getByRole('button',{name:/发送/}));
  await screen.findByText('候选人满足核心技能要求。');
};

const ask=async()=>{
  fireEvent.change(screen.getByLabelText('问题'),{target:{value:'候选人是否满足要求？'}});
  fireEvent.click(screen.getByRole('button',{name:/发送/}));
};

afterEach(()=>{
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

beforeEach(()=>{
  testAuth.user=testUser('user-a');
});

describe('EvidenceAssistant',()=>{
  test('answered 展示回答、来源 Drawer 与页内来源上下文，且只调用新 BFF',async()=>{
    const sourceRef={...answered.references[0],
      evidence_id:'jd-context-1',source_object_type:'source_jd',source_object_id:'JD_CONTEXT',
      source_document_id:'JD_CONTEXT',quote:'Python',location_start:10,location_end:16,
    };
    const sourceAnswered={...answered,references:[sourceRef]};
    const sourceCitation={...citationResolved,
      evidence_id:sourceRef.evidence_id,source_object_type:'source_jd',source_object_id:'JD_CONTEXT',
      source_document_id:'JD_CONTEXT',start:10,end:16,highlight_text:'Python',
    };
    const fetchMock=vi.fn(async(url:string)=>{
      if(url.endsWith('/api/v1/jds/JD_CONTEXT'))return response({raw_text:'岗位要求：熟练使用 Python 开发后端服务。'});
      if(url.includes('/citations/resolve'))return response(sourceCitation);
      return response(sourceAnswered);
    });
    vi.stubGlobal('fetch',fetchMock);
    renderPage();

    await ask();

    expect((await screen.findAllByText('候选人满足核心技能要求。')).length).toBeGreaterThan(0);
    expect(screen.getByRole('button',{name:/查看全部来源/})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:/查看全部来源/}));
    expect(await screen.findByText('来源证据')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button',{name:'查看上下文'}));
    expect(await screen.findByLabelText('来源上下文')).toBeInTheDocument();
    expect(screen.getByText('相关来源上下文')).toBeInTheDocument();
    expect(screen.getByRole('blockquote')).toHaveTextContent('岗位要求：熟练使用 Python 开发后端服务。');
    expect(screen.queryByText(/对齐：|权限：|岗位原文|原文定位/)).not.toBeInTheDocument();
    expect(screen.queryByRole('link',{name:'查看原文并定位证据'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:/返回来源/}));
    fireEvent.click(screen.getByRole('button',{name:'更多信息'}));
    expect(await screen.findByText('运行信息')).toBeInTheDocument();
    expect(screen.queryByText('deepseek-v4-flash · deepseek-evidence-rag-answer.v1')).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/rag/evidence',expect.objectContaining({method:'POST'}));
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/rag/evidence/citations/resolve',expect.objectContaining({method:'POST'}));
  });

  test('来源按类型聚合，最多展示三个 chip，其余收起到 Drawer',async()=>{
    const fetchMock=vi.fn(async(url:string)=>response(url.includes('/citations/resolve')?citationResolved:answeredWithManySources));
    vi.stubGlobal('fetch',fetchMock);
    renderPage();

    await ask();

    expect(await screen.findByRole('button',{name:'岗位证据 1 条来源'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'岗位图谱 1 条来源'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'匹配分析 1 条来源'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'查看其余 1 条来源'})).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'查看其余 1 条来源'}));

    expect(await screen.findByText('来源证据')).toBeInTheDocument();
    expect(screen.queryByText(/原文位置：|对齐：|权限：/)).not.toBeInTheDocument();
  });

  test('岗位来源在当前 Drawer 展示真实连续原文并高亮引用',async()=>{
    const jdAnswered:EvidenceRAGResponseV1={
      ...answered,
      references:[{...answered.references[0],evidence_id:'jd-context-1',source_object_type:'source_jd',source_object_id:'JD_CONTEXT',source_document_id:'JD_CONTEXT',quote:'Python',location_start:10,location_end:16}],
    };
    const jdCitation={...citationResolved,evidence_id:'jd-context-1',start:10,end:16,highlight_text:'Python',source_object_type:'source_jd',source_object_id:'JD_CONTEXT',source_document_id:'JD_CONTEXT'};
    const fetchMock=vi.fn(async(url:string)=>{
      if(url.endsWith('/api/v1/jds/JD_CONTEXT'))return response({raw_text:'岗位要求：熟练使用 Python 开发后端服务。'});
      if(url.includes('/citations/resolve'))return response(jdCitation);
      return response(jdAnswered);
    });
    vi.stubGlobal('fetch',fetchMock);
    renderPage();

    await ask();
    fireEvent.click(await screen.findByRole('button',{name:/查看全部来源/}));
    fireEvent.click(await screen.findByRole('button',{name:'查看上下文'}));

    expect(await screen.findByText('Python',{selector:'mark'})).toBeInTheDocument();
    expect(screen.getByRole('blockquote')).toHaveTextContent('岗位要求：熟练使用 Python 开发后端服务。');
    expect(screen.queryByText(/对齐：|权限：|图谱版本：|岗位原文|原文定位/)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/api/v1/jds/JD_CONTEXT',expect.anything());
  });

  test('insufficient_evidence 追加拒答消息且保留历史',async()=>{
    let call=0;
    const fetchMock=vi.fn(async(url:string)=>response(url.includes('/citations/resolve')?citationResolved:call++===0?answered:insufficient));
    vi.stubGlobal('fetch',fetchMock);
    renderPage();

    await ask();
    expect((await screen.findAllByText('候选人满足核心技能要求。')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('问题'),{target:{value:'换一个问题'}});
    await waitFor(()=>expect(screen.getByRole('button',{name:/发送/})).toBeEnabled());
    fireEvent.click(screen.getByRole('button',{name:/发送/}));

    expect(await screen.findByText('证据不足')).toBeInTheDocument();
    expect(screen.queryByText(/EVIDENCE_NOT_FOUND：/)).not.toBeInTheDocument();
    expect(screen.getByText('如果这是刚发布的图谱，检索索引可能仍在后台建立。')).toBeInTheDocument();
    expect(await screen.findByText('换一个问题')).toBeInTheDocument();
    expect(screen.getAllByText('候选人满足核心技能要求。').length).toBeGreaterThan(0);
    const calls=fetchMock.mock.calls as unknown as [string,RequestInit][];
    const ragCalls=calls.filter(([url])=>String(url).endsWith('/api/v1/rag/evidence'));
    const secondBody=JSON.parse(String(ragCalls[1]?.[1]?.body));
    expect(secondBody.conversation_history).toEqual(expect.arrayContaining([
      {role:'user',text:'候选人是否满足要求？'},
      {role:'assistant',text:'候选人满足核心技能要求。'},
    ]));
  });

  test.each([
    ['PERMISSION_DENIED','权限不足'],
    ['EMBEDDING_REVISION_MISMATCH','版本或配置不一致'],
    ['LLM_PROVIDER_UNAVAILABLE','模型服务暂时不可用'],
    ['EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID','证据响应版本校验失败'],
  ])('%s 按错误码展示独立失败状态',async(code,label)=>{
    vi.stubGlobal('fetch',vi.fn(async()=>response(failed(code,'detail'))));
    renderPage();

    await ask();

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(code))).not.toBeInTheDocument();
    expect(await screen.findByText('系统处理失败，请稍后重试。')).toBeInTheDocument();
    expect(screen.queryByText('候选人满足核心技能要求。')).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:/查看全部来源/})).not.toBeInTheDocument();
  });

  test('缺少对象或唯一版本时禁用提问，且不提供手填对象输入',async()=>{
    renderPage('/evidence/assistant?objectType=matching_evaluation&objectId=evaluation-1&versionKind=graph_version');

    expect(screen.getByRole('button',{name:/发送/})).toBeDisabled();
    expect(screen.queryByPlaceholderText(/手填|手动输入/)).not.toBeInTheDocument();
  });

  test('多岗位问答使用各岗位自己的当前发布版本',async()=>{
    const positions=[
      {position_id:'position-1',name:'后端工程师',category_code:'TECH',current_version_id:7,current_version_number:3},
      {position_id:'position-2',name:'大模型工程师',category_code:'TECH',current_version_id:11,current_version_number:5},
    ];
    const fetchMock=vi.fn(async(url:string)=>{
      if(url.endsWith('/api/v1/portal/positions'))return response(positions);
      if(url.endsWith('/api/v1/rag/evidence'))return response(answered);
      return response([]);
    });
    vi.stubGlobal('fetch',fetchMock);
    renderPage('/evidence/assistant');

    const contextButton=screen.getByRole('button',{name:'添加上下文'});
    fireEvent.click(contextButton);
    expect(await screen.findByRole('dialog',{name:'添加上下文'})).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('checkbox',{name:'后端工程师'}));
    fireEvent.click(await screen.findByRole('checkbox',{name:'大模型工程师'}));
    fireEvent.click(contextButton);
    expect(await screen.findByText('后端工程师')).toBeInTheDocument();
    expect(await screen.findByText('大模型工程师')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('问题'),{target:{value:'比较两个岗位'}});
    fireEvent.click(screen.getByRole('button',{name:/发送/}));

    await screen.findByRole('button',{name:/查看全部来源/});
    const calls=fetchMock.mock.calls as unknown as [string,RequestInit][];
    const ragCall=calls.find(([url])=>url.endsWith('/api/v1/rag/evidence'));
    const body=JSON.parse(String(ragCall?.[1]?.body));
    expect(body.version_scope).toBe('multi_object');
    expect(body.graph_version_id).toBeUndefined();
    expect(body.business_objects).toEqual([
      {object_type:'standard_position',object_id:'position-1',object_version:'7'},
      {object_type:'standard_position',object_id:'position-2',object_version:'11'},
    ]);
  });

  test('会话独立保存消息与上下文，并可新建、切换和恢复',async()=>{
    const positions=[{
      position_id:'position-1',
      name:'后端工程师',
      category_code:'TECH',
      current_version_id:7,
    }];
    const fetchMock=vi.fn(async(url:string)=>{
      if(url.endsWith('/api/v1/portal/positions'))return response(positions);
      if(url.endsWith('/api/v1/rag/evidence'))return response(answered);
      return response([]);
    });
    vi.stubGlobal('fetch',fetchMock);
    renderPage('/evidence/assistant');

    const contextButton=screen.getByRole('button',{name:'添加上下文'});
    fireEvent.click(contextButton);
    fireEvent.click(await screen.findByRole('checkbox',{name:'后端工程师'}));
    fireEvent.click(contextButton);
    fireEvent.change(screen.getByLabelText('问题'),{target:{value:'比较岗位证据'}});
    fireEvent.click(screen.getByRole('button',{name:/发送/}));

    expect(await screen.findByText('候选人满足核心技能要求。')).toBeInTheDocument();
    expect(await screen.findByText('后端工程师')).toBeInTheDocument();
    const stored=JSON.parse(String(window.localStorage.getItem(ragHistoryStorageKey('user-a'))));
    expect(stored.sessions).toHaveLength(1);
    expect(stored.sessions[0].attachedPositionIds).toEqual(['position-1']);
    expect(stored.sessions[0].attachedPositionVersions).toEqual({'position-1':7});

    fireEvent.click(screen.getByRole('button',{name:'新建对话'}));
    expect(screen.getByPlaceholderText('先通过 + 添加一个岗位上下文')).toBeInTheDocument();
    expect(screen.queryByText('比较岗位证据')).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText('先通过 + 添加一个岗位上下文')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button',{name:/历史/}));
    const historyEntries=await screen.findAllByRole('button',{name:/比较岗位证据/});
    fireEvent.click(historyEntries[0]);
    expect(await screen.findByText('候选人满足核心技能要求。')).toBeInTheDocument();
    expect(screen.getByText('候选人满足核心技能要求。')).toBeInTheDocument();

    const storedAfterSwitch=JSON.parse(String(window.localStorage.getItem(ragHistoryStorageKey('user-a'))));
    expect(storedAfterSwitch.sessions).toHaveLength(2);
  });

  test('历史按 user_id 隔离，切换账号后只加载对应 namespace',async()=>{
    const fetchMock=vi.fn(async()=>response(answered));
    vi.stubGlobal('fetch',fetchMock);
    const view=renderPage();

    await askQuestion('A 的第一问');
    await askQuestion('A 的第二问');
    const userAState=JSON.parse(String(window.localStorage.getItem(ragHistoryStorageKey('user-a'))));
    expect(userAState.sessions[0].messages.filter((message:{role:string})=>message.role==='user')).toHaveLength(2);
    expect(window.localStorage.getItem(ragHistoryStorageKey('user-b'))).toBeNull();

    testAuth.user=testUser('user-b');
    view.rerender(pageElement());
    await waitFor(()=>{
      expect(screen.queryByText('A 的第一问')).not.toBeInTheDocument();
      expect(screen.queryByText('A 的第二问')).not.toBeInTheDocument();
    });
    await waitFor(()=>expect(window.localStorage.getItem(ragHistoryStorageKey('user-b'))).not.toBeNull());
    await askQuestion('B 的问题');
    expect(window.localStorage.getItem(ragHistoryStorageKey('user-b'))).toContain('B 的问题');

    testAuth.user=testUser('user-a');
    view.rerender(pageElement());
    expect(await screen.findByText('A 的第一问')).toBeInTheDocument();
    expect(screen.getByText('A 的第二问')).toBeInTheDocument();
    expect(screen.queryByText('B 的问题')).not.toBeInTheDocument();
  });

  test('logout 清空当前页面内存，旧账号响应不会写入新账号会话',async()=>{
    const pending=deferred<ReturnType<typeof response>>();
    const fetchMock=vi.fn((url:string)=>url.endsWith('/api/v1/rag/evidence')?pending.promise:Promise.resolve(response([])));
    vi.stubGlobal('fetch',fetchMock);
    const view=renderPage();

    fireEvent.change(screen.getByLabelText('问题'),{target:{value:'退出前的问题'}});
    fireEvent.click(screen.getByRole('button',{name:/发送/}));
    await waitFor(()=>expect(fetchMock).toHaveBeenCalled());

    testAuth.user=null;
    view.rerender(pageElement());
    expect(screen.queryByText('退出前的问题')).not.toBeInTheDocument();
    expect(screen.queryByText('候选人满足核心技能要求。')).not.toBeInTheDocument();

    testAuth.user=testUser('user-b');
    view.rerender(pageElement());
    await waitFor(()=>expect(screen.queryByText('退出前的问题')).not.toBeInTheDocument());
    pending.resolve(response(answered));
    await waitFor(()=>expect(screen.queryByText('退出前的问题')).not.toBeInTheDocument());
    expect(screen.queryByText('候选人满足核心技能要求。')).not.toBeInTheDocument();
  });

  test('只读取当前用户当前会话的 conversation_history',async()=>{
    const ragBodies:Record<string,unknown>[]=[];
    const fetchMock=vi.fn(async(url:string,init?:RequestInit)=>{
      if(url.endsWith('/api/v1/rag/evidence'))ragBodies.push(JSON.parse(String(init?.body)) as Record<string,unknown>);
      return response(answered);
    });
    vi.stubGlobal('fetch',fetchMock);
    const view=renderPage();

    await askQuestion('A 的会话问题');
    testAuth.user=testUser('user-b');
    view.rerender(pageElement());
    await waitFor(()=>expect(screen.queryByText('A 的会话问题')).not.toBeInTheDocument());

    await askQuestion('B 的第一问');
    await askQuestion('B 的第二问');
    const bFirst=ragBodies.find(body=>body.query_text==='B 的第一问');
    const bSecond=ragBodies.find(body=>body.query_text==='B 的第二问');
    expect(bFirst?.conversation_history).toEqual([]);
    expect(bSecond?.conversation_history).toEqual([
      {role:'user',text:'B 的第一问'},
      {role:'assistant',text:'候选人满足核心技能要求。'},
    ]);
    expect(JSON.stringify(bSecond?.conversation_history)).not.toContain('A 的会话问题');
  });

  test('不加载旧全局 history key，也不把旧记录迁移到账号 namespace',async()=>{
    window.localStorage.setItem('jobpulse.rag.chat.sessions.v1',JSON.stringify({
      activeSessionId:'legacy-session',
      sessions:[{id:'legacy-session',title:'旧全局记录',messages:[{id:'legacy-message',createdAt:new Date().toISOString(),role:'user',text:'旧全局记录'}]},],
    }));
    window.localStorage.setItem('jobpulse:rag-history',JSON.stringify({sessions:[]}));
    renderPage();

    expect(screen.queryByText('旧全局记录')).not.toBeInTheDocument();
    await waitFor(()=>expect(window.localStorage.getItem('jobpulse.rag.chat.sessions.v1')).toBeNull());
    expect(window.localStorage.getItem('jobpulse:rag-history')).toBeNull();
    expect(window.localStorage.getItem(ragHistoryStorageKey('user-a'))).not.toContain('旧全局记录');
  });

  test('模型运行时停止按钮可点击，且仍可新建和切换历史会话',async()=>{
    const first=deferred<ReturnType<typeof response>>();
    let ragCallCount=0;
    const fetchMock=vi.fn((url:string)=>{
      if(url.endsWith('/api/v1/rag/evidence')){
        ragCallCount+=1;
        return first.promise;
      }
      return Promise.resolve(response([]));
    });
    vi.stubGlobal('fetch',fetchMock);
    renderPage();

    fireEvent.change(screen.getByLabelText('问题'),{target:{value:'第一问'}});
    fireEvent.click(screen.getByRole('button',{name:'发送问题'}));
    await waitFor(()=>expect(ragCallCount).toBe(1));
    const stopButton=screen.getByRole('button',{name:'停止当前检索'});
    expect(stopButton).toBeEnabled();
    expect(stopButton.querySelector('.rag-stop-mark')).toBeInTheDocument();
    expect(screen.getByLabelText('问题')).toBeEnabled();
    expect(screen.getByRole('button',{name:'新建对话'})).toBeEnabled();
    expect(screen.getByRole('button',{name:/历史/})).toBeEnabled();

    fireEvent.click(screen.getByRole('button',{name:'新建对话'}));
    expect(screen.getByPlaceholderText('先通过 + 添加一个岗位上下文')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:/历史/}));
    const historyEntries=await screen.findAllByRole('button',{name:/第一问/});
    fireEvent.click(historyEntries[0]);
    expect(await screen.findByText('第一问')).toBeInTheDocument();

    first.resolve(response(answered));
    await waitFor(()=>expect(screen.getAllByText('候选人满足核心技能要求。').length).toBeGreaterThan(0));
  });
});
