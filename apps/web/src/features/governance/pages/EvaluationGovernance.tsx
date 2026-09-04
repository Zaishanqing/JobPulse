import {useCallback,useEffect,useMemo,useState} from 'react';
import {
  Alert,
  App,
  Button,
  Drawer,
  Empty,
  Form,
  Input,
  Popconfirm,
  Segmented,
  Select,
  Spin,
  Tag,
  Typography,
} from 'antd';
import {
  CheckOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import {ApiError} from '../../../shared/api';
import {Failure} from '../../../shared/components/States';
import {useAuth} from '../../auth/AuthContext';
import {
  createEvaluationDataset,
  deleteEvaluationDataset,
  listEvaluationDatasets,
  listFeedback,
  runEvaluation,
  updateFeedbackStatus,
} from '../api';
import type {Evaluation,EvaluationDataset,FeedbackRecord} from '../types';

type Surface='质量评估'|'反馈治理';
type DatasetValues={
  dataset_type:EvaluationDataset['dataset_type'];
  name:string;
  description?:string;
  payload_text:string;
};

const datasetTypeCopy={jd:'JD 解析',resume:'简历解析',match:'岗位匹配'};
const reportTypeCopy:Record<string,string>={
  jd_parse:'JD 解析准确率',
  resume_parse:'简历解析准确率',
  match:'匹配准确率',
  skill_normalization:'技能归一化准确率',
};
const feedbackTypeCopy:Record<string,string>={
  resume_parse:'简历解析',
  match_report:'匹配报告',
  learning_path:'学习路径',
  jd_parse:'JD 解析',
  skill_weight:'技能权重',
  candidate_match:'候选匹配',
  job_requirement_change:'岗位需求变化',
};
const feedbackStatusCopy:Record<string,string>={
  pending_review:'待处理',
  reviewing:'处理中',
  accepted:'已采纳',
  rejected:'已驳回',
};

const dateLabel=(value:string|null)=>value
  ?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value))
  :'时间未知';

const metricEntry=(evaluation:Evaluation)=>{
  const reserved=new Set(['evaluated_count','correct_count','error_count','skipped_count','evaluation_status','algorithm_version','implementation_status','cluster_count']);
  return Object.entries(evaluation.metrics).find(([key,value])=>!reserved.has(key)&&typeof value==='number');
};

const feedbackSummary=(record:FeedbackRecord)=>{
  const keys=['summary','description','reason','comment','message'];
  for(const key of keys){
    const value=record.payload[key];
    if(typeof value==='string'&&value.trim())return value.trim();
  }
  const first=Object.entries(record.payload).find(([,value])=>typeof value==='string'&&value.trim());
  return first?String(first[1]):'反馈中未提供文字说明';
};

export function EvaluationGovernance(){
  const {message}=App.useApp();
  const {can}=useAuth();
  const canEvaluate=can('trend.run.manage');
  const [surface,setSurface]=useState<Surface>(canEvaluate?'质量评估':'反馈治理');
  const [datasets,setDatasets]=useState<EvaluationDataset[]>([]);
  const [feedback,setFeedback]=useState<FeedbackRecord[]>([]);
  const [selectedDatasetId,setSelectedDatasetId]=useState<string>();
  const [evaluation,setEvaluation]=useState<Evaluation>();
  const [evaluationType,setEvaluationType]=useState('jd_parse');
  const [feedbackFilter,setFeedbackFilter]=useState('all');
  const [drawerOpen,setDrawerOpen]=useState(false);
  const [loading,setLoading]=useState(true);
  const [working,setWorking]=useState('');
  const [error,setError]=useState<ApiError>();
  const [datasetForm]=Form.useForm<DatasetValues>();

  const load=useCallback(async()=>{
    setLoading(true);setError(undefined);
    try{
      const [datasetResult,feedbackResult]=await Promise.all([
        canEvaluate?listEvaluationDatasets():Promise.resolve([]),
        listFeedback(),
      ]);
      setDatasets(datasetResult);
      setFeedback(feedbackResult);
      setSelectedDatasetId(current=>datasetResult.some(item=>item.dataset_id===current)?current:datasetResult[0]?.dataset_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[canEvaluate]);

  useEffect(()=>{const timer=window.setTimeout(()=>void load(),0);return()=>window.clearTimeout(timer)},[load]);

  const selectedDataset=datasets.find(item=>item.dataset_id===selectedDatasetId);
  const allowedReports=useMemo(()=>{
    if(!selectedDataset)return Object.keys(reportTypeCopy);
    if(selectedDataset.dataset_type==='jd')return ['jd_parse','skill_normalization'];
    if(selectedDataset.dataset_type==='resume')return ['resume_parse','skill_normalization'];
    return ['match'];
  },[selectedDataset]);

  useEffect(()=>{
    if(allowedReports.includes(evaluationType))return;
    const timer=window.setTimeout(()=>setEvaluationType(allowedReports[0]),0);
    return()=>window.clearTimeout(timer);
  },[allowedReports,evaluationType]);

  const filteredFeedback=useMemo(
    ()=>feedbackFilter==='all'?feedback:feedback.filter(item=>item.status===feedbackFilter),
    [feedback,feedbackFilter],
  );

  const createDataset=async()=>{
    const values=await datasetForm.validateFields();
    let payload:Record<string,unknown>;
    try{
      payload=JSON.parse(values.payload_text) as Record<string,unknown>;
      if(!payload||Array.isArray(payload)||typeof payload!=='object')throw new Error();
    }catch{
      datasetForm.setFields([{name:'payload_text',errors:['请输入合法的 JSON 对象']}]);
      return;
    }
    setWorking('dataset');setError(undefined);
    try{
      const created=await createEvaluationDataset(values.dataset_type,{name:values.name,description:values.description,payload});
      message.success('评估数据集已创建');
      setDrawerOpen(false);datasetForm.resetFields();await load();setSelectedDatasetId(created.dataset_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const removeDataset=async(datasetId:string)=>{
    setWorking(`delete:${datasetId}`);setError(undefined);
    try{await deleteEvaluationDataset(datasetId);message.success('数据集已删除');setEvaluation(undefined);await load()}
    catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const evaluate=async()=>{
    setWorking('evaluate');setError(undefined);
    try{
      const result=await runEvaluation(evaluationType,selectedDatasetId);
      setEvaluation(result);message.success('规则评估已完成');
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const updateStatus=async(item:FeedbackRecord,status:FeedbackRecord['status'])=>{
    setWorking(`${item.feedback_id}:${status}`);setError(undefined);
    try{
      const updated=await updateFeedbackStatus(item.feedback_id,status);
      setFeedback(values=>values.map(value=>value.feedback_id===updated.feedback_id?updated:value));
      message.success('反馈状态已更新');
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const metric=evaluation?metricEntry(evaluation):undefined;

  if(loading)return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>;

  return <div className="governance-page">
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>质量评估与反馈治理</Typography.Title>
        <Typography.Paragraph type="secondary">用可复现数据集验证抽取与匹配结果，并将用户反馈纳入人工治理。</Typography.Paragraph>
      </div>
      {canEvaluate&&surface==='质量评估'&&<Button type="primary" icon={<ExperimentOutlined/>} onClick={()=>setDrawerOpen(true)}>创建评估数据集</Button>}
    </div>

    {error&&<Failure message={error.message} status={error.status} retry={()=>void load()}/>}

    <div className="governance-switch">
      <Segmented<Surface>
        value={surface}
        options={canEvaluate?['质量评估','反馈治理']:['反馈治理']}
        onChange={setSurface}
      />
      <Typography.Text type="secondary">
        {surface==='质量评估'?'评估报告绑定数据集和算法版本':'审核操作保留反馈创建人和状态变化'}
      </Typography.Text>
    </div>

    {surface==='质量评估'?<div className="evaluation-layout">
      <aside className="evaluation-datasets">
        <div className="governance-panel-head"><Typography.Text strong>评估数据集</Typography.Text><Tag>{datasets.length}</Tag></div>
        {datasets.length?datasets.map(item=><button
          key={item.dataset_id}
          className={item.dataset_id===selectedDatasetId?'is-selected':''}
          onClick={()=>{setSelectedDatasetId(item.dataset_id);setEvaluation(undefined)}}
        >
          <span><strong>{item.name}</strong><small>{datasetTypeCopy[item.dataset_type]} · {dateLabel(item.updated_at||item.created_at)}</small></span>
          <Popconfirm title="删除这个评估数据集？" okText="删除" cancelText="取消" onConfirm={event=>{event?.stopPropagation();void removeDataset(item.dataset_id)}}>
            <Button type="text" danger size="small" icon={<DeleteOutlined/>} loading={working===`delete:${item.dataset_id}`} onClick={event=>event.stopPropagation()}/>
          </Popconfirm>
        </button>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无评估数据集"/>}
      </aside>

      <main className="evaluation-workspace">
        <section className="evaluation-runner">
          <div>
            <Typography.Title level={4}>运行确定性评估</Typography.Title>
            <Typography.Text type="secondary">每次运行记录数据集、规则版本、评估数量和错误样本。</Typography.Text>
          </div>
          <Select
            value={evaluationType}
            options={allowedReports.map(value=>({value,label:reportTypeCopy[value]}))}
            onChange={setEvaluationType}
          />
          <Button type="primary" icon={<PlayCircleOutlined/>} loading={working==='evaluate'} onClick={()=>void evaluate()}>
            {selectedDataset?'运行当前数据集':'运行空数据检查'}
          </Button>
        </section>

        <section className="evaluation-report">
          <div className="governance-panel-head">
            <div><Typography.Title level={4}>评估结果</Typography.Title><Typography.Text type="secondary">{evaluation?'当前评估已写入数据库':'运行评估后查看结果'}</Typography.Text></div>
          </div>
          {evaluation?<>
            <div className="evaluation-verdict">
              <div>
                <span>{metric?.[0]?reportTypeCopy[evaluation.report_type]||metric[0]:'评估状态'}</span>
                <strong>{typeof metric?.[1]==='number'?`${Math.round(metric[1]*100)}%`:evaluation.evaluation_status==='completed'?'已完成':'数据不足'}</strong>
              </div>
              <dl>
                <div><dt>评估样本</dt><dd>{evaluation.evaluated_count}</dd></div>
                <div><dt>错误样本</dt><dd>{evaluation.error_count}</dd></div>
                <div><dt>算法版本</dt><dd>{evaluation.algorithm_version}</dd></div>
                <div><dt>评估记录</dt><dd>已生成</dd></div>
              </dl>
            </div>
            <div className="evaluation-errors">
              <div className="governance-panel-head">
                <Typography.Text strong>错误与跳过样本</Typography.Text>
                {evaluation.error_cases.length>0&&<Tag color="warning">{evaluation.error_cases.length} 条</Tag>}
              </div>
              {evaluation.error_cases.length?evaluation.error_cases.slice(0,8).map((item,index)=><div key={String(item.case_id||index)}>
                <span><strong>{String(item.case_id||`样本 ${index+1}`)}</strong><small>{String(item.type||'unknown')}</small></span>
                <p>{String(item.description||'未提供错误说明')}</p>
              </div>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前报告没有错误样本"/>}
            </div>
          </>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未加载评估报告"/>}
        </section>
      </main>
    </div>:<div className="feedback-governance">
      <div className="feedback-toolbar">
        <div><Typography.Title level={4}>用户反馈队列</Typography.Title><Typography.Text type="secondary">按状态领取、处理并形成可追溯结论</Typography.Text></div>
        <Segmented
          value={feedbackFilter}
          options={[
            {value:'all',label:`全部 ${feedback.length}`},
            {value:'pending_review',label:`待处理 ${feedback.filter(item=>item.status==='pending_review').length}`},
            {value:'reviewing',label:`处理中 ${feedback.filter(item=>item.status==='reviewing').length}`},
            {value:'accepted',label:'已采纳'},
            {value:'rejected',label:'已驳回'},
          ]}
          onChange={setFeedbackFilter}
        />
      </div>
      {filteredFeedback.length?<div className="feedback-list">{filteredFeedback.map(item=><article key={item.feedback_id}>
        <div className="feedback-main">
          <div><Tag>{feedbackTypeCopy[item.feedback_type]||item.feedback_type}</Tag><Typography.Text type="secondary">{dateLabel(item.created_at)}</Typography.Text></div>
          <Typography.Title level={5}>{feedbackSummary(item)}</Typography.Title>
          <Typography.Text type="secondary">提交人 {item.user_id} · {item.feedback_id}</Typography.Text>
        </div>
        <Tag color={item.status==='accepted'?'success':item.status==='rejected'?'error':item.status==='reviewing'?'warning':'default'}>{feedbackStatusCopy[item.status]}</Tag>
        <div className="feedback-actions">
          {item.status==='pending_review'&&<Button icon={<ReloadOutlined/>} loading={working===`${item.feedback_id}:reviewing`} onClick={()=>void updateStatus(item,'reviewing')}>开始处理</Button>}
          {['pending_review','reviewing'].includes(item.status)&&<>
            <Button type="primary" icon={<CheckOutlined/>} loading={working===`${item.feedback_id}:accepted`} onClick={()=>void updateStatus(item,'accepted')}>采纳</Button>
            <Button danger icon={<StopOutlined/>} loading={working===`${item.feedback_id}:rejected`} onClick={()=>void updateStatus(item,'rejected')}>驳回</Button>
          </>}
        </div>
      </article>)}</div>:<Empty className="feedback-empty" description="当前筛选条件下没有反馈"/>}
    </div>}

    <Drawer title="创建评估数据集" size="large" open={drawerOpen} onClose={()=>setDrawerOpen(false)} destroyOnHidden>
      <Alert type="info" title="数据集格式" description="数据内容必须是 JSON 对象，测试样本放在样本列表中；每条样本需分别填写预期结果和实际结果。"/>
      <Form
        form={datasetForm}
        layout="vertical"
        initialValues={{dataset_type:'jd',payload_text:'{\n  "items": [\n    {"case_id": "case_001", "expected": "Python", "actual": "Python"}\n  ]\n}'}}
      >
        <Form.Item name="dataset_type" label="数据集类型" rules={[{required:true}]}><Select options={Object.entries(datasetTypeCopy).map(([value,label])=>({value,label}))}/></Form.Item>
        <Form.Item name="name" label="数据集名称" rules={[{required:true,message:'请输入数据集名称'}]}><Input maxLength={255}/></Form.Item>
        <Form.Item name="description" label="用途说明"><Input.TextArea rows={2}/></Form.Item>
        <Form.Item name="payload_text" label="测试样本 JSON" rules={[{required:true,message:'请输入测试样本'}]}><Input.TextArea className="evaluation-json-input" rows={14} spellCheck={false}/></Form.Item>
        <Button block type="primary" loading={working==='dataset'} onClick={()=>void createDataset()}>保存数据集</Button>
      </Form>
    </Drawer>
  </div>;
}
