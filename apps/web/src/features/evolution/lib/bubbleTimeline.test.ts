import {expect,test} from 'vitest';
import type {GraphSnapshot,Relation} from '../../../shared/api';
import type {CapabilityEvolution} from '../types';
import {buildBubbleTimeline,quarterLabel} from './bubbleTimeline';

const relation=(id:string,name:string,weight:number):Relation=>({
  relation_id:1,skill_id:id,canonical_name:name,category_code:'AI',category_name:'人工智能',weight,confidence:.9,
  importance_level:'core',primary_modality:'required',modality_distribution:{required:1},trend_score:null,
  metrics:{support_document_count:4,support_count:4,trusted_evidence_ratio:1,unknown_ratio:0},
});

const snapshot=(relations:Relation[]):GraphSnapshot=>({
  position_id:'POS_AI',position:{position_id:'POS_AI',name:'人工智能工程师',category_code:'AI'},skill_relations:relations,
  requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:4},
});

const evolution=(frames:Array<{id:number;end:string;relations:Relation[]}>):CapabilityEvolution=>({
  schema_version:'capability-evolution.v1',position_id:'POS_AI',comparisons:[],events:[],comparison_count:0,event_count:0,
  frame_count:frames.length,frames:frames.map(item=>({
    id:item.id,version_number:item.id,version_name:`版本 ${item.id}`,build_run_id:item.id,release_id:null,
    rollback_from_version_id:null,created_at:item.end,dependencies:{source_time_window:{start:'2026-07-27',end:item.end}},snapshot:snapshot(item.relations),
  })),
});

test('按图谱来源时间升序构建快照并保持稳定技能 ID',()=>{
  const timeline=buildBubbleTimeline(evolution([
    {id:3,end:'2026-08-07',relations:[relation('pytorch','PyTorch',.82),relation('agent','智能体工程',.4)]},
    {id:2,end:'2026-08-01',relations:[relation('pytorch','PyTorch',.45)]},
  ]));
  expect(timeline.frames.map(frame=>frame.label)).toEqual(['2026 第 3 季度','2026 第 3 季度']);
  expect(timeline.frames[0].skills[0]).toMatchObject({id:'pytorch',value:45,delta:0,comparable:true});
  expect(timeline.frames[1].skills.find(item=>item.id==='pytorch')).toMatchObject({value:82,comparable:true});
  expect(timeline.frames[1].skills.find(item=>item.id==='agent')).toMatchObject({isNew:true,delta:0,comparable:false});
  expect(timeline.skillIds).toEqual(['pytorch']);
});

test('动态支持门槛排除弱证据变化并且不把首次出现算作百分比增长',()=>{
  const weak=relation('weak','弱证据技能',.3);weak.metrics.support_document_count=1;weak.metrics.support_count=1;
  const weakChanged=relation('weak','弱证据技能',.6);weakChanged.metrics.support_document_count=1;weakChanged.metrics.support_count=1;
  const timeline=buildBubbleTimeline(evolution([
    {id:1,end:'2026-07-27',relations:[relation('pytorch','PyTorch',.4),weak]},
    {id:2,end:'2026-08-07',relations:[relation('pytorch','PyTorch',.8),weakChanged,relation('new','新技能',.9)]},
  ]));
  expect(timeline.frames[1].supportThreshold).toBe(2);
  expect(timeline.frames[1].skills.find(item=>item.id==='weak')?.comparable).toBe(false);
  expect(timeline.frames[1].skills.find(item=>item.id==='new')).toMatchObject({delta:0,comparable:false});
  expect(timeline.skillIds).toEqual(['pytorch']);
});

test('岗位内按支持度稳定排序且最多展示三十项变化能力',()=>{
  const before=Array.from({length:35},(_,index)=>relation(`skill-${index}`,`技能 ${index}`,.4));
  const after=Array.from({length:35},(_,index)=>relation(`skill-${index}`,`技能 ${index}`,.5+index/1000));
  const timeline=buildBubbleTimeline(evolution([
    {id:1,end:'2026-07-27',relations:before},
    {id:2,end:'2026-08-07',relations:after},
  ]));
  expect(timeline.frames[0].skills.filter(skill=>skill.comparable)).toHaveLength(30);
  expect(timeline.frames[1].skills.filter(skill=>skill.comparable)).toHaveLength(30);
  expect(timeline.skillIds).toHaveLength(30);
});

test('季度标签对无效日期使用明确回退',()=>{
  expect(quarterLabel('2026-09-30','回退')).toBe('2026 第 3 季度');
  expect(quarterLabel('bad-date','回退')).toBe('回退');
});
