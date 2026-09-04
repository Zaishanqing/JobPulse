import type {
  InsightAuthorityState,
  InsightCardContract,
  InsightNextAction,
  InsightUncertaintyState,
} from './types';

const authorityLabels: Record<InsightAuthorityState, string> = {
  candidate: '候选',
  reviewed: '已审核',
  authoritative: '权威',
};

const uncertaintyLabels: Record<InsightUncertaintyState, string> = {
  ok: '正常',
  not_observed: '未观测到',
  unresolved: '未解决',
  insufficient_evidence: '证据不足',
  source_concentrated: '来源集中',
  stale_observation: '观测过期',
  blocked: '阻断',
};

const nextActionLabels: Record<InsightNextAction, string> = {
  publish: '发布',
  collect_evidence: '补充证据',
  rerun: '重跑',
  review: '审核',
  user_action: '用户操作',
  none: '无',
};

export function authorityLabel(state: InsightAuthorityState): string {
  return authorityLabels[state];
}

export function uncertaintyLabel(state: InsightUncertaintyState): string {
  return uncertaintyLabels[state];
}

export function nextActionLabel(action: InsightNextAction): string {
  return nextActionLabels[action];
}

/** Human-oriented display contract; never re-interprets the BFF enum value. */
export function insightCardDisplay(card: InsightCardContract) {
  return {
    authority: authorityLabel(card.authority_state),
    uncertainty: uncertaintyLabel(card.uncertainty_state),
    nextAction: nextActionLabel(card.next_action),
    reviewed:
      card.authority_state !== 'candidate' || card.human_decision !== null,
  };
}

const temporalReasonLabels: Record<string, string> = {
  temporal_coverage_low: '发布时间覆盖不足',
  source_lag_profile_insufficient: '来源采集时滞样本不足',
  temporal_state_indeterminate: '时效状态不确定',
  temporal_anomaly_detected: '检测到异常时间戳',
  high_stale_evidence_ratio: '过期证据占比高',
  all_clusters_stale: '独立簇全部过期',
};

export function temporalReasonLabel(reason: string): string | null {
  return temporalReasonLabels[reason] ?? null;
}

export type InsightTemporalDisplay = {
  present: boolean;
  referenceDate: string | null;
  publishTimeCoverage: number | null;
  medianMarketAgeDays: number | null;
  p90MarketAgeDays: number | null;
  staleEvidenceRatio: number | null;
  freshnessAdjustedNeff: number | null;
  sourceRows: Array<{
    sourceId: string;
    validSampleCount: number;
    medianDelayDays: number | null;
    p90DelayDays: number | null;
    pipelineObservationCount: number;
  }>;
  reasonLabels: string[];
};

/**
 * Structured display model for the temporal evidence block. Always returns a
 * stable shape; rendering decides visibility from `present`. Never renders
 * model-generated prose — only the structured metrics of the contract.
 */
export function insightTemporalEvidence(
  card: InsightCardContract,
): InsightTemporalDisplay {
  const temporal = card.temporal_evidence;
  if (!temporal) {
    return {
      present: false,
      referenceDate: null,
      publishTimeCoverage: null,
      medianMarketAgeDays: null,
      p90MarketAgeDays: null,
      staleEvidenceRatio: null,
      freshnessAdjustedNeff: null,
      sourceRows: [],
      reasonLabels: [],
    };
  }
  return {
    present: true,
    referenceDate: temporal.reference_date,
    publishTimeCoverage: temporal.publish_time_coverage,
    medianMarketAgeDays: temporal.median_market_age_days,
    p90MarketAgeDays: temporal.p90_market_age_days,
    staleEvidenceRatio: temporal.stale_evidence_ratio,
    freshnessAdjustedNeff: temporal.freshness_adjusted_neff,
    sourceRows: temporal.source_lag_summary.map((row) => ({
      sourceId: row.source_id,
      validSampleCount: row.valid_sample_count,
      medianDelayDays: row.median_delay_days,
      p90DelayDays: row.p90_delay_days,
      pipelineObservationCount: row.pipeline_observation_count,
    })),
    reasonLabels: temporal.temporal_reasons
      .map((reason) => temporalReasonLabel(reason))
      .filter((label): label is string => label !== null),
  };
}
