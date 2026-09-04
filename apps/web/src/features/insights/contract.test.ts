import {describe, expect, it} from 'vitest';
import rawSnapshot from './insight-card-contract.snapshot.json';
import {authorityLabel, nextActionLabel, uncertaintyLabel} from './contract';
import type {
  InsightAuthorityState,
  InsightCardContract,
  InsightNextAction,
  InsightUncertaintyState,
} from './types';

const snapshot = rawSnapshot as {
  schema_version: string;
  bff_contract_version: string;
  fields: Record<string, {json_name: string; kind: string; nullable: boolean}>;
  definitions: Record<string, Record<string, unknown>>;
  core_roles: Record<string, string[]>;
};

const expectedFields = [
  'contract_version',
  'insight_id',
  'claim_type',
  'subject_ref',
  'claim',
  'authority_state',
  'evidence_refs',
  'counter_evidence_refs',
  'used_evidence_ids',
  'effective_sample_size',
  'raw_evidence_count',
  'uncertainty_state',
  'uncertainty_reasons',
  'sensitivity_results',
  'fragile_factor',
  'data_refs',
  'release_refs',
  'graph_version_refs',
  'catalog_refs',
  'algorithm_version',
  'algorithm_config_version',
  'algorithm_config_hash',
  'evidence_algorithm_version',
  'evidence_config_hash',
  'evidence_subject_ref',
  'coverage_status',
  'coverage_summary',
  'source_coverage',
  'human_decision',
  'limitations',
  'temporal_evidence',
  'next_action',
] as const satisfies readonly (keyof InsightCardContract)[];

describe('InsightCard contract snapshot', () => {
  it('is the official BFF version', () => {
    expect(snapshot.schema_version).toBe('insight-card-contract-snapshot.v1');
    expect(snapshot.bff_contract_version).toBe('insight-card.v1');
  });

  it('has exactly the BFF field names without rename or drop', () => {
    expect(Object.keys(snapshot.fields).sort()).toEqual(
      [...expectedFields].sort(),
    );
    for (const name of expectedFields) {
      expect(snapshot.fields[name].json_name).toBe(name);
    }
  });

  it('freezes nullability of core fields', () => {
    expect(snapshot.fields.algorithm_version.nullable).toBe(false);
    expect(snapshot.fields.algorithm_config_version.nullable).toBe(true);
    expect(snapshot.fields.algorithm_config_hash.nullable).toBe(true);
    expect(snapshot.fields.uncertainty_state.nullable).toBe(false);
    expect(snapshot.fields.evidence_refs.nullable).toBe(false);
    expect(snapshot.fields.effective_sample_size.nullable).toBe(true);
    expect(snapshot.fields.human_decision.nullable).toBe(true);
    expect(snapshot.fields.coverage_status.nullable).toBe(true);
  });

  it('keeps nested EvidenceRef, HumanDecision and temporal definitions', () => {
    expect(Object.keys(snapshot.definitions).sort()).toEqual([
      'EvidenceRef',
      'HumanDecision',
      'SensitivityResult',
      'TemporalEvidenceSummary',
      'TemporalSourceLagRow',
    ]);
    const evidenceRef = snapshot.definitions.EvidenceRef as Record<
      string,
      {json_name: string; nullable: boolean; required: boolean}
    >;
    expect(evidenceRef.evidence_id.required).toBe(true);
    expect(evidenceRef.quote.nullable).toBe(true);
    expect(evidenceRef.location_start.nullable).toBe(true);
    expect(evidenceRef.location_end.nullable).toBe(true);
    expect(evidenceRef.source_version.nullable).toBe(false);
    const lagRow = snapshot.definitions.TemporalSourceLagRow as Record<
      string,
      {json_name: string; nullable: boolean; required: boolean}
    >;
    expect(lagRow.valid_sample_count.required).toBe(true);
    expect(lagRow.pipeline_observation_count.nullable).toBe(false);
  });

  it('keeps review-state and result-status roles wired to the snapshot', () => {
    expect(snapshot.core_roles.review_state).toEqual([
      'authority_state',
      'human_decision',
    ]);
    expect(snapshot.core_roles.result_status).toEqual([
      'next_action',
      'coverage_status',
      'limitations',
    ]);
    expect(snapshot.core_roles.algorithm_config_version).toContain(
      'algorithm_version',
    );
    expect(snapshot.core_roles.evidence_refs).toContain('used_evidence_ids');
  });

  it('interprets contract enums through exhaustive labels only', () => {
    const authorities: InsightAuthorityState[] = [
      'candidate',
      'reviewed',
      'authoritative',
    ];
    const uncertainties: InsightUncertaintyState[] = [
      'ok',
      'not_observed',
      'unresolved',
      'insufficient_evidence',
      'source_concentrated',
      'stale_observation',
      'blocked',
    ];
    const actions: InsightNextAction[] = [
      'publish',
      'collect_evidence',
      'rerun',
      'review',
      'user_action',
      'none',
    ];
    for (const value of authorities) expect(authorityLabel(value)).toBeTruthy();
    for (const value of uncertainties) {
      expect(uncertaintyLabel(value)).toBeTruthy();
    }
    for (const value of actions) expect(nextActionLabel(value)).toBeTruthy();
  });
});
