export type JDRecord={
  jd_id:string;
  source_type:string;
  source_name:string|null;
  source_platform?:string|null;
  enterprise_id:string|null;
  title:string;
  raw_text:string;
  publish_date:string|null;
  url:string|null;
  file_id?:string|null;
  parse_status:string;
  input_extraction_status?:string;
  input_provider?:string|null;
  input_error_code?:string|null;
  input_error_message?:string|null;
  copy_risk_score:number|null;
  inflation_score:number|null;
  is_downweighted:boolean;
  created_at?:string|null;
  updated_at?:string|null;
};

export type ParsedSkill={
  raw_skill:string;
  normalized_skill_id:string|null;
  confidence:number;
  resolution_status:string;
};

export type JDParseResult={
  parse_result_id:string;
  jd_id:string;
  position_title:string|null;
  responsibilities:string[];
  required_skills:ParsedSkill[];
  bonus_skills:ParsedSkill[];
  education:string|null;
  experience:string|null;
  industry:string|null;
  tools:string[];
  business_scenarios:string[];
  parse_confidence:number;
  need_review:boolean;
  workflow_status?:string;
  extraction_status?:string;
  normalization_status?:string;
  schema_version?:string;
  normalization_schema_version?:string;
  extraction_result?:Record<string,unknown>|null;
  normalized_result?:Record<string,unknown>|null;
  execution?:{
    mode:'llm'|'rule';
    provider:string;
    model:string;
    prompt_version:string;
    algorithm_version:string;
    schema_version:string;
    normalization_version:string;
    started_at:string;
    finished_at:string;
  }|null;
  created_at?:string|null;
  updated_at?:string|null;
};

export type JDCreateResult={jd_id:string;parse_status:string;created_at:string|null};
