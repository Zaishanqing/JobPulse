import {expect,test,type Page,type Route} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';

type Json=Record<string,unknown>;

const personalEvidence=JSON.parse(readFileSync(fileURLToPath(new URL(
  '../../docs/acceptance/artifacts/date-match-browser-e2e/20260807-1710-report/report-evidence.json',
  import.meta.url,
)),'utf8')) as Json;
const enterpriseCase=JSON.parse(readFileSync(fileURLToPath(new URL(
  '../../docs/acceptance/enterprise-case-v1/formal-results.json',
  import.meta.url,
)),'utf8')) as Json;
const resumeFile=fileURLToPath(new URL(
  '../../docs/acceptance/artifacts/cv-browser-e2e/20260806-233435/inputs/anonymous_resume_text.pdf',
  import.meta.url,
));

const personalPermissions=[
  'catalog.read_published','emerging.read_published','evidence.read_public','trend.published.read',
  'resume.parse.manage','resume.profile.generate','matching.run','learning_path.create',
];
const enterprisePermissions=['catalog.read_published','emerging.read_published','evidence.read_public','trend.published.read','jd.create','jd.parse'];

const ok=(route:Route,data:unknown)=>route.fulfill({
  status:200,
  contentType:'application/json',
  body:JSON.stringify({code:0,message:'success',data,details:{},trace_id:'browser-e2e'}),
});
const fail=(route:Route,status:number,message:string,errorCode?:string)=>route.fulfill({
  status,
  contentType:'application/json',
  body:JSON.stringify({code:status*100+1,message,data:null,details:errorCode?{error_code:errorCode}:{},trace_id:`browser-e2e-${status}`}),
});
const pathOf=(route:Route)=>new URL(route.request().url()).pathname;
const methodOf=(route:Route)=>route.request().method();

const lineage=personalEvidence.backend_lineage as Json;
const personalIds={
  resume:String(lineage.resume_id),
  snapshot:String(lineage.validated_cv_snapshot_id),
  position:String(lineage.position_id),
  evaluation:String(personalEvidence.evaluation_id),
  graph:String((lineage.data_versions as Json).graph),
  cvProfile:`cv-profile:${String(lineage.validated_cv_snapshot_id)}`,
  cvProfileVersion:String((lineage.data_versions as Json).cv_source),
  positionProfileVersion:String((lineage.data_versions as Json).position_source),
  algorithm:String((lineage.algorithm_versions as Json).evaluation),
  scoringConfig:String((lineage.algorithm_versions as Json).scoring_config),
};

const evidence=(kind:'candidate'|'position'|'gap',quote:string,index:number)=>{
  const candidate=kind==='candidate';
  const position=kind==='position';
  const sourceObjectType=candidate?'validated_cv_snapshot':position?'position_profile':'matching_evidence';
  const sourceObjectId=candidate?personalIds.snapshot:position?personalIds.position:personalIds.evaluation;
  const fragment=`${kind}-evidence-${index}`;
  return {
    source_object_type:sourceObjectType,
    source_object_id:sourceObjectId,
    source_document_id:sourceObjectId,
    source_fragment_id:fragment,
    quote,start:index*20,end:index*20+quote.length,alignment:'exact',occurrence_index:0,
    version:{
      validated_cv_snapshot_id:candidate?personalIds.snapshot:null,
      source_cv_version_id:candidate?'source-cv-version:personal-closeout':null,
      resume_id:candidate?personalIds.resume:null,
      position_id:position?personalIds.position:null,
      graph_version:position?personalIds.graph:null,
      source_jd_version_id:position?'source-jd-version:competition-demo-v1':null,
      evaluation_id:kind==='gap'?personalIds.evaluation:null,
    },
    result_reference:`${sourceObjectType}:${sourceObjectId}#evidence:${fragment}`,
  };
};

const candidateEvidence=[
  evidence('candidate','2024.03 - 2025.02 多格式简历解析平台 | 核心开发',0),
  evidence('candidate','实现文本 PDF、DOCX、图片和扫描 PDF 的统一解析流程,区分文本提取与 OCR 模式。',1),
  evidence('candidate','设计任务状态、错误反馈与人工确认流程,使抽取结果可复核并可继续用于岗位匹配。',2),
];
const positionEvidence=[
  evidence('position','Python 和 SQL 为必需技能',0),
  evidence('position','了解 FastAPI',1),
];
const gapSkills=['生产级 RAG 评测','向量模型训练','召回质量诊断','在线服务观测','检索数据治理','证据充分性'];

function personalReport(options:{stale?:boolean;missingEvidence?:boolean;status?:string}={}){
  const missing=Boolean(options.missingEvidence);
  const status=options.status||'completed';
  const cvEvidence=missing?[]:candidateEvidence;
  const jobEvidence=missing?[]:positionEvidence;
  const gapEvidence=missing?[]:[evidence('gap','正式评估记录显示该要求尚无充分候选人 Evidence。',0)];
  const skillResults=[{
    requirement_id:'req-python',skill_id:'skill-python',skill_name:'Python',match_status:missing?'unknown':'matched',
    position_evidence:jobEvidence,candidate_evidence:cvEvidence,reason_code:missing?'CANDIDATE_EVIDENCE_UNKNOWN':'MATCHED',confidence:missing?0:.86,
  },...gapSkills.map((name,index)=>({
    requirement_id:`req-gap-${index+1}`,skill_id:`skill-gap-${index+1}`,skill_name:name,match_status:'missing',
    position_evidence:jobEvidence.slice(0,1),candidate_evidence:[],reason_code:'REQUIRED_SKILL_NOT_OBSERVED',confidence:.8,
  }))];
  const final=status==='completed'?{
    overall_score:missing?null:Number(personalEvidence.backend_overall_score),
    match_confidence:missing ? .22 : .74,
    recommendation_level:missing?'insufficient_information':String(personalEvidence.backend_recommendation),
    hard_gate_status:missing?'uncertain':'passed',
    dimension_scores:[{dimension:'required_skills',score:22.5,confidence:.74,configured_weight:.4,effective_weight:.4,applicable_count:7,scored_count:7,uncertain_count:missing?1:0}],
    score_contributions:[],strengths:[],gaps:[],
    uncertain_items:missing?[{dimension:'required_skills',result_id:'req-python',reason_code:'CANDIDATE_EVIDENCE_UNKNOWN',message:'候选人 Evidence 不足',evidence:[]}]:[],
    explanation:'冻结 Evaluation 的只读 BFF 投影。',algorithm_version:personalIds.algorithm,
    scoring_config_version:personalIds.scoringConfig,cv_profile_id:personalIds.cvProfile,
    position_profile_id:personalIds.position,position_graph_version:personalIds.graph,
  }:null;
  return {
    evaluation_id:personalIds.evaluation,task_id:'match-task:personal-closeout',status:options.stale?'stale':'current',
    stale:Boolean(options.stale),stale_reason_codes:options.stale?['INPUT_FINGERPRINT_CHANGED']:[],
    evaluation:{
      evaluation_id:personalIds.evaluation,evaluation_status:status,
      error_code:status==='completed'?null:'MATCHING_INPUT_INCOMPLETE',
      error_message:status==='completed'?null:'正式评分输入不完整，Evaluation 被拒绝。',
      algorithm_version:personalIds.algorithm,cv_profile_id:personalIds.cvProfile,cv_profile_version:personalIds.cvProfileVersion,
      position_profile_id:personalIds.position,position_profile_version:personalIds.positionProfileVersion,
      hard_constraint_results:[],skill_results:skillResults,responsibility_results:[],project_results:[],scenario_results:[],
      summary:{hard_constraint_pass_count:0,hard_constraint_fail_count:0,required_skill_matched_count:missing?0:1,required_skill_missing_count:6,bonus_skill_matched_count:0,bonus_skill_missing_count:0,coverage_denominator_policy:'exclude_unknown_unresolved_and_not_required'},
      final_match_result:final,
    },
    gap_analysis:{
      generation_status:status==='completed'?'completed':'rejected',result_status:status==='completed'?'completed':'rejected',
      error_code:status==='completed'?null:'SOURCE_EVALUATION_REJECTED',error_message:status==='completed'?null:'来源 Evaluation 未完成。',
      prioritized_gaps:gapSkills.map((name,index)=>({
        gap_type:index===5?'evidence_gap':'required_skill_missing',requirement_id:`req-gap-${index+1}`,
        skill_id:`skill-gap-${index+1}`,current_level:null,target_level:'working',priority:index<2?'critical':'high',
        priority_score:90-index*5,reason_codes:[index===5?'EVIDENCE_INSUFFICIENT':'MISSING_SKILL'],evidence:gapEvidence,
      })),
      learning_path:status==='completed'?[{step_order:1,target_skill_id:'skill-gap-1',objective:'完成生产级 RAG 评测闭环',prerequisite_skill_ids:[],prerequisite_states:[],basis:['正式 Gap 分析'],estimated_hours:40,planning_status:'ready'}]:[],
      candidate_actions:status==='completed'?[{action_id:'action-rag-eval',action_type:'add_project_experience',skill_id:'skill-gap-1',canonical_name:'生产级 RAG 评测',target_level:'working',ownership:'owner',target_requirement_ids:['req-gap-1'],responsibilities:[],business_scenarios:['企业知识库'],path_refs:[],estimated_hours:40,stage:'project',requires_action_ids:[],supersedes_action_ids:[],cost_model:'formal-gap-action.v1',estimated_score_delta:12,estimated_utility:.8,score_effect_reason:'由正式假设分析重算决定'}]:[],
      learning_routes:[],skill_path_decisions:[],minimal_action_set:null,counterfactual_suggestions:[],algorithm_version:'deterministic-gap-path.v1',
    },
    versions:{position_graph_version:personalIds.graph,evaluation_algorithm_version:personalIds.algorithm,scoring_config_version:personalIds.scoringConfig},
    lineage:{...lineage,provider:'matching-service',method:'deterministic_explainable'},created_at:'2026-08-07T09:10:00Z',updated_at:'2026-08-07T09:10:00Z',
  };
}

const resumeRecord={
  resume_id:personalIds.resume,display_name:'Competition Demo CV',original_filename:'anonymous_resume_text.pdf',source_type:'file',file_id:'file:personal-closeout',
  raw_text:'三年软件开发经验，使用 Python、FastAPI 和 PostgreSQL 开发企业知识库服务。',parse_status:'completed',input_extraction_status:'completed',
  implementation_status:'validated_snapshot',validated_cv_snapshot_id:personalIds.snapshot,created_at:'2026-08-07T08:00:00Z',updated_at:'2026-08-07T09:00:00Z',
};
const positionRecord={position_id:personalIds.position,position_name:'AI Application Engineer',taxonomy_family_name:'技术',status:'published',lifecycle_status:'active',matchable:true,reason:'MATCHABLE',blockers:[],position_graph_version:personalIds.graph};

async function authenticate(page:Page,role:'personal_user'|'enterprise_user'){
  await page.addInitScript(()=>localStorage.setItem('main_access_token','browser-e2e-token'));
  const permissions=role==='personal_user'?personalPermissions:enterprisePermissions;
  return {user_id:`${role}:browser-e2e`,username:role,role,permissions};
}

async function installPersonalApi(page:Page,options:{confirmConflict?:boolean;delayResumes?:boolean}={}){
  const user=await authenticate(page,'personal_user');
  let confirmed=false;
  const learningPathResult={path_id:'learning-path:personal-closeout',evaluation_id:personalIds.evaluation,target_position_id:personalIds.position,time_budget_hours:40,learning_goal:'补齐生产级 RAG 评测能力',status:'completed',provider:'matching-service',stages:[],gap_analysis:{generation_status:'completed',learning_path:[{step_order:1,target_skill_id:'skill-gap-1',objective:'完成 40 小时生产级 RAG 评测闭环',prerequisite_skill_ids:[],prerequisite_states:[],basis:['正式 Gap 分析'],estimated_hours:40,planning_status:'ready'}]},algorithm_versions:{evaluation:personalIds.algorithm,learning_path:'deterministic-learning-path.v2'},data_versions:{validated_cv_snapshot_id:personalIds.snapshot,position_profile_version:personalIds.positionProfileVersion,position_graph_version:personalIds.graph},created_at:'2026-08-17T03:00:00Z',updated_at:'2026-08-17T03:00:00Z'};
  await page.route('**/api/v1/**',async route=>{
    const path=pathOf(route);
    const method=methodOf(route);
    if(path==='/api/v1/auth/me')return ok(route,user);
    if(path==='/api/v1/resumes/me'){
      if(options.delayResumes)await new Promise(resolve=>setTimeout(resolve,700));
      return ok(route,confirmed?[resumeRecord]:[]);
    }
    if(path==='/api/v1/matches/reports'&&method==='GET')return ok(route,confirmed?[{evaluation_id:personalIds.evaluation,resume_id:personalIds.resume,position_id:personalIds.position,target_id:personalIds.position,overall_score:22.5,status:'current',created_at:'2026-08-07T09:10:00Z',updated_at:'2026-08-07T09:10:00Z'}]:[]);
    if(path==='/api/v1/source-cvs/upload-and-extract'&&method==='POST')return ok(route,{source_cv_id:'source-cv:personal-closeout',source_cv_version_id:'source-cv-version:personal-closeout',cv_extraction_task_id:'cv-task:personal-closeout',created_source:true,created_version:true,created_task:true,task_status:'succeeded',text_extraction_status:'completed',extraction_method:'pdf_text',extraction_provider:'pymupdf',source_file_id:null});
    if(path==='/api/v1/cv-extraction-tasks/cv-task%3Apersonal-closeout')return ok(route,{task_id:'cv-task:personal-closeout',source_cv_version_id:'source-cv-version:personal-closeout',owner_id:'personal-user',request_id:'browser-e2e',execution_id:'cv-execution:personal-closeout',execution_metadata:{provider:'cv-extraction-service',model:'deepseek-v4-flash',normalization_version:'2.0',taxonomy_version:'skill-taxonomy-snapshot.v1'},status:'succeeded',processing_stage:'review_pending',attempt_count:1,max_attempts:3,last_error_code:null,last_error_message:null,retryable:false,claimed_by:null,lease_expires_at:null,heartbeat_at:null,next_attempt_at:null,finished_at:'2026-08-07T08:30:00Z',validation_conclusion:'pass',validation_report_payload:null,validation_task_id:'cv-validation:personal-closeout',validation_report_id:'cv-validation-report:personal-closeout',resume_id:null,created_at:'2026-08-07T08:00:00Z',updated_at:'2026-08-07T08:30:00Z',review_payload:null,review_id:'cv-review:personal-closeout',confirmation_status:'pending',latest_validated_cv_snapshot_id:null,confirmed_at:null,confirmed_by:null,review_revision:1,confirmation_idempotency_key:null,confirmation_idempotency_id:null});
    if(path==='/api/v1/cv-extraction-tasks/cv-task%3Apersonal-closeout/review')return ok(route,{task_id:'cv-task:personal-closeout',source_cv_id:'source-cv:personal-closeout',source_cv_version_id:'source-cv-version:personal-closeout',status:'succeeded',confirmation_status:'pending',review_id:'cv-review:personal-closeout',review_revision:1,source_text:'三年软件开发经验，使用 Python、FastAPI 和 PostgreSQL 开发企业知识库服务。',source_file_id:null,content_type:null,ocr_layout:null,reviewable_fields:[{field_id:'skill-python',field_type:'skill',section:'skills',item_id:'skill-python',field_path:'name',field_label:'技能',original_value:'Python',suggested_value:'Python',evidence:{source_document_id:'source-cv-version:personal-closeout',source_id:'cv-evidence:python',quote:'使用 Python、FastAPI 和 PostgreSQL',start:10,end:42,alignment:'exact',occurrence_index:0},flag_codes:[]}],review_flags:[],validation:{conclusion:'pass',policy_version:'cv-validation-policy.v2',validation_task_id:'cv-validation:personal-closeout',validation_report_id:'cv-validation-report:personal-closeout',blocking_reasons:[]}});
    if(path==='/api/v1/cv-extraction-tasks/cv-task%3Apersonal-closeout/confirm'&&method==='POST'){
      if(options.confirmConflict)return fail(route,409,'确认载荷与当前审核修订冲突','CV_CONFIRMATION_CONFLICT');
      confirmed=true;
      return ok(route,{snapshot_id:personalIds.snapshot,snapshot_revision:1,resume_id:personalIds.resume,task_id:'cv-task:personal-closeout',supersedes_snapshot_id:null,idempotency_key:'confirm-cv-task:personal-closeout'});
    }
    if(path===`/api/v1/resumes/${personalIds.resume}/parse-result`)return ok(route,{parse_result_id:'parse-result:personal-closeout',resume_id:personalIds.resume,education:[],projects:[],internships:[],skills:[],certificates:[],competitions:[],parse_confidence:.91,need_review:false});
    if(path===`/api/v1/resumes/${personalIds.resume}/skill-profile`)return ok(route,{resume_id:personalIds.resume,skills:[{resume_skill_id:'resume-skill:python',resume_id:personalIds.resume,skill_id:'skill-python',raw_skill:'Python',confidence:.91,evidence:'使用 Python、FastAPI 和 PostgreSQL',proficiency:'working'}]});
    if(path==='/api/v1/matches/positions')return ok(route,[positionRecord]);
    if(path==='/api/v1/matches/preflight')return ok(route,{ready:true,cv_snapshot_ready:true,cv_profile_ready:true,position_profile_ready:true,blockers:[],validated_cv_snapshot_id:personalIds.snapshot,position_graph_version:personalIds.graph});
    if(path==='/api/v1/matches/tasks'&&method==='POST')return ok(route,{task_id:'match-task:personal-closeout',status:'succeeded',progress:100,evaluation_id:personalIds.evaluation,result_reference:`matching_evaluation:${personalIds.evaluation}`,error_code:null,error_message:null,provider:'matching-service',target_type:'standard_position'});
    if(path===`/api/v1/matches/reports/${personalIds.evaluation}`)return ok(route,personalReport());
    if(path===`/api/v1/matches/reports/${personalIds.evaluation}/what-if`&&method==='POST')return ok(route,{generation_status:'completed',scenario_id:'what-if:personal-closeout',actions:[],baseline_score:22.5,scenario_score:34.5,score_delta:12,baseline_confidence:.74,scenario_confidence:.79,confidence_delta:.05,baseline_recommendation:'not_recommended',scenario_recommendation:'potential_match',baseline_hard_gate_status:'passed',scenario_hard_gate_status:'passed',dimension_deltas:[],denominator_changed:false,score_effect_status:'modeled',baseline_evaluation_id:personalIds.evaluation,scoring_algorithm_version:'explainable-scoring.v1',scoring_config_version:personalIds.scoringConfig,position_graph_version:personalIds.graph,target_type:'standard_position',use_enterprise_weights:false,hypothetical:true,algorithm_version:'deterministic-what-if.v2'});
    if(path===`/api/v1/matches/reports/${personalIds.evaluation}/evidence-deletions`&&method==='POST')return ok(route,{generation_status:'completed',deletion_run_id:'deletion-run:personal-closeout',deletion_kind:'critical',deleted_evidence_source_ids:['candidate-evidence-0'],critical_evidence_source_ids:['candidate-evidence-0'],noncritical_evidence_source_ids:[],explanation_factors:[],baseline_evaluation:null,ablated_evaluation:null,baseline_gap_analysis:null,ablated_gap_analysis:null,baseline_score:22.5,ablated_score:10,retained_only_score:22.5,score_delta:-12.5,dimension_deltas:[],baseline_hard_gate_status:'passed',ablated_hard_gate_status:'uncertain',hard_gate_delta:'passed → uncertain',added_gap_ids:['evidence-gap:python'],removed_gap_ids:[],added_action_ids:['action-evidence-python'],removed_action_ids:[],comprehensiveness:.55,sufficiency:.9,unsupported_reason_rate:0,faithfulness_status:'faithful',baseline_evaluation_id:personalIds.evaluation,cv_profile_version:personalIds.cvProfileVersion,position_profile_version:personalIds.positionProfileVersion,scoring_algorithm_version:'explainable-scoring.v1',scoring_config_version:personalIds.scoringConfig,classification_policy_version:'explanation-factor-policy.v1',stability_threshold_points:1,hypothetical:true,algorithm_version:'evidence-deletion-recompute.v1'});
    if(path==='/api/v1/learning-paths'&&method==='GET')return ok(route,[]);
    if(path==='/api/v1/learning-paths'&&method==='POST')return ok(route,learningPathResult);
    if(decodeURIComponent(path)==='/api/v1/learning-paths/learning-path:personal-closeout')return ok(route,learningPathResult);
    return ok(route,[]);
  });
}

test('Personal Career Decision 使用同一冻结身份贯穿完整 UI 链',async({page})=>{
  await installPersonalApi(page);
  await page.goto('/profile/resumes');
  await page.locator('input[type=file]').setInputFiles(resumeFile);
  await expect(page.getByText('字段证据审核')).toBeVisible();
  await expect(page.getByText('source-cv-version:personal-closeout',{exact:true})).toBeVisible();
  await expect(page.getByText('cv-validation-policy.v2')).toBeVisible();
  await expect(page.getByText('使用 Python、FastAPI 和 PostgreSQL',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'批量接受本组'}).click();
  await page.getByRole('button',{name:'确认并生成快照'}).click();
  await expect(page.getByText(`snapshot_id：${personalIds.snapshot}`)).toBeVisible();
  await page.getByRole('button',{name:'进入岗位匹配'}).first().click();
  await expect(page.getByLabel('评分数据 Gate')).toContainText(personalIds.snapshot);
  await expect(page.getByLabel('评分数据 Gate')).toContainText(personalIds.position);
  await expect(page.getByLabel('评分数据 Gate')).toContainText(personalIds.graph);
  await page.getByRole('button',{name:'运行匹配'}).click();

  await expect(page.getByRole('heading',{name:'岗位匹配报告'})).toBeVisible();
  await expect(page.locator('.match-score strong')).toHaveText('22.5');
  await expect(page.getByText('完整证据（6）')).toBeVisible();
  await expect(page.getByText('全部技能差距（6）')).toBeVisible();

  await page.getByText('假设分析实验').click();
  await page.getByRole('checkbox',{name:/生产级 RAG 评测/}).check();
  await page.getByRole('button',{name:'模拟所选行动'}).click();
  await expect(page.getByText('deterministic-what-if.v2')).toBeVisible();
  await expect(page.getByText(personalIds.scoringConfig).first()).toBeVisible();

  await page.getByRole('button',{name:'生成学习路线'}).click();
  await expect(page.getByText('完成 40 小时生产级 RAG 评测闭环')).toBeVisible();
  await expect(page.getByText(personalIds.snapshot)).toBeVisible();
  await expect(page.getByText(personalIds.positionProfileVersion).first()).toBeVisible();

  await page.getByRole('button',{name:/技术与审计信息/}).click();
  await expect(page.getByText(personalIds.algorithm).first()).toBeVisible();
  await expect(page.getByText(personalIds.cvProfileVersion).first()).toBeVisible();
  await expect(page.getByText(personalIds.positionProfileVersion).first()).toBeVisible();
  await expect(page.getByText('解释忠实度证据删除测试')).toBeVisible();
  const deletionCheckbox=page.getByRole('checkbox',{name:'candidate-evidence-0'});
  await deletionCheckbox.evaluate((element:HTMLInputElement)=>element.click());
  await expect(deletionCheckbox).toBeChecked();
  await page.getByRole('button',{name:'运行删除重算'}).click();
  await expect(page.getByText('evidence-deletion-recompute.v1')).toBeVisible();
  await expect(page.getByText(`CV ${personalIds.cvProfileVersion} · Position ${personalIds.positionProfileVersion}`)).toBeVisible();
});

test('浏览器异常矩阵显式覆盖 loading、403、404 与 409',async({page})=>{
  await installPersonalApi(page,{delayResumes:true});
  await page.goto('/profile/resumes');
  await expect(page.getByText('正在加载…')).toBeVisible();
  await expect(page.getByText('还没有简历')).toBeVisible();

  await page.unroute('**/api/v1/**');
  const enterpriseUser=await authenticate(page,'enterprise_user');
  await page.route('**/api/v1/**',route=>pathOf(route)==='/api/v1/auth/me'?ok(route,enterpriseUser):ok(route,[]));
  await page.goto('/profile/resumes');
  await expect(page.getByText('无权访问')).toBeVisible();
  await page.goto('/route-does-not-exist');
  await expect(page.getByText('页面不存在')).toBeVisible();

  await page.unroute('**/api/v1/**');
  await installPersonalApi(page,{confirmConflict:true});
  await page.goto('/profile/resumes');
  await page.locator('input[type=file]').setInputFiles(resumeFile);
  await expect(page.getByText('字段证据审核')).toBeVisible();
  await page.getByRole('button',{name:'批量接受本组'}).click();
  await page.getByRole('button',{name:'确认并生成快照'}).click();
  await expect(page.getByText('确认指纹或载荷已变化，请重新审核')).toBeVisible();
  await expect(page.getByText('审核未完成')).toBeVisible();
});

test('stale、missing evidence 与 rejected/incomplete 不会伪装成当前正式结果',async({page})=>{
  const user=await authenticate(page,'personal_user');
  await page.route('**/api/v1/**',route=>{
    const path=pathOf(route);
    if(path==='/api/v1/auth/me')return ok(route,user);
    if(path==='/api/v1/matches/positions'||path==='/api/v1/learning-paths')return ok(route,[]);
    if(path.endsWith('/formal-stale'))return ok(route,{...personalReport({stale:true}),evaluation_id:'formal-stale'});
    if(path.endsWith('/formal-missing-evidence'))return ok(route,{...personalReport({missingEvidence:true}),evaluation_id:'formal-missing-evidence'});
    if(path.endsWith('/formal-rejected'))return ok(route,{...personalReport({status:'rejected'}),evaluation_id:'formal-rejected'});
    return ok(route,[]);
  });

  await page.goto('/matching/reports/formal-stale');
  await expect(page.getByText('这份报告需要重新计算')).toBeVisible();
  await expect(page.getByRole('button',{name:'重新匹配'})).toBeVisible();
  await page.goto('/matching/reports/formal-missing-evidence');
  await expect(page.getByText(/共使用 0 条证据，1 项结论证据不足/)).toBeVisible();
  await expect(page.getByText('证据不足，暂不判断').first()).toBeVisible();
  await page.goto('/matching/reports/formal-rejected');
  await expect(page.getByText('Evaluation rejected')).toBeVisible();
  await expect(page.getByText('正式评分输入不完整，Evaluation 被拒绝。').first()).toBeVisible();
  await expect(page.getByText('匹配结论不可用')).toBeVisible();
});

test('EnterpriseJobProfile 经正式 Candidate Board Evaluation 进入同一报告页',async({page})=>{
  const user=await authenticate(page,'enterprise_user');
  const formal=enterpriseCase.formal_results as Json;
  const results=formal.results as Json[];
  const selectedResult=results[0];
  const enterpriseJob=enterpriseCase.enterprise_job as Json;
  const profile=enterpriseCase.enterprise_profile_before as Json;
  const board=enterpriseCase.enterprise_board as Json;
  const evaluation=selectedResult.evaluation as Json;
  const cvProfile=selectedResult.cv_profile as Json;
  const evaluationId=String(evaluation.evaluation_id);
  const jobId=String(enterpriseJob.job_id);
  await page.route('**/api/v1/**',route=>{
    const path=pathOf(route);
    if(path==='/api/v1/auth/me')return ok(route,user);
    if(path==='/api/v1/enterprises/me')return ok(route,{enterprise_id:'enterprise-case-001',owner_user_id:'enterprise-case-recruiter',enterprise_name:'Enterprise Case V1',industry:'金融科技',scale:'case',location:'Shanghai',description:'D Enterprise formal acceptance',status:'active',created_at:'2026-08-17T02:30:00Z',updated_at:'2026-08-17T02:30:00Z'});
    if(path==='/api/v1/enterprise-jobs')return ok(route,[{enterprise_job_id:jobId,enterprise_id:'enterprise-case-001',title:'反欺诈实时决策工程师',standard_position_id:String(profile.canonical_position_id),jd_text:String(enterpriseJob.jd_text),headcount:1,location:'Shanghai',employment_type:'full_time',salary_min:null,salary_max:null,status:'published',created_at:'2026-08-17T02:30:00Z',updated_at:'2026-08-17T02:30:00Z'}]);
    if(path===`/api/v1/enterprise-jobs/${jobId}/skill-weights`)return ok(route,[{id:'enterprise-weight:python',enterprise_job_id:jobId,skill_id:'Python',weight:1,is_required:true,is_bonus:false}]);
    if(path===`/api/v1/enterprise-jobs/${jobId}/match-reports`)return ok(route,[{evaluation_id:evaluationId,task_id:`enterprise-case-task:${String(selectedResult.candidate_id)}`,resume_id:String(selectedResult.candidate_id),position_id:jobId,status:'succeeded',provider:'matching-service',created_at:'2026-08-17T02:30:00Z',updated_at:'2026-08-17T02:30:00Z'}]);
    if(path===`/api/v1/enterprise-jobs/${jobId}/candidate-submissions`)return ok(route,(board.items as Json[]).map(item=>({submission_id:item.submission_id,resume_id:item.resume_id,resume_display_name:item.candidate_display_name,enterprise_job_id:jobId,enterprise_id:'enterprise-case-001',status:item.candidate_status,created_at:'2026-08-17T02:30:00Z',updated_at:'2026-08-17T02:30:00Z',parse_status:'completed',validated_cv_snapshot_id:`snapshot:${String(item.resume_id)}`,skill_count:3,matchable:item.candidate_status==='submitted',matchable_reason:item.candidate_status==='submitted'?'可匹配':'投递已撤销'})));
    if(path===`/api/v1/enterprise-jobs/${jobId}/candidate-decision-board`)return ok(route,board);
    if(path===`/api/v1/matches/reports/${encodeURIComponent(evaluationId)}`||decodeURIComponent(path)===`/api/v1/matches/reports/${evaluationId}`)return ok(route,{evaluation_id:evaluationId,task_id:`enterprise-case-task:${String(selectedResult.candidate_id)}`,status:'current',stale:false,stale_reason_codes:[],evaluation,gap_analysis:selectedResult.gap_analysis,versions:{position_graph_version:String((evaluation.final_match_result as Json).position_graph_version),evaluation_algorithm_version:String(evaluation.algorithm_version),scoring_config_version:String((evaluation.final_match_result as Json).scoring_config_version)},lineage:{resume_id:String(selectedResult.candidate_id),position_id:jobId,validated_cv_snapshot_id:String(cvProfile.verification_snapshot_id),target_type:'enterprise_job',provider:'matching-service',method:'deterministic_explainable',algorithm_versions:{evaluation:evaluation.algorithm_version,scoring_config:(evaluation.final_match_result as Json).scoring_config_version},data_versions:{enterprise_job_profile:profile.profile_version}},created_at:'2026-08-17T02:30:00Z',updated_at:'2026-08-17T02:30:00Z'});
    if(path==='/api/v1/matches/positions'||path==='/api/v1/learning-paths')return ok(route,[]);
    return ok(route,[]);
  });

  await page.goto('/enterprise/recruitment');
  await expect(page.getByText(String(enterpriseJob.jd_text))).toBeVisible();
  await page.getByText('候选评估').click();
  await page.getByText('决策板').click();
  await expect(page.getByText('高匹配且业务场景命中')).toBeVisible();
  await expect(page.getByText('39 条')).toBeVisible();
  await page.getByRole('button',{name:/查看/}).first().click();
  await page.getByRole('link',{name:/查看完整匹配报告/}).click();
  await expect(page.getByRole('heading',{name:'岗位匹配报告'})).toBeVisible();
  await page.getByRole('button',{name:/技术与审计信息/}).click();
  await expect(page.getByText(String(profile.profile_id))).toBeVisible();
  await expect(page.getByText(`${String(profile.profile_id)} · ${String(profile.profile_version)}`)).toBeVisible();
  await expect(page.getByText('deterministic-matching.v6').first()).toBeVisible();
  await expect(page.getByText('scoring-config.enterprise.v2').first()).toBeVisible();
});
