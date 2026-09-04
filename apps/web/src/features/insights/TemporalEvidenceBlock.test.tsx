import {cleanup, render, screen} from '@testing-library/react';
import {afterEach, describe, expect, it} from 'vitest';
import {insightTemporalEvidence} from './contract';
import {TemporalEvidenceBlock} from './TemporalEvidenceBlock';
import type {InsightCardContract} from './types';

afterEach(cleanup);

const baseCard = {
  contract_version: 'insight-card.v1',
  temporal_evidence: null,
  insight_id: 'i-1',
  claim_type: 'emerging_position',
  subject_ref: 'BACKEND_ENGINEER',
  claim: 'claim',
  authority_state: 'candidate',
  evidence_refs: [],
  counter_evidence_refs: [],
  used_evidence_ids: [],
  effective_sample_size: 210.7,
  raw_evidence_count: 233,
  uncertainty_state: 'source_concentrated',
  uncertainty_reasons: [],
  sensitivity_results: [],
  fragile_factor: null,
  data_refs: [],
  release_refs: [],
  graph_version_refs: [],
  catalog_refs: [],
  algorithm_version: 'algo.v1',
  algorithm_config_version: null,
  algorithm_config_hash: null,
  evidence_algorithm_version: 'evidence-independence.v3.2',
  evidence_config_hash: 'hash',
  evidence_subject_ref: null,
  coverage_status: 'unknown',
  coverage_summary: [],
  source_coverage: null,
  human_decision: null,
  limitations: [],
  next_action: 'review',
} as InsightCardContract;

describe('insightTemporalEvidence selector', () => {
  it('returns present=false when temporal_evidence is null', () => {
    const display = insightTemporalEvidence(baseCard);
    expect(display.present).toBe(false);
    expect(display.sourceRows).toEqual([]);
  });

  it('maps structured metrics and reason labels when present', () => {
    const card: InsightCardContract = {
      ...baseCard,
      temporal_evidence: {
        reference_date: '2026-08-12',
        publish_time_coverage: 0.086,
        median_market_age_days: 27,
        p90_market_age_days: 27,
        stale_evidence_ratio: 0.0172,
        freshness_adjusted_neff: 210.7,
        temporal_algorithm_version: 'temporal-freshness.v1',
        temporal_reasons: ['temporal_coverage_low', 'source_lag_profile_insufficient'],
        fresh_evidence_count: 336,
        stale_evidence_count: 29,
        unknown_evidence_count: 0,
        time_provenance_policy: 'time-provenance.v2',
        source_lag_summary: [
          {
            source_id: 'boss_zhipin',
            valid_sample_count: 0,
            median_delay_days: null,
            p90_delay_days: null,
            pipeline_observation_count: 268,
            unknown_provenance_count: 0,
            missing_publish_count: 0,
            invalid_sample_count: 0,
          },
        ],
      },
    };
    const display = insightTemporalEvidence(card);
    expect(display.present).toBe(true);
    expect(display.publishTimeCoverage).toBe(0.086);
    expect(display.medianMarketAgeDays).toBe(27);
    expect(display.freshnessAdjustedNeff).toBe(210.7);
    expect(display.reasonLabels).toEqual(['发布时间覆盖不足', '来源采集时滞样本不足']);
    expect(display.sourceRows[0].sourceId).toBe('boss_zhipin');
  });
});

describe('TemporalEvidenceBlock', () => {
  it('renders null without temporal_evidence', () => {
    const {container} = render(<TemporalEvidenceBlock card={baseCard} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the compact structured block when present', () => {
    const card: InsightCardContract = {
      ...baseCard,
      temporal_evidence: {
        reference_date: '2026-08-12',
        publish_time_coverage: 0.086,
        median_market_age_days: 27,
        p90_market_age_days: 27,
        stale_evidence_ratio: 0.0172,
        freshness_adjusted_neff: 210.7,
        temporal_algorithm_version: 'temporal-freshness.v1',
        temporal_reasons: ['temporal_coverage_low'],
        fresh_evidence_count: 336,
        stale_evidence_count: 29,
        unknown_evidence_count: 0,
        time_provenance_policy: 'time-provenance.v2',
        source_lag_summary: [
          {
            source_id: 'boss_zhipin',
            valid_sample_count: 0,
            median_delay_days: null,
            p90_delay_days: null,
            pipeline_observation_count: 268,
            unknown_provenance_count: 0,
            missing_publish_count: 0,
            invalid_sample_count: 0,
          },
        ],
      },
    };
    render(<TemporalEvidenceBlock card={card} />);
    expect(screen.getByRole('region', {name: '数据时效'})).toBeTruthy();
    expect(screen.getByText('8.6%')).toBeTruthy();
    expect(screen.getByText(/27 天 \/ 27 天/)).toBeTruthy();
    expect(screen.getByText(/210.7/)).toBeTruthy();
    expect(screen.getByText(/boss_zhipin/)).toBeTruthy();
    expect(screen.getByText('发布时间覆盖不足')).toBeTruthy();
  });
});
