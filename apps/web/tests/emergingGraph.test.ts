import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {expect,test} from 'vitest';
import {buildReferenceGraphData} from '../src/graphTransform';
import type {EmergingPosition} from '../src/features/emerging/api';
import {buildEmergingGraph} from '../src/features/emerging/lib/emergingGraph';

const position=(values:Partial<EmergingPosition>={}):EmergingPosition=>({
  emerging_id:'EM_1',cluster_id:'CL_1',position_name:'智能体工程师',core_responsibilities:[],
  required_skills:[],bonus_skills:[],industry_scenarios:[],germination_score:null,
  score_dimensions:{},evidence_jd_ids:['企业名','playwright','167276'],status:'published',...values,
});
const evidence={source_jd_id:'formal-bundle:167276',original_text_snippet:'使用 Python 构建智能体',data_source:'playwright'};

test('未映射技能合并字段证据与重复节点，不把展示引用或重复片段算成 JD',()=>{
  const graph=buildEmergingGraph(position({
    required_skills:[{raw_skill:'Python'},{raw_skill:'SQL'}],
    bonus_skills:[{raw_skill:'python',evidence:[evidence]},{raw_skill:'Rust'}],
    field_evidence:{required_skills:{items:[{content:'Python',evidence:[evidence,evidence]}]}},
  }));
  expect(graph.skills.map(item=>item.canonical_name)).toEqual(['Python','SQL','Rust']);
  expect(graph.skills[0].requirement).toBe('required');
  expect(graph.skills[0].evidence).toHaveLength(1);
  expect(graph.skills[0].supportJdCount).toBe(1);
  expect(graph.skills[1].supportJdCount).toBeNull();
  expect(graph.skills[0]).not.toHaveProperty('confidence');
  expect(buildReferenceGraphData(graph.positionId,graph.name,graph.skills).nodes.filter(item=>item.nodeKind==='skill')).toHaveLength(3);
});

test('仅有字段定义也能显示，空数据不会生成虚构技能',()=>{
  const graph=buildEmergingGraph(position({field_evidence:{required_skills:{content:['Python']},core_responsibilities:{items:[{content:'交付智能体',evidence:[evidence]}]}}}));
  expect(graph.skills[0].canonical_name).toBe('Python');
  expect(graph.responsibilities[0].text).toBe('交付智能体');
  expect(graph.responsibilities[0].evidence).toHaveLength(1);
  expect(buildEmergingGraph(position()).skills).toEqual([]);
});

test('现有 10 个正式实验岗位均可直接投影为交互图谱',()=>{
  const data=JSON.parse(readFileSync(resolve(process.cwd(),'../api/data/emerging-discovery/exp-emerge-01-crosswindow-v3.2-20260823.clusters.json'),'utf8'));
  const clusters=data.clusters.filter((item:{state:string})=>item.state==='emerging');
  expect(clusters).toHaveLength(10);
  for(const cluster of clusters){
    const graph=buildEmergingGraph(position({...cluster.definition,emerging_id:cluster.cluster_key,evidence_jd_ids:cluster.evidence_refs}));
    expect(graph.skills.length,cluster.canonical_title).toBeGreaterThan(0);
    expect(graph.responsibilities.length,cluster.canonical_title).toBeGreaterThan(0);
    expect(graph.skills.some(item=>item.evidence.length>0),cluster.canonical_title).toBe(true);
    const visual=buildReferenceGraphData(graph.positionId,graph.name,graph.skills);
    expect(visual.nodes.filter(item=>item.nodeKind==='skill')).toHaveLength(graph.skills.length);
  }
});
