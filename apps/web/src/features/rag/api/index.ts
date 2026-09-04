import {api} from '../../../shared/api';
import type {EvidenceCitationResolveRequest,EvidenceCitationResolution,EvidenceRAGQueryV1,EvidenceRAGResponseV1,RagIndexStatus} from '../types';

export type {
  BusinessObjectRef,
  EvidenceAlignment,
  EvidenceCitationResolveRequest,
  EvidenceCitationResolution,
  EvidenceRAGCoverageV1,
  EvidenceRAGQueryV1,
  EvidenceRAGResponseV1,
  EvidenceTypeScope,
  PermissionContextV1,
  RAGErrorV1,
  RAGEvidenceReferenceV1,
  RAGStatus,
  RAGVersionScope,
  RagIndexStatus,
} from '../types';

export const queryEvidenceRAG=(query:EvidenceRAGQueryV1,signal?:AbortSignal)=>api<EvidenceRAGResponseV1>('/rag/evidence',{
  method:'POST',
  body:JSON.stringify(query),
  ...(signal?{signal}:{}),
});

export const resolveEvidenceCitation=(request:EvidenceCitationResolveRequest)=>api<EvidenceCitationResolution>('/rag/evidence/citations/resolve',{
  method:'POST',
  body:JSON.stringify(request),
});

export const getRagIndexStatus=(params:{business_object_type:string;business_object_id:string;graph_version_id?:number|null;graph_version?:string|null;business_version?:string|null})=>{
  const query=new URLSearchParams();
  query.set('business_object_type',params.business_object_type);
  query.set('business_object_id',params.business_object_id);
  if(params.graph_version_id!=null)query.set('graph_version_id',String(params.graph_version_id));
  if(params.graph_version)query.set('graph_version',params.graph_version);
  if(params.business_version)query.set('business_version',params.business_version);
  return api<RagIndexStatus>(`/rag/evidence/index-status?${query.toString()}`);
};
