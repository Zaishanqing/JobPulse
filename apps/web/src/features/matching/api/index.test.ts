import {beforeEach,expect,test,vi} from 'vitest';

vi.mock('../../../shared/api',()=>({
  api:vi.fn(),
  apiBlob:vi.fn(),
}));

import {api} from '../../../shared/api';
import {createEnterpriseJobMatchTask,createMatchTask,getMatchPreflight,personalRunIdempotencyKey,restartMatchTask} from './index';

beforeEach(()=>{
  vi.clearAllMocks();
});

test('same run identity reuses one Idempotency-Key for retries',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({task_id:'task-1',status:'succeeded'});
  const key=personalRunIdempotencyKey('resume-1','position-1','run-id-1');
  await createMatchTask('resume-1','position-1','run-id-1');
  await createMatchTask('resume-1','position-1','run-id-1');
  expect(call).toHaveBeenCalledTimes(2);
  expect(call.mock.calls[0][1].headers['Idempotency-Key']).toBe(key);
  expect(call.mock.calls[1][1].headers['Idempotency-Key']).toBe(key);
  expect(JSON.parse(call.mock.calls[0][1].body).generate_learning_path).toBe(true);
});

test('different run identities produce different idempotency keys',()=>{
  const first=personalRunIdempotencyKey('resume-1','position-1','run-id-1');
  const second=personalRunIdempotencyKey('resume-1','position-1','run-id-2');
  expect(first).not.toBe(second);
  expect(first).toMatch(/^personal-run:resume-1:position-1:/);
});

test('restart keeps a stable key for one user action and changes on the next',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({task_id:'task-2',status:'pending'});
  await restartMatchTask('task-1','restart-id-1');
  await restartMatchTask('task-1','restart-id-1');
  expect(call.mock.calls[0][1].headers['Idempotency-Key']).toBe('personal-restart:task-1:restart-id-1');
  expect(call.mock.calls[1][1].headers['Idempotency-Key']).toBe('personal-restart:task-1:restart-id-1');
});

test('enterprise job detail uses formal enterprise target and weights after preflight',async()=>{
  const call=api as ReturnType<typeof vi.fn>;
  call.mockResolvedValue({ready:true});
  await getMatchPreflight('resume-1','job-1','enterprise_job');
  await createEnterpriseJobMatchTask('resume-1','job-1','run-1');
  expect(call.mock.calls[0][0]).toContain('target_type=enterprise_job');
  expect(JSON.parse(call.mock.calls[1][1].body)).toEqual({
    resume_id:'resume-1',target_type:'enterprise_job',target_id:'job-1',use_enterprise_weights:true,generate_learning_path:true,
  });
});
