import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import type {ReactNode} from 'react';
import {localizeSystemMessage} from '../../../shared/api';
import {
  App,
  Button,
  Drawer,
  Empty,
  Form,
  type FormInstance,
  Input,
  InputNumber,
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
  FileSearchOutlined,
  PauseOutlined,
  PlusOutlined,
  RadarChartOutlined,
  ReloadOutlined,
  SendOutlined,
  StopOutlined,
} from '@ant-design/icons';
import {useNavigate} from 'react-router-dom';
import {ApiError,type Position} from '../../../shared/api';
import type {CatalogSkill} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {CandidateDecisionBoard} from '../components/CandidateDecisionBoard';
import {
  changeEnterpriseJobStatus,
  createEnterprise,
  createEnterpriseJob,
  deleteEnterpriseJob,
  decideCandidate,
  getEnterpriseMatchTask,
  getMyEnterprise,
  getSkillWeights,
  listCandidateSubmissions,
  listEnterpriseJobs,
  listEnterpriseMatchEvaluations,
  listSkillCategories,
  matchEnterpriseSubmissions,
  saveSkillWeights,
} from '../api';
import type {CandidateSubmission,EnterpriseJob,EnterpriseJobInput,EnterpriseMatchEvaluation,EnterpriseMatchBatch,EnterpriseProfile,SalaryUnit,SkillWeight} from '../types';
import {listPublishedPositions} from '../../positions/api';

type ProfileValues={enterprise_name:string;industry?:string;scale?:string;location?:string;description?:string};
type WeightItem={skill_id:string;weight:number;kind:'required'|'bonus'|'related'};
type JobValues=Omit<EnterpriseJobInput,'enterprise_id'>&{weights?:WeightItem[]};
type WeightValues={weights:WeightItem[]};
type ViewMode='岗位设置'|'候选评估';
type AttemptStatus='pending'|'running'|'succeeded'|'failed';
type MatchAttempt={submissionId:string;resumeId:string;taskId:string|null;evaluationId:string|null;status:AttemptStatus;errorCode:string|null;errorMessage:string|null};
type SkillCategoryGroup={category:string;skills:CatalogSkill[]};
type ResourceState<T>=
  |{kind:'loading'}
  |{kind:'success';data:T[]}
  |{kind:'empty';data:T[]}
  |{kind:'forbidden';error:ApiError}
  |{kind:'unavailable';error:ApiError}
  |{kind:'error';error:ApiError};

function RecruitmentSpaceIcon(){
  return <svg className="enterprise-onboarding-icon" viewBox="0 0 64 64" fill="none" aria-hidden="true">
    <path d="M18 21v-5.5A5.5 5.5 0 0 1 23.5 10h17a5.5 5.5 0 0 1 5.5 5.5V21"/>
    <path d="M48.5 50H12a6 6 0 0 1-6-6V27a6 6 0 0 1 6-6h40a6 6 0 0 1 6 6v13"/>
    <path d="m7 31 19 7h12l19-7"/>
    <rect x="27" y="34" width="10" height="9" rx="2"/>
    <circle cx="50" cy="50" r="10"/>
    <path d="M50 44v12M44 50h12"/>
  </svg>;
}

const loadingResource=<T,>():ResourceState<T>=>({kind:'loading'});
const settledResource=<T,>(result:PromiseSettledResult<T[]>):ResourceState<T>=>{
  if(result.status==='fulfilled')return result.value.length?{kind:'success',data:result.value}:{kind:'empty',data:result.value};
  const error=result.reason instanceof ApiError?result.reason:new ApiError(0,result.reason instanceof Error?result.reason.message:'请求失败');
  if(error.status===403)return {kind:'forbidden',error};
  if([502,503,504].includes(error.status))return {kind:'unavailable',error};
  return {kind:'error',error};
};

function ResourceContent<T>({state,emptyTitle,emptyDescription,retry,children}:{
  state:ResourceState<T>;
  emptyTitle:string;
  emptyDescription:string;
  retry:()=>void;
  children:(data:T[])=>ReactNode;
}){
  if(state.kind==='loading')return <div className="state-panel loading-state" aria-live="polite"><Spin size="small"/><span className="state-panel-hint">正在加载…</span></div>;
  if(state.kind==='success')return children(state.data);
  if(state.kind==='empty')return <div className="enterprise-resource-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span><strong>{emptyTitle}</strong><br/>{emptyDescription}</span>}/></div>;
  const {error}=state;
  const title=state.kind==='forbidden'?'无权限':state.kind==='unavailable'?'上游不可用':'加载失败';
  const fallback=state.kind==='forbidden'?'当前账号没有查看此区域的权限。':state.kind==='unavailable'?'上游服务暂时不可用，请稍后重试。':'该区域加载失败，请重试。';
  return <Alert
    type="error"
    showIcon
    title={title}
    description={<div><div>{error.message||fallback}</div>{error.traceId&&<Typography.Text copyable type="secondary">trace_id: {error.traceId}</Typography.Text>}</div>}
    action={<Button size="small" icon={<ReloadOutlined/>} onClick={retry}>重试</Button>}
  />;
}

const statusCopy:Record<string,string>={
  draft:'草稿',
  published:'招聘中',
  paused:'已暂停',
  cancelled:'已取消',
  pending:'排队中',
  running:'评估中',
  succeeded:'已完成',
  completed:'已完成',
  failed:'失败',
};

const statusColor=(status:string)=>{
  if(['published','succeeded','completed'].includes(status))return 'success';
  if(['paused','pending','running'].includes(status))return 'warning';
  if(['cancelled','failed'].includes(status))return 'error';
  return 'default';
};

const attemptStatusCopy:Record<AttemptStatus,string>={pending:'排队中',running:'评估中',succeeded:'已完成',failed:'失败'};
const currentReportStatus=(status:string):AttemptStatus=>{
  if(['pending','queued','created'].includes(status))return 'pending';
  if(['running','processing','reconciling'].includes(status))return 'running';
  if(['succeeded','completed','current'].includes(status))return 'succeeded';
  return 'failed';
};
const latestReports=(items:EnterpriseMatchEvaluation[])=>{
  const seen=new Set<string>();
  return items.filter(item=>{
    if(seen.has(item.resume_id))return false;
    seen.add(item.resume_id);
    return true;
  });
};
const attemptsFromBatch=(result:EnterpriseMatchBatch):MatchAttempt[]=>result.items.map(item=>({
  submissionId:item.submission_id,
  resumeId:item.resume_id,
  taskId:item.task_id,
  evaluationId:item.evaluation_id,
  status:item.status==='created'?'pending':item.status==='reconciling'?'running':'failed',
  errorCode:item.error_code,
  errorMessage:item.error_message,
}));

const formatDate=(value:string|null)=>value
  ?new Intl.DateTimeFormat('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}).format(new Date(value))
  :'时间未知';

const salaryUnitLabel:Record<SalaryUnit,string>={year:'年',month:'月',day:'天'};
const employmentTypeLabel:Record<string,string>={full_time:'全职',part_time:'兼职',internship:'实习',contract:'合同'};
const salaryUnitOptions=[{value:'month',label:'元/月'},{value:'year',label:'元/年'},{value:'day',label:'元/天'}];
const weightKindOptions=[{value:'required',label:'必备'},{value:'bonus',label:'加分'},{value:'related',label:'相关'}];

function SkillWeightConfigurator({form,name,state,onRetry}:{
  form:FormInstance;
  name:string;
  state:ResourceState<SkillCategoryGroup>;
  onRetry:()=>void;
}){
  const [category,setCategory]=useState<string>();
  const [selectedSkillIds,setSelectedSkillIds]=useState<string[]>([]);
  const groups=useMemo(()=>state.kind==='success'?state.data:[],[state]);
  const skillNameById=useMemo(()=>{
    const map:Record<string,string>={};
    for(const group of groups)for(const skill of group.skills)map[skill.skill_id]=skill.skill_name;
    return map;
  },[groups]);
  const currentGroup=groups.find(item=>item.category===category);
  const addSelected=()=>{
    if(!selectedSkillIds.length)return;
    const current=(form.getFieldValue(name) as WeightItem[]|undefined)??[];
    const existing=new Set(current.map(item=>item.skill_id));
    const next=[...current];
    for(const skillId of selectedSkillIds){
      if(existing.has(skillId))continue;
      next.push({skill_id:skillId,weight:.5,kind:'required'});
      existing.add(skillId);
    }
    form.setFieldsValue({[name]:next});
    setSelectedSkillIds([]);
  };
  return <>
    {state.kind==='loading'&&<div className="state-panel loading-state" aria-live="polite"><Spin size="small"/><span className="state-panel-hint">正在加载技能目录…</span></div>}
    {state.kind==='success'&&<div className="enterprise-weight-picker">
      <div>
        <small>技能领域</small>
        <Select
          showSearch
          placeholder="选择领域"
          optionFilterProp="label"
          value={category}
          onChange={value=>{setCategory(value);setSelectedSkillIds([])}}
          options={groups.map(item=>({value:item.category,label:item.category}))}
        />
      </div>
      <div>
        <small>选择技能（可多选）</small>
        <Select
          mode="multiple"
          showSearch
          disabled={!category}
          placeholder={category?'可多选技能':'请先选择领域'}
          optionFilterProp="label"
          maxTagCount="responsive"
          value={selectedSkillIds}
          onChange={setSelectedSkillIds}
          options={(currentGroup?.skills??[]).map(skill=>({value:skill.skill_id,label:skill.skill_name}))}
        />
      </div>
      <Button icon={<PlusOutlined/>} disabled={!selectedSkillIds.length} onClick={addSelected}>添加所选技能</Button>
    </div>}
    {state.kind==='empty'&&<Alert type="info" title="技能目录为空" description="当前没有可批量添加的标准技能，请先到能力目录补充后再配置。"/>}
    {(state.kind==='forbidden'||state.kind==='unavailable'||state.kind==='error')&&<Alert
      type="error"
      showIcon
      title={state.kind==='forbidden'?'无权限':state.kind==='unavailable'?'上游不可用':'技能目录加载失败'}
      description={state.error.message||'技能目录暂时不可用，请重试。'}
      action={<Button size="small" icon={<ReloadOutlined/>} onClick={onRetry}>重试</Button>}
    />}
    <div className="enterprise-weight-editor">
      <Form.List name={name}>
        {(fields,{add,remove})=><>
          {fields.map(field=>{
            const {key:fieldKey,...fieldProps}=field;
            const skillId=form.getFieldValue([name,field.name,'skill_id']) as string|undefined;
            // 技能目录可能暂时缺少历史权重引用的条目；保留 skill_id，避免把有效配置隐藏成空泛提示。
            const skillName=skillId?skillNameById[skillId]||skillId:'技能未指定';
            return <div key={fieldKey}>
              <div className="enterprise-weight-skill"><strong>{skillName}</strong></div>
              <Form.Item {...fieldProps} name={[field.name,'skill_id']} hidden><Input/></Form.Item>
              <Form.Item {...fieldProps} name={[field.name,'weight']} rules={[{required:true,message:'请输入权重'}]}><InputNumber min={0} max={1} step={0.05}/></Form.Item>
              <Form.Item {...fieldProps} name={[field.name,'kind']}><Select options={weightKindOptions}/></Form.Item>
              <Button danger type="text" onClick={()=>remove(field.name)}>移除</Button>
            </div>;
          })}
          <Button icon={<PlusOutlined/>} onClick={()=>add({weight:.5,kind:'required'})}>添加技能</Button>
        </>}
      </Form.List>
    </div>
  </>;
}

export function EnterpriseRecruitment(){
  const {message}=App.useApp();
  const navigate=useNavigate();
  const [profile,setProfile]=useState<EnterpriseProfile|null>();
  const [jobs,setJobs]=useState<EnterpriseJob[]>([]);
  const [standardPositions,setStandardPositions]=useState<Position[]>([]);
  const [selectedId,setSelectedId]=useState<string>();
  const [weightState,setWeightState]=useState<ResourceState<SkillWeight>>(loadingResource);
  const [skillCategoryState,setSkillCategoryState]=useState<ResourceState<SkillCategoryGroup>>(loadingResource);
  const [reportState,setReportState]=useState<ResourceState<EnterpriseMatchEvaluation>>(loadingResource);
  const [submissionState,setSubmissionState]=useState<ResourceState<CandidateSubmission>>(loadingResource);
  const [mode,setMode]=useState<ViewMode>('岗位设置');
  const [candidateView,setCandidateView]=useState<'pool'|'board'>('pool');
  const [selectedSubmissionIds,setSelectedSubmissionIds]=useState<string[]>([]);
  const [matchAttempts,setMatchAttempts]=useState<MatchAttempt[]>([]);
  const [loading,setLoading]=useState(true);
  const [working,setWorking]=useState('');
  const [error,setError]=useState<ApiError>();
  const [jobDrawer,setJobDrawer]=useState(false);
  const [weightDrawer,setWeightDrawer]=useState(false);
  const [profileForm]=Form.useForm<ProfileValues>();
  const [jobForm]=Form.useForm<JobValues>();
  const [weightForm]=Form.useForm<WeightValues>();
  const salaryUnit=Form.useWatch('salary_unit',jobForm)??'month';
  const detailLoadId=useRef(0);
  const activeJobId=useRef<string|undefined>(undefined);
  const trackingGeneration=useRef(0);

  const selected=jobs.find(item=>item.enterprise_job_id===selectedId);
  const weights=useMemo(()=>weightState.kind==='success'?weightState.data:[],[weightState]);
  const reports=useMemo(()=>reportState.kind==='success'?reportState.data:[],[reportState]);
  const submissions=useMemo(()=>submissionState.kind==='success'?submissionState.data:[],[submissionState]);
  const skillNameById=useMemo(()=>{
    const map:Record<string,string>={};
    if(skillCategoryState.kind==='success')for(const group of skillCategoryState.data)for(const skill of group.skills)map[skill.skill_id]=skill.skill_name;
    return map;
  },[skillCategoryState]);

  const load=useCallback(async(preferredId?:string)=>{
    setLoading(true);setError(undefined);
    try{
      const [enterprise,positions]=await Promise.all([getMyEnterprise(),listPublishedPositions()]);
      setStandardPositions(positions);
      setProfile(enterprise);
      if(!enterprise){setJobs([]);setSelectedId(undefined);return}
      const values=await listEnterpriseJobs();
      setJobs(values);
      setSelectedId(current=>{
        const candidate=preferredId||current;
        return values.some(item=>item.enterprise_job_id===candidate)?candidate:values[0]?.enterprise_job_id;
      });
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[]);

  const loadSkillCategories=useCallback(async()=>{
    setSkillCategoryState(loadingResource());
    const result=await Promise.allSettled([listSkillCategories()]);
    setSkillCategoryState(settledResource(result[0]));
  },[]);

  const loadJobDetail=useCallback(async(jobId:string)=>{
    const loadId=++detailLoadId.current;
    setWeightState(loadingResource());
    setReportState(loadingResource());
    setSubmissionState(loadingResource());
    const [weightResult,reportResult,submissionResult]=await Promise.allSettled([
      getSkillWeights(jobId),
      listEnterpriseMatchEvaluations(jobId),
      listCandidateSubmissions(jobId),
    ]);
    if(loadId!==detailLoadId.current||activeJobId.current!==jobId)return;
    setWeightState(settledResource(weightResult));
    setReportState(reportResult.status==='fulfilled'?settledResource({status:'fulfilled',value:latestReports(reportResult.value)}):settledResource(reportResult));
    setSubmissionState(settledResource(submissionResult));
  },[]);

  const refreshReports=useCallback(async(jobId:string)=>{
    const result=await Promise.allSettled([listEnterpriseMatchEvaluations(jobId)]);
    if(activeJobId.current!==jobId)return;
    const reportResult=result[0];
    setReportState(reportResult.status==='fulfilled'?settledResource({status:'fulfilled',value:latestReports(reportResult.value)}):settledResource(reportResult));
  },[]);

  const trackAttempt=useCallback((jobId:string,attempt:MatchAttempt,generation:number)=>{
    const poll=async(currentAttempt:MatchAttempt,pollCount:number):Promise<void>=>{
      if(!currentAttempt.taskId||generation!==trackingGeneration.current||activeJobId.current!==jobId)return;
      try{
        const task=await getEnterpriseMatchTask(currentAttempt.taskId);
        if(generation!==trackingGeneration.current||activeJobId.current!==jobId)return;
        const next:MatchAttempt={...currentAttempt,status:task.status,evaluationId:task.evaluation_id||currentAttempt.evaluationId,errorCode:task.error_code||null,errorMessage:task.error_message||null};
        setMatchAttempts(current=>current.map(item=>item.taskId===currentAttempt.taskId?next:item));
        if(task.status==='succeeded'){
          await refreshReports(jobId);
          return;
        }
        if(task.status==='failed')return;
        if(pollCount>=19){
          setMatchAttempts(current=>current.map(item=>item.taskId===currentAttempt.taskId?{...item,errorMessage:'状态仍在处理中，请稍后刷新或前往任务中心查看。'}:item));
          return;
        }
        window.setTimeout(()=>void poll(next,pollCount+1),1500);
      }catch(reason){
        const error=reason as ApiError;
        if(generation===trackingGeneration.current&&activeJobId.current===jobId)setMatchAttempts(current=>current.map(item=>item.taskId===currentAttempt.taskId?{...item,errorCode:error.status?String(error.status):null,errorMessage:`状态跟踪失败：${error.message}。请前往任务中心查看。`}:item));
      }
    };
    void poll(attempt,0);
  },[refreshReports]);

  const retryResource=useCallback(async<T,>(jobId:string,request:()=>Promise<T[]>,setState:(state:ResourceState<T>)=>void)=>{
    setState(loadingResource());
    const result=await Promise.allSettled([request()]);
    if(activeJobId.current===jobId)setState(settledResource(result[0]));
  },[]);

  useEffect(()=>{const timer=window.setTimeout(()=>void load(),0);return()=>window.clearTimeout(timer)},[load]);
  useEffect(()=>{const timer=window.setTimeout(()=>void loadSkillCategories(),0);return()=>window.clearTimeout(timer)},[loadSkillCategories]);
  useEffect(()=>{
    activeJobId.current=selectedId;
    detailLoadId.current+=1;
    trackingGeneration.current+=1;
    if(!selectedId)return;
    const timer=window.setTimeout(()=>{
      setMatchAttempts([]);
      setSelectedSubmissionIds([]);
      void loadJobDetail(selectedId);
    },0);
    return()=>window.clearTimeout(timer);
  },[loadJobDetail,selectedId]);

  useEffect(()=>{
    if(mode!=='候选评估'||candidateView!=='pool'||!selectedId)return;
    const pendingReports=reports.filter(report=>report.task_id&&['pending','running'].includes(currentReportStatus(report.status)));
    if(!pendingReports.length)return;
    const jobId=selectedId;
    const timer=window.setTimeout(async()=>{
      if(activeJobId.current!==jobId)return;
      const results=await Promise.allSettled(pendingReports.map(report=>getEnterpriseMatchTask(report.task_id!)));
      if(activeJobId.current!==jobId||!results.some(result=>result.status==='fulfilled'))return;
      await refreshReports(jobId);
    },1500);
    return()=>window.clearTimeout(timer);
  },[candidateView,mode,refreshReports,reports,selectedId]);

  const totals=useMemo(()=>({
    active:jobs.filter(item=>item.status==='published').length,
    candidates:new Set(reports.map(item=>item.resume_id)).size,
    completed:reports.filter(item=>['succeeded','completed'].includes(item.status)).length,
  }),[jobs,reports]);

  const submitProfile=async()=>{
    const values=await profileForm.validateFields();
    setWorking('profile');setError(undefined);
    try{await createEnterprise(values);message.success('企业档案已建立');await load()}
    catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const submitJob=async()=>{
    if(!profile)return;
    const values=await jobForm.validateFields();
    setWorking('job');setError(undefined);
    try{
      const {weights,...jobValues}=values;
      const job=await createEnterpriseJob({...jobValues,enterprise_id:profile.enterprise_id});
      if(weights?.length){
        try{
          await saveSkillWeights(job.enterprise_job_id,weights.map(item=>({
            skill_id:item.skill_id.trim(),
            weight:item.weight,
            is_required:item.kind==='required',
            is_bonus:item.kind==='bonus',
          })));
          message.success('招聘岗位已创建，技能权重已保存');
        }catch{
          message.warning('招聘岗位已创建，但技能权重保存失败，请稍后在岗位设置中重试');
        }
      }else{
        message.success('招聘岗位已创建');
      }
      setJobDrawer(false);jobForm.resetFields();await load(job.enterprise_job_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const runStatus=async(action:'publish'|'pause'|'resume'|'cancel')=>{
    if(!selected)return;
    setWorking(action);setError(undefined);
    try{
      await changeEnterpriseJobStatus(selected.enterprise_job_id,action);
      message.success('岗位状态已更新');await load(selected.enterprise_job_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const removeJob=async()=>{
    if(!selected)return;
    setWorking('delete');setError(undefined);
    try{
      await deleteEnterpriseJob(selected.enterprise_job_id);
      message.success('招聘记录已删除');
      setSelectedId(undefined);
      setMode('岗位设置');
      await load();
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const openWeights=()=>{
    weightForm.setFieldsValue({weights:weights.map(item=>({
      skill_id:item.skill_id,
      weight:item.weight,
      kind:item.is_required?'required':item.is_bonus?'bonus':'related',
    }))});
    setWeightDrawer(true);
  };

  const submitWeights=async()=>{
    if(!selected)return;
    const values=await weightForm.validateFields();
    setWorking('weights');setError(undefined);
    try{
      await saveSkillWeights(selected.enterprise_job_id,values.weights.map(item=>({
        skill_id:item.skill_id.trim(),
        weight:item.weight,
        is_required:item.kind==='required',
        is_bonus:item.kind==='bonus',
      })));
      message.success('技能权重已保存');setWeightDrawer(false);await loadJobDetail(selected.enterprise_job_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const runMatch=async()=>{
    if(!selected)return;
    if(!selectedSubmissionIds.length){message.warning('请选择至少一个已授权候选人');return}
    setWorking('match');setError(undefined);
    try{
      const result=await matchEnterpriseSubmissions(selected.enterprise_job_id,[...new Set(selectedSubmissionIds)]);
      const attempts=attemptsFromBatch(result);
      const failed=attempts.filter(item=>item.status==='failed');
      const accepted=attempts.filter(item=>item.taskId&&item.status!=='failed');
      setMatchAttempts(attempts);
      message.success(failed.length?`已接收 ${accepted.length} 份评估任务，${failed.length} 份提交失败`:`已接收 ${accepted.length} 份评估任务，正在等待完成`);
      setSelectedSubmissionIds([]);
      const generation=++trackingGeneration.current;
      accepted.forEach(item=>void trackAttempt(selected.enterprise_job_id,item,generation));
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  const decide=async(report:EnterpriseMatchEvaluation,decision:'fit'|'unfit')=>{
    setWorking(`${decision}:${report.resume_id}`);setError(undefined);
    try{
      await decideCandidate(selected!.enterprise_job_id,report.resume_id,report.evaluation_id,decision);
      message.success(decision==='fit'?'已标记为适配':'已标记为不适配');
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('')}
  };

  if(loading)return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>;

  if(profile===null)return <div className="enterprise-onboarding">
    <div>
      <RecruitmentSpaceIcon/>
      <Typography.Title level={2}>建立企业招聘空间</Typography.Title>
      <Typography.Paragraph>企业档案用于隔离岗位、候选人和匹配记录。建立后即可进入招聘闭环。</Typography.Paragraph>
    </div>
    {error&&<Failure message={error.message} status={error.status}/>}
    <Form form={profileForm} layout="vertical" onFinish={()=>void submitProfile()}>
      <Form.Item name="enterprise_name" label="企业名称" rules={[{required:true,message:'请输入企业名称'}]}><Input maxLength={255}/></Form.Item>
      <div className="enterprise-form-row">
        <Form.Item name="industry" label="所属行业"><Input/></Form.Item>
        <Form.Item name="scale" label="企业规模"><Input placeholder="例如：100–499 人"/></Form.Item>
      </div>
      <Form.Item name="location" label="所在地"><Input/></Form.Item>
      <Form.Item name="description" label="企业简介"><Input.TextArea rows={3} maxLength={500}/></Form.Item>
      <Button block type="primary" htmlType="submit" loading={working==='profile'}>创建企业空间</Button>
    </Form>
  </div>;

  return <div className="enterprise-page">
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>企业招聘工作台</Typography.Title>
        <Typography.Paragraph>{profile?.enterprise_name} · 从岗位发布到候选人评估与决策</Typography.Paragraph>
      </div>
      <Button type="primary" icon={<PlusOutlined/>} onClick={()=>setJobDrawer(true)}>创建招聘岗位</Button>
    </div>

    {error&&<Failure message={error.message} status={error.status} retry={()=>void load(selectedId)}/>}

    <section className="enterprise-trust-strip">
      <div><span>招聘中岗位</span><strong>{totals.active}</strong></div>
      <div><span>当前岗位候选人</span><strong>{totals.candidates}</strong></div>
      <div><span>已完成评估</span><strong>{totals.completed}</strong></div>
      <div><span>租户数据边界</span><strong>企业内可见</strong></div>
    </section>

    <div className="enterprise-layout">
      <aside className="enterprise-job-library">
        <div className="enterprise-panel-head"><Typography.Text strong>招聘岗位</Typography.Text><Tag>{jobs.length}</Tag></div>
        {jobs.length?jobs.map(item=><button
          key={item.enterprise_job_id}
          className={item.enterprise_job_id===selectedId?'is-selected':''}
          onClick={()=>setSelectedId(item.enterprise_job_id)}
        >
          <span><strong>{item.title}</strong><small>{item.location||'地点未设置'} · {item.headcount} 人</small></span>
          <Tag color={statusColor(item.status)}>{statusCopy[item.status]||'状态未知'}</Tag>
        </button>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无招聘岗位"/>}
      </aside>

      <main className="enterprise-workspace">
        {!selected?<Empty description="创建或选择一个招聘岗位后开始"/>:<>
          <header className="enterprise-job-head">
            <div>
              <Typography.Title level={3}>{selected.title}</Typography.Title>
              <Typography.Text type="secondary">{selected.employment_type?employmentTypeLabel[selected.employment_type]||'用工类型未设置':'用工类型未设置'} · {selected.location||'地点未设置'} · 招聘 {selected.headcount} 人</Typography.Text>
            </div>
            <div className="enterprise-status-actions">
              {selected.status==='draft'&&<Button type="primary" icon={<SendOutlined/>} loading={working==='publish'} onClick={()=>void runStatus('publish')}>发布岗位</Button>}
              {selected.status==='published'&&<Button icon={<PauseOutlined/>} loading={working==='pause'} onClick={()=>void runStatus('pause')}>暂停招聘</Button>}
              {selected.status==='paused'&&<Button type="primary" icon={<ReloadOutlined/>} loading={working==='resume'} onClick={()=>void runStatus('resume')}>恢复招聘</Button>}
              {!['cancelled'].includes(selected.status)&&<Button danger icon={<StopOutlined/>} loading={working==='cancel'} onClick={()=>void runStatus('cancel')}>取消岗位</Button>}
              <Popconfirm
                title="删除这条招聘记录？"
                description="岗位、候选投递和相关决策记录将被永久删除，且无法恢复。"
                okText="确认删除"
                cancelText="保留记录"
                okButtonProps={{danger:true,loading:working==='delete'}}
                onConfirm={()=>removeJob()}
              >
                <Button danger type="text" icon={<DeleteOutlined/>} loading={working==='delete'}>删除记录</Button>
              </Popconfirm>
            </div>
          </header>

          {selected.status==='cancelled'&&<Alert className="enterprise-cancelled-banner" type="warning" showIcon title="该岗位已取消" description="已取消的岗位仅保留历史记录供查看，不能配置权重、发起候选评估或进行适配决策。"/>}

          <Segmented<ViewMode> value={mode} options={['岗位设置','候选评估']} onChange={setMode}/>

          <div className="enterprise-job-settings" hidden={mode!=='岗位设置'}>
            <section>
              <div className="enterprise-panel-head"><Typography.Title level={4}>岗位要求</Typography.Title><Tag color={statusColor(selected.status)}>{statusCopy[selected.status]||'状态未知'}</Tag></div>
              <p>{selected.jd_text||'尚未填写岗位描述。创建新岗位时可录入完整 JD。'}</p>
              <dl>
                <div><dt>标准岗位</dt><dd>{standardPositions.find(item=>item.position_id===selected.standard_position_id)?.name||(selected.standard_position_id?'已关联':'未关联')}</dd></div>
                <div><dt>薪资范围</dt><dd>{selected.salary_min!==null&&selected.salary_max!==null
                  ?`${selected.salary_min} – ${selected.salary_max} 元/${salaryUnitLabel[selected.salary_unit]}`
                  :selected.salary_min!==null?`${selected.salary_min} 元/${salaryUnitLabel[selected.salary_unit]}起`
                  :selected.salary_max!==null?`最高 ${selected.salary_max} 元/${salaryUnitLabel[selected.salary_unit]}`
                  :'未设置'}</dd></div>
                <div><dt>更新时间</dt><dd>{formatDate(selected.updated_at)}</dd></div>
              </dl>
            </section>
            <section>
              <div className="enterprise-panel-head"><Typography.Title level={4}>企业技能权重</Typography.Title><Button size="small" disabled={selected.status==='cancelled'||!['success','empty'].includes(weightState.kind)} onClick={openWeights}>配置权重</Button></div>
              <ResourceContent state={weightState} emptyTitle="暂无数据" emptyDescription="尚未配置企业技能权重。候选评估前配置权重，可获得更准确的匹配结果。" retry={()=>void retryResource(selected.enterprise_job_id,()=>getSkillWeights(selected.enterprise_job_id),setWeightState)}>{items=><div className="enterprise-weight-list">{items.map(item=><div key={item.id}>
                <span><strong>{skillNameById[item.skill_id]||item.skill_id}</strong><small>{item.is_required?'必备技能':item.is_bonus?'加分技能':'相关技能'}</small></span>
                <b>{Math.round(item.weight*100)}%</b>
              </div>)}</div>}</ResourceContent>
            </section>
          </div>
          <div className="enterprise-candidate-view" hidden={mode!=='候选评估'}>
            <div className="enterprise-candidate-tabs">
              <Segmented value={candidateView} options={[{label:'候选池',value:'pool'},{label:'决策板',value:'board'}]} onChange={setCandidateView}/>
            </div>
            <div className="enterprise-board-panel" hidden={candidateView!=='board'}><CandidateDecisionBoard jobId={selected.enterprise_job_id}/></div>
            <div className="enterprise-candidate-pool" hidden={candidateView!=='pool'}>
            <section className="enterprise-match-launcher">
              <div>
                <Typography.Title level={4}>提交候选人评估</Typography.Title>
              </div>
              {matchAttempts.length>0&&<div className="enterprise-submission-pool" aria-label="本次评估任务状态">{matchAttempts.map(item=>{const candidate=submissions.find(value=>value.submission_id===item.submissionId);return <div key={item.submissionId}>
                <span><strong>{candidate?.resume_display_name||'候选人名称未返回'}</strong><small>{item.taskId?'任务记录':'未创建任务'}</small>{item.errorMessage&&<Typography.Text type="danger">{localizeSystemMessage(item.errorMessage)}</Typography.Text>}{item.errorCode&&<Typography.Text type="secondary">处理失败</Typography.Text>}</span>
                <Tag color={item.status==='succeeded'?'success':item.status==='failed'?'error':'processing'}>{attemptStatusCopy[item.status]}</Tag>
              </div>})}</div>}
              <ResourceContent state={submissionState} emptyTitle="暂无数据" emptyDescription="暂无候选人投递。候选人通过个人端提交申请后，此处会显示已授权候选池。" retry={()=>void retryResource(selected.enterprise_job_id,()=>listCandidateSubmissions(selected.enterprise_job_id),setSubmissionState)}>{items=><div className="enterprise-submission-pool">
                {items.map(item=><label key={item.submission_id} className={selectedSubmissionIds.includes(item.submission_id)?'is-selected':''}>
                  <input type="checkbox" disabled={selected.status==='cancelled'||!item.matchable} checked={selectedSubmissionIds.includes(item.submission_id)} onChange={event=>setSelectedSubmissionIds(current=>event.target.checked?[...current,item.submission_id]:current.filter(id=>id!==item.submission_id))}/>
                  <span><strong>{item.resume_display_name}</strong></span>
                  <Tag color={item.matchable?'success':'default'}>{item.matchable?'可匹配':'不可匹配'}</Tag>
                </label>)}
              </div>}</ResourceContent>
              <Button type="primary" icon={<RadarChartOutlined/>} disabled={selected.status==='cancelled'||submissionState.kind!=='success'||!selectedSubmissionIds.length} loading={working==='match'} onClick={()=>void runMatch()}>运行候选评估</Button>
            </section>
            <section className="enterprise-candidate-list">
              <div className="enterprise-panel-head"><Typography.Title level={4}>评估记录</Typography.Title><Tag>{reports.length}</Tag></div>
              <ResourceContent state={reportState} emptyTitle="暂无数据" emptyDescription="尚无候选人评估记录。" retry={()=>void refreshReports(selected.enterprise_job_id)}>{items=>items.map(report=>{const candidate=submissions.find(item=>item.resume_id===report.resume_id);const currentStatus=currentReportStatus(report.status);return <div className="enterprise-report-row" key={report.evaluation_id}>
                <span>
                  <strong>{candidate?.resume_display_name||'候选人名称未返回'}</strong>
                  <small>{formatDate(report.updated_at||report.created_at)}</small>
                </span>
                <Tag color={currentStatus==='succeeded'?'success':currentStatus==='failed'?'error':'processing'}>{attemptStatusCopy[currentStatus]}</Tag>
                <div>
                  <Button size="small" icon={<FileSearchOutlined/>} disabled={currentStatus!=='succeeded'} onClick={()=>navigate(`/enterprise/recruitment/reports/${encodeURIComponent(report.evaluation_id)}`,{state:{positionName:selected.title}})}>查看正式报告</Button>
                  <Button size="small" icon={<CheckOutlined/>} loading={working===`fit:${report.resume_id}`} disabled={selected.status==='cancelled'||currentStatus!=='succeeded'} onClick={()=>void decide(report,'fit')}>适配</Button>
                  <Button size="small" danger loading={working===`unfit:${report.resume_id}`} disabled={selected.status==='cancelled'||currentStatus!=='succeeded'} onClick={()=>void decide(report,'unfit')}>不适配</Button>
                </div>
              </div>})}</ResourceContent>
            </section>
            </div>
          </div>
        </>}
      </main>
    </div>

    <Drawer title="创建招聘岗位" size="large" open={jobDrawer} onClose={()=>setJobDrawer(false)} destroyOnHidden>
      <Form form={jobForm} layout="vertical" initialValues={{headcount:1,status:'draft',salary_unit:'month',weights:[]}}>
        <Form.Item name="title" label="岗位名称" rules={[{required:true,message:'请输入岗位名称'}]}><Input maxLength={255}/></Form.Item>
        <div className="enterprise-form-row">
          <Form.Item name="location" label="工作地点"><Input/></Form.Item>
          <Form.Item name="employment_type" label="用工类型"><Select options={[{value:'full_time',label:'全职'},{value:'part_time',label:'兼职'},{value:'internship',label:'实习'}]}/></Form.Item>
        </div>
        <Form.Item name="standard_position_id" label="关联标准岗位"><Select allowClear showSearch placeholder="可选，用于继承岗位能力基线" filterOption={(input,option)=>String(option?.label||'').toLowerCase().includes(input.toLowerCase())} options={standardPositions.map(item=>({value:item.position_id,label:item.name}))}/></Form.Item>
        <Form.Item name="jd_text" label="岗位描述"><Input.TextArea rows={6} maxLength={6000}/></Form.Item>
        <div className="enterprise-form-row enterprise-form-row-three">
          <Form.Item name="headcount" label="招聘人数" rules={[{required:true}]}><InputNumber min={0}/></Form.Item>
          <Form.Item name="salary_unit" label="薪资单位"><Select options={salaryUnitOptions}/></Form.Item>
          <Form.Item name="status" label="初始状态"><Select options={[{value:'draft',label:'保存为草稿'},{value:'published',label:'直接发布'}]}/></Form.Item>
        </div>
        <div className="enterprise-form-row">
          <Form.Item name="salary_min" label={`最低薪资（元/${salaryUnitLabel[salaryUnit]}）`}><InputNumber min={0}/></Form.Item>
          <Form.Item name="salary_max" label={`最高薪资（元/${salaryUnitLabel[salaryUnit]}）`}><InputNumber min={0}/></Form.Item>
        </div>
        <section className="enterprise-create-weights">
          <div className="enterprise-panel-head"><Typography.Text strong>技能权重（可选）</Typography.Text><Typography.Text type="secondary">创建岗位后可立即用于候选评估</Typography.Text></div>
          <SkillWeightConfigurator form={jobForm} name="weights" state={skillCategoryState} onRetry={()=>void loadSkillCategories()}/>
        </section>
        <Button block type="primary" loading={working==='job'} onClick={()=>void submitJob()}>创建岗位</Button>
      </Form>
    </Drawer>

    <Drawer title="配置企业技能权重" size="large" open={weightDrawer} onClose={()=>setWeightDrawer(false)} destroyOnHidden>
      <Alert type="info" title="技能编号必须来自能力目录" description="权重用于企业岗位匹配；必备技能和加分技能会进入可解释报告。"/>
      <Form form={weightForm} layout="vertical" initialValues={{weights:[]}}>
        <SkillWeightConfigurator form={weightForm} name="weights" state={skillCategoryState} onRetry={()=>void loadSkillCategories()}/>
        <Button className="enterprise-drawer-submit" block type="primary" loading={working==='weights'} onClick={()=>void submitWeights()}>保存权重</Button>
      </Form>
    </Drawer>
  </div>;
}
