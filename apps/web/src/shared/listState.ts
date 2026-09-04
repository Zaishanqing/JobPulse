export type PositionListFilter={
  search:string;
  domain?:string;
  sort:'name'|'domain'|'jd_count';
  order:'asc'|'desc';
  page:number;
};

const PREFIX='jobgraph:list-state:';

export function loadPositionListFilter(key:string):PositionListFilter|undefined{
  if(typeof window==='undefined')return undefined;
  try{
    const raw=window.localStorage.getItem(PREFIX+key);
    if(!raw)return undefined;
    const value=JSON.parse(raw) as PositionListFilter;
    if(typeof value.search!=='string'||typeof value.page!=='number')return undefined;
    return value;
  }catch{
    return undefined;
  }
}

export function savePositionListFilter(key:string,value:PositionListFilter):void{
  if(typeof window==='undefined')return;
  try{
    window.localStorage.setItem(PREFIX+key,JSON.stringify(value));
  }catch{
    // Storage may be unavailable in private mode; filters simply reset next time.
  }
}
