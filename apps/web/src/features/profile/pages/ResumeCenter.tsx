import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {
  App,
  Button,
  Collapse,
  Empty,
  Input,
  Popconfirm,
  Progress,
  Spin,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {
  CheckCircleOutlined,
  CommentOutlined,
  DeleteOutlined,
  EditOutlined,
  FileAddOutlined,
  FileTextOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SwapRightOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {EvidenceDeepLinkFocus,useDirectEvidenceLocation} from '../../rag/EvidenceDeepLink';
import {CVReviewPanel} from '../components/CVReviewPanel';
import {ExperienceDrawer} from '../components/ExperienceDrawer';
import type {ExperienceSectionKey} from '../components/ExperienceDrawer';
import {useSmoothPercent} from './useSmoothPercent';
import {
  confirmCVExtraction,
  deleteResume,
  getCVExtractionReview,
  getCVExtractionTask,
  getCVSourcePreview,
  getResumeParseResult,
  getResumeSkillProfile,
  listMatchEvaluations,
  listMyResumes,
  renameResume,
  uploadSourceCV,
} from '../../matching/api';
import type {
  CVConfirmPayload,
  CVExtractionTask,
  CVFieldDecisionValue,
  CVReview,
  CVReviewDecisionDraft,
  MatchReference,
  ResumeParseResult,
  ResumeRecord,
  ResumeSkill,
} from '../../matching/types';

const dateLabel=(value:string|null)=>value
  ?new Intl.DateTimeFormat('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(value))
  :'日期未知';

const statusMeta=(resume:ResumeRecord)=>{
  if(resume.input_extraction_status==='failed'||resume.parse_status==='failed'){
    return {label:'处理失败',color:'error' as const};
  }
  if(resume.validated_cv_snapshot_id)return {label:'证据已验证',color:'success' as const};
  if(resume.parse_status==='completed')return {label:'已解析',color:'warning' as const};
  return {label:'待处理',color:'default' as const};
};

const cvStageLabel:Record<CVExtractionTask['processing_stage'],string>={
  queued:'等待后台 Worker',
  ocr_running:'正在识别图片文字',
  extracting:'正在抽取结构化字段',
  contract_validating:'正在校验抽取契约',
  semantic_repairing:'正在修复语义字段',
  review_pending:'等待人工审核',
  failed:'处理失败',
  succeeded:'已确认',
};

/** 任务失败后后台队列会自动重试；只有重试耗尽（retryable=false）才算终态失败。 */
const isAutoRetrying=(task:CVExtractionTask)=>task.status==='failed'&&task.retryable;

const hasEncodingDamage=(value:string)=>{
  const replacementCount=(value.match(/[?�]/g)||[]).length;
  return replacementCount>=4&&replacementCount/Math.max(value.length,1)>.04;
};

const sourceLabel=(resume:ResumeRecord)=>{
  if(resume.original_filename)return `原始文件：${resume.original_filename}`;
  if(resume.source_type==='text')return '粘贴文本导入 · 未关联原始文件';
  return `${resume.source_type||'未知'}来源 · 未关联原始文件`;
};

const overviewLabel=(item:Record<string,unknown>)=>{
  const preferredKeys=['project_name','company','organization','school','name','title','role','degree','description'];
  for(const key of preferredKeys){
    const value=item[key];
    if(typeof value==='string'&&value.trim())return value.trim();
  }
  return '';
};

const metadataString=(metadata:Record<string,unknown>|null,key:string)=>typeof metadata?.[key]==='string'?metadata[key] as string:null;

// confidence 表示抽取与归一化的可信度，不能当作候选人的技能熟练度。
// 这里只展示原文明确支持的受控熟练度；没有程度词时如实标为未说明。
const proficiencyMeta=(value:string|null)=>({
  know:{label:'了解',color:'default'},
  familiar:{label:'熟悉',color:'blue'},
  proficient:{label:'熟练掌握',color:'green'},
  expert:{label:'精通',color:'gold'},
  unknown:{label:'程度未说明',color:'default'},
}[value??'unknown']??{label:'程度未说明',color:'default'});

const sectionLabels:Record<string,string>={
  identity_history:'基本信息与任职经历',
  projects:'项目经历',
  skills_credentials:'技能与资质',
  research_summary:'研究成果与个人总结',
};

const progressDetailOf=(task:CVExtractionTask|undefined)=>{
  const value=task?.execution_metadata?.progress_detail;
  return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:undefined;
};

const draftForField=(field:CVReview['reviewable_fields'][number]):CVReviewDecisionDraft=>({
  decision:'',
  corrected_value:null,
  correction_reason:null,
  evidence_quote:field.evidence?.quote??null,
  evidence_start:field.evidence?.start??null,
  evidence_end:field.evidence?.end??null,
});

function buildConfirmPayload(
  task:CVExtractionTask,
  review:CVReview,
  decisions:Record<string,CVReviewDecisionDraft|undefined>,
):CVConfirmPayload{
  const execution=task.execution_metadata??{};
  return {
    expected_review_id:review.review_id||'',
    idempotency_key:`confirm-${task.task_id}`,
    field_decisions:review.reviewable_fields.flatMap(field=>{
      const draft=decisions[field.field_id];
      if(!draft||!draft.decision)return [];
      return [{
        field_id:field.field_id,
        field_type:field.field_type,
        section:field.section,
        item_id:field.item_id,
        field_path:field.field_path,
        decision:draft.decision as CVFieldDecisionValue,
        corrected_value:draft.corrected_value,
        correction_reason:draft.correction_reason,
        evidence_quote:draft.evidence_quote,
        evidence_start:draft.evidence_start,
        evidence_end:draft.evidence_end,
      }];
    }),
    normalization_version:metadataString(execution,'normalization_version'),
    taxonomy_version:metadataString(execution,'taxonomy_version'),
    display_name:'智能抽取简历',
  };
}

export function ResumeCenter(){
  const {message}=App.useApp();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const routeResumeId=searchParams.get('resumeId')||undefined;
  const routeCvTaskId=searchParams.get('cvTaskId')||undefined;
  const [resumes,setResumes]=useState<ResumeRecord[]>([]);
  const [reports,setReports]=useState<MatchReference[]>([]);
  const [selectedId,setSelectedId]=useState<string|undefined>(routeResumeId);
  const [parseResult,setParseResult]=useState<ResumeParseResult>();
  const [skills,setSkills]=useState<ResumeSkill[]>([]);
  const [editing,setEditing]=useState(false);
  const [draftName,setDraftName]=useState('');
  const [loading,setLoading]=useState(true);
  const [working,setWorking]=useState(false);
  const [error,setError]=useState<ApiError>();
  const [cvTask,setCvTask]=useState<CVExtractionTask>();
  const [cvReview,setCvReview]=useState<CVReview>();
  const [cvSourcePreview,setCvSourcePreview]=useState<string>();
  const [cvDecisions,setCvDecisions]=useState<Record<string,CVReviewDecisionDraft|undefined>>({});
  const [cvTaskLoading,setCvTaskLoading]=useState(false);
  const [drawerSection,setDrawerSection]=useState<ExperienceSectionKey|null>(null);
  const [cvUploadWorking,setCvUploadWorking]=useState(false);
  const [cvProgressResetToken,setCvProgressResetToken]=useState(0);
  const [cvConfirmWorking,setCvConfirmWorking]=useState(false);
  const [cvPollExhausted,setCvPollExhausted]=useState(false);
  const pollVersion=useRef(0);
  const directEvidence=useDirectEvidenceLocation();

  const load=useCallback(async(preferredId?:string)=>{
    setLoading(true);setError(undefined);
    try{
      const [resumeValues,reportValues]=await Promise.all([listMyResumes(),listMatchEvaluations()]);
      setResumes(resumeValues);setReports(reportValues);
      setSelectedId(current=>{
        const next=preferredId||current;
        return resumeValues.some(item=>item.resume_id===next)?next:resumeValues[0]?.resume_id;
      });
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[]);

  useEffect(()=>{
    const timer=window.setTimeout(()=>void load(routeResumeId),0);
    return()=>window.clearTimeout(timer);
  },[load,routeResumeId]);

  const loadDetail=useCallback(async(resumeId:string)=>{
    const [parse,profile]=await Promise.allSettled([
      getResumeParseResult(resumeId),
      getResumeSkillProfile(resumeId),
    ]);
    setParseResult(parse.status==='fulfilled'?parse.value:undefined);
    setSkills(profile.status==='fulfilled'?profile.value.skills:[]);
  },[]);

  useEffect(()=>{
    if(!selectedId)return;
    const timer=window.setTimeout(()=>void loadDetail(selectedId),0);
    return()=>window.clearTimeout(timer);
  },[loadDetail,selectedId]);

  const selected=resumes.find(item=>item.resume_id===selectedId);
  const focusedSourceText=useMemo(()=>{
    if(!selected||!directEvidence)return null;
    const {start,end,quote}=directEvidence;
    const exact=selected.raw_text.slice(start,end);
    if(!exact||(quote!=null&&exact!==quote))return null;
    return {
      before:selected.raw_text.slice(0,start),
      exact,
      after:selected.raw_text.slice(end),
    };
  },[directEvidence,selected]);
  const selectedReports=useMemo(
    ()=>reports.filter(item=>item.resume_id===selectedId),
    [reports,selectedId],
  );

  useEffect(()=>{
    if(!focusedSourceText)return;
    const timer=window.setTimeout(()=>{
      const target=document.querySelector<HTMLElement>('[data-source-evidence-focus]');
      target?.scrollIntoView({block:'center',behavior:'smooth'});
      target?.focus({preventScroll:true});
    },0);
    return()=>window.clearTimeout(timer);
  },[focusedSourceText]);

  const loadReview=useCallback(async(taskId:string)=>{
    const review=await getCVExtractionReview(taskId);
    setCvReview(review);
    setCvDecisions(Object.fromEntries(review.reviewable_fields.map(field=>[field.field_id,draftForField(field)])));
  },[]);

  useEffect(()=>{
    let disposed=false;
    let objectUrl:string|undefined;
    const resetId=requestAnimationFrame(()=>{
      setCvSourcePreview(undefined);
      if(!cvReview?.source_file_id)return;
      void getCVSourcePreview(cvReview.source_file_id).then(blob=>{
        if(disposed)return;
        objectUrl=URL.createObjectURL(blob);
        setCvSourcePreview(objectUrl);
      }).catch(()=>setCvSourcePreview(undefined));
    });
    return()=>{
      disposed=true;
      if(objectUrl)URL.revokeObjectURL(objectUrl);
      cancelAnimationFrame(resetId);
    };
  },[cvReview?.source_file_id]);

  const pollCVTask=useCallback(async(taskId:string)=>{
    const generation=pollVersion.current+1;
    pollVersion.current=generation;
    setCvPollExhausted(false);
    // 端到端最坏周期 = 300s 读取超时 × 3 次尝试 + 重试间隔，窗口按 20 分钟覆盖。
    // 自动重试中的 failed 是中间态而非终态；单次轮询失败多为网络抖动，不中断跟踪。
    let consecutiveFetchErrors=0;
    for(let attempt=0;attempt<1600;attempt+=1){
      await new Promise(resolve=>window.setTimeout(resolve,750));
      if(pollVersion.current!==generation)return;
      let task:CVExtractionTask;
      try{
        task=await getCVExtractionTask(taskId);
        consecutiveFetchErrors=0;
      }catch{
        consecutiveFetchErrors+=1;
        if(consecutiveFetchErrors<20)continue;
        break;
      }
      if(pollVersion.current!==generation)return;
      setCvTask(task);
      if(task.status==='succeeded'){
        await loadReview(taskId);
        return;
      }
      if(task.status==='failed'&&!task.retryable)return;
    }
    if(pollVersion.current===generation)setCvPollExhausted(true);
  },[loadReview]);

  const loadCVTask=useCallback(async(taskId:string)=>{
    setCvReview(undefined);setCvDecisions({});
    setCvTaskLoading(true);setError(undefined);
    try{
      const task=await getCVExtractionTask(taskId);
      setCvTask(task);
      if(task.status==='succeeded'){
        await loadReview(taskId);
      }else if(task.status==='pending'||task.status==='running'||isAutoRetrying(task)){
        await pollCVTask(taskId);
      }
    }catch(reason){setError(reason as ApiError)}
    finally{setCvTaskLoading(false)}
  },[loadReview,pollCVTask]);

  useEffect(()=>{
    if(!routeCvTaskId)return;
    const timer=window.setTimeout(()=>void loadCVTask(routeCvTaskId),0);
    return()=>window.clearTimeout(timer);
  },[loadCVTask,routeCvTaskId]);

  const handleUpload=async(file:File)=>{
    if(!file)return;
    setCvProgressResetToken(current=>current+1);
    setCvUploadWorking(true);setError(undefined);
    setCvTask(undefined);setCvReview(undefined);setCvDecisions({});setCvPollExhausted(false);
    try{
      const created=await uploadSourceCV(file);
      navigate(`/profile/resumes?cvTaskId=${encodeURIComponent(created.cv_extraction_task_id)}`);
    }catch(reason){setError(reason as ApiError)}
    finally{setCvUploadWorking(false)}
  };

  const handleConfirm=async()=>{
    if(!cvTask||!cvReview)return;
    setCvConfirmWorking(true);setError(undefined);
    try{
      const confirmed=await confirmCVExtraction(cvReview.task_id,buildConfirmPayload(cvTask,cvReview,cvDecisions));
      const refreshed=await listMyResumes();
      setResumes(refreshed);
      setSelectedId(confirmed.resume_id);
      await loadDetail(confirmed.resume_id);
      navigate(`/profile/resumes?resumeId=${encodeURIComponent(confirmed.resume_id)}`);
      setCvTask(undefined);setCvReview(undefined);setCvDecisions({});setCvPollExhausted(false);
      message.success('简历快照已确认并生成派生投影');
    }catch(reason){
      const err=reason as ApiError;
      if(err.status===409){
        setCvDecisions({});
        if(cvReview)await loadReview(cvReview.task_id);
        message.warning('确认指纹或载荷已变化，请重新审核');
      }else{
        setError(err);
      }
    }finally{setCvConfirmWorking(false)}
  };

  const saveName=async()=>{
    if(!selected||!draftName.trim())return;
    setWorking(true);setError(undefined);
    try{
      const updated=await renameResume(selected.resume_id,draftName);
      setResumes(items=>items.map(item=>item.resume_id===updated.resume_id?updated:item));
      setEditing(false);
      message.success('简历名称已更新');
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking(false)}
  };

  const remove=async()=>{
    if(!selected)return;
    setWorking(true);setError(undefined);
    try{
      await deleteResume(selected.resume_id);
      await load();
      message.success('简历已删除');
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking(false)}
  };

  const refreshCVTask=()=>{
    if(cvTask)void loadCVTask(cvTask.task_id);
  };

  const cvProgress=cvUploadWorking
    ?{percent:14,label:cvStageLabel.ocr_running,step:'正在读取文件并生成可抽取文本',details:[] as Array<{label:string;state:string}>}
    :cvTask?.status==='pending'
      ?{percent:20,label:cvStageLabel.queued,step:'任务已进入后台队列',details:[] as Array<{label:string;state:string}>}
    :cvTask?.status==='running'
        ?(()=>{
          const detail=progressDetailOf(cvTask);
          const percent=typeof detail?.percent==='number'?Math.round(detail.percent*100):28;
          const completed=new Set(Array.isArray(detail?.completed_sections)?detail.completed_sections.filter((item):item is string=>typeof item==='string'):[]);
          const active=new Set(Array.isArray(detail?.active_sections)?detail.active_sections.filter((item):item is string=>typeof item==='string'):[]);
          const stage=typeof detail?.stage==='string'?detail.stage:'preparing';
          const stageText:Record<string,string>={
            preparing:'正在准备文本与技能词表',
            extracting_sections:'正在并行抽取简历内容',
            semantic_validating:'正在校验字段语义并修复异常',
            contract_validating:'正在校验证据与数据契约',
            position_classifying:'正在识别候选岗位方向',
            completed:'抽取完成',
          };
          return {
            percent,
            label:stageText[stage]||cvStageLabel[cvTask.processing_stage],
            step:stage==='extracting_sections'?`已完成 ${completed.size} / ${Object.keys(sectionLabels).length} 个内容分段`:stageText[stage]||'正在处理',
            details:Object.entries(sectionLabels).map(([key,label])=>({
              label,
              state:completed.has(key)?'已完成':active.has(key)?'抽取中':'等待',
            })),
          };
        })()
        :cvTask&&isAutoRetrying(cvTask)
          ?{
            percent:24,
            label:'正在自动重试',
            step:`第 ${cvTask.attempt_count} / ${cvTask.max_attempts} 次尝试未成功，系统已自动排队下一次尝试`,
            details:[] as Array<{label:string;state:string}>,
          }
        :cvTaskLoading&&cvTask?.status==='succeeded'&&!cvReview
          ?{percent:100,label:'抽取完成，正在加载审核字段',step:'正在生成审核视图',details:[] as Array<{label:string;state:string}>}
          :undefined;
  const displayedCVPercent=useSmoothPercent(cvProgress?.percent,cvProgressResetToken);

  return <div className="resume-center-page">
    <EvidenceDeepLinkFocus resourceId={selectedId}/>
    <div className="page-heading resume-page-heading">
      <Typography.Title level={2}>我的简历</Typography.Title>
      <Typography.Paragraph type="secondary">上传简历并查看解析结果，核对字段证据后生成正式简历快照。</Typography.Paragraph>
    </div>

    {cvProgress&&<div className="resume-upload-progress" role="status" aria-live="polite">
      <div>
        <Typography.Text strong>{cvProgress.label}</Typography.Text>
        <Typography.Text type="secondary">{cvProgress.step} · {displayedCVPercent}%</Typography.Text>
      </div>
      <Progress percent={displayedCVPercent} status="active" showInfo={false}/>
      {cvProgress.details.length>0&&<div className="resume-upload-progress-details">
        {cvProgress.details.map(item=><span key={item.label} data-state={item.state}>
          <i/>{item.label}<small>{item.state}</small>
        </span>)}
      </div>}
    </div>}

    {error&&<Failure
      message={error.message}
      status={error.status}
      retry={()=>void load(selectedId)}
    />}

    {cvTask&&!cvTaskLoading&&cvTask.status==='failed'&&!cvTask.retryable&&<Alert
      type="error"
      showIcon
      title="CV 抽取失败"
      description={cvTask.last_error_message||'任务失败但未返回错误消息，请重新上传简历。'}
    />}
    {cvPollExhausted&&<Alert
      type="info"
      title="任务仍在处理中"
      description="已持续刷新 20 分钟。任务仍在后台执行，不会判定失败，可稍后刷新查看最新结果。"
      action={<Button icon={<ReloadOutlined/>} onClick={refreshCVTask}>刷新状态</Button>}
    />}

    {cvReview&&cvTask&&<CVReviewPanel
      task={cvTask}
      review={cvReview}
      sourcePreviewUrl={cvSourcePreview}
      decisions={cvDecisions}
      disabled={cvConfirmWorking}
      onDecisionChange={(fieldId,update)=>setCvDecisions(current=>{
        const existing=current[fieldId]??{
          decision:'',corrected_value:null,correction_reason:null,
          evidence_quote:null,evidence_start:null,evidence_end:null,
        };
        return {...current,[fieldId]:{...existing,...update}};
      })}
      onConfirm={()=>void handleConfirm()}
    />}

    {loading?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>:resumes.length===0?(
      // 已有 CV 任务在上传/抽取/审核时，不再重复展示"还没有简历"空状态。
      !cvTask&&!cvTaskLoading&&!cvUploadWorking&&<div className="resume-empty">
        <FileAddOutlined/>
        <Typography.Title level={3}>还没有简历</Typography.Title>
        <Typography.Paragraph>上传简历后，系统会提取内容、校验字段并生成验证快照。</Typography.Paragraph>
        <Upload
          accept=".pdf,.docx,.txt,.png,.jpg,.jpeg"
          showUploadList={false}
          beforeUpload={file=>{void handleUpload(file);return false}}
        >
          <Button type="primary" size="large" icon={<UploadOutlined/>}>上传简历</Button>
        </Upload>
      </div>
    ):(
      <>
      <div className="resume-center-layout">
        <aside className="resume-library">
          <div className="resume-library-head">
            <div className="resume-library-head-title">
              <Typography.Text strong>简历库</Typography.Text>
              <Tag>{resumes.length}</Tag>
            </div>
            <Upload
              accept=".pdf,.docx,.txt,.png,.jpg,.jpeg"
              showUploadList={false}
              beforeUpload={file=>{void handleUpload(file);return false}}
            >
              <Button type="primary" size="small" icon={<UploadOutlined/>} loading={cvUploadWorking}>上传简历</Button>
            </Upload>
          </div>
          <div className="resume-library-list">
            {resumes.map(item=>{
              const status=statusMeta(item);
              return <button
                key={item.resume_id}
                className={item.resume_id===selectedId?'is-selected':''}
                onClick={()=>setSelectedId(item.resume_id)}
              >
                <FileTextOutlined/>
                <span>
                  <strong>{item.display_name}</strong>
                  <small>{item.original_filename||`${item.source_type==='text'?'文本':'上传'}来源`} · {dateLabel(item.created_at)}</small>
                </span>
                <Tag color={status.color}>{status.label}</Tag>
              </button>;
            })}
          </div>
        </aside>

        {selected&&<main className="resume-detail">
          <header className="resume-detail-head">
            <div>
              {editing?(
                <div className="resume-name-editor">
                  <Input
                    autoFocus
                    maxLength={120}
                    value={draftName}
                    onChange={event=>setDraftName(event.target.value)}
                    onPressEnter={()=>void saveName()}
                  />
                  <Button type="primary" loading={working} onClick={()=>void saveName()}>保存</Button>
                  <Button onClick={()=>setEditing(false)}>取消</Button>
                </div>
              ):(
                <div className="resume-title-line">
                  <Typography.Title level={3}>{selected.display_name}</Typography.Title>
                  <Button
                    type="text"
                    icon={<EditOutlined/>}
                    aria-label="重命名简历"
                    onClick={()=>{setDraftName(selected.display_name);setEditing(true)}}
                  />
                </div>
              )}
              <Typography.Text type="secondary">
                {selected.original_filename||'文本导入'} · 更新于 {dateLabel(selected.updated_at)}
              </Typography.Text>
            </div>
            <div className="resume-detail-actions">
              {selected.validated_cv_snapshot_id&&<Button
                icon={<CommentOutlined/>}
                onClick={()=>navigate(`/evidence/assistant?${new URLSearchParams({
                  objectType:'cv_profile',
                  objectId:selected.resume_id,
                  objectVersion:selected.validated_cv_snapshot_id||'',
                  versionKind:'business_version',
                  evidenceTypes:'cv_evidence,matching_evidence',
                  returnTo:`/profile/resumes?resumeId=${encodeURIComponent(selected.resume_id)}`,
                })}`)}
              >
                证据问答
              </Button>}
              <Button
                type="primary"
                icon={<SwapRightOutlined/>}
                onClick={()=>navigate(`/matching?resumeId=${encodeURIComponent(selected.resume_id)}`)}
              >
                进入岗位匹配
              </Button>
              <Popconfirm
                title="删除这份简历？"
                description={selectedReports.length?`将一并删除 ${selectedReports.length} 份匹配报告记录，删除后无法恢复。`:'删除后无法恢复。'}
                okText="删除"
                cancelText="取消"
                okButtonProps={{danger:true}}
                onConfirm={()=>void remove()}
              >
                <Button danger icon={<DeleteOutlined/>}>删除</Button>
              </Popconfirm>
            </div>
          </header>

          <section className="resume-trust-strip">
            <div><SafetyCertificateOutlined/><span>验证快照</span><strong>{selected.validated_cv_snapshot_id?'已生成':'尚未生成'}</strong></div>
            <div><CheckCircleOutlined/><span>解析状态</span><strong>{statusMeta(selected).label}</strong></div>
            <div><FileTextOutlined/><span>历史匹配</span><strong>{selectedReports.length} 份报告</strong></div>
          </section>

          {selected.input_error_message&&<Alert type="error" showIcon title="简历处理失败" description={selected.input_error_message}/>}

          <div className="resume-detail-grid">
            <Collapse className="resume-compact-collapse" items={[{
              key:'skills',
              label:<div className="resume-collapse-label">
                <span><strong>技能画像</strong><small>{skills.length} 项规范化技能 · 点击展开</small></span>
                {parseResult&&<Tag color={parseResult.need_review?'warning':'success'}>{parseResult.need_review?'待人工确认':'已确认'}</Tag>}
              </div>,
              children:skills.length?(
                <div className="resume-collapsible-scroll resume-skill-evidence">
                  {skills.map(skill=>{
                    const proficiency=proficiencyMeta(skill.proficiency);
                    return <div key={skill.resume_skill_id}>
                      <span><strong>{skill.raw_skill}</strong><small>{skill.evidence||'暂无证据片段'}</small></span>
                      <Tag color={proficiency.color}>{proficiency.label}</Tag>
                    </div>;
                  })}
                </div>
              ):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未生成技能画像"/>,
            }]}/>

            <Collapse className="resume-compact-collapse" items={[{
              key:'reports',
              label:<div className="resume-collapse-label">
                <span><strong>当前简历的匹配记录</strong><small>按当前简历关联 · {selectedReports.length} 份报告 · 点击展开</small></span>
              </div>,
              children:selectedReports.length?(
                <div className="resume-collapsible-scroll resume-report-list">
                  {selectedReports.slice().reverse().map(item=><button
                    key={item.evaluation_id||item.task_id}
                    onClick={()=>navigate(`/matching/reports/${encodeURIComponent(item.evaluation_id||'')}`)}
                  >
                    <span><strong>岗位匹配报告</strong><small>{dateLabel(item.created_at)} · {item.status}</small></span>
                    <b>查看</b>
                  </button>)}
                </div>
              ):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前简历还没有匹配记录"/>,
            }]}/>
          </div>

          <section className="resume-source-preview">
            <div className="resume-section-head">
              <div>
                <Typography.Title level={4}>原始简历概览</Typography.Title>
                <Typography.Text type="secondary">{sourceLabel(selected)}</Typography.Text>
              </div>
            </div>
            {hasEncodingDamage(selected.raw_text)&&(
              <Alert
                type="warning"
                showIcon
                title="源文本在导入前已损坏"
                description="以下概览仅展示验证快照中仍可恢复的信息；不可变源记录保持原样。"
              />
            )}
            <div className="resume-overview">
              <div>
                <Typography.Text strong>已恢复技能</Typography.Text>
                <div className="resume-overview-tags">
                  {(parseResult?.skills.length?parseResult.skills.map(item=>item.raw_skill):skills.map(item=>item.raw_skill))
                    .filter((value,index,items)=>value&&items.indexOf(value)===index)
                    .map(value=><Tag key={value}>{value}</Tag>)}
                  {!parseResult?.skills.length&&!skills.length&&<Typography.Text type="secondary">未恢复技能信息</Typography.Text>}
                </div>
              </div>
              <div>
                <Typography.Text strong>经历概览</Typography.Text>
                <div className="resume-overview-list">
                  {([
                    ['项目','projects',parseResult?.projects||[],false],
                    ['工作 / 实习','internships',parseResult?.internships||[],false],
                    ['教育经历','education',parseResult?.education||[],true],
                  ] as Array<[string,ExperienceSectionKey,Array<Record<string,unknown>>,boolean]>).map(([label,key,items,isEducation])=>(
                    <button
                      key={key}
                      type="button"
                      aria-label={`查看与编辑${label}`}
                      onClick={()=>setDrawerSection(key)}
                    >
                      <span>{label}</span>
                      <strong>{isEducation&&!items.length?'未提取':items.length}</strong>
                      {items.length>0
                        ?<small>{items.map(overviewLabel).filter(Boolean).slice(0,2).join('、')}</small>
                        :isEducation&&<small>结构化快照未返回教育条目</small>}
                      <em>查看与编辑</em>
                    </button>
                  ))}
                </div>
                {!parseResult?.projects.length&&!parseResult?.internships.length&&!parseResult?.education.length&&(
                  <Typography.Text type="secondary">未从当前快照恢复项目、工作或教育经历</Typography.Text>
                )}
              </div>
            </div>
            {!hasEncodingDamage(selected.raw_text)&&(
              <div className={`resume-original-text${focusedSourceText?' is-evidence-focused':''}`}>
                <Typography.Text strong>原始文本</Typography.Text>
                <p>{focusedSourceText?<>{focusedSourceText.before}<mark data-source-evidence-focus tabIndex={-1}>{focusedSourceText.exact}</mark>{focusedSourceText.after}</>:selected.raw_text||'当前简历没有可展示的文本内容。'}</p>
              </div>
            )}
          </section>
        </main>}
      </div>
      </>
    )}

    <ExperienceDrawer
      resume={selected}
      section={drawerSection}
      items={drawerSection==='projects'?parseResult?.projects||[]:drawerSection==='internships'?parseResult?.internships||[]:parseResult?.education||[]}
      onClose={()=>setDrawerSection(null)}
      onSaved={async()=>{
        if(!selectedId)return;
        try{setResumes(await listMyResumes())}catch{/* 列表刷新失败时保留现状,详情仍会重新加载 */}
        await loadDetail(selectedId);
      }}
    />
  </div>;
}
