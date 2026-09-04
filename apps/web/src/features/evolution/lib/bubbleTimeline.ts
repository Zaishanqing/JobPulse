import type {CapabilityEvolution} from '../types';

export type BubbleDirection='growth'|'decline'|'stable';

export type EvolutionBubbleDatum={
  id:string;
  name:string;
  stack:string;
  time:string;
  value:number;
  delta:number;
  level:string;
  direction:BubbleDirection;
  isNew:boolean;
  evidenceCount:number;
  previousEvidenceCount:number;
  supportThreshold:number;
  comparable:boolean;
};

export type EvolutionBubbleFrame={
  key:string;
  label:string;
  timestamp:number;
  skills:EvolutionBubbleDatum[];
  supportThreshold:number;
};

export type EvolutionBubbleTimeline={
  frames:EvolutionBubbleFrame[];
  stacks:string[];
  levels:string[];
  skillIds:string[];
};

const direction=(delta:number):BubbleDirection=>delta>1?'growth':delta<-1?'decline':'stable';
const CHANGE_EPSILON=.05;
const MAX_VISIBLE_SKILLS=30;

const evidenceCount=(skill:CapabilityEvolution['frames'][number]['snapshot']['skill_relations'][number]|undefined)=>
  skill?.statistics?.evidence_count||skill?.metrics?.support_document_count||0;

function dynamicSupportThreshold(current:number[],previous:number[]){
  const paired=current.map((value,index)=>Math.min(value,previous[index]||0)).filter(value=>value>0).sort((a,b)=>b-a);
  if(!paired.length)return 2;
  const scaleFloor=Math.max(2,Math.ceil(Math.max(...current)*.05));
  const countFloor=paired.length>MAX_VISIBLE_SKILLS?paired[MAX_VISIBLE_SKILLS-1]:0;
  return Math.max(scaleFloor,countFloor);
}

function frameTimestamp(frame:CapabilityEvolution['frames'][number],index:number){
  const window=frame.dependencies?.source_time_window as {end?:string|null;start?:string|null}|undefined;
  for(const value of [window?.end,frame.created_at,window?.start]){
    const parsed=value?Date.parse(value):Number.NaN;
    if(Number.isFinite(parsed))return parsed;
  }
  return index;
}

export function quarterLabel(value:string|null|undefined,fallback:string){
  if(!value)return fallback;
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return fallback;
  return `${date.getFullYear()} 第 ${Math.floor(date.getMonth()/3)+1} 季度`;
}

export function buildBubbleTimeline(evolution:CapabilityEvolution):EvolutionBubbleTimeline{
  const ordered=[...evolution.frames]
    .map((frame,index)=>({frame,timestamp:frameTimestamp(frame,index)}))
    .sort((left,right)=>left.timestamp-right.timestamp);
  const skillIdSet=new Set<string>();
  const frames=ordered.map(({frame,timestamp},index)=>{
    const previous=ordered[index-1]?.frame;
    const previousById=new Map((previous?.snapshot.skill_relations||[]).map(skill=>[skill.skill_id,skill]));
    const currentRelations=frame.snapshot.skill_relations||[];
    const currentEvidence=currentRelations.map(evidenceCount);
    const previousEvidence=currentRelations.map(skill=>evidenceCount(previousById.get(skill.skill_id)));
    const supportThreshold=dynamicSupportThreshold(currentEvidence,previousEvidence);
    const skills=currentRelations.map(skill=>{
      const before=previousById.get(skill.skill_id);
      const beforeWeight=before?.weight??0;
      const delta=before&&beforeWeight>0?((skill.weight-beforeWeight)/beforeWeight)*100:0;
      const currentSupport=evidenceCount(skill);
      const previousSupport=before?evidenceCount(before):0;
      const comparable=Boolean(
        before&&beforeWeight>0&&Math.abs(delta)>=CHANGE_EPSILON&&
        currentSupport>=supportThreshold&&previousSupport>=supportThreshold,
      );
      const stack=skill.category_name||skill.category_code||'未分类';
      const level=skill.importance_level||'未分类';
      skillIdSet.add(skill.skill_id);
      const window=frame.dependencies?.source_time_window as {end?:string|null;start?:string|null}|undefined;
      return {
        id:skill.skill_id,
        name:skill.canonical_name,
        stack,
        time:quarterLabel(window?.end||frame.created_at,`图谱版本 ${frame.version_number}`),
        value:Math.max(1,skill.weight*100),
        delta,
        level,
        direction:direction(delta),
        isNew:!before,
        evidenceCount:currentSupport,
        previousEvidenceCount:previousSupport,
        supportThreshold,
        comparable,
      } satisfies EvolutionBubbleDatum;
    });
    return {
      key:String(frame.id),
      label:quarterLabel((frame.dependencies?.source_time_window as {end?:string|null}|undefined)?.end||frame.created_at,`图谱版本 ${frame.version_number}`),
      timestamp,
      skills,
      supportThreshold,
    };
  });
  const candidates=new Map<string,{firstChangeIndex:number;support:number;delta:number;name:string}>();
  frames.forEach((frame,index)=>frame.skills.filter(skill=>skill.comparable).forEach(skill=>{
    const current=candidates.get(skill.id);
    const support=Math.min(skill.evidenceCount,skill.previousEvidenceCount);
    candidates.set(skill.id,{
      firstChangeIndex:current?.firstChangeIndex??index,
      support:Math.max(current?.support??0,support),
      delta:Math.max(current?.delta??0,Math.abs(skill.delta)),
      name:skill.name,
    });
  }));
  const selectedIds=new Set([...candidates.entries()].sort((left,right)=>
    right[1].support-left[1].support||right[1].delta-left[1].delta||left[1].name.localeCompare(right[1].name,'zh-CN'),
  ).slice(0,MAX_VISIBLE_SKILLS).map(([id])=>id));
  frames.forEach((frame,index)=>frame.skills.forEach(skill=>{
    const candidate=candidates.get(skill.id);
    skill.comparable=Boolean(candidate&&selectedIds.has(skill.id)&&index>=Math.max(0,candidate.firstChangeIndex-1));
  }));
  const eligibleIds=selectedIds;
  const eligibleSkills=frames.flatMap(frame=>frame.skills.filter(skill=>eligibleIds.has(skill.id)));
  return {
    frames,
    stacks:[...new Set(eligibleSkills.map(skill=>skill.stack))].sort((left,right)=>left.localeCompare(right,'zh-CN')),
    levels:[...new Set(eligibleSkills.map(skill=>skill.level))],
    skillIds:[...skillIdSet].filter(id=>eligibleIds.has(id)),
  };
}

export function datumAt(timeline:EvolutionBubbleTimeline,skillId:string,frameIndex:number){
  return timeline.frames[frameIndex]?.skills.find(skill=>skill.id===skillId);
}
