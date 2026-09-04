import {api,type GraphSnapshot,type PositionRequirementInflation,type RelationExplanation} from '../../../shared/api';
import {getPublishedPositionGraph} from '../../positions/api';
const positionPath=(positionId:string)=>encodeURIComponent(positionId);
export {getPublishedPositionGraph};
export const getRequirementInflation=(positionId:string)=>api<PositionRequirementInflation>(`/portal/positions/${positionPath(positionId)}/requirement-inflation`);
export const openGraphDraft=(positionId:string,baseVersionId?:number)=>api<{draft_id:number;build_run_id:number;position_id:string;base_version_id:number}>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/graph/drafts`,{method:'POST',body:JSON.stringify({base_version_id:baseVersionId})});
export const getDraftGraph=(buildRunId:number)=>api<GraphSnapshot>(`/portal/admin/knowledge-graph/drafts/${buildRunId}/graph`);
export const modifyRelation=(relationId:number,values:object)=>api(`/portal/admin/knowledge-graph/relations/${relationId}/modify`,{method:'POST',body:JSON.stringify(values)});
export const getRelationExplanation=(relationId:number,versionId?:number)=>api<RelationExplanation>(`/portal/admin/knowledge-graph/relations/${relationId}/explanation${versionId?`?version_id=${versionId}`:''}`);
