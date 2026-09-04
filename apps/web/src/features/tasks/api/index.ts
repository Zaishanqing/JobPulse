import {api} from '../../../shared/api';
import type {ExtractionTask,ExtractionTaskPage,TaskRecord} from '../types';

export function listTasks(){return api<TaskRecord[]>('/tasks')}
export function listExtractionTasks(){return api<ExtractionTaskPage>('/extraction-tasks?page=1&page_size=100')}
export function retryExtractionTask(taskId:string){return api<ExtractionTask>(`/extraction-tasks/${encodeURIComponent(taskId)}/retry`,{method:'POST'})}
