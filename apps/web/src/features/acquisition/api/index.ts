import {api} from '../../../shared/api';
import type {AcquisitionJob,AcquisitionJobPage,AcquisitionSourceStatus,CreateAcquisitionJobRequest} from '../types';

export function listAcquisitionSources(){return api<AcquisitionSourceStatus[]>('/acquisition/sources')}
export function saveBossCookies(cookies:unknown[]){return api<{saved:boolean;count:number;verified:boolean}>('/acquisition/boss/cookies',{method:'POST',body:JSON.stringify({cookies})})}
export function saveLiepinCookies(cookies:unknown[]){return api<{saved:boolean;count:number;verified:boolean}>('/acquisition/liepin/cookies',{method:'POST',body:JSON.stringify({cookies})})}
export function createAcquisitionJob(payload:CreateAcquisitionJobRequest){return api<AcquisitionJob>('/acquisition/jobs',{method:'POST',body:JSON.stringify(payload)})}
export function listAcquisitionJobs(params:{status?:string;source?:string;page?:number;page_size?:number}={}){return api<AcquisitionJobPage>(`/acquisition/jobs?${new URLSearchParams(Object.entries(params).filter((entry):entry is [string,string|number]=>entry[1]!==undefined&&entry[1]!=='').map(([key,value])=>[key,String(value)])).toString()}`)}
export function getAcquisitionJob(jobId:string){return api<AcquisitionJob>(`/acquisition/jobs/${encodeURIComponent(jobId)}`)}
export function retryAcquisitionJob(jobId:string){return api<AcquisitionJob>(`/acquisition/jobs/${encodeURIComponent(jobId)}/retry`,{method:'POST'})}
