/* eslint-disable react-refresh/only-export-components */
import {useCallback,useEffect,useMemo,useState} from 'react';
import {Button,Card,Empty,Spin,Tag,Typography} from 'antd';
import {ArrowRightOutlined,CheckCircleOutlined,ClockCircleOutlined,ExclamationCircleOutlined,LockOutlined,ReloadOutlined} from '@ant-design/icons';
import {Link} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {ToastAlert as Alert} from '../../../shared/components/States';
import {statusText} from '../../../shared/idText';
import {useAuth} from '../../auth/AuthContext';
import {getJDSummary} from '../../data/api';
import {listDiscoveryRuns,listPublishedEmerging} from '../../emerging/api';
import {getMatchEvaluation,listMatchEvaluations,listMyResumes} from '../../matching/api';
import {listPublishedPositions} from '../../positions/api';
import {listPortalDemoTasks} from '../api';
import type {PortalDemoTask} from '../types';

type StepTone='loading'|'ready'|'waiting'|'empty'|'failed'|'forbidden';
type DemoStep={key:string;label:string;detail:string;tone:StepTone;href?:string};
type DemoLane={key:string;title:string;description:string;steps:DemoStep[]};

const baseLanes:DemoLane[]=[
  {key:'jd',title:'岗位能力发布链',description:'岗位数据 → 人工审核 → 岗位发布 → 能力演化',steps:[
    {key:'jd',label:'岗位数据',detail:'正在读取岗位数据',tone:'loading'},
    {key:'review',label:'人工审核',detail:'正在读取审核状态',tone:'loading'},
    {key:'position',label:'岗位发布',detail:'正在读取已发布岗位',tone:'loading'},
    {key:'evolution',label:'能力演化',detail:'正在读取已发布能力图谱',tone:'loading'},
  ]},
  {key:'emerging',title:'新兴岗位发现链',description:'历史窗口 → 新兴岗位发现 → 证据引用 → 公开发布',steps:[
    {key:'window',label:'历史窗口',detail:'正在读取历史窗口',tone:'loading'},
    {key:'discovery',label:'新兴岗位发现',detail:'正在读取发现运行',tone:'loading'},
    {key:'evidence',label:'证据引用',detail:'正在读取证据引用',tone:'loading'},
    {key:'publish',label:'公开发布',detail:'正在读取公开发布岗位',tone:'loading'},
  ]},
  {key:'cv',title:'个人能力成长链',description:'简历上传 → 结果确认 → 匹配评估 → 差距分析 → 学习路径',steps:[
    {key:'cv',label:'简历上传',detail:'正在读取简历资源',tone:'loading'},
    {key:'confirm',label:'结果确认',detail:'正在读取确认结果',tone:'loading'},
    {key:'evaluation',label:'匹配评估',detail:'正在读取匹配评估',tone:'loading'},
    {key:'gap',label:'差距分析',detail:'正在读取差距分析',tone:'loading'},
    {key:'learning',label:'学习路径',detail:'正在读取学习路径',tone:'loading'},
  ]},
];

const cloneLanes=()=>baseLanes.map(lane=>({...lane,steps:lane.steps.map(step=>({...step}))}));
const errorText=(reason:unknown)=>(reason as ApiError)?.message||'资源读取失败';
// 有缓存就先渲染；TTL 只决定切回后是否再做一次后台静默刷新，不再阻塞页面。
const DEMO_OVERVIEW_CACHE_TTL_MS=60_000;
type LanesCache={key:string;lanes:DemoLane[];fetchedAt:number};
type TasksCache={key:string;tasks:PortalDemoTask[];error?:ApiError;fetchedAt:number};
let lanesCache:LanesCache|null=null;
let tasksCache:TasksCache|null=null;
const isFresh=(fetchedAt:number)=>Date.now()-fetchedAt<DEMO_OVERVIEW_CACHE_TTL_MS;
const readLanesCache=(key:string)=>lanesCache?.key===key?lanesCache:null;
const readTasksCache=(key:string)=>tasksCache?.key===key?tasksCache:null;
export function resetDemoOverviewCacheForTests(){lanesCache=null;tasksCache=null}
const toneMeta:Record<StepTone,{label:string;color?:string;icon:React.ReactNode}>={
  loading:{label:'读取中',icon:<ClockCircleOutlined/>},
  ready:{label:'已有资源',color:'success',icon:<CheckCircleOutlined/>},
  waiting:{label:'待处理',color:'warning',icon:<ClockCircleOutlined/>},
  empty:{label:'暂无数据',icon:<ClockCircleOutlined/>},
  failed:{label:'读取失败',color:'error',icon:<ExclamationCircleOutlined/>},
  forbidden:{label:'权限不足',icon:<LockOutlined/>},
};
/** 运行状态码统一走共享中文映射，原始状态码不透出。 */
const demoTaskTypeLabel:Record<PortalDemoTask['task_type'],string>={
  jd_extraction:'JD 结构化抽取',
  cv_extraction:'CV 结构化抽取',
  trend:'能力演化分析',
  discovery:'新兴岗位发现',
  matching:'岗位匹配',
};

type DemoTaskGroup={
  taskType:PortalDemoTask['task_type'];
  service:string;
  total:number;
  succeeded:number;
  failed:number;
  pending:number;
  running:number;
  cancelled:number;
  updatedAt:string|null;
};

function groupDemoTasks(tasks:PortalDemoTask[]):DemoTaskGroup[]{
  const byType=new Map<PortalDemoTask['task_type'],DemoTaskGroup>();
  tasks.forEach(task=>{
    let group=byType.get(task.task_type);
    if(!group){
      group={taskType:task.task_type,service:task.service,total:0,succeeded:0,failed:0,pending:0,running:0,cancelled:0,updatedAt:null};
      byType.set(task.task_type,group);
    }
    group.total+=1;
    if(task.status==='succeeded')group.succeeded+=1;
    else if(task.status==='failed')group.failed+=1;
    else if(task.status==='pending')group.pending+=1;
    else if(task.status==='running')group.running+=1;
    else group.cancelled+=1;
    if(!group.updatedAt||String(task.updated_at||'')>group.updatedAt)group.updatedAt=task.updated_at;
  });
  return [...byType.values()];
}

export function DemoOverview(){
  const {user,can}=useAuth();
  const cacheKey=useMemo(()=>[
    user?.user_id??'anonymous',
    user?.role??'none',
    can('integration.status.view')?'1':'0',
    can('kg.review.manage')?'1':'0',
    can('catalog.read_published')?'1':'0',
    can('emerging.discovery.manage')?'1':'0',
    can('emerging.candidate.manage')?'1':'0',
  ].join('|'),[can,user?.role,user?.user_id]);
  const [lanes,setLanes]=useState<DemoLane[]>(()=>readLanesCache(cacheKey)?.lanes??cloneLanes());
  const [loaded,setLoaded]=useState(()=>Boolean(readLanesCache(cacheKey)));
  const [demoTasks,setDemoTasks]=useState<PortalDemoTask[]>(()=>readTasksCache(cacheKey)?.tasks??[]);
  const [demoTaskLoading,setDemoTaskLoading]=useState(false);
  const [demoTaskError,setDemoTaskError]=useState<ApiError|undefined>(()=>readTasksCache(cacheKey)?.error);
  const demoGroups=useMemo(()=>groupDemoTasks(demoTasks),[demoTasks]);

  const loadDemoTasks=useCallback(async()=>{
    if(!can('integration.status.view'))return;
    setDemoTaskLoading(true);setDemoTaskError(undefined);
    try{
      const tasks=await listPortalDemoTasks();
      setDemoTasks(tasks);
      tasksCache={key:cacheKey,tasks,fetchedAt:Date.now()};
    }catch(reason){
      const error=reason as ApiError;
      setDemoTasks([]);
      setDemoTaskError(error);
      tasksCache={key:cacheKey,tasks:[],error,fetchedAt:Date.now()};
    }finally{
      setDemoTaskLoading(false);
    }
  },[can,cacheKey]);

  useEffect(()=>{
    if(!can('integration.status.view'))return;
    const cached=readTasksCache(cacheKey);
    if(cached){
      setDemoTasks(cached.tasks);
      setDemoTaskError(cached.error);
      if(isFresh(cached.fetchedAt))return;
    }
    void loadDemoTasks();
  },[can,loadDemoTasks,cacheKey]);

  useEffect(()=>{
    const cached=readLanesCache(cacheKey);
    if(cached){
      setLanes(cached.lanes);
      setLoaded(true);
      if(isFresh(cached.fetchedAt))return;
    }
    let active=true;
    const run=async()=>{
      const next=cloneLanes();
      const set=(lane:string,key:string,value:Partial<DemoStep>)=>{
        const target=next.find(item=>item.key===lane)?.steps.find(item=>item.key===key);
        if(target)Object.assign(target,value);
      };

      let positions:Awaited<ReturnType<typeof listPublishedPositions>>=[];
      try{
        positions=await listPublishedPositions();
        set('jd','position',{tone:positions.length?'ready':'empty',detail:positions.length?`${positions.length} 个已发布岗位`:'尚无已发布岗位',href:'/positions'});
      }catch(reason){set('jd','position',{tone:'failed',detail:errorText(reason),href:'/positions'})}

      if(can('kg.review.manage')){
        try{
          const summary=await getJDSummary();
          set('jd','jd',{tone:summary.total?'ready':'empty',detail:summary.total?`${summary.total} 份岗位数据${summary.failed?` · ${summary.failed} 份失败`:''}`:'尚无岗位数据',href:'/data/jds'});
          set('jd','review',{tone:summary.awaiting_review?'waiting':summary.total?'ready':'empty',detail:summary.awaiting_review?`${summary.awaiting_review} 份等待审核`:summary.total?'当前无待审核':'尚无可审核数据',href:'/admin/review'});
        }catch(reason){set('jd','jd',{tone:'failed',detail:errorText(reason),href:'/data/jds'});set('jd','review',{tone:'failed',detail:errorText(reason),href:'/admin/review'})}
      }else{
        set('jd','jd',{tone:'forbidden',detail:'当前账号无权限'});set('jd','review',{tone:'forbidden',detail:'当前账号无权限'});
      }

      if(can('catalog.read_published')&&positions.length){
        const position=positions.find(item=>Boolean(item.current_version_id))||positions[0];
        const href=`/analysis/evolution?${new URLSearchParams({positionId:position.position_id})}`;
        set('jd','evolution',{tone:'ready',detail:`${position.name} 可查看能力演化`,href});
      }else if(!can('catalog.read_published'))set('jd','evolution',{tone:'forbidden',detail:'当前账号无权限'});
      else set('jd','evolution',{tone:'empty',detail:'没有已发布岗位，无法查看能力演化',href:'/analysis/evolution'});

      if(can('emerging.discovery.manage')){
        try{
          const runs=await listDiscoveryRuns();
          const latest=runs[0];
          const window=latest?.time_window_start||latest?.time_window_end?`${latest.time_window_start||'起点未提供'} — ${latest.time_window_end||'终点未提供'}`:'运行未提供时间窗口';
          set('emerging','window',{tone:latest?'ready':'empty',detail:latest?window:'尚无历史窗口',href:'/admin/discovery'});
          set('emerging','discovery',{tone:latest?latest.status==='failed'?'failed':latest.status==='succeeded'||latest.status==='completed'?'ready':'waiting':'empty',detail:latest?`最近一次运行${statusText(latest.status)}`:'尚未运行新兴岗位发现',href:'/admin/discovery'});
        }catch(reason){set('emerging','window',{tone:'failed',detail:errorText(reason),href:'/admin/discovery'});set('emerging','discovery',{tone:'failed',detail:errorText(reason),href:'/admin/discovery'})}
      }else{
        set('emerging','window',{tone:'forbidden',detail:'当前账号无权限'});set('emerging','discovery',{tone:'forbidden',detail:'当前账号无权限'});
      }

      try{
        const emerging=await listPublishedEmerging();
        const evidenceCount=new Set(emerging.flatMap(item=>item.evidence_jd_ids)).size;
        set('emerging','evidence',{tone:evidenceCount?'ready':'empty',detail:evidenceCount?`已引用 ${evidenceCount} 份岗位数据`:'已发布数据暂无证据引用',href:'/emerging'});
        set('emerging','publish',{tone:emerging.length?'ready':'empty',detail:emerging.length?`${emerging.length} 个公开新兴岗位`:'尚无公开新兴岗位',href:can('emerging.candidate.manage')?'/admin/emerging':'/emerging'});
      }catch(reason){set('emerging','evidence',{tone:'failed',detail:errorText(reason),href:'/emerging'});set('emerging','publish',{tone:'failed',detail:errorText(reason),href:'/emerging'})}

      if(user?.role==='personal_user'){
        try{
          const [resumes,evaluations]=await Promise.all([listMyResumes(),listMatchEvaluations()]);
          const latestResume=resumes[0];
          const confirmed=resumes.filter(item=>Boolean(item.validated_cv_snapshot_id)).length;
          set('cv','cv',{tone:latestResume?'ready':'empty',detail:latestResume?`${resumes.length} 份简历已上传`:'尚无简历',href:'/profile/resumes'});
          set('cv','confirm',{tone:confirmed?'ready':resumes.length?'waiting':'empty',detail:confirmed?`${confirmed} 份简历已确认`:resumes.length?'等待确认简历':'尚无可确认简历',href:'/profile/resumes'});
          const latest=evaluations[0];
          if(!latest){
            ['evaluation','gap','learning'].forEach(key=>set('cv',key,{tone:'empty',detail:key==='evaluation'?'尚无匹配评估':'需要先完成匹配评估',href:'/matching'}));
          }else{
            // 提交后的引用会先于远端 Evaluation 创建。用户此时离开匹配页时，
            // 列表中最新记录可能仍没有 evaluation_id，不能把 null 当成详情 ID。
            const workbenchParams=new URLSearchParams();
            if(latest.resume_id)workbenchParams.set('resumeId',latest.resume_id);
            if(latest.position_id)workbenchParams.set('positionId',latest.position_id);
            const workbenchHref=`/matching${workbenchParams.size?`?${workbenchParams}`:''}`;
            const taskParams=new URLSearchParams(workbenchParams);
            if(latest.task_id)taskParams.set('matchTaskId',latest.task_id);
            const taskHref=`/matching${taskParams.size?`?${taskParams}`:''}`;
            const latestCompleted=evaluations.find(item=>Boolean(item.evaluation_id));
            const evaluationHref=latest.evaluation_id
              ?`/matching/reports/${encodeURIComponent(latest.evaluation_id)}`
              :taskHref;
            set('cv','evaluation',{tone:latest.status==='failed'?'failed':latest.status==='succeeded'||latest.status==='completed'||latest.status==='current'?'ready':'waiting',detail:`最近一次评估${statusText(latest.status)}`,href:evaluationHref});
            if(!latestCompleted?.evaluation_id){
              set('cv','gap',{tone:'waiting',detail:'最近一次评估尚未生成报告',href:workbenchHref});
              set('cv','learning',{tone:'waiting',detail:'需要等待匹配报告完成',href:workbenchHref});
            }else{
              const href=`/matching/reports/${encodeURIComponent(latestCompleted.evaluation_id)}`;
              try{
                const evaluation=await getMatchEvaluation(latestCompleted.evaluation_id);
                const prioritized=evaluation.gap_analysis?.prioritized_gaps;
                const learning=evaluation.gap_analysis?.learning_path;
                const gaps=Array.isArray(prioritized)?prioritized.length:evaluation.evaluation.final_match_result?.gaps?.length??0;
                const steps=Array.isArray(learning)?learning.length:0;
                // 总览中的“差距分析”是能力匹配工作流入口，保留简历与岗位选择，
                // 由用户在工作台查看排名和历史报告，不直接打开某一份报告。
                set('cv','gap',{tone:gaps?'waiting':'ready',detail:gaps?`${gaps} 项结构化差距`:'当前评估未返回差距项',href:workbenchHref});
                set('cv','learning',{tone:steps?'ready':'empty',detail:steps?`${steps} 个学习阶段`:'尚未生成学习路径',href});
              }catch(reason){set('cv','gap',{tone:'failed',detail:errorText(reason),href});set('cv','learning',{tone:'failed',detail:errorText(reason),href})}
            }
          }
        }catch(reason){['cv','confirm','evaluation','gap','learning'].forEach(key=>set('cv',key,{tone:'failed',detail:errorText(reason)}))}
      }else{
        ['cv','confirm','evaluation','gap','learning'].forEach(key=>set('cv',key,{tone:'forbidden',detail:'仅个人工作区可访问'}));
      }
      if(active){
        lanesCache={key:cacheKey,lanes:next,fetchedAt:Date.now()};
        setLanes(next);
        setLoaded(true);
      }
    };
    void run();
    return()=>{active=false};
  },[can,user?.role,user?.user_id,cacheKey]);

  return <div className="demo-overview">
    <div className="page-heading">
      <Typography.Title level={2}>演示总览</Typography.Title>
      <Typography.Paragraph type="secondary">查看三条业务演示链路的当前进度，从这里进入各环节。</Typography.Paragraph>
    </div>
    {can('integration.status.view')&&<section className="demo-task-status" aria-labelledby="demo-task-status-title">
      <div className="demo-task-status-head">
        <div>
          <Typography.Title level={4} id="demo-task-status-title">当前运行状态</Typography.Title>
          <Typography.Text type="secondary">统一任务投影显示最近执行过程。</Typography.Text>
        </div>
        <Button icon={<ReloadOutlined/>} loading={demoTaskLoading} onClick={()=>void loadDemoTasks()}>刷新任务状态</Button>
      </div>
      {demoTaskError
        ?<div className="demo-task-status-state"><Alert type="error" showIcon title="任务状态读取失败" description={demoTaskError.message}/></div>
        :demoTaskLoading&&!demoTasks.length
          ?<div className="demo-task-status-state"><Spin/><Typography.Text type="secondary">正在读取任务状态…</Typography.Text></div>
          :!demoTasks.length
            ?<div className="demo-task-status-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无演示任务"/></div>
            :<div className="demo-task-status-list">
              {demoGroups.map(group=>{
                const inFlight=group.pending+group.running;
                const tone=group.failed?'error':inFlight?'processing':'success';
                return <article className={`demo-task-status-row demo-task-group is-${group.failed?'has-failed':inFlight?'running':'done'}`} key={group.taskType}>
                  <div className="demo-task-status-main">
                    <div className="demo-task-status-title">
                      <Typography.Text strong>{demoTaskTypeLabel[group.taskType]||group.taskType}</Typography.Text>
                    </div>
                    <Typography.Text type="secondary">{group.service}</Typography.Text>
                  </div>
                  <div className="demo-task-status-badge">
                    <Tag color={tone}>{group.failed?`${group.failed} 失败`:inFlight?'进行中':'已完成'}</Tag>
                  </div>
                  <div className="demo-task-counts">
                    <span>成功</span>
                    <div className="demo-task-count-value"><strong>{group.succeeded}</strong><small>/ {group.total}</small></div>
                  </div>
                  <div className="demo-task-counts">
                    <span>失败</span>
                    <div className="demo-task-count-value"><strong>{group.failed}</strong><small>{inFlight?`${inFlight} 进行中`:''}</small></div>
                  </div>
                  <div className="demo-task-status-updated">
                    <span>更新时间</span>
                    <strong>{group.updatedAt?new Date(group.updatedAt).toLocaleString('zh-CN'):'-'}</strong>
                  </div>
                  <div className="demo-task-status-action">
                    <Link to={`/tasks?type=${encodeURIComponent(group.taskType)}`}><Button type="primary" icon={<ArrowRightOutlined/>}>查看详情</Button></Link>
                  </div>
                </article>;
              })}
            </div>}
    </section>}
    {!loaded&&<div className="demo-loading"><Spin/><Typography.Text type="secondary">正在读取业务资源…</Typography.Text></div>}
    <div className="demo-lanes">
      {lanes.map((lane,index)=><Card key={lane.key} className="demo-lane" title={<div><span>0{index+1}</span><Typography.Title level={4}>{lane.title}</Typography.Title></div>} extra={<Typography.Text type="secondary">{lane.description}</Typography.Text>}>
        <div className="demo-chain">
          {lane.steps.map((step,stepIndex)=>{const meta=toneMeta[step.tone];const body=<>
            <div className="demo-step-index">{stepIndex+1}</div>
            <div className="demo-step-copy"><Typography.Text strong>{step.label}</Typography.Text><div><Tag color={meta.color} icon={meta.icon}>{meta.label}</Tag></div><Typography.Text type="secondary">{step.detail}</Typography.Text></div>
            {step.href&&<span className="demo-step-enter">进入<ArrowRightOutlined/></span>}
          </>;return step.href
            ?<Link to={step.href} className={`demo-step is-${step.tone} is-clickable`} key={step.key}>{body}</Link>
            :<div className={`demo-step is-${step.tone}`} key={step.key}>{body}</div>})}
        </div>
      </Card>)}
    </div>
    {loaded&&lanes.every(lane=>lane.steps.every(step=>['empty','forbidden'].includes(step.tone)))&&<Empty description="当前账号没有可展示的链路资源"/>}
  </div>;
}
