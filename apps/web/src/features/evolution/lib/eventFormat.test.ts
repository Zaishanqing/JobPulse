import {expect,test} from 'vitest';
import type {EvolutionEvent,EvolutionGraphVersion,EvolutionVersionPair} from '../types';
import {
  dateLabel,eventChangeSummary,eventGroup,eventSubject,eventTypeLabel,filterEvents,
  isStrongEvent,monthLabel,needsCaution,overviewStats,percent,sortEvents,versionNumberById,
} from './eventFormat';

const relation=(skillId:string,name:string,weight:number)=>({
  skill_id:skillId,canonical_name:name,category_code:'ML',weight,confidence:.85,
  importance_level:'core',primary_modality:'required',
  statistics:{support_document_count:4,source_diversity:2,enterprise_coverage:1},
});

const event=(partial:Partial<EvolutionEvent>):EvolutionEvent=>({
  event_id:'evt-1-2-skill_emergence-001',
  event_type:'skill_emergence',
  position_id:'POS_AI',
  from_version:1,
  to_version:2,
  source_entities:[],
  target_entities:[relation('PY','Python',.55)],
  confidence:.91,
  magnitude:.74,
  evidence:{
    lineage:{position_id:'POS_AI',from_version_id:1,to_version_id:2},
    source_relations:[],
    target_relations:[relation('PY','Python',.55)],
    source:'graph_version_snapshot_diff',
  },
  reason:'skill Python (PY) emerged: weight 0.4 -> 0.55',
  detector_version:'position-evolution-events-v1',
  created_at:'2026-05-12T00:00:00Z',
  metrics:{before_weight:.4,after_weight:.55,delta:.15},
  metadata:{atomic_signals:['skill_emergence']},
  ...partial,
});

const versions:EvolutionGraphVersion[]=[
  {id:1,version_number:1,version_name:'初始图谱',build_run_id:1,release_id:null,rollback_from_version_id:null,created_at:'2026-01-10T00:00:00Z'},
  {id:2,version_number:2,version_name:'图谱 v2',build_run_id:2,release_id:null,rollback_from_version_id:null,is_current:true,created_at:'2026-05-12T00:00:00Z'},
];
const pairs:EvolutionVersionPair[]=[{from_version_id:1,to_version_id:2}];

test('事件类型中文映射与未知类型回退',()=>{
  expect(eventTypeLabel('skill_emergence')).toBe('新技能出现');
  expect(eventTypeLabel('skill_decline')).toBe('技能衰退');
  expect(eventTypeLabel('skill_replacement')).toBe('技能替代');
  expect(eventTypeLabel('technology_stack_migration')).toBe('技术栈迁移');
  expect(eventTypeLabel('responsibility_shift')).toBe('岗位职责迁移');
  expect(eventTypeLabel('role_expansion')).toBe('岗位职责扩张');
  expect(eventTypeLabel('role_contraction')).toBe('岗位职责收缩');
  expect(eventTypeLabel('position_rename')).toBe('岗位名称变化');
  expect(eventTypeLabel('future_unknown_event')).toBe('其他能力变化');
});

test('事件分组：8 种真实类型落入 4 类，未知类型归 other',()=>{
  expect(eventGroup('skill_emergence')).toBe('emergence');
  expect(eventGroup('role_expansion')).toBe('emergence');
  expect(eventGroup('skill_replacement')).toBe('replacement');
  expect(eventGroup('technology_stack_migration')).toBe('replacement');
  expect(eventGroup('skill_decline')).toBe('decline');
  expect(eventGroup('role_contraction')).toBe('decline');
  expect(eventGroup('responsibility_shift')).toBe('shift');
  expect(eventGroup('position_rename')).toBe('shift');
  expect(eventGroup('future_unknown_event')).toBe('other');
});

test('事件主体与变化摘要基于真实结构字段',()=>{
  expect(eventSubject(event({}))).toBe('Python');
  expect(eventChangeSummary(event({}))).toBe('权重 0.40 → 0.55，首次达到事件检测阈值');
  expect(eventChangeSummary(event({event_type:'skill_decline',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[],metrics:{before_weight:.6,after_weight:.2,delta:-.4}}))).toBe('权重 0.60 → 0.20，低于事件检测阈值');
  expect(eventChangeSummary(event({event_type:'skill_replacement',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[relation('PT','PyTorch',.5)],reason:'replacement'}))).toBe('TensorFlow → PyTorch');
  expect(eventChangeSummary(event({event_type:'responsibility_shift',source_entities:['旧职责'],target_entities:['新职责'],metrics:{removed_count:1,added_count:2,similarity:.5}}))).toBe('职责集合变化：移除 1 项 / 新增 2 项');
  expect(eventChangeSummary(event({event_type:'position_rename',source_entities:['NLP 工程师'],target_entities:['LLM 工程师'],confidence:1,magnitude:1,metrics:{}}))).toBe('岗位名称变化');
});

test('position_rename 主体展示前后名称',()=>{
  expect(eventSubject(event({event_type:'position_rename',source_entities:['NLP 工程师'],target_entities:['LLM 工程师']}))).toBe('NLP 工程师 → LLM 工程师');
});

test('confidence / magnitude 展示与强事件/谨慎解释标记',()=>{
  expect(percent(.914)).toBe('91%');
  expect(percent(undefined)).toBe('—');
  expect(isStrongEvent(event({magnitude:.74}))).toBe(true);
  expect(isStrongEvent(event({magnitude:.4}))).toBe(false);
  expect(needsCaution(event({confidence:.5}))).toBe(true);
  expect(needsCaution(event({confidence:.9}))).toBe(false);
});

test('多事件按时间 / 版本正确排序',()=>{
  const early=event({event_id:'a',created_at:'2026-03-01T00:00:00Z',from_version:1,to_version:2});
  const late=event({event_id:'b',created_at:'2026-07-01T00:00:00Z',from_version:2,to_version:3});
  const sameDayLaterVersion=event({event_id:'c',created_at:'2026-05-12T00:00:00Z',from_version:2,to_version:3,event_type:'skill_decline'});
  const sameDay=event({event_id:'d',created_at:'2026-05-12T00:00:00Z',from_version:1,to_version:2,event_type:'skill_decline'});
  const sorted=sortEvents([late,early,sameDayLaterVersion,sameDay]);
  expect(sorted.map(item=>item.event_id)).toEqual(['a','d','c','b']);
});

test('日期格式化',()=>{
  expect(monthLabel('2026-05-12T00:00:00Z')).toBe('2026-05');
  expect(dateLabel('2026-05-12T00:00:00Z')).toBe('2026-05-12');
  expect(dateLabel(undefined)).toBe('日期未返回');
});

test('版本号展示与版本元数据查找',()=>{
  expect(versionNumberById(versions,1)).toBe('V1');
  expect(versionNumberById(versions,2)).toBe('V2');
  expect(versionNumberById(versions,99)).toBe('版本未找到');
});

test('Overview 统计直接基于 event list 前端计算',()=>{
  const events=[
    event({event_type:'skill_emergence'}),
    event({event_type:'skill_emergence',event_id:'b'}),
    event({event_type:'skill_replacement',event_id:'c'}),
    event({event_type:'skill_decline',event_id:'d'}),
    event({event_type:'responsibility_shift',event_id:'e'}),
    event({event_type:'future_unknown_event',event_id:'f'}),
  ];
  const stats=overviewStats(events,versions,pairs);
  expect(stats.total).toBe(6);
  expect(stats.emergence).toBe(2);
  expect(stats.replacement).toBe(1);
  expect(stats.decline).toBe(1);
  expect(stats.shift).toBe(1);
  expect(stats.other).toBe(1);
  expect(stats.versionCount).toBe(2);
  expect(stats.pairCount).toBe(1);
});

test('筛选：事件类型分组与 GraphVersion 版本对均为纯前端过滤',()=>{
  const events=[
    event({event_id:'e1',event_type:'skill_emergence'}),
    event({event_id:'e2',event_type:'skill_replacement',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[relation('PT','PyTorch',.5)]}),
    event({event_id:'e3',event_type:'skill_decline',from_version:2,to_version:3,source_entities:[relation('TF','TensorFlow',.2)],target_entities:[],metrics:{before_weight:.6,after_weight:.2,delta:-.4}}),
  ];
  expect(filterEvents(events,'all','all').map(item=>item.event_id)).toEqual(['e1','e2','e3']);
  expect(filterEvents(events,'emergence','all').map(item=>item.event_id)).toEqual(['e1']);
  expect(filterEvents(events,'decline','all').map(item=>item.event_id)).toEqual(['e3']);
  expect(filterEvents(events,'all','1:2').map(item=>item.event_id)).toEqual(['e1','e2']);
  expect(filterEvents(events,'all','2:3').map(item=>item.event_id)).toEqual(['e3']);
  expect(filterEvents(events,'replacement','2:3')).toEqual([]);
});
