import {expect,test} from 'vitest';
import {buildDirectSkillGraphData,buildReferenceGraphData} from './graphTransform';
import type {Relation,SkillClassification} from './shared/api';

const classification=(facet:SkillClassification['facet'],code:string,name_zh:string):SkillClassification=>({facet,code,name_zh,is_primary:true});
const relation=(skill_id:string,classifications:SkillClassification[]):Relation=>({relation_id:1,skill_id,canonical_name:skill_id==='PY'?'Python':skill_id==='JS'?'JavaScript':'需求分析',category_code:null,classifications,weight:.8,confidence:.9,importance_level:'core',primary_modality:'required',modality_distribution:{required:1},trend_score:null,metrics:{support_document_count:2,support_count:2,trusted_evidence_ratio:1,unknown_ratio:0}});

test('graph_transform_builds_concept_and_technology_kind_layers',()=>{
  const graph=buildReferenceGraphData('POSITION','后端工程师',[
    relation('PY',[classification('concept_class','technology','技术栈'),classification('technology_kind','language','编程语言')]),
    relation('JS',[classification('concept_class','technology','技术栈'),classification('technology_kind','language','编程语言')]),
    relation('ANALYSIS',[classification('concept_class','practice','方法与实践')]),
  ]);
  const byId=new Map(graph.nodes.map(node=>[node.id,node]));
  expect(byId.get('POSITION')).toMatchObject({label:'后端工程师',nodeKind:'position'});
  expect(byId.get('__concept:technology')).toMatchObject({label:'技术栈',nodeKind:'classification'});
  expect(byId.get('__technology-kind:language')).toMatchObject({label:'编程语言',nodeKind:'classification'});
  expect(byId.get('__concept:practice')).toMatchObject({label:'方法与实践',nodeKind:'classification'});
  expect(byId.get('PY')).toMatchObject({label:'Python',routePath:'PY',nodeKind:'skill'});
  expect(byId.get('ANALYSIS')).toMatchObject({label:'需求分析',routePath:'ANALYSIS',nodeKind:'skill'});
});

test('graph_transform_connects_the_reference_graph_by_levels_and_keeps_a_direct_view',()=>{
  const relations=[
    relation('PY',[classification('concept_class','technology','技术栈'),classification('technology_kind','language','编程语言')]),
    relation('JS',[classification('concept_class','technology','技术栈'),classification('technology_kind','language','编程语言')]),
  ];
  const graph=buildReferenceGraphData('POSITION','后端工程师',relations);
  expect(graph.links).toEqual(expect.arrayContaining([
    {source:'POSITION',target:'__concept:technology',level:1},
    {source:'__concept:technology',target:'__technology-kind:language',level:2},
    {source:'__technology-kind:language',target:'PY',level:3},
    {source:'__technology-kind:language',target:'JS',level:3},
  ]));

  const direct=buildDirectSkillGraphData('POSITION','后端工程师',relations);
  expect(direct.links).toEqual([
    {source:'POSITION',target:'PY',level:1},
    {source:'POSITION',target:'JS',level:1},
  ]);
});
