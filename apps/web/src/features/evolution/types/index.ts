export type StandardSkill={
  skill_id:string;
  skill_name:string;
  category:string;
  weight:number;
  confidence:number;
  importance_level:string;
  trend_score:number;
  evidence_count:number;
  created_at?:string;
  growth_rate?:number|null;
  trend_direction?:string|null;
  current_window_signal?:number|null;
  historical_window_signal?:number|null;
  evidence_references?:string[];
  quality_flags?:string[];
  score_explanation?:Record<string,unknown>|null;
};

export type StandardPosition={
  position_id:string;
  position_name:string;
  taxonomy_family_code:string|null;
  taxonomy_family_name:string|null;
  core_responsibilities:string[];
  required_skills:StandardSkill[];
  bonus_skills:StandardSkill[];
  industry_scenarios:string[];
  status:string;
    graph_onboarding_status:string;
    position_code?:string|null;
    definition?:string;
    aliases?:string[];
    include_when?:string[];
    exclude_when?:string[];
    confusable_with?:Array<{position_code:string;distinguish_by:string}>;
    taxonomy_version?:string;
    lifecycle_status?:'active'|'deprecated';
    deprecated_at?:string|null;
    replaced_by?:string|null;
    sample_support_status?:'none'|'sparse'|'sufficient';
  };

export type StandardGraph={
  position_id:string;
  position_name:string;
  graph_version:string;
  skills:StandardSkill[];
  relations:Array<{source:string;target:string;relation_type:string;weight:number}>;
  core_responsibilities:string[];
  industry_scenarios:string[];
};

export type TechStackView={
  position_id:string;
  position_name:string;
  tech_stacks:Array<{category:string;skills:StandardSkill[]}>;
};

export type LevelView={
  position_id:string;
  position_name:string;
  levels:Array<{importance_level:string;skills:StandardSkill[]}>;
};

export type SkillReplacement={declining_skill:StandardSkill;replacement_skill_name:string;reason:string};
export type SkillComboShift={from_combo:string[];to_combo:string[];reason:string};
export type TrendRisk={risk_type:string;level:string;reason:string};
export type TrendSkillDetail=StandardSkill&{
  growth_rate:number|null;
  trend_direction:string|null;
  current_window_signal:number|null;
  historical_window_signal:number|null;
  evidence_references:string[];
  quality_flags:string[];
  score_explanation:Record<string,unknown>|null;
};
export type UnresolvedTrendTerm={term?:string;evidence_references?:string[];[key:string]:unknown};
export type TrendReviewAdjustment={adjustment_id:string;actor_user_id:string;reason:string;before_values:Record<string,unknown>;after_values:Record<string,unknown>;created_at:string|null};

export type TrendReport={
  report_id:string;
  position_id:string;
  graph_version_id:string|null;
  time_window_start:string|null;
  time_window_end:string|null;
  current_graph:StandardGraph;
  skill_weight_distribution:Record<string,StandardSkill[]>;
  new_skills:StandardSkill[];
  rising_skills:StandardSkill[];
  declining_skills:StandardSkill[];
  replaced_skills:SkillReplacement[];
  skill_combo_shifts:SkillComboShift[];
  risks:TrendRisk[];
  summary:string|null;
  status:'draft'|'published';
  analysis_mode:string;
  provider:string;
  provider_run_id:string|null;
  algorithm_version:string|null;
  formula_version:string|null;
  skill_catalog_version:string|null;
  source_coverage:number|null;
  missing_sources:string[];
  quality_flags:string[];
  evidence_references:string[];
  unresolved_terms:UnresolvedTrendTerm[];
  skill_trends:TrendSkillDetail[];
  review_status:string|null;
  review_task_id:string|null;
  publishable:boolean;
  publication_blockers:string[];
  algorithm_result?:Record<string,unknown>|null;
  reviewed_result?:Record<string,unknown>|null;
  review_adjustments?:TrendReviewAdjustment[];
  created_at:string|null;
  updated_at:string|null;
};

export type TrendAnalysisTask={
  task_id:string;
  canonical_status:'pending'|'running'|'succeeded'|'failed'|'cancelled';
  progress:number;
  result_payload:Record<string,unknown>;
  result_reference?:string|null;
  error_code:string|null;
  error_message:string|null;
};

export type TrendReviewTask={task_id:string;object_type:string;object_id:string;status:string;reviewer_id:string|null;review_comment:string|null};

export type TrendReportCollection={
  schema_version:'trend-delivery.v1';
  items:TrendReport[];
  pagination:{page:number;page_size:number;total:number;total_pages:number};
  filters:Record<string,unknown>;
  sort:{by:string;order:'asc'|'desc'};
  not_found_ids:string[];
};

export type SnapshotChange={
  type:'added'|'removed'|'changed';
  skill:StandardSkill;
  before?:StandardSkill;
  fields?:string[];
};

// ── Evolution Event（GraphVersion 之间检测出的岗位能力结构变化）──
// Contract 以 knowledge-graph 服务 `detect_evolution_events` 与主系统 portal BFF
// `/portal/admin/knowledge-graph/positions/{position_id}/evolution-events` 返回为准。

export type EvolutionEventType =
  | 'skill_emergence'
  | 'skill_decline'
  | 'skill_replacement'
  | 'technology_stack_migration'
  | 'responsibility_shift'
  | 'role_expansion'
  | 'role_contraction'
  | 'position_rename';

export type EvolutionEntity =
  | string
  | {
      skill_id?: string;
      canonical_name?: string;
      name?: string;
      category_code?: string | null;
      weight?: number;
      confidence?: number;
      importance_level?: string;
      primary_modality?: string;
      statistics?: {
        support_document_count?: number;
        source_diversity?: number;
        enterprise_coverage?: number;
      };
      [key: string]: unknown;
    };

export type EvolutionLineage = {
  position_id: string;
  from_version_id: number;
  to_version_id: number;
};

export type EvolutionEvidence = {
  lineage: EvolutionLineage;
  source_relations: EvolutionEntity[];
  target_relations: EvolutionEntity[];
  source: string;
  related_context?: string;
  similarity?: number;
};

export type EvolutionEvent = {
  event_id: string;
  event_type: string;
  position_id: string;
  from_version: number;
  to_version: number;
  source_entities: EvolutionEntity[];
  target_entities: EvolutionEntity[];
  confidence: number;
  magnitude: number;
  evidence: EvolutionEvidence;
  reason: string;
  detector_version: string;
  created_at: string;
  metrics: Record<string, unknown>;
  metadata: Record<string, unknown>;
};

export type EvolutionGraphVersion = {
  id: number;
  version_number: number;
  version_name: string | null;
  build_run_id: number | null;
  release_id: string | null;
  rollback_from_version_id: number | null;
  rollback_from_version_number?: number | null;
  rollback_reason?: string | null;
  is_current?: boolean;
  dependencies?: Record<string, unknown>;
  created_at: string | null;
};

export type EvolutionVersionPair = {
  from_version_id: number;
  to_version_id: number;
};

/** 主系统 portal BFF 聚合返回：单个岗位跨全部相邻 GraphVersion 的演化事件。 */
export type EvolutionEventCollection = {
  position_id: string;
  from_version_id: number | null;
  to_version_id: number | null;
  event_type: string | null;
  versions: EvolutionGraphVersion[];
  version_pairs: EvolutionVersionPair[];
  events: EvolutionEvent[];
  count: number;
};

export type CapabilityEvolutionComparison={
  from_version_id:number;
  to_version_id:number;
  added:Relation[];
  removed:Relation[];
  changed:Array<{skill_id:string;changed_fields:string[];change_sources?:string[]}>;
  summary:{added:number;removed:number;changed:number;support_changed:number;context_changed:number};
  context_change_fields:string[];
};

export type CapabilityEvolutionFrame=EvolutionGraphVersion&{
  snapshot:Pick<GraphSnapshot,'position'|'skill_relations'> & {position_id?:string};
};

/** 只由已发布岗位图谱版本组成，不包含任何外部趋势情报。 */
export type CapabilityEvolution={
  schema_version:'capability-evolution.v1';
  position_id:string;
  frames:CapabilityEvolutionFrame[];
  comparisons:CapabilityEvolutionComparison[];
  events:EvolutionEvent[];
  frame_count:number;
  comparison_count:number;
  event_count:number;
};
import type {GraphSnapshot,Relation} from '../../../shared/api';
