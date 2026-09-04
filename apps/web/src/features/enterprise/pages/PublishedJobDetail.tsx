import {ArrowLeftOutlined,CheckCircleOutlined,EnvironmentOutlined,FileDoneOutlined,RadarChartOutlined} from '@ant-design/icons';
import {Button,Result,Select,Spin,Tag,Typography} from 'antd';
import {useEffect,useState} from 'react';
import {Link,useNavigate,useParams} from 'react-router-dom';
import {getPublishedEnterpriseJob,listCandidateSubmissionOptions,revokeCandidateSubmission,submitCandidate} from '../api';
import {createEnterpriseJobMatchTask,getMatchPreflight,getMatchTask} from '../../matching/api';
import {ToastAlert as Alert} from '../../../shared/components/States';
import type {CandidateApplicationOption,PublishedEnterpriseJob,SalaryUnit} from '../types';
import type {MatchPreflight,MatchTask} from '../../matching/types';

const employmentCopy:Record<string,string>={full_time:'全职',part_time:'兼职',internship:'实习',contract:'合同'};
const salaryUnitLabel:Record<SalaryUnit,string>={year:'年',month:'月',day:'天'};
const displaySalary=(job:PublishedEnterpriseJob)=>job.salary_min!==null&&job.salary_max!==null
  ?`${job.salary_min.toLocaleString('zh-CN')}–${job.salary_max.toLocaleString('zh-CN')} 元/${salaryUnitLabel[job.salary_unit]}`:'薪资面议';

export function PublishedJobDetail(){
  const {jobId=''}=useParams();
  const navigate=useNavigate();
  const [job,setJob]=useState<PublishedEnterpriseJob>();
  const [loading,setLoading]=useState(true);
  const [missing,setMissing]=useState(false);
  const [options,setOptions]=useState<CandidateApplicationOption[]>([]);
  const [selectedResumeId,setSelectedResumeId]=useState<string>();
  const [working,setWorking]=useState(false);
  const [actionError,setActionError]=useState('');
  const [preflight,setPreflight]=useState<MatchPreflight>();
  const [preflightLoading,setPreflightLoading]=useState(false);
  const [matchTask,setMatchTask]=useState<MatchTask>();
  const [matchWorking,setMatchWorking]=useState(false);
  const [matchError,setMatchError]=useState('');
  useEffect(()=>{let current=true;Promise.allSettled([getPublishedEnterpriseJob(jobId),listCandidateSubmissionOptions(jobId)]).then(([jobResult,optionResult])=>{if(!current)return;if(jobResult.status==='rejected'){setMissing(true);return}setJob(jobResult.value);if(optionResult.status==='rejected'){setActionError(optionResult.reason instanceof Error?optionResult.reason.message:'简历列表读取失败，请刷新重试。');return}const optionValues=optionResult.value;setOptions(optionValues);const selectable=optionValues.filter(option=>option.eligible);setSelectedResumeId(selectable.find(option=>option.submission?.status==='submitted')?.resume_id??selectable[0]?.resume_id)}).finally(()=>{if(current)setLoading(false)});return()=>{current=false}},[jobId]);
  useEffect(()=>{
    let current=true;
    setPreflight(undefined);setMatchError('');
    if(!selectedResumeId)return()=>{current=false};
    setPreflightLoading(true);
    getMatchPreflight(selectedResumeId,jobId,'enterprise_job')
      .then(value=>{if(current)setPreflight(value)})
      .catch(reason=>{if(current)setMatchError(reason instanceof Error?reason.message:'匹配准备检查失败，请稍后重试。')})
      .finally(()=>{if(current)setPreflightLoading(false)});
    return()=>{current=false};
  },[jobId,selectedResumeId]);
  useEffect(()=>{
    if(!matchTask)return;
    if(matchTask.status==='succeeded'){
      if(matchTask.evaluation_id)navigate(`/matching/reports/${encodeURIComponent(matchTask.evaluation_id)}`,{state:{positionName:job?.title}});
      else setMatchError('匹配任务已完成，但没有返回正式评估报告。');
      setMatchWorking(false);
      return;
    }
    if(matchTask.status==='failed'){
      setMatchError(matchTask.error_message||'正式匹配失败，请检查简历和岗位画像后重试。');
      setMatchWorking(false);
      return;
    }
    const timer=window.setTimeout(()=>{
      getMatchTask(matchTask.task_id).then(setMatchTask).catch(reason=>{
        setMatchError(reason instanceof Error?reason.message:'匹配状态读取失败，请重试。');
        setMatchWorking(false);
      });
    },1000);
    return()=>window.clearTimeout(timer);
  },[job?.title,matchTask,navigate]);
  if(loading)return <div className="center-loading" aria-live="polite"><Spin size="large" description="正在读取岗位详情"/></div>;
  if(missing||!job)return <Result status="404" title="岗位不可用" subTitle="该岗位未发布、已停止招聘或不存在。" extra={<Link to="/jobs">返回企业岗位</Link>}/>;
  const selectableOptions=options.filter(option=>option.eligible);
  const selected=selectableOptions.find(option=>option.resume_id===selectedResumeId);
  const refreshOptions=async()=>{
    const refreshed=await listCandidateSubmissionOptions(jobId);
    setOptions(refreshed);
    const selectable=refreshed.filter(option=>option.eligible);
    setSelectedResumeId(current=>selectable.some(option=>option.resume_id===current)?current:selectable[0]?.resume_id);
  };
  const act=async(action:'submit'|'revoke')=>{
    if(!selected)return;
    setWorking(true);setActionError('');
    try{
      if(action==='submit')await submitCandidate(jobId,selected.resume_id);
      else await revokeCandidateSubmission(jobId,selected.resume_id);
      await refreshOptions();
    }catch(error){setActionError(error instanceof Error?error.message:'请稍后重试。')}
    finally{setWorking(false)}
  };
  const runFormalMatch=async()=>{
    if(!selected||!preflight?.ready)return;
    setMatchWorking(true);setMatchError('');
    try{
      const runId=typeof crypto!=='undefined'&&'randomUUID' in crypto?crypto.randomUUID():`${Date.now()}`;
      setMatchTask(await createEnterpriseJobMatchTask(selected.resume_id,jobId,runId));
    }catch(error){setMatchError(error instanceof Error?error.message:'正式匹配启动失败，请稍后重试。');setMatchWorking(false)}
  };
  const status=actionError?'投递失败':!selected?'不满足前置条件':selected.submission?.status==='submitted'?'已投递':selected.submission?.status==='revoked'?'已撤销':'未投递';
  return <div className="published-job-detail">
    <Link className="published-job-back" to="/jobs"><ArrowLeftOutlined/>返回企业岗位</Link>
    <header><div><Typography.Title level={2}>{job.title}</Typography.Title><Typography.Text strong>{job.enterprise_name}</Typography.Text></div><Tag className="published-job-status" color="success"><CheckCircleOutlined/><span>已发布 · 招聘中</span></Tag></header>
    <div className="published-job-facts"><div><span>工作地点</span><strong><EnvironmentOutlined/>{job.location||'待定'}</strong></div><div><span>用工类型</span><strong>{employmentCopy[job.employment_type||'']||'用工类型待定'}</strong></div><div><span>招聘人数</span><strong>{job.headcount} 人</strong></div><div><span>薪资</span><strong>{displaySalary(job)}</strong></div></div>
    <main className="published-job-description"><Typography.Title level={3}>岗位描述</Typography.Title><Typography.Paragraph>{job.jd_text||'企业暂未提供更多岗位描述。'}</Typography.Paragraph></main>
    <aside className="published-job-apply"><FileDoneOutlined/><div className="published-job-apply-content"><Typography.Title level={4}>投递此岗位</Typography.Title><Typography.Paragraph>只可选择你本人拥有、已生成验证快照且满足当前投递要求的简历。</Typography.Paragraph>
      {selectableOptions.length>0
        ?<Select aria-label="选择投递简历" value={selectedResumeId} onChange={setSelectedResumeId} options={selectableOptions.map(option=>({value:option.resume_id,label:option.resume_display_name}))}/>
        :<Alert type="warning" showIcon title="不满足前置条件" description={<span>当前没有可投递的验证快照。请先到<Link to="/profile/resumes">我的简历</Link>完成解析、技能画像与验证快照。</span>}/>
      }
      {options.some(option=>!option.eligible)&&selectableOptions.length>0&&<Typography.Text className="published-job-ineligible">另有简历尚未生成有效验证快照，已从可选项中排除。<Link to="/profile/resumes">前往恢复</Link></Typography.Text>}
      <div className="published-job-apply-actions"><Tag color={actionError?'error':status==='已投递'?'success':status==='已撤销'?'warning':status==='不满足前置条件'?'warning':'default'}>{status}</Tag>{actionError&&<Typography.Text type="danger">{actionError}</Typography.Text>}
        {selected&&selected.submission?.status==='submitted'?<Button danger loading={working} onClick={()=>void act('revoke')}>撤销投递</Button>:selected?<Button type="primary" loading={working} onClick={()=>void act('submit')}>{selected.submission?.status==='revoked'?'重新投递':'确认投递'}</Button>:<Link className="published-job-apply-cta" to="/profile/resumes"><Button type="primary">完善我的简历</Button></Link>}
      </div>
      {selected&&<div className="published-job-formal-match">
        <div><Typography.Text strong>CV × JD 正式匹配</Typography.Text><Typography.Text type="secondary">使用当前已验证 CV 快照与该企业岗位的正式 JD 画像生成可解释报告。</Typography.Text></div>
        {matchError&&<Alert type="error" showIcon title="匹配暂不可用" description={matchError}/>}
        {!matchError&&<Typography.Text type="secondary">{preflightLoading?'正在检查正式画像…':preflight?.ready?'CV 与 JD 正式画像已就绪':'当前 CV 或 JD 正式画像尚未就绪'}</Typography.Text>}
        <Button icon={<RadarChartOutlined/>} loading={matchWorking} disabled={preflightLoading||!preflight?.ready||matchWorking} onClick={()=>void runFormalMatch()}>查看正式匹配报告</Button>
      </div>}
    </div></aside>
  </div>;
}
