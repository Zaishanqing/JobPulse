import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {App,Button,Card,Checkbox,Descriptions,Dropdown,Form,Input,Modal,Row,Segmented,Select,Space,Spin,Table,Tabs,Tag,Typography} from 'antd';
import {CheckOutlined,CommentOutlined,SortAscendingOutlined} from '@ant-design/icons';
import {useNavigate,useParams,useSearchParams} from 'react-router-dom';
import {getDraftGraph,getPublishedPositionGraph,getRelationExplanation,getRequirementInflation,modifyRelation,openGraphDraft} from '../api';
import type {GraphSnapshot,JDRequirementInflationDiagnostic,Relation,RequirementInflationReport} from '../types';
import {aggregateEvidence,type AggregateEvidenceKind} from '../../evidence/api';
import {EvidenceDrawer,EvidenceRow,EvidenceViewer} from '../../evidence/components/EvidenceViewer';
import type {ProfileItem} from '../../evidence/types';
import {ApiError,type AggregateEvidenceSupport} from '../../../shared/api';
import {EmptyState,Failure,ToastAlert as Alert,type LoadState} from '../../../shared/components/States';
import {GraphView,type GraphViewMode} from '../../../GraphView';
import {useAuth} from '../../auth/AuthContext';
import {EvidenceDeepLinkFocus} from '../../rag/EvidenceDeepLink';
import {statusText} from '../../../shared/idText';

type ProfileSortField='support'|'importance';
type SortDirection='asc'|'desc';
const importanceRank:Record<string,number>={supplementary:1,important:2,core:3};
const importanceLabels:Record<string,string>={core:'核心',important:'重要',supplementary:'补充'};
const importanceOrder=['core','important','supplementary'];
type GraphProfileView='graph'|'stack'|'level';
type SkillViewItem={skill_id:string;skill_name:string;weight:number;evidence_count:number;importance_level:string};
type TechStackGroup={category:string;skills:SkillViewItem[]};
type LevelGroup={importance_level:string;skills:SkillViewItem[]};
const STACK_LIST_PAGE_SIZE=5;
const PROFILE_LIST_PAGE_SIZE=10;

function primaryClassification(relation:Relation,facet:'concept_class'|'technology_kind'){
  return relation.classifications?.find(item=>item.facet===facet&&item.is_primary);
}

function viewSkill(relation:Relation):SkillViewItem{
  return {skill_id:relation.skill_id,skill_name:relation.canonical_name,weight:relation.weight,evidence_count:relation.metrics.support_document_count,importance_level:relation.importance_level};
}

function buildTechStackGroups(relations:Relation[]):TechStackGroup[]{
  const groups=new Map<string,SkillViewItem[]>();
  relations.forEach(relation=>{
    const concept=primaryClassification(relation,'concept_class');
    const kind=concept?.code==='technology'?primaryClassification(relation,'technology_kind'):undefined;
    const category=kind?.name_zh||(concept?.code==='technology'?'未分类技术栈':'其他能力');
    groups.set(category,[...(groups.get(category)||[]),viewSkill(relation)]);
  });
  return [...groups.entries()].map(([category,skills])=>({category,skills:skills.sort((left,right)=>right.weight-left.weight)})).sort((left,right)=>left.category.localeCompare(right.category,'zh-CN'));
}

function buildLevelGroups(relations:Relation[]):LevelGroup[]{
  const groups=new Map<string,SkillViewItem[]>();
  relations.forEach(relation=>{
    const level=importanceLabels[relation.importance_level]?relation.importance_level:'supplementary';
    groups.set(level,[...(groups.get(level)||[]),viewSkill(relation)]);
  });
  return [...groups.entries()]
    .sort(([left],[right])=>importanceOrder.indexOf(left)-importanceOrder.indexOf(right))
    .map(([importance_level,skills])=>({importance_level,skills:skills.sort((left,right)=>right.weight-left.weight)}));
}

function TechStackList({group}:{group:TechStackGroup}){
  return <div className="stack-lane">
    <div className="stack-lane-title"><strong>{group.category}</strong><span>{group.skills.length} 项</span></div>
    <Table
      className="stack-skill-table"
      size="small"
      rowKey="skill_id"
      dataSource={group.skills}
      pagination={{pageSize:STACK_LIST_PAGE_SIZE,showSizeChanger:false}}
      columns={[
        {title:'技能',dataIndex:'skill_name',render:(value:string)=><strong>{value}</strong>},
        {title:'支持',dataIndex:'evidence_count',width:72,align:'center' as const,render:(value:number)=>`${value} 条`},
        {title:'权重',width:96,align:'center' as const,render:(_:unknown,skill:SkillViewItem)=>`${Math.round(skill.weight*100)}%`},
      ]}
    />
  </div>;
}

function TechStackSurface({groups}:{groups:TechStackGroup[]}){
  if(!groups.length)return <div className="state-panel"><EmptyState text="当前图谱尚未形成技术栈分组"/></div>;
  return <section className="analysis-surface graph-profile-surface">
    <div className="analysis-surface-head"><div><Typography.Title level={4}>技术栈视图</Typography.Title><Typography.Text type="secondary">按知识图谱中的技术形态组织，并以关系权重排序。</Typography.Text></div></div>
    <div className="stack-lanes">{groups.map(group=><TechStackList key={group.category} group={group}/>)}</div>
  </section>;
}

function LevelSurface({groups}:{groups:LevelGroup[]}){
  if(!groups.length)return <div className="state-panel"><EmptyState text="当前图谱尚未形成能力级别"/></div>;
  return <section className="analysis-surface graph-profile-surface">
    <div className="analysis-surface-head"><div><Typography.Title level={4}>能力级别视图</Typography.Title><Typography.Text type="secondary">按知识图谱的核心、重要与补充级别查看能力分布。</Typography.Text></div></div>
    <div className="level-bands">{groups.map(group=><section key={group.importance_level}><div><strong>{importanceLabels[group.importance_level]||group.importance_level}</strong><span>{group.skills.length} 项能力</span></div><div>{group.skills.map(skill=><Tag key={skill.skill_id}>{skill.skill_name} · {Math.round(skill.weight*100)}%</Tag>)}</div></section>)}</div>
  </section>;
}

function ProfileSortMenu({field,direction,allowImportance=true,onChange}:{field:ProfileSortField;direction:SortDirection;allowImportance?:boolean;onChange:(field:ProfileSortField,direction:SortDirection)=>void}){
  const fieldLabel=field==='importance'?'重要性':'支持文档数';
  const checkIcon=(selected:boolean)=><CheckOutlined style={{visibility:selected?'visible':'hidden'}}/>;
  return <Dropdown trigger={['click']} rootClassName="profile-sort-dropdown" menu={{
    items:[
      {key:'field:support',icon:checkIcon(field==='support'),label:'支持文档数'},
      ...(allowImportance?[{key:'field:importance',icon:checkIcon(field==='importance'),label:'重要性'}]:[]),
      {type:'divider' as const},
      {key:'direction:asc',icon:checkIcon(direction==='asc'),label:'递增'},
      {key:'direction:desc',icon:checkIcon(direction==='desc'),label:'递减'},
    ],
    selectable:false,
    onClick:({key})=>{
      const [kind,value]=key.split(':');
      if(kind==='field')onChange(value as ProfileSortField,direction);
      else onChange(field,value as SortDirection);
    },
  }}><Button icon={<SortAscendingOutlined/>}>排序：{fieldLabel} · {direction==='asc'?'递增':'递减'}</Button></Dropdown>;
}

const profilePagination={pageSize:PROFILE_LIST_PAGE_SIZE,showSizeChanger:false,hideOnSinglePage:true};
const profileCountColumn={title:'支持文档数',width:120,align:'center' as const};
const profileEvidenceColumn={title:'证据',width:120,align:'center' as const};

type ProfileSortState={field:ProfileSortField;direction:SortDirection};
type ProfileTabKey='technical'|'tasks'|'education'|'experience'|'certificate'|'soft_skill'|'other'|'company'|'employment';
const defaultProfileSorts:Record<ProfileTabKey,ProfileSortState>={
  technical:{field:'importance',direction:'desc'},
  tasks:{field:'support',direction:'desc'},
  education:{field:'support',direction:'desc'},
  experience:{field:'support',direction:'desc'},
  certificate:{field:'support',direction:'desc'},
  soft_skill:{field:'support',direction:'desc'},
  other:{field:'support',direction:'desc'},
  company:{field:'support',direction:'desc'},
  employment:{field:'support',direction:'desc'},
};

function ProfileList({items,columnTitle,endpoint,onEvidence,direction}:{items:ProfileItem[];columnTitle:string;endpoint:AggregateEvidenceKind;onEvidence:(title:string,evidence:AggregateEvidenceSupport[])=>void;direction:SortDirection}){
  const {message}=App.useApp();
  const sortedItems=useMemo(()=>[...items].sort((left,right)=>{
    const difference=left.support_document_count-right.support_document_count;
    if(difference!==0)return direction==='asc'?difference:-difference;
    return String(left.text||left.kind||'').localeCompare(String(right.text||right.kind||''),'zh-CN');
  }),[direction,items]);
  const showEvidence=async(item:ProfileItem)=>{
    try{
      const evidence=await aggregateEvidence(endpoint,item.aggregate_id);
      onEvidence(String(item.text||item.kind||'条目'),evidence);
    }catch(reason){message.error((reason as ApiError).message)}
  };
  return items.length?<div className="profile-table-shell">
    <Table className="profile-table" tableLayout="fixed" pagination={profilePagination} scroll={{x:660}} rowKey="aggregate_id" dataSource={sortedItems} columns={[
      {title:columnTitle,width:420,render:(_:unknown,item:ProfileItem)=><div className="table-primary"><strong>{String(item.text||item.kind||'条目')}</strong></div>},
      {...profileCountColumn,render:(_:unknown,item:ProfileItem)=>item.support_document_count},
      {...profileEvidenceColumn,render:(_:unknown,item:ProfileItem)=><Button onClick={()=>void showEvidence(item)}>证据入口</Button>},
    ]}/>
  </div>:<EmptyState/>;
}

function SkillProfileList({items,onEvidence,sort}:{items:Relation[];onEvidence:(relation:Relation)=>void;sort:ProfileSortState}){
  const {field,direction}=sort;
  const sortedItems=useMemo(()=>[...items].sort((left,right)=>{
    if(field==='importance'){
      const importanceDifference=(importanceRank[left.importance_level]||0)-(importanceRank[right.importance_level]||0);
      if(importanceDifference!==0)return direction==='asc'?importanceDifference:-importanceDifference;
      const supportDifference=right.metrics.support_document_count-left.metrics.support_document_count;
      if(supportDifference!==0)return supportDifference;
    }else{
      const supportDifference=left.metrics.support_document_count-right.metrics.support_document_count;
      if(supportDifference!==0)return direction==='asc'?supportDifference:-supportDifference;
    }
    return left.canonical_name.localeCompare(right.canonical_name,'zh-CN');
  }),[direction,field,items]);
  return items.length?<div className="profile-table-shell">
    <Table className="profile-table" tableLayout="fixed" pagination={profilePagination} scroll={{x:780}} rowKey="skill_id" dataSource={sortedItems} columns={[
      {title:'技能',width:380,dataIndex:'canonical_name',render:(value:string)=><div className="table-primary"><strong>{value}</strong></div>},
      {title:'重要程度',width:120,align:'center' as const,render:(_:unknown,row:Relation)=>importanceLabels[row.importance_level]||row.importance_level},
      {...profileCountColumn,render:(_:unknown,row:Relation)=>row.metrics.support_document_count},
      {...profileEvidenceColumn,render:(_:unknown,row:Relation)=><Button onClick={()=>onEvidence(row)}>证据入口</Button>},
    ]}/>
  </div>:<EmptyState/>;
}

const classifications=(relation:Relation,facet:'concept_class'|'technology_kind'|'domain')=>(relation.classifications||[]).filter(item=>item.facet===facet);
const domains=(relation:Relation)=>classifications(relation,'domain');
const modalityLabels:Record<string,string>={required:'必须掌握',preferred:'优先考虑',bonus:'加分项',unknown:'原文未说明'};
const buildStatusLabels:Record<string,string>={pending:'等待构建',running:'构建中',succeeded:'构建完成',published:'已发布',failed:'构建失败'};
const inflationRiskLabels={low:'低',medium:'中',high:'高'} as const;
const inflationReasonLabels:Record<string,string>={LOW_MARKET_REQUIRED_PREVALENCE:'市场必备普及率偏低',INSUFFICIENT_CROSS_JD_REQUIRED_SUPPORT:'跨 JD 必备支持不足',LOW_REQUIRED_PURITY:'多数同类 JD 仅视为加分或偏好',INSUFFICIENT_CROSS_ENTERPRISE_SUPPORT:'跨企业支持不足'};
const graphCache=new Map<string,GraphSnapshot>();
const inflationCache=new Map<string,RequirementInflationReport|null>();
const graphCacheKey=(id:string,buildRunId:number|undefined,showingDraft:boolean)=>`${id}:${buildRunId??'published'}:${showingDraft?'draft':'published'}`;
const explanationLabels:Record<string,string>={auto:'系统计算值',manual:'人工调整值',final:'最终采用值',weighted_frequency:'质量加权频率',support_ratio:'样本支持比例',modality_strength:'要求强度',trusted_evidence_ratio:'可信证据比例',source_diversity:'来源多样性',raw_frequency:'原始频率',adjusted_frequency:'质量调整后频率',frequency_delta:'质量调整差值',freshness_score:'数据新鲜度'};
const historyFieldLabels:Record<string,string>={weight:'权重',confidence:'置信度',importance_level:'重要程度',manual_weight:'人工权重',manual_confidence:'人工置信度',manual_importance_level:'人工重要程度',final_weight:'最终权重',final_confidence:'最终置信度',final_importance_level:'最终重要程度'};
const displayValue=(value:unknown)=>value==null?'未设置':typeof value==='number'?Number(value.toFixed(4)):importanceLabels[String(value)]||String(value);
function ExplanationBasis({values}:{values:Record<string,unknown>}){return <div className="graph-explanation-basis">{Object.entries(values).map(([key,value])=><div key={key}><span>{explanationLabels[key]||key}</span><strong>{displayValue(value)}</strong></div>)}</div>}
function HistoryChange({before,after}:{before:unknown;after:unknown}){
  const previous=(before&&typeof before==='object'?before:{}) as Record<string,unknown>;
  const current=(after&&typeof after==='object'?after:{}) as Record<string,unknown>;
  const keys=[...new Set([...Object.keys(previous),...Object.keys(current)])].filter(key=>previous[key]!==current[key]&&historyFieldLabels[key]);
  return keys.length?<Space direction="vertical" size={2}>{keys.map(key=><Typography.Text key={key}>{historyFieldLabels[key]}：{displayValue(previous[key])} → {displayValue(current[key])}</Typography.Text>)}</Space>:<Typography.Text type="secondary">未记录字段变化</Typography.Text>;
}

function inflationRiskTag(level:JDRequirementInflationDiagnostic['risk_level']){
  const color=level==='high'?'error':level==='medium'?'warning':'success';
  return <Tag color={color}>{inflationRiskLabels[level]}风险</Tag>;
}

function RequirementInflationDetails({report}:{report:RequirementInflationReport}){
  const affected=report.jd_diagnostics.filter(item=>item.inflation_risk_skill_count>0);
  return <Space direction="vertical" size={18} className="full requirement-inflation-details">
    <Descriptions size="small" column={3} items={[
      {key:'requirements',label:'必备要求',children:report.summary.total_required_requirement_count},
      {key:'risk',label:'通胀风险要求',children:report.summary.inflation_risk_count},
      {key:'jds',label:'受影响 JD',children:affected.length},
    ]}/>
    <Table
      size="small"
      tableLayout="fixed"
      pagination={false}
      scroll={{x:720}}
      rowKey="document_id"
      dataSource={affected}
      locale={{emptyText:'当前正式画像未发现要求通胀风险'}}
      columns={[
        {title:'样本',width:72,align:'center',render:(_:unknown,_item:JDRequirementInflationDiagnostic,index:number)=>`JD ${index+1}`},
        {title:'风险等级',width:100,render:(_:unknown,item:JDRequirementInflationDiagnostic)=>inflationRiskTag(item.risk_level)},
        {title:'风险占比',width:100,render:(_:unknown,item:JDRequirementInflationDiagnostic)=>`${item.inflation_risk_skill_count}/${item.required_skill_count} · ${Math.round(item.inflation_ratio*100)}%`},
        {title:'缺乏市场共识的必备技能',render:(_:unknown,item:JDRequirementInflationDiagnostic)=><Space direction="vertical" size={4}>{item.requirements.filter(requirement=>requirement.inflation_risk).map(requirement=><div key={requirement.skill_id}><Typography.Text strong>{requirement.skill_name}</Typography.Text><Typography.Text type="secondary"> · 同类 JD 必备率 {Math.round(requirement.market.required_prevalence*100)}% · {requirement.reason_codes.map(code=>inflationReasonLabels[code]||code).join('、')}</Typography.Text></div>)}</Space>},
      ]}
    />
  </Space>;
}

export function PositionGraph(){
  const {message,modal}=App.useApp();
  const {can}=useAuth();
  const navigate=useNavigate();
  const {positionId=''}=useParams();
  const [searchParams]=useSearchParams();
  const id=positionId;
  const [selectedDomains,setSelectedDomains]=useState<string[]>([]);
  const [visibleDomains,setVisibleDomains]=useState<string[]>([]);
  const [filterTransitioning,setFilterTransitioning]=useState(false);
  const filterTimer=useRef<number|undefined>(undefined);
  const [graphProfileView,setGraphProfileView]=useState<GraphProfileView>('graph');
  const [graphViewMode,setGraphViewMode]=useState<GraphViewMode>('explore');
  const [viewTransitioning,setViewTransitioning]=useState(false);
  const viewTimer=useRef<number|undefined>(undefined);
  const [evidence,setEvidence]=useState<Relation|null>(null);
  const [selectedSkillId,setSelectedSkillId]=useState<string>();
  const [profileEvidence,setProfileEvidence]=useState<{title:string;items:AggregateEvidenceSupport[]}|null>(null);
  const [profileTab,setProfileTab]=useState<ProfileTabKey>('technical');
  const [profileSorts,setProfileSorts]=useState<Record<ProfileTabKey,ProfileSortState>>(defaultProfileSorts);
  const activeProfileSort=profileSorts[profileTab];
  const changeActiveProfileSort=(field:ProfileSortField,direction:SortDirection)=>setProfileSorts(previous=>({...previous,[profileTab]:{field,direction}}));
  const [inflationReport,setInflationReport]=useState<RequirementInflationReport|null>(()=>inflationCache.get(id)??null);
  const [inflationDetailsOpen,setInflationDetailsOpen]=useState(false);
  const [editing,setEditing]=useState<Relation|null>(null);
  const canManageGraph=can('kg.build.manage');
  const initialBuildRunId=canManageGraph?(Number(searchParams.get('buildRunId'))||undefined):undefined;
  const [buildRunId,setBuildRunId]=useState<number|undefined>(initialBuildRunId);
  const [showingDraft,setShowingDraft]=useState(Boolean(initialBuildRunId));
  const [saveError,setSaveError]=useState<ApiError>();
  const [saving,setSaving]=useState(false);
  const [changedFields,setChangedFields]=useState<Set<string>>(new Set());
  const currentKey=graphCacheKey(id,buildRunId,showingDraft);
  const [state,setState]=useState<LoadState<GraphSnapshot>>(graphCache.has(currentKey)?{kind:'success',data:graphCache.get(currentKey)!}:{kind:'loading'});
  const load=useCallback(async()=>{
    const key=graphCacheKey(id,buildRunId,showingDraft);
    const cached=graphCache.get(key);
    if(cached){setState({kind:'success',data:cached});return}
    setState({kind:'loading'});
    try{
      const data=await (buildRunId&&showingDraft?getDraftGraph(buildRunId):getPublishedPositionGraph(id));
      graphCache.set(key,data);
      setState({kind:'success',data});
    }catch(error){
      const apiError=error as ApiError;
      setState({kind:'error',message:apiError.message,status:apiError.status});
    }
  },[buildRunId,id,showingDraft]);
  useEffect(()=>{const id=requestAnimationFrame(()=>void load());return()=>cancelAnimationFrame(id)},[load]);
  useEffect(()=>{
    let active=true;
    if(showingDraft){setInflationReport(null);return()=>{active=false}}
    if(inflationCache.has(id)){setInflationReport(inflationCache.get(id)??null);return()=>{active=false}}
    getRequirementInflation(id).then(data=>{
      if(!active)return;
      inflationCache.set(id,data.requirement_inflation);
      setInflationReport(data.requirement_inflation);
    }).catch(()=>{
      if(!active)return;
      inflationCache.set(id,null);
      setInflationReport(null);
    });
    return()=>{active=false};
  },[id,showingDraft]);
  useEffect(()=>{
    if(!buildRunId)return;
    const draftKey=graphCacheKey(id,buildRunId,true);
    const publishedKey=graphCacheKey(id,undefined,false);
    if(!graphCache.has(draftKey))getDraftGraph(buildRunId).then(data=>graphCache.set(draftKey,data)).catch(()=>undefined);
    if(!graphCache.has(publishedKey))getPublishedPositionGraph(id).then(data=>graphCache.set(publishedKey,data)).catch(()=>undefined);
  },[buildRunId,id]);
  useEffect(()=>()=>{
    if(filterTimer.current)window.clearTimeout(filterTimer.current);
    if(viewTimer.current)window.clearTimeout(viewTimer.current);
  },[]);
  if(state.kind==='loading')return <div className="center-loading"><Spin size="large"/><span className="state-panel-hint">正在加载岗位图谱</span></div>;
  if(state.kind==='error')return <Failure {...state} retry={load}/>;
  const graph=state.data,relations=graph.skill_relations;
  const domainOptions=new Map<string,string>();
  relations.forEach(item=>domains(item).forEach(domain=>domainOptions.set(domain.code,domain.name_zh)));
  const categories=[...domainOptions.keys()];
  const domainStats=new Map<string,{name:string;count:number}>();
  relations.forEach(relation=>{
    const items=domains(relation);
    if(items.length===0){
      const entry=domainStats.get('__uncategorized__')||{name:'未分类',count:0};
      entry.count+=1;
      domainStats.set('__uncategorized__',entry);
    }else{
      items.forEach(domain=>{
        const entry=domainStats.get(domain.code)||{name:domain.name_zh||domain.code,count:0};
        entry.count+=1;
        domainStats.set(domain.code,entry);
      });
    }
  });
  const domainStatsList=[...domainStats.entries()].sort((a,b)=>b[1].count-a[1].count);
  const affectedInflationJdCount=inflationReport?.jd_diagnostics.filter(item=>item.inflation_risk_skill_count>0).length??0;
  const visibleRelations=visibleDomains.length?relations.filter(item=>domains(item).some(domain=>visibleDomains.includes(domain.code))):relations;
  const techStackGroups=buildTechStackGroups(relations);
  const levelGroups=buildLevelGroups(relations);
  const selected=visibleRelations.find(item=>item.skill_id===selectedSkillId);
  const changeDomains=(values:string[])=>{
    setSelectedDomains(values);
    setFilterTransitioning(true);
    if(filterTimer.current)window.clearTimeout(filterTimer.current);
    filterTimer.current=window.setTimeout(()=>{
      setVisibleDomains(values);
      if(selectedSkillId&&!relations.some(item=>item.skill_id===selectedSkillId&&(!values.length||domains(item).some(domain=>values.includes(domain.code)))))setSelectedSkillId(undefined);
      requestAnimationFrame(()=>setFilterTransitioning(false));
    },140);
  };
  const changeGraphViewMode=(value:GraphViewMode)=>{
    if(value===graphViewMode)return;
    setViewTransitioning(true);
    if(viewTimer.current)window.clearTimeout(viewTimer.current);
    viewTimer.current=window.setTimeout(()=>{
      setGraphViewMode(value);
      setSelectedSkillId(undefined);
      requestAnimationFrame(()=>setViewTransitioning(false));
    },170);
  };
  const graphViewDescriptions:Record<GraphViewMode,string>={
    skills:'岗位直接连接全部技能，适合快速浏览技能全貌',
    hierarchy:'同时展示分类层级与全部技能，适合查看整体结构',
    explore:'逐层查看分类与技能；悬停一级分类可预览下一层',
  };
  const beginEdit=async(row:Relation)=>{if(!canManageGraph)return;setSaveError(undefined);setChangedFields(new Set());try{let runId=buildRunId;if(!runId){const draft=await openGraphDraft(id,graph.version_id);setBuildRunId(draft.build_run_id);runId=draft.build_run_id}const draftGraph=await getDraftGraph(runId);graphCache.set(graphCacheKey(id,runId,true),draftGraph);setShowingDraft(true);setState({kind:'success',data:draftGraph});setEditing(draftGraph.skill_relations.find(item=>item.skill_id===row.skill_id)||null)}catch(reason){const error=reason as ApiError;modal.error({title:'无法创建编辑草稿',content:error.message})}};
  const showExplanation=async(row:Relation)=>{try{const detail=await getRelationExplanation(row.relation_id,graph.version_id);modal.info({className:'graph-explanation-modal',title:`${row.canonical_name} · 关系解释`,width:900,okText:'确定',content:<Descriptions bordered size="small" column={2} items={[{key:'jd',label:'支持 JD',children:detail.statistics.supporting_jd_count??'不可用'},{key:'dedup',label:'去重后 JD',children:detail.statistics.deduplicated_jd_count??'不可用'},{key:'enterprise',label:'企业数量',children:detail.statistics.enterprise_count??'不可用'},{key:'source',label:'来源数量',children:detail.statistics.source_count??'不可用'},{key:'weight',label:'权重依据',span:2,children:<ExplanationBasis values={detail.weight_basis}/>},{key:'confidence',label:'置信度依据',span:2,children:<ExplanationBasis values={detail.confidence_basis}/>},{key:'quality',label:'质量影响',span:2,children:<ExplanationBasis values={detail.quality_impact}/>} ]}/>})}catch(reason){const error=reason as ApiError;modal.error({title:'无法加载关系解释',content:error.message})}};
  const showHistory=async(row:Relation)=>{try{const detail=await getRelationExplanation(row.relation_id,graph.version_id);const history=detail.manual_modification_history as Array<Record<string,unknown>>;modal.info({title:`${row.canonical_name} · 修改历史`,width:900,content:history.length?<Table pagination={false} rowKey={item=>String(item.event_id)} dataSource={history} columns={[{title:'时间',dataIndex:'created_at',render:(value:string)=>new Date(value).toLocaleString('zh-CN')},{title:'修改人',dataIndex:'actor_id',render:(value:unknown)=>value??'系统'},{title:'修改原因',dataIndex:'reason',render:(value:unknown)=>String(value||'未填写')},{title:'具体变化',render:(_:unknown,item:Record<string,unknown>)=><HistoryChange before={item.before} after={item.after}/>} ]}/>:<Typography.Text type="secondary">该技能节点暂无人工修改记录</Typography.Text>})}catch(reason){const error=reason as ApiError;modal.error({title:'无法加载修改历史',content:error.message})}};
  const requirements=graph.requirement_profile;
  const kinds=(...values:string[])=>requirements.filter(item=>values.includes(String(item.kind)));
  const other=requirements.filter(item=>!['education','experience','certificate','soft_skill'].includes(String(item.kind)));
  const profileTabs=[
    {key:'technical',label:`技能（${relations.length}）`,children:<SkillProfileList items={relations} onEvidence={setEvidence} sort={profileSorts.technical}/>},
    {key:'tasks',label:`职责（${graph.responsibilities.length}）`,children:<ProfileList items={graph.responsibilities} columnTitle="职责" endpoint="tasks" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.tasks.direction}/>},
    {key:'education',label:`学历（${kinds('education').length}）`,children:<ProfileList items={kinds('education')} columnTitle="学历" endpoint="requirements" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.education.direction}/>},
    {key:'experience',label:`经验（${kinds('experience').length}）`,children:<ProfileList items={kinds('experience')} columnTitle="经验" endpoint="requirements" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.experience.direction}/>},
    {key:'certificate',label:`证书（${kinds('certificate').length}）`,children:<ProfileList items={kinds('certificate')} columnTitle="证书" endpoint="requirements" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.certificate.direction}/>},
    {key:'soft_skill',label:`软技能（${kinds('soft_skill').length}）`,children:<ProfileList items={kinds('soft_skill')} columnTitle="软技能" endpoint="requirements" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.soft_skill.direction}/>},
    {key:'other',label:`其他要求（${other.length}）`,children:<ProfileList items={other} columnTitle="其他要求" endpoint="requirements" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.other.direction}/>},
    {key:'company',label:`公司背景（${graph.company_context.length}）`,children:<ProfileList items={graph.company_context} columnTitle="公司背景" endpoint="company_facts" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.company.direction}/>},
    {key:'employment',label:`招聘与用工信息（${graph.employment_context.length}）`,children:<ProfileList items={graph.employment_context} columnTitle="招聘与用工信息" endpoint="employment_facts" onEvidence={(title,items)=>setProfileEvidence({title,items})} direction={profileSorts.employment.direction}/>},
  ];
  const submitEdit=async(values:Record<string,unknown>)=>{
    if(!canManageGraph||!buildRunId||!editing)return;
    setSaving(true);setSaveError(undefined);
    const payload:Record<string,unknown>={
      build_run_id:buildRunId,position_id:id,expected_revision:editing.revision??1,
      reason:values.reason,
    };
    for(const field of ['weight','confidence','importance_level']){
      if(values[`clear_${field}`]===true)payload[field]=null;
      else if(changedFields.has(field))payload[field]=values[field];
    }
    try{
      await modifyRelation(editing.relation_id,payload);
      const draftGraph=await getDraftGraph(buildRunId);
      graphCache.set(graphCacheKey(id,buildRunId,true),draftGraph);
      setState({kind:'success',data:draftGraph});setEditing(null);
      message.success('已保存到草稿，尚未发布');
    }catch(reason){
      const error=reason as ApiError;setSaveError(error);
      const details=(error.details||{}) as Record<string,unknown>;
      if(error.status===409&&details.error_code==='RELATION_EDIT_CONFLICT'){
        const fresh=await getDraftGraph(buildRunId);
        graphCache.set(graphCacheKey(id,buildRunId,true),fresh);
        setState({kind:'success',data:fresh});
        setEditing(fresh.skill_relations.find(item=>item.skill_id===editing.skill_id)||null);
        setChangedFields(new Set());
      }
    }finally{setSaving(false)}
  };
  const editConflictReloaded=saveError?.status===409&&
    (saveError.details as Record<string,unknown>|undefined)?.error_code==='RELATION_EDIT_CONFLICT';
  const statusLine=showingDraft&&graph.build_info
    ?`构建版本 ${graph.build_info.build_version} · 尚未发布 · ${graph.sample_stats.included_samples||0} 个有效样本`
    :`正式发布版本${graph.build_info?` #${graph.build_info.build_version}`:'尚未生成'} · ${graph.sample_stats.included_samples||0} 个有效样本`;
  return <><EvidenceDeepLinkFocus resourceId={id}/><Row justify="space-between"><div><Typography.Title level={2}>{graph.position.name}能力图谱</Typography.Title></div><Space>{canManageGraph&&buildRunId&&<Button onClick={()=>setShowingDraft(value=>!value)}>{showingDraft?'查看当前正式版':'返回构建草稿'}</Button>}{!showingDraft&&graph.version_id&&<Button icon={<CommentOutlined/>} onClick={()=>navigate(`/evidence/assistant?${new URLSearchParams({objectType:'standard_position',objectId:id,objectName:graph.position.name,objectVersion:String(graph.version_id),versionKind:'graph_version_id',evidenceTypes:'jd_evidence,kg_skill_relation_evidence',returnTo:`/positions/${encodeURIComponent(id)}`})}`)}>证据问答</Button>}</Space></Row><div className="graph-profile-view-toolbar"><Typography.Text strong>图谱视图</Typography.Text><Segmented value={graphProfileView} onChange={value=>setGraphProfileView(value as GraphProfileView)} options={[{label:'关系图',value:'graph'},{label:'技术栈',value:'stack'},{label:'能力级别',value:'level'}]}/></div>{graphProfileView==='graph'?<div className="graph-layout">
  <section className="graph-layout-info">
    <Typography.Text strong>{statusLine}</Typography.Text>
    {graph.warning&&<Typography.Paragraph type="secondary">{graph.warning}</Typography.Paragraph>}
    {graph.build_info&&<>
      <Typography.Title level={5} style={{marginTop:18}}>{showingDraft?'本次构建结果':'构建记录'}</Typography.Title>
      <div className="graph-build-info">
        <div><span>处理状态</span><strong>{buildStatusLabels[graph.build_info.status]||statusText(graph.build_info.status)}</strong></div>
        <div><span>采用的 JD</span><strong>{graph.build_info.summary.included_samples??0}</strong></div>
        <div><span>未采用的 JD</span><strong>{graph.build_info.summary.excluded_samples??0}</strong></div>
        <div><span>生成的技能关系</span><strong>{graph.build_info.summary.relations??0}</strong></div>
      </div>
    </>}
    <Typography.Title level={5} style={{marginTop:18}}>技能统计</Typography.Title>
    <div className="graph-build-info">
      <div><span>技能总数</span><strong>{relations.length}</strong></div>
      {domainStatsList.map(([code,item])=><div key={code}><span>{item.name}</span><strong>{item.count} 项</strong></div>)}
    </div>
    {!showingDraft&&inflationReport&&<div className="requirement-calibration-summary">
      <div className="requirement-calibration-head"><Typography.Text strong>岗位要求校准</Typography.Text></div>
      <Typography.Text type="secondary">跨 JD、企业与来源校准必备要求</Typography.Text>
      <div className="graph-build-info">
        <div><span>通胀风险要求</span><strong>{inflationReport.summary.inflation_risk_count} 项</strong></div>
        <div><span>受影响 JD</span><strong>{affectedInflationJdCount} 份</strong></div>
      </div>
      <Button size="small" block onClick={()=>setInflationDetailsOpen(true)}>查看诊断</Button>
    </div>}
  </section>
  <section className="graph-layout-canvas">
    {relations.length===0?<EmptyState text={showingDraft?'本次构建没有生成岗位—技能关系':'当前没有已发布的岗位—技能关系'}/>
      :<div className={`graph-filter-stage${filterTransitioning||viewTransitioning?' is-filtering':''}`}><GraphView position={id} positionName={graph.position.name} relations={visibleRelations} viewMode={graphViewMode} onSelect={setSelectedSkillId}/></div>}
  </section>
  <section className="graph-layout-tools" aria-label="功能区">
    <div className="graph-view-selector">
      <Typography.Text strong>图谱视图</Typography.Text>
      <Select<GraphViewMode>
        aria-label="图谱视图"
        value={graphViewMode}
        onChange={changeGraphViewMode}
        options={[
          {value:'explore',label:'逐层探索'},
          {value:'skills',label:'技能全景'},
          {value:'hierarchy',label:'层级树'},
        ]}
      />
      <Typography.Text type="secondary" className="graph-domain-filter-hint">{graphViewDescriptions[graphViewMode]}</Typography.Text>
    </div>
    <div className="graph-domain-filter">
      <div className="graph-domain-filter-head"><Typography.Text strong>技术领域</Typography.Text><Typography.Text type="secondary">显示 {visibleRelations.length} / {relations.length} 个技能</Typography.Text></div>
      <Select mode="multiple" allowClear maxTagCount="responsive" placeholder="选择一个或多个技术领域" value={selectedDomains} onChange={changeDomains} options={categories.map(value=>({value,label:domainOptions.get(value)||value}))}/>
      <Typography.Text type="secondary" className="graph-domain-filter-hint">选择多个领域时，展示属于其中任一领域的技能节点</Typography.Text>
    </div>
    {!selected?<div className="graph-tools-empty"><Typography.Text type="secondary">点击图谱中的技能节点，查看详情与操作</Typography.Text></div>
      :<>
        <div className="graph-tools-head">
          <Typography.Title level={5} style={{margin:0}}>{selected.canonical_name}</Typography.Title>
          <Tag color={selected.importance_level==='core'?'error':selected.importance_level==='important'?'warning':'default'}>{importanceLabels[selected.importance_level]||selected.importance_level}</Tag>
        </div>
        <div className="graph-tools-info">
          <div className="graph-tools-field"><span>概念性质</span><strong>{classifications(selected,'concept_class')[0]?.name_zh||'未分类'}</strong></div>
          <div className="graph-tools-field"><span>技术形态</span><strong>{classifications(selected,'technology_kind')[0]?.name_zh||'-'}</strong></div>
          <div className="graph-tools-field"><span>技术领域</span><strong>{domains(selected).map(item=>item.name_zh).join('、')||'-'}</strong></div>
          <div className="graph-tools-field"><span>岗位要求</span><strong>{modalityLabels[selected.primary_modality]||'未说明'}</strong></div>
          <div className="graph-tools-field"><span>权重</span><strong>{Math.round(selected.weight*100)}%</strong></div>
          <div className="graph-tools-field"><span>置信度</span><strong>{Math.round(selected.confidence*100)}%</strong></div>
          <div className="graph-tools-field"><span>支持</span><strong>{selected.statistics?`${selected.statistics.deduplicated_jd_count} JD · ${selected.statistics.enterprise_count} 企业`:`${selected.metrics.support_document_count} JD`}</strong></div>
        </div>
        <div className="graph-tools-actions">
          <Button onClick={()=>setEvidence(selected)}>查看证据</Button>
          <Button onClick={()=>void showExplanation(selected)}>查看解释</Button>
          <Button onClick={()=>void showHistory(selected)}>修改历史</Button>
          {canManageGraph&&<Button type="primary" onClick={()=>void beginEdit(selected)}>编辑</Button>}
        </div>
      </>}
  </section>
</div>:graphProfileView==='stack'?<TechStackSurface groups={techStackGroups}/>:<LevelSurface groups={levelGroups}/>}<Card className="profile graph-profile-lists" title="岗位画像明细"><Tabs activeKey={profileTab} onChange={value=>setProfileTab(value as ProfileTabKey)} tabBarExtraContent={<div className="profile-sort-tabs-extra"><ProfileSortMenu field={activeProfileSort.field} direction={activeProfileSort.direction} allowImportance={profileTab==='technical'} onChange={changeActiveProfileSort}/></div>} items={profileTabs}/></Card><EvidenceViewer relation={evidence} onClose={()=>setEvidence(null)}/><Modal className="requirement-inflation-modal" title="岗位要求通胀诊断" width={960} open={inflationDetailsOpen} footer={null} onCancel={()=>setInflationDetailsOpen(false)}>{inflationReport&&<RequirementInflationDetails report={inflationReport}/>}</Modal>{canManageGraph&&<Modal title="编辑草稿关系" open={Boolean(editing)} footer={null} onCancel={()=>{if(!saving)setEditing(null)}}>
{editing&&<Form
  initialValues={{weight:editing.weight,confidence:editing.confidence,importance_level:editing.importance_level,reason:'',clear_weight:false,clear_confidence:false,clear_importance_level:false}}
  onValuesChange={changed=>setChangedFields(previous=>new Set([...previous,...Object.keys(changed)]))}
  onFinish={submitEdit}
>
  {saveError&&<Alert type="error" showIcon title={editConflictReloaded?'编辑冲突，已重新加载草稿':saveError.message} description="请求已记录，请稍后重试。"/>}
  <Form.Item name="weight" label="权重"><Input type="number" step="0.01"/></Form.Item>
  <Form.Item name="clear_weight" valuePropName="checked"><Checkbox>清除人工权重，恢复自动值</Checkbox></Form.Item>
  <Form.Item name="confidence" label="置信度"><Input type="number" step="0.01"/></Form.Item>
  <Form.Item name="clear_confidence" valuePropName="checked"><Checkbox>清除人工置信度，恢复自动值</Checkbox></Form.Item>
  <Form.Item name="importance_level" label="重要程度"><Select options={Object.entries(importanceLabels).map(([value,label])=>({value,label}))}/></Form.Item>
  <Form.Item name="clear_importance_level" valuePropName="checked"><Checkbox>清除人工重要程度，恢复系统计算值</Checkbox></Form.Item>
  <Form.Item name="reason" label="修改原因" rules={[{required:true,message:'请填写修改原因'}]}><Input placeholder="请说明本次修改的依据"/></Form.Item>
  <Button loading={saving} htmlType="submit" type="primary">保存到草稿</Button>
</Form>}</Modal>}
  <EvidenceDrawer open={Boolean(profileEvidence)} title="证据链" subtitle={profileEvidence?.title||''} onClose={()=>setProfileEvidence(null)}>
    {profileEvidence&&(profileEvidence.items.length===0?<EmptyState text="暂无可用证据"/>
      :<Space direction="vertical" className="full" size={20}>
        {profileEvidence.items.map(item=>{
          const itemEvidence=item.evidence;
          return <EvidenceRow key={item.evidence_id} row={{
            key:item.evidence_id,
            alignment:itemEvidence.alignment,
            occurrence_index:itemEvidence.occurrence_index,
            start:itemEvidence.start,
            end:itemEvidence.end,
            quote:itemEvidence.quote,
            rawText:item.source?.raw_text,
            documentId:itemEvidence.document_id||item.source?.document_id,
          }}/>;
        })}
      </Space>)}
  </EvidenceDrawer>
</>
}
