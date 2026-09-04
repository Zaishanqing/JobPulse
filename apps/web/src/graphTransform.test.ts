import {expect,test} from 'vitest';

import {
  buildDirectSkillGraphData,
  buildExpandedReferenceGraphLayer,
  buildExpandedReferenceGraphPath,
  buildReferenceGraphData,
  buildReferenceGraphLayer,
} from './graphTransform';
import type {Relation} from './shared/api';

const relation=(skillId:string,importanceLevel:string,concept='knowledge',technologyKind?:string)=>({
  relation_id:Number(skillId.slice(-1)),
  skill_id:skillId,
  canonical_name:`技能 ${skillId}`,
  category_code:'TECH',
  weight:.8,
  confidence:.9,
  importance_level:importanceLevel,
  primary_modality:'required',
  modality_distribution:{required:1,preferred:0,bonus:0,unknown:0},
  trend_score:null,
  metrics:{support_document_count:1,support_count:1,trusted_evidence_ratio:1,unknown_ratio:0},
  classifications:[
    {facet:'concept_class',code:concept,name_zh:concept==='technology'?'技术实体':'知识概念',is_primary:true},
    ...(technologyKind?[{facet:'technology_kind',code:technologyKind,name_zh:'框架',is_primary:true}]:[]),
    {facet:'domain',code:'ai_intelligent_systems',name_zh:'人工智能与智能系统',is_primary:true},
  ],
} as Relation);

test('岗位关系图把技能重要程度传递给画布节点',()=>{
  const graph=buildReferenceGraphData('POS','测试岗位',[
    relation('SKILL_1','core'),
    relation('SKILL_2','important'),
    relation('SKILL_3','supplementary'),
  ]);

  expect(graph.nodes.filter(node=>node.nodeKind==='skill').map(node=>node.importanceLevel)).toEqual([
    'core',
    'important',
    'supplementary',
  ]);
});

test('多级关系图每次只投影当前父节点及其直接子节点',()=>{
  const graph=buildReferenceGraphData('POS','测试岗位',[
    relation('KNOWLEDGE_SKILL','important'),
    relation('FRAMEWORK_SKILL','core','technology','framework'),
  ]);

  expect(buildReferenceGraphLayer(graph,'POS').nodes.map(node=>node.id)).toEqual([
    'POS','__concept:knowledge','__concept:technology',
  ]);
  expect(buildReferenceGraphLayer(graph,'__concept:technology').nodes.map(node=>node.id)).toEqual([
    '__concept:technology','__technology-kind:framework',
  ]);
  expect(buildReferenceGraphLayer(graph,'__technology-kind:framework').nodes.map(node=>node.id)).toEqual([
    '__technology-kind:framework','FRAMEWORK_SKILL',
  ]);
  expect(buildReferenceGraphLayer(graph,'__concept:knowledge').nodes.map(node=>node.id)).toEqual([
    '__concept:knowledge','KNOWLEDGE_SKILL',
  ]);
});

test('技能全景视图由岗位直接连接全部技能',()=>{
  const graph=buildDirectSkillGraphData('POS','测试岗位',[
    relation('KNOWLEDGE_SKILL','important'),
    relation('FRAMEWORK_SKILL','core','technology','framework'),
  ]);

  expect(graph.nodes.map(node=>node.id)).toEqual(['POS','KNOWLEDGE_SKILL','FRAMEWORK_SKILL']);
  expect(graph.links).toEqual([
    {source:'POS',target:'KNOWLEDGE_SKILL',level:1},
    {source:'POS',target:'FRAMEWORK_SKILL',level:1},
  ]);
});

test('逐层探索悬停一级分类时保留同级节点并展开下一层',()=>{
  const graph=buildReferenceGraphData('POS','测试岗位',[
    relation('KNOWLEDGE_SKILL','important'),
    relation('FRAMEWORK_SKILL','core','technology','framework'),
  ]);
  const preview=buildExpandedReferenceGraphLayer(graph,'POS','__concept:technology');

  expect(preview.showAll).toBe(true);
  expect(preview.nodes.map(node=>node.id)).toEqual([
    'POS','__concept:knowledge','__concept:technology','__technology-kind:framework',
  ]);
  expect(preview.links).toEqual([
    {source:'POS',target:'__concept:knowledge',level:1},
    {source:'POS',target:'__concept:technology',level:1},
    {source:'__concept:technology',target:'__technology-kind:framework',level:2},
  ]);
});

test('逐层探索允许沿技术分类连续悬停展开到技能叶节点',()=>{
  const graph=buildReferenceGraphData('POS','测试岗位',[
    relation('KNOWLEDGE_SKILL','important'),
    relation('FRAMEWORK_SKILL','core','technology','framework'),
  ]);
  const preview=buildExpandedReferenceGraphPath(graph,'POS',[
    '__concept:technology','__technology-kind:framework',
  ]);

  expect(preview.nodes.map(node=>node.id)).toContain('FRAMEWORK_SKILL');
  expect(preview.links).toContainEqual({
    source:'__technology-kind:framework',target:'FRAMEWORK_SKILL',level:3,
  });
});
