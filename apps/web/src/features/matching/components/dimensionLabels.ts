export const dimensionLabels:Record<string,string>={
  required_skills:'必备技能',
  responsibilities:'岗位职责',
  projects:'综合实践',
  capability_level:'能力等级',
  hard_conditions:'硬性条件',
  business_scenarios:'业务场景',
  bonus_transferable:'可加分能力',
  requirement_groups:'组合要求',
  semantic:'语义补充',
};

export const dimensionLabel=(value:string)=>dimensionLabels[value]||'其他维度';
