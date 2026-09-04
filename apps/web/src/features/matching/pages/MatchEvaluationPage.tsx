/* eslint-disable react-refresh/only-export-components */
import {useCallback,useEffect,useMemo,useState} from 'react';
import {Button,Card,Checkbox,Collapse,Descriptions,Drawer,Empty,InputNumber,Pagination,Select,Space,Spin,Tag,Tooltip,Typography} from 'antd';
import {CommentOutlined,DownloadOutlined,FileSearchOutlined,LockOutlined} from '@ant-design/icons';
import {useLocation,useNavigate,useParams,useSearchParams} from 'react-router-dom';
import {ApiError,errorTitle} from '../../../shared/api';
import {statusText} from '../../../shared/idText';
import {ToastAlert as Alert} from '../../../shared/components/States';
import {createLearningPath,evaluateEvidenceDeletion,evaluateMatchWhatIf,exportLearningPath,getLearningPath,getMatchEvaluation,listEnterpriseJobs,listMatchPositions,listMyResumes} from '../api';
import {EvidenceDrawer,EvidenceRow} from '../../evidence/components/EvidenceViewer';
import {getJD} from '../../data/api';
import {MatchCapabilityDetails} from '../components/MatchCapabilityDetails';
import {MatchDimensionRadar} from '../components/MatchDimensionRadar';
import {MatchKeyInsights} from '../components/MatchKeyInsights';
import {MatchReportOverview} from '../components/MatchReportOverview';
import {MatchReportSectionNav} from '../components/MatchReportSectionNav';
import {SemanticShadowSection} from '../components/SemanticShadowSection';
import {
  buildResponsibilityViewModel,
  matchingMethodLabel,
  resolveMatchingMethod,
} from '../viewModels/responsibility';
import {EvidenceDeepLinkFocus} from '../../rag/EvidenceDeepLink';
import {dimensionLabel} from '../components/dimensionLabels';
import {compactEvidenceTexts,gapLevelText,normalizeDisplayText,readableHardConstraintValue,readablePositionName,readableRequirement,readableSkillName,readableSystemValue,roundedScoreText,uniqueEvidence} from '../viewModels/presentation';
import type {
  ActionCost,
  DimensionScore,
  EvidenceDeletionResult,
  EvaluationReport,
  Evidence,
  GapAnalysis,
  LearningPath,
  LearningRoute,
  MatchLineage,
  MatchPosition,
  MatchVersions,
  PrioritizedGap,
  ResponsibilityResult,
  WhatIfAction,
} from '../types';

const learningPathErrorTitle=(error:ApiError|undefined,action:'create'|'load'|'export')=>{
  if(action==='export')return '学习路径导出失败';
  if(error?.status===404)return action==='create'?'来源评估不存在':'学习路径不存在';
  if(error?.status===403)return '无权访问学习路径';
  if(error?.status===409&&['EVALUATION_STALE','LEARNING_PATH_TARGET_MISMATCH'].includes(error.errorCode||''))return '评估已过期或不可用于生成';
  return action==='create'?'学习路线生成失败':errorTitle(error||{});
};

const learningPathFailureLabels:Record<string,string>={
  LEARNING_PATH_NOT_REQUESTED:'原始匹配未请求生成学习步骤，请重新规划。',
  LEARNING_PATH_PROFILE_INVALID:'简历或岗位画像格式不兼容，无法生成学习路径。',
  LEARNING_PATH_EMPTY:'存在能力差距，但服务没有返回学习步骤。',
  LEARNING_PATH_GENERATION_REJECTED:'学习路径生成被拒绝，请检查简历与岗位数据后重试。',
  EVALUATION_PROFILE_MISMATCH:'评估与当前简历或岗位画像版本不一致。',
  EVALUATION_STALE:'评估数据已更新，请重新匹配后再规划。',
  CV_SNAPSHOT_UNAVAILABLE:'当前简历没有可用于规划的正式快照。',
};

const formatPathTime=(value:string|null|undefined)=>value?new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'生成时间待确认';

function LearningPathDetail({path,targetPositionName,skillName,requirementName,onExport,exporting}:{path:LearningPath;targetPositionName:string;skillName:(value:string|null|undefined)=>string;requirementName:(value:string|null|undefined)=>string;onExport:()=>void;exporting:boolean}){
  const failureCode=path.gap_analysis?.error_code||null;
  return <div className="learning-plan-detail">
    <div className="learning-plan-detail-head">
      <div><Typography.Title level={5}>学习建议</Typography.Title><Typography.Text type="secondary">{targetPositionName} · {path.time_budget_hours===null?'弹性时间':`${path.time_budget_hours} 小时`} · {formatPathTime(path.created_at)}</Typography.Text></div>
      <Button type="link" icon={<DownloadOutlined/>} loading={exporting} onClick={onExport}>导出</Button>
    </div>
    {path.gap_analysis&&<ActionPlanView gap={path.gap_analysis} skillName={skillName} requirementName={requirementName}/>}
    {path.gap_analysis?.generation_status==='rejected'?<Alert
      type="error"
      showIcon
      title="该学习路径生成失败"
      description={failureCode&&learningPathFailureLabels[failureCode]||'服务未生成可执行的学习步骤，请重新规划。'}
    />:null}
  </div>;
}

const scorePercent=(value:number|null|undefined)=>roundedScoreText(value);
const ratioPercent=(value:number|null|undefined)=>value===null||value===undefined?'未测量':`${Math.round(value*100)}%`;
const roundedScoreDelta=(baseline:number|null|undefined,finalScore:number|null|undefined)=>baseline===null||baseline===undefined||finalScore===null||finalScore===undefined?null:Math.round(finalScore)-Math.round(baseline);
const roundedScoreDeltaText=(baseline:number|null|undefined,finalScore:number|null|undefined,fallback='未测量')=>String(roundedScoreDelta(baseline,finalScore)??fallback);

const gateLabels:Record<string,string>={passed:'硬性条件通过',failed:'硬性条件未通过',uncertain:'硬性条件待确认',not_applicable:'无适用硬性条件'};
const hardConstraintTypeLabels:Record<string,string>={
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

const staleReasonLabels:Record<string,string>={
  ALGORITHM_VERSION_CHANGED:'评分方法已更新',
  INPUT_FINGERPRINT_CHANGED:'简历或岗位信息已更新',
  CV_PROFILE_VERSION_CHANGED:'简历画像已更新',
  POSITION_PROFILE_VERSION_CHANGED:'岗位要求已更新',
  POSITION_GRAPH_VERSION_CHANGED:'岗位能力图谱已更新',
};

const looksLikeInternalId=(value:string)=>/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(value)||value.includes(':');
const gapTypeLabels:Record<string,string>={required_skill_missing:'缺少必备技能',bonus_skill_missing:'可加分能力不足',required_skill_weak:'必备技能掌握不足',capability_level_gap:'能力等级不足',evidence_gap:'缺少有效证据',hard_condition_gap:'硬性条件未满足',responsibility_gap:'岗位职责证据差距',project_gap:'综合实践证据差距',scenario_gap:'业务场景证据差距'};
const priorityLabels:Record<string,string>={critical:'最高优先级',high:'高优先级',medium:'中优先级',low:'低优先级'};

const spanText=(start:number|null,end:number|null)=>start===null||end===null?'未标注原文区间':`${start}-${end}`;

const evidenceSourceLabels:Record<string,string>={validated_cv_snapshot:'候选人简历',position_profile:'岗位能力要求',matching_evidence:'匹配分析',source_jd:'岗位证据',jd_evidence:'岗位证据',cv_evidence:'候选人简历'};
const alignmentLabels:Record<string,string>={exact:'原文精确匹配',normalized:'归一化匹配',inferred:'系统推断'};
const evidenceVersionText=(evidence:Evidence)=>evidence.version?'证据版本已锁定':'未标注证据版本';

const responsibilityStatusColor=(value:string|null|undefined)=>{
  if(value==='matched')return 'success';
  if(value==='partial')return 'warning';
  if(value==='insufficient_evidence'||value==='uncertain')return 'default';
  return 'default';
};

function responsibilityCollapseItem(item:ResponsibilityResult){
  const view=buildResponsibilityViewModel(item);
  const displayed={...view};
  const fallbackEvidence=item.top_candidates?.flatMap(candidate=>candidate.evidence_refs||[])||[];
  const evidence=uniqueEvidence(view.evidence.length?view.evidence:fallbackEvidence);
  const texts=compactEvidenceTexts([item.candidate_experience,...evidence.map(row=>row.quote)]);
  return {
    key:item.requirement_id,
    label:<div className="match-responsibility-head"><div><Typography.Text strong>{normalizeDisplayText(Array.isArray(item.position_requirement)?item.position_requirement.join('、'):item.position_requirement)||'岗位职责'}</Typography.Text><small>{texts.length?`${texts.length} 条候选人证据`:'暂无候选人证据'}</small></div><Tag color={responsibilityStatusColor(view.finalStatus)}>{view.statusLabel}</Tag></div>,
    children:<div className="match-responsibility-item">
    {texts.length>0&&<div className="match-responsibility-evidence">{texts.map(text=><blockquote key={text}>{text}</blockquote>)}</div>}
    {view.confidence!==null&&<Typography.Text type="secondary">评估置信度 {Math.round(view.confidence*100)}%</Typography.Text>}
    {displayed.semanticDiagnostics&&<div className="match-responsibility-semantic">
      <Typography.Text strong>智能语义验证</Typography.Text>
      <Space wrap size={8}>
        <Tag>语义验证：{displayed.semanticDiagnostics.semanticVerification}</Tag>
        {displayed.semanticDiagnostics.evidenceStrength&&<Tag>证据相关度：{displayed.semanticDiagnostics.evidenceStrength}</Tag>}
        {displayed.semanticDiagnostics.decisionConfidence&&<Tag>判断置信程度：{displayed.semanticDiagnostics.decisionConfidence}</Tag>}
      </Space>
    </div>}
  </div>,
  };
}

export function ResponsibilityDetailList({items}:{items:ResponsibilityResult[]}){
  if(!items.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无岗位职责结论"/>;
  return <Collapse ghost size="small" className="match-responsibility-list" items={items.map(responsibilityCollapseItem)}/>;
}

function EvidenceOriginalDrawer({evidence,onClose}:{evidence:Evidence|null;onClose:()=>void}){
  const [rawText,setRawText]=useState('');
  const [loading,setLoading]=useState(false);
  useEffect(()=>{
    if(!evidence)return;
    let active=true;
    const source=evidence.source_object_type||'';
    const sourceId=evidence.source_document_id||evidence.source_object_id;
    const timer=window.setTimeout(()=>{
      setLoading(true);
      setRawText('');
      if(source==='validated_cv_snapshot'||source==='cv_evidence'||source==='resume'||source.includes('cv')){
        listMyResumes().then(values=>{
          const resume=values.find(item=>[
            item.resume_id,
            item.source_cv_version_id,
            item.validated_cv_snapshot_id,
          ].some(value=>value&&[evidence.source_object_id,evidence.source_document_id].includes(value)));
          if(active&&resume)setRawText(resume.raw_text);
        }).catch(()=>undefined)
          .finally(()=>{if(active)setLoading(false)});
      }else if(source==='source_jd'||source==='jd_evidence'||source.includes('jd')){
        getJD(sourceId).then(value=>{if(active)setRawText(value.raw_text)})
          .catch(()=>undefined)
          .finally(()=>{if(active)setLoading(false)});
      }else{
        setLoading(false);
      }
    },0);
    return()=>{active=false;window.clearTimeout(timer)};
  },[evidence]);
  if(!evidence)return null;
  return <EvidenceDrawer open title="证据链" subtitle={evidenceSourceLabels[evidence.source_object_type]||'业务证据'} onClose={onClose}>
    {loading?<Spin/>:<EvidenceRow row={{
      key:evidence.result_reference,
      alignment:evidence.alignment,
      occurrence_index:evidence.occurrence_index,
      start:evidence.start,
      end:evidence.end,
      quote:evidence.quote,
      rawText:rawText||undefined,
      documentId:evidence.source_document_id||undefined,
    }}/>}
  </EvidenceDrawer>;
}

function EvidenceItem({evidence,onOpen}:{evidence:Evidence;onOpen:(evidence:Evidence)=>void}){
  return <div className="match-evidence-item">
    <blockquote>{normalizeDisplayText(evidence.quote)}</blockquote>
    <Typography.Text type="secondary">
      {evidenceSourceLabels[evidence.source_object_type]||'业务证据'} · 原文区间 {spanText(evidence.start,evidence.end)} · {alignmentLabels[evidence.alignment]||'已关联'}
    </Typography.Text>
    <Typography.Text type="secondary">来源版本：{evidenceVersionText(evidence)}</Typography.Text>
    <div className="match-evidence-action">
      <Button type="link" size="small" onClick={()=>onOpen(evidence)}>查看原文</Button>
    </div>
  </div>;
}

function GapCard({gap,name}:{gap:PrioritizedGap;name:string}){
  return <div className="match-gap-item">
    <div className="match-gap-head">
      <Typography.Text strong>{name}</Typography.Text>
      <div className="match-gap-meta">
        <Tag>{gapTypeLabels[gap.gap_type]||'能力差距'}</Tag>
        <Tag color={gap.priority==='critical'?'error':gap.priority==='high'?'warning':'default'}>{priorityLabels[gap.priority]||'待排序'}</Tag>
        <b>{roundedScoreText(gap.priority_score)}</b>
      </div>
    </div>
    <Typography.Text type="secondary">
      {gapLevelText(gap)}
    </Typography.Text>
  </div>;
}

const routeLabels:Record<LearningRoute['route_type'],string>={
  fastest_employment:'最快就业',
  budget_max_gain:'预算内最大增益',
  foundation_first:'基础优先',
};
const routeHighlights:Record<LearningRoute['route_type'],string>={
  fastest_employment:'投入更轻',
  budget_max_gain:'提升最多',
  foundation_first:'基础打底',
};
const routeDescriptions:Record<LearningRoute['route_type'],string>={
  fastest_employment:'优先完成耗时更短的关键实践，更快形成可验收的岗位证据。',
  budget_max_gain:'组合当前高价值行动，争取本轮可获得的最高预计分数。',
  foundation_first:'先补强工程落地基础，为后续能力扩展和持续提升打底。',
};

const actionLabels:Record<WhatIfAction['action_type'],string>={
  add_skill:'补充技能',
  // 综合实践证据：候选人在项目/实习/工作中实际使用目标能力，而非狭义“项目经验”。
  add_project_experience:'补充综合实践证据',
  strengthen_evidence:'强化证据',
  strengthen_ownership:'强化负责程度',
  satisfy_hard_condition:'满足硬性条件',
  controlled_skill_transfer:'受控技能迁移',
};

const containsUuid=(value:string|null|undefined)=>Boolean(value&&/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i.test(value));
export const planningText=(value:string|null|undefined,internalLabel='目标能力')=>normalizeDisplayText(value)
  .replace(/standard-position[：:]skill[：:][0-9a-f-]{36}/gi,internalLabel)
  .replace(/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/gi,internalLabel)
  .replace(/\bJD\b/gi,'岗位描述')
  .replace(/\brequirements?\b/gi,'岗位要求')
  .replace(/\bownership\b/gi,'负责程度')
  .replace(/\bworking\b/gi,'可独立使用')
  .replace(/\bpartial\b/gi,'部分满足')
  .replace(/\bsatisfied\b/gi,'已满足')
  .replace(/\bunknown\b/gi,'待确认')
  .replace(/([\p{Script=Han}])\s+(?=[\p{Script=Han}])/gu,'$1');

const actionTargetName=(action:WhatIfAction,skillName:(value:string|null|undefined)=>string,requirementName:(value:string|null|undefined)=>string)=>action.learning_title&&!containsUuid(action.learning_title)&&!action.learning_title.includes('standard-position')
  ?planningText(action.learning_title)
  :action.canonical_name&&!containsUuid(action.canonical_name)&&!action.canonical_name.includes('standard-position')
    ?planningText(action.canonical_name)
    :action.action_type==='add_project_experience'
      ?requirementName(action.target_requirement_ids[0])
      :skillName(action.skill_id)||requirementName(action.target_requirement_ids[0]);

function ActionPlanView({gap,skillName,requirementName}:{gap:GapAnalysis;skillName:(value:string|null|undefined)=>string;requirementName:(value:string|null|undefined)=>string}){
  const minimal=gap.minimal_action_set;
  const actions=gap.candidate_actions||[];
  const routes=gap.learning_routes||[];
  const steps=gap.learning_path||[];
  if(!minimal&&!actions.length&&!routes.length&&!steps.length)return null;
  const selectedIds=new Set(minimal?.selected_action_ids||[]);
  const actionTime=(action:WhatIfAction)=>action.cost_band
    ?`粗略估算 ${action.cost_band.min_hours}–${action.cost_band.max_hours} 小时`
    :'尚无可靠时间区间';
  const actionById=new Map(actions.map(action=>[action.action_id,action]));
  const formalEntries=steps
    .slice()
    .sort((left,right)=>left.step_order-right.step_order)
    .map(step=>({step,action:step.source_action_id?actionById.get(step.source_action_id):undefined}))
    .filter((entry):entry is {step:NonNullable<GapAnalysis['learning_path'][number]>;action:WhatIfAction}=>Boolean(entry.action));
  const formalActionIds=new Set(formalEntries.map(entry=>entry.action.action_id));
  const leftover=actions.filter(action=>!formalActionIds.has(action.action_id));
  const stepStatus=(step:NonNullable<GapAnalysis['learning_path'][number]>)=>{
    if(step.planning_status==='blocked'||(step.blocked_reason_codes||[]).length)return {label:'前置能力待补充',tone:'warning' as const};
    if((step.prerequisite_states||[]).some(item=>item.status!=='satisfied'))return {label:'需先完成前置步骤',tone:'default' as const};
    return {label:'可开始',tone:'success' as const};
  };
  const renderCard=({action,step,index,showStatus}:{action?:WhatIfAction;step?:NonNullable<GapAnalysis['learning_path'][number]>;index:number;showStatus:boolean})=>{
    const targetLabel=action?actionTargetName(action,skillName,requirementName):planningText(step?.objective||'学习步骤');
    const deliverable=action?.deliverable
      ?planningText(action.deliverable,targetLabel)
      :step?.objective&&step.objective!==targetLabel?planningText(step.objective,targetLabel):'';
    const hours=action?actionTime(action):step?.estimated_hours!=null?`${step.estimated_hours} 小时`:'';
    const criteria=action?.acceptance_criteria?.length
      ?action.acceptance_criteria.map(item=>planningText(item,targetLabel)).join('；')
      :step?.completion_criteria?.length
        ?step.completion_criteria.join('；')
        :'形成一项可核验交付物';
    const status=showStatus&&step?stepStatus(step):null;
    return <Card key={action?.action_id||`step-${step?.step_order}`} hoverable className="learning-action-card">
      <div className="learning-action-card-kicker"><span>0{index+1}</span><Space size={5} wrap>
        {action&&<Tag>{actionLabels[action.action_type]||'提升行动'}</Tag>}
        {status&&<Tag color={status.tone}>{status.label}</Tag>}
      </Space></div>
      <Typography.Title level={5} title={targetLabel}>{targetLabel}</Typography.Title>
      {deliverable&&<div className="learning-action-deliverable"><strong>实践任务</strong><span>{deliverable}</span></div>}
      <div className="learning-action-summary">
        <div><small>预计投入</small><strong>{hours||'尚无可靠时间区间'}</strong></div>
        <div><small>完成标准</small><span>{criteria}</span></div>
        {action&&selectedIds.has(action.action_id)&&<Tag color="success">推荐优先</Tag>}
      </div>
    </Card>;
  };
  return <div className="learning-plan-action-panel">
    {formalEntries.length>0&&<>
      <div className="learning-plan-section-title"><Typography.Text strong>推荐学习路径</Typography.Text></div>
      <div className="learning-action-list">{formalEntries.map((entry,index)=>renderCard({action:entry.action,step:entry.step,index,showStatus:true}))}</div>
    </>}
    {leftover.length>0&&<>
      <div className="learning-plan-section-title"><Typography.Text strong>其他可选提升行动</Typography.Text></div>
      <div className="learning-action-list">{leftover.map((action,index)=>renderCard({action,index,showStatus:false}))}</div>
    </>}
  </div>;
}
const sourceTypeLabels:Record<string,string>={
  heuristic:'启发式',
  dataset_backed:'数据集来源',
  expert_estimate:'专家估计',
  manual:'人工录入',
  unknown:'未知来源',
};
const estimateStatusLabels:Record<string,string>={
  estimated:'规划模型估计',
  verified:'已验证来源',
  unknown:'未注明估算状态',
};

const hardGateText=(value:string|null|undefined)=>gateLabels[value||'']||'待确认';

const COST_ESTIMATE_TOOLTIP='时长为规划模型的估计值。';

type CostProvenanceItem={
  costSourceType?:string|null;
  costSourceRef?:string|null;
  estimateStatus?:string|null;
  costModel?:string|null;
};

function costProvenanceText({costSourceType,costSourceRef,estimateStatus,costModel}:CostProvenanceItem){
  const source=costSourceType||'unknown';
  const status=estimateStatus||'unknown';
  return {
    label:`${estimateStatusLabels[status]||'估算状态待确认'}`,
    tooltip:[
      `来源类型 ${sourceTypeLabels[source]||'未知来源'}`,
      costSourceRef?'来源记录已关联':null,
      costModel?`估算模型 ${readableSystemValue(costModel)}`:null,
      `估算状态 ${estimateStatusLabels[status]||'待确认'}`,
    ].filter(Boolean).join(' · '),
  };
}

function CostProvenance({hours,costSourceType,costSourceRef,estimateStatus,costModel}:CostProvenanceItem&{hours?:number|null}){
  const text=costProvenanceText({costSourceType,costSourceRef,estimateStatus,costModel});
  return <Tooltip title={`${COST_ESTIMATE_TOOLTIP} ${text.tooltip}`}>
    <span>{hours!=null?`${hours} 小时 · `:''}{text.label}</span>
  </Tooltip>;
}

const actionCostProvenance=(cost:ActionCost):CostProvenanceItem=>({
  costSourceType:cost.cost_source_type,
  costSourceRef:cost.cost_source_ref,
  estimateStatus:cost.estimate_status,
  costModel:cost.cost_model,
});

// Deduplicate real cost provenance (cost_model/source/status) so a route or a
// minimal action set never collapses to a single hardcoded or first entry.
function CostProvenanceList({items}:{items?:(CostProvenanceItem|null)[]}){
  const unique=[...new Map(
    (items||[]).filter((item):item is CostProvenanceItem=>Boolean(item)).map(item=>{
      const key=[item.costModel||'',item.costSourceType||'',item.costSourceRef||'',item.estimateStatus||''].join('|');
      return [key,item];
    }),
  ).values()];
  if(!unique.length)return <Typography.Text type="secondary">暂无估算依据</Typography.Text>;
  return <Space size={4} wrap>
    {unique.map((item,index)=><CostProvenance key={index} {...item}/>)}
  </Space>;
}


export function WhatIfWorkbench({evaluationId,gap,actionName,dimensionScores}:{evaluationId:string;gap:GapAnalysis;actionName?:(action:WhatIfAction)=>string;dimensionScores:DimensionScore[]}){
  const actions=useMemo(()=>gap.candidate_actions||[],[gap.candidate_actions]);
  const routes=useMemo(()=>gap.learning_routes||[],[gap.learning_routes]);
  const minimal=gap.minimal_action_set;
  const [scenarioByRoute,setScenarioByRoute]=useState<Record<string,DimensionScore[]|undefined>>({});
  const showMinimal=Boolean(minimal&&(minimal.target_reachable||minimal.status==='already_satisfied'||minimal.selected_action_ids.length>0));
  useEffect(()=>{
    let active=true;
    const candidateByActionId=new Map(
      (gap.candidate_actions||[]).map(action=>[action.action_id,action])
    );
    const missing=routes.filter(route=>{
      if(route.scenario_dimension_scores?.length)return false;
      return scenarioByRoute[route.route_type]===undefined;
    });
    if(!missing.length)return;
    missing.forEach(route=>{
      const routeActions=route.action_ids
        .map(actionId=>candidateByActionId.get(actionId))
        .filter((action):action is WhatIfAction=>Boolean(action));
      if(!routeActions.length)return;
      evaluateMatchWhatIf(evaluationId,routeActions)
        .then(result=>{
          if(!active)return;
          const evaluation=result.projected_evaluation||result.scenario_evaluation;
          setScenarioByRoute(previous=>({...previous,[route.route_type]:evaluation?.final_match_result?.dimension_scores||[]}));
        })
        .catch(()=>{
          if(active)setScenarioByRoute(previous=>({...previous,[route.route_type]:[]}));
        });
    });
    return()=>{active=false};
  },[evaluationId,gap,routes,scenarioByRoute]);
  if(!actions.length&&!routes.length&&!minimal){
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前报告没有返回候选行动或正式路线"/>;
  }
  const byActionId=new Map(actions.map(action=>[action.action_id,action]));
  const routeActionName=(route:LearningRoute)=>{
    const names=route.action_ids.map(actionId=>{
      const action=byActionId.get(actionId);
      if(!action)return null;
      return actionName?actionName(action):(action.learning_title||readableSkillName(action.canonical_name)||action.skill_id||'行动');
    }).filter((name):name is string=>Boolean(name));
    return names.length?names.join(' + '):'暂无行动明细';
  };
  const routeAdvice=(route:LearningRoute,scenarioScores:DimensionScore[])=>{
    const compareByName=new Map(scenarioScores.map(item=>[item.dimension,item]));
    const changes=dimensionScores.map(row=>{
      const compare=compareByName.get(row.dimension);
      if(!compare||compare.score===null||row.score===null)return null;
      const delta=Math.round(compare.score)-Math.round(row.score);
      return delta===0?null:{rawDimension:row.dimension,label:dimensionLabel(row.dimension),delta};
    }).filter((item):item is {rawDimension:string;label:string;delta:number}=>Boolean(item));
    const positive=changes.filter(item=>item.delta>0);
    const negative=changes.filter(item=>item.delta<0);
    if(!positive.length&&!negative.length)return null;
    const actionNames=route.action_ids.map(actionId=>{
      const action=byActionId.get(actionId);
      if(!action)return null;
      return actionName?actionName(action):(action.learning_title||readableSkillName(action.canonical_name)||action.skill_id||null);
    }).filter((name):name is string=>Boolean(name));
    const dimensionNarrative:Record<string,{label:string;advice:string}>={
      hard_conditions:{label:'硬性门槛',advice:'优先解锁硬性门槛，把之前未满足的准入条件转正，避免匹配被一票否决'},
      required_skills:{label:'核心技能',advice:'重点补强岗位点名的核心技能，把技能覆盖从“缺”变“有”，直接补上短板'},
      projects:{label:'实践证据',advice:'重在沉淀可验收的实践证据，让报告里有可运行、可核验的产出，证明“做过且能交付”'},
      capability_level:{label:'能力深度',advice:'推动能力深度提升，从“了解/用过”推进到符合目标等级的独立掌握'},
      responsibilities:{label:'职责贴合',advice:'拉近职责经历与岗位的贴合度，让已有经验按岗位职责口径重新对齐'},
      bonus_transferable:{label:'加分能力',advice:'激活可加分能力，把现有经验中能加分但尚未识别的部分转化为匹配增量'},
      business_scenarios:{label:'业务场景',advice:'补强业务场景匹配，让能力画像落在岗位的真实语境里'},
      requirement_groups:{label:'组合要求',advice:'补足组合要求，让多个条件在同一条路径里一并满足'},
      semantic:{label:'语义匹配',advice:'增强语义匹配，让画像与岗位要求的关联表达得更充分'},
    };
    const routeLeads:Record<LearningRoute['route_type'],string>={
      fastest_employment:'这条路线优先用较短投入见效',
      budget_max_gain:'这条路线在预算内组合高价值行动',
      foundation_first:'这条路线先把工程基础打牢',
    };
    const actionRef=actionNames.length?`「${actionNames.join('」「')}」等学习行动`:'这些学习行动';
    let narrative:string;
    if(positive.length){
      const dimensionPriority=['hard_conditions','required_skills','projects','capability_level','responsibilities','bonus_transferable','business_scenarios','requirement_groups','semantic'];
      const positiveByRaw=new Map(positive.map(item=>[item.rawDimension,item]));
      const primaryRaw=dimensionPriority.find(raw=>positiveByRaw.has(raw))??positive[0].rawDimension;
      const primary=dimensionNarrative[primaryRaw]??{label:positive[0].label,advice:'让匹配画像在岗位要求下更完整'};
      const primarySentence=`${routeLeads[route.route_type]}：围绕${actionRef}，${primary.advice}。`;
      const otherLabels=positive.filter(item=>item.rawDimension!==primaryRaw).map(item=>item.label);
      const secondary=otherLabels.length?`同时也会带动${otherLabels.join('、')}等维度一起提升。`:'';
      narrative=[primarySentence,secondary].filter(Boolean).join(' ');
    }else{
      narrative='这条路线暂未带来可量化的正面提升，建议优先检查行动组合是否覆盖当前短板。';
    }
    const negativeSentence=negative.length?`需留意：${negative.map(item=>`${item.label}可能出现回退`).join('、')}。`:'';
    return {narrative,negativeSentence,hasNegative:negative.length>0};
  };

  return <Space direction="vertical" size="middle" style={{width:'100%'}}>
    {showMinimal&&minimal&&<div className="match-gap-item">
      <div className="match-gap-head">
        <Typography.Text strong>最小可行行动集</Typography.Text>
        <Tag color={minimal.status==='hard_blocked'?'error':minimal.target_reachable?'success':'warning'}>{statusText(minimal.status)}</Tag>
      </div>
      <Descriptions size="small" column={{xs:1,md:2}} items={[
        {key:'count',label:'行动数',children:minimal.minimum_action_count},
        {key:'cost',label:'预计成本',children:<span>{minimal.total_cost_hours} 小时 · <CostProvenanceList items={(minimal.action_costs||[]).filter(cost=>cost.selected).map(actionCostProvenance)}/></span>},
        {key:'score',label:'模型评分变化',children:`${roundedScoreText(minimal.baseline_score)} → ${roundedScoreText(minimal.modeled_final_score??minimal.scenario_score,'未达到')}（Δ ${roundedScoreDeltaText(minimal.baseline_score,minimal.modeled_final_score??minimal.scenario_score)}）`},
        {key:'semantics',label:'结果语义',children:'模型反事实重评分'},
        {key:'observed',label:'真实观察结果',children:'否（模拟）'},
        {key:'gate',label:'硬性门槛',children:minimal.scenario_hard_gate_status&&minimal.scenario_hard_gate_status!==minimal.baseline_hard_gate_status?`${hardGateText(minimal.baseline_hard_gate_status)} → ${hardGateText(minimal.scenario_hard_gate_status)}`:hardGateText(minimal.baseline_hard_gate_status)},
      ]}/>
    </div>}
    {routes.length>0&&<div className="match-route-section">
      <div className="match-route-section-head">
        <div><Typography.Title level={5}>推荐提升路线</Typography.Title><Typography.Text type="secondary">对比假设分析后的能力面板，查看不同行动组合的提升建议。</Typography.Text></div>
        <Tag color="success">已生成 {routes.length} 条路线</Tag>
      </div>
      <div className="match-route-grid">
        {routes.map((route,index)=>{
          const scenarioScores=route.scenario_dimension_scores||scenarioByRoute[route.route_type]||[];
          const advice=routeAdvice(route,scenarioScores);
          return <Card hoverable className={`match-route-card is-${route.route_type}`} key={route.route_type}>
            <div className="match-route-card-kicker"><span>0{index+1}</span><Tag color={route.route_type==='budget_max_gain'?'success':'default'}>{routeHighlights[route.route_type]}</Tag></div>
            <div className="match-route-card-head"><Typography.Title level={5}>{routeLabels[route.route_type]}</Typography.Title></div>
            <Typography.Paragraph className="match-route-card-description">{routeDescriptions[route.route_type]}</Typography.Paragraph>
            <div className="match-route-summary">
              <div><span>预计投入时间</span><strong>{route.total_cost_hours} 小时</strong></div>
              <div><span>行动组合</span><strong>{routeActionName(route)}</strong></div>
            </div>
            <div className="match-route-radar"><MatchDimensionRadar dimensionScores={dimensionScores} compareScores={scenarioScores} baselineSeriesName="当前能力" compareSeriesName="假设分析后"/></div>
            {advice&&<div className="match-route-advice"><Typography.Text strong>提升建议</Typography.Text>
              <span>{advice.narrative}</span>
              {advice.hasNegative&&<span className="is-negative">{advice.negativeSentence}</span>}
            </div>}
          </Card>;
        })}
      </div>
    </div>}
  </Space>;
}

function EvidenceDeletionWorkbench({
  evaluationId,
  evidenceSourceIds,
  disabled,
}:{
  evaluationId:string;
  evidenceSourceIds:string[];
  disabled:boolean;
}){
  const [selectedIds,setSelectedIds]=useState<string[]>([]);
  const [result,setResult]=useState<EvidenceDeletionResult>();
  const [running,setRunning]=useState(false);
  const [runError,setRunError]=useState<string>();

  const run=()=>{
    if(!selectedIds.length)return;
    setRunning(true);
    setRunError(undefined);
    setResult(undefined);
    evaluateEvidenceDeletion(evaluationId,'critical',selectedIds)
      .then(setResult)
      .catch(()=>setRunError('当前无法完成删除重算，请稍后重试。'))
      .finally(()=>setRunning(false));
  };

  if(!evidenceSourceIds.length){
    return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前报告没有被正式评分器使用的候选人证据"/>;
  }

  return <Space direction="vertical" size="middle" style={{width:'100%'}}>
    <Alert
      type="info"
      title="受控删除测试"
      description="这里只允许选择正式评分结果中被使用的关键证据；删除后使用同一评分器重算，不会修改简历。"
    />
    <Checkbox.Group
      value={selectedIds}
      onChange={values=>setSelectedIds((values as string[]).slice(0,32))}
    >
      <Space direction="vertical">
        {evidenceSourceIds.map((sourceId,index)=><Checkbox key={sourceId} value={sourceId}>
          <Typography.Text>关键证据 {index+1}</Typography.Text>
        </Checkbox>)}
      </Space>
    </Checkbox.Group>
    <Space wrap>
      <Button type="primary" loading={running} disabled={disabled||!selectedIds.length} onClick={run}>运行删除重算</Button>
      <Button disabled={!selectedIds.length} onClick={()=>setSelectedIds([])}>清空</Button>
      <Typography.Text type="secondary">单次最多 32 条证据</Typography.Text>
    </Space>
    {disabled&&<Alert type="warning" showIcon title="历史报告不可测试" description="请先重新匹配，再基于当前版本运行删除测试。"/>}
    {runError&&<Alert type="error" showIcon title="删除测试失败" description={runError}/>}
    {result&&result.generation_status==='rejected'&&<Alert
      type="error"
      showIcon
      title="删除测试被拒绝"
      description="当前证据不足以完成判断，请稍后重试。"
    />}
    {result&&result.generation_status==='completed'&&<div className="match-gap-item">
      <div className="match-gap-head">
        <Typography.Text strong>重算结果</Typography.Text>
        <Tag color={result.faithfulness_status==='faithful'?'success':'warning'}>{result.faithfulness_status==='faithful'?'解释与证据一致':'需要复核'}</Tag>
      </div>
      <Descriptions size="small" column={{xs:1,md:2}} items={[
        {key:'score',label:'匹配分',children:`${roundedScoreText(result.baseline_score)} → ${roundedScoreText(result.ablated_score)}（Δ ${roundedScoreText(result.score_delta)}）`},
        {key:'gate',label:'硬性门槛',children:`${hardGateText(result.baseline_hard_gate_status)} → ${hardGateText(result.ablated_hard_gate_status)}`},
        {key:'comprehensiveness',label:'全面性',children:ratioPercent(result.comprehensiveness)},
        {key:'sufficiency',label:'充分性',children:ratioPercent(result.sufficiency)},
        {key:'unsupported',label:'无依据理由率',children:ratioPercent(result.unsupported_reason_rate)},
        {key:'run',label:'删除任务',children:<Typography.Text copyable={{text:result.deletion_run_id}}>已记录</Typography.Text>},
        {key:'baseline',label:'基准评估',children:'已关联'},
        {key:'profiles',label:'画像版本',children:`简历画像 ${result.cv_profile_version?'已记录':'缺失'} · 岗位画像 ${result.position_profile_version?'已记录':'缺失'}`},
        {key:'scorer',label:'评分规则',children:result.scoring_algorithm_version&&result.scoring_config_version?'已记录':'缺失'},
        {key:'algorithm',label:'证据删除重算算法',children:result.algorithm_version?'已记录':'缺失'},
        {key:'gaps',label:'新增差距项',children:result.added_gap_ids.length?`${result.added_gap_ids.length} 项`:'无'},
        {key:'actions',label:'新增行动项',children:result.added_action_ids.length?`${result.added_action_ids.length} 项`:'无'},
      ]}/>
      {result.faithfulness_status!=='faithful'&&<Alert
        type="warning"
        showIcon
        title="解释忠实度需复核"
        description="关键证据删除后未产生预注册规则预期的变化；该结果属于诊断信号，不能作为已通过实验的结论。"
      />}
    </div>}
  </Space>;
}

export function MatchEvaluationPage(){
  const {evaluationId=''}=useParams();
  const navigate=useNavigate();
  const location=useLocation();
  const routedPositionName=typeof (location.state as {positionName?:unknown}|null)?.positionName==='string'
    ?(location.state as {positionName:string}).positionName
    :'';
  const reportBase=location.pathname.startsWith('/enterprise/')?'/enterprise/recruitment/reports':'/matching/reports';
  const [searchParams,setSearchParams]=useSearchParams();
  const pathId=searchParams.get('pathId')||'';
  const [report,setReport]=useState<EvaluationReport>();
  const [error,setError]=useState<ApiError>();
  const [generatingPath,setGeneratingPath]=useState(false);
  const [pathError,setPathError]=useState<ApiError>();
  const [pathLoadError,setPathLoadError]=useState<ApiError>();
  const [learningPath,setLearningPath]=useState<LearningPath>();
  const [pathLoading,setPathLoading]=useState(false);
  const [exportError,setExportError]=useState<ApiError>();
  const [exporting,setExporting]=useState(false);
  const [timeBudgetHours,setTimeBudgetHours]=useState<number|null>(40);
  const [positions,setPositions]=useState<MatchPosition[]>([]);
  const [positionName,setPositionName]=useState(routedPositionName);
  const [evidenceOpen,setEvidenceOpen]=useState<Evidence>();
  const [auditOpen,setAuditOpen]=useState(false);
  const [gapPriorityFilter,setGapPriorityFilter]=useState('all');
  const [gapTypeFilter,setGapTypeFilter]=useState('all');
  const [gapPage,setGapPage]=useState(1);

  const load=useCallback(()=>{
    setError(undefined);
    if(!evaluationId||evaluationId==='null'||evaluationId==='undefined'){
      setError(new ApiError(400,'匹配报告编号无效，请返回岗位匹配重新选择报告。'));
      return;
    }
    getMatchEvaluation(evaluationId)
      .then(value=>{
        setReport(value);
        if(value.lineage?.position_name)setPositionName(readablePositionName(value.lineage.position_name));
      })
      .catch(reason=>setError(reason as ApiError));
  },[evaluationId]);

  useEffect(()=>{
    const timer=window.setTimeout(load,0);
    return()=>window.clearTimeout(timer);
  },[load]);

  useEffect(()=>{
    if(!report)return;
    // 评估仍在生成时轮询等待正式评分，避免把中间态展示为最终结论。
    if(report.evaluation.evaluation_status==='completed'&&report.evaluation.final_match_result)return;
    const timer=window.setTimeout(load,800);
    return()=>window.clearTimeout(timer);
  },[load,report]);

  useEffect(()=>{
    if(!report)return;
    const targetId=report.lineage?.position_id||report.evaluation.position_profile_id||report.evaluation.final_match_result?.position_profile_id;
    let active=true;
    // 工作台已经持有岗位目录时会通过路由状态立即提供名称；目录请求只负责校正，
    // 不再清空名称或阻塞整份历史报告的展示。
    const timer=window.setTimeout(()=>{
      if(!targetId){setPositionName('岗位名称未提供');return}
      if(routedPositionName)setPositionName(routedPositionName);
      if(report.lineage?.target_type==='enterprise_job'){
        if(routedPositionName||report.lineage?.position_name){
          setPositionName(readablePositionName(routedPositionName||report.lineage.position_name||''));
          return;
        }
        listEnterpriseJobs().then(items=>{
          const job=items.find(item=>item.enterprise_job_id===targetId);
          if(active)setPositionName(job?.title||'岗位名称未找到');
        }).catch(()=>{if(active)setPositionName('岗位名称读取失败')});
      }else{
        listMatchPositions().then(items=>{
          const position=items.find(item=>item.position_id===targetId);
          if(active){
            setPositions(items);
            setPositionName(position?readablePositionName(position.position_name):'岗位名称未找到');
          }
        }).catch(()=>{if(active)setPositionName('岗位名称读取失败')});
      }
    },0);
    return()=>{active=false;window.clearTimeout(timer)};
  },[report,routedPositionName]);

  useEffect(()=>{
    if(!pathId)return;
    let active=true;
    const timer=window.setTimeout(()=>{
      setPathLoading(true);
      setPathLoadError(undefined);
      getLearningPath(pathId).then(path=>{
        if(active)setLearningPath(path);
      }).catch(reason=>{
        if(active){
          setLearningPath(undefined);
          setPathLoadError(reason as ApiError);
        }
      }).finally(()=>{if(active)setPathLoading(false)});
    },0);
    return()=>{active=false;window.clearTimeout(timer)};
  },[pathId]);

  if(error)return <div className="state-panel match-report-load-failure" role="alert">
    <Typography.Title level={3}>匹配报告加载失败</Typography.Title>
    <Typography.Paragraph>{error.message}</Typography.Paragraph>
    {error.traceId&&<Typography.Text type="secondary">追踪编号：{error.traceId}</Typography.Text>}
    <Space wrap>
      <Button type="primary" onClick={load}>重试</Button>
      <Button onClick={()=>navigate(reportBase.startsWith('/enterprise/')?'/enterprise/recruitment':'/matching')}>返回岗位匹配</Button>
    </Space>
  </div>;
  if(!report)return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>;
  const displayPositionName=positionName||'岗位名称读取中…';

  const evaluation=report.evaluation;
  const final=evaluation.final_match_result;
  if(evaluation.evaluation_status!=='completed'||!final){
    return <div className="center-loading" aria-live="polite"><Spin size="large"/></div>;
  }
  const gap: GapAnalysis=report.gap_analysis;
  const lineage: MatchLineage|null=report.lineage;
  const versions: MatchVersions=report.versions||{};
  const evaluationAlgorithm=versions.evaluation_algorithm_version||evaluation.algorithm_version||final?.algorithm_version;
  const scoringConfig=versions.scoring_config_version||final?.scoring_config_version;
  const cvProfileId=evaluation.cv_profile_id||final?.cv_profile_id;
  const cvProfileVersion=evaluation.cv_profile_version;
  const positionProfileId=evaluation.position_profile_id||final?.position_profile_id;
  const positionProfileVersion=evaluation.position_profile_version;
  const graphVersion=versions.position_graph_version||final?.position_graph_version;

  const missingHeader=([
    ['评估 ID',report.evaluation_id],
    ['简历 ID',lineage?.resume_id],
    ['已验证简历快照 ID',lineage?.validated_cv_snapshot_id],
    ['岗位 ID',lineage?.position_id],
    ['岗位图谱版本',graphVersion],
    ['执行服务',lineage?.provider],
    ['匹配算法版本',evaluationAlgorithm],
    ['评分配置版本',scoringConfig],
    ['简历画像 ID',cvProfileId],
    ['岗位画像 ID',positionProfileId],
  ] as Array<[string,string|null|undefined]>).filter(([,value])=>!value);

  const collectedEvidence=()=>{
    const rows:Evidence[]=[];
    const push=(items:Evidence[]|undefined)=>{if(items)for(const item of items)rows.push(item)};
    for(const skill of evaluation.skill_results){
      push(skill.candidate_evidence);push(skill.position_evidence);
    }
    for(const item of evaluation.hard_constraint_results){
      push(item.candidate_evidence);push(item.position_evidence);
    }
    for(const item of [...evaluation.responsibility_results,...evaluation.project_results,...evaluation.scenario_results]){
      push(item.candidate_evidence);push(item.position_evidence);
    }
    for(const item of gap.prioritized_gaps)push(item.evidence);
    return uniqueEvidence(rows);
  };
  const allEvidence=collectedEvidence();
  const candidateEvidence=allEvidence.filter(item=>item.source_object_type==='validated_cv_snapshot');
  const criticalEvidenceIds=Array.from(new Set([
    ...(evaluation.hard_constraint_results||[])
      .filter(item=>['pass','partial'].includes(item.status))
      .flatMap(item=>item.candidate_evidence||[]),
    ...(evaluation.skill_results||[])
      .filter(item=>!['missing','unknown','unresolved'].includes(item.match_status))
      .flatMap(item=>item.candidate_evidence||[]),
    ...[
      ...(evaluation.responsibility_results||[]),
      ...(evaluation.project_results||[]),
      ...(evaluation.scenario_results||[]),
    ]
      .filter(item=>!['missing','unknown','unresolved'].includes(item.match_status||'unknown'))
      .flatMap(item=>item.candidate_evidence||[]),
  ].map(item=>item.source_fragment_id).filter(Boolean))).sort();
  const positionEvidence=allEvidence.filter(item=>item.source_object_type==='position_profile');
  const gapEvidence=allEvidence.filter(item=>item.source_object_type==='matching_evidence');
  const unresolvedEvidence=allEvidence.filter(item=>!['validated_cv_snapshot','position_profile','matching_evidence'].includes(item.source_object_type));
  const matchedSkills=evaluation.skill_results.filter(item=>['matched','weak','declared_only','partial'].includes(item.match_status));
  const uncertainItems=final?.uncertain_items??[];
  const dimensionScores=final?.dimension_scores??[];
  const topStrengths=(final?.strengths||[]).slice(0,5);
  const displayGaps=gap.prioritized_gaps.filter(item=>{
    if(item.gap_type!=='project_gap')return true;
    const project=evaluation.project_results.find(row=>row.requirement_id===item.requirement_id);
    const requirement=project
      ?normalizeDisplayText(Array.isArray(project.position_requirement)?project.position_requirement.join('、'):project.position_requirement)
      :'';
    return requirement.length<=180;
  });
  const topGaps=displayGaps.slice(0,5);
  const filteredGaps=displayGaps.filter(item=>(
    (gapPriorityFilter==='all'||item.priority===gapPriorityFilter)
    &&(gapTypeFilter==='all'||item.gap_type===gapTypeFilter)
  ));
  const gapPageSize=4;
  const gapPageCount=Math.max(1,Math.ceil(filteredGaps.length/gapPageSize));
  const displayedGapPage=Math.min(gapPage,gapPageCount);
  const pagedGaps=filteredGaps.slice((displayedGapPage-1)*gapPageSize,displayedGapPage*gapPageSize);
  const skillNameByRequirement=new Map(
    evaluation.skill_results.map(item=>[item.requirement_id,readableSkillName(item.skill_name||item.skill_id)])
  );
  const gapName=(item:PrioritizedGap)=>{
    const hard=evaluation.hard_constraint_results.find(row=>row.requirement_id===item.requirement_id);
    if(hard)return readableHardConstraintValue(hard.constraint_type,hard.required_value)||hardConstraintTypeLabels[hard.constraint_type]||'硬性条件要求';
    const responsibility=evaluation.responsibility_results.find(row=>row.requirement_id===item.requirement_id);
    const experience=[...(evaluation.project_results||[]),...(evaluation.scenario_results||[])].find(row=>row.requirement_id===item.requirement_id);
    const skill=evaluation.skill_results.find(row=>row.skill_id===item.skill_id||row.requirement_id===item.requirement_id);
    const responsibilityName=responsibility?normalizeDisplayText(Array.isArray(responsibility.position_requirement)?responsibility.position_requirement.join('、'):responsibility.position_requirement):'';
    const experienceName=experience?readableRequirement(experience.position_requirement,evaluation.skill_results,item.gap_type==='project_gap'?'综合实践证据':'业务场景要求'):'';
    const name=skillNameByRequirement.get(item.requirement_id)||responsibilityName||experienceName||skill?.skill_name||item.skill_id;
    return name&&!looksLikeInternalId(name)?readableSkillName(name):'岗位能力要求';
  };
  const skillName=(value:string|null|undefined)=>{
    if(!value)return '相关岗位要求';
    const name=evaluation.skill_results.find(item=>item.skill_id===value||item.requirement_id===value)?.skill_name;
    return name&&!looksLikeInternalId(name)?readableSkillName(name):looksLikeInternalId(value)?'岗位能力要求':readableSkillName(value);
  };
  const requirementName=(value:string|null|undefined)=>{
    if(!value)return '相关岗位要求';
    const hard=evaluation.hard_constraint_results.find(row=>row.requirement_id===value);
    if(hard)return readableHardConstraintValue(hard.constraint_type,hard.required_value)||hardConstraintTypeLabels[hard.constraint_type]||'硬性条件要求';
    const responsibility=evaluation.responsibility_results.find(row=>row.requirement_id===value);
    if(responsibility){
      const name=normalizeDisplayText(Array.isArray(responsibility.position_requirement)?responsibility.position_requirement.join('、'):responsibility.position_requirement);
      if(name)return name;
    }
    const experience=[...(evaluation.project_results||[]),...(evaluation.scenario_results||[])].find(row=>row.requirement_id===value);
    if(experience)return readableRequirement(experience.position_requirement,evaluation.skill_results,'岗位要求');
    const skill=evaluation.skill_results.find(row=>row.skill_id===value||row.requirement_id===value);
    if(skill)return readableSkillName(skill.skill_name||skill.skill_id);
    return looksLikeInternalId(value)?'相关岗位要求':normalizeDisplayText(value);
  };
  const staleReasons=(report.stale_reason_codes||[]).map(code=>staleReasonLabels[code]||'相关数据已更新');

  const learningPathDeferred=gap.error_code==='LEARNING_PATH_NOT_REQUESTED';
  const gapFailed=!learningPathDeferred&&(Boolean(gap.error_code)||gap.generation_status==='rejected');
  const learningPathFailed=gapFailed;
  const generatePath=async()=>{
    setGeneratingPath(true);
    setPathError(undefined);
    setExportError(undefined);
    try{
      const path=await createLearningPath(report.evaluation_id,lineage?.position_id||undefined,timeBudgetHours??undefined);
      if(!path.gap_analysis)throw new Error('服务未返回学习路线');
      if(path.gap_analysis.generation_status!=='completed'){
        const code=path.gap_analysis.error_code||'LEARNING_PATH_GENERATION_FAILED';
        throw new ApiError(409,learningPathFailureLabels[code]||'学习路径生成失败',undefined,{error_code:code});
      }
      const noActionOutcome=path.gap_analysis.minimal_action_set?.target_reachable===false;
      if(
        (path.gap_analysis.prioritized_gaps?.length||0)>0
        &&!(path.gap_analysis.learning_path?.length||0)
        &&!noActionOutcome
      ){
        throw new ApiError(409,'存在能力差距，但服务没有返回学习步骤',undefined,{error_code:'LEARNING_PATH_EMPTY'});
      }
      setLearningPath(path);
      setReport(current=>current?{...current,gap_analysis:{...current.gap_analysis,...path.gap_analysis}}:current);
      setSearchParams(current=>{
        const next=new URLSearchParams(current);
        next.set('pathId',path.path_id);
        return next;
      },{replace:true});
    }catch(reason){
      setPathError(reason instanceof ApiError?reason:new ApiError(0,reason instanceof Error?reason.message:'学习路线生成失败'));
    }finally{
      setGeneratingPath(false);
    }
  };
  const exportPath=async()=>{
    if(!learningPath)return;
    setExporting(true);
    setExportError(undefined);
    try{
      const exported=await exportLearningPath(learningPath.path_id);
      const blob=new Blob([JSON.stringify(exported.learning_path,null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob);
      const anchor=document.createElement('a');
      anchor.href=url;
      anchor.download=`learning-path-${learningPath.path_id.replace(/[^a-zA-Z0-9_-]/g,'_')}.json`;
      anchor.click();
      URL.revokeObjectURL(url);
    }catch(reason){
      setExportError(reason as ApiError);
    }finally{
      setExporting(false);
    }
  };

  return <>
    <EvidenceDeepLinkFocus resourceId={evaluationId}/>
    <div className="page-heading page-heading-row match-report-page-heading">
      <div>
        <Typography.Title level={2}>岗位匹配报告</Typography.Title>
        <Space wrap size={8}>
          <Typography.Text type="secondary">快速判断匹配结论、关键依据与下一步行动</Typography.Text>
          <Tag>{matchingMethodLabel(resolveMatchingMethod(report))}</Tag>
        </Space>
      </div>
      <Space wrap>
        {graphVersion&&<Button
          icon={<CommentOutlined/>}
          onClick={()=>navigate(`/evidence/assistant?${new URLSearchParams({
            objectType:'matching_evaluation',
            objectId:report.evaluation_id,
            objectVersion:graphVersion,
            versionKind:'graph_version',
            evidenceTypes:'matching_evidence,jd_evidence,cv_evidence',
            returnTo:`/matching/reports/${encodeURIComponent(report.evaluation_id)}`,
          })}`)}
        >
          证据问答
        </Button>}
        <Button icon={<LockOutlined/>} onClick={()=>setAuditOpen(true)}>技术与审计信息</Button>
        <Button onClick={()=>navigate('/matching')}>返回匹配工作台</Button>
      </Space>
    </div>

    {report.stale&&<Alert
      type="warning"
      showIcon
      title="这份报告需要重新计算"
      description={`${staleReasons.join('、')||'相关数据已更新'}。下方内容仅供参考，请重新匹配以获得当前结果。`}
      action={<Button onClick={()=>navigate(`/matching?resumeId=${encodeURIComponent(lineage?.resume_id||'')}&positionId=${encodeURIComponent(lineage?.position_id||'')}`)}>重新匹配</Button>}
    />}

    <MatchReportOverview
      positionName={displayPositionName}
      final={final}
      report={report}
      summary={evaluation.summary}
      dimensionScores={dimensionScores}
      topActionName={topGaps[0]?gapName(topGaps[0]):null}
    />

    <MatchReportSectionNav/>

    <MatchKeyInsights
      strengths={topStrengths}
      fallbackSkills={matchedSkills}
      gaps={topGaps}
      gapName={gapName}
      gapFailed={gapFailed}
    />

    <MatchCapabilityDetails
      evaluation={evaluation}
      responsibilityContent={<ResponsibilityDetailList items={evaluation.responsibility_results||[]}/>}
    />

    <section id="match-report-gaps-actions" className="match-report-section match-report-gaps-actions">
      <div className="match-report-section-head"><div><Typography.Title level={4}>优先差距</Typography.Title></div></div>
      <div className="match-priority-gaps">
        <div>
          {!gapFailed&&displayGaps.length>0&&<div className="match-gap-filters">
            <Select aria-label="按优先级筛选差距" value={gapPriorityFilter} options={[
              {value:'all',label:'全部优先级'},
              {value:'critical',label:'最高优先级'},
              {value:'high',label:'高优先级'},
              {value:'medium',label:'中优先级'},
              {value:'low',label:'低优先级'},
            ]} onChange={value=>{setGapPriorityFilter(value);setGapPage(1)}}/>
            <Select aria-label="按差距类型筛选" value={gapTypeFilter} options={[
              {value:'all',label:'全部差距类型'},
              ...Array.from(new Set(displayGaps.map(item=>item.gap_type))).map(value=>({value,label:gapTypeLabels[value]||'其他能力差距'})),
            ]} onChange={value=>{setGapTypeFilter(value);setGapPage(1)}}/>
          </div>}
          {gapFailed?<Alert type="error" showIcon title="差距分析失败" description="当前证据不足以完成判断，请稍后重试。"/>:pagedGaps.length?<><div className="match-gap-page">{pagedGaps.map((item,index)=><GapCard key={`${item.requirement_id}-${index}`} gap={item} name={gapName(item)}/>)}</div>{filteredGaps.length>gapPageSize&&<Pagination className="match-gap-pagination" current={displayedGapPage} pageSize={gapPageSize} total={filteredGaps.length} showSizeChanger={false} onChange={setGapPage}/>}</>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={displayGaps.length?'当前筛选条件下没有差距':'没有结构化差距'}/>}
        </div>
      </div>
      <div id="learning-path" className="learning-plan-workspace match-report-subblock">
        <div className="match-report-section-head"><div><Typography.Title level={4}>学习路径</Typography.Title></div>
          <Space wrap>
            <Space.Compact><InputNumber aria-label="时间预算（小时）" min={1} value={timeBudgetHours} onChange={setTimeBudgetHours} placeholder="时间预算"/><Button disabled>小时</Button></Space.Compact>
            <Button type="primary" loading={generatingPath} disabled={report.stale} onClick={generatePath}>按预算优化</Button>
          </Space>
        </div>
        {report.stale&&<Alert type="warning" showIcon title="建议需要更新" description="简历或岗位信息已变化，重新匹配后会同步生成新的学习建议。"/>}
        {pathError&&<Alert type="error" showIcon title={learningPathErrorTitle(pathError,'create')} description={(pathError.errorCode&&learningPathFailureLabels[pathError.errorCode])||pathError.message||'当前无法完成该操作，请稍后重试。'}/>}
        {pathLoadError&&<Alert type="error" showIcon title={learningPathErrorTitle(pathLoadError,'load')} description="当前无法完成该操作，请稍后重试。"/>}
        {exportError&&<Alert type="error" showIcon title={learningPathErrorTitle(exportError,'export')} description="当前无法完成该操作，请稍后重试。"/>}
        <div className="learning-plan-current">
          {pathLoading?<div className="state-panel loading-state"><Spin/><span className="state-panel-hint">正在恢复学习建议…</span></div>:learningPath?<LearningPathDetail path={learningPath} targetPositionName={positions.find(position=>position.position_id===learningPath.target_position_id)?.position_name||displayPositionName} skillName={skillName} requirementName={requirementName} onExport={exportPath} exporting={exporting}/>:gap.generation_status==='completed'&&(gap.minimal_action_set||(gap.candidate_actions||[]).length||(gap.learning_routes||[]).length)?<div className="learning-plan-detail"><ActionPlanView gap={gap} skillName={skillName} requirementName={requirementName}/></div>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本次匹配暂未生成学习建议"/>}
        </div>
      </div>
      <div className="match-report-what-if-inline">
        <div className="match-report-section-head"><div><Typography.Title level={4}>假设分析</Typography.Title></div></div>
        {learningPathDeferred?<Typography.Text type="secondary">生成学习路线后可运行假设分析。</Typography.Text>:learningPathFailed?<Alert type="error" showIcon title="假设分析暂不可用" description="差距分析未完成。"/>:<WhatIfWorkbench evaluationId={report.evaluation_id} gap={gap} dimensionScores={dimensionScores} actionName={action=>actionTargetName(action,skillName,requirementName)}/>}
      </div>
    </section>

    <section id="match-report-trust" className="match-report-section match-report-trust">
      <div className="match-report-section-head"><div><Typography.Title level={4}>结果可信度</Typography.Title><Typography.Text type="secondary">共使用 {allEvidence.length} 条证据，{uncertainItems.length} 项结论证据不足</Typography.Text></div>
        <Button icon={<FileSearchOutlined/>} onClick={()=>document.getElementById('match-report-details')?.scrollIntoView?.({behavior:'smooth',block:'start'})}>查看证据详情</Button>
      </div>
      <div className="match-trust-summary" aria-label="证据可信度摘要">
        <div><span>已使用证据</span><strong>{allEvidence.length}</strong></div>
        <div><span>简历证据</span><strong>{candidateEvidence.length}</strong></div>
        <div><span>岗位证据</span><strong>{positionEvidence.length}</strong></div>
        <div><span>待确认结论</span><strong>{uncertainItems.length}</strong></div>
        <div><span>可执行差距</span><strong>{displayGaps.length}</strong></div>
      </div>
    </section>

    <div id="match-report-details"><Collapse className="match-report-details" items={[
      {key:'dimensions',label:'完整维度评分',children:dimensionScores.length?<div className="match-dimension-score-list">{dimensionScores.map((row:DimensionScore)=><div key={row.dimension}><span>{dimensionLabel(row.dimension)}</span><b>{scorePercent(row.score)}</b><small>置信度 {Math.round(row.confidence*100)}% · 有效权重 {Math.round(row.effective_weight<=1?row.effective_weight*100:row.effective_weight)}% · 已评分 {row.scored_count}/{row.applicable_count} · 待确认 {row.uncertain_count}</small></div>)}</div>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无可用维度分"/>},
      {key:'gaps',label:`全部技能差距（${gap.prioritized_gaps.length}）`,children:gap.prioritized_gaps.length?gap.prioritized_gaps.map((item,index)=><GapCard key={`${item.requirement_id}-${index}`} gap={item} name={gapName(item)}/>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有结构化差距"/>},
      {key:'evidence',label:`完整证据（${allEvidence.length}）`,children:<><div className="match-evidence-columns"><section><div className="match-evidence-column-title"><Typography.Text strong>简历证据</Typography.Text><Tag>{candidateEvidence.length}</Tag></div>{candidateEvidence.length?candidateEvidence.map((item,index)=><EvidenceItem key={`${item.result_reference}-${index}`} evidence={item} onOpen={setEvidenceOpen}/>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无简历证据"/>}</section><section><div className="match-evidence-column-title"><Typography.Text strong>岗位证据</Typography.Text><Tag>{positionEvidence.length}</Tag></div>{positionEvidence.length?positionEvidence.map((item,index)=><EvidenceItem key={`${item.result_reference}-${index}`} evidence={item} onOpen={setEvidenceOpen}/>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无岗位证据"/>}</section></div>{gapEvidence.map((item,index)=><EvidenceItem key={`${item.result_reference}-${index}`} evidence={item} onOpen={setEvidenceOpen}/>)}</>},
    ]}/></div>

    <Drawer title="技术与审计信息" size="large" open={auditOpen} onClose={()=>setAuditOpen(false)}>
      {missingHeader.length>0&&<Alert type="error" showIcon title="审计字段不完整" description={`缺失：${missingHeader.map(([label])=>label).join('、')}`}/>}
      <Descriptions className="match-audit-list" bordered size="small" column={1} items={[
        {key:'id',label:'评估记录',children:<Typography.Text copyable={{text:report.evaluation_id}}>已生成</Typography.Text>},
        {key:'resume',label:'简历记录',children:lineage?.resume_id?<Typography.Text copyable={{text:lineage.resume_id}}>已关联</Typography.Text>:'缺失'},
        {key:'snapshot',label:'快照记录',children:lineage?.validated_cv_snapshot_id?<Typography.Text copyable={{text:lineage.validated_cv_snapshot_id}}>已关联</Typography.Text>:'缺失'},
        {key:'position',label:'目标岗位',children:displayPositionName},
        {key:'graph',label:'图谱版本',children:readableSystemValue(graphVersion)},
        {key:'provider',label:'执行服务',children:readableSystemValue(lineage?.provider)},
        {key:'algorithm',label:'算法版本',children:readableSystemValue(evaluationAlgorithm)},
        {key:'scoring',label:'评分配置版本',children:readableSystemValue(scoringConfig)},
        {key:'cv-profile',label:'简历画像',children:cvProfileId?<Typography.Text copyable={{text:cvProfileId}}>已关联 · {readableSystemValue(cvProfileVersion)}</Typography.Text>:'缺失'},
        {key:'position-profile',label:'岗位画像',children:positionProfileId?<Typography.Text copyable={{text:positionProfileId}}>已关联 · {readableSystemValue(positionProfileVersion)}</Typography.Text>:'缺失'},
        {key:'validity',label:'结果状态',children:report.stale?<Tag color="warning">需要重新计算</Tag>:<Tag color="success">当前有效</Tag>},
        {key:'stale-reason',label:'需要重算的原因',children:staleReasons.join('、')||'无'},
      ]}/>
      <SemanticShadowSection evaluation={evaluation}/>
      {evaluation.input_coverage&&<Card className="profile" title="输入覆盖诊断">
        <Descriptions size="small" column={{xs:1,md:2}} items={[
          ...Object.entries(evaluation.input_coverage).map(([key,value])=>({
            key,
            label:{
              required_skills:'必备技能',
              responsibilities:'岗位职责',
              education:'学历',
              experience:'工作经验',
              certificate:'证书',
              language:'语言',
              location:'地点',
              availability:'可用性',
            }[key]||'其他输入',
            children:`${Number(value.count)||0} 项 · 岗位侧 ${value.condition_available?'有':'无'} · 候选人侧 ${value.candidate_available?'有':'无'} · ${value.available?'可用于匹配':'不可用于匹配'}`,
          })),
        ]}/>
      </Card>}
      {unresolvedEvidence.length>0&&<Card className="profile" title="未映射来源证据">{unresolvedEvidence.map((item,index)=><EvidenceItem key={`${item.result_reference}-${index}`} evidence={item} onOpen={setEvidenceOpen}/>)}</Card>}
      <Card className="profile" title="解释忠实度证据删除测试"><EvidenceDeletionWorkbench evaluationId={report.evaluation_id} evidenceSourceIds={criticalEvidenceIds} disabled={report.stale}/></Card>
    </Drawer>
    <EvidenceOriginalDrawer evidence={evidenceOpen||null} onClose={()=>setEvidenceOpen(undefined)}/>
  </>;
}
