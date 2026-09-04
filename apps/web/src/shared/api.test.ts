import {afterEach,describe,expect,test,vi} from 'vitest';
import {api,apiBlob,ApiError,AUTH_EXPIRED_EVENT,setAccessToken} from './api';

const response=(status:number,body:unknown)=>Promise.resolve(new Response(JSON.stringify(body),{
  status,
  headers:{'Content-Type':'application/json'},
}));

const requestError=async(status:number,body:Record<string,unknown>)=>{
  vi.stubGlobal('fetch',vi.fn(()=>response(status,body)));
  try{
    await api('/test');
    throw new Error('Expected api() to reject');
  }catch(error){
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
};

afterEach(()=>{
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('structured backend error normalization',()=>{
  test('normalizes a 400 HTTPException data envelope',async()=>{
    const error=await requestError(400,{
      code:400,message:'Invalid request',
      data:{error_code:'INVALID_REQUEST',message:'Choose another value',fields:{choice:'invalid'}},
      trace_id:'req-400',
    });

    expect(error).toMatchObject({
      status:400,message:'系统处理失败，请稍后重试。',errorCode:'INVALID_REQUEST',
      fields:{choice:'invalid'},traceId:'req-400',
      details:{error_code:'INVALID_REQUEST',message:'Choose another value',fields:{choice:'invalid'}},
    });
  });

  test.each([
    [403,'Forbidden','当前账号没有执行该操作的权限。'],
    [404,'Not found','没有找到请求的内容，请刷新后重试。'],
  ])('localizes an unstructured %s error',async(status,message,expected)=>{
    const error=await requestError(status,{code:status,message,data:null,trace_id:`req-${status}`});
    expect(error).toMatchObject({status,message:expected,traceId:`req-${status}`});
    expect(error.details).toBeUndefined();
  });

  test('exposes a 409 error_code from data',async()=>{
    const error=await requestError(409,{
      code:409,message:'Conflict',data:{error_code:'CV_REVIEW_CONFLICT',message:'Review payload is stale'},trace_id:'req-409',
    });
    expect(error).toMatchObject({status:409,message:'系统处理失败，请稍后重试。',errorCode:'CV_REVIEW_CONFLICT'});
  });

  test('exposes 422 validation fields from data',async()=>{
    const fields=[{field:'email',message:'Invalid email'}];
    const error=await requestError(422,{
      code:422,message:'Validation error',data:{message:'Check the form',fields},trace_id:'req-422',
    });
    expect(error).toMatchObject({status:422,message:'系统处理失败，请稍后重试。',fields});
  });

  test('explains when the formal discovery dataset has not been initialized',async()=>{
    const error=await requestError(422,{
      code:422,
      message:'Discovery dataset is unavailable or has no approved facts: d5-short-window-main-v1-37585b4079dd',
      data:{
        error_code:'DISCOVERY_DATASET_NOT_READY',
        message:'Discovery dataset is unavailable or has no approved facts: d5-short-window-main-v1-37585b4079dd',
      },
      trace_id:'req-discovery-dataset',
    });
    expect(error).toMatchObject({
      status:422,
      message:'冻结发现数据集不可用或校验失败，请联系管理员检查随版本发布的数据资产。',
      errorCode:'DISCOVERY_DATASET_NOT_READY',
      traceId:'req-discovery-dataset',
    });
  });

  test('prefers details and exposes upstream metadata for a 503',async()=>{
    const upstream={reason:'ConnectTimeout'};
    const error=await requestError(503,{
      code:503,message:'Service unavailable',
      data:{error_code:'LEGACY_CODE',message:'Legacy message'},
      details:{error_code:'DISCOVERY_UNAVAILABLE',message:'Discovery unavailable',upstream},
      trace_id:'req-503',
    });
    expect(error).toMatchObject({
      status:503,message:'系统处理失败，请稍后重试。',errorCode:'DISCOVERY_UNAVAILABLE',upstream,traceId:'req-503',
    });
    expect(error.details).toEqual({error_code:'DISCOVERY_UNAVAILABLE',message:'Discovery unavailable',upstream});
  });

  test('keeps the existing 401 identity-expiry behavior',async()=>{
    setAccessToken('expired-token');
    const listener=vi.fn();
    window.addEventListener(AUTH_EXPIRED_EVENT,listener,{once:true});

    const error=await requestError(401,{code:401,message:'Unauthorized',data:null,trace_id:'req-401'});

    expect(error.status).toBe(401);
    expect(localStorage.getItem('main_access_token')).toBeNull();
    expect(listener).toHaveBeenCalledOnce();
  });

  test('maps known KG conflict codes to Chinese messages',async()=>{
    const error=await requestError(409,{
      code:409,message:'draft is based on a stale graph version',
      data:null,
      details:{error_code:'STALE_GRAPH_DRAFT',upstream:{base_version_id:null,current_version_id:1}},
      trace_id:'req-stale',
    });

    expect(error).toMatchObject({
      status:409,
      message:'当前草稿基于已过期的图谱版本，请刷新后重新打开草稿再发布。',
      errorCode:'STALE_GRAPH_DRAFT',
    });
  });
});

test('returns ordinary successful data without interpreting it as error details',async()=>{
  const data={error_code:'BUSINESS_STATE',message:'This is domain data',fields:{stage:'complete'}};
  vi.stubGlobal('fetch',vi.fn(()=>response(200,{code:0,message:'success',data,trace_id:'req-ok'})));

  await expect(api('/test')).resolves.toEqual(data);
});

test('expires the session when a blob request returns 401',async()=>{
  setAccessToken('expired-blob-token');
  const listener=vi.fn();
  window.addEventListener(AUTH_EXPIRED_EVENT,listener,{once:true});
  vi.stubGlobal('fetch',vi.fn(()=>response(401,{message:'Unauthorized'})));

  await expect(apiBlob('/files/test/preview')).rejects.toMatchObject({status:401});

  expect(localStorage.getItem('main_access_token')).toBeNull();
  expect(listener).toHaveBeenCalledOnce();
});

test('does not treat arbitrary failed-request data as error details',async()=>{
  const error=await requestError(400,{
    code:400,message:'Request failed',data:{fields:{stage:'complete'},upstream:{status:'ready'}},trace_id:'req-data',
  });

  expect(error).toMatchObject({message:'请求失败，请稍后重试。',traceId:'req-data'});
  expect(error.details).toBeUndefined();
  expect(error.fields).toBeUndefined();
  expect(error.upstream).toBeUndefined();
});
