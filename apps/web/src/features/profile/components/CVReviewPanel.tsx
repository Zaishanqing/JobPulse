import {Button,Input,Select,Space,Tag,Typography} from 'antd';
import {DownloadOutlined,FileWordOutlined} from '@ant-design/icons';
import {ToastAlert as Alert} from '../../../shared/components/States';
import type {
  CVExtractionTask,
  CVFieldDecisionValue,
  CVReview,
  CVReviewDecisionDraft,
} from '../../matching/types';

const CV_SECTION_ORDER=[
  'personal_info',
  'education',
  'work_experience',
  'project_experience',
  'skills',
  'languages',
  'certificates',
  'awards',
  'publications',
  'patents',
  'research_outputs',
  'self_evaluation',
];
const SECTION_LABELS:Record<string,string>={
  personal_info:'求职意向',
  education:'教育经历',
  work_experience:'工作经历',
  project_experience:'项目经历',
  skills:'技能',
  languages:'语言能力',
  certificates:'证书',
  awards:'奖项',
  publications:'论文',
  patents:'专利',
  research_outputs:'科研产出',
  self_evaluation:'自我评价',
};
const DECISION_OPTIONS:Array<{value:CVFieldDecisionValue;label:string}>=[
  {value:'accept',label:'接受'},
  {value:'correct',label:'修正'},
  {value:'unknown',label:'无法判断'},
  {value:'remove',label:'移除'},
];
const WORK_STATUS_LABELS:Record<string,string>={
  student:'在校学生',
  fresh_graduate:'应届毕业生',
  employed:'在职',
  unemployed:'待业',
  job_seeking:'求职中',
  freelance:'自由职业',
};

const displayFieldValue=(field:CVReview['reviewable_fields'][number])=>{
  const value=field.original_value||field.suggested_value||field.field_id;
  if(field.section==='personal_info'&&field.field_path==='work_status')return WORK_STATUS_LABELS[value]||value;
  return value;
};

const spanText=(start:number|null,end:number|null)=>start===null||end===null?'区间未返回':`${start}-${end}`;

export function CVReviewPanel({
  review,
  sourcePreviewUrl,
  decisions,
  disabled,
  onDecisionChange,
  onConfirm,
}:{
  task:CVExtractionTask;
  review:CVReview;
  sourcePreviewUrl:string|undefined;
  decisions:Record<string,CVReviewDecisionDraft|undefined>;
  disabled:boolean;
  onDecisionChange:(fieldId:string,update:Partial<CVReviewDecisionDraft>)=>void;
  onConfirm:()=>void;
}){
  const validation=review.validation;
  const fields=review.reviewable_fields;
  const knownSections=new Set<string>(CV_SECTION_ORDER);
  const extraSectionNames=[...new Set(
    fields.map(field=>field.section).filter(section=>!knownSections.has(section)),
  )];
  const sections=[...CV_SECTION_ORDER,...extraSectionNames]
    .map(section=>({section,items:fields.filter(field=>field.section===section)}))
    .filter(group=>group.items.length>0);
  const renderedFields=sections.flatMap(group=>group.items);
  const blockingFlags=review.review_flags.filter(flag=>flag.severity==='block');
  const allDecided=renderedFields.every(field=>Boolean(decisions[field.field_id]?.decision));
  const correctRules=fields.every(field=>{
    const draft=decisions[field.field_id];
    if(!draft||draft.decision!=='correct')return true;
    return Boolean(draft.corrected_value?.trim())&&Boolean(draft.correction_reason?.trim());
  });
  const reviewIdReady=Boolean(review.review_id);
  const confirmBlocked=!allDecided||validation?.conclusion==='block'||blockingFlags.length>0||!correctRules||!reviewIdReady;
  const confirmDisabled=disabled||confirmBlocked;

  return <section className="cv-review-panel" aria-label="字段证据审核">
    <div className="cv-review-head">
      <div>
        <Typography.Title level={4}>字段证据审核</Typography.Title>
      </div>
      <Tag color={validation?.conclusion==='pass'?'success':validation?.conclusion==='warn'?'warning':validation?.conclusion==='block'?'error':'default'}>
        校验结论：{validation?.conclusion||'未返回'}
      </Tag>
    </div>

    {!validation&&<Alert type="error" showIcon title="接口数据错误" description="审核接口未返回校验结论，无法确认快照。"/>}
    {validation?.conclusion==='block'&&<Alert type="error" showIcon title="验证阻断" description={validation.blocking_reasons.join('、')||'当前结论禁止确认。'}/>}

    {blockingFlags.length>0&&<div className="cv-review-flags">
      {blockingFlags.map(flag=><Alert
        key={`${flag.code}:${flag.item_id||''}`}
        type="error"
        showIcon
        title={flag.code}
        description={flag.message||flag.suggested_action||'审核标记未返回说明'}
      />)}
    </div>}

    {fields.length===0&&<Alert type="warning" showIcon title="没有可审核字段" description="审核接口未返回可审核字段，无法构造确认载荷。"/>}

    {(sourcePreviewUrl||review.source_text)&&<div className="cv-review-source-grid">
      <div>
        <Typography.Text strong>原始文件</Typography.Text>
        {sourcePreviewUrl&&review.content_type?.startsWith('image/')&&<img
          className="cv-review-source-image"
          src={sourcePreviewUrl}
          alt="原始简历"
        />}
        {sourcePreviewUrl&&review.content_type==='application/pdf'&&<iframe
          className="cv-review-source-document"
          src={sourcePreviewUrl}
          title="原始简历"
        />}
        {sourcePreviewUrl&&Boolean(review.content_type)&&!review.content_type!.startsWith('image/')&&review.content_type!=='application/pdf'&&<div className="cv-review-source-fallback">
          <FileWordOutlined className="cv-review-source-fallback-icon"/>
          <Typography.Text strong>该格式暂不支持在线预览</Typography.Text>
          <Typography.Text type="secondary">Word 等文档可直接在右侧核对解析文本，或下载原始文件查看。</Typography.Text>
          <Button href={sourcePreviewUrl} download="简历原始文件" icon={<DownloadOutlined/>}>下载原始文件</Button>
        </div>}
        {!sourcePreviewUrl&&<Typography.Text type="secondary">原始文件预览不可用</Typography.Text>}
      </div>
      <div>
        <Typography.Text strong>OCR / 解析文本</Typography.Text>
        <pre className="cv-review-source-text">{review.source_text||'未返回源文本'}</pre>
        {review.ocr_layout&&<Typography.Text type="secondary">
          已保留 {review.ocr_layout.length} 页 OCR 坐标，可用于证据定位。
        </Typography.Text>}
      </div>
    </div>}

    {sections.map(({section,items})=>(
      <div className="cv-review-section" key={section}>
        <div className="cv-review-section-title">
          <Typography.Text strong>{SECTION_LABELS[section]||'其他信息'}</Typography.Text>
          <Tag>{items.length}</Tag>
          <Button
            size="small"
            disabled={disabled}
            onClick={()=>items
              .filter(field=>!(field.original_value===null&&field.item_id.startsWith('new_')))
              .forEach(field=>onDecisionChange(field.field_id,{decision:'accept'}))}
          >批量接受本组</Button>
        </div>
        {items.map(field=>{
          const draft=decisions[field.field_id];
          const decision=draft?.decision||'';
          const evidence=field.evidence;
          const isMissingItem=field.original_value===null&&field.item_id.startsWith('new_');
          const decisionOptions=isMissingItem
            ?DECISION_OPTIONS.filter(option=>option.value!=='accept')
            :DECISION_OPTIONS;
          return <div className="cv-review-field" key={field.field_id}>
            <div className="cv-review-field-values">
              <span><Tag>{field.field_label}</Tag></span>
              <strong>{displayFieldValue(field)}</strong>
              {field.suggested_value&&field.suggested_value!==field.original_value&&<span>建议值：{field.suggested_value}</span>}
            </div>
            <div className="cv-review-evidence">
              {evidence
                ?<>
                  <blockquote>{evidence.quote}</blockquote>
                  <Typography.Text type="secondary">
                    原文区间 {spanText(evidence.start,evidence.end)} · {evidence.alignment==='exact'?'精确匹配':'已关联'}
                  </Typography.Text>
                </>
                :<Typography.Text type="secondary">该字段没有可复制的证据片段。</Typography.Text>}
            </div>
            <Space className="cv-review-decision" wrap>
              <Select
                aria-label={`${field.field_id} 决策`}
                style={{width:150}}
                value={decision||undefined}
                placeholder="选择决策"
                disabled={disabled}
                options={decisionOptions}
                onChange={value=>onDecisionChange(field.field_id,{decision:value as CVFieldDecisionValue})}
              />
              {decision==='correct'&&<>
                <Input
                  aria-label={`${field.field_id} 修正值`}
                  style={{width:220}}
                  placeholder="修正后的值"
                  value={draft?.corrected_value||''}
                  disabled={disabled}
                  onChange={event=>onDecisionChange(field.field_id,{corrected_value:event.target.value||null})}
                />
                <Input
                  aria-label={`${field.field_id} 修正原因`}
                  style={{width:220}}
                  placeholder="修正原因"
                  value={draft?.correction_reason||''}
                  disabled={disabled}
                  onChange={event=>onDecisionChange(field.field_id,{correction_reason:event.target.value||null})}
                />
              </>}
            </Space>
          </div>;
        })}
      </div>
    ))}

    {!correctRules&&<Alert type="warning" showIcon title="修正信息不完整" description="选择「修正」的字段必须填写修正值和修正原因。"/>}
    {!reviewIdReady&&<Alert type="error" showIcon title="接口数据错误" description="审核接口未返回 review_id，无法构造稳定确认载荷。"/>}

    <div className="cv-review-actions">
      <Button type="primary" disabled={confirmDisabled} onClick={onConfirm}>确认并生成快照</Button>
    </div>
  </section>;
}
