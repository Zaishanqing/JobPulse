import {useCallback,useEffect,useMemo,useState,type ReactNode} from 'react';
import {Button,Card,Collapse,Descriptions,Empty,Form,Input,InputNumber,List,Modal,Select,Space,Spin,Statistic,Table,Tabs,Tag,Typography,message} from 'antd';
import {actOnGovernanceReview,actOnReview,batchReviewTasks,deprecateJD,getGovernanceReviewContext,getGovernanceReviewSummary,listGovernanceReviewTasks,listReviewTasks,mapReviewPosition,publishReviewedJD} from '../api';
import type {ReviewTask} from '../types';
import {ApiError} from '../../../shared/api';
import {Failure,ToastAlert as Alert,WorkbenchState,type LoadState} from '../../../shared/components/States';
import type {GovernanceReviewAction,GovernanceReviewContext,GovernanceReviewTask,PendingValidationReview,ReviewAction} from '../../../shared/api';
import {listCatalogAdminStandardPositions} from '../../evolution/api';
import type {StandardPosition} from '../../evolution/types';
import {useAuth} from '../../auth/AuthContext';
import {autoReviewBuild} from '../../build/api';
import {UnresolvedWorkbench} from '../../normalization/pages/UnresolvedWorkbench';

const labels:Record<ReviewAction,string>={claim:'领取任务',approve:'确认采用',reject:'退回修正',modify:'修改后采用'};
const fieldLabels:Record<string,string>={weight:'关系权重',confidence:'结论可信度',importance_level:'重要程度',status:'处理状态',position_id:'标准岗位',skill_id:'标准技能',canonical_name:'标准名称',source_name:'原文名称',reason:'需要审核的原因',reasons:'需要审核的原因',build_run_id:'构建版本',document_id:'来源 JD',requirement_id:'原文要求编号',modality:'岗位要求类型',position_skill_relation:'岗位与技能关系',position_task:'岗位职责归纳',position_requirement:'任职要求归纳',graph_version:'整张图谱发布检查'};
const labelFor=(key:string)=>fieldLabels[key]||key.replaceAll('_',' ');
const reasonLabels:Record<string,string>={unresolved_or_ambiguous_mapping:'该技能还没有唯一的标准名称',insufficient_evidence:'支持这项结论的 JD 原文太少',medium_or_low_confidence:'系统对这条关系把握不足',unknown_modality:'原文没有说明这是必备还是加分项',low_aggregate_confidence:'多条 JD 合并后，支持力度仍然不足',conflicting_requirements:'不同 JD 对同一要求说法不一致',low_confidence_merge:'多条职责是否属于同一件事还不确定',pre_publish_overall_review:'发布整张图谱前需要人工做最后确认'};
const graphStatusLabels:Record<string,string>={pending:'等待领取',claimed:'正在处理',modified:'已修改，等待确认',approved:'已采用',rejected:'已退回'};
const careerLevelOptions=[
  {value:'intern',label:'实习生'},{value:'junior',label:'初级'},{value:'mid',label:'中级'},
  {value:'senior',label:'高级'},{value:'expert',label:'专家'},{value:'unspecified',label:'暂不区分'},
];
const leadershipScopeOptions=[
  {value:'none',label:'无管理职责'},{value:'technical_lead',label:'技术负责人'},{value:'team',label:'团队负责人'},
  {value:'department',label:'部门负责人'},{value:'organization',label:'组织负责人'},{value:'executive',label:'高层管理'},
];
const technologyFocusOptions=[
  {value:'ARTIFICIAL_INTELLIGENCE',label:'人工智能'},{value:'LLM',label:'大语言模型'},{value:'AI_AGENT',label:'智能体'},
  {value:'RAG',label:'检索增强生成'},{value:'NLP',label:'自然语言处理'},{value:'COMPUTER_VISION',label:'计算机视觉'},
  {value:'MULTIMODAL',label:'多模态'},{value:'BIG_DATA',label:'大数据'},{value:'CLOUD_NATIVE',label:'云原生'},
  {value:'CYBERSECURITY',label:'网络安全'},{value:'IOT',label:'物联网'},{value:'ROBOTICS',label:'机器人'},
];
const industryContextOptions=[
  {value:'FINANCE',label:'金融'},{value:'HEALTHCARE',label:'医疗健康'},{value:'EDUCATION',label:'教育'},
  {value:'ECOMMERCE',label:'电商'},{value:'MANUFACTURING',label:'制造业'},{value:'GOVERNMENT',label:'政务'},
  {value:'ENTERPRISE_SERVICES',label:'企业服务'},{value:'TELECOMMUNICATIONS',label:'通信'},{value:'TRANSPORTATION',label:'交通运输'},
  {value:'ENERGY',label:'能源'},{value:'MEDIA',label:'媒体内容'},{value:'GAMING',label:'游戏'},
];
const validationFindingLabels:Record<string,{title:string;description:string}>={
  cross_source_exact_duplicate:{title:'与其他来源的内容完全重复',description:'这条结构化内容也出现在另一份来源数据中。它是重复采集警告，不会自动删除内容；请判断这是正常转载或多平台重复发布，还是来源数据需要修正。'},
  exact_duplicate_item:{title:'同一份 JD 内存在完全重复内容',description:'同一份 JD 的结构化结果中出现了重复项，需要修正后再发布。'},
  missing_evidence:{title:'缺少原文证据',description:'系统提取的内容没有可核对的原文证据。'},
  orphan_evidence:{title:'证据来源不一致',description:'证据指向的来源与当前 JD 不一致，需要人工核对。'},
  invalid_evidence_span:{title:'原文区间无效',description:'证据在原文中的起止位置无效。'},
  evidence_text_mismatch:{title:'证据文字与原文不一致',description:'记录的证据文字与对应原文片段不一致。'},
};
const reasonLabel=(value:string)=>reasonLabels[value]||value;
const asRecord=(value:unknown):Record<string,unknown>|undefined=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:undefined;

function StatusPill({tone,children}:{tone:'passed'|'warning'|'blocked'|'neutral';children:ReactNode}){
  return <span className={`status-pill ${tone}`}>{children}</span>;
}

function ReadableValue({value}:{value:unknown}):ReactNode{
  if(value===null||value===undefined||value==='')return <Typography.Text type="secondary">未提供</Typography.Text>;
  if(typeof value==='boolean')return <StatusPill tone={value?'passed':'neutral'}>{value?'是':'否'}</StatusPill>;
  if(typeof value==='string'||typeof value==='number')return <Typography.Text>{String(value)}</Typography.Text>;
  if(Array.isArray(value))return value.length?<Space wrap>{value.map((item,index)=><span key={index}>{typeof item==='object'?<Card size="small"><ReadableValue value={item}/></Card>:<Tag>{String(item)}</Tag>}</span>)}</Space>:<Typography.Text type="secondary">无</Typography.Text>;
  const record=asRecord(value);
  return record?<Descriptions size="small" bordered column={1} items={Object.entries(record).map(([key,item])=>({key,label:labelFor(key),children:<ReadableValue value={item}/>}))}/>:String(value);
}

function validationLocation(path:string){
  if(path.includes('.responsibilities['))return '岗位职责';
  if(path.includes('.requirements['))return '任职要求';
  if(path.includes('.company_facts['))return '公司信息';
  if(path.includes('.employment_facts['))return '招聘与用工信息';
  if(path.includes('.job_title'))return '岗位名称';
  return '当前 JD 的结构化数据';
}

function ValidationReview({data,onApprove,onReject}:{data:GovernanceReviewContext;onApprove:()=>void;onReject:()=>void}){
  const report=data.report||{};
  const findings=(Array.isArray(report.findings)?report.findings:[]).map(item=>asRecord(item)||{});
  const versionItems=[
    ['规则版本',report.ruleset_version],
    ['技能目录版本',report.catalog_snapshot_version],
    ['校验策略版本',report.policy_binding_version||data.policy_version],
  ].filter((item):item is [string,string]=>typeof item[1]==='string'&&Boolean(item[1]));
  return <Space direction="vertical" size={18} style={{width:'100%'}}>
    <div><Typography.Title level={4}>{data.conclusion==='block'?'存在阻止发布的数据问题':'发现需要确认的数据质量提醒'}</Typography.Title><Typography.Paragraph type="secondary">逐条核对下列问题。黄色提醒可以在确认属于正常重复后继续发布；红色问题需要退回修正。</Typography.Paragraph></div>
    <Table size="small" pagination={false} rowKey={(_,index)=>String(index)} dataSource={findings} scroll={{x:760}} columns={[
      {title:'影响',width:100,render:(_:unknown,item)=>item.severity==='block'?<StatusPill tone="blocked">阻止发布</StatusPill>:<StatusPill tone="warning">需要确认</StatusPill>},
      {title:'发现的问题',render:(_:unknown,item)=>{const label=validationFindingLabels[String(item.code)];return <Space direction="vertical" size={2}><Typography.Text strong>{label?.title||'数据质量检查发现异常'}</Typography.Text><Typography.Text type="secondary">{label?.description||String(item.message||'请对照原始 JD 核对该项内容。')}</Typography.Text></Space>}},
      {title:'涉及内容',width:150,render:(_:unknown,item)=>validationLocation(String(item.path||''))},
      {title:'重复来源',width:160,render:(_:unknown,item)=>{const details=asRecord(item.details);const sourceIds=Array.isArray(details?.source_ids)?details.source_ids:[];return sourceIds.length?<Typography.Text>{sourceIds.length} 条其他来源</Typography.Text>:<Typography.Text type="secondary">无</Typography.Text>}},
    ]}/>
    {versionItems.length>0&&<Descriptions
      size="small"
      column={1}
      items={versionItems.map(([label])=>({key:label,label,children:<Typography.Text>已绑定</Typography.Text>}))}
    />}
    <Space><Button type="primary" onClick={onApprove}>确认问题可接受，继续发布</Button><Button danger onClick={onReject}>问题不可接受，退回修正</Button></Space>
  </Space>;
}

function Comparison({task}:{task:ReviewTask}){
  const before=asRecord(task.original_content)||{};
  const after=asRecord(task.changed_content)||{};
  const keys=Array.from(new Set([...Object.keys(before),...Object.keys(after)]));
  if(!keys.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该任务没有字段变更"/>;
  return <Table size="small" pagination={false} rowKey="key" dataSource={keys.map(key=>({key,before:before[key],after:after[key]}))} columns={[{title:'字段',dataIndex:'key',render:(key:string)=><Typography.Text strong>{labelFor(key)}</Typography.Text>},{title:'原值',dataIndex:'before',render:(value:unknown)=><ReadableValue value={value}/>},{title:'待审核值',dataIndex:'after',render:(value:unknown)=><ReadableValue value={value}/>}]} />;
}

function evidenceDescription(item:ReviewTask['evidence'][number]){
  const relation='normalized_skill' in item?item:undefined;
  return [
    ...(relation?[{key:'skill',label:'标准技能',children:<Space><Typography.Text strong>{relation.normalized_skill.canonical_name}</Typography.Text><Tag>原文：{relation.normalized_skill.source_name}</Tag></Space>},{key:'requirement',label:'所在岗位要求',children:relation.original_requirement.text||relation.original_requirement.items?.map(value=>value.name).join('、')||'未提供结构化要求文本'}]:[]),
    {key:'quote',label:'对应原文',children:<Typography.Text>“{item.evidence.quote}”</Typography.Text>},
    {key:'source',label:'来源',children:'当前审核 JD'},
  ];
}

function GraphReviewWorkbench(){
  const [state,setState]=useState<LoadState<ReviewTask[]>>({kind:'loading'});
  const [detail,setDetail]=useState<ReviewTask>();
  const [decision,setDecision]=useState<{task:ReviewTask;action:ReviewAction}>();
  const [submitting,setSubmitting]=useState(false);
  const [actionError,setActionError]=useState<ApiError>();
  const [selectedTaskIds,setSelectedTaskIds]=useState<number[]>([]);
  const [batchSubmitting,setBatchSubmitting]=useState(false);
  const [autoReviewing,setAutoReviewing]=useState(false);
  const load=useCallback(()=>{
    Promise.all([listReviewTasks('pending'),listReviewTasks('claimed'),listReviewTasks('modified')])
      .then(([pending,claimed,modified])=>setState({kind:'success',data:[...pending,...claimed,...modified].filter(item=>item.object_type!=='skill_normalization'&&['pending','claimed','modified'].includes(item.status))}))
      .catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}));
  },[]);
  useEffect(()=>{void load()},[load]);
  const runBatchApprove=async()=>{
    if(!selectedTaskIds.length||batchSubmitting)return;
    setBatchSubmitting(true);
    try{
      await batchReviewTasks(selectedTaskIds.map(id=>`kg:${id}`),'approve','批量审核通过');
      message.success(`已批量通过 ${selectedTaskIds.length} 条图谱审核任务`);
      setSelectedTaskIds([]);
      await load();
    }catch(reason){message.error((reason as ApiError).message)}
    finally{setBatchSubmitting(false)}
  };
  const openItems=state.kind==='success'?state.data:[];
  const runAutoReviewAll=async()=>{
    if(autoReviewing)return;
    const buildRunIds=Array.from(new Set(
      openItems
        .filter(item=>item.build_run_id!=null&&['pending','claimed','modified'].includes(item.status))
        .map(item=>item.build_run_id as number)
    ));
    if(!buildRunIds.length)return;
    setAutoReviewing(true);
    try{
      let accepted=0;
      let human=0;
      for(const runId of buildRunIds){
        const result=await autoReviewBuild(runId);
        accepted+=result.auto_accepted_count;
        human+=result.requires_human_count;
      }
      message.success(`按新策略自动审核完成：自动通过 ${accepted} 条，仍需人工 ${human} 条`);
      setSelectedTaskIds([]);
      await load();
    }catch(reason){message.error((reason as ApiError).message)}
    finally{setAutoReviewing(false)}
  };
  const editableFields=useMemo(()=>{const source=asRecord(decision?.task.changed_content)||asRecord(decision?.task.original_content)||{};return Object.entries(source).filter(([,value])=>['string','number','boolean'].includes(typeof value))},[decision]);
  const submit=async(values:{reason:string;payload?:Record<string,unknown>})=>{if(!decision||submitting)return;setSubmitting(true);setActionError(undefined);try{await actOnReview(decision.task.id,decision.action,values.reason,decision.action==='modify'?values.payload:undefined);message.success(`审核操作“${labels[decision.action]}”已完成`);setDecision(undefined);setDetail(undefined);await load()}catch(reason){setActionError(reason as ApiError)}finally{setSubmitting(false)}};
  const generatedText=(task:ReviewTask)=>{const value=asRecord(task.changed_content)||asRecord(task.original_content)||{};return String(value.text||value.canonical_name||value.skill_id||labelFor(task.object_type))};
  return <><WorkbenchState title="待确认的图谱结论" state={state} retry={load} render={items=><Space direction="vertical" size={16} style={{width:'100%'}}>
    <Space size={24} wrap><Statistic title="待领取" value={items.filter(item=>item.status==='pending').length}/><Statistic title="处理中" value={items.filter(item=>['claimed','modified'].includes(item.status)).length}/></Space>
    <Space wrap>
      <Button type="primary" loading={batchSubmitting} disabled={!selectedTaskIds.length} onClick={()=>void runBatchApprove()}>批量通过（{selectedTaskIds.length}）</Button>
      <Button loading={autoReviewing} disabled={!openItems.some(item=>item.build_run_id!=null&&['pending','claimed','modified'].includes(item.status))} onClick={()=>void runAutoReviewAll()}>按新策略自动审核</Button>
      <Typography.Text type="secondary">仅可批量通过允许 approve 的待审核结论。</Typography.Text>
    </Space>
    <Table
      rowKey="id"
      dataSource={items}
      rowSelection={{
        selectedRowKeys:selectedTaskIds,
        onChange:keys=>setSelectedTaskIds(keys as number[]),
        getCheckboxProps:(item:ReviewTask)=>({disabled:!item.allowed_actions.includes('approve')}),
      }}
      columns={[
      {title:'所属岗位',render:(_:unknown,item:ReviewTask)=><Space direction="vertical" size={0}><Typography.Text strong>{item.position_name||'未知岗位'}</Typography.Text>{item.build_version!=null&&<Typography.Text type="secondary">构建版本 {item.build_version}</Typography.Text>}</Space>},
      {title:'系统归纳结果',render:(_:unknown,item:ReviewTask)=><Space direction="vertical" size={0}><Typography.Text>{generatedText(item)}</Typography.Text><Typography.Text type="secondary">{labelFor(item.object_type)}</Typography.Text></Space>},
      {title:'为什么需要你判断',render:(_:unknown,item:ReviewTask)=>{const reasons=item.payload.reasons||[item.payload.reason].filter(Boolean) as string[];return reasons.length?<Space direction="vertical" size={2}>{reasons.map(reason=><Typography.Text key={reason}>{reasonLabel(reason)}</Typography.Text>)}</Space>:<Typography.Text type="secondary">请核对合并前后的内容和原文证据</Typography.Text>}},
      {title:'处理进度',render:(_:unknown,item:ReviewTask)=><StatusPill tone={item.status==='approved'?'passed':item.status==='rejected'?'blocked':'warning'}>{graphStatusLabels[item.status]||'状态未知'}</StatusPill>},
      {title:'可核对的原文',render:(_:unknown,item:ReviewTask)=>`${item.evidence.length} 条`},
      {title:'操作',render:(_:unknown,item:ReviewTask)=><Space wrap><Button onClick={()=>setDetail(item)}>查看内容与证据</Button>{item.allowed_actions.map(action=><Button key={action} danger={action==='reject'} type={action==='approve'?'primary':'default'} disabled={submitting||(action==='modify'&&!Object.keys(asRecord(item.changed_content)||asRecord(item.original_content)||{}).length)} onClick={()=>{setDecision({task:item,action});setActionError(undefined)}}>{labels[action]}</Button>)}</Space>},
    ]}/>
  </Space>}/>
  <Modal title={detail?`核对 ${detail.position_name||'岗位'}的${labelFor(detail.object_type)}`:'核对图谱结论'} open={Boolean(detail)} width={1080} footer={null} onCancel={()=>setDetail(undefined)}>{detail?<Space direction="vertical" size={20} style={{width:'100%'}}>
    <div><Typography.Title level={4}>系统归纳结果</Typography.Title><Comparison task={detail}/></div>
    <div><Typography.Title level={4}>原文证据（{detail.evidence.length} 条）</Typography.Title>{detail.evidence.length?<List dataSource={detail.evidence} renderItem={item=><List.Item><Descriptions size="small" column={1} style={{width:'100%'}} items={evidenceDescription(item)}/></List.Item>}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有找到支持这项结论的 JD 原文"/>}</div>
    <div><Typography.Title level={4}>为什么需要人工判断</Typography.Title><ReadableValue value={detail.review_flags}/></div>
    <div><Typography.Title level={4}>处理影响</Typography.Title><ReadableValue value={detail.impact_scope}/></div>
    <div><Typography.Title level={4}>处理记录</Typography.Title>{detail.history.length?<Table size="small" pagination={false} rowKey="id" dataSource={detail.history} columns={[{title:'时间',dataIndex:'created_at'},{title:'操作',dataIndex:'action',render:(value:string)=>graphStatusLabels[value]||labels[value as ReviewAction]||value},{title:'操作人',dataIndex:'actor_id'},{title:'判断依据',dataIndex:'reason'}]}/>:<Typography.Text type="secondary">还没有处理记录</Typography.Text>}</div>
  </Space>:null}</Modal>
  <Modal title={`${decision?labels[decision.action]:''}图谱结论`} open={Boolean(decision)} footer={null} onCancel={()=>{if(!submitting)setDecision(undefined)}}>{actionError?<Typography.Paragraph type="danger">{actionError.message}</Typography.Paragraph>:null}<Form layout="vertical" onFinish={submit} initialValues={{payload:Object.fromEntries(editableFields)}}><Form.Item name="reason" label="判断依据" rules={[{required:true,message:'请填写判断依据'}]}><Input.TextArea placeholder="说明为什么采用、修改或退回这条结论"/></Form.Item>{decision?.action==='modify'?<div>{editableFields.length?editableFields.map(([key,value])=><Form.Item key={key} name={['payload',key]} label={labelFor(key)}>{typeof value==='number'?<InputNumber style={{width:'100%'}}/>:typeof value==='boolean'?<Select options={[{value:true,label:'是'},{value:false,label:'否'}]}/>:<Input/>}</Form.Item>):<Typography.Paragraph type="secondary">该任务没有可直接修改的结构化字段。</Typography.Paragraph>}</div>:null}<Button type="primary" htmlType="submit" loading={submitting} disabled={decision?.action==='modify'&&!editableFields.length}>确认{decision?labels[decision.action]:''}</Button></Form></Modal>
  </>;
}

const requirementKindLabels:Record<string,string>={task:'职责',education:'学历',experience:'经验',certificate:'证书',soft_skill:'软性要求',other:'其他要求'};
const extractionText=(item:{action?:string;text?:string;skills?:string[];minimum_degree?:string;duration_text?:string;evidence?:{quote?:string}})=>item.action||item.text||item.skills?.join('、')||item.minimum_degree||item.duration_text||item.evidence?.quote||'未提取到可展示内容';

function GovernanceReviewWorkbench(){
  const {can}=useAuth();
  const canPublish=can('jd.publish');
  const [state,setState]=useState<LoadState<GovernanceReviewTask[]>>({kind:'loading'});
  const [selected,setSelected]=useState<GovernanceReviewTask>();
  const [context,setContext]=useState<LoadState<GovernanceReviewContext>>();
  const [validationTaskId,setValidationTaskId]=useState<string>();
  const [validationContext,setValidationContext]=useState<LoadState<GovernanceReviewContext>>();
  const [positions,setPositions]=useState<StandardPosition[]>([]);
  const [targetPositionId,setTargetPositionId]=useState<string>();
  const [careerLevel,setCareerLevel]=useState<string>();
  const [leadershipScope,setLeadershipScope]=useState<string>();
  const [technologyFocusCodes,setTechnologyFocusCodes]=useState<string[]>([]);
  const [industryContextCodes,setIndustryContextCodes]=useState<string[]>([]);
  const [statusFilter,setStatusFilter]=useState<'all'|'pending'|'claimed'|'approved'>('all');
  const [comment,setComment]=useState('');
  const [actionError,setActionError]=useState<ApiError>();
  const [submitting,setSubmitting]=useState(false);
  const [operationLabel,setOperationLabel]=useState<string>();
  const [selectedTaskIds,setSelectedTaskIds]=useState<string[]>([]);
  const [batchSubmitting,setBatchSubmitting]=useState(false);
  const [summary,setSummary]=useState<{pending:number;claimed:number;approved:number}>({pending:0,claimed:0,approved:0});
  const [page,setPage]=useState(1);
  const [pageSize,setPageSize]=useState(20);
  const [total,setTotal]=useState(0);
  const load=useCallback(async()=>{
    setState({kind:'loading'});
    try{
      const [counts,items]=await Promise.all([
        getGovernanceReviewSummary(),
        listGovernanceReviewTasks({page,pageSize,status:statusFilter==='all'?undefined:statusFilter}),
      ]);
      setSummary(counts);
      setTotal(statusFilter==='all'?counts.pending+counts.claimed+counts.approved:counts[statusFilter]||0);
      setState({kind:'success',data:items});
    }catch(error){const reason=error as ApiError;setState({kind:'error',message:reason.message,status:reason.status})}
  },[page,pageSize,statusFilter]);
  useEffect(()=>{const id=requestAnimationFrame(()=>{void load();listCatalogAdminStandardPositions().then(setPositions).catch(reason=>message.error((reason as ApiError).message))});return()=>cancelAnimationFrame(id)},[load]);
  const runBatch=async(action:'claim'|'approve')=>{
    if(batchSubmitting)return;
    const targetIds=selectedTaskIds.filter(id=>{
      const task=state.kind==='success'?state.data.find(item=>item.task_id===id):undefined;
      return task&&(action==='claim'?task.status==='pending':['pending','claimed'].includes(task.status));
    });
    if(!targetIds.length)return;
    setBatchSubmitting(true);
    try{
      await batchReviewTasks(targetIds,action,action==='claim'?'批量领取审核任务':'批量确认通过');
      message.success(action==='claim'?`已批量领取 ${targetIds.length} 条任务`:`已批量通过 ${targetIds.length} 条任务`);
      setSelectedTaskIds([]);
      await load();
    }catch(reason){message.error((reason as ApiError).message)}
    finally{setBatchSubmitting(false)}
  };
  const publishAfterValidation=async(parseResultId:string)=>{
    for(let attempt=0;attempt<12;attempt+=1){
      try{return await publishReviewedJD(parseResultId)}catch(reason){
        const error=reason as ApiError;
        const running=error.status===409&&(error.message.includes('数据质量检查仍在运行')||error.message.includes('尚未完成数据质量检查'));
        if(!running||attempt===11)throw error;
        await new Promise(resolve=>window.setTimeout(resolve,800));
      }
    }
  };
  const fetchContext=async(task:GovernanceReviewTask)=>{setContext({kind:'loading'});try{const data=await getGovernanceReviewContext(task.task_id);setContext({kind:'success',data});setTargetPositionId(data.position?.position_id);setCareerLevel(data.position?.career_level||undefined);setLeadershipScope(data.position?.leadership_scope||undefined);setTechnologyFocusCodes(data.position?.technology_focus_codes||[]);setIndustryContextCodes(data.position?.industry_context_codes||[])}catch(reason){const error=reason as ApiError;setContext({kind:'error',message:error.message,status:error.status})}};
  const open=async(task:GovernanceReviewTask)=>{setSelected(task);setComment('');setActionError(undefined);setValidationTaskId(undefined);setValidationContext(undefined);await fetchContext(task)};
  const act=async(task:GovernanceReviewTask,action:GovernanceReviewAction,reason?:string)=>{if(submitting)return;setSubmitting(true);setActionError(undefined);try{await actOnGovernanceReview(task.task_id,action,reason);message.success(action==='claim'?'任务已领取，请开始逐项核对':action==='reject'?'已退回重新处理':'任务已释放');if(action==='claim'){await load();await open({...task,status:'claimed'})}else{setSelected(undefined);setContext(undefined);await load()}}catch(value){setActionError(value as ApiError)}finally{setSubmitting(false)}};
  const bindPosition=async(data:GovernanceReviewContext)=>{if(!data.parse_result_id||!targetPositionId||submitting)return;setSubmitting(true);setOperationLabel('正在保存岗位身份与独立分类维度…');setActionError(undefined);try{await mapReviewPosition(data.parse_result_id,{target_position_id:targetPositionId,career_level:careerLevel,leadership_scope:leadershipScope,technology_focus_codes:technologyFocusCodes,industry_context_codes:industryContextCodes});if(selected)await fetchContext(selected);message.success('岗位身份与分类维度已保存到当前 JD 版本')}catch(value){setActionError(value as ApiError)}finally{setSubmitting(false);setOperationLabel(undefined)}};
  const handleDeprecate=async(data:GovernanceReviewContext)=>{if(!data.jd_id||submitting)return;setSubmitting(true);setOperationLabel('正在弃用该 JD 的全部信息…');setActionError(undefined);try{await deprecateJD(data.jd_id);message.success('JD 已弃用，相关审核任务已关闭');setSelected(undefined);setContext(undefined);await load()}catch(value){setActionError(value as ApiError)}finally{setSubmitting(false);setOperationLabel(undefined)}};
  const openValidationReview=async(taskId:string)=>{setValidationTaskId(taskId);setValidationContext({kind:'loading'});try{const data=await getGovernanceReviewContext(taskId);setValidationContext({kind:'success',data})}catch(reason){const error=reason as ApiError;setValidationContext({kind:'error',message:error.message,status:error.status})}};
  const actValidationTask=async(taskId:string,action:GovernanceReviewAction)=>{if(submitting)return;setSubmitting(true);setActionError(undefined);try{await actOnGovernanceReview(taskId,action,action==='approve'?'已确认质量提醒属于可接受的重复数据，可以继续发布':'质量问题不可接受，需要修正来源数据');message.success(action==='approve'?'数据质量提醒已确认':'数据质量问题已退回');setValidationTaskId(undefined);setValidationContext(undefined);if(selected)await fetchContext(selected)}catch(value){setActionError(value as ApiError)}finally{setSubmitting(false)}};
  const publish=async(data:GovernanceReviewContext)=>{if(!data.parse_result_id||submitting)return;setSubmitting(true);setOperationLabel('正在核对最新 Validation 版本并发布…');setActionError(undefined);try{await publishAfterValidation(data.parse_result_id);message.success('JD 已发布，其他未归一化技能仍保留但不进入下游');setSelected(undefined);setContext(undefined);await load()}catch(value){setActionError(value as ApiError);if(selected)await fetchContext(selected)}finally{setSubmitting(false);setOperationLabel(undefined)}};
  const approve=async(task:GovernanceReviewTask,data:GovernanceReviewContext)=>{if(!data.parse_result_id||submitting)return;setSubmitting(true);setOperationLabel('正在确认结构化内容并生成新的 Validation 版本…');setActionError(undefined);try{await actOnGovernanceReview(task.task_id,'approve',comment||'岗位归类与结构化内容已核对');if(canPublish){setOperationLabel('审核已保存，正在等待最新 Validation 完成并发布…');await publishAfterValidation(data.parse_result_id);message.success('审核通过，JD 已发布')}else{message.success('审核已通过，等待管理员发布')}setSelected(undefined);setContext(undefined);await load()}catch(value){setActionError(value as ApiError);await load();if(selected)await fetchContext(selected)}finally{setSubmitting(false);setOperationLabel(undefined)}};
  return <>
  <WorkbenchState title="待审核的 JD 与数据质量问题" state={state} retry={load} render={items=>{return <Space direction="vertical" size={16} style={{width:'100%'}}>
    <Space size={24}><Statistic title="待审核" value={summary.pending}/><Statistic title="审核中" value={summary.claimed}/><Statistic title="已审核" value={summary.approved}/></Space>
    <Space wrap>
      <Button type="primary" loading={batchSubmitting} disabled={!selectedTaskIds.some(id=>items.some(item=>item.task_id===id&&item.status==='pending'))} onClick={()=>void runBatch('claim')}>批量领取（{items.filter(item=>selectedTaskIds.includes(item.task_id)&&item.status==='pending').length}）</Button>
      <Button type="primary" loading={batchSubmitting} disabled={!selectedTaskIds.some(id=>items.some(item=>item.task_id===id&&['pending','claimed'].includes(item.status)))} onClick={()=>void runBatch('approve')}>批量通过（{items.filter(item=>selectedTaskIds.includes(item.task_id)&&['pending','claimed'].includes(item.status)).length}）</Button>
      <Typography.Text type="secondary">勾选后可批量领取或批量通过待审核 JD。</Typography.Text>
    </Space>
    <Table rowKey="task_id" dataSource={items} rowSelection={{
      selectedRowKeys:selectedTaskIds,
      onChange:keys=>setSelectedTaskIds(keys as string[]),
      getCheckboxProps:(item:GovernanceReviewTask)=>({disabled:['approved','rejected','modified'].includes(item.status)}),
    }} title={()=>(
      <Space wrap><Typography.Text strong>状态</Typography.Text><Select value={statusFilter} onChange={value=>{setStatusFilter(value);setPage(1)}} style={{minWidth:150}} options={[{value:'all',label:'全部'},{value:'pending',label:'待审核'},{value:'claimed',label:'审核中'},{value:'approved',label:'已审核'}]}/></Space>
    )} pagination={{current:page,pageSize,total,showSizeChanger:{getPopupContainer:()=>document.body},onChange:(nextPage:number,nextSize:number)=>{setPageSize(nextSize);setPage(nextSize===pageSize?nextPage:1)}}} locale={{emptyText:'当前没有待处理的 JD / Validation 审核任务'}} columns={[
      {title:'岗位名称',render:(_:unknown,item:GovernanceReviewTask)=><Typography.Text strong>{item.object_name||'未命名 JD'}</Typography.Text>},
      {title:'待处理',render:(_:unknown,item:GovernanceReviewTask)=><Tag>{item.review_stage||(item.object_type==='data_validation_report'?'数据质量':'内容核对')}</Tag>},
      {title:'状态',render:(_:unknown,item:GovernanceReviewTask)=><StatusPill tone={item.status==='approved'?'passed':item.status==='claimed'?'warning':'neutral'}>{item.status==='pending'?'待审核':item.status==='claimed'?'审核中':'已审核'}</StatusPill>},
      {title:'审核人',render:(_:unknown,item:GovernanceReviewTask)=>item.status==='pending'?'/':(item.reviewer_name||item.reviewer_id||'/')},
      {title:'操作',render:(_:unknown,item:GovernanceReviewTask)=>item.status==='pending'?<Button type="primary" loading={submitting} onClick={()=>void act(item,'claim')}>开始审核</Button>:item.status==='claimed'?<Space><Button type="primary" onClick={()=>void open(item)}>继续审核</Button><Button onClick={()=>void act(item,'release')}>放弃修改</Button></Space>:<StatusPill tone="passed">已通过</StatusPill>},
    ]}/>
  </Space>}}/>
  <Modal title={selected?.object_type==='data_validation_report'?'确认数据质量问题':context?.kind==='success'&&context.data.title?`审核 JD：${context.data.title}`:'审核 JD 结构化结果'} open={Boolean(selected)} width={1120} footer={null} onCancel={()=>{if(!submitting){setSelected(undefined);setContext(undefined)}}}>
    {selected&&context?(context.kind==='loading'?<div className="state-panel loading-state"><Spin/></div>:context.kind==='error'?<Failure {...context} retry={()=>void open(selected)}/>:context.data.kind==='jd_parse_result'?(()=>{const data=context.data;const savedPositionId=data.position?.position_id;const dimensionsDirty=careerLevel!==(data.position?.career_level||undefined)||leadershipScope!==(data.position?.leadership_scope||undefined)||JSON.stringify(technologyFocusCodes)!==JSON.stringify(data.position?.technology_focus_codes||[])||JSON.stringify(industryContextCodes)!==JSON.stringify(data.position?.industry_context_codes||[]);const positionDirty=Boolean(targetPositionId&&targetPositionId!==savedPositionId)||dimensionsDirty;const activePositions=positions.filter(item=>item.lifecycle_status!=='deprecated');const candidateRows=(data.position?.candidate_positions||[]).map(candidate=>({...candidate,position:positions.find(item=>item.position_code===candidate.position_code)}));return <Space direction="vertical" size={18} style={{width:'100%'}}>
      {operationLabel&&<Typography.Paragraph type="secondary">{operationLabel}</Typography.Paragraph>}
      {actionError&&<Typography.Paragraph type="danger">当前操作没有完成，数据尚未保存：{actionError.message}{actionError.traceId?`（请求编号：${actionError.traceId}）`:''}</Typography.Paragraph>}
      <Collapse ghost items={[{key:'raw',label:'JD 原文',children:<Typography.Paragraph style={{whiteSpace:'pre-wrap',maxHeight:300,overflow:'auto',margin:0}}>{data.raw_text}</Typography.Paragraph>}]}/>
      {(data.pending_validation_reviews||[]).length>0&&<div><Typography.Title level={4}>数据质量审核未完成</Typography.Title><Typography.Paragraph type="secondary">这条 JD 还有数据质量提醒需要人工确认，通过后才能发布。</Typography.Paragraph><Table size="small" pagination={false} rowKey="task_id" dataSource={data.pending_validation_reviews||[]} columns={[{title:'结论',width:110,render:(_:unknown,item:PendingValidationReview)=><StatusPill tone={item.conclusion==='warn'?'warning':'neutral'}>{item.conclusion==='warn'?'需要确认':'待确认'}</StatusPill>},{title:'原因',dataIndex:'reason',render:(value:string|null)=><Typography.Text>{value||'数据质量检查发现需要确认的问题'}</Typography.Text>},{title:'状态',width:100,render:(_:unknown,item:PendingValidationReview)=><StatusPill tone="neutral">{item.status==='claimed'?'处理中':'待处理'}</StatusPill>},{title:'操作',width:140,render:(_:unknown,item:PendingValidationReview)=><Button type="primary" size="small" onClick={()=>void openValidationReview(item.task_id)}>处理数据质量</Button>}]}/></div>}
      {validationTaskId&&(validationContext?.kind==='loading'?<div className="state-panel loading-state"><Spin/></div>:validationContext?.kind==='error'?<Failure {...validationContext} retry={()=>void openValidationReview(validationTaskId)}/>:validationContext?.kind==='success'&&<ValidationReview data={validationContext.data} onApprove={()=>void actValidationTask(validationTaskId,'approve')} onReject={()=>void actValidationTask(validationTaskId,'reject')}/>)}
      <div>
        <Typography.Title level={4}>岗位多维分类</Typography.Title>
        {candidateRows.length>0&&<Table size="small" pagination={false} rowKey="position_code" dataSource={candidateRows} columns={[{title:'候选岗位',render:(_:unknown,item)=>item.position?.position_name||'未命名候选岗位'},{title:'置信分',dataIndex:'score',render:(value:number)=>value.toFixed(2)},{title:'区分规则',render:(_:unknown,item)=>{const rules=item.position?.confusable_with||[];return rules.length?rules.map(rule=><div key={rule.position_code}>{positions.find(position=>position.position_code===rule.position_code)?.position_name||'其他相近岗位'}：{rule.distinguish_by}</div>):'无目录混淆规则'}}]}/>}
        <div className="review-classification-fields">
          <Form.Item
            label="标准岗位"
            required
            validateStatus={!targetPositionId?'error':positionDirty?'warning':'success'}
            help={!targetPositionId?'必选：请选择正确的标准岗位。':positionDirty?'当前选择尚未保存，请保存分类结果。':'已保存到当前 JD 版本。'}
          >
            <Select showSearch popupMatchSelectWidth={420} status={!targetPositionId?'error':undefined} aria-required="true" placeholder="必选：搜索并选择岗位" value={targetPositionId} onChange={setTargetPositionId} filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())} options={activePositions.map(item=>({value:item.position_id,label:item.position_name}))}/>
          </Form.Item>
          <Form.Item label="岗位级别（选填）"><Select allowClear placeholder="请选择岗位级别" value={careerLevel} onChange={setCareerLevel} options={careerLevelOptions}/></Form.Item>
          <Form.Item label="管理职责（选填）"><Select allowClear placeholder="请选择管理职责" value={leadershipScope} onChange={setLeadershipScope} options={leadershipScopeOptions}/></Form.Item>
          <Form.Item label="技术方向（选填）"><Select mode="multiple" allowClear placeholder="可选择多个技术方向" value={technologyFocusCodes} onChange={setTechnologyFocusCodes} options={technologyFocusOptions}/></Form.Item>
          <Form.Item label="行业场景（选填）"><Select mode="multiple" allowClear placeholder="可选择多个行业场景" value={industryContextCodes} onChange={setIndustryContextCodes} options={industryContextOptions}/></Form.Item>
        </div>
        <Button className="review-classification-save" type="primary" loading={submitting} disabled={!targetPositionId||!positionDirty} onClick={()=>void bindPosition(data)}>{positionDirty?'保存分类结果':'分类结果已保存'}</Button>
      </div>
      <div><Typography.Title level={4}>内容核对</Typography.Title><Typography.Paragraph type="secondary">判断下列内容是否准确摘自原文。准确就保留；有遗漏、错分或改写时，在审核意见中指出并选择“退回重新解析”。</Typography.Paragraph><Table size="small" pagination={false} rowKey={(item,index)=>item.requirement_id||String(index)} dataSource={[...(data.responsibilities||[]),...(data.requirements||[])]} columns={[{title:'字段类型',width:120,render:(_:unknown,item)=>requirementKindLabels[item.kind||'task']||item.kind||'其他要求'},{title:'系统提取内容',render:(_:unknown,item)=>extractionText(item)},{title:'对应原文证据',render:(_:unknown,item)=><Typography.Text>“{item.evidence?.quote||'未提供证据'}”</Typography.Text>} ]}/>{(data.responsibilities||[]).length===0&&(data.requirements||[]).length===0&&<Alert type="warning" showIcon title="该 JD 暂无结构化抽取内容" description="这条 JD 没有可核对的职责或任职要求，请退回重新解析；如果确认该 JD 不应进入图谱，可以弃用。"/>}</div>
      <div><Typography.Text strong>审核意见</Typography.Text><Input.TextArea value={comment} onChange={event=>setComment(event.target.value)} placeholder="通过时可记录核对依据；退回时请明确写出哪一项与原文不符" style={{marginTop:8,marginBottom:12}}/><Space wrap><Button type="primary" loading={submitting} disabled={!data.can_approve||positionDirty} onClick={()=>void approve(selected,data)}>{canPublish?'确认无误并发布 JD':'确认无误，提交管理员发布'}</Button>{data.workflow_status==='reviewed'&&canPublish&&<Button loading={submitting} onClick={()=>void publish(data)}>重试发布 JD</Button>}<Button danger loading={submitting} onClick={()=>Modal.confirm({title:'退回这条 JD 重新解析？',content:'请先在审核意见中写清楚错误字段。退回后不会发布当前结果。',okText:'退回重新解析',cancelText:'继续检查',onOk:()=>act(selected,'reject',comment||'结构化结果与原文不一致，需要重新解析')})}>退回重新解析</Button><Button danger loading={submitting} onClick={()=>Modal.confirm({title:'弃用这条 JD？',content:'弃用后将关闭这条 JD 的审核任务，后续不再进入图谱与匹配流程；历史发布记录仍会保留。',okText:'确认弃用',okButtonProps:{danger:true},cancelText:'取消',onOk:()=>handleDeprecate(data)})}>弃用 JD</Button></Space></div>
    </Space>})():<ValidationReview data={context.data} onApprove={()=>void act(selected,'approve','已确认质量提醒属于可接受的重复数据，可以继续发布')} onReject={()=>void act(selected,'reject','质量问题不可接受，需要修正来源数据')}/>):null}
  </Modal>
  </>;
}

export function ReviewWorkbench(){
  const {can}=useAuth();
  return <div className="review-workbench">
    <div className="page-heading">
      <Typography.Title level={2}>审核中心</Typography.Title>
      <Typography.Paragraph type="secondary">集中处理需要人工判断的内容：JD 解析、数据质量、图谱结论与技能归一化。</Typography.Paragraph>
    </div>
    <Tabs className="review-workbench-tabs" destroyOnHidden={false} items={[
    {key:'pipeline',label:'JD 与数据质量审核',children:<div className="review-tab-content"><GovernanceReviewWorkbench/></div>},
    {key:'graph',label:'图谱结论审核',children:<div className="review-tab-content"><GraphReviewWorkbench/></div>},
    // 归一化审核同属人工审核，作为 Tab 归入审核中心；无归一化权限时不展示。
    ...(can('kg.normalization.manage')?[{key:'normalize',label:'归一化审核',children:<div className="review-tab-content"><UnresolvedWorkbench embedded/></div>}]:[]),
  ]}/>
  </div>;
}
