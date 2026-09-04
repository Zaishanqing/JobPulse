import {api} from '../../../shared/api';

export type StandardPositionProjection={standard_position_id:string;position_name:string;status:string;graph_onboarding_status:string;created_at?:string|null};
export type EmergingPosition={emerging_id:string;cluster_id:string;position_name:string;core_responsibilities:string[];required_skills:Array<Record<string,unknown>>;bonus_skills:Array<Record<string,unknown>>;industry_scenarios:string[];germination_score:number|null;score_dimensions:Record<string,unknown>;evidence_jd_ids:string[];field_evidence?:Record<string,unknown>;review_history?:Array<Record<string,unknown>>;published_snapshot?:Record<string,unknown>;status:string;germination_assessment?:Record<string,unknown>;standard_position?:StandardPositionProjection|null;created_at?:string|null;updated_at?:string|null;concept_type?:string;concept_note?:string};
export type DefinitionVersion={version_id:string;emerging_id:string;snapshot:Record<string,unknown>;selected:boolean;created_by:string;created_at:string|null;implementation_status:string};
export type ClusterJD={jd_id:string;title:string;raw_text:string;source_name:string|null;enterprise_id:string|null;publish_date:string|null};
export type PositionCluster={cluster_id:string;cluster_name:string;sample_count:number;core_skills:Array<Record<string,unknown>>;representative_titles:string[];representative_jd_ids:string[];stability_score:number;growth_score:number;distance_from_existing_positions:number;discovery_run_id:string;evolution_relations:Array<Record<string,unknown>>;time_window_start?:string|null;time_window_end?:string|null;created_at?:string|null;updated_at?:string|null;emergence_assessment?:Record<string,unknown>;germination_assessment?:Record<string,unknown>;generated_definition?:Record<string,unknown>;input_quality_report?:Record<string,unknown>;run_context?:Record<string,unknown>;standard_position_comparison?:Record<string,unknown>;explainability?:Record<string,unknown>;lineage_relations?:Array<Record<string,unknown>>;request_id?:string|null;input_fingerprint?:string|null};
export type DiscoveryRun={run_id:string;request_id?:string|null;input_fingerprint?:string|null;status:string;algorithm_version:string;time_window_start:string|null;time_window_end:string|null;cluster_count:number;sample_count:number;input_quality_report?:Record<string,unknown>;run_context?:Record<string,unknown>};
export type DiscoveryIdentityProfile={titles:string[];skills:string[];responsibilities:string[];member_jd_ids:string[];observed_window_ids:string[];semantic_centroid?:number[]};
export type DiscoveryCandidate={candidate_id:string;status:string;first_seen_window_id:string;last_seen_window_id:string;age:number;current_cluster_id:string|null;previous_cluster_ids:string[];canonical_title:string;display_title:string;definition:Record<string,unknown>;identity_profile:DiscoveryIdentityProfile;evidence:Record<string,unknown>;support_count:number;company_coverage:number;skill_similarity:number|null;responsibility_similarity:number|null;title_similarity:number|null;membership_overlap:number|null;identity_similarity:number;novelty_score:number;emergence_score:number;identity_stability:number;created_at:string|null;updated_at:string|null};
export type DiscoveryCandidateObservation={observation_id:string;candidate_id:string;run_id:string;cluster_id:string;cluster_name:string|null;window_id:string;title:string;status:string;emergence_score:number;support_count:number;company_count:number;identity_similarity:number;skill_similarity:number|null;responsibility_similarity:number|null;title_similarity:number|null;membership_overlap:number|null;semantic_similarity:number|null;evidence:Record<string,unknown>;match_evidence:Record<string,unknown>;created_at:string|null};
export type DiscoveryCandidateDetail={candidate:DiscoveryCandidate;latest_observation:DiscoveryCandidateObservation|null};
export type CandidateTrajectory={candidate_id:string;trajectory:DiscoveryCandidateObservation[]};
export type DiscoveryCandidateList={candidates:DiscoveryCandidate[];filters:{status:string|null;candidate_id:string|null;window_id:string|null}};
export type RecentPositionSignal={signal_id:string;position_name:string;representative_title:string;skills:string[];observed_at:string|null;source_jd_ids:string[];source_count:number;projection_version:string};
export type RecentPositionSignalFeed={signals:RecentPositionSignal[];observed_from:string|null;observed_to:string|null;source_contract:string;projection_version:string};
export type DiscoveryTask={task_id:string;status:string;result_payload?:{discovery_run_id?:string;discovery_run_ids?:string[]}};
export type FormalDiscoveryExperiment={
  experiment_id:string;
  algorithm_version:string;
  status:string;
  stage2_unit:string;
  evidence_level:string;
  window_semantics:string;
  source_results_sha256:string;
  cluster_counts:Record<string,number>;
  stage2_distribution_over_eligible:Record<string,number>;
  coverage:Record<string,number>;
  stage1_regression:{matched:number;total:number;distribution:Record<string,number>};
  acceptance_gates:{passed:number;total:number};
  emerging_clusters:Array<{cluster_key:string;canonical_title:string;stage1_relation:string;postings:number;enterprises:number;sources:number}>;
  validation_boundary:string;
};
export type FormalDiscoveryReplay={
  execution_id:string;
  experiment_id:string;
  algorithm_version:string;
  status:'passed'|'failed';
  started_at:string;
  completed_at:string;
  duration_ms:number;
  replay_scope:string;
  bundle_sha256:string;
  source_results_sha256:string;
  source_diagnostics_sha256:string;
  cluster_counts:Record<string,number>;
  stage2_distribution_over_eligible:Record<string,number>;
  ablations:Record<string,{distribution:Record<string,number>;emerging_count:number}>;
  emerging_clusters:FormalDiscoveryExperiment['emerging_clusters'];
  checks:Array<{key:string;label:string;mode:'executed'|'frozen_asset_validation';passed:boolean;evidence:string}>;
  passed_checks:number;
  total_checks:number;
  mismatches:Array<{cluster_key:string;mode:string;expected:string;actual:string}>;
  validation_boundary:string;
};
export type FormalExperimentCluster={
  cluster_key:string;
  canonical_title:string;
  stage1_relation:string;
  representative:boolean;
  eligible:boolean;
  state:string;
  ablation_states:Record<string,string>;
  counts:{
    observations:number;
    distinct_dates:number;
    independent_postings:number;
    enterprises:number;
    sources:number;
    content_hash_count:number;
  };
  growth:{
    available:boolean;
    growth_delta:number;
    per_window:Array<{date:string;distinct_postings:number}>;
  };
  structural_changed:boolean;
  evidence_refs:string[];
  display_refs?:string[];
  definition?:{
    position_name:string;
    position_summary:string;
    core_responsibilities:string[];
    required_skills:Array<Record<string,unknown>>;
    bonus_skills:Array<Record<string,unknown>>;
    industry_scenarios:string[];
    distinguishing_features:string[];
    representative_enterprises:string[];
    growth_trajectory:string[];
    field_evidence:Record<string,unknown>;
  };
};
export type FormalExperimentImportResult={
  experiment_id:string;
  imported:number;
  existing:number;
  cluster_keys:string[];
};

export type EmergingAsset=EmergingPosition&{source_kind:'discovery_asset';asset_definition:Record<string,unknown>;support_jd_count:number;source_count:number;enterprise_count:number;experiment_id:string};
export const emergingAssetId=(clusterKey:string)=>`formal:${clusterKey}`;
export const listEmergingAssets=()=>api<EmergingAsset[]>('/portal/emerging-assets');
export const getEmergingDisplay=(id:string)=>id.startsWith('formal:')
  ?api<EmergingAsset>(`/portal/emerging-assets/${encodeURIComponent(id)}`)
  :getPublishedEmerging(id);
export const updateEmergingDisplay=(id:string,values:object)=>id.startsWith('formal:')
  ?api<EmergingAsset>(`/portal/emerging-assets/${encodeURIComponent(id)}`,{method:'PUT',body:JSON.stringify(values)})
  :updateCandidate(id,values);
export const listPublishedEmerging=()=>api<EmergingPosition[]>('/portal/emerging-positions');
export const getPublishedEmerging=(id:string)=>api<EmergingPosition>(`/portal/emerging-positions/${id}`);
export const listRecentPositionSignals=()=>api<RecentPositionSignalFeed>('/portal/emerging-position-signals').then(value=>({
  signals:Array.isArray(value?.signals)?value.signals:[],
  observed_from:value?.observed_from||null,
  observed_to:value?.observed_to||null,
  source_contract:value?.source_contract||'published-jd-fact.v2',
  projection_version:value?.projection_version||'recent-position-signals.v1',
}));
export const listDiscoveryRuns=()=>api<DiscoveryRun[]>('/portal/admin/discovery-runs');
export const getFormalDiscoveryExperiment=()=>api<FormalDiscoveryExperiment>('/portal/admin/discovery-formal-experiment');
export const listFormalExperimentClusters=()=>api<FormalExperimentCluster[]>('/portal/admin/discovery-formal-experiment/clusters');
export const replayFormalDiscoveryExperiment=()=>api<FormalDiscoveryReplay>('/portal/admin/discovery-formal-experiment/replay',{method:'POST',body:JSON.stringify({})});
export const importFormalExperimentResults=()=>api<FormalExperimentImportResult>('/emerging-positions/import-formal-experiment',{method:'POST',body:JSON.stringify({})});
export const listDiscoveryCandidates=(params?:{status?:string;window_id?:string;candidate_id?:string})=>{
  const query=new URLSearchParams();
  if(params?.status)query.set('status',params.status);
  if(params?.window_id)query.set('window_id',params.window_id);
  if(params?.candidate_id)query.set('candidate_id',params.candidate_id);
  const suffix=query.toString();
  return api<DiscoveryCandidateList>(`/portal/admin/discovery-candidates${suffix?`?${suffix}`:''}`);
};
export const getDiscoveryCandidate=(candidateId:string)=>api<DiscoveryCandidateDetail>(`/portal/admin/discovery-candidates/${candidateId}`);
export const getDiscoveryCandidateTrajectory=(candidateId:string)=>api<CandidateTrajectory>(`/portal/admin/discovery-candidates/${candidateId}/trajectory`);
export const enterDiscoveryCandidateGovernance=(candidateId:string)=>api<EmergingPosition>(`/portal/admin/discovery-candidates/${candidateId}/enter-governance`,{method:'POST',body:JSON.stringify({})});
export const listClusters=()=>api<PositionCluster[]>('/position-clusters');
export const getClusterJds=(clusterId:string)=>api<ClusterJD[]>(`/position-clusters/${clusterId}/jds`);
export const startDiscovery=(params?:{maxSamples?:number;datasetId?:string})=>api<DiscoveryTask>('/position-clusters/tasks',{method:'POST',body:JSON.stringify({
  algorithm:'emerge_v3_2',
  ...(params?.maxSamples?{max_samples:params.maxSamples}:{}),
  dataset_id:params?.datasetId||'d5-short-window-main-v1-37585b4079dd',
})});
export const createCandidate=(clusterId:string)=>api(`/emerging-positions/from-cluster/${clusterId}`,{method:'POST',body:JSON.stringify({})});
export const listCandidates=()=>api<EmergingPosition[]>('/emerging-positions');
export const updateCandidate=(id:string,values:object)=>api<EmergingPosition>(`/emerging-positions/${id}`,{method:'PUT',body:JSON.stringify(values)});
export const submitCandidateReview=(id:string)=>api<EmergingPosition>(`/emerging-positions/${id}/submit-review`,{method:'POST',body:JSON.stringify({})});
export const reviewCandidate=(id:string,conclusion:'approved'|'rejected',reason:string)=>api<EmergingPosition>(`/emerging-positions/${id}/review`,{method:'POST',body:JSON.stringify({conclusion,reason})});
export const publishCandidate=(id:string)=>api<EmergingPosition>(`/emerging-positions/${id}/publish`,{method:'POST',body:JSON.stringify({})});
export const promoteCandidate=(id:string)=>api<{emerging_id:string;standard_position:StandardPositionProjection}>(`/emerging-positions/${id}/promote-to-position`,{method:'POST',body:JSON.stringify({})});
export const generateDefinition=(id:string)=>api<EmergingPosition>(`/emerging-positions/${id}/generate-definition`,{method:'POST',body:JSON.stringify({})});
export const listDefinitionVersions=(id:string)=>api<DefinitionVersion[]>(`/portal/admin/emerging-positions/${id}/definition-versions`);
export const selectDefinitionVersion=(id:string,versionId:string)=>api<DefinitionVersion&{definition:EmergingPosition}>(`/emerging-positions/${id}/definition-versions/${versionId}/select`,{method:'POST',body:JSON.stringify({})});
