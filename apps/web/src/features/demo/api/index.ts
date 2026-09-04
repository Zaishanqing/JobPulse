import {api} from '../../../shared/api';
import type {PortalDemoTask,PortalDemoTaskFilters} from '../types';

export function listPortalDemoTasks(filters?:PortalDemoTaskFilters){
  const params=new URLSearchParams();
  if(filters?.task_type)params.set('task_type',filters.task_type);
  if(filters?.status)params.set('status',filters.status);
  if(filters?.object_id)params.set('object_id',filters.object_id);
  const query=params.toString();
  return api<PortalDemoTask[]>(`/portal/admin/demo-tasks${query?`?${query}`:''}`);
}
