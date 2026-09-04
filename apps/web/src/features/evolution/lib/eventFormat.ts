import type {EvolutionEvent,EvolutionEntity,EvolutionGraphVersion,EvolutionVersionPair} from '../types';

// ── 事件类型中文映射（未知内部枚举不直接暴露给用户）──
export const eventTypeLabels: Record<string,string>={
  skill_emergence:'新技能出现',
  skill_decline:'技能衰退',
  skill_replacement:'技能替代',
  technology_stack_migration:'技术栈迁移',
  responsibility_shift:'岗位职责迁移',
  role_expansion:'岗位职责扩张',
  role_contraction:'岗位职责收缩',
  position_rename:'岗位名称变化',
};

export type EventGroup='emergence'|'replacement'|'decline'|'shift'|'other';

export const eventGroupLabels: Record<EventGroup,string>={
  emergence:'新增 / 扩张',
  replacement:'替代 / 迁移',
  decline:'衰退 / 收缩',
  shift:'职责 / 名称变化',
  other:'其他',
};

const GROUP_BY_TYPE: Record<string,EventGroup>={
  skill_emergence:'emergence',
  role_expansion:'emergence',
  skill_replacement:'replacement',
  technology_stack_migration:'replacement',
  skill_decline:'decline',
  role_contraction:'decline',
  responsibility_shift:'shift',
  position_rename:'shift',
};

export function eventGroup(eventType:string):EventGroup{
  return GROUP_BY_TYPE[eventType]||'other';
}

export function eventTypeLabel(eventType:string):string{
  return eventTypeLabels[eventType]||'其他能力变化';
}

export function entityName(entity:EvolutionEntity|undefined):string{
  if(entity===undefined||entity===null)return '未命名主体';
  if(typeof entity==='string')return entity||'未命名主体';
  const value=entity.canonical_name??entity.name??entity.skill_id;
  return typeof value==='string'&&value.trim()?value:String(value??'未命名主体');
}

/** 事件主体：例如 Python / RAG / TensorFlow → PyTorch / 数据分析职责。 */
export function eventSubject(event:EvolutionEvent):string{
  const source=event.source_entities.map(entityName);
  const target=event.target_entities.map(entityName);
  if(event.event_type==='position_rename')return `${source[0]??'旧名称'} → ${target[0]??'新名称'}`;
  if(event.event_type==='skill_replacement'||event.event_type==='technology_stack_migration'){
    return `${source.join('、')||'（无）'} → ${target.join('、')||'（无）'}`;
  }
  if(event.event_type==='responsibility_shift')return target.join('、')||source.join('、')||'岗位职责';
  if(event.event_type==='role_expansion'||event.event_type==='role_contraction')return '岗位能力广度';
  return target.length?target.join('、'):source.join('、')||'未命名主体';
}

const numberText=(value:unknown):string|undefined=>typeof value==='number'?value.toFixed(2):undefined;

/** 变化摘要：完全基于后端真实结构字段做确定性前端格式化。 */
export function eventChangeSummary(event:EvolutionEvent):string{
  const metrics=event.metrics||{};
  switch(event.event_type){
    case 'skill_emergence':{
      const after=numberText(metrics.after_weight);
      const before=numberText(metrics.before_weight);
      return before!==undefined&&after!==undefined?`权重 ${before} → ${after}，首次达到事件检测阈值`:'技能权重显著上升';
    }
    case 'skill_decline':{
      const after=numberText(metrics.after_weight);
      const before=numberText(metrics.before_weight);
      return before!==undefined&&after!==undefined?`权重 ${before} → ${after}，低于事件检测阈值`:'技能权重显著下降';
    }
    case 'skill_replacement':{
      const from=entityName(event.source_entities[0]);
      const to=entityName(event.target_entities[0]);
      return `${from} → ${to}`;
    }
    case 'technology_stack_migration':{
      const from=event.source_entities.map(entityName).join('、');
      const to=event.target_entities.map(entityName).join('、');
      return `${from} → ${to}`;
    }
    case 'responsibility_shift':{
      const removed=typeof metrics.removed_count==='number'?metrics.removed_count:undefined;
      const added=typeof metrics.added_count==='number'?metrics.added_count:undefined;
      return `职责集合变化：移除 ${removed??'—'} 项 / 新增 ${added??'—'} 项`;
    }
    case 'role_expansion':
    case 'role_contraction':{
      const breadth=numberText(metrics.breadth_score);
      const label=event.event_type==='role_expansion'?'能力广度扩张':'能力广度收缩';
      return breadth!==undefined?`${label}（能力广度值 ${breadth}）`:label;
    }
    case 'position_rename':
      return '岗位名称变化';
    default:
      return '能力结构发生变化';
  }
}

export const STRONG_MAGNITUDE_THRESHOLD=0.6;
export const CAUTION_CONFIDENCE_THRESHOLD=0.6;

export function isStrongEvent(event:EvolutionEvent):boolean{
  return typeof event.magnitude==='number'&&event.magnitude>=STRONG_MAGNITUDE_THRESHOLD;
}

/** 低 confidence 不等于低概率，提示“需谨慎解释”。 */
export function needsCaution(event:EvolutionEvent):boolean{
  return typeof event.confidence==='number'&&event.confidence<CAUTION_CONFIDENCE_THRESHOLD;
}

export function percent(value:number|undefined|null):string{
  if(typeof value!=='number'||!Number.isFinite(value))return '—';
  return `${Math.round(value*100)}%`;
}

export function dateLabel(value:string|undefined|null):string{
  if(!value)return '日期未返回';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '日期格式异常';
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
}

export function monthLabel(value:string|undefined|null):string{
  if(!value)return '日期未返回';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '日期格式异常';
  return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}`;
}

export function sortEvents(events:EvolutionEvent[]):EvolutionEvent[]{
  return [...events].sort((left,right)=>{
    const leftTime=Date.parse(left.created_at||'')||0;
    const rightTime=Date.parse(right.created_at||'')||0;
    if(leftTime!==rightTime)return leftTime-rightTime;
    if(left.to_version!==right.to_version)return left.to_version-right.to_version;
    if(left.from_version!==right.from_version)return left.from_version-right.from_version;
    return String(left.event_type).localeCompare(String(right.event_type));
  });
}

export function versionById(versions:EvolutionGraphVersion[],versionId:number):EvolutionGraphVersion|undefined{
  return versions.find(item=>item.id===versionId);
}

export function versionNumberById(versions:EvolutionGraphVersion[],versionId:number):string{
  const version=versionById(versions,versionId);
  return version?`V${version.version_number}`:'版本未找到';
}

export type EventOverviewStats={
  total:number;
  emergence:number;
  replacement:number;
  decline:number;
  shift:number;
  other:number;
  versionCount:number;
  pairCount:number;
};

export function overviewStats(events:EvolutionEvent[],versions:EvolutionGraphVersion[],pairs:EvolutionVersionPair[]):EventOverviewStats{
  const counts:Record<EventGroup,number>={emergence:0,replacement:0,decline:0,shift:0,other:0};
  events.forEach(event=>{counts[eventGroup(event.event_type)]+=1});
  const involvedVersions=new Set<number>();
  events.forEach(event=>{involvedVersions.add(event.from_version);involvedVersions.add(event.to_version)});
  const versionCount=involvedVersions.size||versions.length;
  return {
    total:events.length,
    emergence:counts.emergence,
    replacement:counts.replacement,
    decline:counts.decline,
    shift:counts.shift,
    other:counts.other,
    versionCount,
    pairCount:pairs.length||versions.length-1,
  };
}

export function filterEvents(events:EvolutionEvent[],groupFilter:string,pairFilter:string):EvolutionEvent[]{
  return events.filter(event=>{
    if(groupFilter!=='all'&&eventGroup(event.event_type)!==groupFilter)return false;
    if(pairFilter!=='all'){
      const [from,to]=pairFilter.split(':').map(Number);
      if(event.from_version!==from||event.to_version!==to)return false;
    }
    return true;
  });
}
