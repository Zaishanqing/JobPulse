import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,test} from 'vitest';
import type {GraphSnapshot,Relation} from '../../../shared/api';
import type {CapabilityEvolution} from '../types';
import {SkillEvolutionBubbleView} from './SkillEvolutionBubbleView';

const relation=(weight:number):Relation=>({
  relation_id:1,skill_id:'pytorch',canonical_name:'PyTorch',category_code:'AI',category_name:'深度学习',weight,confidence:.9,
  importance_level:'core',primary_modality:'required',modality_distribution:{required:1},trend_score:null,
  metrics:{support_document_count:8,support_count:8,trusted_evidence_ratio:1,unknown_ratio:0},
});
const snapshot=(weight:number):GraphSnapshot=>({
  position_id:'POS_AI',position:{position_id:'POS_AI',name:'人工智能工程师',category_code:'AI'},skill_relations:[relation(weight)],
  requirement_profile:[],responsibilities:[],company_context:[],employment_context:[],sample_stats:{included_samples:8},
});
const evolution:CapabilityEvolution={
  schema_version:'capability-evolution.v1',position_id:'POS_AI',comparisons:[],events:[],comparison_count:0,event_count:0,frame_count:2,
  frames:[
    {id:1,version_number:1,version_name:'七月版本',build_run_id:1,release_id:null,rollback_from_version_id:null,created_at:'2026-07-27',dependencies:{source_time_window:{start:'2026-07-27',end:'2026-07-27'}},snapshot:snapshot(.45)},
    {id:2,version_number:2,version_name:'八月版本',build_run_id:2,release_id:null,rollback_from_version_id:null,created_at:'2026-08-07',dependencies:{source_time_window:{start:'2026-07-27',end:'2026-08-07'}},snapshot:snapshot(.82)},
  ],
};

afterEach(()=>cleanup());

test('跨图谱版本切换复用同一个技能节点并可打开详情',async()=>{
  render(<SkillEvolutionBubbleView evolution={evolution} positionName="人工智能工程师"/>);
  expect(screen.getAllByText('2026 第 3 季度').length).toBeGreaterThan(0);
  const node=await screen.findByRole('button',{name:/PyTorch/});
  fireEvent.click(node);
  expect(await screen.findByText('PyTorch · 能力详情')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'上一个时间点'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:/PyTorch/})).toBe(node));
  expect(screen.getByText('跨时间轨迹')).toBeInTheDocument();
});
