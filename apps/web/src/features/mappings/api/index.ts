import {api,type MappingCandidate,type MappingEntityType,type MappingItem} from '../../../shared/api';

const enc=(value:string)=>encodeURIComponent(value);
export const listMappings=(entityType:MappingEntityType,query='',status='')=>api<MappingItem[]>(`/portal/admin/knowledge-graph/mappings?entity_type=${enc(entityType)}&query=${enc(query)}${status?`&status=${enc(status)}`:''}`);
export const searchMappingCandidates=(entityType:MappingEntityType,query:string)=>api<MappingCandidate[]>(`/portal/admin/knowledge-graph/mapping-candidates?entity_type=${enc(entityType)}&query=${enc(query)}`);
export const confirmMapping=(entityType:MappingEntityType,mainSystemId:string,knowledgeGraphId:string)=>api<MappingItem>(`/portal/admin/knowledge-graph/mappings/${enc(entityType)}/${enc(mainSystemId)}`,{method:'PUT',body:JSON.stringify({knowledge_graph_id:knowledgeGraphId})});
export const cancelMapping=(entityType:MappingEntityType,mainSystemId:string)=>api<MappingItem>(`/portal/admin/knowledge-graph/mappings/${enc(entityType)}/${enc(mainSystemId)}`,{method:'DELETE'});
export const retryMapping=(entityType:MappingEntityType,mainSystemId:string)=>api<MappingItem>(`/portal/admin/knowledge-graph/mappings/${enc(entityType)}/${enc(mainSystemId)}/retry`,{method:'POST'});
