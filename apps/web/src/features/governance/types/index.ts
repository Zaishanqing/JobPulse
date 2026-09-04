export type EvaluationDataset={
  dataset_id:string;
  dataset_type:'jd'|'resume'|'match';
  name:string;
  description:string|null;
  payload:Record<string,unknown>;
  created_at:string|null;
  updated_at:string|null;
};

export type Evaluation={
  evaluation_id:string;
  report_type:string;
  dataset_id:string|null;
  metrics:Record<string,unknown>;
  error_cases:Array<Record<string,unknown>>;
  evaluation_status:string;
  algorithm_version:string;
  config_snapshot:Record<string,unknown>;
  evaluated_count:number;
  error_count:number;
  implementation_status:string;
  created_at:string|null;
  updated_at:string|null;
};

export type FeedbackRecord={
  feedback_id:string;
  feedback_type:string;
  user_id:string;
  payload:Record<string,unknown>;
  status:'pending_review'|'reviewing'|'accepted'|'rejected';
  created_at:string|null;
  updated_at:string|null;
  implementation_status:string;
};
