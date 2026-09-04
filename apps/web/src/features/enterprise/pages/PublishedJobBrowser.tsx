import {EnvironmentOutlined,FileDoneOutlined,RightOutlined} from '@ant-design/icons';
import {Button,Empty,Result,Spin,Tag,Typography} from 'antd';
import {useEffect,useState} from 'react';
import {Link} from 'react-router-dom';
import {listPublishedEnterpriseJobs} from '../api';
import type {PublishedEnterpriseJob,SalaryUnit} from '../types';

const employmentCopy:Record<string,string>={full_time:'全职',part_time:'兼职',internship:'实习',contract:'合同'};
const salaryUnitLabel:Record<SalaryUnit,string>={year:'年',month:'月',day:'天'};
const money=(value:number)=>new Intl.NumberFormat('zh-CN').format(value);
const salary=(job:PublishedEnterpriseJob)=>job.salary_min!==null&&job.salary_max!==null
  ?`${money(job.salary_min)}–${money(job.salary_max)} 元/${salaryUnitLabel[job.salary_unit]}`
  :job.salary_min!==null?`${money(job.salary_min)} 元/${salaryUnitLabel[job.salary_unit]}起`
  :job.salary_max!==null?`最高 ${money(job.salary_max)} 元/${salaryUnitLabel[job.salary_unit]}`
  :'薪资面议';

export function PublishedJobBrowser(){
  const [jobs,setJobs]=useState<PublishedEnterpriseJob[]>([]);
  const [loading,setLoading]=useState(true);
  const [failed,setFailed]=useState(false);

  useEffect(()=>{let current=true;listPublishedEnterpriseJobs()
    .then(items=>{if(current)setJobs(items)})
    .catch(()=>{if(current)setFailed(true)})
    .finally(()=>{if(current)setLoading(false)});return()=>{current=false}},[]);

  if(loading)return <div className="center-loading" aria-live="polite"><Spin size="large" description="正在读取已发布企业岗位"/></div>;
  if(failed)return <Result status="error" title="企业岗位加载失败" subTitle="请稍后刷新页面重试。"/>;

  return <div className="published-jobs-page">
    <header className="published-jobs-heading">
      <div><Typography.Title level={2}>企业岗位</Typography.Title><Typography.Paragraph>浏览企业正式发布且当前开放的招聘岗位。</Typography.Paragraph></div>
    </header>
    {jobs.length?<div className="published-job-list">{jobs.map(job=><article className="published-job-row" key={job.enterprise_job_id}>
      <div className="published-job-main"><div className="published-job-title"><Typography.Title level={3}>{job.title}</Typography.Title><Tag color="success">已发布 · 招聘中</Tag></div><Typography.Text strong>{job.enterprise_name}</Typography.Text><div className="published-job-meta"><span><EnvironmentOutlined/>{job.location||'地点待定'}</span><span>{employmentCopy[job.employment_type||'']||'用工类型待定'}</span><span>{salary(job)}</span><span>招聘 {job.headcount} 人</span></div></div>
      <Button type="link"><Link to={`/jobs/${encodeURIComponent(job.enterprise_job_id)}`}>查看详情 <RightOutlined/></Link></Button>
    </article>)}</div>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无已发布企业岗位"/>}
    <footer className="published-job-prerequisite"><FileDoneOutlined/><div><Typography.Text strong>投递前置条件</Typography.Text><Typography.Paragraph>投递时需选择归属于你的简历，并且该简历已完成确认、生成可用的验证快照。</Typography.Paragraph></div><Link to="/profile/resumes">检查我的简历</Link></footer>
  </div>;
}
