import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,test,vi} from 'vitest';
import {MemoryRouter} from 'react-router-dom';
import {App} from 'antd';
import {citationRequest,EvidenceDeepLinkFocus} from './EvidenceDeepLink';
import {SystemNoticeHost} from '../../shared/components/States';

const response=(data:unknown,status=200)=>({
  ok:status<400,status,statusText:status<400?'OK':'error',
  json:async()=>({code:status<400?0:status,message:status<400?'success':'failed',data,trace_id:'trace-citation'}),
});

const resolution=(resourceId:string,targetRoute:string)=>({
  contract_version:'evidence-citation-resolution.v1',target_route:targetRoute,
  resource_id:resourceId,version_id:'source-v2',evidence_id:'evidence-42',start:11,end:29,
  highlight_text:'可核验的具体证据',source_object_type:'source',source_object_id:'source-1',
  source_document_id:'document-1',source_version:'source-v2',graph_version_id:null,graph_version:null,business_version:'business-v3',
});

const citationUrl=(path:string)=>`${path}${path.includes('?')?'&':'?'}citationEvidenceId=evidence-42&citationSourceVersion=source-v2&citationBusinessVersion=business-v3`;

beforeEach(()=>{
  Object.defineProperty(HTMLElement.prototype,'scrollIntoView',{configurable:true,value:vi.fn()});
  Object.defineProperty(HTMLElement.prototype,'focus',{configurable:true,value:vi.fn()});
});

afterEach(()=>{cleanup();vi.unstubAllGlobals()});

describe('Resume、Position、Match、JD 目标页共享 citation deep-link',()=>{
  test('同时保留图谱 ID 与名称的引用以稳定 ID 为唯一版本身份',()=>{
    expect(citationRequest({
      evidence_id:'evidence-42',source_object_type:'source_jd',source_object_id:'jd-1',source_document_id:'document-1',
      source_version:'v2',alignment:'exact',graph_version_id:7,graph_version:'release-7',business_version:null,
      tenant_ref:'jobgraph-platform-public',permission_scope:'platform:public',
    })).toEqual({evidence_id:'evidence-42',source_version:'v2',graph_version_id:7});
  });

  test.each([
    ['/profile/resumes?resumeId=resume-1','resume-1'],
    ['/positions/position-1','position-1'],
    ['/matching/reports/evaluation-1','evaluation-1'],
    ['/data/jds?jdId=jd-1','jd-1'],
  ])('刷新 %s 后恢复同一版本、Evidence 与 span',async(path,resourceId)=>{
    const target=citationUrl(path);
    const fetchMock=vi.fn(async(url:string,init?:RequestInit)=>{
      void url;void init;
      return response(resolution(resourceId,target));
    });
    vi.stubGlobal('fetch',fetchMock);

    const first=render(<MemoryRouter initialEntries={[target]}><EvidenceDeepLinkFocus resourceId={resourceId}/></MemoryRouter>);
    expect(await screen.findByText('已定位到引用证据')).toBeInTheDocument();
    expect(screen.getByText('可核验的具体证据').tagName).toBe('MARK');
    expect(screen.queryByText(/原文位置|来源版本已关联/)).not.toBeInTheDocument();
    first.unmount();

    render(<MemoryRouter initialEntries={[target]}><EvidenceDeepLinkFocus resourceId={resourceId}/></MemoryRouter>);
    expect(await screen.findByText('已定位到引用证据')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const body=JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body));
    expect(body).toEqual({evidence_id:'evidence-42',source_version:'source-v2',business_version:'business-v3'});
  });

  test.each([
    [409,'CITATION_VERSION_INVALID','Evidence 版本已经失效'],
    [403,'CITATION_PERMISSION_DENIED','无权访问该 Evidence'],
  ])('稳定展示 resolver 错误 %s / %s',async(status,errorCode,message)=>{
    vi.stubGlobal('fetch',vi.fn(async()=>response({error_code:errorCode,message},status)));
    render(<App><SystemNoticeHost/><MemoryRouter initialEntries={[citationUrl('/positions/position-1')]}><EvidenceDeepLinkFocus resourceId="position-1"/></MemoryRouter></App>);

    expect(await screen.findByText('无法恢复引用')).toBeInTheDocument();
    await waitFor(()=>expect(screen.getByText(new RegExp(errorCode))).toBeInTheDocument());
  });

  test('直接来源链接无需引用索引即可展示原文片段',()=>{
    const fetchMock=vi.fn();
    vi.stubGlobal('fetch',fetchMock);
    render(<MemoryRouter initialEntries={['/profile/resumes?resumeId=resume-1&sourceEvidenceId=src%3A1&sourceEvidenceStart=12&sourceEvidenceEnd=22&sourceEvidenceQuote=%E8%B4%9F%E8%B4%A3%E6%A8%A1%E5%9E%8B%E6%9C%8D%E5%8A%A1%E6%80%A7%E8%83%BD%E4%BC%98%E5%8C%96']}>
      <EvidenceDeepLinkFocus resourceId="resume-1"/>
    </MemoryRouter>);

    expect(screen.getByText('已定位到原文片段')).toBeInTheDocument();
    expect(screen.getByText('负责模型服务性能优化').tagName).toBe('MARK');
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
