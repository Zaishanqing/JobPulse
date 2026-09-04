import {describe,expect,test} from 'vitest';
import {growthRateText,newSkillsFirst,shouldShowGrowthRate} from './growthRate';

describe('growthRateText trend zero-baseline consumption',()=>{
  test('newly observed skill must not render a fake +100%',()=>{
    expect(growthRateText({growth_rate:null,trend_direction:'new'})).toBe('新出现（无历史基线，不显示百分比增长）');
  });

  test('zero-to-zero has no baseline and does not render +0%',()=>{
    expect(growthRateText({growth_rate:null,trend_direction:'stable'})).toBe('无增长基线');
  });

  test('comparable growth still renders a normal percentage',()=>{
    expect(growthRateText({growth_rate:0.24,trend_direction:'rising'})).toBe('24.0%');
  });
});

describe('newSkillsFirst',()=>{
  test('新增技能置顶并保持其他技能的原有顺序',()=>{
    const skills=[
      {skill_name:'CUDA',trend_direction:'stable'},
      {skill_name:'Ray',trend_direction:'new'},
      {skill_name:'PyTorch',trend_direction:'rising'},
      {skill_name:'SGLang',trend_direction:'new'},
    ];
    expect(newSkillsFirst(skills).map(skill=>skill.skill_name)).toEqual(['Ray','SGLang','CUDA','PyTorch']);
    expect(skills.map(skill=>skill.skill_name)).toEqual(['CUDA','Ray','PyTorch','SGLang']);
  });
});

describe('shouldShowGrowthRate',()=>{
  test('新增且无历史基线时隐藏增长率行',()=>{
    expect(shouldShowGrowthRate({growth_rate:null,trend_direction:'new'})).toBe(false);
    expect(shouldShowGrowthRate({growth_rate:.24,trend_direction:'rising'})).toBe(true);
  });
});
