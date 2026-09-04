import {useEffect,useState} from 'react';
import type {ReactNode} from 'react';
import {App,Button,Card,Descriptions,Input,Select,Space,Tag,Tooltip,Typography} from 'antd';
import {ApiError} from '../../../shared/api';
import {ToastAlert as Alert} from '../../../shared/components/States';
import {statusText} from '../../../shared/idText';
import {executeWorkflowAction,getJDSyncStatus,getResumeParseResult,getWorkflowStatus,listPortalPositions,syncJD,updateResumeParseResult,type JDSyncStatus,type PortalPosition,type ResumeParseResult,type WorkflowAction,type WorkflowChain,type WorkflowStatus} from '../api';
import {listJDs} from '../../data/api';
import type {JDRecord} from '../../data/types';

type StageKey=Exclude<keyof WorkflowChain,'entity_id'|'actions'>;
const stages:Array<[StageKey,string]>=[['source','来源'],['extraction','抽取'],['validation','校验'],['draft','草稿'],['review','审核'],['publication','发布'],['outbox','外发'],['knowledge_graph','知识图谱'],['discovery','新兴发现'],['matching','岗位匹配']];
function Chain({title,value,loading,onAction,children}:{title:string;value:WorkflowChain;loading:boolean;onAction:(action:WorkflowAction)=>void;children?:ReactNode}){return <Card className={'profile'} size={'small'} title={title}>
  <Descriptions bordered size={'small'} column={2} items={stages.map(([key,label])=>{const stage=value[key] as WorkflowChain[typeof key];const flags=stage.details?.review_flags||[];return {key,label,children:<><Tag>{statusText(stage.status)}</Tag>{stage.error&&<Typography.Text type={'danger'}>处理失败：{stage.error.message}</Typography.Text>}{flags.length>0&&<Typography.Text type={'warning'}>待确认项：{flags.length} 项</Typography.Text>}</>}})}/>
  <Space className={'profile'} wrap>{value.actions.map(action=><Tooltip key={action.code} title={action.reason||action.permission}><Button disabled={!action.enabled} loading={loading&&action.enabled} onClick={()=>onAction(action)}>{action.code==='sync'?'同步':action.code==='parse'?'解析':action.code==='review'?'审核':action.code==='publish'?'发布':action.code==='retry'?'重试':'处理'}</Button></Tooltip>)}</Space>
  {children}
</Card>}

function CVFlow({value,positions,targetId,setTargetId,onView,onEdit}:{value:WorkflowChain;positions:PortalPosition[];targetId:string;setTargetId:(value:string)=>void;onView:()=>void;onEdit:()=>void}){
  const resumeId=String(value.draft.details?.resume_id||'');
  const canEdit=value.actions.some(action=>action.permission==='resume.parse.manage'&&action.authorized);
  const skills=Array.isArray(value.draft.details?.skills)?value.draft.details.skills as Array<Record<string,unknown>>:[];
  const report=value.matching.details?.report as Record<string,unknown>|null|undefined;
  return <Card className={'profile'} size={'small'} title={'CV 操作闭环'}>
    <Space wrap>
      <Button disabled={!resumeId} onClick={onView}>查看解析结果</Button>
      <Button disabled={!resumeId||!canEdit} onClick={onEdit}>编辑解析结果</Button>
      <Select showSearch style={{minWidth:320}} value={targetId||undefined} placeholder={'选择目标岗位'} onChange={setTargetId} options={positions.map(item=>({value:item.position_id,label:item.position_name}))}/>
    </Space>
    <Typography.Paragraph className={'profile'}>技能画像：{skills.length?skills.map(item=>String(item.name||item.skill_id)).join('、'):'尚未生成'}</Typography.Paragraph>
    {value.matching.status==='stale'&&<Alert type={'warning'} showIcon title={'简历或岗位数据已变化，请重新匹配'}/>}
    {report&&<Descriptions className={'profile'} bordered size={'small'} items={[
      {key:'evaluation',label:'评估记录',children:report.evaluation_id?'已生成':'尚未生成'},
      {key:'status',label:'状态',children:String(report.status||'-')},
      {key:'target',label:'目标岗位',children:positions.find(item=>item.position_id===String(report.target_id||report.position_id||''))?.position_name||'未选择'},
      {key:'provider',label:'执行服务',children:String(report.provider||'matching-service')},
    ]}/>}
  </Card>;
}

export function IntegrationWorkbench(){
  const {modal}=App.useApp();
  const [documentId,setDocumentId]=useState('');
  const [mapping,setMapping]=useState<JDSyncStatus>();
  const [jdId,setJdId]=useState('');
  const [cvTaskId,setCvTaskId]=useState('');
  const [workflow,setWorkflow]=useState<WorkflowStatus>();
  const [error,setError]=useState<ApiError>();
  const [loading,setLoading]=useState(false);
  const [positions,setPositions]=useState<PortalPosition[]>([]);
  const [jds,setJds]=useState<JDRecord[]>([]);
  const [targetId,setTargetId]=useState('');
  useEffect(()=>{listPortalPositions().then(setPositions).catch(()=>setPositions([]))},[]);
  useEffect(()=>{listJDs().then(setJds).catch((reason:ApiError)=>setError(reason))},[]);
  const run=async(operation:'status'|'sync')=>{
    if(!documentId.trim())return;
    setLoading(true);setError(undefined);
    try{setMapping(await (operation==='sync'?syncJD(documentId.trim()):getJDSyncStatus(documentId.trim())))}
    catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  };
  const inspect=async()=>{if(!jdId.trim()&&!cvTaskId.trim())return;setLoading(true);setError(undefined);try{setWorkflow(await getWorkflowStatus(jdId,cvTaskId))}catch(reason){setError(reason as ApiError)}finally{setLoading(false)}};
  const resumeId=String(workflow?.cv?.draft.details?.resume_id||'');
  const report=workflow?.cv?.matching.details?.report as Record<string,unknown>|null|undefined;
  const act=async(action:WorkflowAction)=>{setLoading(true);setError(undefined);try{
    const body=action.code==='create_match'?{resume_id:resumeId,target_type:'standard_position',target_id:targetId,use_enterprise_weights:false,generate_learning_path:false}:action.code==='create_learning_path'?{evaluation_id:String(report?.evaluation_id||'')}:undefined;
    if(action.code==='create_match'&&!targetId)throw new Error('请先选择目标岗位');
    await executeWorkflowAction(action,body);await inspect();
  }catch(reason){setError(reason as ApiError)}finally{setLoading(false)}};
  const viewParse=async()=>{if(!resumeId)return;try{const value=await getResumeParseResult(resumeId);modal.info({title:'CV 解析结果',width:900,content:<pre className={'versionSnapshot'}>{JSON.stringify(value,null,2)}</pre>})}catch(reason){setError(reason as ApiError)}};
  const editParse=async()=>{if(!resumeId)return;const value=await getResumeParseResult(resumeId);const editorId='cv-parse-result-editor';modal.confirm({title:'编辑 CV 解析结果',width:900,content:<Input.TextArea id={editorId} rows={20} defaultValue={JSON.stringify(value,null,2)}/>,onOk:async()=>{const raw=(document.getElementById(editorId) as HTMLTextAreaElement|null)?.value||'';await updateResumeParseResult(resumeId,JSON.parse(raw) as ResumeParseResult);await inspect()}})};
  return <>
    <div className="page-heading">
      <Typography.Title level={2}>数据同步</Typography.Title>
      <Typography.Paragraph type={'secondary'}>将主系统中已审核、版本化的 JD 快照同步给知识图谱服务。</Typography.Paragraph>
    </div>
    <Card className={'profile'} title={'JD 快照同步'}>
      <Space.Compact style={{width:'100%'}}>
        <Select showSearch style={{flex:1}} value={documentId||undefined} onChange={setDocumentId} placeholder="选择已审核 JD" filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())} options={jds.map(item=>({value:item.jd_id,label:`${item.title} · ${item.source_name||item.source_type}`}))}/>
        <Button loading={loading} onClick={()=>void run('status')}>查询状态</Button>
        <Button type={'primary'} loading={loading} onClick={()=>void run('sync')}>同步已审核快照</Button>
      </Space.Compact>
      {error&&<Alert className={'profile'} type={'error'} title={error.message} description="请求已记录，请稍后重试或联系管理员。"/>}
      {mapping&&<Descriptions className={'profile'} bordered size={'small'} items={[
        {key:'main',label:'主系统 JD',children:'已识别'},
        {key:'kg',label:'图谱快照',children:mapping.knowledge_graph_id?'已创建':'尚未创建'},
        {key:'status',label:'同步状态',children:mapping.sync_status},
        {key:'trace',label:'上游请求记录',children:mapping.last_trace_id?'已记录':'未返回'},
      ]}/>}
    </Card>
    <Card className={'profile'} title={'端到端工作流状态'}>
      <Typography.Paragraph type={'secondary'}>选择 JD，或输入 CV 抽取任务编号，查询该资源的端到端处理状态。</Typography.Paragraph>
      <Space.Compact style={{width:'100%'}}>
        <Select showSearch allowClear style={{width:'45%'}} value={jdId||undefined} onChange={value=>setJdId(value||'')} placeholder="选择 JD（可选）" filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())} options={jds.map(item=>({value:item.jd_id,label:`${item.title} · ${item.source_name||item.source_type}`}))}/>
        <Input style={{width:'45%'}} value={cvTaskId} onChange={event=>setCvTaskId(event.target.value)} placeholder={'CV 抽取任务编号（可选）'}/>
        <Button type={'primary'} loading={loading} onClick={()=>void inspect()}>查询全链路</Button>
      </Space.Compact>
      {workflow?.jd&&<Chain title={'JD 链路'} value={workflow.jd} loading={loading} onAction={action=>void act(action)}/>}
      {workflow?.cv&&<Chain title={'CV 链路'} value={workflow.cv} loading={loading} onAction={action=>void act(action)}><CVFlow value={workflow.cv} positions={positions} targetId={targetId} setTargetId={setTargetId} onView={()=>void viewParse()} onEdit={()=>void editParse()}/></Chain>}
    </Card>
  </>;
}
