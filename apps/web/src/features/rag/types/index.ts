export const EVIDENCE_RAG_QUERY_VERSION='evidence-rag-query.v1' as const;
export const EVIDENCE_RAG_RESPONSE_VERSION='evidence-rag-response.v1' as const;

export type EvidenceTypeScope=
  |'jd_evidence'
  |'cv_evidence'
  |'kg_skill_relation_evidence'
  |'trend_evidence'
  |'discovery_evidence'
  |'matching_evidence'
  |'gap_evidence'
  |'learning_path_evidence'
  |'review_decision_evidence'
  |'all';

export type RAGStatus='answered'|'insufficient_evidence'|'failed';
export type RAGVersionScope='single_object'|'multi_object';
export type EvidenceAlignment='exact'|'normalized_exact'|'unresolved';
export type EvidenceEntailmentRelation='support'|'contradict'|'insufficient';

export type BusinessObjectType=
  |'source_jd'
  |'source_cv'
  |'standard_position'
  |'enterprise_job'
  |'cv_profile'
  |'matching_evaluation'
  |'trend_report'
  |'discovery_cluster'
  |'graph_version'
  |'resume';

export interface BusinessObjectRef{
  object_type:BusinessObjectType;
  object_id:string;
  object_version?:string|null;
}

export interface EvidenceConversationTurn{
  role:'user'|'assistant';
  text:string;
}

export interface PermissionContextV1{
  user_id:string;
  tenant_ref:string;
  permission_scope:string;
  assembled_by:'main-system-bff';
}

export interface EvidenceRAGQueryV1{
  contract_version:'evidence-rag-query.v1';
  business_object:BusinessObjectRef;
  business_object_label?:string|null;
  business_objects?:BusinessObjectRef[];
  conversation_history?:EvidenceConversationTurn[];
  query_text:string;
  evidence_types:EvidenceTypeScope[];
  version_scope:RAGVersionScope;
  graph_version_id?:number|null;
  graph_version?:string|null;
  business_version?:string|null;
}

export interface RAGEvidenceReferenceV1{
  evidence_id:string;
  business_object_id?:string|null;
  source_object_type:string;
  source_object_id:string;
  source_document_id:string;
  quote?:string|null;
  location_start?:number|null;
  location_end?:number|null;
  occurrence_index?:number|null;
  alignment:EvidenceAlignment;
  graph_version_id?:number|null;
  graph_version?:string|null;
  business_version?:string|null;
  source_version:string;
  retrieval_score?:number|null;
  tenant_ref:string;
  permission_scope:string;
}

export interface EvidenceCitationResolveRequest{
  evidence_id:string;
  source_version:string;
  graph_version_id?:number|null;
  graph_version?:string|null;
  business_version?:string|null;
}

export interface EvidenceCitationResolution{
  contract_version:'evidence-citation-resolution.v1';
  target_route:string;
  resource_id:string;
  version_id:string|number;
  evidence_id:string;
  start:number|null;
  end:number|null;
  highlight_text:string;
  source_object_type:string;
  source_object_id:string;
  source_document_id:string;
  source_version:string;
  graph_version_id?:number|null;
  graph_version?:string|null;
  business_version?:string|null;
}

export interface RAGErrorV1{
  code:string;
  message:string;
}

export interface EvidenceEntailmentV1{
  claim:string;
  relation:EvidenceEntailmentRelation;
  used_evidence_ids:string[];
  reason:string;
  dimension?:string|null;
}

export interface EvidenceRAGCoverageV1{
  selected_object_count:number;
  objects_with_candidates:number;
  objects_with_visible_evidence:number;
  evidence_count_by_object:Record<string,number>;
}

export type RagIndexStatusKind='running'|'completed'|'unknown'|'disabled';

export interface RagIndexStatus{
  status:RagIndexStatusKind;
  indexed_count:number|null;
  expected_count:number|null;
}

export interface EvidenceRAGResponseV1{
  contract_version:'evidence-rag-response.v1';
  status:RAGStatus;
  answer?:string|null;
  references:RAGEvidenceReferenceV1[];
  entailment?:EvidenceEntailmentV1[];
  provider:string;
  model:string;
  model_version:string;
  trace_id:string;
  error?:RAGErrorV1|null;
  explanation_only:true;
  version_scope:RAGVersionScope;
  coverage?:EvidenceRAGCoverageV1|null;
  graph_version_id?:number|null;
  graph_version?:string|null;
  business_version?:string|null;
  permission:PermissionContextV1;
}
