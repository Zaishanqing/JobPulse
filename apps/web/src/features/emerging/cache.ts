const CACHE_TTL_MS=5*60_000;

type CacheEntry<T>={value:T;fetchedAt:number};

const entries=new Map<string,CacheEntry<unknown>>();
const inFlight=new Map<string,{generation:number;promise:Promise<unknown>}>();
const generations=new Map<string,number>();

const namespacedKey=(userId:string,key:string)=>`${userId}:${key}`;

export const emergingCacheKeys={
  assets:'discovery-assets',
  published:'published',
  recentSignals:'recent-signals',
  formalExperiment:'discovery-formal-experiment',
  formalClusters:'discovery-formal-experiment-clusters',
  runs:'discovery-runs',
  clusters:'clusters',
  candidates:'discovery-candidates',
  governance:'governance-candidates',
  clusterJds:(clusterId:string)=>`cluster-jds:${clusterId}`,
  trajectory:(candidateId:string)=>`trajectory:${candidateId}`,
};

export function readEmergingCache<T>(userId:string,key:string):T|undefined{
  return entries.get(namespacedKey(userId,key))?.value as T|undefined;
}

export async function loadEmergingCache<T>(
  userId:string,
  key:string,
  loader:()=>Promise<T>,
  force=false,
):Promise<T>{
  const fullKey=namespacedKey(userId,key);
  if(force)generations.set(fullKey,(generations.get(fullKey)??0)+1);
  const generation=generations.get(fullKey)??0;
  const cached=entries.get(fullKey) as CacheEntry<T>|undefined;
  if(!force&&cached&&Date.now()-cached.fetchedAt<CACHE_TTL_MS)return cached.value;
  const pending=inFlight.get(fullKey);
  if(pending?.generation===generation)return pending.promise as Promise<T>;
  const request=loader().then(value=>{
    if((generations.get(fullKey)??0)===generation){
      entries.set(fullKey,{value,fetchedAt:Date.now()});
    }
    return value;
  }).finally(()=>{
    if(inFlight.get(fullKey)?.promise===request)inFlight.delete(fullKey);
  });
  inFlight.set(fullKey,{generation,promise:request});
  return request;
}

export function invalidateEmergingCache(userId:string,keys?:string[]){
  const namespace=`${userId}:`;
  const existingKeys=new Set([...entries.keys(),...inFlight.keys()]);
  for(const fullKey of existingKeys){
    if(!fullKey.startsWith(namespace))continue;
    const key=fullKey.slice(namespace.length);
    if(!keys||keys.some(candidate=>key===candidate||key.startsWith(`${candidate}:`))){
      entries.delete(fullKey);
      generations.set(fullKey,(generations.get(fullKey)??0)+1);
    }
  }
}

export function resetEmergingCacheForTests(){
  entries.clear();
  inFlight.clear();
  generations.clear();
}
