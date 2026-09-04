import {afterEach,describe,expect,test,vi} from 'vitest';
import type {EvidenceRAGQueryV1,EvidenceRAGResponseV1} from '../types';
import {queryEvidenceRAG,resolveEvidenceCitation} from './index';

const query:EvidenceRAGQueryV1={
  contract_version:'evidence-rag-query.v1',
  business_object:{object_type:'matching_evaluation',object_id:'evaluation-1',object_version:null},
  query_text:'候选人是否满足要求？',
  evidence_types:['matching_evidence'],
  version_scope:'single_object',
  graph_version_id:null,
  graph_version:null,
  business_version:'snapshot-1',
};

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
    graph_version:null,
    business_version:'snapshot-1',
    source_version:'snapshot-1',
    retrieval_score:0.91,
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
  graph_version:null,
  business_version:'snapshot-1',
  permission:{user_id:'user-1',tenant_ref:'tenant-a',permission_scope:'personal:user-1',assembled_by:'main-system-bff'},
};

const response=(data:unknown,status=200)=>({
  ok:status<400,
  status,
  statusText:status<400?'OK':'error',
  json:async()=>({code:status<400?0:1,message:status<400?'success':'failed',data,details:{},trace_id:'trace'}),
});

afterEach(()=>{
  vi.unstubAllGlobals();
});

describe('queryEvidenceRAG',()=>{
  test('只向冻结 BFF 发送不含 permission 的请求',async()=>{
    const fetchMock=vi.fn(async(url:string,init?:RequestInit)=>{
      void url;void init;
      return response(answered);
    });
    vi.stubGlobal('fetch',fetchMock);

    const result=await queryEvidenceRAG(query);

    expect(result.status).toBe('answered');
    const [url,init]=fetchMock.mock.calls[0] as [string,RequestInit];
    expect(url).toBe('/api/v1/rag/evidence');
    expect(init.method).toBe('POST');
    const body=JSON.parse(String(init.body));
    expect(body).not.toHaveProperty('permission');
    expect(body).toMatchObject({
      contract_version:'evidence-rag-query.v1',
      business_object:{object_type:'matching_evaluation',object_id:'evaluation-1'},
      query_text:'候选人是否满足要求？',
      evidence_types:['matching_evidence'],
      business_version:'snapshot-1',
    });
  });

  test('HTTP 失败抛出 ApiError 且不返回伪数据',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>response(null,503)));

    await expect(queryEvidenceRAG(query)).rejects.toMatchObject({status:503});
  });
});

describe('resolveEvidenceCitation',()=>{
  test('只发送稳定 Evidence 与版本身份',async()=>{
    const resolved={contract_version:'evidence-citation-resolution.v1',target_route:'/positions/position-1',resource_id:'position-1',version_id:7,evidence_id:'evidence-1',start:0,end:8,highlight_text:'原文证据',source_object_type:'position_profile',source_object_id:'position-1',source_document_id:'document-1',source_version:'v1'};
    const fetchMock=vi.fn(async(url:string,init?:RequestInit)=>{
      void url;void init;
      return response(resolved);
    });
    vi.stubGlobal('fetch',fetchMock);

    const result=await resolveEvidenceCitation({evidence_id:'evidence-1',source_version:'v1',graph_version_id:7});

    expect(result.target_route).toBe('/positions/position-1');
    const [url,init]=fetchMock.mock.calls[0] as [string,RequestInit];
    expect(url).toBe('/api/v1/rag/evidence/citations/resolve');
    expect(JSON.parse(String(init.body))).toEqual({evidence_id:'evidence-1',source_version:'v1',graph_version_id:7});
  });
});

describe('getRagIndexStatus',()=>{
  test('发送 index readiness 查询并返回公共状态',async()=>{
    const fetchMock=vi.fn(async()=>response({status:'completed',indexed_count:10,expected_count:10}));
    vi.stubGlobal('fetch',fetchMock);

    const result=await import('./index').then(m=>m.getRagIndexStatus({business_object_type:'matching_evaluation',business_object_id:'evaluation-1',graph_version_id:7}));

    expect(result.status).toBe('completed');
    const [url]=fetchMock.mock.calls[0] as unknown as [string,RequestInit];
    expect(url).toBe('/api/v1/rag/evidence/index-status?business_object_type=matching_evaluation&business_object_id=evaluation-1&graph_version_id=7');
  });
});
