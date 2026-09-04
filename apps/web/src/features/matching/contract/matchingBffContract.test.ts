import {describe,expect,it} from 'vitest';
import rawFixture from './matching-bff-contract.fixture.json';
import rawSnapshot from './matching-bff-contract.snapshot.json';
import type {FinalMatchResult,WhatIfResult} from '../types';

type SnapshotField={json_name:string;required:boolean;nullable:boolean};
type ModelSnapshot={fields:Record<string,SnapshotField>};
const snapshot=rawSnapshot as {
  schema_version:string;
  models:{FinalMatchResult:ModelSnapshot;WhatIfResult:ModelSnapshot};
};

const finalMatchFields=[
  'algorithm_version',
  'cv_derivation_version',
  'cv_profile_id',
  'cv_taxonomy_version',
  'dimension_scores',
  'embedding_model',
  'embedding_version',
  'explanation',
  'gaps',
  'hard_gate_status',
  'input_evaluation_algorithm_version',
  'match_confidence',
  'overall_score',
  'position_graph_version',
  'position_profile_id',
  'position_quality_snapshot_id',
  'position_taxonomy_version',
  'position_trend_version',
  'recommendation_level',
  'score_contributions',
  'scoring_config_version',
  'semantic_algorithm_version',
  'semantic_collection',
  'semantic_embedding_dimension',
  'semantic_embedding_normalization',
  'semantic_embedding_normalized',
  'semantic_index_revision',
  'semantic_text_derivation_version',
  'semantic_threshold_config_version',
  'semantic_vector_representation',
  'semantic_vector_similarity',
  'semantic_weight',
  'source_evaluation_id',
  'strengths',
  'uncertain_items',
  'vector_text_derivation_version',
] as const satisfies readonly (keyof FinalMatchResult)[];

const whatIfFields=[
  'actions',
  'algorithm_version',
  'baseline_confidence',
  'baseline_evaluation',
  'baseline_evaluation_id',
  'baseline_hard_gate_status',
  'baseline_recommendation',
  'baseline_score',
  'confidence_delta',
  'current_verified_outcome',
  'denominator_changed',
  'dimension_deltas',
  'error_code',
  'error_message',
  'generation_status',
  'hypothetical',
  'modeled_confidence_delta',
  'modeled_final_score',
  'modeled_score_delta',
  'observed_outcome',
  'outcome_semantics',
  'position_graph_version',
  'projected_actions',
  'projected_confidence',
  'projected_evaluation',
  'projected_hard_gate_status',
  'projected_if_completed',
  'projected_if_completed_outcome',
  'projected_recommendation',
  'projected_score',
  'projected_score_delta',
  'scenario_confidence',
  'scenario_evaluation',
  'scenario_hard_gate_status',
  'scenario_id',
  'scenario_recommendation',
  'scenario_score',
  'score_delta',
  'score_effect_status',
  'scoring_algorithm_version',
  'scoring_config_version',
  'target_type',
  'use_enterprise_weights',
] as const satisfies readonly (keyof WhatIfResult)[];

type OptionalKeys<T>={
  [K in keyof T]-?:Record<string,never> extends Pick<T,K>?K:never
}[keyof T];
type Equal<A,B>=
  (<T>()=>T extends A?1:2) extends (<T>()=>T extends B?1:2)
    ?(<T>()=>T extends B?1:2) extends (<T>()=>T extends A?1:2)?true:false
    :false;

const finalRequiredFields=[
  'algorithm_version',
  'cv_derivation_version',
  'cv_taxonomy_version',
  'explanation',
  'hard_gate_status',
  'input_evaluation_algorithm_version',
  'match_confidence',
  'position_graph_version',
  'position_quality_snapshot_id',
  'position_taxonomy_version',
  'recommendation_level',
  'scoring_config_version',
] as const;
const finalOptionalFields=[
  'cv_profile_id',
  'dimension_scores',
  'embedding_model',
  'embedding_version',
  'gaps',
  'overall_score',
  'position_profile_id',
  'position_trend_version',
  'score_contributions',
  'semantic_algorithm_version',
  'semantic_collection',
  'semantic_embedding_dimension',
  'semantic_embedding_normalization',
  'semantic_embedding_normalized',
  'semantic_index_revision',
  'semantic_text_derivation_version',
  'semantic_threshold_config_version',
  'semantic_vector_representation',
  'semantic_vector_similarity',
  'semantic_weight',
  'source_evaluation_id',
  'strengths',
  'uncertain_items',
  'vector_text_derivation_version',
] as const;
const whatIfRequiredFields=['algorithm_version','generation_status','scenario_id'] as const;
const whatIfOptionalFields=[
  'actions',
  'baseline_confidence',
  'baseline_evaluation',
  'baseline_evaluation_id',
  'baseline_hard_gate_status',
  'baseline_recommendation',
  'baseline_score',
  'confidence_delta',
  'current_verified_outcome',
  'denominator_changed',
  'dimension_deltas',
  'error_code',
  'error_message',
  'hypothetical',
  'modeled_confidence_delta',
  'modeled_final_score',
  'modeled_score_delta',
  'observed_outcome',
  'outcome_semantics',
  'position_graph_version',
  'projected_actions',
  'projected_confidence',
  'projected_evaluation',
  'projected_hard_gate_status',
  'projected_if_completed',
  'projected_if_completed_outcome',
  'projected_recommendation',
  'projected_score',
  'projected_score_delta',
  'scenario_confidence',
  'scenario_evaluation',
  'scenario_hard_gate_status',
  'scenario_recommendation',
  'scenario_score',
  'score_delta',
  'score_effect_status',
  'scoring_algorithm_version',
  'scoring_config_version',
  'target_type',
  'use_enterprise_weights',
] as const;
const finalKeysExact:Equal<keyof FinalMatchResult,typeof finalMatchFields[number]>=true;
const finalOptionalityExact:Equal<OptionalKeys<FinalMatchResult>,typeof finalOptionalFields[number]>=true;
const whatIfKeysExact:Equal<keyof WhatIfResult,typeof whatIfFields[number]>=true;
const whatIfOptionalityExact:Equal<OptionalKeys<WhatIfResult>,typeof whatIfOptionalFields[number]>=true;

describe('Matching BFF to TypeScript contract',()=>{
  it('freezes the exact FinalMatchResult and WhatIfResult field names',()=>{
    expect([finalKeysExact,finalOptionalityExact,whatIfKeysExact,whatIfOptionalityExact]).toEqual([true,true,true,true]);
    expect(snapshot.schema_version).toBe('matching-bff-ts-contract-snapshot.v1');
    expect(Object.keys(snapshot.models.FinalMatchResult.fields).sort()).toEqual([...finalMatchFields].sort());
    expect(Object.keys(snapshot.models.WhatIfResult.fields).sort()).toEqual([...whatIfFields].sort());
    for(const name of finalMatchFields)expect(snapshot.models.FinalMatchResult.fields[name].json_name).toBe(name);
    for(const name of whatIfFields)expect(snapshot.models.WhatIfResult.fields[name].json_name).toBe(name);
  });

  it('keeps the generated fixture exhaustive and explicitly non-formal',()=>{
    expect(rawFixture.fixture_scope).toBe('contract_regression_only_not_formal_result');
    expect(Object.keys(rawFixture.final_match_result).sort()).toEqual([...finalMatchFields].sort());
    expect(Object.keys(rawFixture.what_if_result).sort()).toEqual([...whatIfFields].sort());
    expect(rawFixture.what_if_result.generation_status).toBe('rejected');
    expect(rawFixture.what_if_result.error_code).toBe('CONTRACT_FIXTURE_ONLY');
  });

  it('freezes identity nullability and requiredness used by Competition C',()=>{
    const finalFields=snapshot.models.FinalMatchResult.fields;
    expect(Object.entries(finalFields).filter(([,field])=>field.required).map(([name])=>name).sort()).toEqual([...finalRequiredFields].sort());
    expect(Object.entries(finalFields).filter(([,field])=>!field.required).map(([name])=>name).sort()).toEqual([...finalOptionalFields].sort());
    expect(finalFields.algorithm_version.required).toBe(true);
    expect(finalFields.position_quality_snapshot_id.required).toBe(true);
    expect(finalFields.cv_profile_id.nullable).toBe(true);
    const scenarioFields=snapshot.models.WhatIfResult.fields;
    expect(Object.entries(scenarioFields).filter(([,field])=>field.required).map(([name])=>name).sort()).toEqual([...whatIfRequiredFields].sort());
    expect(Object.entries(scenarioFields).filter(([,field])=>!field.required).map(([name])=>name).sort()).toEqual([...whatIfOptionalFields].sort());
    expect(scenarioFields.scenario_id.required).toBe(true);
    expect(scenarioFields.baseline_score.nullable).toBe(true);
    expect(scenarioFields.hypothetical.required).toBe(false);
  });
});
