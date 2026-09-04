import {api,type CatalogSkill,type UnresolvedItem} from '../../../shared/api';
import type {NormalizationSuggestion} from '../types';
export const listUnresolved=()=>api<UnresolvedItem[]>('/review-tasks/unresolved-skills');
export const listCatalogSkills=()=>api<CatalogSkill[]>('/skills');
export const suggestNormalizations=(rawSkill:string,context:string,topK=5)=>api<NormalizationSuggestion[]>('/skills/normalization-suggestions',{method:'POST',body:JSON.stringify({raw_skill:rawSkill,context,top_k:topK})});
export const resolveUnresolved=(item:UnresolvedItem,skillId:string)=>api(`/jd-parse-results/${encodeURIComponent(item.parse_result_id)}/skill-catalog-mappings`,{method:'POST',body:JSON.stringify({source_name:item.source_name,target_skill_id:skillId,requirement_id:item.requirement_id})});
export const excludeUnresolved=(item:UnresolvedItem,reason:string)=>api(`/jd-parse-results/${encodeURIComponent(item.parse_result_id)}/skill-catalog-exclusions`,{method:'POST',body:JSON.stringify({source_name:item.source_name,requirement_id:item.requirement_id,reason})});
