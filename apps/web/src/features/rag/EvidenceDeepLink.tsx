/* eslint-disable react-refresh/only-export-components */
import {useEffect,useMemo,useState} from 'react';
import {Spin} from 'antd';
import {useSearchParams} from 'react-router-dom';
import {ApiError} from '../../shared/api';
import {ToastAlert as Alert} from '../../shared/components/States';
import {resolveEvidenceCitation} from './api';
import type {
  EvidenceCitationResolveRequest,
  EvidenceCitationResolution,
  RAGEvidenceReferenceV1,
} from './types';

type ResolutionState={
  requested:boolean;
  loading:boolean;
  resolution?:EvidenceCitationResolution;
  error?:ApiError;
};

const requestKey=(request:EvidenceCitationResolveRequest|null)=>request?JSON.stringify(request):'';

function useResolution(request:EvidenceCitationResolveRequest|null):ResolutionState{
  const key=requestKey(request);
  const [result,setResult]=useState<{key:string;resolution?:EvidenceCitationResolution;error?:ApiError}>({key:''});
  useEffect(()=>{
    if(!key)return;
    let active=true;
    const current=JSON.parse(key) as EvidenceCitationResolveRequest;
    resolveEvidenceCitation(current)
      .then(resolution=>{if(active)setResult({key,resolution})})
      .catch(reason=>{if(active)setResult({key,error:reason as ApiError})});
    return ()=>{active=false};
  },[key]);
  if(!request)return {requested:false,loading:false};
  if(result.key!==key)return {requested:true,loading:true};
  return {requested:true,loading:false,resolution:result.resolution,error:result.error};
}

export function citationRequest(reference:RAGEvidenceReferenceV1):EvidenceCitationResolveRequest{
  const request:EvidenceCitationResolveRequest={
    evidence_id:reference.evidence_id,
    source_version:reference.source_version,
  };
  if(reference.graph_version_id!=null)request.graph_version_id=reference.graph_version_id;
  else if(reference.graph_version)request.graph_version=reference.graph_version;
  else if(reference.business_version)request.business_version=reference.business_version;
  return request;
}

export function useCitationResolution(reference:RAGEvidenceReferenceV1){
  return useResolution(citationRequest(reference));
}

export function useEvidenceDeepLink(resourceId?:string):ResolutionState{
  const [searchParams]=useSearchParams();
  const evidenceId=searchParams.get('citationEvidenceId');
  const sourceVersion=searchParams.get('citationSourceVersion');
  const graphVersionId=Number(searchParams.get('citationGraphVersionId'));
  const graphVersion=searchParams.get('citationGraphVersion');
  const businessVersion=searchParams.get('citationBusinessVersion');
  const request:EvidenceCitationResolveRequest|null=evidenceId&&sourceVersion?{
      evidence_id:evidenceId,
      source_version:sourceVersion,
      ...(Number.isInteger(graphVersionId)&&graphVersionId>0?{graph_version_id:graphVersionId}:{}),
      ...(graphVersion?{graph_version:graphVersion}:{}),
      ...(businessVersion?{business_version:businessVersion}:{}),
    }:null;
  const state=useResolution(request);
  const matchesResource=!resourceId||!state.resolution||state.resolution.resource_id===resourceId;
  const safeState=matchesResource?state:{
    requested:true,
    loading:false,
    error:new ApiError(409,'引用目标与当前资源不一致',undefined,{error_code:'CITATION_TARGET_MISMATCH'}),
  };
  useEffect(()=>{
    if(!safeState.resolution)return;
    const exact=[...document.querySelectorAll<HTMLElement>('[data-evidence-id]')]
      .find(item=>item.dataset.evidenceId===safeState.resolution?.evidence_id);
    const target=exact??document.querySelector<HTMLElement>('[data-citation-focus]');
    target?.scrollIntoView({block:'center',behavior:'smooth'});
    target?.focus({preventScroll:true});
  },[safeState.resolution]);
  return safeState;
}

export type DirectEvidenceLocation={
  evidenceId:string;
  start:number;
  end:number;
  quote:string|null;
};

export function useDirectEvidenceLocation():DirectEvidenceLocation|null{
  const [searchParams]=useSearchParams();
  return useMemo(()=>{
    const evidenceId=searchParams.get('sourceEvidenceId');
    const start=Number(searchParams.get('sourceEvidenceStart'));
    const end=Number(searchParams.get('sourceEvidenceEnd'));
    if(!evidenceId||!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start)return null;
    return {evidenceId,start,end,quote:searchParams.get('sourceEvidenceQuote')};
  },[searchParams]);
}

export function EvidenceDeepLinkFocus({resourceId}:{resourceId?:string}){
  const direct=useDirectEvidenceLocation();
  const state=useEvidenceDeepLink(resourceId);
  if(direct)return <div
    className="evidence-deep-link-focus"
    data-citation-focus
    data-evidence-id={direct.evidenceId}
    tabIndex={-1}
  >
    <Alert
      type="info"
      title="已定位到原文片段"
      description={direct.quote?<mark>{direct.quote}</mark>:'请查看页面中的原文高亮。'}
    />
  </div>;
  if(!state.requested)return null;
  if(state.loading)return <Alert className="evidence-deep-link-focus" showIcon icon={<Spin size="small"/>} title="正在恢复引用证据" description="正在读取正式来源。"/>;
  if(state.error)return <Alert className="evidence-deep-link-focus" type="error" showIcon title="无法恢复引用" description={`${state.error.errorCode||'CITATION_RESOLVE_FAILED'}：${state.error.message}`}/>;
  const item=state.resolution;
  if(!item)return null;
  return <div
    className="evidence-deep-link-focus"
    data-citation-focus
    data-evidence-id={item.evidence_id}
    tabIndex={-1}
  >
    <Alert
      type="info"
      title="已定位到引用证据"
      description={item.highlight_text?<mark>{item.highlight_text}</mark>:'该来源没有返回可展示的原文片段。'}
    />
  </div>;
}
