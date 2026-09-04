import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {App,Button,Descriptions,Empty,Input,Progress,Segmented,Select,Space,Table,Tag,Timeline,Typography} from 'antd';
import {ArrowRightOutlined,ReloadOutlined,RedoOutlined} from '@ant-design/icons';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {ApiError,localizeSystemMessage} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {StatusTag,type StatusTone} from '../../../shared/components/StatusTag';
import {useAuth} from '../../auth/AuthContext';
import {listPortalDemoTasks} from '../../demo/api';
import {resolveDemoTaskResult} from '../../demo/resultRoute';
import type {PortalDemoTask,PortalDemoTaskFilters,PortalDemoTaskStatus,PortalDemoTaskType} from '../../demo/types';
import {listExtractionTasks,listTasks,retryExtractionTask} from '../api';
import type {ExtractionTask,TaskRecord} from '../types';

type CenterItem=
  |{kind:'system';id:string;type:string;status:string;progress:number;attempts:number;updatedAt:string|null;record:TaskRecord}
  |{kind:'extraction';id:string;type:string;status:string;progress:number;attempts:number;updatedAt:string|null;record:ExtractionTask}
  |{kind:'demo';id:string;type:string;status:string;progress:number;attempts:number;updatedAt:string|null;record:PortalDemoTask}
  |{kind:'demo-group';groupType:PortalDemoTaskType;service:string;total:number;succeeded:number;failed:number;pending:number;running:number;cancelled:number;updatedAt:string|null}
  |{kind:'system-group';groupType:string;kinds:string[];total:number;succeeded:number;failed:number;pending:number;running:number;cancelled:number;attempts:number;updatedAt:string|null};

const statusLabel:Record<string,string>={pending:'等待中',running:'运行中',succeeded:'已完成',completed:'已完成',failed:'失败',cancelled:'已取消'};
const taskTypeLabel:Record<string,string>={
  jd_parse:'岗位描述解析',jd_extraction:'岗位描述结构化抽取',extraction:'岗位描述结构化抽取',
  resume_parse:'简历解析',cv_parse:'简历解析',cv_extraction:'简历结构化抽取',
  trend_analysis:'趋势情报分析',predicted_position_analysis:'趋势窗口分析',
  trend:'趋势情报分析',position_cluster:'新兴岗位发现',emerging_discovery:'新兴岗位发现',discovery:'新兴岗位发现',
  matching:'岗位匹配',match_evaluation:'岗位匹配评估',evaluation:'匹配评估',learning_path:'学习路径生成',
};
const objectTypeLabel:Record<string,string>={source_jd:'岗位描述',jd_parse_result:'岗位描述解析结果',data_validation_report:'数据质量报告',standard_position:'标准岗位',resume:'候选人简历',evaluation:'匹配评估',learning_path:'学习路径'};
const serviceLabel:Record<string,string>={
  'trend-intelligence':'趋势情报分析服务','trend_intelligence':'趋势情报分析服务','trend-intelligence-service':'趋势情报分析服务',
  'matching-service':'岗位匹配服务','main-system-bff':'主系统服务','jd-extraction':'岗位描述抽取服务','cv-extraction':'简历抽取服务',
};
const providerLabel:Record<string,string>={rule_based_jd_extraction:'规则抽取服务',http_jd_extraction:'岗位描述抽取服务',llm:'大模型抽取服务',rule:'规则抽取服务'};
const executionModeLabel:Record<string,string>={synchronous_local:'本地同步执行',remote_async_polling:'远程异步执行',asynchronous_remote:'远程异步执行',worker_async:'后台异步执行'};
const hasChinese=(value:string)=>/[\u3400-\u9fff]/.test(value);
const displayService=(value:string|undefined|null)=>value?(serviceLabel[value]||'后台任务服务'):'后台任务服务';
const displayProvider=(value:string|undefined|null)=>value?(providerLabel[value]||'抽取服务'):'抽取服务';
const displayError=(value:string|undefined|null,fallback='未提供失败详情')=>value?(hasChinese(value)?value:localizeSystemMessage(value)):fallback;
const relevantTask=(type:string)=>Boolean(taskTypeLabel[type]);
function taskStatus(status:string){
  const tone:StatusTone=status==='succeeded'||status==='completed'?'stable':status==='failed'?'risk':status==='running'?'review':'neutral';
  return <StatusTag tone={tone}>{statusLabel[status]||'状态待确认'}</StatusTag>;
}

function normalizeSystem(record:TaskRecord):CenterItem{
  return {kind:'system',id:record.task_id,type:record.task_type,status:record.canonical_status,progress:record.progress,attempts:record.attempt_count,updatedAt:record.updated_at,record};
}
function normalizeExtraction(record:ExtractionTask):CenterItem{
  const progress=record.status==='succeeded'?1:record.status==='running'?0.6:0;
  return {kind:'extraction',id:record.id,type:'extraction',status:record.status,progress,attempts:record.attempt_count,updatedAt:record.updated_at,record};
}
function normalizeDemo(record:PortalDemoTask):CenterItem{
  return {kind:'demo',id:record.task_id,type:record.task_type,status:record.status,progress:record.progress,attempts:0,updatedAt:record.updated_at,record};
}

function groupDemoTasks(tasks:PortalDemoTask[]):Extract<CenterItem,{kind:'demo-group'}>[]{
  const byType=new Map<PortalDemoTaskType,Extract<CenterItem,{kind:'demo-group'}>>();
  tasks.forEach(task=>{
    let group=byType.get(task.task_type);
    if(!group){
      group={kind:'demo-group',groupType:task.task_type,service:task.service,total:0,succeeded:0,failed:0,pending:0,running:0,cancelled:0,updatedAt:null};
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

function groupSystemItems(items:CenterItem[]):Extract<CenterItem,{kind:'system-group'}>[]{
  const byType=new Map<string,Extract<CenterItem,{kind:'system-group'}>>();
  items.forEach(item=>{
    if(item.kind!=='system'&&item.kind!=='extraction')return;
    let group=byType.get(item.type);
    if(!group){
      group={kind:'system-group',groupType:item.type,kinds:[],total:0,succeeded:0,failed:0,pending:0,running:0,cancelled:0,attempts:0,updatedAt:null};
      byType.set(item.type,group);
    }
    if(!group.kinds.includes(item.kind==='extraction'?'抽取流水线':'业务任务'))group.kinds.push(item.kind==='extraction'?'抽取流水线':'业务任务');
    group.total+=1;
    if(item.status==='succeeded'||item.status==='completed')group.succeeded+=1;
    else if(item.status==='failed')group.failed+=1;
    else if(item.status==='pending')group.pending+=1;
    else if(item.status==='running')group.running+=1;
    else group.cancelled+=1;
    group.attempts+=item.attempts;
    if(!group.updatedAt||String(item.updatedAt||'')>group.updatedAt)group.updatedAt=item.updatedAt;
  });
  return [...byType.values()];
}

function resultRoute(task:TaskRecord):string|undefined{
  const payload={...task.input_payload,...task.result_payload};
  const stringValue=(key:string)=>typeof payload[key]==='string'?payload[key] as string:undefined;
  const reference=task.result_reference||'';
  const [kind,referenceId]=reference.split(':',2);
  const evaluationId=stringValue('evaluation_id')||(kind==='evaluation_report'?referenceId:undefined);
  if(evaluationId&&['matching','match_evaluation','evaluation'].includes(task.task_type))return `/matching/reports/${encodeURIComponent(evaluationId)}`;
  if(task.task_type==='resume_parse'||task.task_type.startsWith('cv_'))return `/profile/resumes?resumeId=${encodeURIComponent(stringValue('resume_id')||referenceId||'')}`;
  if(task.task_type.startsWith('jd_'))return `/data/jds?jdId=${encodeURIComponent(stringValue('jd_id')||referenceId||'')}`;
  if(['trend_analysis','predicted_position_analysis'].includes(task.task_type)){
    const reportId=stringValue('report_id');
    const resultReference=task.result_reference||(task.task_type==='trend_analysis'&&reportId?`trend_report:${reportId}`:reportId)||task.task_id;
    const params=new URLSearchParams({resultReference});
    const positionId=stringValue('position_id');if(positionId)params.set('positionId',positionId);
    return `/analysis/trends?${params.toString()}`;
  }
  if(['position_cluster','emerging_discovery'].includes(task.task_type))return `/admin/discovery?runId=${encodeURIComponent(stringValue('run_id')||stringValue('discovery_run_id')||task.task_id)}`;
  if(task.task_type==='learning_path')return evaluationId?`/matching/reports/${encodeURIComponent(evaluationId)}`:'/matching';
  return undefined;
}

export function TaskCenter(){
  const {message}=App.useApp();
  const {can}=useAuth();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const routeType=(searchParams.get('type')||'').trim();
  const initialType=routeType&&(['jd_extraction','cv_extraction','trend','discovery','matching'] as const).includes(routeType as PortalDemoTaskType)?routeType as PortalDemoTaskType:undefined;
  const [view,setView]=useState<'overview'|'execution'>('overview');
  const [demoTasks,setDemoTasks]=useState<PortalDemoTask[]>([]);
  const [demoLoading,setDemoLoading]=useState(false);
  const [demoError,setDemoError]=useState<ApiError>();
  const [demoTaskType,setDemoTaskType]=useState<PortalDemoTaskType|undefined>(initialType);
  const [demoStatus,setDemoStatus]=useState<PortalDemoTaskStatus>();
  const [demoObjectId,setDemoObjectId]=useState<string>();
  const [items,setItems]=useState<Extract<CenterItem,{kind:'system'|'extraction'}>[]>([]);
  const [selectedKey,setSelectedKey]=useState<string>();
  const [loading,setLoading]=useState(true);
  const [actionLoading,setActionLoading]=useState('');
  const [query,setQuery]=useState('');
  const [error,setError]=useState<ApiError>();
  const demoRequestId=useRef(0);

  const demoFilters=useMemo<PortalDemoTaskFilters>(()=>({
    ...(demoTaskType?{task_type:demoTaskType}:{}),
    ...(demoStatus?{status:demoStatus}:{}),
    ...(demoObjectId?{object_id:demoObjectId}:{}),
  }),[demoObjectId,demoStatus,demoTaskType]);

  const invalidateDemoRequests=useCallback(()=>{
    demoRequestId.current+=1;
  },[]);

  const load=useCallback(async(preferredKey?:string)=>{
    setLoading(true);setError(undefined);
    try{
      const [system,extraction]=await Promise.all([listTasks(),listExtractionTasks()]);
      const data=[...system.filter(task=>relevantTask(task.task_type)).map(normalizeSystem),...extraction.items.map(normalizeExtraction)]
        .sort((a,b)=>String(b.updatedAt||'').localeCompare(String(a.updatedAt||''))) as Extract<CenterItem,{kind:'system'|'extraction'}>[];
      setItems(data);
      setSelectedKey(current=>{
        if(preferredKey&&data.some(item=>`${item.kind}:${item.id}`===preferredKey))return preferredKey;
        if(current&&data.some(item=>`${item.kind}:${item.id}`===current))return current;
        return data[0]&&`system-group:${data[0].type}`;
      });
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[]);

  const loadDemoTasks=useCallback(async(filters:PortalDemoTaskFilters)=>{
    const requestId=++demoRequestId.current;
    setDemoLoading(true);setDemoError(undefined);
    try{
      const data=await listPortalDemoTasks(filters);
      if(requestId!==demoRequestId.current)return;
      setDemoTasks(data);
      setSelectedKey(current=>current&&(data.some(task=>`demo:${task.task_id}`===current)||data.some(task=>`demo-group:${task.task_type}`===current))?current:data[0]&&`demo-group:${data[0].task_type}`);
    }catch(reason){
      if(requestId!==demoRequestId.current)return;
      setDemoTasks([]);
      setSelectedKey(undefined);
      setDemoError(reason as ApiError);
    }finally{
      if(requestId===demoRequestId.current)setDemoLoading(false);
    }
  },[]);

  useEffect(()=>{
    const timer=window.setTimeout(()=>{
      if(view==='overview'){
        if(can('integration.status.view'))void loadDemoTasks(demoFilters);
        return;
      }
      void load();
    },0);
    return()=>{
      window.clearTimeout(timer);
      invalidateDemoRequests();
    };
  },[can,demoFilters,invalidateDemoRequests,load,loadDemoTasks,view]);

  const demoItems=useMemo(()=>demoTasks.map(task=>normalizeDemo(task) as Extract<CenterItem,{kind:'demo'}>),[demoTasks]);
  const filtered=useMemo(()=>{
    const needle=query.trim().toLowerCase();
    return needle?items.filter(item=>`${item.type} ${item.id} ${item.kind}`.toLowerCase().includes(needle)):items;
  },[items,query]);
  const demoGroups=useMemo(()=>groupDemoTasks(demoTasks),[demoTasks]);
  const systemGroups=useMemo(()=>groupSystemItems(filtered),[filtered]);
  const selectedDemoGroup=demoGroups.find(item=>item.kind==='demo-group'&&`demo-group:${item.groupType}`===selectedKey) as Extract<CenterItem,{kind:'demo-group'}>|undefined;
  const selectedSystemGroup=systemGroups.find(item=>item.kind==='system-group'&&`system-group:${item.groupType}`===selectedKey) as Extract<CenterItem,{kind:'system-group'}>|undefined;
  const selected=(view==='overview'?demoItems:items).find(item=>`${item.kind}:${item.id}`===selectedKey);
  const demoObjectOptions=useMemo(()=>Array.from(new Map(demoTasks.map(task=>[
    task.object_id,
    {value:task.object_id,label:objectTypeLabel[task.object_type]||'业务记录'},
  ])).values()),[demoTasks]);

  const act=async(key:string,request:()=>Promise<unknown>,success:string)=>{
    setActionLoading(key);setError(undefined);
    try{await request();message.success(success);await load(selectedKey)}
    catch(reason){setError(reason as ApiError)}
    finally{setActionLoading('')}
  };

  return <>
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>任务中心</Typography.Title>
        <Typography.Paragraph type="secondary">查看解析、构建、发现、匹配等后台任务的处理进度与结果。</Typography.Paragraph>
      </div>
      {view==='execution'&&<Input.Search className="page-search" allowClear value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索任务类型或任务编号"/>}
    </div>
    <div className="task-center-controls">
      <Segmented<'overview'|'execution'>
        value={view}
        options={[{value:'overview',label:'业务总览'},{value:'execution',label:'执行明细'}]}
        onChange={value=>{invalidateDemoRequests();setView(value);setSelectedKey(undefined)}}
      />
      {view==='overview'&&<Space className="task-center-filters" wrap>
        <Select
          aria-label="任务类型"
          value={demoTaskType??''}
          options={[
            {value:'',label:'全部类型'},
            {value:'jd_extraction',label:'岗位描述结构化抽取'},
            {value:'cv_extraction',label:'简历结构化抽取'},
            {value:'trend',label:'能力演化分析'},
            {value:'discovery',label:'新兴岗位发现'},
            {value:'matching',label:'岗位匹配'},
          ]}
          onChange={value=>{invalidateDemoRequests();setDemoTaskType(value?value as PortalDemoTaskType:undefined)}}
        />
        <Select
          aria-label="任务状态"
          value={demoStatus??''}
          options={[
            {value:'',label:'全部状态'},
            {value:'pending',label:'等待中'},
            {value:'running',label:'运行中'},
            {value:'succeeded',label:'已完成'},
            {value:'failed',label:'失败'},
            {value:'cancelled',label:'已取消'},
          ]}
          onChange={value=>{invalidateDemoRequests();setDemoStatus(value?value as PortalDemoTaskStatus:undefined)}}
        />
        <Select
          aria-label="业务对象"
          value={demoObjectId??''}
          options={[{value:'',label:'全部业务对象'},...demoObjectOptions]}
          onChange={value=>{invalidateDemoRequests();setDemoObjectId(value||undefined)}}
        />
      </Space>}
    </div>
    {view==='overview'&&demoError&&<Failure message={demoError.message} status={demoError.status} retry={()=>void loadDemoTasks(demoFilters)}/>}
    {view==='execution'&&error&&<Failure message={error.message} status={error.status} retry={()=>void load(selectedKey)}/>}
    <div className="data-workbench task-workbench">
      <section className="task-list-section" aria-label="任务列表">
        <div className="section-toolbar task-list-heading">
          <Typography.Text strong>{view==='overview'?'业务任务总览':'任务执行明细'}</Typography.Text>
          {view==='overview'
            ?can('integration.status.view')&&<Button type="text" icon={<ReloadOutlined/>} loading={demoLoading} onClick={()=>void loadDemoTasks(demoFilters)}>刷新</Button>
            :<Button type="text" icon={<ReloadOutlined/>} loading={loading} onClick={()=>void load(selectedKey)}>刷新</Button>}
        </div>
        <div className="data-list">
        {view==='overview'
          ?can('integration.status.view')
            ?<Table
              className="primary-table"
              rowKey="groupType"
              loading={demoLoading}
              dataSource={demoGroups}
              pagination={false}
              locale={{emptyText:<Empty description="暂无业务任务"/>}}
              onRow={record=>({onClick:()=>setSelectedKey(`demo-group:${record.groupType}`),className:`demo-group:${record.groupType}`===selectedKey?'is-selected':''})}
              columns={[
                {title:'任务类型',dataIndex:'groupType',render:(value:PortalDemoTaskType)=><strong>{taskTypeLabel[value]||'后台任务'}</strong>},
                {title:'服务',dataIndex:'service',render:(value:string)=>displayService(value)},
                {title:'成功/总数',width:130,render:(_:unknown,record:Extract<CenterItem,{kind:'demo-group'}>)=><Typography.Text><strong>{record.succeeded}</strong> / {record.total}</Typography.Text>},
                {title:'失败',dataIndex:'failed',width:80},
                {title:'更新时间',dataIndex:'updatedAt',width:170,render:(value:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'-'},
              ]}
            />
            :<div className="task-permission-state"><Alert type="warning" showIcon title="权限不足" description="当前账户缺少业务任务总览查看权限。"/></div>
          :<Table
            className="primary-table"
            rowKey="groupType"
            loading={loading}
            dataSource={systemGroups}
            pagination={false}
            locale={{emptyText:<Empty description="暂无任务"/>}}
            onRow={record=>({onClick:()=>setSelectedKey(`system-group:${record.groupType}`),className:`system-group:${record.groupType}`===selectedKey?'is-selected':''})}
            columns={[
              {title:'任务',dataIndex:'groupType',render:(value:string)=><div className="table-primary"><strong>{taskTypeLabel[value]||'后台任务'}</strong><span>{systemGroups.find(item=>item.groupType===value)?.total??0} 项</span></div>},
              {title:'来源',dataIndex:'kinds',width:140,render:(value:string[])=><>{value.map(kind=><Tag key={kind}>{kind}</Tag>)}</>},
              {title:'成功/总数',width:130,render:(_:unknown,record:Extract<CenterItem,{kind:'system-group'}>)=><Typography.Text><strong>{record.succeeded}</strong> / {record.total}</Typography.Text>},
              {title:'失败',dataIndex:'failed',width:80},
              {title:'尝试',dataIndex:'attempts',width:72},
            ]}
          />}
        </div>
      </section>
      <aside className="context-panel" aria-label="任务详情">
        {view==='overview'&&!can('integration.status.view')
          ?<Empty description="无权查看业务任务详情"/>
          :!selected&&!selectedDemoGroup&&!selectedSystemGroup?<Empty description="选择任务查看执行详情"/>
          :selectedDemoGroup
          ?<DemoTaskGroupDetail group={selectedDemoGroup} tasks={demoTasks.filter(task=>task.task_type===selectedDemoGroup.groupType)} onSelect={setSelectedKey}/>
          :selectedSystemGroup
          ?<SystemTaskGroupDetail group={selectedSystemGroup} items={filtered.filter(item=>item.type===selectedSystemGroup.groupType) as Extract<CenterItem,{kind:'system'|'extraction'}>[]} onSelect={setSelectedKey}/>
          :selected&&selected.kind==='demo'
          ?<DemoTaskDetail item={selected} onView={route=>navigate(route)}/>
          :selected&&selected.kind==='system'
          ?<SystemTaskDetail item={selected} onView={route=>navigate(route)}/>
          :selected&&selected.kind==='extraction'
          ?<ExtractionTaskDetail item={selected} canRetry={can('integration.jd.retry')} loading={actionLoading} onAction={act} onView={route=>navigate(route)}/>
          :<Empty description="选择任务查看执行详情"/>}
      </aside>
    </div>
  </>;
}

function DemoTaskGroupDetail({group,tasks,onSelect}:{group:Extract<CenterItem,{kind:'demo-group'}>;tasks:PortalDemoTask[];onSelect:(key:string)=>void}){
  return <>
    <div className="context-panel-head task-group-detail-head">
      <Typography.Title level={4}>{taskTypeLabel[group.groupType]||'后台任务'}</Typography.Title>
      <Typography.Text type="secondary" className="task-group-detail-meta">{group.total} 项任务 · {displayService(group.service)}</Typography.Text>
      <Tag className="task-group-detail-count">{group.succeeded} 成功 · {group.failed} 失败</Tag>
    </div>
    <Table size="small" tableLayout="fixed" rowKey="task_id" dataSource={tasks} pagination={{pageSize:6,showSizeChanger:false}} onRow={record=>({onClick:()=>onSelect(`demo:${record.task_id}`)})} columns={[
      {title:'业务对象',width:112,render:(_:unknown,record:PortalDemoTask)=><div className="demo-task-object"><span>{objectTypeLabel[record.object_type]||'业务记录'}</span></div>},
      {title:'状态',dataIndex:'status',width:92,render:(value:string)=>taskStatus(value)},
      {title:'更新时间',dataIndex:'updated_at',render:(value:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'-'},
    ]}/>
  </>;
}

function SystemTaskGroupDetail({group,items,onSelect}:{group:Extract<CenterItem,{kind:'system-group'}>;items:Extract<CenterItem,{kind:'system'|'extraction'}>[];onSelect:(key:string)=>void}){
  return <>
    <div className="context-panel-head task-group-detail-head">
      <Typography.Title level={4}>{taskTypeLabel[group.groupType]||'后台任务'}</Typography.Title>
      <Typography.Text type="secondary" className="task-group-detail-meta">{group.total} 项任务</Typography.Text>
      <Tag className="task-group-detail-count">{group.succeeded} 成功 · {group.failed} 失败</Tag>
    </div>
    <Table size="small" rowKey={item=>`${item.kind}:${item.id}`} dataSource={items} pagination={{pageSize:6,showSizeChanger:false}} onRow={record=>({onClick:()=>onSelect(`${record.kind}:${record.id}`)})} columns={[
      {title:'任务',render:(_:unknown,item:Extract<CenterItem,{kind:'system'|'extraction'}>)=><div className="table-primary"><strong>{taskTypeLabel[item.type]||'后台任务'}</strong><Tag>{item.kind==='extraction'?'抽取流水线':'业务任务'}</Tag></div>},
      {title:'状态',dataIndex:'status',width:90,render:(value:string)=>taskStatus(value)},
      {title:'尝试',dataIndex:'attempts',width:60},
    ]}/>
  </>;
}

function DetailHead({item}:{item:Extract<CenterItem,{kind:'system'|'extraction'|'demo'}>}){
  return <div className="context-panel-head">
    <div><Typography.Title level={4}>{taskTypeLabel[item.type]||'后台任务'}</Typography.Title><Typography.Text type="secondary">后台处理记录</Typography.Text></div>
    {taskStatus(item.status)}
  </div>;
}

function SystemTaskDetail({item,onView}:{item:Extract<CenterItem,{kind:'system'}>;onView:(route:string)=>void}){
  const task=item.record;
  const route=resultRoute(task);
  return <><DetailHead item={item}/>
    <Descriptions size="small" column={2} items={[
      {key:'owner',label:'创建者',children:task.created_by||'系统'},
      {key:'attempt',label:'执行次数',children:task.attempt_count},
      {key:'mode',label:'执行方式',children:executionModeLabel[task.execution_mode]||'后台异步执行'},
      {key:'updated',label:'最近更新',children:task.updated_at?new Date(task.updated_at).toLocaleString('zh-CN'):'-'},
    ]}/>
    <Progress className="task-progress" percent={Math.round(task.progress*100)} status={task.canonical_status==='failed'?'exception':task.canonical_status==='succeeded'?'success':'active'}/>
    {task.error_message&&<Alert type="error" showIcon title="任务执行失败" description={displayError(task.error_message)}/>}
    <div className="task-log">
      <Typography.Title level={5}>执行日志</Typography.Title>
      <Timeline items={task.logs.map(log=>({color:log.status==='failed'?'#b94a3b':log.status==='succeeded'?'#1f883d':'#c1b8ad',children:<div><strong>{statusLabel[log.status]||'处理中'}</strong><span>{new Date(log.at).toLocaleString('zh-CN')}</span>{log.message&&<p>{log.message}</p>}</div>}))}/>
    </div>
    <div className="context-actions">
      {task.canonical_status==='succeeded'&&route&&<Button type="primary" icon={<ArrowRightOutlined/>} onClick={()=>onView(route)}>查看结果</Button>}
    </div>
  </>;
}

function DemoTaskDetail({item,onView}:{item:Extract<CenterItem,{kind:'demo'}>;onView:(route:string)=>void}){
  const task=item.record;
  const result=resolveDemoTaskResult(task);
  return <><DetailHead item={item}/>
    <Descriptions size="small" column={1} items={[
      {key:'task_type',label:'任务类型',children:taskTypeLabel[task.task_type]||'后台任务'},
      {key:'service',label:'处理服务',children:displayService(task.service)},
      {key:'object_type',label:'业务对象',children:objectTypeLabel[task.object_type]||'业务记录'},
      {key:'status',label:'任务状态',children:statusLabel[task.status]||'状态待确认'},
      {key:'progress',label:'处理进度',children:`${Math.round(task.progress*100)}%`},
      {key:'error.message',label:'失败原因',children:task.error?.message?displayError(task.error.message):'-'},
    ]}/>
    <Progress className="task-progress" percent={Math.round(task.progress*100)} status={task.status==='failed'?'exception':task.status==='succeeded'?'success':'active'}/>
    {task.status==='failed'&&<Alert type="error" showIcon title="任务执行失败" description={displayError(task.error?.message,'统一投影未提供失败详情')}/>}
    {task.status==='cancelled'&&<Alert type="warning" showIcon title="任务已取消" description="该任务已终止，不会产生新的业务结果。"/>}
    <div className="task-log">
      <Typography.Title level={5}>执行日志</Typography.Title>
      <Typography.Text type="secondary">统一投影未提供执行日志</Typography.Text>
    </div>
    <div className="demo-result-state">
      <Typography.Title level={5}>结果引用</Typography.Title>
      {result.path
        ?<Typography.Text type="secondary">已解析为正式业务结果页面。</Typography.Text>
        :<Alert type="info" title="结果暂不可查看" description={result.reason&&hasChinese(result.reason)?result.reason:'当前任务尚未生成可查看的业务结果。'}/>}
    </div>
    <div className="context-actions">
      {result.path&&<Button type="primary" icon={<ArrowRightOutlined/>} onClick={()=>onView(result.path!)}>查看结果</Button>}
    </div>
  </>;
}

function ExtractionTaskDetail({item,canRetry,loading,onAction,onView}:{item:Extract<CenterItem,{kind:'extraction'}>;canRetry:boolean;loading:string;onAction:(key:string,request:()=>Promise<unknown>,success:string)=>Promise<void>;onView:(route:string)=>void}){
  const task=item.record;
  return <><DetailHead item={item}/>
    <Descriptions size="small" column={1} items={[
      {key:'source',label:'源岗位描述',children:'已关联'},
      {key:'provider',label:'抽取提供方',children:displayProvider(task.provider)},
      {key:'attempt',label:'执行次数',children:`${task.attempt_count} / ${task.max_attempts}`},
      {key:'updated',label:'最近更新',children:new Date(task.updated_at).toLocaleString('zh-CN')},
    ]}/>
    <Progress className="task-progress" percent={Math.round(item.progress*100)} status={task.status==='failed'?'exception':task.status==='succeeded'?'success':'active'}/>
    {task.last_error_message&&<Alert type="error" showIcon title="抽取失败" description={displayError(task.last_error_message)}/>}
    <div className="extraction-result">
      <Typography.Title level={5}>结构化产物</Typography.Title>
      {task.bundle_payload?<Alert type="success" showIcon title="抽取包已生成" description="可导入为岗位描述草稿，进入人工审核与发布流程。"/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成抽取包"/>}
    </div>
    <div className="context-actions">
      {canRetry&&task.status==='failed'&&task.retryable&&<Button type="primary" icon={<RedoOutlined/>} loading={loading==='retryExtraction'} onClick={()=>void onAction('retryExtraction',()=>retryExtractionTask(task.id),'抽取任务已重新排队')}>重试抽取</Button>}
      {task.status==='succeeded'&&task.bundle_payload&&<Button type="primary" icon={<ArrowRightOutlined/>} onClick={()=>onView(`/data/jds?sourceVersionId=${encodeURIComponent(task.source_jd_version_id)}`)}>查看结果</Button>}
    </div>
  </>;
}
