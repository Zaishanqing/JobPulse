import {useCallback,useEffect,useMemo,useState} from 'react';
import {App,Button,Card,Descriptions,Drawer,Form,Input,InputNumber,Modal,Select,Space,Spin,Table,Typography} from 'antd';
import {RedoOutlined,ReloadOutlined} from '@ant-design/icons';
import {useNavigate} from 'react-router-dom';
import {ApiError,localizeSystemMessage} from '../../../shared/api';
import {EmptyState,Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {StatusTag,type StatusTone} from '../../../shared/components/StatusTag';
import {useAuth} from '../../auth/AuthContext';
import {createAcquisitionJob,getAcquisitionJob,listAcquisitionJobs,listAcquisitionSources,retryAcquisitionJob,saveBossCookies,saveLiepinCookies} from '../api';
import type {AcquisitionJob,AcquisitionSourceStatus} from '../types';

const statusLabel:Record<string,string>={
  pending:'等待中',crawling:'采集中',exporting:'导出中',verifying:'校验中',importing:'导入中',
  completed:'已完成',crawl_failed:'采集失败',export_failed:'导出失败',verify_failed:'校验失败',import_failed:'导入失败',cancelled:'已取消',
};
const sourceLabel:Record<string,string>={boss:'Boss 直聘',liepin:'猎聘',feishu:'飞书招聘'};
const stageTone=(status:string):StatusTone=>{
  if(status==='completed')return 'stable';
  if(status==='cancelled')return 'neutral';
  if(status.endsWith('_failed'))return 'risk';
  return 'review';
};
function statusTag(status:string){return <StatusTag tone={stageTone(status)}>{statusLabel[status]||'状态未知'}</StatusTag>}

export function AcquisitionWorkbench(){
  const {message}=App.useApp();
  const {can}=useAuth();
  const navigate=useNavigate();
  const canManage=can('acquisition.job.manage');
  const [sources,setSources]=useState<AcquisitionSourceStatus[]>([]);
  const [jobs,setJobs]=useState<AcquisitionJob[]>([]);
  const [total,setTotal]=useState(0);
  const [page,setPage]=useState(1);
  const [pageSize,setPageSize]=useState(20);
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [detail,setDetail]=useState<AcquisitionJob|null>(null);
  const [sourceFilter,setSourceFilter]=useState<string|undefined>();
  const [statusFilter,setStatusFilter]=useState<string|undefined>();
  const [loadingSources,setLoadingSources]=useState(true);
  const [loadingJobs,setLoadingJobs]=useState(true);
  const [creating,setCreating]=useState(false);
  const [retrying,setRetrying]=useState(false);
  const [error,setError]=useState<ApiError>();
  const [cookieModal,setCookieModal]=useState<{open:boolean;source:string}>({open:false,source:""});
  const [cookieText,setCookieText]=useState("");
  const [cookieSaving,setCookieSaving]=useState(false);
  const [form]=Form.useForm<{source:string;keyword:string;city:string;pages:number}>();

  const openCookiePaste=(source:string)=>{setCookieModal({open:true,source});setCookieText("");};
  const saveCookies=async()=>{
    setCookieSaving(true);setError(undefined);
    try{
      const raw=JSON.parse(cookieText);
      const cookies=Array.isArray(raw)?raw:[raw];
      const result=cookieModal.source==='boss'
        ?await saveBossCookies(cookies)
        :await saveLiepinCookies(cookies);
      if(result.verified){message.success(`已保存并验证 ${result.count} 个 Cookie`)}
      else{message.error('Cookie 保存失败，请重试')}
      setCookieModal({open:false,source:""});
      setCookieText("");
      void loadSources();
    }catch{message.error('JSON 格式错误，请检查 Cookie 格式')}
    finally{setCookieSaving(false)}
  };

  const loadSources=useCallback(async()=>{
    setLoadingSources(true);
    try{setSources(await listAcquisitionSources())}
    catch(reason){setError(reason as ApiError)}
    finally{setLoadingSources(false)}
  },[]);

  const loadJobs=useCallback(async(preferredPage?:number)=>{
    setLoadingJobs(true);setError(undefined);
    try{
      const result=await listAcquisitionJobs({status:statusFilter,source:sourceFilter,page:preferredPage??page,page_size:pageSize});
      setJobs(result.items);setTotal(result.total);setPage(result.page);
      if(selectedId&&!result.items.some(item=>item.id===selectedId)&&result.page===1)setSelectedId(null);
    }catch(reason){setError(reason as ApiError)}
    finally{setLoadingJobs(false)}
  },[page,pageSize,selectedId,sourceFilter,statusFilter]);

  const loadDetail=useCallback(async(jobId:string)=>{
    try{
      const value=await getAcquisitionJob(jobId);
      setDetail(value);
      setJobs(current=>current.map(item=>item.id===jobId?value:item));
    }catch(reason){setError(reason as ApiError)}
  },[]);

  useEffect(()=>{const id=requestAnimationFrame(()=>void loadSources());return()=>cancelAnimationFrame(id)},[loadSources]);
  useEffect(()=>{const id=requestAnimationFrame(()=>void loadJobs());return()=>cancelAnimationFrame(id)},[loadJobs]);
  useEffect(()=>{
    if(!selectedId)return;
    const loadDetailId=requestAnimationFrame(()=>void loadDetail(selectedId));
    const timer=window.setInterval(()=>void loadDetail(selectedId),3000);
    return()=>{window.clearInterval(timer);cancelAnimationFrame(loadDetailId)};
  },[loadDetail,selectedId]);

  const submit=async()=>{
    const values=await form.validateFields();
    setCreating(true);setError(undefined);
    try{
      const source=values.source;
      const payload=source==='feishu'
        ?{source,keyword:'all',city:'全国',pages:1}
        :{source,keyword:values.keyword,city:values.city,pages:values.pages||5};
      const created=await createAcquisitionJob(payload);
      message.success('采集任务已创建');
      form.resetFields(['keyword','city']);
      setSelectedId(created.id);
      await loadJobs(1);
      await loadDetail(created.id);
    }catch(reason){setError(reason as ApiError)}
    finally{setCreating(false)}
  };

  const retry=async(job:AcquisitionJob)=>{
    setRetrying(true);setError(undefined);
    try{
      const created=await retryAcquisitionJob(job.id);
      message.success('已创建重试任务');
      setSelectedId(created.id);
      await loadJobs(1);
      await loadDetail(created.id);
    }catch(reason){setError(reason as ApiError)}
    finally{setRetrying(false)}
  };

  const sourceOptions=useMemo(()=>sources.map(item=>({value:item.source,label:`${sourceLabel[item.source]||item.source}${item.ready?'':'（未就绪）'}`})),[sources]);
  const columns=[
    {title:'任务',dataIndex:'id',width:110,render:(value:string)=> <Typography.Text copyable={{text:value}}>{value.slice(0,8)}</Typography.Text>},
    {title:'状态',dataIndex:'status',width:110,render:(value:string)=>statusTag(value)},
    {title:'来源',dataIndex:'source',width:100,render:(value:string)=><>{sourceLabel[value]||value}</>},
    {title:'关键词',dataIndex:'keyword',ellipsis:true},
    {title:'城市',dataIndex:'city',width:90},
    {title:'发现',dataIndex:'discovered_count',width:80},
    {title:'导入',dataIndex:'imported_count',width:80},
    {title:'重复跳过',dataIndex:'no_op_count',width:100},
    {title:'失败',dataIndex:'failed_count',width:70},
    {title:'创建时间',dataIndex:'created_at',width:160,render:(value:string|null)=>value?new Date(value).toLocaleString('zh-CN'):'-'},
  ];

  if((loadingSources||loadingJobs)&&!sources.length&&!jobs.length&&!error)return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载采集数据</span></div>;

  return <>
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>数据采集</Typography.Title>
        <Typography.Paragraph type="secondary">统一管理采集任务（BOSS 直聘、猎聘、飞书招聘），采集结果经校验后导入 JD 数据中心。</Typography.Paragraph>
      </div>
      <Space>
        <Button onClick={()=>navigate('/data/jds')}>前往 JD 数据中心</Button>
        <Button icon={<ReloadOutlined/>} loading={loadingJobs} onClick={()=>void loadJobs()}>刷新</Button>
      </Space>
    </div>
    {error&&<Failure message={error.message} status={error.status} retry={()=>{setError(undefined);void loadSources();void loadJobs()}}/>}
    <Card title="采集源状态" loading={loadingSources} className="acquisition-section-card">
      <div className="acquisition-source-grid">
        {sources.length===0
          ?<Alert type="warning" showIcon message="暂无可用采集源" description="采集服务不可用，或尚未启用数据采集功能。"/>
          :sources.map(source=>(
            <Card key={source.source} size="small" className="acquisition-source-card">
              <div className="acquisition-source-card-content">
                <div className="acquisition-source-card-main">
                  <Typography.Text strong>{sourceLabel[source.source]||source.source}</Typography.Text>
                  {source.available
                    ?<StatusTag tone={source.ready?'stable':source.login_required?'risk':'review'}>{source.ready?'可用':'未就绪'}</StatusTag>
                    :<StatusTag tone="risk">不可用</StatusTag>}
                  {source.login_required&&<Typography.Text type="warning">需要在采集服务端完成登录</Typography.Text>}
                </div>
                <div className="acquisition-source-card-action">
                  {['boss','liepin'].includes(source.source)&&canManage&&(
                    <Button size="small" disabled={cookieSaving} onClick={()=>openCookiePaste(source.source)}>
                      粘贴 Cookie
                    </Button>
                  )}
                </div>
              </div>
            </Card>
          ))}
      </div>
    </Card>
    <Card title="创建采集任务" className="acquisition-section-card acquisition-create-card">
      {!canManage
        ?<Alert type="warning" showIcon message="权限不足" description="当前账户没有创建采集任务或重试的权限。"/>
        :<Form form={form} layout="inline" initialValues={{source:'boss',pages:5}} onFinish={()=>void submit()}>
          <Form.Item name="source" rules={[{required:true}]}><Select style={{width:150}} options={sourceOptions}/></Form.Item>
          <Form.Item noStyle shouldUpdate={(prev,next)=>prev.source!==next.source}>
            {({getFieldValue})=>getFieldValue('source')==='feishu'
              ?null
              :<Space size={8} wrap>
                <Form.Item name="keyword" rules={[{required:true}]}><Input placeholder="关键词，如 Java" style={{width:180}}/></Form.Item>
                <Form.Item name="city" rules={[{required:true}]}><Input placeholder="城市，如 北京" style={{width:140}}/></Form.Item>
                <Form.Item name="pages" rules={[{required:true}]}><InputNumber min={1} max={100} style={{width:100}}/></Form.Item>
              </Space>}
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={creating}>创建任务</Button>
        </Form>}
    </Card>
    <Card title={`采集任务（${total}）`} className="acquisition-section-card">
      <Space className="acquisition-filter" wrap>
        <Select allowClear placeholder="来源" style={{width:140}} value={sourceFilter} onChange={value=>{setSourceFilter(value||undefined);setPage(1)}} options={sources.map(item=>({value:item.source,label:sourceLabel[item.source]||item.source}))}/>
        <Select allowClear placeholder="状态" style={{width:160}} value={statusFilter} onChange={value=>{setStatusFilter(value||undefined);setPage(1)}} options={Object.entries(statusLabel).map(([value,label])=>({value,label}))}/>
      </Space>
      <Table
        className="primary-table"
        rowKey="id"
        size="middle"
        loading={loadingJobs}
        dataSource={jobs}
        columns={columns}
        pagination={{current:page,pageSize,total,showSizeChanger:true,onChange:(next,size)=>{setPage(next);setPageSize(size)}}}
        locale={{emptyText:<EmptyState text="暂无采集任务"/>}}
        onRow={record=>({onClick:()=>{setSelectedId(record.id);setDetail(record)},className:selectedId===record.id?'is-selected':''})}
      />
    </Card>
    <Drawer
      title={detail?`采集任务 ${detail.id}`:'采集任务详情'}
      width={640}
      open={Boolean(detail)}
      onClose={()=>{setSelectedId(null);setDetail(null)}}
    >
      {detail&&<AcquisitionJobDetail job={detail} canManage={canManage} retrying={retrying} onRetry={()=>void retry(detail)} onRefresh={()=>void loadDetail(detail.id)}/>}
    </Drawer>
    <Modal
      open={cookieModal.open}
      onCancel={()=>{setCookieModal({open:false,source:""});setCookieText("")}}
      title={`粘贴 ${sourceLabel[cookieModal.source]||cookieModal.source} Cookie`}
      onOk={saveCookies}
      confirmLoading={cookieSaving}
      okText="保存并验证"
    >
      <Typography.Paragraph type="secondary">
        在浏览器中打开猎聘/BOSS直聘并登录，然后用 EditThisCookie 或开发者工具导出 Cookie 为 JSON 格式，粘贴到下方：
      </Typography.Paragraph>
      <Input.TextArea
        rows={8}
        value={cookieText}
        onChange={e=>setCookieText(e.target.value)}
        placeholder={'[{"name":"wt2","value":"...","domain":".zhipin.com"}]'}
      />
    </Modal>
  </>;
}

type ProvenanceStage='acquisition'|'crawler'|'discovered'|'bundle'|'verify'|'import';
type ProvenanceState='completed'|'running'|'pending'|'failed';

function provenanceState(job:AcquisitionJob,stage:ProvenanceStage):ProvenanceState{
  const s=job.status;
  switch(stage){
    case 'acquisition':
      if(s==='completed')return 'completed';
      if(s==='cancelled')return 'pending';
      if(s.endsWith('_failed'))return 'failed';
      return 'running';
    case 'crawler':
    case 'discovered':
      if(['exporting','verifying','importing','completed'].includes(s))return 'completed';
      if(s==='crawling')return 'running';
      if(s==='crawl_failed')return 'failed';
      return 'pending';
    case 'bundle':
      if(['verifying','importing','completed'].includes(s))return 'completed';
      if(s==='exporting')return 'running';
      if(s==='export_failed')return 'failed';
      return 'pending';
    case 'verify':
      if(['importing','completed'].includes(s))return 'completed';
      if(s==='verifying')return 'running';
      if(s==='verify_failed')return 'failed';
      return 'pending';
    case 'import':
      if(s==='completed')return 'completed';
      if(s==='importing')return 'running';
      if(s==='import_failed')return 'failed';
      return 'pending';
  }
}

const provenanceStateLabel:Record<ProvenanceState,string>={completed:'完成',running:'进行中',pending:'等待',failed:'失败'};

function AcquisitionJobDetail({job,canManage,retrying,onRetry,onRefresh}:{job:AcquisitionJob;canManage:boolean;retrying:boolean;onRetry:()=>void;onRefresh:()=>void}){
  const retryable=['crawl_failed','export_failed','verify_failed','import_failed'].includes(job.status);
  return <>
    <Space style={{marginBottom:12}}>
      {statusTag(job.status)}
      <Button size="small" icon={<ReloadOutlined/>} onClick={onRefresh}>刷新</Button>
      {canManage&&retryable&&<Button size="small" type="primary" icon={<RedoOutlined/>} loading={retrying} onClick={onRetry}>重试</Button>}
    </Space>
    <Descriptions bordered size="small" column={1} items={[
      {key:'id',label:'主系统作业记录',children:<Typography.Text copyable={{text:job.id}}>作业记录</Typography.Text>},
      {key:'attempt',label:'尝试次数',children:job.attempt},
      {key:'retry_of',label:'重试自',children:job.retry_of_id||'-'},
      {key:'source',label:'采集源',children:sourceLabel[job.source]||job.source},
      {key:'keyword',label:'关键词',children:job.keyword},
      {key:'city',label:'城市',children:job.city},
      {key:'pages',label:'页数',children:job.pages},
      {key:'crawler_task',label:'采集任务',children:job.crawler_task_id?'已创建':'-'},
      {key:'bundle',label:'数据包',children:job.bundle_id?'已生成':'-'},
      {key:'import_batch',label:'导入批次',children:job.import_batch_id?'已创建':'-'},
    ]}/>
    <Typography.Title level={5} style={{marginTop:16}}>数据链路</Typography.Title>
    <Table
      size="small"
      rowKey="key"
      pagination={false}
      dataSource={[
        {key:'acquisition',label:'采集请求',value:'已创建',status:provenanceState(job,'acquisition')},
        {key:'crawler',label:'网页采集',value:job.crawler_task_id?'已创建任务':'尚未创建',status:provenanceState(job,'crawler')},
        {key:'discovered',label:'发现数据',value:`${job.discovered_count} 条`,status:provenanceState(job,'discovered')},
        {key:'bundle',label:'生成数据包',value:job.bundle_id?'已生成':'尚未生成',status:provenanceState(job,'bundle')},
        {key:'verify',label:'完整性校验',value:job.bundle_hash?'已通过':'等待校验',status:provenanceState(job,'verify')},
        {key:'import',label:'导入结果',value:`新增 ${job.imported_count} 条 · 重复跳过 ${job.no_op_count} 条 · 失败 ${job.failed_count} 条`,status:provenanceState(job,'import')},
      ]}
      columns={[
        {title:'环节',dataIndex:'label'},
        {title:'状态',dataIndex:'status',width:110,render:(value:ProvenanceState)=><StatusTag tone={value==='completed'?'stable':value==='failed'?'risk':value==='running'?'review':'neutral'}>{provenanceStateLabel[value]}</StatusTag>},
        {title:'引用',dataIndex:'value',ellipsis:true},
      ]}
    />
    <Descriptions className="acquisition-counts" title="导入统计" bordered size="small" column={2} items={[
      {key:'discovered',label:'网页采集发现',children:job.discovered_count},
      {key:'bundle_records',label:'数据包记录',children:job.exported_count},
      {key:'imported',label:'新增导入',children:job.imported_count},
      {key:'noop',label:'重复跳过',children:job.no_op_count},
      {key:'failed',label:'拒绝或失败',children:job.failed_count},
      {key:'progress',label:'进度',children:`${Math.round(job.progress*100)}%`},
    ]}/>
    {job.error_code&&<Alert className="acquisition-error" type="error" showIcon title="采集任务执行失败" description={job.error_message?localizeSystemMessage(job.error_message):'请重试该任务。'}/>}
    <Descriptions bordered size="small" column={1} title="时间" items={[
      {key:'created',label:'创建',children:job.created_at?new Date(job.created_at).toLocaleString('zh-CN'):'-'},
      {key:'started',label:'开始',children:job.started_at?new Date(job.started_at).toLocaleString('zh-CN'):'-'},
      {key:'updated',label:'更新',children:job.updated_at?new Date(job.updated_at).toLocaleString('zh-CN'):'-'},
      {key:'finished',label:'结束',children:job.finished_at?new Date(job.finished_at).toLocaleString('zh-CN'):'-'},
    ]}/>
  </>;
}
