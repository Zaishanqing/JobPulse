import {afterEach,expect,test,vi} from 'vitest';
import {emergingCacheKeys,invalidateEmergingCache,loadEmergingCache,readEmergingCache,resetEmergingCacheForTests} from './cache';

afterEach(()=>{
  resetEmergingCacheForTests();
  vi.useRealTimers();
});

test('同一账号复用缓存并去重并发请求',async()=>{
  const loader=vi.fn().mockResolvedValue(['candidate']);
  const first=loadEmergingCache('user-1',emergingCacheKeys.candidates,loader);
  const second=loadEmergingCache('user-1',emergingCacheKeys.candidates,loader);
  expect(await first).toEqual(['candidate']);
  expect(await second).toEqual(['candidate']);
  expect(await loadEmergingCache('user-1',emergingCacheKeys.candidates,loader)).toEqual(['candidate']);
  expect(loader).toHaveBeenCalledTimes(1);
});

test('缓存按 user_id 隔离',async()=>{
  await loadEmergingCache('user-1',emergingCacheKeys.published,async()=>['user-1-data']);
  expect(readEmergingCache('user-1',emergingCacheKeys.published)).toEqual(['user-1-data']);
  expect(readEmergingCache('user-2',emergingCacheKeys.published)).toBeUndefined();
});

test('失效后的旧请求结果不会重新写回缓存',async()=>{
  let resolveRequest:(value:string[])=>void=()=>undefined;
  const request=loadEmergingCache('user-1',emergingCacheKeys.governance,()=>new Promise(resolve=>{
    resolveRequest=resolve;
  }));
  invalidateEmergingCache('user-1',[emergingCacheKeys.governance]);
  resolveRequest(['stale']);
  await request;
  expect(readEmergingCache('user-1',emergingCacheKeys.governance)).toBeUndefined();
});
