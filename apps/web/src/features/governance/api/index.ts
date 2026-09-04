import {api} from '../../../shared/api';
import type {Evaluation,EvaluationDataset,FeedbackRecord} from '../types';

const id=(value:string)=>encodeURIComponent(value);

export const listEvaluationDatasets=()=>api<EvaluationDataset[]>('/evaluation/datasets');
export const createEvaluationDataset=(
  type:EvaluationDataset['dataset_type'],
  payload:{name:string;description?:string;payload:Record<string,unknown>},
)=>api<EvaluationDataset>(`/evaluation/datasets/${type}`,{method:'POST',body:JSON.stringify(payload)});
export const deleteEvaluationDataset=(datasetId:string)=>
  api<{dataset_id:string;deleted:boolean}>(`/evaluation/datasets/${id(datasetId)}`,{method:'DELETE'});

type RawEvaluation=Omit<Evaluation,'evaluation_id'>&{report_id:string;evaluation_id?:string};
const normalizeEvaluation=({report_id,evaluation_id,...evaluation}:RawEvaluation):Evaluation=>({...evaluation,evaluation_id:evaluation_id||report_id});

export const runEvaluation=(reportType:string,datasetId?:string)=>
  api<RawEvaluation>(`/evaluation/${reportType.replaceAll('_','-')}/run`,{
    method:'POST',
    body:JSON.stringify(datasetId?{dataset_id:datasetId}:{}),
  }).then(normalizeEvaluation);
export const getEvaluation=(evaluationId:string)=>api<RawEvaluation>(`/evaluation/reports/${id(evaluationId)}`).then(normalizeEvaluation);

export const listFeedback=()=>api<FeedbackRecord[]>('/feedback');
export const updateFeedbackStatus=(feedbackId:string,status:FeedbackRecord['status'])=>
  api<FeedbackRecord>(`/feedback/${id(feedbackId)}`,{method:'PUT',body:JSON.stringify({status})});
