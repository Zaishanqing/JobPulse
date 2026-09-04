import {describe,expect,test} from 'vitest';
import type {DimensionScore} from '../types';
import {buildMatchVerdict,MatchVerdictInput} from './matchVerdict';

const dimension=(name:string,score:number|null,applicable=1):DimensionScore=>({
  dimension:name,
  score,
  confidence:.9,
  configured_weight:.2,
  effective_weight:.2,
  applicable_count:applicable,
  scored_count:score===null?0:applicable,
  uncertain_count:0,
});

const baseInput:MatchVerdictInput={
  evaluationId:'evaluation-1',
  overallScore:66,
  matchConfidence:.73,
  hardGateStatus:'uncertain',
  dimensionScores:[
    dimension('required_skills',75),
    dimension('responsibilities',81),
    dimension('projects',18),
    dimension('capability_level',67),
    dimension('bonus_transferable',17),
  ],
  requiredSkillMissingCount:1,
  topActionName:'CUDA 项目实践',
};

describe('buildMatchVerdict',()=>{
  test('同一报告输入生成稳定一致的批语',()=>{
    expect(buildMatchVerdict(baseInput)).toBe(buildMatchVerdict(baseInput));
  });

  test('批语由五句组成且长度在目标区间内',()=>{
    const verdict=buildMatchVerdict(baseInput);
    expect(verdict.split('。').filter(Boolean)).toHaveLength(5);
    expect(verdict.length).toBeGreaterThanOrEqual(90);
    expect(verdict.length).toBeLessThanOrEqual(240);
  });

  test('高分报告使用高匹配开场',()=>{
    const verdict=buildMatchVerdict({...baseInput,overallScore:90});
    expect(verdict).toMatch(/整体匹配程度较高|匹配表现优秀|高度契合/);
  });

  test('低分报告使用低匹配开场',()=>{
    const verdict=buildMatchVerdict({...baseInput,overallScore:30});
    expect(verdict).toMatch(/较明显能力差距|差距较大|支撑整体偏弱/);
  });

  test('最强维度来自实际评分最高的维度',()=>{
    const verdict=buildMatchVerdict(baseInput);
    expect(verdict).toMatch(/岗位职责/);
  });

  test('最弱维度来自实际评分最低的维度',()=>{
    const verdict=buildMatchVerdict(baseInput);
    expect(verdict).toMatch(/加分能力/);
  });

  test('必备技能缺口数量写入短板句',()=>{
    const verdict=buildMatchVerdict({
      ...baseInput,
      dimensionScores:[dimension('required_skills',20),dimension('responsibilities',81),dimension('projects',60)],
      requiredSkillMissingCount:3,
    });
    expect(verdict).toMatch(/3 项必备技能/);
  });

  test('硬性条件未通过时证据句给出硬约束提示',()=>{
    const verdict=buildMatchVerdict({...baseInput,hardGateStatus:'failed'});
    expect(verdict).toMatch(/硬性/);
  });

  test('低置信度时证据句提示不确定性',()=>{
    const verdict=buildMatchVerdict({...baseInput,hardGateStatus:'passed',matchConfidence:.4});
    expect(verdict).toMatch(/不确定性|置信度仍有提升空间/);
  });

  test('行动句包含最值得优先补足的方向名称',()=>{
    const verdict=buildMatchVerdict(baseInput);
    expect(verdict).toContain('CUDA 项目实践');
  });

  test('缺少 Gap 数据时行动句使用通用兜底',()=>{
    const verdict=buildMatchVerdict({...baseInput,topActionName:null});
    expect(verdict).toMatch(/提升空间|逐步完善项目成果/);
  });

  test('无可评分维度时仍生成完整批语',()=>{
    const verdict=buildMatchVerdict({...baseInput,dimensionScores:[dimension('required_skills',null,0)]});
    expect(verdict.split('。').filter(Boolean)).toHaveLength(5);
    expect(verdict).toMatch(/维度信息有限/);
  });

  test('不同报告 ID 的措辞允许不同但各自稳定',()=>{
    const other=buildMatchVerdict({...baseInput,evaluationId:'evaluation-2'});
    expect(other).toBe(buildMatchVerdict({...baseInput,evaluationId:'evaluation-2'}));
  });
});
