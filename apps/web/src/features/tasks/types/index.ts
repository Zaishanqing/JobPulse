export type TaskLog={status:string;at:string;message:string|null};
export type TaskRecord={
  task_id:string;
  task_type:string;
  status:string;
  canonical_status:'pending'|'running'|'succeeded'|'failed'|'cancelled';
  progress:number;
  input_payload:Record<string,unknown>;
  result_payload:Record<string,unknown>;
  result_reference:string|null;
  error_code:string|null;
  error_message:string|null;
  created_by:string|null;
  attempt_count:number;
  logs:TaskLog[];
  created_at:string|null;
  updated_at:string|null;
  started_at:string|null;
  finished_at:string|null;
  execution_mode:string;
};

export type ExtractionTask={
  id:string;
  source_jd_version_id:string;
  status:string;
  provider:string;
  request_id:string;
  attempt_count:number;
  max_attempts:number;
  started_at:string|null;
  finished_at:string|null;
  last_error_code:string|null;
  last_error_message:string|null;
  retryable:boolean;
  bundle_payload:Record<string,unknown>|null;
  claimed_by:string|null;
  lease_expires_at:string|null;
  heartbeat_at:string|null;
  created_at:string;
  updated_at:string;
};

export type ExtractionTaskPage={items:ExtractionTask[];total:number;page:number;page_size:number};
