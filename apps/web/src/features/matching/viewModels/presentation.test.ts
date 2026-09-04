import {describe,expect,test} from 'vitest';
import {compactEvidenceTexts,gapLevelText,normalizeDisplayText,readableHardConstraintValue,readableRequirement,readableSkillName,readableSystemValue,roundedScoreText} from './presentation';
import type {MatchSkillResult} from '../types';

describe('matching presentation',()=>{
  test('清理中文抽取空格并翻译界面中的英文技术描述',()=>{
    expect(normalizeDisplayText('策 - 记 忆 模 块 化 设计 及 Alpha-Service 框 架')).toBe('策—记忆模块化设计及阿尔法服务框架');
    expect(normalizeDisplayText('Trigger-Analysis-Reasoning 与 Token 效率')).toBe('触发—分析—推理与词元效率');
    expect(normalizeDisplayText('Diffusio 与 diffution 模型')).toBe('Diffusion 与 Diffusion 模型');
  });

  test('报告评分统一四舍五入为整数',()=>{
    expect(roundedScoreText(64.8529)).toBe('65');
    expect(roundedScoreText(60.5084)).toBe('61');
    expect(roundedScoreText(13.6554)).toBe('14');
  });

  test('合并跨片段断句并删除整组重复',()=>{
    expect(compactEvidenceTexts(['全 流程 训练 , 端 到','端 可 复 现 。','全 流程 训练 , 端 到','端 可 复 现 。']))
      .toEqual(['全流程训练，端到端可复现。']);
  });

  test('把岗位要求里的 UUID 映射为技能中文说明',()=>{
    const skills=[{requirement_id:'standard-position:skill:3feed904-e148-4d9f-b3f7-88c062447f63',skill_id:'3feed904-e148-4d9f-b3f7-88c062447f63',skill_name:'Megatron-LM'}] as MatchSkillResult[];
    expect(readableRequirement(['3feed904-e148-4d9f-b3f7-88c062447f63','性能优化'],skills,'综合实践经历'))
      .toBe('大模型分布式训练框架 Megatron-LM、性能优化');
    expect(readableSkillName('3feed904-e148-4d9f-b3f7-88c062447f63')).toBe('岗位能力要求');
  });

  test('审计抽屉不直接展示内部英文编码',()=>{
    expect(readableSystemValue('matching-service')).toBe('匹配计算服务');
    expect(readableSystemValue('deterministic-matching.v9')).toBe('确定性可解释匹配（第 9 版）');
  });

  test('无等级字段的上下文差距不被伪装成等级结论',()=>{
    expect(gapLevelText({gap_type:'responsibility_gap',current_level:null,target_level:null}))
      .toBe('岗位职责证据不足，尚未形成可核验的职责经历');
  expect(gapLevelText({gap_type:'skill_level_gap',current_level:'working',target_level:'proficient'}))
    .toBe('当前 可独立使用 → 目标 熟练');
  expect(gapLevelText({gap_type:'skill_level_gap',current_level:null,target_level:'proficient'}))
    .toBe('目标 熟练');
  expect(gapLevelText({gap_type:'skill_level_gap',current_level:'partial',target_level:'satisfied'}))
    .toBe('当前 部分满足 → 目标 已满足');
});

test('硬性条件规范值转换为中文业务文案',()=>{
  expect(readableHardConstraintValue('education','master')).toBe('硕士及以上');
  expect(readableHardConstraintValue('experience','3 years')).toBe('3 年及以上工作经验');
  expect(readableHardConstraintValue('location','上海')).toBe('上海');
});
});
