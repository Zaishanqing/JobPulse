import {api} from '../../../shared/api';
import type {BuildGraphInput,BuildGraphResult,BuildRun,CatalogPositionPage,PublishGate,PublishResult} from '../types';

const positionPath=(positionId:string)=>encodeURIComponent(positionId);
const positionPageCache=new Map<string,CatalogPositionPage>();
const buildRunsCache=new Map<string,BuildRun[]>();

export type AutoReviewResult={
  build_run_id:number;
  policy_version:string;
  auto_accepted_count:number;
  requires_human_count:number;
  auto_accepted_task_ids:number[];
  requires_human_task_ids:number[];
};

export type CatalogPositionQuery={
  search?:string;
  domain?:string;
  sort?:'name'|'domain'|'jd_count';
  order?:'asc'|'desc';
  page?:number;
  page_size?:number;
};

const positionQueryKey=(query:CatalogPositionQuery)=>JSON.stringify({
  search:query.search?.trim()||'',
  domain:query.domain||'',
  sort:query.sort||'name',
  order:query.order||'asc',
  page:query.page||1,
  page_size:query.page_size||10,
});

export function peekCachedCatalogPositions(query:CatalogPositionQuery):CatalogPositionPage|undefined{
  return positionPageCache.get(positionQueryKey(query));
}

/** Catalog Admin API: reserved for catalog/build/review management surfaces. */
export const listCatalogAdminPositions=(query:CatalogPositionQuery={})=>{
  const key=positionQueryKey(query);
  const cached=positionPageCache.get(key);
  if(cached)return Promise.resolve(cached);
  const params=new URLSearchParams();
  if(query.search)params.set('search',query.search);
  if(query.domain)params.set('domain',query.domain);
  if(query.sort)params.set('sort',query.sort);
  if(query.order)params.set('order',query.order);
  if(query.page!==undefined)params.set('page',String(query.page));
  if(query.page_size!==undefined)params.set('page_size',String(query.page_size));
  const suffix=params.toString()?`?${params.toString()}`:'';
  return api<CatalogPositionPage>(`/portal/admin/catalog/positions${suffix}`).then(data=>{
    positionPageCache.set(key,data);
    return data;
  });
};
export const buildPosition=(positionId:string,input:BuildGraphInput={})=>api<BuildGraphResult>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/build`,{method:'POST',body:JSON.stringify(input)});
export const listBuildRuns=(positionId:string)=>{
  const key=positionPath(positionId);
  const cached=buildRunsCache.get(key);
  if(cached)return Promise.resolve(cached);
  return api<BuildRun[]>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/build-runs`).then(data=>{
    buildRunsCache.set(key,data);
    return data;
  });
};
export function peekCachedBuildRuns(positionId:string):BuildRun[]|undefined{
  return buildRunsCache.get(positionPath(positionId));
}
export const invalidateBuildRuns=(positionId:string)=>{
  buildRunsCache.delete(positionPath(positionId));
};
export const getPublishGate=(runId:number)=>api<PublishGate>(`/portal/admin/knowledge-graph/build-runs/${runId}/publish-gate`);
export const publishBuild=(runId:number)=>api<PublishResult>(`/portal/admin/knowledge-graph/build-runs/${runId}/publish`,{method:'POST',body:JSON.stringify({reason:'frontend publish'})});
export const autoReviewBuild=(runId:number,policyVersion='review-policy.v2',reason='按新发布策略自动审核')=>api<AutoReviewResult>(`/portal/admin/knowledge-graph/build-runs/${runId}/auto-review`,{method:'POST',body:JSON.stringify({policy_version:policyVersion,reason})});
