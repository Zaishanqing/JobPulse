import {Button,Collapse,Empty,Tag,Typography} from 'antd';
import {useState,type ReactNode} from 'react';
import type {EvaluationContract,HardConstraintResult,MatchSkillResult,ProjectResult} from '../types';
import {compactEvidenceTexts,normalizeDisplayText,readableHardConstraintValue,readableRequirement,readableSkillName,uniqueEvidence} from '../viewModels/presentation';

type MatchCapabilityDetailsProps={evaluation:EvaluationContract;responsibilityContent:ReactNode};

const levelLabels:Record<string,string>={unknown:'待确认',basic:'基础掌握',beginner:'入门',working:'可独立使用',proficient:'熟练',advanced:'进阶',expert:'专家'};
const levelLabel=(value:string|null|undefined)=>value?levelLabels[value.toLowerCase()]||'待确认':null;
const skillStatusLabels:Record<string,string>={matched:'已具备',partial:'已有基础',missing:'可继续补充',weak:'可继续提升',declared_only:'待补充实践',unknown:'信息待补充',unresolved:'信息待整理'};
const hardConstraintLabels:Record<string,string>={pass:'已满足',partial:'已有部分信息',fail:'尚未满足',unknown:'信息待补充',unresolved:'信息待整理',not_required:'无要求'};
const hardConstraintTagColor:Record<string,string>={pass:'success',partial:'warning',fail:'error'};
const constraintTypeLabels:Record<string,string>={
  education:'学历要求',
  degree:'学位要求',
  experience:'工作年限',
  experience_years:'工作年限',
  certificate:'资格证书',
  certification:'资格证书',
  language:'语言要求',
  location:'工作地点',
  availability:'到岗/可用状态',
};

function EvidenceList({quotes}:{quotes:string[]}){
  if(!quotes.length)return <Typography.Text type="secondary">暂无可展示的候选人依据</Typography.Text>;
  return <div className="match-capability-evidence-list">
    {quotes.slice(0,2).map(quote=><blockquote key={quote}>{quote}</blockquote>)}
    {quotes.length>2&&<Typography.Text type="secondary">其余 {quotes.length-2} 条相近依据已归并</Typography.Text>}
  </div>;
}

const compactLabel=(value:string,max=96)=>value.length>max?`${value.slice(0,max).trim()}…`:value;

function SkillRows({items,title}:{items:MatchSkillResult[];title:string}){
  const [expanded,setExpanded]=useState(false);
  const visible=expanded?items:items.slice(0,8);
  if(!items.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`暂无${title}结论`}/>;
  return <div className="match-capability-list">
    <Collapse ghost size="small" items={visible.map(item=>{
      const candidateLevel=levelLabel(item.candidate_demonstrated_level||item.candidate_declared_level);
      const requiredLevel=levelLabel(item.required_level);
      const levelText=[candidateLevel&&`候选能力等级：${candidateLevel}`,requiredLevel&&`岗位要求：${requiredLevel}`].filter(Boolean).join(' · ');
      return {
      key:item.requirement_id,
      label:<div className="match-capability-row-head"><strong>{readableSkillName(item.skill_name||item.skill_id)}</strong><Tag color={item.match_status==='matched'?'success':item.match_status==='missing'?'error':item.match_status==='partial'?'warning':'default'}>{skillStatusLabels[item.match_status]||'待确认'}</Tag></div>,
      children:<div className="match-capability-detail">{levelText&&<span>{levelText}</span>}<EvidenceList quotes={uniqueEvidence(item.candidate_evidence||[]).map(row=>normalizeDisplayText(row.quote))}/></div>,
    };})}/>
    {items.length>8&&<Button type="link" className="match-capability-expand" onClick={()=>setExpanded(value=>!value)}>{expanded?'收起':'展开全部'}{title}（{items.length}）</Button>}
  </div>;
}

function ExperienceRows({items,title,emptyDescription,skills}:{items:ProjectResult[];title:string;emptyDescription:string;skills:MatchSkillResult[]}){
  const [expanded,setExpanded]=useState(false);
  if(!items.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyDescription}/>;
  const visible=expanded?items:items.slice(0,4);
  return <div className="match-capability-list"><Collapse ghost size="small" items={visible.map(item=>{
    const taskValues=(item.candidate_tasks||[]).length?(item.candidate_tasks||[]):[item.candidate_experience];
    const tasks=compactEvidenceTexts(taskValues);
    const taskKeys=new Set(tasks.map(normalizeDisplayText));
    const evidence=compactEvidenceTexts(uniqueEvidence(item.candidate_evidence||[]).map(row=>row.quote)).filter(quote=>!taskKeys.has(normalizeDisplayText(quote)));
    const requirement=readableRequirement(item.position_requirement,skills,title);
    return {
      key:item.requirement_id,
      label:<div className="match-capability-row-head"><div><strong title={requirement}>{compactLabel(requirement)}</strong><small>{tasks.length+evidence.length} 条相关依据</small></div><Tag color={item.match_status==='matched'?'success':item.match_status==='partial'?'warning':'default'}>{item.match_status==='matched'?'已有充分实践':item.match_status==='partial'?'已有相关实践':'可继续补充'}</Tag></div>,
      children:<div className="match-capability-detail"><EvidenceList quotes={[...tasks,...evidence]}/>{item.confidence!==undefined&&<small>判断置信度 {Math.round((item.confidence??0)*100)}%</small>}</div>,
    };
  })}/>{items.length>4&&<Button type="link" className="match-capability-expand" onClick={()=>setExpanded(value=>!value)}>{expanded?'收起':'查看其余'}综合实践（{items.length}）</Button>}</div>;
}

function HardConstraintRows({items}:{items:HardConstraintResult[]}){
  if(!items.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无硬性条件"/>;
  return <Collapse ghost size="small" className="match-capability-list" items={items.map(item=>({
    key:item.requirement_id,
    label:<div className="match-capability-row-head"><strong>{item.constraint_type==='certificate'?'获奖、论文或专利经历':compactLabel(readableHardConstraintValue(item.constraint_type,item.required_value)||constraintTypeLabels[item.constraint_type]||'硬性条件',56)}</strong><Tag color={hardConstraintTagColor[item.status]||'default'}>{hardConstraintLabels[item.status]||'信息待补充'}</Tag></div>,
    children:<div className="match-capability-detail"><span>{readableHardConstraintValue(item.constraint_type,item.candidate_value)?`已识别：${readableHardConstraintValue(item.constraint_type,item.candidate_value)}`:'简历中暂未识别到可计算信息'}</span><EvidenceList quotes={uniqueEvidence(item.candidate_evidence||[]).map(row=>normalizeDisplayText(row.quote))}/></div>,
  }))}/>;
}

export function MatchCapabilityDetails({evaluation,responsibilityContent}:MatchCapabilityDetailsProps){
  const requiredSkills=evaluation.skill_results.filter(item=>item.importance_level==='required');
  const bonusSkills=evaluation.skill_results.filter(item=>item.importance_level!=='required');
  return <section id="match-report-capabilities" className="match-report-section match-capability-details" aria-labelledby="match-capability-title">
    <div className="match-report-section-head"><div><Typography.Title id="match-capability-title" level={4}>能力匹配明细</Typography.Title></div></div>
    <div className="match-capability-primary">
      <div className="match-capability-panel"><Typography.Title level={5}>必备技能</Typography.Title><SkillRows items={requiredSkills} title="必备技能"/></div>
      <div className="match-capability-panel"><Typography.Title level={5}>岗位职责</Typography.Title>{responsibilityContent}</div>
    </div>
    <div className="match-capability-secondary">
      <div className="match-capability-panel"><Typography.Title level={5}>综合实践证据</Typography.Title><ExperienceRows items={evaluation.project_results||[]} title="综合实践经历" emptyDescription="暂无综合实践证据" skills={evaluation.skill_results}/></div>
      <div className="match-capability-panel"><Typography.Title level={5}>硬性条件</Typography.Title><HardConstraintRows items={evaluation.hard_constraint_results||[]}/></div>
      <div className="match-capability-panel"><Typography.Title level={5}>可加分能力</Typography.Title><SkillRows items={bonusSkills} title="可加分能力"/></div>
    </div>
  </section>;
}
