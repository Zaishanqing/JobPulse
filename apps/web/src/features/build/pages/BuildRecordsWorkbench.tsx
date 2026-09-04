import {useCallback,useEffect,useState} from 'react';
import {App,Button,Card,Empty,Space,Spin,Table,Typography} from 'antd';
import {useNavigate,useSearchParams} from 'react-router-dom';

import {useAuth} from '../../auth/AuthContext';
import {autoReviewBuild,getPublishGate,invalidateBuildRuns,listBuildRuns,listCatalogAdminPositions,peekCachedBuildRuns,publishBuild} from '../api';
import type {BuildRun,PublishGate} from '../types';
import {ApiError} from '../../../shared/api';
import {Failure,ToastAlert as Alert,type LoadState} from '../../../shared/components/States';

function gateRequirementRows(gate:PublishGate){
  const count=(rule:string)=>gate.errors.filter(item=>item.rule===rule).length;
  const versionError=gate.errors.find(item=>item.rule==='graph_version_current');
  const statusFor=(blocked:boolean,warning=false)=>blocked?'blocked':warning?'warning':'passed';
  return [
    {key:'build_status',name:'构建状态',status:statusFor(Boolean(count('build_status'))),detail:'构建必须已完成'},
    {key:'graph_version_current',name:gate.already_published?'发布状态':'草稿版本',status:statusFor(Boolean(versionError)),detail:gate.already_published?'该构建版本已经发布为正式图谱':versionError?.message==='draft base graph version no longer exists'?'草稿引用的原正式版本已不存在，请改用最新构建版本':versionError?'当前草稿不是基于最新正式版本，请重新构建':'草稿基于该岗位当前正式版本'},
    {key:'samples',name:'有效样本',status:statusFor(!gate.minimum_samples_met),detail:`${gate.valid_sample_count} / ${gate.minimum_valid_samples}`},
    {key:'position_status',name:'岗位状态',status:statusFor(Boolean(count('position_status'))),detail:'岗位必须处于可发布状态'},
    {key:'non_empty',name:'图谱非空',status:statusFor(Boolean(count('non_empty_graph'))),detail:'至少包含技能、任务或职责之一'},
    {key:'support',name:'证据支撑',status:statusFor(Boolean(count('support_integrity'))),detail:'关系支撑必须完整'},
    {key:'unresolved',name:'未解析项',status:statusFor(false,gate.unresolved_count>0),detail:`${gate.unresolved_count} 项非阻塞提醒`},
    {key:'relations',name:'关系审核',status:statusFor(Boolean(count('relation_approval'))),detail:`${count('relation_approval')} 条关系待审核`},
    {key:'confidence',name:'中/低置信度审核',status:statusFor(Boolean(count('confidence_review')),gate.low_confidence_relation_count>0),detail:`${gate.low_confidence_relation_count} 条关系${count('confidence_review')?'待审核':'已自动通过'}`},
    {key:'open_review',name:'开放审查任务',status:statusFor(gate.open_review_task_count>0),detail:`${gate.open_review_task_count} 条任务待处理`},
    {key:'graph_review',name:'整图发布审查',status:statusFor(gate.open_review_task_count>0),detail:'包含整张图谱的发布前确认'},
    {key:'modality',name:'岗位要求类型',status:statusFor(Boolean(count('unknown_modality'))),detail:'不允许存在未知 modality'},
    {key:'evidence',name:'证据引用',status:statusFor(false,gate.non_exact_evidence_count>0),detail:`${gate.non_exact_evidence_count} 条非精确引用（非阻塞提醒）`},
  ];
}

const positionNameCache=new Map<string,string>();
const buildStatusLabels:Record<string,string>={published:'已发布',succeeded:'构建完成',draft:'草稿',reviewing:'审核中',approved:'已审核',pending:'等待中',failed:'失败'};

export function BuildRecordsWorkbench(){
  const {message}=App.useApp();
  const {can}=useAuth();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const positionId=searchParams.get('positionId')?.trim()??'';
  const [positionName,setPositionName]=useState(()=>positionNameCache.get(positionId)??'');
  const cachedRuns=peekCachedBuildRuns(positionId);
  const [state,setState]=useState<LoadState<BuildRun[]>>(cachedRuns?{kind:'success',data:cachedRuns}:{kind:'loading'});
  const [selected,setSelected]=useState<BuildRun>();
  const [gate,setGate]=useState<PublishGate>();
  const [error,setError]=useState<ApiError>();
  const [publishing,setPublishing]=useState(false);
  const [autoReviewing,setAutoReviewing]=useState(false);

  const resolvePositionName=useCallback(async(target:string)=>{
    const cached=positionNameCache.get(target);
    if(cached)return cached;
    try{
      const page=await listCatalogAdminPositions({search:target,page_size:10});
      const found=page.items.find(item=>item.position_id===target);
      const name=found?.position_name??target;
      positionNameCache.set(target,name);
      return name;
    }catch{
      return target;
    }
  },[]);
  const load=useCallback(async()=>{
    if(!positionId){setPositionName('');setState({kind:'success',data:[]});return}
    setState({kind:'loading'});
    const runsPromise=listBuildRuns(positionId)
      .then(data=>({ok:true as const,data}))
      .catch((reason:ApiError)=>({ok:false as const,message:reason.message,status:reason.status}));
    const [runsResult,name]=await Promise.all([runsPromise,resolvePositionName(positionId)]);
    setPositionName(name);
    if(runsResult.ok)setState({kind:'success',data:runsResult.data});
    else setState({kind:'error',message:runsResult.message,status:runsResult.status});
  },[positionId,resolvePositionName]);
  useEffect(()=>{const id=requestAnimationFrame(()=>void load());return()=>cancelAnimationFrame(id)},[load]);

  const selectRun=async(run:BuildRun)=>{
    setSelected(run);setGate(undefined);setError(undefined);
    try{setGate(await getPublishGate(run.id))}
    catch(reason){setError(reason as ApiError)}
  };
  const publish=async()=>{
    if(!selected||!positionId||publishing)return;
    setPublishing(true);setError(undefined);
    try{
      await publishBuild(selected.id);
      invalidateBuildRuns(positionId);
      message.success('图谱版本已发布，检索索引将在后台建立');
      const returnTo=`/admin/build/records?positionId=${encodeURIComponent(positionId)}`;
      navigate(`/positions/${encodeURIComponent(positionId)}?returnTo=${encodeURIComponent(returnTo)}`);
    }catch(reason){setError(reason as ApiError)}
    finally{setPublishing(false)}
  };
  const runAutoReview=async()=>{
    if(!selected||autoReviewing)return;
    setAutoReviewing(true);setError(undefined);
    try{
      const result=await autoReviewBuild(selected.id);
      message.success(`自动审核完成：自动通过 ${result.auto_accepted_count} 条，仍需人工 ${result.requires_human_count} 条`);
      setGate(await getPublishGate(selected.id));
    }catch(reason){setError(reason as ApiError)}
    finally{setAutoReviewing(false)}
  };

  return <>
    {positionName&&<div style={{display:'flex',alignItems:'center',gap:12,marginBottom:28}}>
      <Typography.Text style={{fontSize:18,color:'var(--text-muted)',fontWeight:600}}>构建记录</Typography.Text>
      <span style={{fontSize:18,color:'#bbb3a8'}}>/</span>
      <Typography.Title level={2} style={{margin:0}}>{positionName}</Typography.Title>
    </div>}
    {!positionId&&<div className="center-loading"><Empty description="请从图谱构建列表选择岗位进入"/></div>}
    {positionId&&state.kind==='loading'&&<div className="center-loading"><Spin size="large"/><span className="state-panel-hint">正在加载{positionName||'该岗位'}构建记录</span></div>}
    {positionId&&state.kind==='error'&&<Failure {...state} retry={load}/>}
    {positionId&&state.kind==='success'&&(state.data.length===0
      ?<div className="center-loading"><Empty description="该岗位暂无构建记录"/></div>
      :<Table rowKey="id" dataSource={state.data} columns={[
        {title:'构建版本',dataIndex:'build_version',render:(value:number)=>`版本 ${value}`},
        {title:'状态',dataIndex:'status',render:(value:string)=>buildStatusLabels[value]||value},
        {title:'有效样本',render:(_:unknown,item:BuildRun)=>item.summary.included_samples??0},
        {title:'操作',render:(_:unknown,item:BuildRun)=>can('kg.build.manage')?<Space>
          <Button onClick={()=>{
            const returnTo=`/admin/build/records?positionId=${encodeURIComponent(positionId)}`;
            navigate(item.status==='published'
              ?`/positions/${encodeURIComponent(positionId)}?returnTo=${encodeURIComponent(returnTo)}`
              :`/positions/${encodeURIComponent(positionId)}?${new URLSearchParams({buildRunId:String(item.id),returnTo}).toString()}`);
          }}>{item.status==='published'?'查看图谱':'打开草稿'}</Button>
          <Button onClick={()=>void selectRun(item)}>{item.status==='published'?'查看发布结果':'检查发布'}</Button>
        </Space>:null},
      ]}/>)}
    {selected&&<Card title={`构建版本 ${selected.build_version}`} style={{marginTop:16}}>
      {gate&&<>
        <div className="gate-card-grid">
          {gateRequirementRows(gate).map(row=>(
            <div key={row.key} className={`gate-card is-${row.status}`}>
              <div className="gate-card-head">
                <span className="gate-card-name">{row.name}</span>
                <span className={`gate-card-status ${row.status}`}>{row.status==='passed'?'已满足':row.status==='warning'?'提醒':'待处理'}</span>
              </div>
              <div className="gate-card-detail">{row.detail}</div>
            </div>
          ))}
        </div>
      </>}
      {error&&<Alert className="publish-gate-error" type="error" title="操作失败" description={error.message||'请稍后重试。'}/>}
      <div className="publish-gate-actions">
        <Button loading={autoReviewing} disabled={!selected||autoReviewing||gate?.already_published} onClick={()=>void runAutoReview()}>自动审核</Button>
        <Button type="primary" loading={publishing} disabled={!gate?.allowed||publishing||gate?.already_published} onClick={()=>void publish()}>{gate?.already_published?'已经发布':'发布正式图谱'}</Button>
      </div>
    </Card>}
  </>;
}
