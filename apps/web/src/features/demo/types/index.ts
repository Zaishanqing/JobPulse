export type PortalDemoTaskType=
  |'jd_extraction'
  |'cv_extraction'
  |'trend'
  |'discovery'
  |'matching';

export type PortalDemoTaskStatus=
  |'pending'
  |'running'
  |'succeeded'
  |'failed'
  |'cancelled';

export type PortalDemoTaskError={
  code:string|null;
  message:string|null;
};

export type PortalDemoTask={
  task_id:string;
  task_type:PortalDemoTaskType;
  object_type:string;
  object_id:string;
  service:string;
  status:PortalDemoTaskStatus;
  progress:number;
  error:PortalDemoTaskError|null;
  result_reference:string|null;
  created_at:string|null;
  updated_at:string|null;
};

export type PortalDemoTaskFilters={
  task_type?:PortalDemoTaskType;
  status?:PortalDemoTaskStatus;
  object_id?:string;
};
