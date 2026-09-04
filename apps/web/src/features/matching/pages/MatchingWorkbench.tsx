/* eslint-disable react-refresh/only-export-components */
import {useCallback,useEffect,useRef,useState} from 'react';
import {Button,Empty,Popconfirm,Select,Space,Spin,Tag,Typography} from 'antd';
import {HistoryOutlined,PlayCircleOutlined,ReloadOutlined,StopOutlined} from '@ant-design/icons';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {ApiError,errorTitle} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {useAuth} from '../../auth/AuthContext';
import {
  abandonMatchTask,
  cancelMatchRanking,
  createMatchTask,
  getMatchRanking,
  getMatchEvaluation,
  getMatchTask,
  getMatchPreflight,
  listMatchEvaluations,
  listMatchPositions,
  listMyResumes,
  prefetchMatchEvaluation,
  restartMatchTask,
  startMatchRanking,
} from '../api';
import {
  matchingMethodLabel,
  referenceStatusLabel,
} from '../viewModels/responsibility';
import {readablePositionName} from '../viewModels/presentation';
import type {
  MatchPosition,
  MatchPreflight,
  MatchRanking,
  MatchReference,
  MatchTask,
  ResumeRecord,
} from '../types';

// 页面切换会卸载工作台；模块级缓存让同一账户返回时先恢复轻量目录数据。
// TTL 到期只触发后台刷新，现有内容继续可用，不再用整页加载态阻塞操作。
const MATCHING_WORKBENCH_CACHE_TTL_MS=5*60_000;
type CachedRanking={value:MatchRanking;inputSignature:string};
type MatchingWorkbenchCache={
  key:string;
  resumes:ResumeRecord[];
  positions:MatchPosition[];
  reports:MatchReference[];
  rankings:Record<string,CachedRanking>;
  resumeId?:string;
  positionId?:string;
  fetchedAt:number;
};
let matchingWorkbenchCache:MatchingWorkbenchCache|null=null;
const readMatchingWorkbenchCache=(key:string)=>matchingWorkbenchCache?.key===key?matchingWorkbenchCache:null;
export function resetMatchingWorkbenchCacheForTests(){matchingWorkbenchCache=null}

// Only inputs that affect the ranking enter this signature. Unrelated database
// writes therefore keep the visible result stable, while a new CV snapshot or
// position profile/graph version invalidates the cached ranking deterministically.
const rankingInputSignature=(resumeId:string,resumes:ResumeRecord[],positions:MatchPosition[])=>{
  const snapshotId=resumes.find(item=>item.resume_id===resumeId)?.validated_cv_snapshot_id??'';
  const positionVersions=positions
    .filter(item=>item.matchable)
    .map(item=>`${item.position_id}:${item.position_profile_version??''}:${item.position_graph_version??''}`)
    .sort()
    .join('|');
  return `${snapshotId}::${positionVersions}`;
};

const sleep=(milliseconds:number)=>new Promise(resolve=>window.setTimeout(resolve,milliseconds));

const evaluationIdFromTask=(task:MatchTask)=>{
  if(task.evaluation_id)return task.evaluation_id;
  if(task.result_payload?.evaluation_id)return task.result_payload.evaluation_id;
  const reference=task.result_reference||'';
  const match=reference.match(/^matching_evaluation:([^:]+)$/)||reference.match(/^\/api\/v1\/matches\/reports\/([^/]+)$/);
  return match?match[1]:undefined;
};

const taskStatusLabel=(status:MatchTask['status'])=>({
  pending:'等待中',
  running:'数据处理中',
  succeeded:'已完成',
  failed:'失败',
}[status]);

const blockerLabels:Record<string,string>={
  CV_SNAPSHOT_UNAVAILABLE:'尚未确认简历快照',
  CV_PROFILE_UNAVAILABLE:'简历画像暂不可用',
  POSITION_PROFILE_UNAVAILABLE:'岗位画像暂不可用',
  POSITION_DEPRECATED:'岗位已下线',
  POSITION_GRAPH_VERSION_UNAVAILABLE:'岗位能力数据尚未同步',
};

const errorLabels:Record<string,string>={
  MATCHING_SERVICE_NOT_CONFIGURED:'匹配服务尚未就绪',
  MATCHING_SERVICE_UNAVAILABLE:'匹配服务暂时不可用',
  MATCHING_SERVICE_TIMEOUT:'匹配服务响应超时',
  MATCHING_REQUEST_REJECTED:'暂时无法发起本次匹配',
  MATCHING_RESOURCE_NOT_FOUND:'未找到本次匹配资源',
};

const friendlyMatchError=(code:string|null|undefined,message:string|null|undefined)=>{
  const mapped=errorLabels[code||'']||(
    message&&/^[\u4e00-\u9fff]/.test(message)?message:'当前无法完成本次匹配'
  );
  return mapped;
};

const rankingFailureLabel=(code:string|null|undefined)=>code==='MATCHING_RESULT_SCORE_MISSING'
  ?'评分结果未完整写回'
  :friendlyMatchError(code,undefined);

const resumeDate=(value:string|null)=>value?value.slice(0,10):'日期未知';

const resumeOptions=(items:ResumeRecord[])=>{
  const totals=new Map<string,number>();
  items.forEach(item=>totals.set(item.display_name,(totals.get(item.display_name)||0)+1));
  const seen=new Map<string,number>();
  return items.map(item=>{
    const order=(seen.get(item.display_name)||0)+1;
    seen.set(item.display_name,order);
    const duplicate=(totals.get(item.display_name)||0)>1;
    const name=duplicate?`简历 ${String(order).padStart(2,'0')}`:item.display_name;
    return {value:item.resume_id,label:`${name} · ${resumeDate(item.created_at)} · 已验证`};
  });
};

type ReadinessTone='ready'|'warning'|'blocking'|'neutral';

const readiness=(ready:boolean|undefined,loading:boolean,blocking:boolean):{tone:ReadinessTone;stateLabel:string}=>{
  if(loading)return {tone:'neutral',stateLabel:'检查中'};
  if(ready)return {tone:'ready',stateLabel:'已就绪'};
  return {tone:blocking?'blocking':'warning',stateLabel:blocking?'不可用':'待确认'};
};

function MatchReadinessGrid({preflight,preflightLoading,selectedResume,selectedPosition}:{preflight:MatchPreflight|undefined;preflightLoading:boolean;selectedResume:ResumeRecord|undefined;selectedPosition:MatchPosition|undefined}){
  const blockers=preflight?.blockers??[];
  const items=[
    {label:'简历快照',detail:preflight?.validated_cv_snapshot_id||selectedResume?.validated_cv_snapshot_id?'已验证快照':'需要先确认',...readiness(Boolean(preflight?.validated_cv_snapshot_id||selectedResume?.validated_cv_snapshot_id),preflightLoading,!selectedResume?.validated_cv_snapshot_id)},
    {label:'简历画像',detail:preflight?.cv_profile_ready?'可用于评分':'尚未生成正式画像',...readiness(preflight?.cv_profile_ready,preflightLoading,blockers.includes('CV_PROFILE_UNAVAILABLE'))},
    {label:'岗位画像',detail:preflight?.position_profile_ready?'已发布画像':'尚未准备完成',...readiness(preflight?.position_profile_ready,preflightLoading,blockers.includes('POSITION_PROFILE_UNAVAILABLE'))},
    {label:'岗位图谱',detail:preflight?.position_graph_version||selectedPosition?.position_graph_version?'已同步版本':'等待图谱同步',...readiness(Boolean(preflight?.position_graph_version||selectedPosition?.position_graph_version),preflightLoading,blockers.includes('POSITION_GRAPH_VERSION_UNAVAILABLE'))},
    {label:'正式评分',detail:preflight?.ready?'可以开始匹配':'仍有准备项未完成',...readiness(preflight?.ready,preflightLoading,blockers.length>0)},
  ];
  return <section className="match-readiness" aria-labelledby="match-readiness-title">
    <div className="match-subsection-head"><Typography.Title id="match-readiness-title" level={5}>匹配准备</Typography.Title></div>
    <div className="match-readiness-grid">
      {items.map(item=><div className="match-readiness-item" data-tone={item.tone} key={item.label}>
        <span className="match-readiness-label"><i aria-hidden="true"/>{item.label}</span>
        <strong>{item.label==='正式评分'&&preflightLoading?'检查中':item.label==='正式评分'&&preflight?.ready?'可开始':item.label==='正式评分'?'暂不可用':item.label==='简历快照'&&item.tone==='ready'?'已验证':item.label==='简历画像'&&item.tone==='ready'?'可用':item.label==='岗位画像'&&item.tone==='ready'?'可用':item.label==='岗位图谱'&&item.tone==='ready'?'已同步':item.stateLabel}</strong>
        <small>{item.detail}</small>
      </div>)}
    </div>
  </section>;
}

export function MatchingWorkbench(){
  const {user}=useAuth();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const routeResumeId=searchParams.get('resumeId')||undefined;
  const routePositionId=searchParams.get('positionId')||undefined;
  const routeMatchTaskId=searchParams.get('matchTaskId')||undefined;
  const cacheKey=`${user?.user_id??'anonymous'}:${user?.role??'none'}`;
  const initialCache=readMatchingWorkbenchCache(cacheKey);
  const [resumes,setResumes]=useState<ResumeRecord[]>(()=>initialCache?.resumes??[]);
  const [positions,setPositions]=useState<MatchPosition[]>(()=>initialCache?.positions??[]);
  const [reports,setReports]=useState<MatchReference[]>(()=>initialCache?.reports??[]);
  const [resumeId,setResumeId]=useState<string|undefined>(()=>routeResumeId??initialCache?.resumeId);
  const [positionId,setPositionId]=useState<string|undefined>(()=>routePositionId??initialCache?.positionId);
  const [activeMatchTask,setActiveMatchTask]=useState<MatchTask>();
  const [matchTaskLoading,setMatchTaskLoading]=useState(false);
  const [matchTaskError,setMatchTaskError]=useState<ApiError>();
  const [loading,setLoading]=useState(()=>!initialCache);
  const [positionsLoading,setPositionsLoading]=useState(()=>!initialCache);
  const [working,setWorking]=useState(false);
  const [preflight,setPreflight]=useState<MatchPreflight>();
  const [preflightLoading,setPreflightLoading]=useState(false);
  const [ranking,setRanking]=useState<MatchRanking>();
  const [rankingLoading,setRankingLoading]=useState(false);
  const [rankingActionLoading,setRankingActionLoading]=useState(false);
  const [rankingError,setRankingError]=useState(false);
  const [rankingReload,setRankingReload]=useState(0);
  const pollVersion=useRef(0);
  const rankingWasIncomplete=useRef(false);
  const rankingForceReload=useRef(false);

  const matchableResumes=resumes.filter(item=>Boolean(item.validated_cv_snapshot_id));

  const refreshReports=useCallback(async()=>{
    const values=await listMatchEvaluations();
    setReports(values);
    if(matchingWorkbenchCache?.key===cacheKey){
      matchingWorkbenchCache={...matchingWorkbenchCache,reports:values,fetchedAt:Date.now()};
    }
    return values;
  },[cacheKey]);

  const loadBase=useCallback(async()=>{
    const cached=readMatchingWorkbenchCache(cacheKey);
    if(cached){
      setResumes(cached.resumes);setPositions(cached.positions);setReports(cached.reports);
      setResumeId(routeResumeId&&cached.resumes.some(item=>item.resume_id===routeResumeId)?routeResumeId:cached.resumeId);
      setPositionId(routePositionId&&cached.positions.some(item=>item.position_id===routePositionId)?routePositionId:cached.positionId);
      setLoading(false);
      const routeResumeKnown=!routeResumeId||cached.resumes.some(item=>item.resume_id===routeResumeId);
      const routePositionKnown=!routePositionId||cached.positions.some(item=>item.position_id===routePositionId);
      if(Date.now()-cached.fetchedAt<MATCHING_WORKBENCH_CACHE_TTL_MS&&routeResumeKnown&&routePositionKnown)return;
    }else{
      setLoading(true);
      setPositionsLoading(true);
    }
    setMatchTaskError(undefined);
    let freshResumes=cached?.resumes??[];
    let freshReports=cached?.reports??[];
    let freshPositions=cached?.positions??[];

    // 简历与历史报告是首屏所需的轻量数据；岗位目录较慢，独立并行加载，
    // 避免没有简历时仍被整批岗位画像和图谱状态阻塞。
    const fastTask=Promise.all([listMyResumes(),listMatchEvaluations()]).then(([resumeValues,reportValues])=>{
      freshResumes=resumeValues;
      freshReports=reportValues;
      setResumes(resumeValues);setReports(reportValues);
      const matchable=resumeValues.filter(item=>Boolean(item.validated_cv_snapshot_id));
      setResumeId(current=>{
        if(matchable.some(item=>item.resume_id===current))return current;
        if(matchable.some(item=>item.resume_id===routeResumeId))return routeResumeId;
        return matchable[0]?.resume_id;
      });
      if(!cached)setLoading(false);
    });
    const positionsTask=listMatchPositions().then(positionValues=>{
      freshPositions=positionValues.filter(item=>item.matchable);
      setPositions(freshPositions);
      setPositionId(current=>{
        if(freshPositions.some(item=>item.position_id===current))return current;
        if(freshPositions.some(item=>item.position_id===routePositionId))return routePositionId;
        return freshPositions[0]?.position_id;
      });
    }).finally(()=>setPositionsLoading(false));

    const [fastResult,positionsResult]=await Promise.allSettled([fastTask,positionsTask]);
    if(fastResult.status==='fulfilled'&&positionsResult.status==='fulfilled'){
      matchingWorkbenchCache={key:cacheKey,resumes:freshResumes,positions:freshPositions,reports:freshReports,rankings:cached?.rankings??{},fetchedAt:Date.now()};
    }else if(!cached){
      setMatchTaskError((fastResult.status==='rejected'?fastResult.reason:positionsResult.status==='rejected'?positionsResult.reason:undefined) as ApiError);
    }
    if(!cached)setLoading(false);
  },[cacheKey,routePositionId,routeResumeId]);

  useEffect(()=>{
    const timer=window.setTimeout(()=>void loadBase(),0);
    return()=>window.clearTimeout(timer);
  },[loadBase]);

  useEffect(()=>{
    if(matchingWorkbenchCache?.key===cacheKey){
      matchingWorkbenchCache={...matchingWorkbenchCache,resumeId,positionId};
    }
  },[cacheKey,positionId,resumeId]);

  useEffect(()=>{
    let cancelled=false;
    let timer:number|undefined;
    const startId=window.setTimeout(()=>{
      if(cancelled)return;
      if(!resumeId){setRanking(undefined);setRankingLoading(false);setRankingError(false);return}
      if(positionsLoading)return;
      rankingWasIncomplete.current=false;
      const inputSignature=rankingInputSignature(resumeId,resumes,positions);
      const cachedEntry=matchingWorkbenchCache?.key===cacheKey?matchingWorkbenchCache.rankings[resumeId]:undefined;
      const cachedRanking=cachedEntry?.inputSignature===inputSignature?cachedEntry.value:undefined;
      const forceReload=rankingForceReload.current;
      rankingForceReload.current=false;
      setRanking(cachedRanking);
      const remember=(value:MatchRanking)=>{
        if(matchingWorkbenchCache?.key===cacheKey){
          matchingWorkbenchCache={...matchingWorkbenchCache,rankings:{...matchingWorkbenchCache.rankings,[resumeId]:{value,inputSignature}}};
        }
      };
      const poll=async(silent:boolean)=>{
        try{
          // 后台轮询不触发按钮 loading，否则生成期间刷新按钮每 1.2s 闪一次。
          if(!silent)setRankingLoading(true);
          const value=await getMatchRanking(resumeId);
          if(cancelled)return;
          if(!value||!Array.isArray(value.items))throw new Error('invalid matching ranking response');
          setRanking(value);setRankingError(false);
          if(!silent)setRankingLoading(false);
          remember(value);
          if(value.status==='completed'){
            if(rankingWasIncomplete.current){
              rankingWasIncomplete.current=false;
              void refreshReports();
            }
            return;
          }
          rankingWasIncomplete.current=true;
          if(value.status==='running'){
            timer=window.setTimeout(()=>void poll(true),1200);
          }
        }catch{
          if(cancelled)return;
          setRankingError(true);
          if(!silent)setRankingLoading(false);
        }
      };
      // Keep cached data visible, but still ask the backend for newer formal
      // results whenever this ranking view is entered or explicitly refreshed.
      void poll(!(forceReload||!cachedRanking));
    },0);
    return()=>{cancelled=true;window.clearTimeout(startId);if(timer!==undefined)window.clearTimeout(timer)};
  },[cacheKey,positions,positionsLoading,rankingReload,refreshReports,resumeId,resumes]);

  const rememberRanking=(value:MatchRanking)=>{
    setRanking(value);
    if(resumeId&&matchingWorkbenchCache?.key===cacheKey){
      const inputSignature=rankingInputSignature(resumeId,resumes,positions);
      matchingWorkbenchCache={...matchingWorkbenchCache,rankings:{...matchingWorkbenchCache.rankings,[resumeId]:{value,inputSignature}}};
    }
  };

  const generateRanking=async()=>{
    if(!resumeId)return;
    setRankingActionLoading(true);setRankingError(false);
    try{
      rememberRanking(await startMatchRanking(resumeId));
      setRankingReload(value=>value+1);
    }catch{setRankingError(true)}
    finally{setRankingActionLoading(false)}
  };

  const cancelRanking=async()=>{
    if(!resumeId)return;
    setRankingActionLoading(true);setRankingError(false);
    try{
      rememberRanking(await cancelMatchRanking(resumeId));
      setRankingReload(value=>value+1);
    }catch{setRankingError(true)}
    finally{setRankingActionLoading(false)}
  };

  const loadMatchTask=useCallback(async(taskId:string)=>{
    const generation=pollVersion.current+1;
    pollVersion.current=generation;
    setMatchTaskLoading(true);setMatchTaskError(undefined);
    const finalize=async(task:MatchTask)=>{
      if(task.status==='succeeded'){
        const evaluationId=evaluationIdFromTask(task);
        if(!evaluationId){
          setMatchTaskError(new ApiError(502,'匹配任务完成但未返回 evaluation_id'));
          return true;
        }
        try{await getMatchEvaluation(evaluationId)}catch{/* history refresh should still proceed */}
        void refreshReports();
        navigate(`/matching/reports/${encodeURIComponent(evaluationId)}`);
        return true;
      }
      if(task.status==='failed'){
        setMatchTaskError(new ApiError(502,friendlyMatchError(task.error_code,task.error_message)));
        return true;
      }
      return false;
    };
    try{
      const task=await getMatchTask(taskId);
      if(pollVersion.current!==generation)return;
      setActiveMatchTask(task);
      if(await finalize(task))return;
      // 匹配任务可能排队数分钟:持续跟踪直到终态并自动打开报告,
      // 单次轮询失败多为网络抖动,不中断跟踪。
      let consecutiveFetchErrors=0;
      for(let attempt=0;attempt<1600;attempt+=1){
        await sleep(750);
        if(pollVersion.current!==generation)return;
        let next:MatchTask;
        try{
          next=await getMatchTask(taskId);
          consecutiveFetchErrors=0;
        }catch{
          consecutiveFetchErrors+=1;
          if(consecutiveFetchErrors<20)continue;
          throw new ApiError(0,'多次刷新匹配状态失败,请检查网络后点击“刷新”重试');
        }
        if(pollVersion.current!==generation)return;
        setActiveMatchTask(next);
        if(await finalize(next))return;
      }
      // 约 20 分钟仍未结束:停止自动跟踪,面板保留手动刷新入口。
    }catch(reason){setMatchTaskError(reason as ApiError)}
    finally{setMatchTaskLoading(false)}
  },[navigate,refreshReports]);

  useEffect(()=>{
    if(!routeMatchTaskId)return;
    const timer=window.setTimeout(()=>void loadMatchTask(routeMatchTaskId),0);
    return()=>window.clearTimeout(timer);
  },[loadMatchTask,routeMatchTaskId]);

  useEffect(()=>{
    let disposed=false;
    const resetId=requestAnimationFrame(()=>{
      setPreflight(undefined);
      if(!resumeId||!positionId)return;
      setPreflightLoading(true);
      void getMatchPreflight(resumeId,positionId)
        .then(value=>{if(!disposed)setPreflight(value)})
        .catch(reason=>{if(!disposed)setMatchTaskError(reason as ApiError)})
        .finally(()=>{if(!disposed)setPreflightLoading(false)});
    });
    return()=>{disposed=true;cancelAnimationFrame(resetId)};
  },[resumeId,positionId]);

  const runMatch=async()=>{
    if(!resumeId||!positionId)return;
    setWorking(true);setMatchTaskError(undefined);
    try{
      // One user action gets one run identity. Network retries of the same
      // create call reuse this key, while the next explicit click generates a
      // new key and therefore a new matching task/evaluation.
      const runId=crypto.randomUUID();
      const task=await createMatchTask(resumeId,positionId,runId);
      setActiveMatchTask(task);
      void refreshReports();
      if(task.status==='succeeded'){
        const evaluationId=evaluationIdFromTask(task);
        if(!evaluationId){
          setMatchTaskError(new ApiError(502,'匹配任务完成但未返回 evaluation_id'));
          return;
        }
        try{await getMatchEvaluation(evaluationId)}catch{/* history refresh should still proceed */}
        void refreshReports();
        navigate(`/matching/reports/${encodeURIComponent(evaluationId)}`);
        return;
      }
      if(task.status==='failed'){
        setMatchTaskError(new ApiError(502,friendlyMatchError(task.error_code,task.error_message)));
        return;
      }
      navigate(`/matching?resumeId=${encodeURIComponent(resumeId)}&positionId=${encodeURIComponent(positionId)}&matchTaskId=${encodeURIComponent(task.task_id)}`);
    }catch(reason){setMatchTaskError(reason as ApiError)}
    finally{setWorking(false)}
  };

  const refreshMatchTask=()=>{
    if(activeMatchTask)void loadMatchTask(activeMatchTask.task_id);
  };

  const restartCurrentMatch=async()=>{
    if(!activeMatchTask)return;
    pollVersion.current+=1;
    setWorking(true);setMatchTaskError(undefined);
    try{
      const runId=crypto.randomUUID();
      const task=await restartMatchTask(activeMatchTask.task_id,runId);
      setActiveMatchTask(task);
      navigate(`/matching?resumeId=${encodeURIComponent(resumeId||'')}&positionId=${encodeURIComponent(positionId||'')}&matchTaskId=${encodeURIComponent(task.task_id)}`);
    }catch(reason){setMatchTaskError(reason as ApiError)}
    finally{setWorking(false)}
  };

  const abandonCurrentMatch=async()=>{
    if(!activeMatchTask)return;
    pollVersion.current+=1;
    setWorking(true);setMatchTaskError(undefined);
    try{
      await abandonMatchTask(activeMatchTask.task_id);
      // The backend keeps the terminal task for auditability, while the personal
      // workspace returns to its ready state and no longer advertises abandoned work.
      setActiveMatchTask(undefined);
      navigate(`/matching?resumeId=${encodeURIComponent(resumeId||'')}&positionId=${encodeURIComponent(positionId||'')}`,{replace:true});
    }catch(reason){setMatchTaskError(reason as ApiError)}
    finally{setWorking(false)}
  };

  const selectedResume=resumes.find(item=>item.resume_id===resumeId);
  const selectedPosition=positions.find(item=>item.position_id===positionId);
  const rankingCanGenerate=Boolean(ranking&&['ready','cancelled'].includes(ranking.status));
  const rankingFailedCount=ranking?.items.filter(item=>item.calculation_status==='failed').length??0;
  const rankingCanRetry=Boolean(ranking&&ranking.status==='completed'&&rankingFailedCount>0);
  const rankingHasDisplayableResult=Boolean(ranking?.items.length);
  const rankingStatusText=ranking?.status==='completed'
    ?`${ranking.completed}/${ranking.total} 已完成 · 已按当前输入版本保存${rankingFailedCount?` · ${rankingFailedCount} 项失败可重试`:''}`
    :ranking?.status==='running'
      ?`${ranking.completed}/${ranking.total} 已完成正式评分`
      :ranking?.status==='cancelled'
        ?`${ranking.completed}/${ranking.total} 已完成 · 生成已取消`
        :ranking
          ?'排名尚未生成，或当前匹配输入已更新'
          :'正在读取已保存排名';

  // 自动排名会为每个岗位留引用记录，只把手动发起的匹配放进历史栏，避免被批量结果刷屏。
  const manualReports=reports
    .filter(item=>item.origin!=='auto_ranking')
    .slice()
    .sort((left,right)=>Date.parse(right.created_at||'')-Date.parse(left.created_at||''));
  const latestReportId=manualReports[0]?.evaluation_id;

  return <div className="matching-page">
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>个人岗位匹配</Typography.Title>
        <Typography.Paragraph type="secondary">选择已确认的简历快照发起岗位匹配，匹配结论与依据在匹配报告中查看。</Typography.Paragraph>
      </div>
    </div>

    {matchTaskError&&<Failure message={errorTitle(matchTaskError)} status={matchTaskError.status} retry={()=>void loadBase()}/>}
    {loading?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>:(
      <div className="matching-workbench matching-workbench-slim">
        <main className="match-decision" aria-label="岗位匹配操作">
          {(activeMatchTask||matchTaskLoading)?<section className="match-task-panel" aria-label="匹配任务状态">
            <div className="match-task-head">
              <div>
                <Typography.Title id="match-launch-title" level={4}>匹配任务</Typography.Title>
                {!activeMatchTask&&<Typography.Text type="secondary">正在读取任务状态</Typography.Text>}
              </div>
              {activeMatchTask&&<Tag color={activeMatchTask.status==='succeeded'?'success':activeMatchTask.status==='failed'?'error':activeMatchTask.status==='running'?'processing':'default'}>
                {taskStatusLabel(activeMatchTask.status)}
              </Tag>}
            </div>
            {activeMatchTask&&<div className="match-task-meta">
              <div><span>任务状态</span><strong>{taskStatusLabel(activeMatchTask.status)}</strong></div>
              <div><span>处理进度</span><strong>{activeMatchTask.progress===null||activeMatchTask.progress===undefined?'处理中':`${Math.round(activeMatchTask.progress)}%`}</strong></div>
              <div><span>目标岗位</span><strong>{readablePositionName(selectedPosition?.position_name)}</strong></div>
            </div>}
            {activeMatchTask?.status==='failed'&&<Alert type="error" showIcon title="匹配任务失败" description={friendlyMatchError(activeMatchTask.error_code,activeMatchTask.error_message)}/>}
            {activeMatchTask&&['pending','running'].includes(activeMatchTask.status)&&<div className="match-task-running">
              <Typography.Text strong><Spin size="small"/> 任务仍在处理中</Typography.Text>
              <Typography.Text type="secondary">系统正在自动跟踪进度，完成后将自动打开匹配报告，无需手动刷新。</Typography.Text>
              <Space wrap>
              <Button icon={<ReloadOutlined/>} onClick={refreshMatchTask}>刷新<span className="match-visually-hidden">匹配状态</span></Button>
              <Popconfirm title="放弃当前任务？" description="任务将停止处理且不会创建新任务。" okText="确认放弃" cancelText="取消" onConfirm={()=>void abandonCurrentMatch()}>
                <Button danger loading={working}>放弃<span className="match-visually-hidden">任务</span></Button>
              </Popconfirm>
              <Popconfirm title="放弃当前任务并重新运行？" description="当前任务将标记为已放弃，系统会使用同一份简历和岗位创建新任务。" okText="放弃并重跑" cancelText="取消" onConfirm={()=>void restartCurrentMatch()}>
                <Button danger loading={working}>放弃并重新运行</Button>
              </Popconfirm>
              </Space>
            </div>}
          </section>:<section className="match-launch-panel">
            <div className="match-selection-grid">
              <div className="match-field">
                <label htmlFor="matching-resume">简历</label>
                <Select id="matching-resume" aria-label="简历" className="matching-select" value={resumeId} placeholder="选择已验证简历" onChange={setResumeId} options={resumeOptions(matchableResumes)} disabled={!matchableResumes.length}/>
              </div>
              <div className="match-field">
                <label htmlFor="matching-position">目标岗位</label>
                <Select id="matching-position" aria-label="目标岗位" showSearch className="matching-select" value={positionId} placeholder={positionsLoading?'正在后台加载岗位目录':'选择目标岗位'} onChange={setPositionId} optionFilterProp="label" options={positions.map(item=>({value:item.position_id,label:readablePositionName(item.position_name)}))} disabled={positionsLoading||!positions.length}/>
              </div>
            </div>

            <div className="match-selection-summary" aria-label="已选择对象摘要">
              <div className="match-selection-card">
                <span>简历摘要</span>
                <strong>{selectedResume?.display_name||'尚未选择已验证简历'}</strong>
                <small>{selectedResume?`${resumeDate(selectedResume.created_at)} · ${selectedResume.validated_cv_snapshot_id?'证据已验证':'未验证'}`:'完成审核后可用于匹配'}</small>
              </div>
              <div className="match-selection-card">
                <span>岗位摘要</span>
                <strong>{selectedPosition?readablePositionName(selectedPosition.position_name):'尚未选择目标岗位'}</strong>
                <small>{selectedPosition?`${selectedPosition.taxonomy_family_name||'未归类岗位'} · ${selectedPosition.position_graph_version?'图谱已就绪':'图谱待同步'}`:'选择已发布且可匹配岗位'}</small>
              </div>
            </div>

            {resumeId&&<section className="match-ranking" aria-labelledby="match-ranking-title">
              <div className="match-subsection-head">
                <div><Typography.Title id="match-ranking-title" level={5}>岗位匹配排名</Typography.Title><Typography.Text type="secondary">{rankingStatusText}</Typography.Text></div>
                <div className="match-ranking-actions">
                  {ranking&&<Tag color={ranking.status==='completed'?'success':ranking.status==='ready'?'warning':ranking.status==='cancelled'?'default':'processing'}>{ranking.status==='completed'?'已固定':ranking.status==='ready'?'待生成':ranking.status==='cancelled'?'已取消':'生成中'}</Tag>}
                  <Button size="small" icon={<ReloadOutlined/>} loading={rankingLoading&&!rankingActionLoading} onClick={()=>{rankingForceReload.current=true;setRankingReload(value=>value+1)}}>刷新</Button>
                  {rankingCanGenerate&&<Button size="small" type="primary" icon={<PlayCircleOutlined/>} loading={rankingActionLoading} onClick={()=>void generateRanking()}>{ranking?.status==='cancelled'?'继续生成':'生成排名'}</Button>}
                  {rankingCanRetry&&<Button size="small" type="primary" icon={<PlayCircleOutlined/>} loading={rankingActionLoading} onClick={()=>void generateRanking()}>重试失败项</Button>}
                  {ranking?.status==='running'&&<Popconfirm title="取消本次排名生成？" description="已启动的少量计算会完成并保留，其余岗位停止生成。" okText="确认取消" cancelText="返回" onConfirm={()=>void cancelRanking()}>
                    <Button size="small" danger icon={<StopOutlined/>} loading={rankingActionLoading}>取消生成</Button>
                  </Popconfirm>}
                </div>
              </div>
              {rankingError&&<Alert type="error" showIcon title="排名更新失败" description="正式评分结果未能完整写回，请重试生成排名。"/>}
              {rankingLoading&&!ranking?<div className="match-ranking-loading"><Spin/><Typography.Text type="secondary">正在读取已保存排名</Typography.Text></div>:rankingError&&!ranking?<div className="match-ranking-loading"><Button size="small" onClick={()=>{rankingForceReload.current=true;setRankingError(false);setRankingReload(value=>value+1)}}>重试</Button></div>:rankingHasDisplayableResult&&ranking?<div className="match-ranking-list">
                <table>
                  <thead><tr><th>排名</th><th>岗位</th><th>匹配度</th><th>计算状态</th></tr></thead>
                  <tbody>{ranking.items.map(item=><tr className={item.position_id===positionId?'is-selected':undefined} key={item.position_id} onClick={()=>setPositionId(item.position_id)}>
                    <td>{item.rank}</td><td>{readablePositionName(item.position_name)}</td><td><strong>{Math.round(item.score)}%</strong></td><td><Tag title={item.calculation_status==='failed'?rankingFailureLabel(item.error_code):undefined} color={item.calculation_status==='completed'?'success':item.calculation_status==='failed'?'error':item.calculation_status==='preliminary'?'default':'processing'}>{({preliminary:'初步排序',pending:'排队中',running:'同步中',completed:'已完成',failed:'评分失败'} as const)[item.calculation_status]}</Tag></td>
                  </tr>)}</tbody>
                </table>
              </div>:ranking?.status==='ready'?<div className="match-ranking-placeholder"><Typography.Text>点击“生成排名”计算并保存当前简历与岗位版本的匹配结果。</Typography.Text></div>:null}
            </section>}

            {resumeId&&positionId&&<MatchReadinessGrid preflight={preflight} preflightLoading={preflightLoading} selectedResume={selectedResume} selectedPosition={selectedPosition}/>}
            {preflight&&!preflight.ready&&<Alert type="warning" showIcon title="评分数据尚未就绪" description={(preflight.blockers??[]).map(code=>blockerLabels[code]||'当前数据暂不完整').join('、')}/>}

            {matchableResumes.length===0&&<div className="match-empty match-empty-compact">
              <HistoryOutlined/>
              <Typography.Title level={4}>还没有已验证简历</Typography.Title>
              <Typography.Paragraph>先在我的简历中完成证据审核并确认快照，才能发起岗位匹配。</Typography.Paragraph>
              <Typography.Text type="secondary">{positionsLoading?'岗位目录正在后台预加载，简历确认后无需重新等待。':'岗位目录已预加载，简历确认后可直接选择。'}</Typography.Text>
              <Button type="primary" onClick={()=>navigate('/profile/resumes')}>前往我的简历</Button>
            </div>}

            {matchableResumes.length>0&&positionsLoading&&positions.length===0&&<div className="match-empty match-empty-compact">
              <Spin/>
              <Typography.Title level={4}>岗位目录正在后台加载</Typography.Title>
              <Typography.Paragraph>简历和历史报告已可查看，岗位画像与图谱状态加载完成后即可选择岗位。</Typography.Paragraph>
            </div>}

            {matchableResumes.length>0&&!positionsLoading&&positions.length===0&&<div className="match-empty match-empty-compact">
              <PlayCircleOutlined/>
              <Typography.Title level={4}>暂无可匹配岗位</Typography.Title>
              <Typography.Paragraph>岗位需要具备已发布的岗位能力画像和图谱版本后才能发起匹配。</Typography.Paragraph>
            </div>}

            <div className="match-launch-action">
              <div><Typography.Text strong>准备匹配「{readablePositionName(selectedPosition?.position_name)}」</Typography.Text><Typography.Text type="secondary">系统会保存输入版本、算法版本和每项能力判断依据。</Typography.Text></div>
              <Button type="primary" icon={<PlayCircleOutlined/>} disabled={!resumeId||!positionId||matchableResumes.length===0||positions.length===0||!preflight?.ready||preflightLoading||working||matchTaskLoading} loading={working} onClick={()=>void runMatch()}><span>开始匹配</span><span className="match-visually-hidden">（运行匹配）</span></Button>
            </div>
          </section>}
        </main>

        <aside className="match-history" aria-label="历史报告">
          <div className="report-rail-head"><div><Typography.Text strong>历史报告</Typography.Text><Typography.Text type="secondary">已保存的个人匹配结论</Typography.Text></div><Tag>{manualReports.length}</Tag></div>
          <div className="match-history-list">
            {manualReports.length?manualReports.map(item=><button
              className={item.evaluation_id&&((searchParams.get('evaluationId')===item.evaluation_id)||(!searchParams.get('evaluationId')&&item.evaluation_id===latestReportId))?'is-selected':undefined}
              key={item.evaluation_id||item.task_id}
              disabled={!item.evaluation_id}
              onMouseEnter={()=>{if(item.evaluation_id)prefetchMatchEvaluation(item.evaluation_id)}}
              onFocus={()=>{if(item.evaluation_id)prefetchMatchEvaluation(item.evaluation_id)}}
              onClick={()=>{
                if(!item.evaluation_id)return;
                const knownName=positions.find(position=>position.position_id===item.position_id)?.position_name;
                navigate(`/matching/reports/${encodeURIComponent(item.evaluation_id)}`,{
                  state:{positionName:knownName?readablePositionName(knownName):undefined},
                });
              }}
            >
              <i/>
              <span>
                <strong>{readablePositionName(positions.find(position=>position.position_id===item.position_id)?.position_name)||'岗位匹配报告'}</strong>
                <small>
                  {item.overall_score!==undefined&&item.overall_score!==null?`${Math.round(item.overall_score)} 分`:'分数未返回'} · {item.created_at?.slice(0,10)||'日期未返回'} · {referenceStatusLabel(item.status)}
                  {(item.matching_method&&item.matching_method!=='unknown')?` · ${matchingMethodLabel(item.matching_method)}`:''}
                </small>
              </span>
            </button>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无历史报告"/>}
          </div>
        </aside>
      </div>
    )}
  </div>;
}
