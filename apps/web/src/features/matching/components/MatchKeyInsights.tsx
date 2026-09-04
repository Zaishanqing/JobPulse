import {Empty,Tag,Typography} from 'antd';
import type {MatchSkillResult,PrioritizedGap,ScoreInsight} from '../types';
import {dimensionLabel} from './dimensionLabels';
import {gapLevelText,normalizeDisplayText,readableBusinessMessage,readableSkillName} from '../viewModels/presentation';

type MatchKeyInsightsProps={
  strengths:ScoreInsight[];
  fallbackSkills:MatchSkillResult[];
  gaps:PrioritizedGap[];
  gapName:(gap:PrioritizedGap)=>string;
  gapFailed:boolean;
};

const levelLabels:Record<string,string>={unknown:'待确认',basic:'基础掌握',beginner:'入门',working:'可独立使用',proficient:'熟练',advanced:'进阶',expert:'专家'};
const levelLabel=(value:string|null|undefined)=>value?levelLabels[value.toLowerCase()]||value:null;
const reasonLabels:Record<string,string>={
  REQUIRED_SKILL_NOT_OBSERVED:'必备能力未见证据',
  INSUFFICIENT_EVIDENCE:'当前证据不足',
  SKILL_OWNERSHIP_GAP:'负责程度不足',
  EXACT_SKILL_EVIDENCE_PRESENT:'存在可核验的技能原文证据',
  RESPONSIBILITY_MATCHED:'岗位职责已有相关实践证据',
  REQUIREMENT_GROUP_SATISFIED:'组合能力要求已满足',
  SPECIALTY_ROUTE_CAPABILITY_AGGREGATE:'专业能力路径已有综合证据',
};

const insightMessage=(item:ScoreInsight)=>reasonLabels[item.reason_code]||readableBusinessMessage(item.message,'该维度已有可核验的匹配证据');
const priorityLabels:Record<string,string>={critical:'最高优先级',high:'高优先级',medium:'中优先级',low:'低优先级'};

function EvidenceQuote({quote}:{quote:string|undefined}){
  return quote?<blockquote>{normalizeDisplayText(quote)}</blockquote>:null;
}

export function MatchKeyInsights({strengths,fallbackSkills,gaps,gapName,gapFailed}:MatchKeyInsightsProps){
  const visibleStrengths=strengths.filter((item,index,all)=>{
    const key=[item.dimension,item.reason_code,normalizeDisplayText(item.evidence[0]?.quote)].join('|');
    return all.findIndex(other=>[other.dimension,other.reason_code,normalizeDisplayText(other.evidence[0]?.quote)].join('|')===key)===index;
  });
  return <section className="match-report-section match-key-insights" aria-labelledby="match-key-insights-title">
    <div className="match-report-section-head"><div><Typography.Title id="match-key-insights-title" level={4}>得分原因</Typography.Title></div></div>
    <div className="match-report-columns">
      <div className="match-insight-column match-insight-column-strengths">
        <Typography.Title level={5}>核心优势</Typography.Title>
        {visibleStrengths.length?visibleStrengths.map(item=><div className="match-report-insight" key={item.result_id}>
          <strong>{dimensionLabel(item.dimension)}</strong>
          <span>{insightMessage(item)}</span>
          <EvidenceQuote quote={item.evidence[0]?.quote}/>
        </div>):fallbackSkills.length?fallbackSkills.slice(0,5).map(item=><div className="match-report-insight" key={item.requirement_id}>
          <strong>{readableSkillName(item.skill_name||item.skill_id)}</strong>
          <span>{item.match_status==='matched'?'已满足':item.match_status==='partial'?'部分满足':'已有相关证据'}{levelLabel(item.candidate_demonstrated_level||item.candidate_declared_level)?` · ${levelLabel(item.candidate_demonstrated_level||item.candidate_declared_level)}`:''}</span>
          <EvidenceQuote quote={item.candidate_evidence[0]?.quote}/>
        </div>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无结构化优势"/>}
      </div>
      <div className="match-insight-column match-insight-column-gaps">
        <Typography.Title level={5}>关键差距</Typography.Title>
        {gapFailed?<Typography.Text type="danger">差距分析失败，完整差距暂不可用。</Typography.Text>:gaps.length?gaps.map((item,index)=><div className="match-report-insight" key={`${item.requirement_id}-${index}`}>
          <div className="match-insight-title"><strong>{gapName(item)}</strong><Tag color={item.priority==='critical'?'error':item.priority==='high'?'warning':'default'}>{priorityLabels[item.priority]||'待排序'}</Tag></div>
          <span>{gapLevelText(item)}</span>
          <EvidenceQuote quote={item.evidence[0]?.quote}/>
        </div>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有结构化差距"/>}
      </div>
    </div>
  </section>;
}
