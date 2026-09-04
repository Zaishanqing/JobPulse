import {api,type AggregateEvidenceSupport,type EvidenceSupport} from '../../../shared/api';
export type AggregateEvidenceKind='requirements'|'tasks'|'company_facts'|'employment_facts';
export const relationEvidence=(relationId:number)=>api<EvidenceSupport[]>(`/portal/evidence/relations/${relationId}`);
export const aggregateEvidence=(kind:AggregateEvidenceKind,aggregateId:number)=>api<AggregateEvidenceSupport[]>(`/portal/evidence/${kind}/${aggregateId}`);
