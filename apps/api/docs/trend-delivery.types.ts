/** Stable frontend-facing contract. Source: trend-delivery.v1 OpenAPI. */
export type TrendResourceType =
  | "prediction_run"
  | "position_skill_trend_run"
  | "predicted_position"
  | "trend_report";

export interface PublicationGate {
  applicable: boolean;
  eligible: boolean;
  blockers: string[];
}

export interface TrendDeliveryResource {
  schema_version: "trend-delivery.v1";
  resource_type: TrendResourceType;
  resource_id: string;
  status: string;
  progress: number;
  source_coverage: number | null;
  missing_sources: string[];
  quality_flags: Array<string | Record<string, unknown>>;
  evidence_references: Array<string | Record<string, unknown>>;
  review_status: string | null;
  review_task_id: string | null;
  publication_gate: PublicationGate;
  [legacyOrResourceSpecificField: string]: unknown;
}

export interface TrendDeliveryCollection<T extends TrendDeliveryResource> {
  schema_version: "trend-delivery.v1";
  items: T[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    total_pages: number;
  };
  filters: Record<string, unknown>;
  sort: { by: string; order: "asc" | "desc" };
  not_found_ids: string[];
}

export interface ApiEnvelope<T> {
  code: number;
  message: string;
  data: T;
  trace_id: string;
}

export interface PredictionRun extends TrendDeliveryResource {
  resource_type: "prediction_run";
  task_id: string;
  provider_run_id?: string | null;
  canonical_status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
}

export interface PredictedPosition extends TrendDeliveryResource {
  resource_type: "predicted_position";
  predicted_id: string;
  position_name: string;
  provider_run_id: string;
  candidate_key: string;
  industry_domain: string;
  emergence_score: number;
  score_components: Record<string, number>;
  algorithm_version: string;
  formula_version: string;
  time_window: { start: string | null; end: string | null };
}

export interface PositionSkillTrendRun extends TrendDeliveryResource {
  resource_type: "position_skill_trend_run";
  task_id: string;
  report_id?: string | null;
  canonical_status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
}

export interface SkillTrendDetail {
  skill_id: string;
  skill_name: string;
  trend_score: number;
  growth_rate: number | null;
  trend_direction: string | null;
  evidence_count: number;
  evidence_references: string[];
  quality_flags: string[];
  score_explanation: Record<string, unknown>;
  current_window_signal: number | null;
  historical_window_signal: number | null;
  confidence: number;
}

export interface TrendReport extends TrendDeliveryResource {
  resource_type: "trend_report";
  report_id: string;
  position_id: string;
  graph_version_id: string;
  provider_run_id: string;
  algorithm_version: string;
  formula_version: string;
  skill_catalog_version: string;
  unresolved_terms: Array<Record<string, unknown>>;
  skill_trends: SkillTrendDetail[];
}
