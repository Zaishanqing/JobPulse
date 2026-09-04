/**
 * Frontend TypeScript mirror of the official InsightCard BFF contract.
 *
 * The shape is generated from the Python domain dataclasses and frozen in
 * `insight-card-contract.snapshot.json` (see
 * 由实验资产构建脚本生成后固化为静态 JSON（不再引用实验资产路径）。
 * 不要手工编辑该 JSON：
 * contract drift is a test failure.
 */

export type InsightClaimType =
  | 'emerging_position'
  | 'role_migration'
  | 'trend_change'
  | 'matching_what_if'
  | 'fact'
  | 'statistical_relation'
  | 'prediction'
  | 'recommendation'
  | 'human_decision';

export type InsightAuthorityState = 'candidate' | 'reviewed' | 'authoritative';
export type InsightNextAction = 'publish' | 'collect_evidence' | 'rerun' | 'review' | 'user_action' | 'none';
export type InsightUncertaintyState =
  | 'ok'
  | 'not_observed'
  | 'unresolved'
  | 'insufficient_evidence'
  | 'source_concentrated'
  | 'stale_observation'
  | 'blocked';
export type InsightCoverageStatus = 'covered' | 'unknown';
export type HumanDecisionValue = 'approved' | 'rejected';

export type InsightEvidenceRefContract = {
  evidence_id: string;
  source_object_type: string;
  source_object_id: string;
  source_document_id: string;
  source_version: string;
  quote: string | null;
  location_start: number | null;
  location_end: number | null;
  used: boolean;
};

export type InsightSensitivityResultContract = {
  ablation_type: string;
  removed_group_id: string | null;
  removed_share: number;
  before_state: string;
  after_state: string;
  threshold_crossed: boolean;
  before_score: number | null;
  after_score: number | null;
  certificate_status: string;
  fragile_factor: string | null;
};

export type InsightHumanDecisionContract = {
  decision_id: string;
  decision: HumanDecisionValue;
  decided_at: string | null;
  decided_by: string | null;
  reason: string | null;
  original_authority_state: InsightAuthorityState | null;
  bound_object_type: string | null;
  bound_object_id: string | null;
  release_ref: string | null;
  graph_version_ref: string | null;
  algorithm_version: string | null;
  config_version: string | null;
};

export type InsightTemporalSourceLagRow = {
  source_id: string;
  valid_sample_count: number;
  median_delay_days: number | null;
  p90_delay_days: number | null;
  pipeline_observation_count: number;
  unknown_provenance_count: number;
  missing_publish_count: number;
  invalid_sample_count: number;
};

export type InsightTemporalEvidenceContract = {
  reference_date: string | null;
  publish_time_coverage: number | null;
  median_market_age_days: number | null;
  p90_market_age_days: number | null;
  stale_evidence_ratio: number | null;
  freshness_adjusted_neff: number | null;
  source_lag_summary: InsightTemporalSourceLagRow[];
  temporal_algorithm_version: string | null;
  temporal_reasons: string[];
  fresh_evidence_count: number | null;
  stale_evidence_count: number | null;
  unknown_evidence_count: number | null;
  time_provenance_policy: string | null;
};

export type InsightCardContract = {
  contract_version: 'insight-card.v1';
  insight_id: string;
  claim_type: InsightClaimType;
  subject_ref: string;
  claim: string;
  authority_state: InsightAuthorityState;
  evidence_refs: InsightEvidenceRefContract[];
  counter_evidence_refs: InsightEvidenceRefContract[];
  used_evidence_ids: string[];
  effective_sample_size: number | null;
  raw_evidence_count: number | null;
  uncertainty_state: InsightUncertaintyState;
  uncertainty_reasons: string[];
  sensitivity_results: InsightSensitivityResultContract[];
  fragile_factor: string | null;
  data_refs: string[];
  release_refs: string[];
  graph_version_refs: string[];
  catalog_refs: string[];
  algorithm_version: string;
  algorithm_config_version: string | null;
  algorithm_config_hash: string | null;
  evidence_algorithm_version: string;
  evidence_config_hash: string;
  evidence_subject_ref: string | null;
  coverage_status: InsightCoverageStatus | null;
  coverage_summary: string[];
  source_coverage: number | null;
  human_decision: InsightHumanDecisionContract | null;
  limitations: string[];
  temporal_evidence: InsightTemporalEvidenceContract | null;
  next_action: InsightNextAction;
};
