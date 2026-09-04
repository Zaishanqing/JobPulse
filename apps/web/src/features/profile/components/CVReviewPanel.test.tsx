import {cleanup,render,screen} from '@testing-library/react';
import {App} from 'antd';
import {beforeEach,expect,test} from 'vitest';
import type {CVExtractionTask,CVReview} from '../../matching/types';
import {CVReviewPanel} from './CVReviewPanel';

beforeEach(()=>cleanup());

const task:CVExtractionTask={
  task_id:'TASK_CV_1',
  source_cv_version_id:'VERSION_1',
  owner_id:'user-1',
  request_id:'req-1',
  execution_id:null,
  execution_metadata:null,
  status:'succeeded',
  processing_stage:'review_pending',
  attempt_count:1,
  max_attempts:3,
  last_error_code:null,
  last_error_message:null,
  retryable:false,
  claimed_by:null,
  lease_expires_at:null,
  heartbeat_at:null,
  next_attempt_at:null,
  finished_at:null,
  validation_conclusion:'pass',
  validation_report_payload:null,
  validation_task_id:null,
  validation_report_id:null,
  resume_id:null,
  created_at:null,
  updated_at:null,
  review_payload:null,
  review_id:'review-1',
  confirmation_status:'pending',
  latest_validated_cv_snapshot_id:null,
  confirmed_at:null,
  confirmed_by:null,
  review_revision:0,
  confirmation_idempotency_key:null,
  confirmation_idempotency_id:null,
};

const review=(contentType:string|null):CVReview=>({
  task_id:'TASK_CV_1',
  source_cv_id:'SOURCE_1',
  source_cv_version_id:'VERSION_1',
  status:'succeeded',
  confirmation_status:'pending',
  review_id:'review-1',
  review_revision:0,
  source_text:'熟练使用 Python',
  source_file_id:'FILE_1',
  content_type:contentType,
  ocr_layout:null,
  reviewable_fields:[],
  review_flags:[],
  validation:{conclusion:'pass',blocking_reasons:[],policy_version:'policy-v1',validation_task_id:null,validation_report_id:null},
});

const renderPanel=(contentType:string|null)=>render(
  <App>
    <CVReviewPanel
      task={task}
      review={review(contentType)}
      sourcePreviewUrl="blob:mock-source"
      decisions={{}}
      disabled={false}
      onDecisionChange={()=>undefined}
      onConfirm={()=>undefined}
    />
  </App>,
);

test('Word 文档原始文件显示回退提示与下载入口',()=>{
  renderPanel('application/vnd.openxmlformats-officedocument.wordprocessingml.document');
  expect(screen.getByText('该格式暂不支持在线预览')).toBeInTheDocument();
  const download=screen.getByRole('link',{name:/下载原始文件/});
  expect(download).toHaveAttribute('href','blob:mock-source');
  expect(download).toHaveAttribute('download');
  expect(document.querySelector('iframe')).toBeNull();
});

test('PDF 原始文件仍使用内嵌预览',()=>{
  renderPanel('application/pdf');
  expect(document.querySelector('iframe.cv-review-source-document')).not.toBeNull();
  expect(screen.queryByText('该格式暂不支持在线预览')).not.toBeInTheDocument();
});

const field=(overrides:Partial<CVReview['reviewable_fields'][number]>):CVReview['reviewable_fields'][number]=>({
  field_id:'personal_info:work_status',
  field_type:'work_status',
  section:'personal_info',
  item_id:'personal_info',
  field_path:'work_status',
  field_label:'求职状态',
  original_value:'student',
  suggested_value:null,
  evidence:null,
  flag_codes:[],
  ...overrides,
});

test('求职意向 section 正常渲染且求职状态值中文化',()=>{
  const r=review(null);
  render(
    <App>
      <CVReviewPanel
        task={task}
        review={{...r,reviewable_fields:[field({})]}}
        sourcePreviewUrl={undefined}
        decisions={{}}
        disabled={false}
        onDecisionChange={()=>undefined}
        onConfirm={()=>undefined}
      />
    </App>,
  );
  expect(screen.getByText('求职意向')).toBeInTheDocument();
  expect(screen.getByText('求职状态')).toBeInTheDocument();
  expect(screen.getByText('在校学生')).toBeInTheDocument();
  expect(screen.queryByText('student')).not.toBeInTheDocument();
});

test('后端新增未知 section 时兜底渲染为其他信息',()=>{
  const r=review(null);
  render(
    <App>
      <CVReviewPanel
        task={task}
        review={{...r,reviewable_fields:[field({field_id:'x:1',section:'brand_new_section',item_id:'x',field_path:'name',field_label:'字段',original_value:'某值'})]}}
        sourcePreviewUrl={undefined}
        decisions={{}}
        disabled={false}
        onDecisionChange={()=>undefined}
        onConfirm={()=>undefined}
      />
    </App>,
  );
  expect(screen.getByText('其他信息')).toBeInTheDocument();
  expect(screen.getByText('某值')).toBeInTheDocument();
  expect(screen.queryByText('brand_new_section')).not.toBeInTheDocument();
});
