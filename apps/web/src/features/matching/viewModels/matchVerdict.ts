import type {DimensionScore} from '../types';

// 匹配评价（批语）生成器：结构固定 + 内容动态 + 句式随机但稳定。
// 全部输出由报告数据经确定性规则生成，不调用任何模型；
// 同一份报告每次展示措辞一致，不同报告通过稳定哈希产生自然的句式差异。

export type MatchVerdictInput={
  evaluationId:string;
  overallScore:number|null;
  matchConfidence:number;
  hardGateStatus:string;
  dimensionScores:DimensionScore[];
  requiredSkillMissingCount:number|null;
  topActionName:string|null;
};

const stableIndex=(seed:string,length:number)=>{
  let hash=5381;
  for(let index=0;index<seed.length;index+=1)hash=((hash<<5)+hash+seed.charCodeAt(index))>>>0;
  return length>0?hash%length:0;
};

const pick=(seed:string,templates:string[])=>templates[stableIndex(seed,templates.length)];

const openingBands:Array<{min:number;templates:string[]}>=[
  {min:85,templates:[
    '候选人与目标岗位整体匹配程度较高，当前能力结构已覆盖多数核心要求。',
    '候选人与目标岗位的匹配表现优秀，核心能力结构完整且证据充分。',
    '候选人整体能力结构与目标岗位高度契合，多数关键要求已得到验证。',
  ]},
  {min:70,templates:[
    '候选人与目标岗位具备较好的匹配基础，多项核心能力已经达到岗位要求。',
    '候选人与目标岗位有较为扎实的匹配基础，核心能力整体上能够满足岗位需要。',
    '候选人已具备岗位所需的大部分核心能力，整体匹配基础较好。',
  ]},
  {min:55,templates:[
    '候选人与目标岗位具备一定匹配基础，但当前能力结构仍存在若干明显缺口。',
    '候选人已有一定的能力积累，但距离完整满足岗位要求仍有可见差距。',
    '候选人与目标岗位存在部分能力交集，整体匹配基础尚不稳固。',
  ]},
  {min:0,templates:[
    '候选人与目标岗位目前存在较明显能力差距，多项核心要求尚未得到充分支撑。',
    '候选人当前能力结构与目标岗位要求差距较大，多项核心条件尚未满足。',
    '候选人现有经历和技能对目标岗位核心要求的支撑整体偏弱。',
  ]},
];

const strengthTemplates:Record<string,string[]>={
  required_skills:[
    '必备技能覆盖情况较好，核心技术基础能够支撑多数岗位要求。',
    '核心必备技能已有较完整的覆盖，技能基础是当前的主要优势。',
    '必备技能方面表现扎实，多数岗位核心技能要求均已具备。',
  ],
  responsibilities:[
    '岗位职责相关性较高，已有经历与目标岗位核心工作存在较明显交集。',
    '已有经历与目标岗位职责的重合度较高，能够直接衔接部分核心工作。',
    '岗位职责适配是当前的主要优势，过往经历覆盖了岗位的多项核心职责。',
  ],
  projects:[
    '已有项目经历较丰富，能够形成较完整的实践能力支撑。',
    '综合实践表现突出，已有项目成果为能力提供了直接证据。',
    '项目经历与岗位实践要求贴合，已具备可核验的实践闭环。',
  ],
  bonus_transferable:[
    '已具备部分可加分能力，相关技能能为岗位匹配提供额外支撑。',
    '可加分能力储备较好，相关技能可直接补充岗位匹配得分。',
    '可加分能力储备充足，为胜任目标岗位提供了额外支撑。',
  ],
  capability_level:[
    '核心能力等级基本达到岗位要求，技能深度能够支撑主要工作。',
    '已具备与岗位要求相当的能力等级，技能深度满足多数工作需要。',
  ],
  hard_conditions:[
    '学历、经验等硬性条件已满足岗位门槛要求。',
    '关键硬性条件均已通过核验，不存在门槛性障碍。',
  ],
  business_scenarios:[
    '已有经历与目标业务场景较为贴近，场景适配基础较好。',
    '候选人对目标业务场景有一定了解，相关经历能够直接复用。',
  ],
};

const weaknessTemplates:Record<string,string[]>={
  required_skills:[
    '当前仍存在较明显的核心技能缺口，对整体匹配形成直接限制。',
    '必备技能覆盖不足是当前的主要短板，部分核心技能要求尚未满足。',
    '核心技能仍存在缺口，技能基础尚不足以完整支撑岗位要求。',
  ],
  responsibilities:[
    '已有项目与目标岗位的实际工作内容存在一定距离。',
    '岗位职责经历不足是当前的主要限制，已有工作与目标职责的重合度有限。',
    '已有经历与目标岗位职责的对应关系较弱，核心工作内容的直接经验不足。',
  ],
  projects:[
    '当前主要限制来自综合实践，已有技能尚缺少足够的项目成果支撑。',
    '综合实践是当前较明显的薄弱项，相关能力仍缺乏完整项目证据。',
    '目前实践侧支撑相对不足，部分技能更多停留在能力声明或局部使用阶段。',
    '已有技能基础尚可，但对应的真实项目成果和实践闭环仍显不足。',
  ],
  bonus_transferable:[
    '可加分能力支撑较弱，相关加分技能尚未形成有效补充。',
    '可加分能力储备有限，相关技能对岗位要求的补充作用不明显。',
  ],
  capability_level:[
    '部分核心技能虽然已经覆盖，但实际能力等级仍低于岗位要求。',
    '能力等级是当前的短板，部分技能的掌握深度尚未达到岗位预期。',
  ],
  hard_conditions:[
    '部分硬性岗位条件尚未满足或缺少明确证据，构成门槛性限制。',
  ],
  business_scenarios:[
    '已有经历与目标业务场景的贴合度有限，场景经验仍需补充。',
  ],
};

const hardGateFailedTemplates=[
  '此外存在尚未满足的硬性岗位条件，对当前匹配结果形成明显约束。',
  '需要特别注意，仍有硬性条件未通过核验，会直接影响岗位适配结论。',
];
const hardGateUncertainTemplates=[
  '同时仍有部分关键条件尚未获得明确证据，需要进一步确认。',
  '部分硬性条件仍处于待确认状态，建议补充相应证明后再做最终判断。',
];
const lowConfidenceTemplates=[
  '部分判断受到证据完整性的影响，当前结果仍存在一定不确定性。',
  '现有证据覆盖有限，部分维度结论的置信度仍有提升空间。',
];
const wellSupportedTemplates=[
  '多数关键判断均能够从现有简历证据中得到直接支持。',
  '主要结论均有简历证据支撑，结果整体可信度较高。',
];

const actionWithNameTemplates=[
  '当前最值得优先补足的是{name}，该项同时影响技能覆盖、实践证据和岗位职责适配。',
  '若优先补充{name}相关实践并形成可核验成果，预计会对当前匹配结果产生较明显改善。',
  '建议优先围绕{name}补齐能力证据，这是当前投入产出比最高的提升方向。',
];
const actionFallbackTemplates=[
  '后续若持续补齐关键能力缺口并形成可核验证据，整体匹配表现仍有提升空间。',
  '当前最优先的方向是补足评分最低维度对应的能力证据，并逐步完善项目成果。',
];

const applicableDimensions=(rows:DimensionScore[])=>rows
  .filter(item=>item.score!==null&&item.applicable_count>0)
  .slice()
  .sort((a,b)=>(b.score as number)-(a.score as number));

const dimensionSentence=(
  seed:string,
  row:DimensionScore,
  templates:Record<string,string[]>,
  missingCount:number|null,
)=>{
  const pool=templates[row.dimension];
  if(!pool)return null;
  let sentence=pick(seed,pool);
  // 必备技能短板在缺口数量已知时，把数量写进句子，保持与底部事实栏一致。
  if(row.dimension==='required_skills'&&templates===weaknessTemplates&&missingCount!==null&&missingCount>0){
    sentence=pick(`${seed}:counted`,[
      `当前仍有 ${missingCount} 项必备技能存在缺口，对整体匹配形成直接限制。`,
      `必备技能仍有 ${missingCount} 项缺口，是当前限制整体匹配的主要因素。`,
    ]);
  }
  return sentence;
};

export const buildMatchVerdict=(input:MatchVerdictInput):string=>{
  const seedOf=(slot:string,key:string)=>`${input.evaluationId}:${slot}:${key}`;

  const score=input.overallScore;
  const opening=score===null
    ?'当前报告尚未形成有效综合评分，以下判断仅基于已返回的部分维度结果。'
    :pick(seedOf('opening',String(Math.round(score))),
        (openingBands.find(band=>score>=band.min)||openingBands[openingBands.length-1]).templates);

  const ranked=applicableDimensions(input.dimensionScores);
  const top=ranked[0];
  const bottom=ranked[ranked.length-1];
  const strength=top
    ?dimensionSentence(seedOf('strength',top.dimension),top,strengthTemplates,null)
      ||'当前可评分维度中已有部分能力表现较好。'
    :'当前可评分的维度信息有限，优势判断主要依赖已有证据。';
  const weakness=bottom&&top&&bottom.dimension!==top.dimension
    ?dimensionSentence(seedOf('weakness',bottom.dimension),bottom,weaknessTemplates,input.requiredSkillMissingCount)
      ||'部分维度评分偏低，是当前主要的提升方向。'
    :ranked.length<=1
      ?'当前可评分维度较少，短板判断需结合更多证据确认。'
      :'各可评分维度表现较为接近，尚未出现明显短板。';

  let evidencePool:string[];
  let evidenceKey:string;
  if(input.hardGateStatus==='failed'){evidencePool=hardGateFailedTemplates;evidenceKey='gate-failed';}
  else if(input.hardGateStatus==='uncertain'){evidencePool=hardGateUncertainTemplates;evidenceKey='gate-uncertain';}
  else if(input.matchConfidence<0.6){evidencePool=lowConfidenceTemplates;evidenceKey='low-confidence';}
  else{evidencePool=wellSupportedTemplates;evidenceKey='supported';}
  const evidence=pick(seedOf('evidence',evidenceKey),evidencePool);

  const action=input.topActionName
    ?pick(seedOf('action',input.topActionName),actionWithNameTemplates).replace(/\{name\}/g,input.topActionName)
    :pick(seedOf('action','fallback'),actionFallbackTemplates);

  return [opening,strength,weakness,evidence,action].join('');
};
