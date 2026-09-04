export type ResumeRecord={
  resume_id:string;
  display_name:string;
  original_filename?:string|null;
  source_type:string;
  file_id?:string|null;
  raw_text:string;
  parse_status:string;
  input_extraction_status?:string;
  input_error_message?:string|null;
  implementation_status:string;
  source_cv_version_id?:string|null;
  validated_cv_snapshot_id?:string|null;
  created_at:string|null;
  updated_at:string|null;
};

export type ResumeParseResult={
  parse_result_id:string;
  resume_id:string;
  education:Array<Record<string,unknown>>;
  projects:Array<Record<string,unknown>>;
  internships:Array<Record<string,unknown>>;
  skills:Array<{raw_skill:string;normalized_skill_id:string;confidence:number;evidence:string}>;
  certificates:Array<Record<string,unknown>>;
  competitions:Array<Record<string,unknown>>;
  parse_confidence:number;
  need_review:boolean;
};

export type ResumeSkill={
  resume_skill_id:string;
  resume_id:string;
  skill_id:string;
  raw_skill:string;
  confidence:number;
  evidence:string;
  proficiency:string|null;
};

export type CVExtractionTaskStatus='pending'|'running'|'succeeded'|'failed';
export type CVProcessingStage='queued'|'ocr_running'|'extracting'|'contract_validating'|'semantic_repairing'|'review_pending'|'failed'|'succeeded';
export type CVConfirmationStatus='pending'|'confirmed';
export type CVValidationConclusion='pass'|'warn'|'block';
export type CVFieldDecisionValue='accept'|'correct'|'unknown'|'remove';

export type CVExtractionImport={
  source_cv_id:string;
  source_cv_version_id:string;
  cv_extraction_task_id:string;
  created_source:boolean;
  created_version:boolean;
  created_task:boolean;
  task_status:CVExtractionTaskStatus;
  text_extraction_status:string|null;
  extraction_method:string|null;
  extraction_provider:string|null;
  source_file_id:string|null;
};

export type CVExtractionTask={
  task_id:string;
  source_cv_version_id:string;
  owner_id:string;
  request_id:string;
  execution_id:string|null;
  execution_metadata:Record<string,unknown>|null;
  status:CVExtractionTaskStatus;
  processing_stage:CVProcessingStage;
  attempt_count:number;
  max_attempts:number;
  last_error_code:string|null;
  last_error_message:string|null;
  retryable:boolean;
  claimed_by:string|null;
  lease_expires_at:string|null;
  heartbeat_at:string|null;
  next_attempt_at:string|null;
  finished_at:string|null;
  validation_conclusion:CVValidationConclusion|null;
  validation_report_payload:Record<string,unknown>|null;
  validation_task_id:string|null;
  validation_report_id:string|null;
  resume_id:string|null;
  created_at:string|null;
  updated_at:string|null;
  review_payload:Record<string,unknown>|null;
  review_id:string|null;
  confirmation_status:CVConfirmationStatus|null;
  latest_validated_cv_snapshot_id:string|null;
  confirmed_at:string|null;
  confirmed_by:string|null;
  review_revision:number;
  confirmation_idempotency_key:string|null;
  confirmation_idempotency_id:string|null;
};

export type CVReviewEvidence={
  source_document_id:string;
  source_id:string;
  quote:string;
  start:number|null;
  end:number|null;
  alignment:string;
  occurrence_index:number|null;
};

export type CVReviewableField={
  field_id:string;
  field_type:string;
  section:string;
  item_id:string;
  field_path:string;
  field_label:string;
  original_value:string|null;
  suggested_value:string|null;
  evidence:CVReviewEvidence|null;
  flag_codes:string[];
};

export type CVReviewFlag={
  code:string;
  severity:string;
  rule_scope:string|null;
  message:string|null;
  suggested_action:string|null;
  item_id:string|null;
};

export type CVValidationSummary={
  conclusion:CVValidationConclusion;
  policy_version:string;
  validation_task_id:string|null;
  validation_report_id:string|null;
  blocking_reasons:string[];
};

export type CVReview={
  task_id:string;
  source_cv_id:string;
  source_cv_version_id:string;
  status:CVExtractionTaskStatus;
  confirmation_status:CVConfirmationStatus|null;
  review_id:string|null;
  review_revision:number;
  source_text:string|null;
  source_file_id:string|null;
  content_type:string|null;
  ocr_layout:Array<Record<string,unknown>>|null;
  reviewable_fields:CVReviewableField[];
  review_flags:CVReviewFlag[];
  validation:CVValidationSummary|null;
};

export type CVFieldDecisionInput={
  field_id:string;
  field_type:string;
  section:string;
  item_id:string;
  field_path:string;
  decision:CVFieldDecisionValue;
  corrected_value:string|null;
  correction_reason:string|null;
  evidence_quote:string|null;
  evidence_start:number|null;
  evidence_end:number|null;
};

export type CVReviewDecisionDraft={
  decision:''|CVFieldDecisionValue;
  corrected_value:string|null;
  correction_reason:string|null;
  evidence_quote:string|null;
  evidence_start:number|null;
  evidence_end:number|null;
};

export type CVConfirmPayload={
  expected_review_id:string;
  idempotency_key:string;
  field_decisions:CVFieldDecisionInput[];
  normalization_version:string|null;
  taxonomy_version:string|null;
  display_name:string|null;
};

export type CVConfirmationResult={
  snapshot_id:string;
  snapshot_revision:number;
  resume_id:string;
  task_id:string;
  supersedes_snapshot_id:string|null;
  idempotency_key:string|null;
};

export type ValidatedCVSnapshotFieldDecision={
  field_id:string;
  field_type:string;
  section:string;
  item_id:string;
  field_path:string;
  decision:CVFieldDecisionValue;
  corrected_value:string|null;
  correction_reason:string|null;
};

export type ValidatedCVSnapshot={
  snapshot_id:string;
  resume_id:string|null;
  cv_extraction_task_id:string;
  snapshot_revision:number;
  supersedes_snapshot_id:string|null;
  normalization_version:string|null;
  taxonomy_version:string|null;
  field_decisions:ValidatedCVSnapshotFieldDecision[];
  extraction_payload:Record<string,unknown>|null;
};

export type MatchPreflight={
  ready:boolean;
  cv_snapshot_ready:boolean;
  cv_profile_ready:boolean;
  position_profile_ready:boolean;
  blockers:string[];
  validated_cv_snapshot_id:string|null;
  position_graph_version:string|null;
};

export type MatchPosition={
  position_id:string;
  position_name:string;
  taxonomy_family_name:string|null;
  status:string;
  lifecycle_status:'active'|'deprecated';
  matchable:boolean;
  reason:string;
  blockers:string[];
  position_graph_version:string|null;
  position_profile_version?:string|null;
};

export type EnterpriseJob={
  enterprise_job_id:string;
  title:string;
  status:string;
};

export type EvidenceVersion={
  validated_cv_snapshot_id:string|null;
  source_cv_version_id:string|null;
  resume_id:string|null;
  position_id:string|null;
  graph_version:string|null;
  source_jd_version_id:string|null;
  evaluation_id:string|null;
};

export type Evidence={
  source_object_type:string;
  source_object_id:string;
  source_document_id:string|null;
  source_fragment_id:string;
  quote:string;
  start:number|null;
  end:number|null;
  alignment:string;
  occurrence_index:number|null;
  version:EvidenceVersion;
  result_reference:string;
};

export type SemanticRetrievalEvidence={
  query_fragment_id:string;
  candidate_fragment_id:string;
  query_fragment_type:string;
  candidate_fragment_type:string;
  candidate_source_id:string;
  similarity:number;
  rank:number;
  dense_rank?:number|null;
  sparse_rank?:number|null;
  rrf_score?:number;
  retrieval_score?:number|null;
  rerank_score?:number|null;
  final_rank?:number|null;
  evidence_ref:Evidence;
  position_evidence_ref:Evidence;
  profile_version:string|null;
  reranker_model_revision?:string|null;
  embedding_model:string;
  embedding_revision:string;
  embedding_dimension:number;
  embedding_normalized:boolean;
  embedding_normalization:'l2';
  vector_representation:'dense';
  vector_similarity:'cosine';
  text_derivation_version:string;
  index_revision?:string|null;
  collection?:string|null;
  retrieval_trace_id:string;
};

export type SemanticCandidate={
  candidate_source_id:string;
  score:number;
  evidence:SemanticRetrievalEvidence[];
  retrieval_score?:number|null;
  rerank_score?:number|null;
  final_rank?:number|null;
  degraded?:boolean;
  degradation_reason?:string|null;
};

export type MatchSkillResult={
  requirement_id:string;
  skill_id?:string|null;
  skill_name?:string|null;
  importance_level?:string|null;
  requirement_weight?:number;
  required_level?:string|null;
  candidate_declared_level?:string|null;
  candidate_demonstrated_level?:string|null;
  verification_status?:string|null;
  match_status:string;
  position_evidence:Evidence[];
  candidate_evidence:Evidence[];
  reason_code?:string|null;
  confidence?:number;
  match_type?:string|null;
  related_candidate_skill_id?:string|null;
  prerequisite_skill_ids?:string[];
  relation_type?:string|null;
  relation_confidence?:number|null;
  relation_evidence?:Evidence[];
  relation_source?:string|null;
  relation_graph_version?:string|null;
  transferability_score?:number;
  semantic_model?:string|null;
  semantic_algorithm_version?:string|null;
  semantic_candidate_id?:string|null;
};
export type MatchSkill=MatchSkillResult;

export type HardConstraintResult={
  requirement_id:string;
  constraint_type:string;
  status:string;
  required_value?:string|null;
  candidate_value?:string|null;
  position_evidence:Evidence[];
  candidate_evidence:Evidence[];
  reason_code:string;
  confidence:number;
};

export type MatchingMethod='rule'|'semantic_verified'|'unknown';

export type ResponsibilityCandidate={
  experience_id:string;
  text:string;
  retrieval_score?:number|null;
  ce_score?:number|null;
  threshold_margin?:number|null;
  evidence_refs:Evidence[];
};

export type ResponsibilityResult={
  requirement_id:string;
  position_requirement?:string|string[]|null;
  candidate_experience_id?:string|null;
  candidate_experience?:string|null;
  candidate_tasks?:string[];
  match_status?:string|null;
  status_detail?:'matched'|'partial'|'uncertain'|'insufficient_evidence'|'not_observed'|null;
  position_evidence:Evidence[];
  candidate_evidence:Evidence[];
  reason_code?:string|null;
  confidence?:number;
  match_type?:string|null;
  ce_score?:number|null;
  retrieval_score?:number|null;
  threshold_margin?:number|null;
  top_candidates?:ResponsibilityCandidate[];
};

export type ProjectResult=ResponsibilityResult;
export type ScenarioResult=ResponsibilityResult;

export type EvaluationSummary={
  hard_constraint_pass_count:number;
  hard_constraint_fail_count:number;
  required_skill_matched_count:number;
  required_skill_missing_count:number;
  bonus_skill_matched_count:number;
  bonus_skill_missing_count:number;
  coverage_denominator_policy:string;
};

export type DimensionScore={
  dimension:string;
  score:number|null;
  confidence:number;
  configured_weight:number;
  effective_weight:number;
  applicable_count:number;
  scored_count:number;
  uncertain_count:number;
};

export type ScoreInsight={
  dimension:string;
  result_id:string;
  reason_code:string;
  message:string;
  evidence:Evidence[];
};

export type ScoreContribution={
  dimension:string;
  result_id:string;
  status:string;
  match_type:string|null;
  reason_code:string;
  score_value:number|null;
  effective_weight:number;
  weighted_points:number;
  confidence:number;
  position_evidence:Evidence[];
  candidate_evidence:Evidence[];
  relation_evidence:Evidence[];
};

export type FinalMatchResult={
  overall_score?:number|null;
  match_confidence:number;
  recommendation_level:'strong_match'|'potential_match'|'weak_match'|'not_recommended'|'insufficient_information';
  hard_gate_status:'passed'|'failed'|'uncertain'|'not_applicable';
  dimension_scores?:DimensionScore[];
  score_contributions?:ScoreContribution[];
  strengths?:ScoreInsight[];
  gaps?:ScoreInsight[];
  uncertain_items?:ScoreInsight[];
  explanation:string;
  algorithm_version:string;
  scoring_config_version:string;
  cv_profile_id?:string|null;
  position_profile_id?:string|null;
  input_evaluation_algorithm_version:string;
  source_evaluation_id?:string|null;
  cv_taxonomy_version:string;
  cv_derivation_version:string;
  position_taxonomy_version:string;
  position_graph_version:string;
  position_quality_snapshot_id:string;
  position_trend_version?:string|null;
  vector_text_derivation_version?:string|null;
  embedding_model?:string|null;
  embedding_version?:string|null;
  semantic_algorithm_version?:string|null;
  semantic_threshold_config_version?:string|null;
  semantic_index_revision?:string|null;
  semantic_collection?:string|null;
  semantic_embedding_dimension?:number|null;
  semantic_embedding_normalized?:boolean|null;
  semantic_embedding_normalization?:'l2'|null;
  semantic_vector_representation?:'dense'|null;
  semantic_vector_similarity?:'cosine'|null;
  semantic_text_derivation_version?:string|null;
  semantic_weight?:number;
};

export type EvaluationContract={
  evaluation_id:string;
  cv_profile_id:string|null;
  cv_profile_version?:string|null;
  position_profile_id:string|null;
  position_profile_version?:string|null;
  algorithm_version:string;
  evaluation_status:string;
  error_code?:string|null;
  error_message?:string|null;
  hard_constraint_results:HardConstraintResult[];
  skill_results:MatchSkillResult[];
  responsibility_results:ResponsibilityResult[];
  project_results:ProjectResult[];
  scenario_results:ScenarioResult[];
  input_coverage?:Record<string,{
    count:number;
    available:boolean;
    condition_available?:boolean;
    candidate_available?:boolean;
  }>;
  summary:EvaluationSummary|null;
  final_match_result:FinalMatchResult|null;
  semantic_status?:'disabled'|'available'|'unavailable';
  semantic_error_code?:string|null;
  semantic_shadow_status?:'disabled'|'available'|'unavailable';
  semantic_shadow_score?:number|null;
  semantic_shadow_evidence?:SemanticRetrievalEvidence[];
  semantic_candidates?:SemanticCandidate[];
  semantic_latency_ms?:number|null;
  semantic_retrieval_trace_id?:string|null;
  semantic_embedding_model?:string|null;
  semantic_embedding_revision?:string|null;
  semantic_embedding_dimension?:number|null;
  semantic_embedding_normalized?:boolean|null;
  semantic_embedding_normalization?:'l2'|null;
  semantic_vector_representation?:'dense'|null;
  semantic_vector_similarity?:'cosine'|null;
  semantic_text_derivation_version?:string|null;
  semantic_index_revision?:string|null;
  semantic_collection?:string|null;
  vector_profile_version?:string|null;
  semantic_weight?:number;
  semantic_effective_weight?:number;
};

export type PrioritizedGap={
  gap_type:string;
  requirement_id:string;
  skill_id?:string|null;
  current_level?:string|null;
  target_level?:string|null;
  priority:string;
  priority_score:number;
  reason_codes:string[];
  evidence:Evidence[];
  position_evidence_present?:boolean;
  candidate_evidence_present?:boolean;
  source_match_type?:string|null;
  transferable_skill_ids?:string[];
  transferability_score?:number;
  prerequisite_skill_ids?:string[];
  current_ownership?:string|null;
  target_ownership?:string|null;
  score_effect_status?:'modeled'|'not_modeled_in_v1';
};

export type WhatIfAction={
  action_id:string;
  action_type:'add_skill'|'add_project_experience'|'strengthen_evidence'|'strengthen_ownership'|'satisfy_hard_condition'|'controlled_skill_transfer';
  skill_id?:string|null;
  canonical_name?:string|null;
  learning_title?:string|null;
  target_level?:string|null;
  ownership?:string|null;
  target_requirement_ids:string[];
  responsibilities:string[];
  business_scenarios:string[];
  path_refs:string[];
  estimated_hours:number;
  cost_band?:{
    min_hours:number;
    expected_hours:number;
    max_hours:number;
    confidence:number;
    basis:string;
  }|null;
  stage?:'foundation'|'proficiency'|'evidence'|'project'|'ownership'|'context'|'hard_gate'|'transfer'|null;
  requires_action_ids:string[];
  supersedes_action_ids:string[];
  cost_model:string;
  estimated_score_delta?:number|null;
  estimated_utility?:number|null;
  score_effect_reason?:string|null;
  milestone_status?:string|null;
  deliverable?:string|null;
  acceptance_criteria?:string[];
};

export type DimensionDelta={
  dimension:string;
  baseline_score:number|null;
  scenario_score:number|null;
  delta:number|null;
};

export type WhatIfResult={
  generation_status:'completed'|'rejected';
  scenario_id:string;
  baseline_evaluation?:EvaluationContract|null;
  scenario_evaluation?:EvaluationContract|null;
  // Modeled projected re-score evaluation used when actions are still
  // planned; its dimension scores power the route radar comparison.
  projected_evaluation?:EvaluationContract|null;
  actions?:WhatIfAction[];
  baseline_score?:number|null;
  // Primary modeled-contract fields: modeled counterfactual re-score, NOT
  // observed real-world learning gains.
  outcome_semantics?:'modeled_counterfactual';
  observed_outcome?:false;
  modeled_final_score?:number|null;
  modeled_score_delta?:number|null;
  modeled_confidence_delta?:number|null;
  // Deprecated aliases (same values) kept for compatibility.
  scenario_score?:number|null;
  score_delta?:number|null;
  baseline_confidence?:number|null;
  scenario_confidence?:number|null;
  confidence_delta?:number|null;
  baseline_recommendation?:string|null;
  scenario_recommendation?:string|null;
  baseline_hard_gate_status?:string|null;
  scenario_hard_gate_status?:string|null;
  dimension_deltas?:DimensionDelta[];
  denominator_changed?:boolean;
  score_effect_status?:'modeled'|'not_modeled_in_v1';
  baseline_evaluation_id?:string|null;
  scoring_algorithm_version?:string|null;
  scoring_config_version?:string|null;
  position_graph_version?:string|null;
  target_type?:'standard_position'|'enterprise_job'|null;
  use_enterprise_weights?:boolean|null;
  hypothetical?:boolean;
  algorithm_version:string;
  error_code?:string|null;
  error_message?:string|null;
  projected_if_completed?:boolean;
  projected_actions?:WhatIfAction[];
  projected_score?:number|null;
  projected_score_delta?:number|null;
  projected_confidence?:number|null;
  projected_recommendation?:string|null;
  projected_hard_gate_status?:string|null;
  current_verified_outcome?:string|null;
  projected_if_completed_outcome?:string|null;
};

export type ExplanationFactor={
  factor_id:string;
  factor_type:string;
  requirement_id?:string|null;
  reason_code:string;
  criticality:'critical'|'noncritical';
  evidence_source_ids:string[];
  used_by_scorer:boolean;
  evidence_supported:boolean;
};

export type EvidenceDeletionResult={
  generation_status:'completed'|'rejected';
  deletion_run_id:string;
  deletion_kind:'critical'|'noncritical'|null;
  deleted_evidence_source_ids:string[];
  critical_evidence_source_ids:string[];
  noncritical_evidence_source_ids:string[];
  explanation_factors:ExplanationFactor[];
  baseline_evaluation:EvaluationContract|null;
  ablated_evaluation:EvaluationContract|null;
  baseline_gap_analysis:GapAnalysis|null;
  ablated_gap_analysis:GapAnalysis|null;
  baseline_score:number|null;
  ablated_score:number|null;
  retained_only_score:number|null;
  score_delta:number|null;
  dimension_deltas:DimensionDelta[];
  baseline_hard_gate_status:string|null;
  ablated_hard_gate_status:string|null;
  hard_gate_delta:string|null;
  added_gap_ids:string[];
  removed_gap_ids:string[];
  added_action_ids:string[];
  removed_action_ids:string[];
  comprehensiveness:number|null;
  sufficiency:number|null;
  unsupported_reason_rate:number;
  faithfulness_status:'faithful'|'possibly_unfaithful'|'unstable'|'not_applicable';
  baseline_evaluation_id:string|null;
  cv_profile_version:string|null;
  position_profile_version:string|null;
  scoring_algorithm_version:string|null;
  scoring_config_version:string|null;
  classification_policy_version:string;
  stability_threshold_points:number;
  hypothetical:boolean;
  algorithm_version:string;
  error_code?:string|null;
  error_message?:string|null;
};

export type LearningRoute={
  route_type:'fastest_employment'|'budget_max_gain'|'foundation_first';
  action_ids:string[];
  total_cost_hours:number;
  baseline_score:number|null;
  // Modeled counterfactual re-score (NOT observed learning gains).
  outcome_semantics?:'modeled_counterfactual';
  observed_outcome?:false;
  modeled_final_score?:number|null;
  modeled_score_delta?:number|null;
  modeled_confidence_delta?:number|null;
  // Deprecated aliases (same values) kept for compatibility.
  final_score:number|null;
  projected_match_gain:number|null;
  confidence_gain:number|null;
  target_reachable:boolean;
  final_recommendation:string|null;
  remaining_blocker_ids:string[];
  path_refs:string[];
  // Explicit per-route real cost provenance (kept optional for compatibility).
  action_costs?:ActionCost[];
  // Modeled scenario dimension scores for the radar comparison panel;
  // the baseline panel reuses the report dimension scores.
  scenario_dimension_scores?:DimensionScore[];
  algorithm_version:string;
};

export type ActionCost={
  action_id:string;
  direct_hours:number;
  dependency_hours:number;
  total_hours:number;
  difficulty:'low'|'medium'|'high';
  selected:boolean;
  cost_model:string;
  cost_source_type:'dataset_backed'|'expert_estimate'|'manual'|'heuristic'|'unknown';
  cost_source_ref?:string|null;
  estimate_status:'verified'|'estimated'|'unknown';
};

export type MinimalActionSet={
  status:'reached'|'already_satisfied'|'hard_blocked'|'position_evidence_insufficient'|'no_positive_actions'|'budget_excluded'|'unreachable';
  source_evaluation_id:string;
  scenario_id?:string|null;
  selected_action_ids:string[];
  deferred_action_ids:string[];
  action_costs:ActionCost[];
  minimum_action_count:number;
  total_cost_hours:number;
  budget_hours?:number|null;
  budget_used_hours:number;
  budget_remaining_hours?:number|null;
  baseline_score?:number|null;
  // Modeled counterfactual re-score (NOT observed learning gains).
  outcome_semantics?:'modeled_counterfactual';
  observed_outcome?:false;
  modeled_final_score?:number|null;
  modeled_score_delta?:number|null;
  modeled_confidence_delta?:number|null;
  // Deprecated aliases (same values) kept for compatibility.
  scenario_score?:number|null;
  score_delta?:number|null;
  dimension_deltas:DimensionDelta[];
  baseline_hard_gate_status?:string|null;
  scenario_hard_gate_status?:string|null;
  hard_gate_delta?:string|null;
  target_reachable:boolean;
  covered_requirement_ids:string[];
  evidence_refs:Evidence[];
  path_refs:string[];
  unreachable_reason_codes:string[];
  graph_version_id:string;
  policy_version:string;
  search_status:'exact_bounded'|'bounded_beam';
  algorithm_version:string;
};

export type LearningStep={
  step_order:number;
  source_action_id?:string|null;
  target_skill_id?:string|null;
  objective:string;
  prerequisite_skill_ids:string[];
  basis:string[];
  estimated_hours:number;
  cost_source_type:'dataset_backed'|'expert_estimate'|'manual'|'heuristic'|'unknown';
  cost_source_ref?:string|null;
  estimate_status:'verified'|'estimated'|'unknown';
  cost_model?:string|null;
  completion_criteria:string[];
  source_requirement_ids:string[];
  reason_codes:string[];
  prerequisite_states?:{
    skill_id:string;
    status:'satisfied'|'missing'|'unknown';
    source:'candidate_profile'|'evaluation'|'unavailable';
    evidence_refs:Evidence[];
  }[];
  planning_status?:'ready'|'blocked';
  blocked_reason_codes?:string[];
};

export type SkillPathDecision={
  target_requirement_id:string;
  target_skill_id:string;
  status:'reachable'|'unreachable';
  paths:{
    path_id:string;
    source_skill_id:string;
    target_skill_id:string;
    target_requirement_id:string;
    node_skill_ids:string[];
    edges:{
      relation_id:string;
      source_skill_id:string;
      target_skill_id:string;
      relation_type:'equivalent'|'parent_child'|'related'|'transferable';
      graph_version:string;
      confidence:number;
      hop_number:number;
      edge_cost_hours:number;
      evidence_refs:Evidence[];
    }[];
    hop_count:number;
    total_cost_hours:number;
    minimum_confidence:number;
    effective_confidence:number;
    outcome_status:'eligible'|'partial';
    graph_version_id:string;
    cost_model:string;
  }[];
  reason_codes:string[];
  max_hops:number;
  max_cost_hours:number;
  relation_whitelist:string[];
  source_status:'available'|'unavailable'|'error';
  algorithm_version:string;
};

export type CounterfactualSuggestion={
  requirement_id:string;
  skill_id?:string|null;
  suggestion:string;
  basis_evidence:Evidence[];
};

export type GapAnalysis={
  generation_status:'completed'|'rejected'|null;
  result_status?:string|null;
  prioritized_gaps:PrioritizedGap[];
  learning_path:LearningStep[];
  counterfactual_suggestions:CounterfactualSuggestion[];
  candidate_actions:WhatIfAction[];
  learning_routes:LearningRoute[];
  minimal_action_set?:MinimalActionSet|null;
  skill_path_decisions:SkillPathDecision[];
  time_budget_hours?:number|null;
  over_budget?:boolean;
  estimated_readiness?:number|null;
  algorithm_version?:string|null;
  config_version?:string|null;
  gap_policy_version?:string|null;
  gap_policy_hash?:string|null;
  error_code?:string|null;
  error_message?:string|null;
};

export type AlgorithmVersions={
  evaluation?:string|null;
  scoring?:string|null;
  scoring_config?:string|null;
  gap?:string|null;
  gap_config?:string|null;
  semantic?:string|null;
};

export type DataVersions={
  cv_source?:string|null;
  position_source?:string|null;
  cv_taxonomy?:string|null;
  position_taxonomy?:string|null;
  graph?:string|null;
  embedding?:string|null;
};

export type MatchVersions={
  schema_version?:string|null;
  profile_contract_mapping_version?:string|null;
  graph_version?:string|null;
  embedding_model?:string|null;
  embedding_version?:string|null;
  embedding_dimension?:number|null;
  vector_text_derivation_version?:string|null;
  semantic_algorithm_version?:string|null;
  semantic_threshold_version?:string|null;
  evaluation_algorithm_version?:string|null;
  scoring_algorithm_version?:string|null;
  scoring_config_version?:string|null;
  gap_algorithm_version?:string|null;
  gap_config_version?:string|null;
  semantic_index_revision?:string|null;
  target_type?:string|null;
  use_enterprise_weights?:boolean|null;
  generate_learning_path?:boolean|null;
  cv_source_version?:string|null;
  position_source_version?:string|null;
  cv_taxonomy_version?:string|null;
  position_taxonomy_version?:string|null;
  position_graph_version?:string|null;
};

export type MatchLineage={
  resume_id?:string|null;
  position_id?:string|null;
  position_name?:string|null;
  validated_cv_snapshot_id?:string|null;
  target_type?:string|null;
  provider?:string|null;
  method?:string|null;
  algorithm_versions?:AlgorithmVersions|null;
  data_versions?:DataVersions|null;
};

export type EvaluationReport={
  evaluation_id:string;
  task_id?:string|null;
  status?:string|null;
  result_status?:string|null;
  matching_method?:MatchingMethod;
  degraded?:boolean;
  stale:boolean;
  stale_reason_codes:string[];
  evaluation:EvaluationContract;
  gap_analysis:GapAnalysis;
  versions:MatchVersions;
  lineage:MatchLineage|null;
  created_at:string|null;
  updated_at:string|null;
};

export type MatchReportReferenceLineage={
  algorithm_version?:string|null;
  source_version?:string|null;
  taxonomy_version?:string|null;
  graph_version?:string|null;
  cv_profile_version?:string|null;
  position_profile_version?:string|null;
};

export type MatchReference={
  evaluation_id:string|null;
  task_id:string;
  resume_id?:string|null;
  position_id?:string|null;
  target_type?:string|null;
  status?:string|null;
  provider?:string|null;
  matching_method?:MatchingMethod|null;
  degraded?:boolean|null;
  overall_score?:number|null;
  origin?:string|null;
  error_code?:string|null;
  lineage?:MatchReportReferenceLineage|null;
  created_at:string|null;
  updated_at:string|null;
};

export type MatchRankingItem={
  rank:number;
  position_id:string;
  position_name:string;
  score:number;
  score_source:'coarse'|'formal';
  calculation_status:'preliminary'|'pending'|'running'|'completed'|'failed';
  evaluation_id:string|null;
  task_id:string|null;
  error_code?:string|null;
};

export type MatchRanking={
  resume_id:string;
  validated_cv_snapshot_id:string;
  algorithm_version:string;
  status:'ready'|'running'|'completed'|'cancelled';
  total:number;
  completed:number;
  items:MatchRankingItem[];
};

export type MatchTaskStatus='pending'|'running'|'succeeded'|'failed';
export type MatchTask={
  task_id:string;
  task_type?:string|null;
  status:MatchTaskStatus;
  canonical_status?:string|null;
  progress:number|null;
  result_payload?:{evaluation_id?:string|null}|null;
  result_reference?:string|null;
  evaluation_id?:string|null;
  error_code?:string|null;
  error_message?:string|null;
  attempt_count?:number|null;
  created_at?:string|null;
  updated_at?:string|null;
  execution_mode?:string|null;
  rule_based?:boolean|null;
  provider?:string|null;
  target_type?:string|null;
  use_enterprise_weights?:boolean|null;
  generate_learning_path?:boolean|null;
  versions?:MatchVersions|null;
  created?:boolean|null;
  implementation_status?:string|null;
  mock?:boolean|null;
  algorithm_version?:string|null;
  capability_implementation_status?:string|null;
  input_payload?:{resume_id?:string|null;target_type?:string|null;target_id?:string|null;use_enterprise_weights?:boolean|null;generate_learning_path?:boolean|null}|null;
  logs?:Array<{status?:string|null;at?:string|null;message?:string|null}>|null;
  created_by?:string|null;
  started_at?:string|null;
  finished_at?:string|null;
};

export type LearningPath={
  path_id:string;
  evaluation_id:string;
  target_position_id:string|null;
  time_budget_hours:number|null;
  learning_goal:string|null;
  status:string;
  provider?:string;
  stages:Array<Record<string,unknown>>;
  gap_analysis?:GapAnalysis;
  algorithm_versions?:Record<string,string>;
  data_versions?:Record<string,string>;
  created_at?:string|null;
  updated_at?:string|null;
};
