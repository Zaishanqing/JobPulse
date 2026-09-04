import {api} from '../../../shared/api';
import type {JDCreateResult,JDParseResult,JDRecord} from '../types';

export type JDTextInput={
  title:string;
  raw_text:string;
  source_name?:string;
  source_type?:string;
  enterprise_id?:string;
};
export type OCRResult={
  result_id:string;
  task_id?:string;
  source_type:string;
  filename:string|null;
  status:string;
  text:string;
  provider:string;
  error_code:string|null;
  error_message:string|null;
};
export type JDSummary={total:number;awaiting_review:number;reviewed:number;published:number;failed:number};
export type JDPage={items:JDRecord[];total:number;offset:number;limit:number};
export type JDBatchParseResult={parsed_count:number;items:JDParseResult[]};
export type ExtractionMode='llm'|'rule';
export type ExtractionModeStatus={ready:boolean;provider:string;error?:string|null;error_code?:string|null;requires_review?:boolean};
export type ExtractionModesReadiness={jd:{llm:ExtractionModeStatus;rule:ExtractionModeStatus}};

export function listJDs(){return api<JDRecord[]>('/jds')}
export function getJDSummary(){return api<JDSummary>('/jds/summary')}
export function getJDPage(offset=0,limit=20,query='',sort='created_desc'){
  const params=new URLSearchParams({offset:String(offset),limit:String(limit)});
  if(query.trim())params.set('query',query.trim());
  params.set('sort',sort);
  return api<JDPage>(`/jds/page?${params.toString()}`);
}
export function getJD(jdId:string){return api<JDRecord>(`/jds/${encodeURIComponent(jdId)}`)}
export function createTextJD(input:JDTextInput){
  return api<JDCreateResult>('/jds/text',{method:'POST',body:JSON.stringify({source_type:'enterprise_upload',...input})});
}
export function createFileJD(file:File,title:string){
  const body=new FormData();
  body.append('file',file);
  body.append('title',title);
  body.append('source_type','enterprise_upload');
  body.append('source_name',file.name);
  return api<JDCreateResult>('/jds/file',{method:'POST',body});
}
export function runOCR(file:File){
  const body=new FormData();
  body.append('file',file);
  const endpoint=file.type==='application/pdf'||file.name.toLowerCase().endsWith('.pdf')?'pdf':'image';
  return api<OCRResult>(`/ocr/${endpoint}`,{method:'POST',body});
}
export function parseJD(jdId:string,extractionMode:ExtractionMode){
  return api<JDParseResult>(`/jds/${encodeURIComponent(jdId)}/parse`,{method:'POST',body:JSON.stringify({extraction_mode:extractionMode})});
}
export function parseJDBatch(jdIds:string[],extractionMode:ExtractionMode){
  return api<JDBatchParseResult>('/jds/parse-batch',{method:'POST',body:JSON.stringify({jd_ids:jdIds,extraction_mode:extractionMode})});
}
export function getExtractionModesReadiness(){return api<ExtractionModesReadiness>('/extraction-modes/readiness')}
export function getJDParseResult(jdId:string){return api<JDParseResult>(`/jds/${encodeURIComponent(jdId)}/parse-result`)}
export function updateJDParseResult(jdId:string,payload:Partial<JDParseResult>){
  return api<JDParseResult>(`/jds/${encodeURIComponent(jdId)}/parse-result`,{method:'PUT',body:JSON.stringify(payload)});
}
export function confirmJDParseResult(jdId:string){return api<JDParseResult>(`/jds/${encodeURIComponent(jdId)}/parse-result/confirm`,{method:'POST'})}
export function publishJDParseResult(jdId:string){return api<Record<string,unknown>>(`/jds/${encodeURIComponent(jdId)}/parse-result/publish`,{method:'POST'})}
