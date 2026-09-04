import {fireEvent,render,screen} from '@testing-library/react';
import {expect,test} from 'vitest';
import {SemanticShadowSection} from './SemanticShadowSection';
import type {EvaluationContract} from '../types';

const evidenceBase={
  source_object_type:'validated_cv_snapshot',
  source_object_id:'snapshot-1',
  source_document_id:'version-1',
  source_fragment_id:'snapshot:1',
  quote:'完成服务稳定性建设',
  start:0,
  end:10,
  alignment:'exact',
  occurrence_index:0,
  version:{validated_cv_snapshot_id:'snapshot-1',source_cv_version_id:'version-1',resume_id:'resume-1',position_id:null,graph_version:null,source_jd_version_id:null,evaluation_id:null},
  result_reference:'validated_cv_snapshot:snapshot-1#evidence:snapshot:1:0-10',
};
const evidence=evidenceBase;
const semanticEvidence={
  query_fragment_id:'position-fragment-1',
  candidate_fragment_id:'cv-fragment-1',
  query_fragment_type:'responsibility',
  candidate_fragment_type:'project_responsibility',
  candidate_source_id:'cv_demo',
  similarity:.91,
  rank:1,
  evidence_ref:evidence,
  position_evidence_ref:{...evidenceBase,source_object_type:'position_profile',source_object_id:'position-1',quote:'负责服务稳定性建设',version:{validated_cv_snapshot_id:null,source_cv_version_id:null,resume_id:null,position_id:'position-1',graph_version:'graph-1',source_jd_version_id:'jd-1',evaluation_id:null},result_reference:'position_profile:position-1#evidence:position:1:0-10'},
  profile_version:'semantic-fragment.v2',
  embedding_model:'BAAI/bge-m3',
  embedding_revision:'5617a9f61b028005a4858fdac845db406aefb181',
  embedding_dimension:1024,
  embedding_normalized:true,
  embedding_normalization:'l2' as const,
  vector_representation:'dense' as const,
  vector_similarity:'cosine' as const,
  text_derivation_version:'semantic-fragment.v1',
  index_revision:'matching_fragments_bge_m3_5617a9f_v1',
  collection:'matching_fragments_bge_m3_5617a9f_v1',
  retrieval_trace_id:'trace-demo',
};

const evaluation=(updates:Partial<EvaluationContract>={}):EvaluationContract=>({
  evaluation_id:'EVAL_1',
  cv_profile_id:null,
  cv_profile_version:null,
  position_profile_id:null,
  position_profile_version:null,
  algorithm_version:'matching.v1',
  evaluation_status:'completed',
  hard_constraint_results:[],
  skill_results:[],
  responsibility_results:[],
  project_results:[],
  scenario_results:[],
  summary:null,
  final_match_result:null,
  ...updates,
});

test('available with candidates shows Evidence and lineage without formal score text',()=>{
  render(<SemanticShadowSection evaluation={evaluation({
    semantic_shadow_status:'available',
    semantic_embedding_model:'BAAI/bge-m3',
    semantic_embedding_revision:semanticEvidence.embedding_revision,
    semantic_embedding_dimension:1024,
    semantic_candidates:[{candidate_source_id:'cv_demo',score:.91,evidence:[semanticEvidence]}],
    semantic_latency_ms:18.4,
    semantic_index_revision:semanticEvidence.index_revision,
  })}/>);
  expect(screen.getByText('证据语义候选召回')).toBeInTheDocument();
  expect(screen.getByText('该区域展示语义召回的补充候选。')).toBeInTheDocument();
  expect(screen.getByText('候选数量')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:/相似度 0.9100/}));
  expect(screen.getByText('模型 多语言语义模型（BGE-M3）')).toBeInTheDocument();
  expect(screen.getByText('追踪记录已保存')).toBeInTheDocument();
  expect(screen.queryByText('trace-demo')).not.toBeInTheDocument();
  expect(screen.queryByText('语义加权分')).not.toBeInTheDocument();
});

test('available without candidates shows the explicit empty state',()=>{
  render(<SemanticShadowSection evaluation={evaluation({semantic_shadow_status:'available'})}/>);
  expect(screen.getByText('本次语义召回已完成，未发现额外证据候选。')).toBeInTheDocument();
});

test('unavailable sanitizes the error code and keeps the rule notice',()=>{
  render(<SemanticShadowSection evaluation={evaluation({
    semantic_shadow_status:'unavailable',
    semantic_error_code:'embedding-timeout;drop',
  })}/>);
  expect(screen.getByText('语义召回不可用')).toBeInTheDocument();
  expect(screen.getByText('语义召回异常（内部原因已记录）')).toBeInTheDocument();
  expect(screen.queryByText(/embedding-timeout/)).not.toBeInTheDocument();
  expect(screen.getByText('规则结果、正式总分和推荐等级仍来自规则评估。')).toBeInTheDocument();
});

test('disabled shows the disabled state',()=>{
  render(<SemanticShadowSection evaluation={evaluation({semantic_shadow_status:'disabled'})}/>);
  expect(screen.getByText('本次评估未启用证据语义候选召回。')).toBeInTheDocument();
});
