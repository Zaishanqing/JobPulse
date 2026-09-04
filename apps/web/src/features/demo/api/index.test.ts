import {afterEach,expect,test,vi} from 'vitest';
import {listPortalDemoTasks} from '.';

const successResponse=()=>Promise.resolve(new Response(JSON.stringify({
  code:0,
  message:'success',
  data:[],
  trace_id:'trace-demo',
}),{status:200,headers:{'Content-Type':'application/json'}}));

afterEach(()=>{
  localStorage.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test('空 filters 不附加查询字符串',async()=>{
  const fetchMock=vi.fn(successResponse);
  vi.stubGlobal('fetch',fetchMock);

  await listPortalDemoTasks();
  await listPortalDemoTasks({});

  expect(fetchMock).toHaveBeenNthCalledWith(1,'/api/v1/portal/admin/demo-tasks',expect.anything());
  expect(fetchMock).toHaveBeenNthCalledWith(2,'/api/v1/portal/admin/demo-tasks',expect.anything());
});

test('三个 filters 同时存在时只发送冻结参数并编码值',async()=>{
  const fetchMock=vi.fn(successResponse);
  vi.stubGlobal('fetch',fetchMock);

  await listPortalDemoTasks({
    task_type:'matching',
    status:'succeeded',
    object_id:'position / 中文',
  });

  expect(fetchMock).toHaveBeenCalledWith(
    '/api/v1/portal/admin/demo-tasks?task_type=matching&status=succeeded&object_id=position+%2F+%E4%B8%AD%E6%96%87',
    expect.anything(),
  );
});
