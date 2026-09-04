export const growthRateText=(skill:{growth_rate?:number|null;trend_direction?:string|null})=>{
  if(skill.growth_rate==null||skill.growth_rate===undefined){
    if(skill.trend_direction==='new')return '新出现（无历史基线，不显示百分比增长）';
    return '无增长基线';
  }
  const value=skill.growth_rate*100;
  return `${value.toFixed(1)}%`;
};

export const shouldShowGrowthRate=(skill:{growth_rate?:number|null;trend_direction?:string|null})=>
  skill.growth_rate!==null&&skill.growth_rate!==undefined||skill.trend_direction!=='new';

export const newSkillsFirst=<T extends {trend_direction?:string|null}>(skills:readonly T[])=>skills
  .map((skill,index)=>({skill,index}))
  .sort((left,right)=>Number(right.skill.trend_direction==='new')-Number(left.skill.trend_direction==='new')||left.index-right.index)
  .map(item=>item.skill);
