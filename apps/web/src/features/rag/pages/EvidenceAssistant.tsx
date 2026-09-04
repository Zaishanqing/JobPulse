import {useCallback,useEffect,useLayoutEffect,useMemo,useRef,useState,type RefObject} from 'react';
import {App,Button,Checkbox,Descriptions,Drawer,Input,Popover,Progress,Spin,Tag,Tooltip,Typography} from 'antd';
import {ArrowUpOutlined,CheckOutlined,CopyOutlined,DeleteOutlined,DownOutlined,HistoryOutlined,LeftOutlined,PlusOutlined,ReloadOutlined,RightOutlined,SearchOutlined} from '@ant-design/icons';
import {useSearchParams} from 'react-router-dom';
import {ApiError,localizeSystemMessage} from '../../../shared/api';
import {domainText} from '../../../shared/idText';
import {useAuth} from '../../auth/AuthContext';
import {getRagIndexStatus,queryEvidenceRAG,resolveEvidenceCitation} from '../api';
import {citationRequest} from '../EvidenceDeepLink';
import {getJD} from '../../data/api';
import {listMyResumes} from '../../matching/api';
import {listPublishedPositions} from '../../positions/api';
import {LEGACY_RAG_CHAT_STORAGE_KEYS,ragHistoryStorageKey} from '../history';
import type {TextAreaRef} from 'antd/es/input/TextArea';
import type {Position} from '../../../shared/api';
import type {
  BusinessObjectType,
  EvidenceEntailmentRelation,
  EvidenceCitationResolution,
  EvidenceConversationTurn,
  EvidenceRAGQueryV1,
  EvidenceRAGResponseV1,
  EvidenceTypeScope,
  RAGEvidenceReferenceV1,
  RagIndexStatus,
} from '../types';

const DEFAULT_EVIDENCE_TYPES:EvidenceTypeScope[]=['jd_evidence','kg_skill_relation_evidence'];
const allowedObjectTypes:BusinessObjectType[]=[
  'source_jd','source_cv','standard_position','enterprise_job','cv_profile',
  'matching_evaluation','trend_report','discovery_cluster','graph_version','resume',
];
const allowedEvidenceTypes:EvidenceTypeScope[]=[
  'jd_evidence','cv_evidence','kg_skill_relation_evidence','trend_evidence',
  'discovery_evidence','matching_evidence','gap_evidence','learning_path_evidence',
  'review_decision_evidence','all',
];
const selectableEvidenceTypes=allowedEvidenceTypes.filter((value):value is Exclude<EvidenceTypeScope,'all'>=>value!=='all');
const objectTypeLabels:Record<string,string>={
  source_jd:'岗位来源',
  source_cv:'简历来源',
  standard_position:'标准岗位',
  position_profile:'岗位画像',
  enterprise_job:'企业岗位',
  cv_profile:'简历画像',
  matching_evaluation:'匹配评估',
  trend_report:'趋势报告',
  discovery_cluster:'发现簇',
  graph_version:'图谱版本',
  resume:'简历',
};
const evidenceTypeLabels:Record<EvidenceTypeScope,string>={
  jd_evidence:'岗位证据',
  cv_evidence:'简历证据',
  kg_skill_relation_evidence:'岗位图谱',
  trend_evidence:'趋势分析',
  discovery_evidence:'新兴岗位',
  matching_evidence:'匹配分析',
  gap_evidence:'差距分析',
  learning_path_evidence:'学习路径',
  review_decision_evidence:'审核决策',
  all:'全部',
};
const entailmentLabels:Record<EvidenceEntailmentRelation,string>={support:'支持',contradict:'矛盾',insufficient:'不足'};

type RagChatError={
  status:number;
  message:string;
  traceId?:string;
  errorCode?:string;
};

type RagLegacyObject={
  objectType:BusinessObjectType;
  objectId:string;
  objectVersion:string;
  versionKind:string;
  label:string;
};

export type ChatMessage=
  | {id:string;createdAt:string;role:'user';text:string}
  | {id:string;createdAt:string;role:'assistant';question:string;response:EvidenceRAGResponseV1;error?:never}
  | {id:string;createdAt:string;role:'assistant';question:string;response?:never;error:RagChatError};

export type RagChatSession={
  id:string;
  title:string;
  createdAt:string;
  updatedAt:string;
  messages:ChatMessage[];
  attachedPositionIds:string[];
  attachedPositionVersions:Record<string,number>;
  evidenceTypes:EvidenceTypeScope[];
  attachedPositionLabels:Record<string,string>;
  legacyObject?:RagLegacyObject;
};

type RagChatState={
  activeSessionId:string;
  sessions:RagChatSession[];
};

type SessionSeed={
  attachedPositionIds:string[];
  attachedPositionVersions:Record<string,number>;
  evidenceTypes:EvidenceTypeScope[];
  attachedPositionLabels:Record<string,string>;
  legacyObject?:RagLegacyObject;
};

type EvidenceSourceGroup={
  key:string;
  label:string;
  references:RAGEvidenceReferenceV1[];
};

type EvidenceDrawerState={
  title:string;
  references:RAGEvidenceReferenceV1[];
  response?:EvidenceRAGResponseV1;
};

type InlineStatusTone='neutral'|'warning'|'error';
type ActiveRagRequest={sessionId:string;controller:AbortController};

function nowIso(){
  return new Date().toISOString();
}

function newId(prefix:string){
  return prefix+'-'+Date.now()+'-'+Math.random().toString(36).slice(2,8);
}

function sameStringArray(left:string[],right:string[]){
  return left.length===right.length&&left.every((value,index)=>value===right[index]);
}

function sameNumberRecord(left:Record<string,number>,right:Record<string,number>){
  const leftKeys=Object.keys(left);
  return leftKeys.length===Object.keys(right).length&&leftKeys.every(key=>left[key]===right[key]);
}

function sessionTitle(question:string){
  const chars=Array.from(question.trim());
  return chars.length>22?chars.slice(0,22).join('')+'…':chars.join('')||'新对话';
}

function createSession(seed:SessionSeed={
  attachedPositionIds:[],
  attachedPositionVersions:{},
  evidenceTypes:DEFAULT_EVIDENCE_TYPES,
  attachedPositionLabels:{},
}):RagChatSession{
  const timestamp=nowIso();
  return {
    id:newId('rag-session'),
    title:'新对话',
    createdAt:timestamp,
    updatedAt:timestamp,
    messages:[],
    attachedPositionIds:[...seed.attachedPositionIds],
    attachedPositionVersions:{...seed.attachedPositionVersions},
    evidenceTypes:seed.evidenceTypes.length?[...seed.evidenceTypes]:[...DEFAULT_EVIDENCE_TYPES],
    attachedPositionLabels:{...seed.attachedPositionLabels},
    legacyObject:seed.legacyObject?{...seed.legacyObject}:undefined,
  };
}

function isRecord(value:unknown):value is Record<string,unknown>{
  return typeof value==='object'&&value!==null;
}

function normalizePersistedSession(value:unknown):RagChatSession|null{
  if(!isRecord(value)||typeof value.id!=='string'||typeof value.title!=='string')return null;
  const messages=Array.isArray(value.messages)?value.messages as ChatMessage[]:[];
  const attachedPositionIds=Array.isArray(value.attachedPositionIds)
    ?value.attachedPositionIds.filter((item):item is string=>typeof item==='string')
    :[];
  const attachedPositionVersions=isRecord(value.attachedPositionVersions)
    ?Object.entries(value.attachedPositionVersions).reduce<Record<string,number>>((result,[key,version])=>{
      if(typeof version==='number'&&Number.isInteger(version)&&version>0)result[key]=version;
      return result;
    },{})
    :{};
  const evidenceTypes=Array.isArray(value.evidenceTypes)
    ?value.evidenceTypes.filter((item):item is EvidenceTypeScope=>allowedEvidenceTypes.includes(item as EvidenceTypeScope))
    :[...DEFAULT_EVIDENCE_TYPES];
  const labels=isRecord(value.attachedPositionLabels)
    ?Object.entries(value.attachedPositionLabels).reduce<Record<string,string>>((result,[key,label])=>{
      if(typeof label==='string')result[key]=label;
      return result;
    },{})
    :{};
  const legacyValue=value.legacyObject;
  const legacyObject=isRecord(legacyValue)
    &&typeof legacyValue.objectType==='string'
    &&allowedObjectTypes.includes(legacyValue.objectType as BusinessObjectType)
    &&typeof legacyValue.objectId==='string'
    ?{
      objectType:legacyValue.objectType as BusinessObjectType,
      objectId:legacyValue.objectId,
      objectVersion:typeof legacyValue.objectVersion==='string'?legacyValue.objectVersion:'',
      versionKind:typeof legacyValue.versionKind==='string'?legacyValue.versionKind:'',
      label:typeof legacyValue.label==='string'?legacyValue.label:'',
    }
    :undefined;
  return {
    id:value.id,
    title:value.title,
    createdAt:typeof value.createdAt==='string'?value.createdAt:nowIso(),
    updatedAt:typeof value.updatedAt==='string'?value.updatedAt:nowIso(),
    messages,
    attachedPositionIds,
    attachedPositionVersions,
    evidenceTypes:evidenceTypes.length?evidenceTypes:[...DEFAULT_EVIDENCE_TYPES],
    attachedPositionLabels:labels,
    legacyObject,
  };
}

function readChatState(seed:SessionSeed,userId?:string):RagChatState{
  const fresh=createSession(seed);
  if(typeof window==='undefined'||!userId)return {activeSessionId:fresh.id,sessions:[fresh]};
  try{
    const raw=window.localStorage.getItem(ragHistoryStorageKey(userId));
    if(!raw)return {activeSessionId:fresh.id,sessions:[fresh]};
    const parsed=JSON.parse(raw) as unknown;
    if(!isRecord(parsed)||!Array.isArray(parsed.sessions))return {activeSessionId:fresh.id,sessions:[fresh]};
    const sessions=parsed.sessions.map(normalizePersistedSession).filter((item):item is RagChatSession=>Boolean(item));
    if(!sessions.length)return {activeSessionId:fresh.id,sessions:[fresh]};
    const activeSessionId=typeof parsed.activeSessionId==='string'&&sessions.some(item=>item.id===parsed.activeSessionId)
      ?parsed.activeSessionId
      :sessions[0].id;
    return {activeSessionId,sessions};
  }catch{
    return {activeSessionId:fresh.id,sessions:[fresh]};
  }
}

function createInitialSeed(objectType:BusinessObjectType|'',objectId:string,objectVersion:string,versionKind:string,objectName:string,urlEvidenceTypes:EvidenceTypeScope[]):SessionSeed{
  const selectedEvidenceTypes=urlEvidenceTypes.length?urlEvidenceTypes:DEFAULT_EVIDENCE_TYPES;
  if(objectType==='standard_position'&&objectId){
    const graphVersionId=versionKind==='graph_version_id'&&Number.isInteger(Number(objectVersion))&&Number(objectVersion)>0
      ?Number(objectVersion)
      :undefined;
    return {
      attachedPositionIds:[objectId],
      attachedPositionVersions:graphVersionId?{[objectId]:graphVersionId}:{},
      evidenceTypes:selectedEvidenceTypes,
      attachedPositionLabels:{[objectId]:objectName||objectId},
    };
  }
  if(objectType&&objectId){
    return {
      attachedPositionIds:[],
      attachedPositionVersions:{},
      evidenceTypes:selectedEvidenceTypes,
      attachedPositionLabels:{},
      legacyObject:{objectType,objectId,objectVersion,versionKind,label:objectName||objectId},
    };
  }
  return {
    attachedPositionIds:[],
    attachedPositionVersions:{},
    evidenceTypes:selectedEvidenceTypes,
    attachedPositionLabels:{},
  };
}

function sessionMatchesSeed(session:RagChatSession,seed:SessionSeed){
  const leftLegacy=session.legacyObject;
  const rightLegacy=seed.legacyObject;
  const legacyMatches=leftLegacy?.objectType===rightLegacy?.objectType
    &&leftLegacy?.objectId===rightLegacy?.objectId
    &&leftLegacy?.objectVersion===rightLegacy?.objectVersion
    &&leftLegacy?.versionKind===rightLegacy?.versionKind;
  return sameStringArray(session.attachedPositionIds,seed.attachedPositionIds)
    &&sameNumberRecord(session.attachedPositionVersions,seed.attachedPositionVersions)
    &&sameStringArray(session.evidenceTypes,seed.evidenceTypes)
    &&(rightLegacy?Boolean(legacyMatches):!leftLegacy);
}

function failureMeta(code:string):{title:string;description:string;tone:'error'|'warning'}{
  if(code==='PERMISSION_DENIED')return {title:'权限不足',description:'当前账号没有查看这组正式来源的权限。',tone:'warning'};
  if(code==='RAG_EVIDENCE_DISABLED')return {title:'证据问答尚未启用',description:'当前服务容器未启用证据问答能力，请联系管理员检查服务配置。',tone:'warning'};
  if(code==='EVIDENCE_INDEX_NOT_READY')return {title:'检索索引尚未就绪',description:'当前图谱版本的检索索引仍在准备中，请稍后重试。',tone:'warning'};
  if(code==='EVIDENCE_RESPONSE_VERSION_SCOPE_INVALID'||code==='EVIDENCE_RESPONSE_CONTRACT_INVALID')return {title:'证据响应版本校验失败',description:'证据响应未通过岗位与 GraphVersion 校验，请稍后重试。',tone:'error'};
  if(code.includes('MISMATCH'))return {title:'版本或配置不一致',description:'本次请求的岗位版本与正式来源不一致，请重新选择岗位上下文。',tone:'error'};
  if(code.includes('UNAVAILABLE')||code.includes('TIMEOUT')||code.includes('RATE_LIMITED')||code.includes('CONNECTION_FAILED')||code.includes('API_KEY_MISSING'))return {title:'模型服务暂时不可用',description:'问答服务没有在规定时间内完成，请稍后重试。',tone:'error'};
  return {title:'证据问答未完成',description:'系统没有返回可用的回答，请稍后重试。',tone:'error'};
}

function toChatError(reason:unknown):RagChatError{
  const error=reason as Partial<ApiError>;
  return {
    status:typeof error.status==='number'?error.status:500,
    message:error.errorCode?failureMeta(error.errorCode).description:(error.message||'请求未完成，请稍后重试。'),
    traceId:error.traceId,
    errorCode:error.errorCode,
  };
}

function sourceGroup(reference:RAGEvidenceReferenceV1):{key:string;label:string}{
  const sourceType=reference.source_object_type.toLowerCase();
  if(sourceType==='source_jd'||sourceType==='jd_evidence'||sourceType.includes('jd'))return {key:'jd_evidence',label:'岗位证据'};
  if(sourceType==='source_cv'||sourceType==='cv_evidence'||sourceType.includes('cv'))return {key:'cv_evidence',label:'简历证据'};
  if(sourceType.includes('graph')||sourceType.includes('kg'))return {key:'graph_evidence',label:'岗位图谱'};
  if(sourceType.includes('matching'))return {key:'matching_evidence',label:'匹配分析'};
  if(sourceType.includes('trend'))return {key:'trend_evidence',label:'趋势分析'};
  if(sourceType.includes('discovery')||sourceType.includes('emerging'))return {key:'discovery_evidence',label:'新兴岗位'};
  if(sourceType.includes('gap'))return {key:'gap_evidence',label:'差距分析'};
  if(sourceType.includes('learning'))return {key:'learning_path_evidence',label:'学习路径'};
  if(sourceType.includes('review'))return {key:'review_decision_evidence',label:'审核决策'};
  const label=evidenceTypeLabels[reference.source_object_type as EvidenceTypeScope]||objectTypeLabels[reference.source_object_type]||'其他来源';
  return {key:sourceType||'other_evidence',label:label.replace(/证据$/,'')};
}

function groupEvidenceReferences(references:RAGEvidenceReferenceV1[]):EvidenceSourceGroup[]{
  const groups=new Map<string,EvidenceSourceGroup>();
  references.forEach(reference=>{
    const {key,label}=sourceGroup(reference);
    const current=groups.get(key);
    if(current)current.references.push(reference);
    else groups.set(key,{key,label,references:[reference]});
  });
  return [...groups.values()];
}

function InlineStatus({tone='neutral',title,description}:{tone?:InlineStatusTone;title:string;description?:string}){
  return <div className={'rag-inline-status rag-inline-status-'+tone} role="status">
    <span className="rag-inline-status-dot" aria-hidden="true"/>
    <div className="rag-inline-status-copy">
      <Typography.Text strong>{title}</Typography.Text>
      {description&&<Typography.Text type="secondary">{description}</Typography.Text>}
    </div>
  </div>;
}

function sourceTextKind(reference:RAGEvidenceReferenceV1):'jd'|'cv'|null{
  const sourceType=reference.source_object_type.toLowerCase();
  if(sourceType==='source_jd'||sourceType==='jd_evidence'||sourceType.includes('jd'))return 'jd';
  if(sourceType==='validated_cv_snapshot'||sourceType==='source_cv'||sourceType==='cv_evidence'||sourceType==='cv_profile'||sourceType==='resume')return 'cv';
  return null;
}

function OriginalEvidenceLocator({reference,onBack}:{reference:RAGEvidenceReferenceV1;onBack:()=>void}){
  const [resolution,setResolution]=useState<EvidenceCitationResolution>();
  const [rawText,setRawText]=useState('');
  const [citationLoading,setCitationLoading]=useState(false);
  const [rawTextLoading,setRawTextLoading]=useState(false);
  const group=sourceGroup(reference);
  const textKind=sourceTextKind(reference);
  const canLoadRawText=textKind!==null;
  const canResolve=canLoadRawText
    &&reference.source_object_type!=='position_profile'
    &&!reference.evidence_id.startsWith('position-profile:');
  useEffect(()=>{
    let active=true;
    setResolution(undefined);
    setRawText('');
    setCitationLoading(canResolve);
    setRawTextLoading(canLoadRawText);
    if(canResolve){
      resolveEvidenceCitation(citationRequest(reference))
        .then(value=>{if(active)setResolution(value)})
        .catch(()=>undefined)
        .finally(()=>{if(active)setCitationLoading(false)});
    }
    if(textKind==='jd'){
      getJD(reference.source_document_id)
        .then(value=>{if(active)setRawText(value.raw_text)})
        .catch(()=>undefined)
        .finally(()=>{if(active)setRawTextLoading(false)});
    }else if(textKind==='cv'){
      listMyResumes()
        .then(values=>{
          const sourceId=reference.source_object_id;
          const resume=values.find(item=>[item.resume_id,item.validated_cv_snapshot_id,item.source_cv_version_id]
            .filter((value):value is string=>typeof value==='string')
            .some(value=>value===sourceId||value===reference.source_document_id));
          if(active&&resume)setRawText(resume.raw_text);
        })
        .catch(()=>undefined)
        .finally(()=>{if(active)setRawTextLoading(false)});
    }
    return ()=>{active=false};
  },[canLoadRawText,canResolve,reference,textKind]);
  const quote=resolution?.highlight_text||reference.quote||'该来源没有返回可展示的片段。';
  const start=resolution?.start??reference.location_start;
  const end=resolution?.end??reference.location_end;
  const hasRange=typeof start==='number'&&typeof end==='number'&&start>=0&&end>=start;
  const contextStart=hasRange&&rawText?Math.max(0,start!-180):0;
  const contextEnd=hasRange&&rawText?Math.min(rawText.length,end!+260):0;
  const hasContext=Boolean(rawText&&hasRange&&end!<=rawText.length);
  const loading=citationLoading||rawTextLoading;
  return <section className="rag-original-locator" aria-label="来源上下文">
    <div className="rag-original-locator-toolbar">
      <Button type="text" size="small" icon={<LeftOutlined/>} onClick={onBack}>返回来源</Button>
    </div>
    <div className="rag-original-context-heading">
      <Typography.Text strong>{group.label}</Typography.Text>
      <Typography.Text type="secondary">相关来源上下文</Typography.Text>
    </div>
    {loading&&<div className="rag-original-locator-status"><Spin size="small"/>正在加载来源上下文…</div>}
    <div className="rag-original-quote">
      {!loading&&hasContext
        ?<blockquote>
          {contextStart>0&&'…'}
          <span>{rawText.slice(contextStart,start!)}</span><mark>{rawText.slice(start!,end!)}</mark><span>{rawText.slice(end!,contextEnd)}</span>
          {contextEnd<rawText.length&&'…'}
        </blockquote>
        :!loading&&<blockquote><mark>{quote}</mark></blockquote>}
    </div>
    {!loading&&!hasContext&&<Typography.Text type="secondary" className="rag-original-locator-note">当前来源没有返回可展开的连续原文，仅展示本次引用片段。</Typography.Text>}
  </section>;
}

function CitationItem({reference,onLocate}:{reference:RAGEvidenceReferenceV1;onLocate:(reference:RAGEvidenceReferenceV1)=>void}){
  const group=sourceGroup(reference);
  const canLocate=sourceTextKind(reference)!==null;
  return <article className="rag-citation-item">
    <div className="rag-citation-item-head">
      <span>{group.label}</span>
    </div>
    <blockquote>{reference.quote||'该引用未提供原文片段。'}</blockquote>
    {canLocate&&<div className="rag-citation-action"><Button type="link" size="small" onClick={()=>onLocate(reference)}>查看上下文</Button></div>}
  </article>;
}

function EntailmentList({items}:{items:NonNullable<EvidenceRAGResponseV1['entailment']>}){
  return <div className="rag-entailment-list">
    {items.map((item,index)=><div key={item.claim+'-'+index} className="rag-entailment-item">
      <div className="rag-entailment-item-head">
        <Tag>{entailmentLabels[item.relation]}</Tag>
        {item.dimension&&<Tag>{item.dimension}</Tag>}
      </div>
      <Typography.Text strong>{item.claim}</Typography.Text>
      <Typography.Text type="secondary">{item.reason}</Typography.Text>
    </div>)}
  </div>;
}

function ResponseDetails({response}:{response:EvidenceRAGResponseV1}){
  const statusLabel=response.status==='answered'?'已完成':response.status==='insufficient_evidence'?'证据不足':'未完成';
  return <div className="rag-response-details">
    {response.entailment?.length?<section className="rag-response-detail-section">
      <div className="rag-response-detail-heading"><span>证据关系</span><Typography.Text type="secondary">{response.entailment.length} 条</Typography.Text></div>
      <EntailmentList items={response.entailment}/>
    </section>:null}
    <section className="rag-response-detail-section">
      <div className="rag-response-detail-heading"><span>运行信息</span><Typography.Text type="secondary">仅解释已有证据</Typography.Text></div>
      <Descriptions size="small" column={{xs:1,md:2}} items={[
        {key:'status',label:'回答状态',children:statusLabel},
        {key:'mode',label:'回答方式',children:'仅解释正式来源'},
        {key:'sources',label:'来源数量',children:response.references.length+' 条'},
      ]}/>
    </section>
  </div>;
}

function EvidenceSources({references,onOpen}:{references:RAGEvidenceReferenceV1[];onOpen:(references:RAGEvidenceReferenceV1[])=>void}){
  const groups=useMemo(()=>groupEvidenceReferences(references),[references]);
  const visibleGroups=groups.slice(0,3);
  const hiddenCount=groups.slice(3).reduce((total,group)=>total+group.references.length,0);
  return <div className="rag-evidence-sources" aria-label="回答来源">
    <Typography.Text className="rag-evidence-sources-label">来源</Typography.Text>
    <div className="rag-source-chip-list">
      {visibleGroups.map(group=><Button
        key={group.key}
        type="text"
        size="small"
        className="rag-source-chip"
        aria-label={group.label+' '+group.references.length+' 条来源'}
        onClick={()=>onOpen(group.references)}
      >
        <span>{group.label}</span><strong>{group.references.length}</strong>
      </Button>)}
      {hiddenCount>0&&<Button type="text" size="small" className="rag-source-chip rag-source-chip-more" aria-label={'查看其余 '+hiddenCount+' 条来源'} onClick={()=>onOpen(references)}>+{hiddenCount}</Button>}
      <Button type="text" size="small" className="rag-source-all" aria-label={'查看全部来源 '+references.length+' 条'} title={'查看全部来源 '+references.length+' 条'} onClick={()=>onOpen(references)}>
        <RightOutlined/>
      </Button>
    </div>
  </div>;
}

function EvidenceDrawer({state,onClose}:{state:EvidenceDrawerState|null;onClose:()=>void}){
  const references=state?.references||[];
  const [locatorReference,setLocatorReference]=useState<RAGEvidenceReferenceV1|null>(null);
  useEffect(()=>{setLocatorReference(null)},[state]);
  return <Drawer
    className="rag-evidence-drawer"
    placement="right"
    size="large"
    title={<div className="rag-evidence-drawer-title"><span>{state?.title||'来源证据'}</span>{references.length>0&&<Typography.Text type="secondary">{references.length} 条</Typography.Text>}</div>}
    open={Boolean(state)}
    onClose={onClose}
    destroyOnHidden
  >
    {locatorReference
      ?<OriginalEvidenceLocator reference={locatorReference} onBack={()=>setLocatorReference(null)}/>
      :<>
        {state?.response&&<ResponseDetails response={state.response}/>}
        {references.length>0?<>
          <Typography.Paragraph className="rag-evidence-drawer-intro" type="secondary">
            以下内容来自本次回答实际使用的正式来源。
          </Typography.Paragraph>
          <div className="rag-evidence-drawer-list">
            {references.map((reference,index)=><CitationItem key={reference.evidence_id+'-'+index} reference={reference} onLocate={setLocatorReference}/>)}
          </div>
        </>:state?.response?null:<div className="rag-drawer-empty">暂无来源</div>}
      </>}
  </Drawer>;
}

function MessageActions({answer,onRetry,onOpenDetails,retryLabel='重新生成',disabled=false}:{answer?:string|null;onRetry:()=>void;onOpenDetails?:()=>void;retryLabel?:string;disabled?:boolean}){
  const {message:messageApi}=App.useApp();
  const [copied,setCopied]=useState(false);

  useEffect(()=>{
    if(!copied)return;
    const timer=window.setTimeout(()=>setCopied(false),2200);
    return ()=>window.clearTimeout(timer);
  },[copied]);

  const copyAnswer=async()=>{
    if(!answer)return;
    if(!navigator.clipboard){
      messageApi.error('当前环境不支持复制');
      return;
    }
    try{
      await navigator.clipboard.writeText(answer);
      setCopied(true);
    }catch{
      messageApi.error('复制失败，请手动选择文本');
    }
  };

  return <div className="rag-message-actions">
    {answer&&<Button type="text" size="small" disabled={disabled} icon={copied?<CheckOutlined/>:<CopyOutlined/>} onClick={()=>void copyAnswer()}>{copied?'已复制':'复制'}</Button>}
    <Button type="text" size="small" disabled={disabled} icon={<ReloadOutlined/>} onClick={onRetry}>{retryLabel}</Button>
    {onOpenDetails&&<Button type="text" size="small" disabled={disabled} onClick={onOpenDetails}>更多信息</Button>}
  </div>;
}

function AssistantMessage({response,onOpenEvidence,onOpenDetails,onRetry,disabled=false}:{response:EvidenceRAGResponseV1;onOpenEvidence:(references:RAGEvidenceReferenceV1[])=>void;onOpenDetails:(response:EvidenceRAGResponseV1)=>void;onRetry:()=>void;disabled?:boolean}){
  if(response.status==='answered'){
    return <div className="rag-assistant-content">
      <div className="rag-assistant-brand">JobPulse</div>
      <div className="rag-answer-text">{response.answer||'未返回回答内容。'}</div>
      <Typography.Text type="secondary" className="rag-answer-note">基于正式来源生成 · 仅解释已有数据</Typography.Text>
      {response.references.length>0&&<EvidenceSources references={response.references} onOpen={onOpenEvidence}/>}
      <MessageActions answer={response.answer} onRetry={onRetry} onOpenDetails={()=>onOpenDetails(response)} disabled={disabled}/>
    </div>;
  }

  if(response.status==='insufficient_evidence'){
    return <div className="rag-assistant-content">
      <div className="rag-assistant-brand">JobPulse</div>
      {response.error?.code==='EVIDENCE_INDEX_NOT_READY'
        ?<InlineStatus tone="warning" title="索引尚未就绪" description="当前图谱版本的检索索引仍在构建中，请稍后重试。"/>
        :<>
          <InlineStatus tone="warning" title="证据不足" description="当前正式来源不足以回答这个问题，因此不会构造没有依据的回答。"/>
          {response.error?.code==='EVIDENCE_NOT_FOUND'&&<Typography.Text type="secondary" className="rag-answer-note">如果这是刚发布的图谱，检索索引可能仍在后台建立。</Typography.Text>}
        </>}
      {response.references.length>0&&<EvidenceSources references={response.references} onOpen={onOpenEvidence}/>}
      <MessageActions onRetry={onRetry} onOpenDetails={()=>onOpenDetails(response)} disabled={disabled}/>
    </div>;
  }

  return <div className="rag-assistant-content">
    <div className="rag-assistant-brand">JobPulse</div>
    {(()=>{
      const meta=response.error?failureMeta(response.error.code):{title:'问答未完成',description:'未返回错误详情。',tone:'error' as const};
      return <InlineStatus tone={meta.tone} title={meta.title} description={response.error?.message?localizeSystemMessage(response.error.message):meta.description}/>;
    })()}
    <MessageActions onRetry={onRetry} onOpenDetails={()=>onOpenDetails(response)} retryLabel="重试" disabled={disabled}/>
  </div>;
}

function ChatMessage({message,onOpenEvidence,onOpenDetails,onRetry,loading=false,disabled=false}:{message?:ChatMessage;onOpenEvidence:(references:RAGEvidenceReferenceV1[])=>void;onOpenDetails:(response:EvidenceRAGResponseV1)=>void;onRetry:(question:string)=>void;loading?:boolean;disabled?:boolean}){
  if(loading)return <article className="rag-message rag-message-assistant" aria-live="polite">
    <div className="rag-assistant-content">
      <div className="rag-assistant-brand">JobPulse</div>
      <div className="rag-typing"><span className="rag-typing-dots" aria-hidden="true"><i/><i/><i/></span><Typography.Text type="secondary">正在检索正式证据…</Typography.Text></div>
    </div>
  </article>;

  if(!message)return null;
  if(message.role==='user')return <article className="rag-message rag-message-user">
    <div className="rag-user-bubble">{message.text}</div>
  </article>;

  return <article className="rag-message rag-message-assistant">
    {message.error
      ?<div className="rag-assistant-content">
        <div className="rag-assistant-brand">JobPulse</div>
        {(()=>{
          const meta=message.error.errorCode?failureMeta(message.error.errorCode):{title:'问答请求未完成',description:'请求未完成，请稍后重试。',tone:'error' as const};
          return <InlineStatus tone={meta.tone} title={meta.title} description={message.error.message?localizeSystemMessage(message.error.message):meta.description}/>;
        })()}
        <MessageActions onRetry={()=>onRetry(message.question)} retryLabel="重试" disabled={disabled}/>
      </div>
      :<AssistantMessage response={message.response} onOpenEvidence={onOpenEvidence} onOpenDetails={onOpenDetails} onRetry={()=>onRetry(message.question)} disabled={disabled}/>}
  </article>;
}

function ContextChips({session,positionOptions,onRemovePosition,onRemoveEvidence,onRemoveLegacy,disabled=false}:{session:RagChatSession;positionOptions:Position[];onRemovePosition:(positionId:string)=>void;onRemoveEvidence:(evidenceType:EvidenceTypeScope)=>void;onRemoveLegacy:()=>void;disabled?:boolean}){
  const positionLabels=session.attachedPositionIds.map(positionId=>({
    id:positionId,
    label:positionOptions.find(item=>item.position_id===positionId)?.name||session.attachedPositionLabels[positionId]||positionId,
  }));
  const selectedEvidenceTypes=session.evidenceTypes.filter(value=>value!=='all');
  const showEvidenceChips=!sameStringArray(selectedEvidenceTypes,DEFAULT_EVIDENCE_TYPES);
  return <div className="rag-context-chips" aria-label="当前上下文">
    {session.legacyObject&&<Tag className="rag-context-chip rag-context-chip-object" closable={!disabled} onClose={onRemoveLegacy}>
      {(objectTypeLabels[session.legacyObject.objectType]||'业务对象')+' · '+session.legacyObject.label}
    </Tag>}
    {positionLabels.map(position=><Tag key={position.id} className="rag-context-chip" closable={!disabled} onClose={()=>onRemovePosition(position.id)}>
      {position.label}
    </Tag>)}
    {showEvidenceChips&&selectedEvidenceTypes.map(value=><Tag key={value} className="rag-context-chip rag-context-chip-evidence" closable={!disabled} onClose={()=>onRemoveEvidence(value)}>
      {evidenceTypeLabels[value]}
    </Tag>)}
  </div>;
}

function ContextAttachmentPicker({session,positionOptions,positionLoading,open,onOpenChange,onTogglePosition,onToggleEvidence,onRequestPositions,disabled=false}:{session:RagChatSession;positionOptions:Position[];positionLoading:boolean;open:boolean;onOpenChange:(open:boolean)=>void;onTogglePosition:(positionId:string,checked:boolean)=>void;onToggleEvidence:(evidenceType:EvidenceTypeScope,checked:boolean)=>void;onRequestPositions:()=>void;disabled?:boolean}){
  const [positionQuery,setPositionQuery]=useState('');
  const filteredPositions=useMemo(()=>{
    const normalized=positionQuery.trim().toLowerCase();
    if(!normalized)return positionOptions;
    return positionOptions.filter(item=>(item.name+' '+item.category_code).toLowerCase().includes(normalized));
  },[positionOptions,positionQuery]);
  const selectedPositionIds=session.attachedPositionIds;
  const content=<div className="rag-context-picker" role="dialog" aria-label="添加上下文">
    <div className="rag-context-picker-head">
      <div>
        <Typography.Text strong>添加上下文</Typography.Text>
        <Typography.Text type="secondary">正式岗位与证据范围</Typography.Text>
      </div>
    </div>
    <section className="rag-context-picker-section">
      <div className="rag-context-picker-section-head">
        <Typography.Text strong>标准岗位</Typography.Text>
        {selectedPositionIds.length>0&&<Typography.Text type="secondary">{selectedPositionIds.length} 个已选</Typography.Text>}
      </div>
      {session.legacyObject
        ?<Typography.Text className="rag-context-picker-note" type="secondary">当前入口已固定业务对象，岗位上下文请从标准岗位入口新建对话。</Typography.Text>
        :<>
          <Input
            allowClear
            value={positionQuery}
            prefix={<SearchOutlined/>}
            aria-label="搜索标准岗位"
            placeholder="搜索已发布岗位"
            onChange={event=>setPositionQuery(event.target.value)}
          />
          <div className="rag-context-picker-options">
            {positionLoading
              ?<div className="rag-context-picker-loading"><Spin size="small"/>正在加载已发布岗位</div>
              :filteredPositions.length>0
                ?filteredPositions.map(position=><label key={position.position_id} className="rag-context-option">
                  <Checkbox
                    checked={selectedPositionIds.includes(position.position_id)}
                    aria-label={position.name}
                    onChange={event=>onTogglePosition(position.position_id,event.target.checked)}
                  />
                    <span className="rag-context-option-copy">
                      <span>{position.name}</span>
                    <small>岗位领域：{domainText(position.category_code)} · {position.current_version_number>0?'当前图谱第 '+position.current_version_number+' 版':'暂无当前图谱版本'}</small>
                  </span>
                  {selectedPositionIds.includes(position.position_id)&&<CheckOutlined className="rag-context-option-check"/>}
                </label>)
                :<Typography.Text type="secondary">没有匹配的已发布岗位</Typography.Text>}
          </div>
        </>}
    </section>
    <section className="rag-context-picker-section">
      <div className="rag-context-picker-section-head">
        <Typography.Text strong>证据范围</Typography.Text>
        <Typography.Text type="secondary">可多选</Typography.Text>
      </div>
      <div className="rag-context-picker-evidence">
        {selectableEvidenceTypes.map(value=><label key={value} className="rag-context-option rag-context-option-evidence">
          <Checkbox
            checked={session.evidenceTypes.includes(value)}
            aria-label={evidenceTypeLabels[value]}
            onChange={event=>onToggleEvidence(value,event.target.checked)}
          />
          <span>{evidenceTypeLabels[value]}</span>
        </label>)}
      </div>
    </section>
  </div>;
  return <Popover
    trigger="click"
    placement="topLeft"
    open={open}
    onOpenChange={nextOpen=>{
      onOpenChange(nextOpen);
      if(nextOpen)onRequestPositions();
    }}
    content={content}
    destroyOnHidden
  >
    <Tooltip title="添加上下文" placement="top">
      <Button
        htmlType="button"
        type="text"
        shape="circle"
        className="rag-composer-plus"
        aria-label="添加上下文"
        disabled={disabled}
        icon={<PlusOutlined/>}
      />
    </Tooltip>
  </Popover>;
}

function ChatComposer({session,positionOptions,positionLoading,pickerOpen,onPickerOpenChange,onTogglePosition,onToggleEvidence,onRemovePosition,onRemoveEvidence,onRemoveLegacy,onRequestPositions,value,onChange,onSubmit,onStop,sendDisabled,loading,inputRef}:{session:RagChatSession;positionOptions:Position[];positionLoading:boolean;pickerOpen:boolean;onPickerOpenChange:(open:boolean)=>void;onTogglePosition:(positionId:string,checked:boolean)=>void;onToggleEvidence:(evidenceType:EvidenceTypeScope,checked:boolean)=>void;onRemovePosition:(positionId:string)=>void;onRemoveEvidence:(evidenceType:EvidenceTypeScope)=>void;onRemoveLegacy:()=>void;onRequestPositions:()=>void;value:string;onChange:(value:string)=>void;onSubmit:()=>void;onStop:()=>void;sendDisabled:boolean;loading:boolean;inputRef:RefObject<TextAreaRef|null>}){
  const canSubmit=!sendDisabled&&Boolean(value.trim());
  const showStop=loading;
  const [isLong,setIsLong]=useState(false);
  const measureRef=useRef<HTMLTextAreaElement|null>(null);
  const getTextArea=useCallback(
    ()=>document.querySelector<HTMLTextAreaElement>('.rag-composer-input'),
    [],
  );
  const updateWrappedState=useCallback(()=>{
    const node=getTextArea();
    const measure=measureRef.current;
    if(!node||!measure)return;
    const style=getComputedStyle(node);
    measure.style.fontFamily=style.fontFamily;
    measure.style.fontSize=style.fontSize;
    measure.style.fontWeight=style.fontWeight;
    measure.style.lineHeight=style.lineHeight;
    measure.style.letterSpacing=style.letterSpacing;
    measure.style.wordSpacing=style.wordSpacing;
    measure.style.whiteSpace=style.whiteSpace;
    measure.style.overflowWrap=style.overflowWrap;
    measure.style.wordBreak=style.wordBreak;
    setIsLong(measure.scrollHeight>measure.clientHeight+1);
  },[getTextArea]);
  useLayoutEffect(()=>{
    updateWrappedState();
  },[value,updateWrappedState]);
  useEffect(()=>{
    updateWrappedState();
    const node=getTextArea();
    const container=node?.closest('.rag-composer-input-row');
    if(!container||typeof ResizeObserver==='undefined')return;
    const observer=new ResizeObserver(updateWrappedState);
    observer.observe(container);
    return()=>observer.disconnect();
  },[getTextArea,updateWrappedState]);
  return <form className="rag-composer" aria-label="证据问答输入" onSubmit={event=>{
    event.preventDefault();
    if(canSubmit)onSubmit();
  }}>
    <ContextChips
      session={session}
      positionOptions={positionOptions}
      onRemovePosition={onRemovePosition}
      onRemoveEvidence={onRemoveEvidence}
      onRemoveLegacy={onRemoveLegacy}
      disabled={false}
    />
    <div className="rag-composer-input-row">
      <textarea
        ref={measureRef}
        className="rag-composer-measure"
        aria-hidden="true"
        tabIndex={-1}
        readOnly
        value={value}
      />
      <ContextAttachmentPicker
        session={session}
        positionOptions={positionOptions}
        positionLoading={positionLoading}
        open={pickerOpen}
        onOpenChange={onPickerOpenChange}
        onTogglePosition={onTogglePosition}
        onToggleEvidence={onToggleEvidence}
        onRequestPositions={onRequestPositions}
        disabled={false}
      />
      <Input.TextArea
        ref={inputRef}
        aria-label="问题"
        autoSize={{minRows:1,maxRows:5}}
        className={'rag-composer-input'+(isLong?' is-long':'')}
        value={value}
        onChange={event=>{
          onChange(event.target.value);
        }}
        placeholder={sendDisabled?'先通过 + 添加一个岗位上下文':'继续询问这个岗位…'}
        onPressEnter={event=>{
          if(!event.shiftKey){
            event.preventDefault();
            if(canSubmit)onSubmit();
          }
        }}
      />
      <Button
        htmlType={showStop?'button':'submit'}
        aria-label={showStop?'停止当前检索':'发送问题'}
        title={loading?'停止当前检索':'发送问题'}
        type="primary"
        shape="circle"
        className={showStop?'rag-composer-submit is-loading':'rag-composer-submit'}
        icon={showStop?<span className="rag-stop-mark" aria-hidden="true"/>:<ArrowUpOutlined/>}
        disabled={!loading&&!canSubmit}
        onClick={showStop?onStop:undefined}
      />
    </div>
  </form>;
}

function NewChatButton({onClick,disabled=false}:{onClick:()=>void;disabled?:boolean}){
  return <Button type="text" className="rag-new-chat-button" icon={<PlusOutlined/>} aria-label="新建对话" onClick={onClick} disabled={disabled}>新建对话</Button>;
}

function ChatHeader({sessionsCount,onNewChat,onOpenHistory,disabled=false}:{sessionsCount:number;onNewChat:()=>void;onOpenHistory:()=>void;disabled?:boolean}){
  return <header className="rag-chat-header">
    <div className="rag-chat-header-actions">
      <NewChatButton onClick={onNewChat} disabled={disabled}/>
      <Button type="text" className="rag-history-button" icon={<HistoryOutlined/>} onClick={onOpenHistory} disabled={disabled}>
        历史{sessionsCount>0&&<span className="rag-history-count">{sessionsCount}</span>}
      </Button>
    </div>
  </header>;
}

function sessionDateLabel(value:string){
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return '';
  return date.toLocaleDateString('zh-CN',{month:'numeric',day:'numeric'});
}

function sessionContextLabel(session:RagChatSession){
  if(session.legacyObject)return session.legacyObject.label||session.legacyObject.objectId;
  if(session.attachedPositionIds.length>0)return session.attachedPositionIds.length+' 个岗位';
  return '未添加岗位上下文';
}

function SessionHistoryDrawer({open,sessions,activeSessionId,onClose,onSelect,onDelete,onNewChat}:{open:boolean;sessions:RagChatSession[];activeSessionId:string;onClose:()=>void;onSelect:(sessionId:string)=>void;onDelete:(sessionId:string)=>void;onNewChat:()=>void}){
  const orderedSessions=[...sessions].sort((left,right)=>right.updatedAt.localeCompare(left.updatedAt));
  return <Drawer
    className="rag-session-history-drawer"
    placement="right"
    size={420}
    title={<div className="rag-session-history-title"><span>历史对话</span><Typography.Text type="secondary">{sessions.length} 个</Typography.Text></div>}
    open={open}
    onClose={onClose}
    destroyOnHidden
  >
    <NewChatButton onClick={onNewChat}/>
    <div className="rag-session-history-list" role="list">
      {orderedSessions.map(session=><div key={session.id} className={session.id===activeSessionId?'rag-session-history-item is-active':'rag-session-history-item'} role="listitem">
        <button type="button" className="rag-session-history-select" onClick={()=>onSelect(session.id)} aria-current={session.id===activeSessionId?'page':undefined}>
          <span className="rag-session-history-item-title">{session.title}</span>
          <span className="rag-session-history-item-meta">{sessionContextLabel(session)+' · '+session.evidenceTypes.filter(value=>value!=='all').length+' 类证据 · '+sessionDateLabel(session.updatedAt)}</span>
        </button>
        <Button type="text" danger className="rag-session-history-delete" aria-label={'删除 '+session.title} icon={<DeleteOutlined/>} onClick={()=>onDelete(session.id)}/>
      </div>)}
    </div>
  </Drawer>;
}

function EmptyState({session,onAddContext,onSuggestion}:{session:RagChatSession;onAddContext:()=>void;onSuggestion:(question:string)=>void}){
  const suggestions=['这个岗位的核心能力是什么？','这个岗位最近发生了哪些能力变化？','哪些能力是多数岗位共同要求的？'];
  return <div className="rag-empty-state">
    <div className="rag-empty-wordmark">JobPulse Evidence</div>
    <Typography.Paragraph type="secondary">基于已发布岗位图谱和正式证据进行连续问答</Typography.Paragraph>
    {!session.attachedPositionIds.length&&!session.legacyObject&&<Button type="text" className="rag-empty-context-button" icon={<PlusOutlined/>} onClick={onAddContext}>添加岗位上下文</Button>}
    <div className="rag-suggestion-list">
      {suggestions.map(suggestion=><button key={suggestion} type="button" className="rag-suggestion" onClick={()=>onSuggestion(suggestion)}>{suggestion}</button>)}
    </div>
  </div>;
}

function ChatViewport({session,threadViewportRef,messages,loading,indexStatus,onScroll,onScrollToBottom,onOpenEvidence,onOpenDetails,onRetry,showScrollButton,disabled,onAddContext,onSuggestion}:{session:RagChatSession;threadViewportRef:RefObject<HTMLDivElement|null>;messages:ChatMessage[];loading:boolean;indexStatus?:RagIndexStatus;onScroll:()=>void;onScrollToBottom:()=>void;onOpenEvidence:(references:RAGEvidenceReferenceV1[])=>void;onOpenDetails:(response:EvidenceRAGResponseV1)=>void;onRetry:(question:string)=>void;showScrollButton:boolean;disabled:boolean;onAddContext:()=>void;onSuggestion:(question:string)=>void}){
  return <div className="rag-chat-viewport-shell">
    <main ref={threadViewportRef} className="rag-chat-scroll" role="log" aria-label="问答消息" onScroll={onScroll}>
      <div className="rag-chat-column">
        {indexStatus?.status==='running'&&indexStatus.expected_count?<div className="rag-index-status">
          <div className="rag-index-status-head"><span><Spin size="small"/>检索索引构建中</span><Typography.Text type="secondary">{indexStatus.indexed_count} / {indexStatus.expected_count} 条</Typography.Text></div>
          <Progress percent={Math.min(99,Math.round((indexStatus.indexed_count||0)/indexStatus.expected_count*100))} showInfo={false} status="active"/>
        </div>:null}
        {indexStatus?.status==='disabled'&&<div className="rag-index-alert"><InlineStatus
          tone="warning"
          title="证据问答尚未启用"
          description="当前服务容器未启用证据问答能力，请联系管理员检查服务配置。"
        /></div>}
        {messages.length===0
          ?<EmptyState session={session} onAddContext={onAddContext} onSuggestion={onSuggestion}/>
          :messages.map(message=><ChatMessage
            key={message.id}
            message={message}
            onOpenEvidence={onOpenEvidence}
            onOpenDetails={onOpenDetails}
            onRetry={onRetry}
            disabled={disabled}
          />)}
        {loading&&<ChatMessage
          loading
          onOpenEvidence={onOpenEvidence}
          onOpenDetails={onOpenDetails}
          onRetry={onRetry}
        />}
      </div>
    </main>
    {showScrollButton&&<Button
      className="rag-scroll-to-bottom"
      shape="circle"
      aria-label="滚动到底部"
      title="滚动到底部"
      icon={<DownOutlined/>}
      onClick={onScrollToBottom}
    />}
  </div>;
}

export function EvidenceAssistant(){
  const {user}=useAuth();
  const userId=user?.user_id;
  // The outer SPA page may stay mounted across auth changes, so keep every
  // RAG session state inside a user-keyed subtree.
  return <EvidenceAssistantSession key={userId??'logged-out'} userId={userId}/>;
}

function EvidenceAssistantSession({userId}:{userId?:string}){
  const [searchParams]=useSearchParams();
  const objectTypeParam=searchParams.get('objectType')||'';
  const objectId=searchParams.get('objectId')||'';
  const objectVersion=searchParams.get('objectVersion')||'';
  const versionKind=searchParams.get('versionKind')||'';
  const initialObjectName=searchParams.get('objectName')||'';
  const objectType=allowedObjectTypes.includes(objectTypeParam as BusinessObjectType)?objectTypeParam as BusinessObjectType:'';
  const urlEvidenceTypesParam=searchParams.get('evidenceTypes')||'';
  const urlEvidenceTypes=useMemo(()=>urlEvidenceTypesParam.split(',').filter((value):value is EvidenceTypeScope=>allowedEvidenceTypes.includes(value as EvidenceTypeScope)),[urlEvidenceTypesParam]);
  const initialSeed=useMemo(()=>createInitialSeed(objectType,objectId,objectVersion,versionKind,initialObjectName,urlEvidenceTypes),[objectType,objectId,objectVersion,versionKind,initialObjectName,urlEvidenceTypes]);
  const initialContextKey=objectType&&objectId?[objectType,objectId,objectVersion,versionKind,urlEvidenceTypes.join(',')].join('|'):'';
  const [chatState,setChatState]=useState<RagChatState>(()=>readChatState(initialSeed,userId));
  const [question,setQuestion]=useState('');
  const [runningBySession,setRunningBySession]=useState<Record<string,number>>({});
  const [positionOptions,setPositionOptions]=useState<Position[]>([]);
  const [positionLoading,setPositionLoading]=useState(false);
  const [positionsLoaded,setPositionsLoaded]=useState(false);
  const [indexStatus,setIndexStatus]=useState<RagIndexStatus>();
  const [drawerState,setDrawerState]=useState<EvidenceDrawerState|null>(null);
  const [historyOpen,setHistoryOpen]=useState(false);
  const [contextPickerOpen,setContextPickerOpen]=useState(false);
  const [showScrollButton,setShowScrollButton]=useState(false);
  const threadViewportRef=useRef<HTMLDivElement|null>(null);
  const composerInputRef=useRef<TextAreaRef|null>(null);
  const activeRequestsRef=useRef(new Map<string,ActiveRagRequest>());
  const autoRetryKeyRef=useRef('');
  const syncedContextKeyRef=useRef('');

  const activeSession=chatState.sessions.find(session=>session.id===chatState.activeSessionId)||chatState.sessions[0];
  const selectedPositionIds=useMemo(()=>activeSession?.attachedPositionIds||[],[activeSession?.attachedPositionIds]);
  const selectedPositions=useMemo(()=>selectedPositionIds
    .map(id=>{
      const position=positionOptions.find(item=>item.position_id===id);
      const graphVersionId=activeSession?.attachedPositionVersions[id]??position?.current_version_id;
      return position&&graphVersionId!=null?{...position,ragVersionId:graphVersionId}:null;
    })
    .filter((item):item is Position & {ragVersionId:number}=>Boolean(item)),
  [selectedPositionIds,positionOptions,activeSession?.attachedPositionVersions]);
  const primaryPosition=selectedPositions[0];
  const legacyObject=activeSession?.legacyObject;
  const requestContexts=useMemo(()=>{
    if(legacyObject)return [{
      object_type:legacyObject.objectType,
      object_id:legacyObject.objectId,
      object_version:legacyObject.objectVersion||null,
    }];
    return selectedPositions.map(position=>({
      object_type:'standard_position' as const,
      object_id:position.position_id,
      object_version:String(position.ragVersionId),
    }));
  },[legacyObject,selectedPositions]);
  const primaryContext=requestContexts[0];
  const allSelectedVersionsReady=Boolean(!legacyObject&&selectedPositionIds.length===selectedPositions.length);
  const contextReady=Boolean(
    activeSession
    &&primaryContext
    &&activeSession.evidenceTypes.length>0
    &&(legacyObject||allSelectedVersionsReady),
  );
  const sendDisabled=!contextReady||indexStatus?.status==='disabled';
  const indexPositionId=legacyObject?undefined:primaryPosition?.position_id;
  const indexVersionId=legacyObject?undefined:primaryPosition?.ragVersionId;
  const loading=Boolean(activeSession&&runningBySession[activeSession.id]>0);

  useEffect(()=>{
    try{
      if(typeof window!=='undefined'){
        LEGACY_RAG_CHAT_STORAGE_KEYS.forEach(key=>window.localStorage.removeItem(key));
      }
    }catch{
      // localStorage is an optional client persistence boundary.
    }
  },[]);

  useEffect(()=>{
    if(typeof window==='undefined'||!userId)return;
    try{
      window.localStorage.setItem(ragHistoryStorageKey(userId),JSON.stringify(chatState));
    }catch{
      // localStorage is an optional client persistence boundary.
    }
  },[chatState,userId]);

  useEffect(()=>{
    if(!initialContextKey||syncedContextKeyRef.current===initialContextKey)return;
    syncedContextKeyRef.current=initialContextKey;
    setChatState(current=>{
      const currentSession=current.sessions.find(session=>session.id===current.activeSessionId);
      if(currentSession&&sessionMatchesSeed(currentSession,initialSeed))return current;
      const seeded=createSession(initialSeed);
      return {activeSessionId:seeded.id,sessions:[...current.sessions,seeded]};
    });
  },[initialContextKey,initialSeed]);

  const updateSession=useCallback((sessionId:string,update:(session:RagChatSession)=>RagChatSession)=>{
    setChatState(current=>({
      ...current,
      sessions:current.sessions.map(session=>{
        if(session.id!==sessionId)return session;
        return {...update(session),updatedAt:nowIso()};
      }),
    }));
  },[]);

  const beginRequest=useCallback((requestId:string,sessionId:string,controller:AbortController)=>{
    activeRequestsRef.current.set(requestId,{sessionId,controller});
    setRunningBySession(current=>({...current,[sessionId]:(current[sessionId]||0)+1}));
  },[]);

  const finishRequest=useCallback((requestId:string)=>{
    const request=activeRequestsRef.current.get(requestId);
    if(!request)return;
    activeRequestsRef.current.delete(requestId);
    setRunningBySession(current=>{
      const nextCount=Math.max(0,(current[request.sessionId]||0)-1);
      if(nextCount===0){
        const next={...current};
        delete next[request.sessionId];
        return next;
      }
      return {...current,[request.sessionId]:nextCount};
    });
  },[]);

  const abortSessionRequests=useCallback((sessionId:string)=>{
    activeRequestsRef.current.forEach(request=>{
      if(request.sessionId===sessionId)request.controller.abort();
    });
  },[]);

  const loadPositions=useCallback(async()=>{
    if(positionsLoaded||positionLoading)return;
    setPositionLoading(true);
    try{
      const positions=await listPublishedPositions();
      setPositionOptions(positions);
      setPositionsLoaded(true);
    }catch{
      setPositionOptions([]);
      setPositionsLoaded(true);
    }finally{
      setPositionLoading(false);
    }
  },[positionLoading,positionsLoaded]);

  useEffect(()=>{
    if(selectedPositionIds.length>0)void loadPositions();
  },[selectedPositionIds,loadPositions]);

  const scrollToBottom=useCallback(()=>{
    const node=threadViewportRef.current;
    if(!node)return;
    node.scrollTop=node.scrollHeight;
    setShowScrollButton(false);
  },[]);

  const handleThreadScroll=useCallback(()=>{
    const node=threadViewportRef.current;
    if(!node)return;
    setShowScrollButton(node.scrollHeight-node.scrollTop-node.clientHeight>96);
  },[]);

  useEffect(()=>{
    const frame=window.requestAnimationFrame(()=>scrollToBottom());
    return ()=>window.cancelAnimationFrame(frame);
  },[activeSession?.id,activeSession?.messages,loading,scrollToBottom]);

  useEffect(()=>()=>{
    activeRequestsRef.current.forEach(request=>request.controller.abort());
    activeRequestsRef.current.clear();
  },[]);

  useEffect(()=>{
    if(!indexPositionId||indexVersionId==null){
      setIndexStatus(undefined);
      return;
    }
    let active=true;
    let timer:ReturnType<typeof window.setTimeout>|undefined;
    const check=async()=>{
      try{
        const status=await getRagIndexStatus({business_object_type:'standard_position',business_object_id:indexPositionId,graph_version_id:indexVersionId});
        if(!active)return;
        setIndexStatus(status);
        if(status.status!=='running')return;
      }catch{
        if(active)setIndexStatus(undefined);
        return;
      }
      if(active)timer=window.setTimeout(()=>{void check()},4000);
    };
    void check();
    return ()=>{active=false;if(timer)window.clearTimeout(timer)};
  },[indexPositionId,indexVersionId]);

  const ask=useCallback(async(text:string,retry=false)=>{
    if(sendDisabled||!activeSession||!primaryContext)return;
    const trimmed=text.trim();
    if(!trimmed)return;
    const sessionAtStart=activeSession;
    const legacyObjectAtStart=legacyObject;
    const requestContextsAtStart=[...requestContexts];
    const primaryContextAtStart=requestContextsAtStart[0];
    const primaryPositionAtStart=selectedPositions[0];
    if(!primaryContextAtStart)return;
    const conversationHistory=sessionAtStart.messages.slice(-12).reduce<EvidenceConversationTurn[]>((turns,message)=>{
      if(message.role==='user')turns.push({role:'user',text:message.text});
      else if(message.response?.answer?.trim())turns.push({role:'assistant',text:message.response.answer.trim()});
      return turns;
    },[]);
    if(!retry){
      const userMessage:ChatMessage={id:newId('rag-message'),createdAt:nowIso(),role:'user',text:trimmed};
      updateSession(sessionAtStart.id,current=>({
        ...current,
        title:current.messages.length===0?sessionTitle(trimmed):current.title,
        messages:[...current.messages,userMessage],
      }));
      setQuestion('');
    }
    const requestController=new AbortController();
    const requestId=newId('rag-request');
    beginRequest(requestId,sessionAtStart.id,requestController);
    const isCurrentRequest=()=>activeRequestsRef.current.has(requestId);
    const businessObjectLabel=legacyObjectAtStart?.label||(
      selectedPositions.length===1?selectedPositions[0].name:
        selectedPositions.length>1?selectedPositions.length+' 个标准岗位':null
    );
    const query:EvidenceRAGQueryV1={
      contract_version:'evidence-rag-query.v1',
      business_object:primaryContextAtStart,
      business_object_label:businessObjectLabel,
      ...(requestContextsAtStart.length>1?{business_objects:requestContextsAtStart}:{}),
      conversation_history:conversationHistory,
      query_text:trimmed,
      evidence_types:sessionAtStart.evidenceTypes,
      version_scope:requestContextsAtStart.length>1?'multi_object':'single_object',
      ...(requestContextsAtStart.length===1?{
        graph_version_id:legacyObjectAtStart
          ?legacyObjectAtStart.versionKind==='graph_version_id'&&Number.isInteger(Number(legacyObjectAtStart.objectVersion))?Number(legacyObjectAtStart.objectVersion):null
          :primaryPositionAtStart?.ragVersionId??null,
        graph_version:legacyObjectAtStart?.versionKind==='graph_version'?legacyObjectAtStart.objectVersion:null,
        business_version:legacyObjectAtStart?.versionKind==='business_version'?legacyObjectAtStart.objectVersion:null,
      }:{}),
    };
    try{
      const response=await queryEvidenceRAG(query,requestController.signal);
      if(!isCurrentRequest())return;
      updateSession(sessionAtStart.id,current=>({
        ...current,
        messages:[...current.messages,{id:newId('rag-message'),createdAt:nowIso(),role:'assistant',question:trimmed,response}],
      }));
    }catch(reason){
      if((reason instanceof DOMException&&reason.name==='AbortError')||!isCurrentRequest())return;
      const error=toChatError(reason);
      updateSession(sessionAtStart.id,current=>({
        ...current,
        messages:[...current.messages,{id:newId('rag-message'),createdAt:nowIso(),role:'assistant',question:trimmed,error}],
      }));
    }finally{
      finishRequest(requestId);
    }
  },[activeSession,beginRequest,finishRequest,legacyObject,primaryContext,requestContexts,selectedPositions,sendDisabled,updateSession]);

  const stop=useCallback(()=>{
    if(!activeSession)return;
    activeRequestsRef.current.forEach(request=>{
      if(request.sessionId===activeSession.id)request.controller.abort();
    });
  },[activeSession]);

  useEffect(()=>{
    const last=activeSession?.messages[activeSession.messages.length-1];
    if(
      indexStatus?.status!=='completed'
      ||last?.role!=='assistant'
      ||!last.response
      ||last.response.status!=='insufficient_evidence'
      ||!['EVIDENCE_INDEX_NOT_READY','EVIDENCE_NOT_FOUND'].includes(last.response.error?.code??'')
      ||loading
    )return;
    const retryKey=activeSession.id+':'+last.question;
    if(autoRetryKeyRef.current===retryKey)return;
    autoRetryKeyRef.current=retryKey;
    const timer=window.setTimeout(()=>{void ask(last.question,true)},400);
    return ()=>window.clearTimeout(timer);
  },[activeSession,activeSession?.messages,ask,indexStatus?.status,loading]);

  const createNewChat=useCallback(()=>{
    const session=createSession();
    setChatState(current=>({activeSessionId:session.id,sessions:[...current.sessions,session]}));
    setQuestion('');
    setDrawerState(null);
    setHistoryOpen(false);
    setContextPickerOpen(false);
    setIndexStatus(undefined);
  },[]);

  const selectSession=useCallback((sessionId:string)=>{
    if(!chatState.sessions.some(session=>session.id===sessionId))return;
    setChatState(current=>({...current,activeSessionId:sessionId}));
    setQuestion('');
    setDrawerState(null);
    setContextPickerOpen(false);
    setHistoryOpen(false);
  },[chatState.sessions]);

  const deleteSession=useCallback((sessionId:string)=>{
    abortSessionRequests(sessionId);
    setChatState(current=>{
      const remaining=current.sessions.filter(session=>session.id!==sessionId);
      if(remaining.length===0){
        const fresh=createSession();
        return {activeSessionId:fresh.id,sessions:[fresh]};
      }
      const activeSessionId=current.activeSessionId===sessionId?remaining[0].id:current.activeSessionId;
      return {activeSessionId,sessions:remaining};
    });
    setQuestion('');
    setDrawerState(null);
    setContextPickerOpen(false);
  },[abortSessionRequests]);

  const togglePosition=useCallback((positionId:string,checked:boolean)=>{
    if(!activeSession)return;
    const position=positionOptions.find(item=>item.position_id===positionId);
    updateSession(activeSession.id,current=>{
      const attachedPositionIds=checked
        ?Array.from(new Set([...current.attachedPositionIds,positionId]))
        :current.attachedPositionIds.filter(id=>id!==positionId);
      const attachedPositionVersions={...current.attachedPositionVersions};
      const attachedPositionLabels={...current.attachedPositionLabels};
      if(position){
        attachedPositionLabels[positionId]=position.name;
        if(position.current_version_id!=null)attachedPositionVersions[positionId]=position.current_version_id;
      }
      if(!checked){
        delete attachedPositionLabels[positionId];
        delete attachedPositionVersions[positionId];
      }
      return {
        ...current,
        legacyObject:undefined,
        attachedPositionIds,
        attachedPositionVersions,
        attachedPositionLabels,
      };
    });
  },[activeSession,positionOptions,updateSession]);

  const toggleEvidence=useCallback((evidenceType:EvidenceTypeScope,checked:boolean)=>{
    if(!activeSession||evidenceType==='all')return;
    updateSession(activeSession.id,current=>{
      const evidenceTypes=checked
        ?Array.from(new Set([...current.evidenceTypes,evidenceType]))
        :current.evidenceTypes.filter(value=>value!==evidenceType);
      return {...current,evidenceTypes};
    });
  },[activeSession,updateSession]);

  const removePosition=useCallback((positionId:string)=>{
    if(!activeSession)return;
    updateSession(activeSession.id,current=>({
      ...current,
      attachedPositionIds:current.attachedPositionIds.filter(id=>id!==positionId),
      attachedPositionVersions:Object.fromEntries(Object.entries(current.attachedPositionVersions).filter(([id])=>id!==positionId)),
      attachedPositionLabels:Object.fromEntries(Object.entries(current.attachedPositionLabels).filter(([id])=>id!==positionId)),
    }));
  },[activeSession,updateSession]);

  const removeEvidence=useCallback((evidenceType:EvidenceTypeScope)=>{
    if(!activeSession||evidenceType==='all')return;
    updateSession(activeSession.id,current=>({...current,evidenceTypes:current.evidenceTypes.filter(value=>value!==evidenceType)}));
  },[activeSession,updateSession]);

  const removeLegacy=useCallback(()=>{
    if(!activeSession)return;
    updateSession(activeSession.id,current=>({...current,legacyObject:undefined}));
  },[activeSession,updateSession]);

  const selectSuggestion=useCallback((suggestion:string)=>{
    setQuestion(suggestion);
    window.requestAnimationFrame(()=>composerInputRef.current?.focus());
  },[]);

  if(!activeSession)return null;
  return <div className="page rag-page">
    <ChatHeader
      sessionsCount={chatState.sessions.length}
      onNewChat={createNewChat}
      onOpenHistory={()=>setHistoryOpen(true)}
    />
    <div className="rag-chat-workspace">
      <ChatViewport
        session={activeSession}
        threadViewportRef={threadViewportRef}
        messages={activeSession.messages}
        loading={loading}
        indexStatus={indexStatus}
        onScroll={handleThreadScroll}
        onScrollToBottom={scrollToBottom}
        onOpenEvidence={references=>setDrawerState({title:'来源证据',references})}
        onOpenDetails={response=>setDrawerState({title:'回答详情',references:response.references,response})}
        onRetry={text=>void ask(text,true)}
        showScrollButton={showScrollButton}
        disabled={false}
        onAddContext={()=>{setContextPickerOpen(true);void loadPositions()}}
        onSuggestion={selectSuggestion}
      />
      <div className="rag-composer-dock">
        <ChatComposer
          session={activeSession}
          positionOptions={positionOptions}
          positionLoading={positionLoading}
          pickerOpen={contextPickerOpen}
          onPickerOpenChange={setContextPickerOpen}
          onTogglePosition={togglePosition}
          onToggleEvidence={toggleEvidence}
          onRemovePosition={removePosition}
          onRemoveEvidence={removeEvidence}
          onRemoveLegacy={removeLegacy}
          onRequestPositions={()=>void loadPositions()}
          value={question}
          onChange={setQuestion}
          onSubmit={()=>void ask(question)}
          onStop={stop}
          sendDisabled={sendDisabled}
          loading={loading}
          inputRef={composerInputRef}
        />
      </div>
    </div>
    <EvidenceDrawer state={drawerState} onClose={()=>setDrawerState(null)}/>
    <SessionHistoryDrawer
      open={historyOpen}
      sessions={chatState.sessions}
      activeSessionId={activeSession.id}
      onClose={()=>setHistoryOpen(false)}
      onSelect={selectSession}
      onDelete={deleteSession}
      onNewChat={createNewChat}
    />
  </div>;
}
