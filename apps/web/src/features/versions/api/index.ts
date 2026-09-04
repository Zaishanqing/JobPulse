import {api,type GraphDiff,type GraphVersion,type GraphVersionDetail} from '../../../shared/api';
const positionPath=(positionId:string)=>encodeURIComponent(positionId);
export const listVersions=(positionId:string)=>api<GraphVersion[]>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/versions`);
export const getVersion=(positionId:string,versionId:number)=>api<GraphVersionDetail>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/versions/${versionId}`);
export const rollbackVersion=(positionId:string,versionId:number,reason:string)=>api(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/versions/${versionId}/rollback`,{method:'POST',body:JSON.stringify({reason})});
export const diffVersions=(positionId:string,fromId:number,toId:number)=>api<GraphDiff>(`/portal/admin/knowledge-graph/positions/${positionPath(positionId)}/versions/diff?from_version_id=${fromId}&to_version_id=${toId}`);
