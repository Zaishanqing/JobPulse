import type {Evidence,MatchSkillResult,PrioritizedGap} from '../types';

const UUID_PATTERN=/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const HASH_PATTERN=/^[0-9a-f]{16,}$/i;

const technicalNameLabels:Record<string,string>={
  'Megatron-LM':'大模型分布式训练框架 Megatron-LM',
  vLLM:'大模型推理引擎 vLLM',
  RLHF:'人类反馈强化学习（RLHF）',
  SFT:'监督微调（SFT）',
  NLP:'自然语言处理（NLP）',
  CV:'计算机视觉（CV）',
  C:'C 语言',
  'C++':'C++ 语言',
  Java:'Java 语言',
  Python:'Python 语言',
  DeepSpeed:'大模型分布式训练框架 DeepSpeed',
  Transformer:'变换器神经网络架构（Transformer）',
  DPO:'直接偏好优化（DPO）',
  PyTorch:'深度学习框架 PyTorch',
  GRPO:'组相对策略优化（GRPO）',
  CPT:'持续预训练（CPT）',
  PPO:'近端策略优化（PPO）',
  VERL:'大模型强化学习框架 veRL',
};

const inlineTechnicalLabels:Array<[RegExp,string]>=[
  [/\bDiffusio\b/gi,'Diffusion'],
  [/\bDiffution\b/gi,'Diffusion'],
  [/Trigger-Analysis-Reasoning/gi,'触发—分析—推理'],
  [/Alpha-Service/gi,'阿尔法服务'],
  [/Multimodal Large Language Model for Scientific Discovery/gi,'面向科学发现的多模态大语言模型'],
  [/OpenRLHF/gi,'开源人类反馈强化学习框架'],
  [/Llama-Factory/gi,'大模型微调框架 Llama-Factory'],
  [/DeepSpeed/gi,'大模型分布式训练框架 DeepSpeed'],
  [/Megatron(?!-LM)/gi,'大模型分布式训练框架 Megatron'],
  [/\bveRL\b/gi,'大模型强化学习框架 veRL'],
  [/\bTRL\b/g,'变换器强化学习框架'],
  [/Post-pretrain/gi,'后续预训练'],
  [/\bThinking\b/gi,'深度推理'],
  [/\bToken\b/gi,'词元'],
  [/\bSOTA\b/gi,'业界领先'],
  [/\bRL\b/g,'强化学习'],
  [/\bCo[lT]\b/g,'思维链'],
  [/\bFirstExam\b/g,'科学家首轮考试基准'],
  [/\bMathVista\b/g,'数学视觉推理基准'],
  [/GitHub Stars/gi,'GitHub 收藏'],
  [/HuggingFace/gi,'Hugging Face 平台'],
];

export const isInternalId=(value:string|null|undefined)=>Boolean(value&&(UUID_PATTERN.test(value)||HASH_PATTERN.test(value)||value.includes(':')));

export const roundedScoreText=(value:number|null|undefined,fallback='未测量')=>value===null||value===undefined?fallback:String(Math.round(value));

export const readableSkillName=(value:string|null|undefined)=>{
  if(!value||isInternalId(value))return '岗位能力要求';
  return technicalNameLabels[value]||value;
};

const positionTerms:Array<[RegExp,string]>=[
  [/\bAI\b/gi,'人工智能'],[/Android/gi,'安卓'],[/DevOps/gi,'开发运维'],[/FPGA/gi,'现场可编程门阵列'],
  [/\bIT\b/gi,'信息技术'],[/MLOps/gi,'机器学习运维'],[/SLAM/gi,'同步定位与地图构建'],[/\bUI\b/gi,'用户界面'],[/iOS/gi,'苹果移动端'],
];

export const readablePositionName=(value:string|null|undefined)=>{
  if(!value)return '目标岗位';
  let result=value;
  for(const [pattern,replacement] of positionTerms)result=result.replace(pattern,replacement);
  return result;
};

export const normalizeDisplayText=(value:string|null|undefined)=>{
  if(!value)return '';
  let text=value.normalize('NFKC')
    .replace(/\s*\|\s*/g,' | ')
    .replace(/\s*([，。！？；：、])\s*/g,'$1')
    .replace(/\s*,\s*/g,'，')
    .replace(/\s*:\s*/g,'：')
    .replace(/([\p{Script=Han}])\s+(?=[\p{Script=Han}])/gu,'$1')
    .replace(/([\p{Script=Han}])\s*-\s*(?=[\p{Script=Han}])/gu,'$1—')
    .replace(/\(\s+/g,'（')
    .replace(/\s+\)/g,'）')
    .replace(/\s+/g,' ')
    .trim();
  for(const [pattern,replacement] of inlineTechnicalLabels)text=text.replace(pattern,replacement);
  return text
    .replace(/([\p{Script=Han}])\s+(?=[\p{Script=Han}])/gu,'$1')
    .replace(/\s+([，。！？；：、）])/g,'$1')
    .replace(/（\s+/g,'（');
};

const completeSentence=(value:string)=>/[。！？；.!?]$/.test(value);

export const compactEvidenceTexts=(values:Array<string|null|undefined>)=>{
  const fragments=values
    .flatMap(value=>(value||'').split(/\s*\|\s*/))
    .map(normalizeDisplayText)
    .filter(Boolean);
  const unique=fragments.filter((value,index)=>fragments.indexOf(value)===index);
  const stitched:string[]=[];
  for(const fragment of unique){
    const previous=stitched.at(-1);
    if(previous&&!completeSentence(previous))stitched[stitched.length-1]=`${previous}${fragment}`;
    else stitched.push(fragment);
  }
  return stitched.filter((value,index)=>stitched.indexOf(value)===index);
};

export const readableRequirement=(value:string|string[]|null|undefined,skills:MatchSkillResult[],fallback:string)=>{
  const values=Array.isArray(value)?value:value?[value]:[];
  const names=values.map(item=>{
    const skill=skills.find(row=>row.skill_id===item||row.requirement_id===item||row.requirement_id.endsWith(`:${item}`));
    return skill?readableSkillName(skill.skill_name||skill.skill_id):isInternalId(item)?null:normalizeDisplayText(item);
  }).filter((item):item is string=>Boolean(item));
  return [...new Set(names)].join('、')||fallback;
};

const gapConclusionLabels:Record<string,string>={
  responsibility_gap:'岗位职责证据不足，尚未形成可核验的职责经历',
  project_gap:'综合实践证据不足，尚未形成可核验的综合实践经历',
  scenario_gap:'业务场景证据不足，尚未形成可核验的业务场景经历',
  evidence_gap:'已有相关证据，但当前尚不足以核验该项',
  unresolved_gap:'该项画像信息尚未解析，暂无法判断',
};

const gapLevelLabels:Record<string,string>={
  unknown:'待确认',
  unresolved:'待整理',
  missing:'尚未具备',
  declared_only:'已有相关描述',
  weak:'已有基础',
  partial:'部分满足',
  matched:'已满足',
  satisfied:'已满足',
  basic:'基础掌握',
  beginner:'入门',
  working:'可独立使用',
  proficient:'熟练',
  advanced:'进阶',
  expert:'专家',
};

export const gapLevelText=(gap:Pick<PrioritizedGap,'gap_type'|'current_level'|'target_level'>)=>{
  const current=gap.current_level?gapLevelLabels[gap.current_level.toLowerCase()]||gap.current_level:null;
  const target=gap.target_level?gapLevelLabels[gap.target_level.toLowerCase()]||gap.target_level:null;
  if(current&&target)return `当前 ${current} → 目标 ${target}`;
  if(current)return `当前 ${current}`;
  if(target)return `目标 ${target}`;
  return gapConclusionLabels[gap.gap_type]||'当前证据不足';
};

const degreeLabels:Record<string,string>={
  associate:'大专及以上',
  bachelor:'本科及以上',
  master:'硕士及以上',
  phd:'博士',
  doctorate:'博士',
};

export const readableHardConstraintValue=(constraintType:string,value:string|null|undefined)=>{
  const normalized=normalizeDisplayText(value);
  if(!normalized)return '';
  const lower=normalized.toLowerCase();
  if(['education','degree'].includes(constraintType)&&degreeLabels[lower])return degreeLabels[lower];
  if(['experience','experience_years'].includes(constraintType)){
    const years=lower.match(/^(\d+(?:\.\d+)?)\s*(?:years?|yrs?)$/i);
    if(years)return `${years[1]} 年及以上工作经验`;
  }
  return normalized;
};

export const uniqueEvidence=(items:Evidence[])=>items.filter((item,index,all)=>{
  const key=`${item.source_object_type}:${normalizeDisplayText(item.quote)}`;
  return all.findIndex(other=>`${other.source_object_type}:${normalizeDisplayText(other.quote)}`===key)===index;
});

export const readableBusinessMessage=(value:string|null|undefined,fallback:string)=>{
  const text=normalizeDisplayText(value);
  if(!text)return fallback;
  const containsMachineCode=/\b[A-Z][A-Z0-9_]{3,}\b/.test(text)||/^strength[：:]/i.test(text);
  const chineseCount=(text.match(/[\p{Script=Han}]/gu)||[]).length;
  const latinWordCount=(text.match(/[A-Za-z]{3,}/g)||[]).length;
  return containsMachineCode||latinWordCount>6&&chineseCount<latinWordCount*2?fallback:text;
};

const versionLabels:Array<[RegExp,string]>=[
  [/^deterministic-matching\.v(\d+)$/i,'确定性可解释匹配（第 $1 版）'],
  [/^explainable-scoring\.v(\d+)$/i,'可解释评分（第 $1 版）'],
  [/^scoring-config\.v(\d+)$/i,'评分配置（第 $1 版）'],
  [/^semantic-disabled$/i,'语义召回未启用'],
  [/^embedding\.disabled$/i,'向量模型未启用'],
  [/^current$/i,'当前发布版'],
];

export const readableSystemValue=(value:string|null|undefined)=>{
  if(!value)return '缺失';
  if(value==='matching-service')return '匹配计算服务';
  if(value.startsWith('matching_fragments_'))return '岗位匹配语义索引';
  if(/bge[-_/]?m3/i.test(value))return '多语言语义模型（BGE-M3）';
  if(value==='dense')return '稠密向量';
  if(value==='cosine')return '余弦相似度';
  if(value==='l2')return '二范数归一化';
  if(value.startsWith('semantic-fragment.'))return `语义片段规则（${value.split('.').at(-1)?.replace(/^v/i,'第 ')} 版）`;
  for(const [pattern,replacement] of versionLabels)if(pattern.test(value))return value.replace(pattern,replacement);
  return isInternalId(value)?'内部记录已关联':normalizeDisplayText(value);
};

export const fragmentTypeLabel=(value:string)=>({responsibility:'岗位职责',project:'综合实践',project_responsibility:'实践职责',scenario:'业务场景',skill:'技能要求'}[value]||'证据片段');
