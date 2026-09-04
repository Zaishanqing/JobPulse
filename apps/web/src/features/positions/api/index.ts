import {api,type GraphSnapshot,type Position} from '../../../shared/api';

const positionPath=(positionId:string)=>encodeURIComponent(positionId);

/** Published Position API: user-facing pages only consume released catalog data. */
export const listPublishedPositions=()=>api<Position[]>('/portal/positions');
export const getPublishedPositionGraph=(positionId:string)=>
  api<GraphSnapshot>(`/portal/positions/${positionPath(positionId)}/graph`);
