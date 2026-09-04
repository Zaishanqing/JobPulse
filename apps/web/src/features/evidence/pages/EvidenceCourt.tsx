import {useCallback,useEffect,useState} from 'react';
import {Card,Descriptions,Empty,Select,Space,Tag,Typography} from 'antd';
import {Link} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {domainText,statusText} from '../../../shared/idText';
import {Failure,ToastAlert as Alert,WorkbenchState,type LoadState} from '../../../shared/components/States';
import {resolveEvidenceReference} from '../../demo/resultRoute';
import {getMatchEvaluation,listMatchEvaluations,listMyResumes} from '../../matching/api';
import type {Evidence,EvaluationReport,MatchReference} from '../../matching/types';
import {listPublishedPositions} from '../../positions/api';
import {groupEvaluationEvidence} from '../lib/groupEvaluationEvidence';

const spanText=(start:number|null,end:number|null)=>start===null||start===undefined?'区间未返回':`${start}-${end}`;
type PositionSummary={name:string;category_code:string};

function EvidenceRow({evidence}:{evidence:Evidence}){
  const route=resolveEvidenceReference(evidence);
  const sourceLabels:Record<string,string>={validated_cv_snapshot:'候选人简历',position_profile:'岗位能力要求',matching_evidence:'匹配分析',source_jd:'岗位证据'};
  return <div className="match-evidence-item">
    <blockquote>{evidence.quote}</blockquote>
    <Typography.Text type="secondary">
      {sourceLabels[evidence.source_object_type]||'业务证据'} · 原文区间 {spanText(evidence.start,evidence.end)} · {evidence.alignment==='exact'?'精确匹配':'已关联'}
    </Typography.Text>
    <Typography.Text type="secondary">{evidence.version?'证据版本已锁定':'版本信息未返回'}</Typography.Text>
    <div className="match-evidence-action">
      {route.path?<Link to={route.path}>查看原文</Link>:<Typography.Text type="secondary">{route.reason}</Typography.Text>}
    </div>
  </div>;
}

function EvidenceColumn({title,items,empty}:{title:string;items:Evidence[];empty:string}){
  return <Card className="profile" title={`${title}（${items.length}）`}>
    {items.length?items.map((item,index)=><EvidenceRow key={`${item.result_reference}-${index}`} evidence={item}/>):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={empty}/>}
  </Card>;
}

export function EvidenceCourt(){
  const [references,setReferences]=useState<LoadState<MatchReference[]>>({kind:'loading'});
  const [nameMaps,setNameMaps]=useState<{positions:Map<string,PositionSummary>;resumes:Map<string,string>}>({positions:new Map(),resumes:new Map()});
  const [selected,setSelected]=useState<string>();
  const [report,setReport]=useState<EvaluationReport>();
  const [error,setError]=useState<ApiError>();

  useEffect(()=>{
    listMatchEvaluations()
      .then(data=>setReferences({kind:'success',data}))
      .catch((reason:ApiError)=>setReferences({kind:'error',message:reason.message,status:reason.status}));
    // 名称映射加载失败不阻塞主流程,回退为短 ID。
    Promise.allSettled([listPublishedPositions(),listMyResumes()]).then(([positions,resumes])=>{
      const positionEntries: Array<[string,PositionSummary]>=positions.status==='fulfilled'
        ?positions.value.map(item=>[item.position_id,{name:item.name,category_code:item.category_code}])
        :[];
      setNameMaps({
        positions:new Map(positionEntries),
        resumes:new Map(resumes.status==='fulfilled'?resumes.value.map(item=>[item.resume_id,item.display_name]):[]),
      });
    });
  },[]);

  const loadReport=useCallback((evaluationId:string)=>{
    setSelected(evaluationId);
    setError(undefined);
    setReport(undefined);
    getMatchEvaluation(evaluationId)
      .then(setReport)
      .catch((reason:ApiError)=>setError(reason));
  },[]);

  const optionLabel=(item:MatchReference)=>{
    const rawPosition=item.position_id?.replace(/^enterprise_job:/,'')??null;
    const position=rawPosition?nameMaps.positions.get(rawPosition):undefined;
    const positionName=position?.name||null;
    const resumeName=(item.resume_id&&nameMaps.resumes.get(item.resume_id))||null;
    const time=item.created_at?new Date(item.created_at).toLocaleString('zh-CN'):'时间未知';
    const title=positionName&&resumeName?`${positionName} × ${resumeName}`:`匹配报告 · ${time}`;
    const sub=[
      positionName?null:rawPosition?'岗位名称未返回':null,
      position?`岗位领域：${domainText(position.category_code)}`:null,
      resumeName?null:item.resume_id?'简历名称未返回':null,
      statusText(item.status),
      positionName&&resumeName?time:null,
    ].filter(Boolean).join(' · ');
    return {title,sub,searchText:`${positionName??''} ${position?domainText(position.category_code):''} ${resumeName??''} ${time} ${rawPosition??''} ${item.resume_id??''}`};
  };

  const groups=report?groupEvaluationEvidence(report):undefined;
  const final=report?.evaluation.final_match_result;

  return <div className="page">
    <div className="page-heading">
      <Typography.Title level={2}>证据法庭</Typography.Title>
      <Typography.Paragraph type="secondary">按支持、岗位依据、差距、未映射来源分组查看匹配评估的证据。</Typography.Paragraph>
    </div>
    <WorkbenchState title="匹配报告" state={references} retry={()=>window.location.reload()} render={items=>
      <Select
        style={{minWidth:380}}
        showSearch
        placeholder="选择匹配报告"
        value={selected}
        onChange={loadReport}
        filterOption={(input,option)=>String((option as {searchText?:string}|undefined)?.searchText??'').toLowerCase().includes(input.toLowerCase())}
        options={items.filter(item=>item.evaluation_id).map(item=>{const {title,sub,searchText}=optionLabel(item);return {
          value:item.evaluation_id!,
          searchText,
          label:<span className="court-option"><span>{title}</span><small>{sub}</small></span>,
        }})}
      />
    }/>
    {error&&<Failure message={error.message} status={error.status}/>}
    {report&&<>
      <Card className="profile" title="判决摘要">
        <Descriptions size="small" column={{xs:1,md:3}} items={[
          {key:'score',label:'正式总分',children:final?.overall_score??'缺失'},
          {key:'recommendation',label:'推荐等级',children:final?.recommendation_level||'缺失'},
          {key:'gate',label:'硬性门槛',children:final?.hard_gate_status||'缺失'},
          {key:'algorithm',label:'算法版本',children:report.versions.evaluation_algorithm_version||report.evaluation.algorithm_version||'缺失'},
          {key:'graph',label:'岗位图谱版本',children:report.versions.position_graph_version||'缺失'},
          {key:'state',label:'结果状态',children:report.stale?<Tag color="warning">已过期</Tag>:<Tag color="success">当前有效</Tag>},
        ]}/>
        {report.stale&&<Alert type="warning" showIcon title="这份报告需要重新计算" description="简历、岗位要求或评分方法已经更新。当前内容仅供参考，请重新匹配。"/>}
      </Card>
      <Space direction="vertical" className="full" size={16}>
        <EvidenceColumn title="支持证据（候选人侧）" items={groups?.candidate||[]} empty="暂无候选人证据"/>
        <EvidenceColumn title="岗位要求依据" items={groups?.position||[]} empty="暂无岗位证据"/>
        <EvidenceColumn title="差距 / 反方证据" items={groups?.gap||[]} empty="暂无差距证据"/>
        {groups&&groups.unresolved.length>0&&<EvidenceColumn title="未映射来源" items={groups.unresolved} empty="无"/>}
      </Space>
    </>}
  </div>;
}
