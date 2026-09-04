import {useCallback,useEffect,useMemo,useState} from 'react';
import {App,Button,Collapse,Descriptions,Empty,Input,Modal,Select,Space,Spin,Statistic,Tag,Typography} from 'antd';
import {ArrowRightOutlined,HistoryOutlined,PlayCircleOutlined,ReloadOutlined} from '@ant-design/icons';
import {useSearchParams} from 'react-router-dom';
import type {Position} from '../../../shared/api';
import {ApiError,localizeSystemMessage} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {StatusTag} from '../../../shared/components/StatusTag';
import {
  createTrendAnalysis,
  approveTrendReview,
  claimTrendReview,
  createTrendReview,
  getCapabilityEvolution,
  listPublishedPositions,
  listTrendReports,
  publishTrendReport,
  waitForTrendAnalysis,
} from '../api';
import {EventTimeline} from '../components/EventTimeline';
import {SkillEvolutionBubbleView} from '../components/SkillEvolutionBubbleView';
import type {CapabilityEvolution,SnapshotChange,StandardSkill,TrendReport,TrendSkillDetail} from '../types';
import {useAuth} from '../../auth/AuthContext';
import {growthRateText,newSkillsFirst,shouldShowGrowthRate} from './growthRate';

const reviewLabel:Record<string,string>={pending:'待领取',claimed:'审核中',approved:'已审核',rejected:'已退回',modified:'已调整'};
const directionLabel:Record<string,string>={new:'新增',rising:'上升',declining:'下降',stable:'稳定'};
const domainLabels:Record<string,string>={
  ai_intelligent_systems:'人工智能与智能系统',blockchain_web3:'区块链与可信计算',cloud_distributed:'云原生与分布式系统',
  computing_hardware:'计算系统与芯片',cybersecurity_privacy:'网络安全与隐私',data_engineering:'数据工程与数据库',
  digital_governance:'数字化治理与标准',embedded_iot_edge:'嵌入式、物联网与边缘计算',hci_graphics_xr:'人机交互、图形与 XR',
  network_communications:'网络与通信',quantum_computing:'量子信息与量子计算',robotics_autonomy:'机器人与自主系统',
  software_engineering:'软件工程',
};
const sourceLabels:Record<string,string>={arxiv:'学术论文（arXiv）',cvf:'计算机视觉论文',acl:'自然语言处理论文',policy:'政策文件',funding:'融资信息',github:'开源项目'};
const sourceLabel=(value:string)=>sourceLabels[value]||'其他来源';
const domainLabel=(value:string)=>/[\u3400-\u9fff]/.test(value)?value:domainLabels[value]||'未分类领域';
const readableNarrative=(value:string|null|undefined,fallback:string)=>value&&/[\u3400-\u9fff]/.test(value)
  ?value.replace(/\s*\bTrend\b\s*/gi,'趋势').replace(/\s*\bEvidence\b\s*/gi,'证据').replace(/\s*\bJD\b\s*/g,'岗位描述').replace(/\s*\bCV\b\s*/g,'简历')
  :fallback;
const comboNarrative=(value:string|null|undefined)=>readableNarrative(value,'').replace(/分析结果显示技能组合发生变化。\s*/g,'').trim();
function trendProviderLabel(report:Pick<TrendReport,'provider'|'analysis_mode'>){
  if(report.provider==='trend_intelligence_http'&&report.analysis_mode==='remote_multi_source')return '趋势分析服务 · 远程多源分析';
  return '趋势分析服务 · 分析方式待确认';
}

function snapshotDiff(current:TrendReport,previous?:TrendReport):SnapshotChange[]{
  if(!previous)return [];
  const before=new Map(previous.current_graph.skills.map(skill=>[skill.skill_id,skill]));
  const after=new Map(current.current_graph.skills.map(skill=>[skill.skill_id,skill]));
  const changes:SnapshotChange[]=[];
  after.forEach((skill,skillId)=>{
    const old=before.get(skillId);
    if(!old)changes.push({type:'added',skill});
    else{
      const fields=['weight','confidence','importance_level','trend_score'].filter(field=>{
        const beforeValue=old[field as keyof StandardSkill];
        const afterValue=skill[field as keyof StandardSkill];
        return typeof beforeValue==='number'&&typeof afterValue==='number'?Math.abs(beforeValue-afterValue)>1e-6:beforeValue!==afterValue;
      });
      if(fields.length)changes.push({type:'changed',skill,before:old,fields});
    }
  });
  before.forEach((skill,skillId)=>{if(!after.has(skillId))changes.push({type:'removed',skill})});
  return changes;
}

type TrendWorkbenchCache={
  expiresAt:number;
  positions?:Position[];
  reportsByPosition:Map<string,TrendReport[]>;
  selectedReportByPosition:Map<string,string>;
  lastPositionId?:string;
};
const trendWorkbenchCacheTtlMs=2*60*1000;
const trendWorkbenchCacheEnabled=typeof navigator==='undefined'||!navigator.userAgent.toLowerCase().includes('jsdom');
const trendWorkbenchCaches=new Map<string,TrendWorkbenchCache>();

function trendWorkbenchCache(userId:string,create=false){
  if(!trendWorkbenchCacheEnabled||!userId)return undefined;
  const cached=trendWorkbenchCaches.get(userId);
  if(cached&&cached.expiresAt>Date.now())return cached;
  if(cached)trendWorkbenchCaches.delete(userId);
  if(!create)return undefined;
  const next:TrendWorkbenchCache={
    expiresAt:Date.now()+trendWorkbenchCacheTtlMs,
    reportsByPosition:new Map(),
    selectedReportByPosition:new Map(),
  };
  trendWorkbenchCaches.set(userId,next);
  return next;
}

function refreshTrendWorkbenchCache(userId:string){
  const cache=trendWorkbenchCache(userId,true);
  if(cache)cache.expiresAt=Date.now()+trendWorkbenchCacheTtlMs;
  return cache;
}

export function EvolutionWorkbench(){
  const [searchParams]=useSearchParams();
  const routePositionId=searchParams.get('positionId')||undefined;
  const [positions,setPositions]=useState<Position[]>([]);
  const [positionId,setPositionId]=useState<string>();
  const [evolution,setEvolution]=useState<CapabilityEvolution>();
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ApiError>();

  useEffect(()=>{
    listPublishedPositions().then(data=>{
      setPositions(data);
      setPositionId(data.some(item=>item.position_id===routePositionId)?routePositionId:data[0]?.position_id);
    }).catch(reason=>setError(reason as ApiError)).finally(()=>setLoading(false));
  },[routePositionId]);

  const loadPosition=useCallback(async(idValue:string)=>{
    setLoading(true);setError(undefined);
    try{
      const nextEvolution=await getCapabilityEvolution(idValue);
      setEvolution(nextEvolution);
    }catch(reason){setEvolution(undefined);setError(reason as ApiError)}
    finally{setLoading(false)}
  },[]);

  useEffect(()=>{
    if(!positionId)return;
    const timer=window.setTimeout(()=>void loadPosition(positionId),0);
    return ()=>window.clearTimeout(timer);
  },[loadPosition,positionId]);

  const selectedPosition=positions.find(item=>item.position_id===positionId);
  const positionName=selectedPosition?.name||evolution?.frames.at(-1)?.snapshot.position.name||'当前岗位';
  return <>
    <div className="page-heading"><div><Typography.Title level={2}>岗位能力演化</Typography.Title><Typography.Paragraph type="secondary">基于同一岗位的已发布图谱版本，比较能力结构、权重与证据支持变化。</Typography.Paragraph></div></div>
    <div className="evolution-position-toolbar"><Space wrap>
      <Select showSearch className="evolution-position-select" value={positionId} placeholder="选择已有发布图谱的岗位" onChange={value=>setPositionId(value)} filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())} options={positions.map(item=>({value:item.position_id,label:item.name}))}/>
      <Button icon={<ReloadOutlined/>} loading={loading} disabled={!positionId} onClick={()=>positionId&&void loadPosition(positionId)}>刷新</Button>
    </Space></div>
    {error&&<Failure message={error.message} status={error.status} retry={()=>positionId&&void loadPosition(positionId)}/>}
    {!positionId&&!loading?<div className="state-panel"><Empty description="暂无已发布图谱的岗位"/></div>:loading&&!evolution?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载能力演化…</span></div>:evolution&&<CapabilityEvolutionReport evolution={evolution} positionName={positionName} onRetry={()=>positionId&&void loadPosition(positionId)}/>}
  </>;
}

function CapabilityEvolutionReport({evolution,positionName,onRetry}:{evolution:CapabilityEvolution;positionName:string;onRetry:()=>void}){
  const latest=evolution.comparisons[evolution.comparisons.length-1];
  const versions=evolution.frames;
  const versionPairs=evolution.comparisons.map(item=>({from_version_id:item.from_version_id,to_version_id:item.to_version_id}));
  return <section className="capability-evolution-report">
    <div className="analysis-surface-head"><div><Typography.Title level={4}>{positionName}演化报告</Typography.Title><Typography.Text type="secondary">{evolution.frame_count} 个已发布图谱版本 · {evolution.event_count} 条能力变化记录</Typography.Text></div><Tag icon={<HistoryOutlined/>}>图谱版本对比</Tag></div>
    {evolution.frame_count<2?<div className="state-panel"><Empty description="能力演化至少需要 2 个已发布图谱版本"/></div>:<>
      <div className="bubble-stat-grid capability-version-stats"><div><span>已发布版本</span><Statistic value={evolution.frame_count} suffix="个"/></div><div><span>新增能力</span><Statistic value={latest?.summary.added||0} suffix="项"/></div><div><span>退出能力</span><Statistic value={latest?.summary.removed||0} suffix="项"/></div><div><span>权重或证据变化</span><Statistic value={latest?.summary.changed||0} suffix="项"/></div></div>
      <SkillEvolutionBubbleView evolution={evolution} positionName={positionName}/>
      <EventTimeline positionName={positionName} versions={versions} versionPairs={versionPairs} events={evolution.events} loading={false} onRetry={onRetry}/>
    </>}
  </section>;
}

export function TrendIntelligenceWorkbench(){
  const {message}=App.useApp();
  const {can,user}=useAuth();
  const cacheUserId=user?.user_id||'';
  const [searchParams]=useSearchParams();
  const routePositionId=searchParams.get('positionId')||undefined;
  const resultReference=searchParams.get('resultReference')||'';
  const routeReportId=resultReference.startsWith('trend_report:')?resultReference.slice('trend_report:'.length):resultReference||undefined;
  const [positions,setPositions]=useState<Position[]>([]);
  const [positionId,setPositionId]=useState<string>();
  const [reports,setReports]=useState<TrendReport[]>([]);
  const [selectedReportId,setSelectedReportId]=useState<string|undefined>(routeReportId);
  const [start,setStart]=useState('');
  const [end,setEnd]=useState('');
  const [loading,setLoading]=useState(true);
  const [reportsLoading,setReportsLoading]=useState(false);
  const [running,setRunning]=useState(false);
  const [error,setError]=useState<ApiError>();
  const [reportError,setReportError]=useState<ApiError>();

  useEffect(()=>{
    const cached=trendWorkbenchCache(cacheUserId);
    if(cached?.positions){
      const data=cached.positions;
      setPositions(data);
      const preferred=data.some(item=>item.position_id===routePositionId)
        ?routePositionId
        :data.some(item=>item.position_id===cached.lastPositionId)
          ?cached.lastPositionId
          :data[0]?.position_id;
      setPositionId(preferred);
      setLoading(false);
      return;
    }
    listPublishedPositions().then(data=>{
      setPositions(data);
      const cache=refreshTrendWorkbenchCache(cacheUserId);
      if(cache)cache.positions=data;
      setPositionId(data.some(item=>item.position_id===routePositionId)?routePositionId:data[0]?.position_id);
    }).catch(reason=>setError(reason as ApiError)).finally(()=>setLoading(false));
  },[cacheUserId,routePositionId]);

  const loadPosition=useCallback(async(idValue:string,force=false)=>{
    const cached=trendWorkbenchCache(cacheUserId);
    if(!force&&cached?.reportsByPosition.has(idValue)){
      const nextReports=cached.reportsByPosition.get(idValue)||[];
      setReports(nextReports);
      setSelectedReportId(current=>nextReports.some(item=>item.report_id===routeReportId)
        ?routeReportId
        :nextReports.some(item=>item.report_id===cached.selectedReportByPosition.get(idValue))
          ?cached.selectedReportByPosition.get(idValue)
          :nextReports.some(item=>item.report_id===current)?current:nextReports[0]?.report_id);
      setReportError(undefined);setLoading(false);setReportsLoading(false);
      return;
    }
    setLoading(true);setReportsLoading(true);setError(undefined);setReportError(undefined);
    setReports([]);setSelectedReportId(undefined);
    try{
      const nextReports=await listTrendReports(idValue);
      setReports(nextReports);
      const cache=refreshTrendWorkbenchCache(cacheUserId);
      if(cache)cache.reportsByPosition.set(idValue,nextReports);
      setSelectedReportId(current=>nextReports.some(item=>item.report_id===routeReportId)?routeReportId:nextReports.some(item=>item.report_id===current)?current:nextReports[0]?.report_id);
    }catch(reason){
      setReportError(reason as ApiError);
    }
    finally{setLoading(false);setReportsLoading(false)}
  },[cacheUserId,routeReportId]);

  useEffect(()=>{
    if(!positionId)return;
    const cache=refreshTrendWorkbenchCache(cacheUserId);
    if(cache)cache.lastPositionId=positionId;
    const timer=window.setTimeout(()=>void loadPosition(positionId),0);
    return ()=>window.clearTimeout(timer);
  },[cacheUserId,loadPosition,positionId]);

  const selectedReport=reports.find(item=>item.report_id===selectedReportId);
  const selectedReportIndex=selectedReport?reports.findIndex(item=>item.report_id===selectedReport.report_id):-1;
  const previousReport=selectedReportIndex>=0?reports[selectedReportIndex+1]:undefined;
  const changes=useMemo(()=>selectedReport?snapshotDiff(selectedReport,previousReport):[],[previousReport,selectedReport]);
  const selectedPosition=positions.find(item=>item.position_id===positionId);
  const canRunAnalysis=can('trend.run.manage');
  const canAnalyze=Boolean(selectedPosition?.skill_count);

  const run=async()=>{
    if(!positionId||!canRunAnalysis||!canAnalyze)return;
    setRunning(true);setError(undefined);
    const noticeKey='evolution-analysis-run';
    void message.loading({key:noticeKey,content:'正在提交趋势情报分析…',duration:0});
    try{
      const createdTask=await createTrendAnalysis(positionId,start||undefined,end||undefined);
      void message.loading({key:noticeKey,content:'趋势情报分析已提交，正在处理…',duration:0});
      const task=createdTask.canonical_status==='succeeded'?createdTask:await waitForTrendAnalysis(createdTask.task_id);
      const trendReportId=String(task.result_payload?.report_id||'');
      if(!trendReportId)throw new ApiError(500,'演化分析已完成，但没有返回报告 ID');
      await loadPosition(positionId,true);
      setSelectedReportId(trendReportId);
      void message.success({key:noticeKey,content:'趋势情报分析完成，报告已生成'});
    }catch(reason){
      const apiError=reason as ApiError;
      message.destroy(noticeKey);
      setError(new ApiError(apiError.status,localizeSystemMessage(apiError.message),apiError.traceId,apiError.details));
    }
    finally{setRunning(false)}
  };

  const publish=async()=>{
    if(!selectedReport?.publishable)return;
    setRunning(true);setError(undefined);
    try{const value=await publishTrendReport(selectedReport.report_id);setReports(items=>{
      const next=items.map(item=>item.report_id===value.report_id?value:item);
      const cache=refreshTrendWorkbenchCache(cacheUserId);
      if(cache&&positionId)cache.reportsByPosition.set(positionId,next);
      return next;
    });message.success('趋势报告已发布')}
    catch(reason){setError(reason as ApiError)}
    finally{setRunning(false)}
  };

  const review=async()=>{
    if(!selectedReport||!positionId)return;
    setRunning(true);setError(undefined);
    try{
      const reviewTaskId=selectedReport.review_task_id;
      const needsNewReview=!reviewTaskId||['rejected','modified'].includes(selectedReport.review_status||'')||(selectedReport.review_status==='approved'&&selectedReport.publication_blockers.includes('REVIEW_NOT_APPROVED'));
      if(needsNewReview)await createTrendReview(selectedReport.report_id);
      else if(selectedReport.review_status==='pending'&&reviewTaskId)await claimTrendReview(reviewTaskId);
      else if(selectedReport.review_status==='claimed'&&reviewTaskId)await approveTrendReview(reviewTaskId);
      await loadPosition(positionId,true);
      setSelectedReportId(selectedReport.report_id);
      message.success(selectedReport.review_status==='claimed'?'趋势报告审核已通过':selectedReport.review_task_id?'审核任务状态已更新':'审核任务已创建');
    }catch(reason){setError(reason as ApiError)}
    finally{setRunning(false)}
  };

  return <>
    <div className="page-heading">
      <div><Typography.Title level={2}>趋势情报</Typography.Title><Typography.Paragraph type="secondary">分析学术论文、政策、开源项目等外部来源，推测因为一个新的技能的出现，而可能带来的新的市场需求。</Typography.Paragraph></div>
    </div>
    <div className="evolution-position-toolbar">
      <Space wrap>
        <Select
          showSearch
          className="evolution-position-select"
          value={positionId}
          placeholder="选择标准岗位"
          onChange={value=>setPositionId(value)}
          filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())}
          options={positions.map(item=>({value:item.position_id,label:item.name}))}
        />
        <Button icon={<ReloadOutlined/>} loading={loading} disabled={!positionId} onClick={()=>positionId&&void loadPosition(positionId,true)}>刷新</Button>
      </Space>
    </div>

    {error&&<Failure message={error.message} status={error.status} retry={()=>positionId&&void loadPosition(positionId)}/>}
    {!positionId
      ?loading?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载趋势报告…</span></div>:<div className="state-panel"><Empty description="暂无标准岗位，请先完成岗位目录建设"/></div>
      :<div className={`evolution-workbench${reportsLoading||!selectedReport?' is-compact':''}`}>
        <aside className="report-rail">
          <div className="report-rail-head"><Typography.Text strong>分析快照</Typography.Text><Tag>{reports.length}</Tag></div>
          {canRunAnalysis?<div className="analysis-window">
            <label>起始日期<Input type="date" value={start} onChange={event=>setStart(event.target.value)}/></label>
            <label>结束日期<Input type="date" value={end} onChange={event=>setEnd(event.target.value)}/></label>
            <Button type="primary" icon={<PlayCircleOutlined/>} loading={running} disabled={!canAnalyze} onClick={()=>void run()}>运行新分析</Button>
          </div>:<Typography.Text className="analysis-window-readonly" type="secondary">当前账号可查看分析快照，但不能运行新分析。</Typography.Text>}
          <Collapse
            className="trend-report-history-collapse"
            items={[{
              key:'history',
              label:`历史快照（${reports.length}）`,
              children:<div className="report-list">
                {reportsLoading?<div className="report-list-loading"><Spin/><span>正在加载分析快照…</span></div>:reports.length===0?<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无分析快照"/>:reports.map((report,index)=><button className={report.report_id===selectedReportId?'is-selected':''} key={report.report_id} onClick={()=>{
                  setSelectedReportId(report.report_id);
                  const cache=refreshTrendWorkbenchCache(cacheUserId);
                  if(cache&&positionId)cache.selectedReportByPosition.set(positionId,report.report_id);
                }}>
                  <i/>
                  <span><strong>{report.time_window_start||'起点未返回'} — {report.time_window_end||'终点未返回'}</strong><small>外部多来源趋势分析</small><small>{reviewLabel[report.review_status||'']||'未审核'} · 来源覆盖 {report.source_coverage==null?'未返回':`${Math.round(report.source_coverage*100)}%`}</small></span>
                  {index===0&&<Tag>最新</Tag>}
                </button>)}
              </div>,
            }]}
          />
        </aside>
        <main className="evolution-report">
          {!canRunAnalysis&&<Alert type="info" title="当前账号仅可查看演化结果" description="当前账号没有运行新分析的权限；已有快照及可用的审核能力不受影响。"/>}
          {canRunAnalysis&&!canAnalyze&&<Alert type="warning" showIcon title="分析数据尚未就绪" description="这个岗位的当前图谱还没有标准技能节点。请先在图谱构建中发布包含岗位—技能关系的版本，再运行分析。"/>}
          {reportError&&<Alert announceOnceKey={`trend-report-error:${positionId}:${reportError.message}`} type="warning" showIcon title="趋势报告暂不可用" description={reportError.status===404?'当前岗位暂无趋势报告，可选择时间窗口运行分析。':reportError.message}/>}
          {reportsLoading
            ?<ReportLoading/>
            :!selectedReport
            ?<EmptyReport onRun={()=>void run()} running={running} canRunAnalysis={canRunAnalysis} dataReady={canAnalyze}/>
            :<TrendSurface report={selectedReport} previous={previousReport} changes={changes} running={running} canReview={can('trend.review.manage')} canPublish={can('trend.publish.manage')} onReview={()=>void review()} onPublish={()=>void publish()}/>}
        </main>
      </div>}
  </>;
}

function EmptyReport({onRun,running,canRunAnalysis,dataReady}:{onRun:()=>void;running:boolean;canRunAnalysis:boolean;dataReady:boolean}){
  return <div className="empty-analysis"><HistoryOutlined/><Typography.Title level={4}>尚未生成趋势报告</Typography.Title><Typography.Paragraph>{!canRunAnalysis?'当前账号可查看已有报告，但不能发起新的趋势分析。':dataReady?'运行分析后，系统会结合当前图谱与外部来源生成可审核趋势报告。':'当前图谱没有标准技能，暂时不能运行趋势分析。'}</Typography.Paragraph>{canRunAnalysis&&<Button type="primary" loading={running} disabled={!dataReady} onClick={onRun}>运行首次分析</Button>}</div>;
}

function ReportLoading(){
  return <div className="report-loading" aria-live="polite"><Spin size="large"/><Typography.Title level={4}>正在加载趋势报告</Typography.Title><Typography.Paragraph>正在读取已有分析快照与外部趋势结果…</Typography.Paragraph></div>;
}

function snapshotChangeDescription(item:SnapshotChange){
  if(item.type!=='changed')return item.type==='added'?'本次快照新增能力':'本次快照不再包含该能力';
  return item.fields?.map(field=>field==='weight'?`权重 ${Math.round((item.before?.weight||0)*100)}% → ${Math.round(item.skill.weight*100)}%`:field==='confidence'?'置信度发生变化':field==='importance_level'?'能力级别发生变化':field==='trend_score'?'趋势分数发生变化':'能力信息发生变化').join('、')||'能力信息发生变化';
}

function trendDirectionTone(direction:string|null){
  return direction==='declining'?'risk':direction==='new'||direction==='rising'?'stable':'neutral';
}

function trendSnapshotDescription(change:SnapshotChange|undefined,hasPrevious:boolean){
  if(!hasPrevious)return '首份趋势报告，没有上一报告可供图谱属性对比';
  return change?snapshotChangeDescription(change):'相对上一报告，图谱技能属性保持一致';
}

function compactGrowthRate(skill:TrendSkillDetail){
  if(skill.growth_rate==null)return skill.trend_direction==='new'?'新出现':'暂无基线';
  return `${skill.growth_rate>0?'+':''}${(skill.growth_rate*100).toFixed(1)}%`;
}

function SkillTrendModal({skill,change,hasPrevious,onClose}:{skill:TrendSkillDetail|undefined;change:SnapshotChange|undefined;hasPrevious:boolean;onClose:()=>void}){
  const showGrowth=Boolean(skill&&shouldShowGrowthRate(skill));
  return <Modal open={Boolean(skill)} title={skill?`${skill.skill_name} · 技能趋势明细`:'技能趋势明细'} footer={null} onCancel={onClose} destroyOnHidden>
    {skill&&<div className="trend-skill-modal">
      <Descriptions size="small" column={2} items={[
        {key:'direction',label:'趋势方向',children:directionLabel[skill.trend_direction||'']||'趋势方向待判断'},
        ...(showGrowth?[{key:'growth',label:'增长率',children:growthRateText(skill)}]:[]),
        {key:'current',label:'当前窗口信号',children:skill.current_window_signal??'未返回'},
        {key:'historical',label:'历史窗口信号',children:skill.historical_window_signal??'未返回'},
        {key:'score',label:'趋势分数',children:skill.trend_score},
        {key:'domain',label:'所属领域',children:domainLabel(skill.category)},
        {key:'weight',label:'岗位技能权重',children:`${Math.round(skill.weight*100)}%`},
        {key:'evidence',label:'外部趋势证据',children:`${skill.evidence_references.length} 条`},
      ]}/>
      <section><strong>快照对比</strong><p>{trendSnapshotDescription(change,hasPrevious)}</p></section>
    </div>}
  </Modal>;
}

function TrendSurface({report,previous,changes,running,canReview,canPublish,onReview,onPublish}:{report:TrendReport;previous?:TrendReport;changes:SnapshotChange[];running:boolean;canReview:boolean;canPublish:boolean;onReview:()=>void;onPublish:()=>void}){
  const [selectedSkillId,setSelectedSkillId]=useState<string>();
  const changesBySkillId=useMemo(()=>new Map(changes.map(item=>[item.skill.skill_id,item])),[changes]);
  const orderedSkillTrends=useMemo(()=>newSkillsFirst(report.skill_trends),[report.skill_trends]);
  const selectedSkill=report.skill_trends.find(item=>item.skill_id===selectedSkillId);
  const selectedChange=selectedSkill?changesBySkillId.get(selectedSkill.skill_id):undefined;
  const validComboShifts=report.skill_combo_shifts.filter(item=>item.from_combo.length>0&&item.to_combo.length>0);
  const needsNewReview=!report.review_task_id||['rejected','modified'].includes(report.review_status||'')||(report.review_status==='approved'&&report.publication_blockers.includes('REVIEW_NOT_APPROVED'));
  const reviewAction=needsNewReview?(report.review_task_id?'重新提交审核':'创建审核任务'):report.review_status==='pending'?'领取审核':report.review_status==='claimed'?'审核通过':undefined;
  return <div className="trend-surface">
    <div className="trend-report-head">
      <div><Typography.Title level={3}>{report.current_graph.position_name}趋势报告</Typography.Title><Typography.Text type="secondary">{report.time_window_start||'未设起始日'} — {report.time_window_end||'当前'} · 外部多来源趋势分析</Typography.Text></div>
      <Space wrap><StatusTag tone={report.status==='published'?'stable':'review'}>{report.status==='published'?'已发布':'审核草稿'}</StatusTag>{report.status==='draft'&&canReview&&reviewAction&&<Button loading={running} onClick={onReview}>{reviewAction}</Button>}{report.status==='draft'&&canPublish&&<Button type="primary" loading={running} disabled={!report.publishable} onClick={onPublish}>发布报告</Button>}</Space>
    </div>
    <Typography.Paragraph className="trend-summary">{readableNarrative(report.summary,'当前报告暂无可读总结。')}</Typography.Paragraph>
    <AnalysisBasis report={report}/>
    <div className="trend-section">
      <div className="trend-section-title"><Typography.Title level={5}>能力趋势与变化</Typography.Title><span>点击卡片查看技能趋势明细{previous?` · 对比 ${previous.time_window_end||'上一报告'}`:''}</span></div>
      {orderedSkillTrends.length?<div className="trend-change-grid">{orderedSkillTrends.map(skill=>{
        return <button type="button" className="trend-change-card" key={skill.skill_id} onClick={()=>setSelectedSkillId(skill.skill_id)} aria-label={`查看 ${skill.skill_name} 技能趋势明细`}>
          <header><StatusTag tone={trendDirectionTone(skill.trend_direction)}>{directionLabel[skill.trend_direction||'']||'待判断'}</StatusTag><span>{skill.evidence_references.length} 条外部证据</span></header>
          <strong>{skill.skill_name}</strong>
          <small>{domainLabel(skill.category)}</small>
          <div className="trend-change-metrics"><span>增长 {compactGrowthRate(skill)}</span><span>趋势分 {skill.trend_score.toFixed(2)}</span></div>
        </button>;
      })}</div>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前报告未返回技能趋势明细"/>}
    </div>
    <SkillTrendModal skill={selectedSkill} change={selectedChange} hasPrevious={Boolean(previous)} onClose={()=>setSelectedSkillId(undefined)}/>
    {(report.replaced_skills.length>0||validComboShifts.length>0)&&<div className="trend-section">
      <div className="trend-section-title"><Typography.Title level={5}>替代关系与技能组合</Typography.Title><span>来自远程分析结果，需人工复核</span></div>
      {report.replaced_skills.map(item=><div className="shift-row" key={`${item.declining_skill.skill_id}:${item.replacement_skill_name}`}><div className="shift-flow"><StatusTag tone="risk">{item.declining_skill.skill_name}</StatusTag><ArrowRightOutlined className="shift-arrow"/><StatusTag tone="stable">{item.replacement_skill_name}</StatusTag></div><p>{readableNarrative(item.reason,'分析结果显示存在能力替代关系。')}</p></div>)}
      {validComboShifts.map((item,index)=>{const narrative=comboNarrative(item.reason);return <div className="shift-row" key={index}><div className="shift-flow"><span>{item.from_combo.join(' + ')}</span><ArrowRightOutlined className="shift-arrow"/><strong>{item.to_combo.join(' + ')}</strong></div>{narrative&&<p>{narrative}</p>}</div>})}
    </div>}
  </div>;
}

function AnalysisBasis({report}:{report:TrendReport}){
  const matchedSkillCount=report.skill_trends.filter(skill=>(skill.evidence_references?.length||0)>0).length;
  return <section className="trend-section trend-basis">
    <div className="trend-section-title"><Typography.Title level={5}>分析依据</Typography.Title><span>版本、来源与技能级证据可追溯</span></div>
    <Descriptions className="trend-basis-descriptions" size="small" column={{xs:1,sm:2,lg:3}} items={[
      {key:'position',label:'关联标准岗位',children:report.current_graph.position_name},
      {key:'graph',label:'关联岗位图谱',children:`版本 ${report.current_graph.graph_version||report.graph_version_id||'未返回'}`},
      {key:'skills',label:'岗位技能匹配',children:`候选 ${report.current_graph.skills.length} 项，外部来源实际命中 ${matchedSkillCount} 项`},
      {key:'provider',label:'结果提供方',children:trendProviderLabel(report)},
      {key:'coverage',label:'来源覆盖率',children:report.source_coverage==null?'未返回':`${Math.round(report.source_coverage*100)}%`},
      {key:'review',label:'审核状态',children:reviewLabel[report.review_status||'']||'未审核'},
      {key:'missing',label:'缺失来源',children:report.missing_sources.length?report.missing_sources.map(sourceLabel).join('、'):'无'},
    ]}/>
  </section>;
}
