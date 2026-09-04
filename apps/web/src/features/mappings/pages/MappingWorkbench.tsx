import {useCallback,useEffect,useState} from 'react';
import {App,Button,Form,Input,Modal,Select,Space,Table,Typography} from 'antd';
import type {MappingCandidate,MappingEntityType,MappingItem} from '../../../shared/api';
import {ApiError} from '../../../shared/api';
import {ToastAlert as Alert,WorkbenchState,type LoadState} from '../../../shared/components/States';
import {StatusTag} from '../../../shared/components/StatusTag';
import {cancelMapping,confirmMapping,listMappings,retryMapping,searchMappingCandidates} from '../api';

const statusLabels:Record<string,string>={unmapped:'尚未关联',pending:'等待同步',confirmed:'已确认对应关系',synced:'已同步'};
const statusLabel=(status:string)=>status.startsWith('failed')?`同步失败（${status.split(':')[1]||'处理阶段'}）`:statusLabels[status]||'状态未知';

export function MappingWorkbench(){
  const {message,modal}=App.useApp();
  const [entityType,setEntityType]=useState<MappingEntityType>('position');
  const [query,setQuery]=useState('');
  const [status,setStatus]=useState('');
  const [state,setState]=useState<LoadState<MappingItem[]>>({kind:'loading'});
  const [current,setCurrent]=useState<MappingItem>();
  const [candidates,setCandidates]=useState<MappingCandidate[]>([]);
  const [candidateError,setCandidateError]=useState<ApiError>();
  const [busy,setBusy]=useState('');
  const load=useCallback(()=>{setState({kind:'loading'});listMappings(entityType,query,status).then(data=>setState({kind:'success',data})).catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}))},[entityType,query,status]);
  useEffect(()=>{listMappings(entityType,query,status).then(data=>setState({kind:'success',data})).catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}))},[entityType,query,status]);
  const searchCandidates=async(value:string)=>{setCandidateError(undefined);try{setCandidates(await searchMappingCandidates(entityType,value))}catch(reason){setCandidateError(reason as ApiError)}};
  const choose=async(values:{knowledge_graph_id:string})=>{if(!current||busy)return;setBusy(current.main_system_id);try{await confirmMapping(entityType,current.main_system_id,values.knowledge_graph_id);message.success('对应关系已确认');setCurrent(undefined);await load()}catch(reason){setCandidateError(reason as ApiError)}finally{setBusy('')}};
  const cancel=async(item:MappingItem)=>{if(busy)return;setBusy(item.main_system_id);try{await cancelMapping(entityType,item.main_system_id);message.success('对应关系已解除');await load()}catch(reason){const error=reason as ApiError;modal.error({title:'解除对应关系失败',content:error.message})}finally{setBusy('')}};
  const retry=async(item:MappingItem)=>{if(busy)return;setBusy(item.main_system_id);try{await retryMapping(entityType,item.main_system_id);message.success('重试成功');await load()}catch(reason){const error=reason as ApiError;modal.error({title:'重试失败',content:error.message})}finally{setBusy('')}};
  return <>
    <div className="page-heading">
      <Typography.Title level={2}>岗位与技能对应关系</Typography.Title>
      <Typography.Paragraph type="secondary">确认两个模块中的岗位或技能是否代表同一项。确认后，图谱构建、查询与数据同步会统一使用这条业务数据。</Typography.Paragraph>
    </div>
    <Space wrap style={{marginBottom:16}}>
      <Select value={entityType} onChange={value=>{setEntityType(value);setQuery('');setStatus('')}} options={[{value:'position',label:'岗位名称对应'},{value:'skill',label:'技能名称对应'}]}/>
      <Input.Search allowClear placeholder={`搜索${entityType==='position'?'岗位':'技能'}名称`} value={query} onChange={event=>setQuery(event.target.value)} onSearch={load}/>
      <Select allowClear placeholder="处理状态" value={status||undefined} onChange={value=>setStatus(value||'')} options={[{value:'unmapped',label:'尚未关联'},{value:'confirmed',label:'已确认对应关系'},{value:'synced',label:'已同步'},{value:'pending',label:'等待同步'}]}/>
    </Space>
    <WorkbenchState title="待确认列表" state={state} retry={load} render={items=><Table rowKey="main_system_id" dataSource={items} columns={[
      {title:entityType==='position'?'业务系统岗位':'业务系统技能',render:(_:unknown,item:MappingItem)=><Space direction="vertical" size={0}><Typography.Text strong>{item.source_name}</Typography.Text>{item.source_taxonomy_name&&<Typography.Text type="secondary">所属分类：{item.source_taxonomy_name}</Typography.Text>}</Space>},
      {title:'能力图谱中的对应项',render:(_:unknown,item:MappingItem)=><Typography.Text>{item.knowledge_graph_id?'已选择对应内容':'尚未选择'}</Typography.Text>},
      {title:'处理状态',render:(_:unknown,item:MappingItem)=><StatusTag tone={item.sync_status.startsWith('failed')?'risk':item.knowledge_graph_id?'stable':'neutral'}>{statusLabel(item.sync_status)}</StatusTag>},
      {title:'需要注意',render:(_:unknown,item:MappingItem)=>item.last_error_message||'—'},
      {title:'操作',render:(_:unknown,item:MappingItem)=><Space><Button disabled={Boolean(busy)} onClick={()=>{setCurrent(item);setCandidates([]);setCandidateError(undefined);void searchCandidates(item.source_name)}}>{item.knowledge_graph_id?'重新选择':'选择对应项'}</Button>{item.knowledge_graph_id&&<Button disabled={Boolean(busy)} danger onClick={()=>void cancel(item)}>解除关联</Button>}{item.sync_status.startsWith('failed')&&<Button loading={busy===item.main_system_id} onClick={()=>void retry(item)}>重试同步</Button>}</Space>},
    ]}/>}/>
    <Modal title={`为“${current?.source_name||''}”选择对应的图谱${entityType==='position'?'岗位':'技能'}`} open={Boolean(current)} footer={null} onCancel={()=>{if(!busy)setCurrent(undefined)}}>
      {candidateError&&<Alert
        type="error"
        showIcon
        title={candidateError.message}
        action={<Button onClick={()=>void searchCandidates(current?.source_name||'')}>重试</Button>}
      />}
      <Typography.Paragraph type="secondary">请选择与业务名称含义相同的一项。页面只展示业务名称，内部编号由系统自动保存。</Typography.Paragraph>
      <Form onFinish={choose} layout="vertical"><Form.Item label="重新搜索"><Input.Search placeholder={`输入${entityType==='position'?'岗位':'技能'}名称`} onSearch={value=>void searchCandidates(value)}/></Form.Item><Form.Item name="knowledge_graph_id" label="图谱中的对应名称" rules={[{required:true,message:'请选择对应内容'}]}><Select showSearch optionFilterProp="label" placeholder="请选择含义相同的内容" options={candidates.map(item=>({value:item.knowledge_graph_id,label:item.name,disabled:item.status!=='active'}))}/></Form.Item><Button type="primary" htmlType="submit" loading={Boolean(busy)}>确认对应关系</Button></Form>
    </Modal>
  </>;
}
