import type {BuildRun,PublishGate} from '../../../shared/api';

export type CatalogPosition={
  position_id:string;
  position_name:string;
  position_code?:string|null;
  taxonomy_family_code?:string|null;
  taxonomy_family_name?:string|null;
  source_emerging_position_id:string|null;
  status:string;
  graph_onboarding_status:string;
  lifecycle_status?:string;
  jd_count?:number;
  created_at:string|null;
  updated_at:string|null;
};

export type CatalogPositionPage={
  items:CatalogPosition[];
  pagination:{page:number;page_size:number;total:number;total_pages:number};
  filters:{domains:Array<{code:string;name:string}>};
  sort:{by:string;order:string};
};

export type BuildGraphInput={
  window_start?:string|null;
  window_end?:string|null;
  minimum_effective_weight?:number;
  minimum_valid_samples?:number;
};

export type BuildGraphResult={
  build_run_id:number;
  status:string;
  summary:BuildRun['summary'];
};

export type PublishResult={
  version_id:number;
  version_number:number;
  rollback_from_version_id?:number|null;
};

export type {BuildRun,PublishGate};
