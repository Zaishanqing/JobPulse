export type AcquisitionSourceStatus={
  source:string;
  available:boolean;
  ready:boolean;
  login_required:boolean;
  reason:string|null;
};

export type AcquisitionJob={
  id:string;
  requested_by:string|null;
  source:string;
  keyword:string;
  city:string;
  pages:number;
  status:string;
  progress:number;
  crawler_task_id:string|null;
  bundle_id:string|null;
  bundle_file_name:string|null;
  bundle_hash:string|null;
  discovered_count:number;
  exported_count:number;
  imported_count:number;
  no_op_count:number;
  failed_count:number;
  import_batch_id:string|null;
  error_code:string|null;
  error_message:string|null;
  retry_of_id:string|null;
  attempt:number;
  created_at:string|null;
  updated_at:string|null;
  started_at:string|null;
  finished_at:string|null;
};

export type AcquisitionJobPage={
  items:AcquisitionJob[];
  total:number;
  page:number;
  page_size:number;
};

export type CreateAcquisitionJobRequest={
  source:string;
  keyword?:string;
  city?:string;
  pages?:number;
};
