export type ApiResponse<T>={code:number;message:string;data:T;details?:unknown;trace_id:string};
export type ApiErrorDetails={error_code?:string;message?:string;fields?:unknown;upstream?:unknown;[key:string]:unknown};
export type CurrentUser={user_id:string;username:string;role:string;permissions:string[]};
export type Position={position_id:string;name:string;category_code:string;current_version_id:number|null;current_version_number:number;sample_count:number;skill_count:number;published_at:string|null;release_id:string|null;quality_state:'ready'|'thin'};
export type ModalityDistribution={required?:number;preferred?:number;bonus?:number;unknown?:number};
export type SkillClassification={facet:'concept_class'|'technology_kind'|'domain';code:string;name_zh:string;name_en?:string|null;is_primary:boolean};
export type ModificationHistory={id:number;actor_id:number|null;before:Record<string,unknown>|null;after:Record<string,unknown>|null;reason:string|null;trace_id:string;created_at:string};
export type RelationStatistics={supporting_jd_count:number;deduplicated_jd_count:number;enterprise_count:number;source_count:number;evidence_count:number;first_seen_at:string|null;last_seen_at:string|null;raw_frequency:number;quality_adjusted_frequency:number};
export type RelationExplanation={relation_id:number;position_id:string;skill_id:string;statistics:Partial<RelationStatistics>;sources:Array<Record<string,unknown>>;evidence:Array<Record<string,unknown>>;weight_basis:Record<string,unknown>;confidence_basis:Record<string,unknown>;quality_impact:Record<string,unknown>;manual_modification_history:Array<Record<string,unknown>>;version_id:number|null;is_current:boolean};
export type Relation={relation_id:number;skill_id:string;canonical_name:string;category_code:string|null;category_name?:string|null;classifications?:SkillClassification[];taxonomy_version?:string|null;revision?:number;weight:number;auto_weight?:number;manual_weight?:number|null;final_weight?:number;confidence:number;auto_confidence?:number;manual_confidence?:number|null;final_confidence?:number;importance_level:string;auto_importance_level?:string;manual_importance_level?:string|null;final_importance_level?:string;primary_modality:'required'|'preferred'|'bonus'|'unknown';modality_distribution:ModalityDistribution;trend_score:number|null;metrics:{support_document_count:number;support_count:number;trusted_evidence_ratio:number;unknown_ratio:number};statistics?:RelationStatistics;explanation?:RelationExplanation;modification_history?:ModificationHistory[]};
export type ProfileItem={aggregate_id:number;kind?:string;text?:string;support_document_count:number;document_ids?:string[];evidence_ids:number[]};
export type RequirementProfile=ProfileItem&{kind:string};
export type SampleStats={included_samples?:number;excluded_samples?:number;relations?:number;minimum_valid_samples?:number};
export type BuildInfo={build_run_id:number;build_version:number;base_build_version:number|null;status:string;window_start:string|null;window_end:string|null;config_snapshot:Record<string,unknown>;summary:SampleStats;created_at:string};
export type GraphSnapshot={position_id:string;position:{position_id:string;name:string;category_code:string};skill_relations:Relation[];requirement_profile:RequirementProfile[];responsibilities:ProfileItem[];company_context:ProfileItem[];employment_context:ProfileItem[];sample_stats:SampleStats;view_type?:'published'|'draft';version_id?:number;draft_id?:number;build_run_id?:number;base_version_id?:number;build_info?:BuildInfo;warning?:string};
export type RequirementInflationMarket={support_ratio:number;supporting_jd_count:number;required_supporting_jd_count:number;required_prevalence:number;required_purity:number;enterprise_count:number;source_count:number;leave_one_out_required_jd_count:number;leave_one_out_enterprise_count:number;leave_one_out_source_count:number};
export type RequirementInflationItem={requirement_id:string;skill_id:string;skill_name:string;evidence_id:number;jd_modality:'required';market_status:'market_supported'|'enterprise_specific'|'inflation_risk';inflation_risk:boolean;reason_codes:string[];market:RequirementInflationMarket};
export type JDRequirementInflationDiagnostic={document_id:string;enterprise_name:string|null;source_name:string|null;required_skill_count:number;inflation_risk_skill_count:number;inflation_ratio:number;risk_level:'low'|'medium'|'high';requirements:RequirementInflationItem[]};
export type RequirementInflationReport={algorithm_version:'requirement-strength-calibration.v1';scope:'required_skills';summary:{jd_count:number;total_required_requirement_count:number;market_supported_count:number;enterprise_specific_count:number;inflation_risk_count:number;jd_risk_level_counts:Record<'low'|'medium'|'high',number>};jd_diagnostics:JDRequirementInflationDiagnostic[]};
export type PositionRequirementInflation={position_id:string;graph_version:string|null;graph_version_id:number|null;requirement_inflation:RequirementInflationReport|null};
export type OriginalRequirement={requirement_id?:string;kind?:string;modality?:string;text?:string;items?:Array<{name:string}>};
export type EvidenceSupport={support_id:number;document_id:string;requirement_id:string;modality:string;evidence:{id:number;document_id?:string;quote:string;start:number|null;end:number|null;alignment:string;occurrence_index:number|null};original_requirement:OriginalRequirement;normalized_skill:{id:number;skill_id:string;canonical_name:string;source_name:string;resolution_status:string};source:{document_id:string;raw_text:string}};
export type AggregateEvidenceSupport={evidence_id:number;evidence:{id:number;document_id?:string;quote:string;start:number|null;end:number|null;alignment:string;occurrence_index:number|null};source?:{document_id:string;raw_text:string}};
export type ReviewPayload={reasons?:string[];reason?:string;checked?:boolean;note?:string;resolution?:{skill_id?:string;canonical_name?:string}};
export type ReviewHistory={id:number;actor_id:number|null;action:string;before:Record<string,unknown>|null;after:Record<string,unknown>|null;reason:string|null;trace_id:string;created_at:string};
export type ReviewAction='claim'|'approve'|'reject'|'modify';
export type ReviewTask={id:number;object_type:string;object_id:string;build_run_id:number|null;build_version:number|null;position_name?:string|null;build_summary?:SampleStats;status:string;assignee_id:number|null;payload:ReviewPayload;original_content:unknown;changed_content:unknown;evidence:Array<EvidenceSupport|AggregateEvidenceSupport>;review_flags:unknown[];impact_scope:unknown;history:ReviewHistory[];allowed_actions:ReviewAction[]};
export type GovernanceReviewAction='claim'|'release'|'approve'|'reject';
export type GovernanceReviewTask={task_id:string;object_type:string;object_id:string;priority:string;reason:string|null;status:string;reviewer_id:string|null;reviewer_name?:string|null;object_name?:string|null;review_stage?:string|null;review_comment:string|null;modified_payload:Record<string,unknown>|null;created_at:string|null;updated_at:string|null};
export type JDReviewSkill={source_name:string;requirement_id:string|null;skill_id:string|null;canonical_name:string|null;resolution_status:string;resolution_source?:string|null};
export type ReviewExtractionItem={requirement_id?:string;kind?:string;modality?:string;action?:string;text?:string;skills?:string[];minimum_degree?:string;duration_text?:string;evidence?:{quote?:string}};
export type GovernanceReviewContext={kind:string;jd_id?:string;parse_result_id?:string;title?:string;source_name?:string|null;raw_text?:string|null;position?:{schema_version?:string;taxonomy_version?:string;source_title?:string;position_id?:string;position_code?:string;position_name?:string;classification_status?:string;candidate_positions?:Array<{position_code:string;score:number}>;career_level?:string|null;leadership_scope?:string|null;technology_focus_codes?:string[];industry_context_codes?:string[];observed_skill_domain_codes?:string[];review_reason_codes?:string[];evidence_refs?:string[];classification_policy_version?:string};responsibilities?:ReviewExtractionItem[];requirements?:ReviewExtractionItem[];company_facts?:Array<Record<string,unknown>>;employment_facts?:Array<Record<string,unknown>>;skills?:JDReviewSkill[];resolved_skill_count?:number;unresolved_skill_count?:number;rejected_skill_count?:number;blocking_issues?:Array<Record<string,unknown>>;pending_validation_reviews?:PendingValidationReview[];can_approve?:boolean;workflow_status?:string;conclusion?:string;policy_version?:string;report?:Record<string,unknown>};
export type PendingValidationReview={task_id:string;conclusion?:string|null;reason?:string|null;status?:string};
export type UnresolvedItem={id:string;parse_result_id:string;jd_id:string;jd_title:string;source_name:string;requirement_id:string|null;reason:string;source_type:string;source_name_label:string|null;raw_text:string};
export type CatalogSkill={skill_id:string;skill_name:string;category:string|null;description:string|null};
export type MappingEntityType='position'|'skill';
export type MappingItem={entity_type:MappingEntityType;main_system_id:string;source_name:string;source_taxonomy_code:string|null;source_taxonomy_name:string|null;knowledge_graph_id:string|null;sync_status:string;last_error_code:string|null;last_error_message:string|null;last_trace_id:string|null;updated_at:string|null};
export type MappingCandidate={entity_type:MappingEntityType;knowledge_graph_id:string;name:string;status:string};
export type GraphVersion={id:number;version_number:number;version_name:string;build_run_id:number;release_id:string|null;rollback_from_version_id:number|null;created_at:string};
export type GraphVersionDetail={version_id:number;version_number:number;position_id:string;build_run_id:number|null;release_id:string|null;base_version_id:number|null;snapshot:GraphSnapshot;snapshot_hash:string;created_at:string;published_by:number|null};
export type GraphDiff={added:Relation[];removed:Relation[];changed:Array<{skill_id:string;before:Relation;after:Relation;changed_fields:Record<string,{before:unknown;after:unknown}>}>;context_changes:Record<string,{before:unknown;after:unknown}>;evidence_changes:Array<{skill_id:string;before:unknown[];after:unknown[]}>};
export type BuildSummary={included_samples?:number;excluded_samples?:number;relations?:number;minimum_valid_samples?:number};
export type BuildRun={id:number;build_version:number;status:string;summary:BuildSummary};
export type PublishGate={allowed:boolean;hard_gate_allowed?:boolean;already_published?:boolean;published_version_id?:number;errors:Array<{rule:string;message:string}>;valid_sample_count:number;open_review_task_count:number;unresolved_count:number;non_exact_evidence_count:number;low_confidence_relation_count:number;minimum_valid_samples:number;minimum_samples_met:boolean;skill_profile_available?:boolean;task_profile_available?:boolean;requirement_profile_available?:boolean};

const apiMessageLabels:Record<string,string>={
  success:'操作成功',
  error:'操作失败',
  failed:'处理失败',
  'Request failed':'请求失败，请稍后重试。',
  'Validation error':'请求参数有误，请检查后重试。',
  Unauthorized:'登录状态已失效，请重新登录。',
  Forbidden:'当前账号没有执行该操作的权限。',
  Conflict:'数据已发生变化，请刷新后重试。',
  'Not Found':'未找到对应内容。',
  'Service is not ready':'服务尚未就绪，请稍后重试。',
  'DISCOVERY_DATASET_NOT_READY':'冻结发现数据集不可用或校验失败，请联系管理员检查随版本发布的数据资产。',
  'DISCOVERY_INPUT_UNAVAILABLE':'当前没有可用于岗位发现的已发布 JD，请先完成 JD 审核与发布。',
  'skill_catalog_snapshot_missing':'所选标准技能缺少完整分类信息，请选择已经完成分类的标准技能。',
  'skill_catalog_conflict':'该技能对应多个标准技能，无法自动确定，请人工选择唯一结果。',
  'skill_catalog_mapping_not_pending':'这项技能已经处理，刷新列表后查看最新状态。',
  'Blocking review flags must be resolved before confirmation':'岗位归类、证据或其他非技能字段仍有阻断问题，请先修正后再确认。',
  'Blocking review flags must be resolved before publication':'岗位归类、证据或其他非技能字段仍有阻断问题，请先修正后再发布。',
  'validation_review_pending':'这条 JD 的数据质量审核尚未完成。请先在审核中心处理对应的数据质量问题，再重试发布。',
  'validation_review_rejected':'这条 JD 的数据质量审核已被退回，当前结果不能发布。请按退回意见修正后重新验证。',
  'validation_review_missing':'这条 JD 尚未生成可处理的数据质量审核任务，请等待 Validation 完成后重试。',
  'validation_blocked':'Validation 判定这条 JD 存在阻断性质量问题，当前结果不能发布。',
  'validation_pending':'这条 JD 的数据质量检查仍在运行，完成后再重试发布。',
  'validation_failed':'这条 JD 的数据质量检查失败，请先在任务中心查看失败原因并重试。',
  'validation_task_missing':'这条 JD 尚未完成数据质量检查，当前不能发布。',
  'validation_result_inconsistent':'这条 JD 的数据质量结果与当前解析版本不一致，需要重新运行 Validation。',
  'CV extraction is disabled':'简历解析服务未启用。请联系管理员启用后重试。',
  'STALE_GRAPH_DRAFT':'当前草稿基于已过期的图谱版本，请刷新后重新打开草稿再发布。',
  'BUILD_ALREADY_PUBLISHED':'该构建版本已经发布，无需重复发布。',
  'RELATION_EDIT_CONFLICT':'图谱关系已被其他操作修改，请刷新后再试。',
  'graph publish gate rejected the build':'发布门禁未通过，请先处理待办项后再发布。',
  'build has already been published':'该构建版本已经发布，无需重复发布。',
  'position_classification_not_publishable':'岗位分类尚未达到发布条件，请先选择标准岗位并保存分类结果。',
  'position_classification_v3_required':'当前 JD 尚未完成新版岗位分类，请重新解析或在审核中心完成分类。',
  'position_taxonomy_version_incompatible':'当前岗位分类使用的目录版本已过期，请重新解析后再审核。',
  'position_catalog_binding_missing':'尚未选择标准岗位，请先完成岗位分类。',
  'position_catalog_binding_invalid':'所选标准岗位已失效，请重新选择有效岗位。',
  'position_catalog_entry_not_active':'所选标准岗位当前不可用，请选择其他有效岗位。',
  'JD text is unavailable; edit the raw text or parse result manually':'JD 原文不可用，请补充原文或重新解析。',
  'Parse the JD with an explicit extraction_mode first':'请先选择解析方式并完成 JD 解析。',
  'Published JD results are immutable; create a new JD version':'已发布的 JD 不能直接修改，请创建新版本。',
  'Versioned extraction and normalization are required for review':'这条 JD 尚未完成版本化解析和归一化，暂不能审核。',
  'Versioned extraction and normalization are required for export':'这条 JD 尚未完成版本化解析和归一化，暂不能导出。',
  'Published JD result has no publication snapshot':'已发布 JD 缺少发布快照，请重新生成后再试。',
  'JD publication snapshot was not created':'JD 发布快照生成失败，请重试。',
  'Publication snapshot exists for a non-published JD result':'JD 状态与发布快照不一致，请刷新后重试。',
  'JD result must be reviewed before publication':'JD 必须先通过审核才能发布。',
  'Internal server error':'系统处理失败，请稍后重试。',
  'Published position not found':'没有找到已发布的标准岗位。',
  'Published emerging position not found':'没有找到已发布的新兴岗位。',
  'Unsupported evidence aggregate kind':'暂不支持这种证据汇总类型。',
  'Unsupported review action':'暂不支持该审核操作。',
  'Unsupported normalization action':'暂不支持该归一化操作。',
  'Review task not found':'没有找到对应的审核任务，请刷新列表。',
  'KG review tasks do not support release':'图谱审核任务不能执行放弃操作。',
  'definition_id is required':'请选择需要分析的岗位定义。',
  'Network Error':'网络连接失败，请检查网络后重试。',
  'Failed to fetch':'网络请求失败，请稍后重试。',
  'Load failed':'内容加载失败，请稍后重试。',
  'Bad Gateway':'上游服务响应异常，请稍后重试。',
  'Service Unavailable':'服务暂时不可用，请稍后重试。',
  'Gateway Timeout':'上游服务响应超时，请稍后重试。',
};
const containsChinese=(value:string)=>/[\u3400-\u9fff]/.test(value);
const looksLikeEnglishSystemMessage=(value:string)=>{
  const stripped=value
    .replace(/\b(?:HTTP|API|JD|CV|JSON|Cookie|Evidence|Trend)\b/gi,'')
    .replace(/[A-Z0-9_./:\-()[\]{}]+/g,' ');
  return /[A-Za-z]{2,}/.test(stripped);
};
/** 所有浮层消息共用的中文化边界，禁止把上游英文异常直接展示给用户。 */
export function localizeSystemMessage(value:string){
  const source=value.trim();
  if(!source)return '系统处理失败，请稍后重试。';
  if(apiMessageLabels[source])return apiMessageLabels[source];
  if(containsChinese(source))return source;
  if(/timed?\s*out|timeout/i.test(source))return '请求处理超时，请稍后重试。';
  if(/network|connect|socket|disconnected|fetch/i.test(source))return '网络连接异常，请稍后重试。';
  if(/unauthori[sz]ed|token|credential|authentication/i.test(source))return '登录状态已失效，请重新登录。';
  if(/forbidden|permission|denied/i.test(source))return '当前账号没有执行该操作的权限。';
  if(/not\s+found|missing/i.test(source))return '没有找到请求的内容，请刷新后重试。';
  return looksLikeEnglishSystemMessage(source)?'系统处理失败，请稍后重试。':source;
}
const humanApiMessage=(details:ApiErrorDetails|undefined,message:string)=>{
  if(details?.error_code&&apiMessageLabels[details.error_code])return apiMessageLabels[details.error_code];
  const source=details?.message||message;
  return localizeSystemMessage(source);
};
const isRecord=(value:unknown):value is Record<string,unknown>=>typeof value==='object'&&value!==null&&!Array.isArray(value);
const isStructuredError=(value:unknown):value is ApiErrorDetails=>{
  if(!isRecord(value))return false;
  const hasErrorCode=typeof value.error_code==='string'&&value.error_code.length>0;
  const hasFields=('fields' in value)&&(Array.isArray(value.fields)||isRecord(value.fields));
  const hasMessage=typeof value.message==='string'&&value.message.length>0;
  return hasErrorCode||(hasMessage&&hasFields);
};
const normalizeErrorDetails=(body:Partial<ApiResponse<unknown>>):ApiErrorDetails|undefined=>{
  if(isStructuredError(body.details))return body.details;
  if(isStructuredError(body.data))return body.data;
  return undefined;
};
export class ApiError extends Error{
  public errorCode?:string;
  public fields?:unknown;
  public upstream?:unknown;
  constructor(public status:number,message:string,public traceId?:string,public details?:ApiErrorDetails){
    super(humanApiMessage(details,message));
    this.name='ApiError';
    this.errorCode=details?.error_code;
    this.fields=details?.fields;
    this.upstream=details?.upstream;
  }
}
export function errorTitle(error:{status?:number}){return error.status===401?'请先登录':error.status===403?'权限不足':error.status===409?'数据冲突':error.status&&[502,503,504].includes(error.status)?'上游服务不可用':'请求失败'}
const TOKEN_KEY='main_access_token';
export const AUTH_EXPIRED_EVENT='jobgraph:auth-expired';
export function setAccessToken(token:string){localStorage.setItem(TOKEN_KEY,token)}
export function clearAccessToken(){localStorage.removeItem(TOKEN_KEY)}
export function hasAccessToken(){return Boolean(localStorage.getItem(TOKEN_KEY))}
function handleAuthFailure(status:number,token:string|null){
  if(status===401&&token){
    clearAccessToken();
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}
export async function api<T>(path:string,init?:RequestInit):Promise<T>{
  const headers=new Headers(init?.headers);
  if(!(init?.body instanceof FormData)&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
  const token=localStorage.getItem(TOKEN_KEY);if(token)headers.set('Authorization',`Bearer ${token}`);
  const response=await fetch(`/api/v1${path}`,{...init,headers});const body=(await response.json().catch(()=>({message:response.statusText}))) as Partial<ApiResponse<T>>;
  // A session can expire while a long extraction task is polling. Clear the
  // stale identity immediately so the shell cannot show a user beside a 401.
  handleAuthFailure(response.status,token);
  if(!response.ok||body.code!==0){
    const details=normalizeErrorDetails(body as Partial<ApiResponse<unknown>>);
    throw new ApiError(response.status,body.message||'请求失败',body.trace_id,details);
  }
  return body.data as T;
}
export async function apiBlob(path:string):Promise<Blob>{
  const headers=new Headers();
  const token=localStorage.getItem(TOKEN_KEY);if(token)headers.set('Authorization',`Bearer ${token}`);
  const response=await fetch(`/api/v1${path}`,{headers});
  handleAuthFailure(response.status,token);
  if(!response.ok)throw new ApiError(response.status,response.statusText||'文件预览失败');
  return response.blob();
}
