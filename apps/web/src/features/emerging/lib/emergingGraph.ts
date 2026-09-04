import type {GraphRelation} from '../../../graphTransform';
import type {EmergingPosition} from '../api';
import {isTechnicalIdentifier} from './discoveryDisplay';

type Json=Record<string,unknown>;
export type EmergingGraphEvidence={sourceId:string;quote:string;source:string;window:string;locator:Json};
export type EmergingGraphSkill=GraphRelation&{
  requirement:'required'|'bonus';
  evidence:EmergingGraphEvidence[];
  supportJdCount:number|null;
};
export type EmergingGraphClaim={id:string;text:string;evidence:EmergingGraphEvidence[]};

const record=(value:unknown):Json=>value&&typeof value==='object'&&!Array.isArray(value)?value as Json:{};
const list=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const text=(...values:unknown[])=>values.find((value):value is string=>typeof value==='string'&&Boolean(value.trim()))?.trim()||'';
const label=(value:unknown)=>typeof value==='string'?value.trim():text(record(value).raw_skill,record(value).skill_name,record(value).canonical_name,record(value).content,record(value).name);
const key=(value:string)=>value.normalize('NFKC').toLocaleLowerCase().trim();

function evidence(values:unknown[]):EmergingGraphEvidence[]{
  const result=new Map<string,EmergingGraphEvidence>();
  for(const value of values){
    const row=record(value);
    const quote=text(row.original_text_snippet,row.quote);
    if(!quote)continue;
    const sourceId=text(row.source_jd_id,row.document_id,row.source_id);
    const source=text(row.data_source,row.source_name);
    const window=text(row.window_id);
    const locator=record(row.locator);
    result.set(JSON.stringify([sourceId,source,window,quote,locator]),{sourceId,quote,source,window,locator});
  }
  return [...result.values()];
}

function mergeEvidence(...groups:EmergingGraphEvidence[][]){
  return [...new Map(groups.flat().map(item=>[JSON.stringify(item),item])).values()];
}

function fieldRows(fields:Json,name:string,values:unknown[],allowFallback=true){
  const field=record(fields[name]);
  const items=list(field.items);
  const rows=values.length||!allowFallback?values:items.length?items:list(field.content);
  return rows.map(value=>{
    const row=record(value);
    const name=label(value);
    const matched=items.filter(item=>key(label(item))===key(name));
    return {name,row,evidence:evidence([...list(row.evidence),...matched.flatMap(item=>list(record(item).evidence))])};
  }).filter(item=>item.name&&!isTechnicalIdentifier(item.name));
}

/** A read projection of the emerging definition; never a published KG snapshot. */
export function buildEmergingGraph(item:EmergingPosition){
  const allowFallback=record(item).source_kind!=='discovery_asset';
  const definition=record(record(item.published_snapshot).definition);
  const fields={...record(definition.field_evidence),...record(item.field_evidence)};
  const skills=new Map<string,EmergingGraphSkill>();
  for(const requirement of ['required','bonus'] as const){
    const fieldName=`${requirement}_skills` as const;
    const values=item[fieldName]??list(definition[fieldName]);
    for(const entry of fieldRows(fields,fieldName,values,allowFallback)){
      const identity=key(entry.name);
      const previous=skills.get(identity);
      const combinedEvidence=mergeEvidence(previous?.evidence??[],entry.evidence);
      const citedJds=new Set(combinedEvidence.map(value=>value.sourceId).filter(Boolean)).size;
      const reported=entry.row.support_jd_count;
      const validReported=typeof reported==='number'&&Number.isFinite(reported)&&reported>=0?reported:null;
      const counts=[previous?.supportJdCount,validReported,citedJds||null].filter((value):value is number=>value!=null);
      const effectiveRequirement=previous?.requirement??requirement;
      skills.set(identity,{
        skill_id:previous?.skill_id??`emerging-skill:${encodeURIComponent(item.emerging_id)}:${encodeURIComponent(identity)}`,
        canonical_name:previous?.canonical_name??entry.name,
        requirement:effectiveRequirement,
        importance_level:effectiveRequirement==='required'?'core':'supplementary',
        // Group by the definition's own requirement labels, without inventing taxonomy bindings.
        classifications:[{facet:'concept_class',code:`emerging_${effectiveRequirement}`,name_zh:effectiveRequirement==='required'?'必备技能':'加分技能',is_primary:true}],
        evidence:combinedEvidence,
        supportJdCount:counts.length?Math.max(...counts):null,
      });
    }
  }
  const claims=(fieldName:string,values:unknown[]):EmergingGraphClaim[]=>{
    const rows=new Map<string,EmergingGraphClaim>();
    for(const entry of fieldRows(fields,fieldName,values,allowFallback)){
      const id=`${fieldName}:${key(entry.name)}`;
      const previous=rows.get(id);
      rows.set(id,{id,text:entry.name,evidence:mergeEvidence(previous?.evidence??[],entry.evidence)});
    }
    return [...rows.values()];
  };
  return {
    positionId:`emerging:${item.emerging_id}`,
    name:isTechnicalIdentifier(item.position_name)?'新兴岗位':item.position_name,
    skills:[...skills.values()],
    responsibilities:claims('core_responsibilities',item.core_responsibilities??list(definition.core_responsibilities)),
    scenarios:claims('industry_scenarios',item.industry_scenarios??list(definition.industry_scenarios)),
  };
}
