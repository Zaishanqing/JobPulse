import {useEffect,useMemo,useState} from 'react';
import {App,Button,Drawer,Empty,Input,Spin,Tag,Typography} from 'antd';
import {ApiError} from '../../../shared/api';
import {
  getCVExtractionReview,
  getValidatedCVSnapshot,
  reviseValidatedCVSnapshot,
} from '../../matching/api';
import type {
  CVConfirmPayload,
  CVFieldDecisionInput,
  CVReview,
  CVReviewableField,
  ResumeRecord,
  ValidatedCVSnapshot,
} from '../../matching/types';

export type ExperienceSectionKey='projects'|'internships'|'education';

const sectionMeta:Record<ExperienceSectionKey,{title:string;reviewSection:string}>={
  projects:{title:'项目经历',reviewSection:'project_experience'},
  internships:{title:'工作 / 实习',reviewSection:'work_experience'},
  education:{title:'教育经历',reviewSection:'education'},
};

// 抽取结果里的嵌套/内部字段不适合直接展示,统一转成中文行。
const internalKeys=new Set(['entry_id','item_id','evidence','field_evidence','duration_text']);
const valueKeyLabels:Record<string,string>={
  name:'名称',project_name:'项目名称',school:'院校',college:'学院',major:'专业',degree:'学位',
  company:'公司',organization:'机构',position:'职位',role:'角色',title:'标题',status:'状态',
  description:'描述',date:'时间',start:'开始时间',end:'结束时间',gpa:'GPA',location:'地点',
};

const displayLabel=(field:CVReviewableField)=>field.field_label.replace(/（待补录）$/,'');

const flattenItem=(item:Record<string,unknown>)=>{
  const rows:Array<{label:string;value:string}>=[];
  for(const [key,value] of Object.entries(item)){
    if(internalKeys.has(key)||value==null||value==='')continue;
    if(typeof value==='string'){
      rows.push({label:valueKeyLabels[key]||key,value});
      continue;
    }
    if(typeof value==='object'&&!Array.isArray(value)){
      const record=value as Record<string,unknown>;
      if(typeof record.start==='string'||typeof record.end==='string'){
        rows.push({label:valueKeyLabels[key]||key,value:`${record.start||'?'} — ${record.end||'?'}`});
      }else if(typeof record.value==='string'){
        rows.push({label:valueKeyLabels[key]||key,value:record.value});
      }
    }
  }
  return rows;
};

const snapshotItemEvidence=(
  snapshot:ValidatedCVSnapshot,
  section:string,
  itemId:string,
)=>{
  const list=snapshot.extraction_payload?.[section];
  if(!Array.isArray(list))return null;
  const item=list.find(value=>value&&typeof value==='object'&&(
    (value as Record<string,unknown>).entry_id===itemId||(value as Record<string,unknown>).item_id===itemId
  )) as Record<string,unknown>|undefined;
  const evidence=item?.evidence;
  if(!evidence||typeof evidence!=='object')return null;
  const record=evidence as Record<string,unknown>;
  return {
    quote:typeof record.quote==='string'?record.quote:null,
    start:typeof record.start==='number'?record.start:null,
    end:typeof record.end==='number'?record.end:null,
  };
};

type Props={
  resume:ResumeRecord|undefined;
  section:ExperienceSectionKey|null;
  items:Array<Record<string,unknown>>;
  onClose:()=>void;
  onSaved:()=>Promise<void>|void;
};

export function ExperienceDrawer({resume,section,items,onClose,onSaved}:Props){
  const {message}=App.useApp();
  const [editing,setEditing]=useState(false);
  const [loading,setLoading]=useState(false);
  const [saving,setSaving]=useState(false);
  const [loadError,setLoadError]=useState<string>();
  const [snapshot,setSnapshot]=useState<ValidatedCVSnapshot>();
  const [review,setReview]=useState<CVReview>();
  const [values,setValues]=useState<Record<string,string>>({});
  const [reason,setReason]=useState('人工核对修正');

  const meta=section?sectionMeta[section]:null;
  const snapshotId=resume?.validated_cv_snapshot_id;

  useEffect(()=>{
    let disposed=false;
    const timer=window.setTimeout(()=>{
      if(disposed)return;
      setEditing(false);setSnapshot(undefined);setReview(undefined);setValues({});setLoadError(undefined);
      if(!section||!snapshotId)return;
      setLoading(true);
      void (async()=>{
        try{
          const snap=await getValidatedCVSnapshot(snapshotId);
          const rev=await getCVExtractionReview(snap.cv_extraction_task_id);
          if(disposed)return;
          setSnapshot(snap);setReview(rev);
        }catch(error){
          if(!disposed)setLoadError((error as ApiError).message||'解析结果加载失败');
        }finally{
          if(!disposed)setLoading(false);
        }
      })();
    },0);
    return()=>{disposed=true;window.clearTimeout(timer)};
  },[section,snapshotId]);

  const priorByFieldId=useMemo(
    ()=>new Map((snapshot?.field_decisions||[]).map(item=>[item.field_id,item])),
    [snapshot],
  );

  const currentValue=(field:CVReviewableField)=>{
    const prior=priorByFieldId.get(field.field_id);
    if(prior?.decision==='correct')return prior.corrected_value||'';
    if(prior?.decision==='unknown')return '';
    return field.original_value||field.suggested_value||'';
  };

  const sectionFields=useMemo(
    ()=>review?.reviewable_fields.filter(field=>field.section===meta?.reviewSection)||[],
    [review,meta],
  );

  const groupedFields=useMemo(()=>{
    const groups=new Map<string,CVReviewableField[]>();
    for(const field of sectionFields){
      const list=groups.get(field.item_id)||[];
      list.push(field);
      groups.set(field.item_id,list);
    }
    return [...groups.entries()];
  },[sectionFields]);

  const enterEdit=()=>{
    setValues(Object.fromEntries(sectionFields.map(field=>[field.field_id,currentValue(field)])));
    setEditing(true);
  };

  const changedDecisions=():CVFieldDecisionInput[]=>sectionFields.flatMap(field=>{
    const next=(values[field.field_id]??'').trim();
    if(!next||next===currentValue(field).trim())return[];
    return [{
      field_id:field.field_id,
      field_type:field.field_type,
      section:field.section,
      item_id:field.item_id,
      field_path:field.field_path,
      decision:'correct' as const,
      corrected_value:next,
      correction_reason:reason.trim()||'人工核对修正',
      evidence_quote:field.evidence?.quote??null,
      evidence_start:field.evidence?.start??null,
      evidence_end:field.evidence?.end??null,
    }];
  });

  const save=async()=>{
    if(!snapshot||!meta)return;
    const edits=changedDecisions();
    if(!edits.length){
      message.info('没有修改内容');
      return;
    }
    // 快照修订从原始抽取结果重放决策:先带上历史人工决策,再覆盖本次修改,
    // 避免只提交改动字段时丢失之前已确认的内容。
    const reviewById=new Map((review?.reviewable_fields||[]).map(field=>[field.field_id,field]));
    const merged=new Map<string,CVFieldDecisionInput>();
    for(const prior of snapshot.field_decisions){
      const field=reviewById.get(prior.field_id);
      const fallback=snapshotItemEvidence(snapshot,prior.section,prior.item_id);
      merged.set(prior.field_id,{
        ...prior,
        evidence_quote:field?.evidence?.quote??fallback?.quote??null,
        evidence_start:field?.evidence?.start??fallback?.start??null,
        evidence_end:field?.evidence?.end??fallback?.end??null,
      });
    }
    for(const edit of edits)merged.set(edit.field_id,edit);
    const payload:CVConfirmPayload={
      expected_review_id:snapshot.snapshot_id,
      idempotency_key:crypto.randomUUID(),
      field_decisions:[...merged.values()],
      normalization_version:snapshot.normalization_version,
      taxonomy_version:snapshot.taxonomy_version,
      display_name:resume?.display_name??null,
    };
    setSaving(true);
    try{
      await reviseValidatedCVSnapshot(snapshot.snapshot_id,payload);
      message.success(`${meta.title}已更新,简历快照已生成新版本`);
      await onSaved();
      onClose();
    }catch(error){
      const err=error as ApiError;
      if(err.status===409){
        message.warning('简历快照刚被其他操作更新,请关闭抽屉后重试');
      }else{
        message.error(err.message||'保存失败,请稍后重试');
      }
    }finally{setSaving(false)}
  };

  const canEdit=sectionFields.some(field=>field.evidence);

  return <Drawer
    title={meta?`${meta.title} · 解析结果`:''}
    size={520}
    open={!!section}
    onClose={onClose}
    footer={editing?(
      <div className="experience-drawer-footer">
        <Button onClick={()=>setEditing(false)} disabled={saving}>取消</Button>
        <Button type="primary" loading={saving} onClick={()=>void save()}>保存修改</Button>
      </div>
    ):(
      <div className="experience-drawer-footer">
        {canEdit&&<Button type="primary" onClick={enterEdit}>编辑内容</Button>}
        <Button onClick={onClose}>关闭</Button>
      </div>
    )}
  >
    {!snapshotId&&<Empty
      image={Empty.PRESENTED_IMAGE_SIMPLE}
      description="该简历尚未生成验证快照,暂不支持在线查看与编辑"
    />}
    {snapshotId&&loading&&<div className="state-panel loading-state"><Spin/><span className="state-panel-hint">正在加载解析结果…</span></div>}
    {snapshotId&&!loading&&loadError&&<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loadError}/>}
    {snapshotId&&!loading&&!loadError&&!groupedFields.length&&(
      items.length?(
        <div className="experience-drawer-items">
          {items.map((item,index)=>{
            const rows=flattenItem(item);
            return <div key={index} className="experience-drawer-item">
              <Typography.Text strong>{`${meta?.title||'条目'} ${index+1}`}</Typography.Text>
              {rows.map(row=><div key={row.label} className="experience-drawer-row">
                <span>{row.label}</span><p>{row.value}</p>
              </div>)}
              {!rows.length&&<Typography.Text type="secondary">暂无可展示的字段</Typography.Text>}
            </div>;
          })}
          <Typography.Paragraph type="secondary">当前抽取结果未提供可编辑字段,暂不支持在线修改。</Typography.Paragraph>
        </div>
      ):<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`当前快照中没有${meta?.title||''}记录`}/>
    )}
    {snapshotId&&!loading&&!loadError&&groupedFields.length>0&&(
      <div className="experience-drawer-items">
        {groupedFields.map(([itemId,fields],index)=>{
          const isSupplement=itemId.startsWith('new_');
          return <div key={itemId} className="experience-drawer-item">
            <div className="experience-drawer-item-head">
              <Typography.Text strong>{`${meta?.title||'条目'} ${index+1}`}</Typography.Text>
              {isSupplement&&<Tag color="warning">人工补录</Tag>}
            </div>
            {fields.map(field=>editing?(
              <div key={field.field_id} className="experience-drawer-field">
                <span>{displayLabel(field)}</span>
                <Input
                  value={values[field.field_id]??''}
                  disabled={saving||!field.evidence}
                  placeholder={field.evidence?'请输入修正后的内容':'缺少原文证据,暂不支持修改'}
                  onChange={event=>setValues(current=>({...current,[field.field_id]:event.target.value}))}
                />
              </div>
            ):(
              <div key={field.field_id} className="experience-drawer-row">
                <span>{displayLabel(field)}</span>
                <p>{currentValue(field)||'未确认'}</p>
              </div>
            ))}
          </div>;
        })}
        {editing&&<div className="experience-drawer-field">
          <span>修正原因</span>
          <Input
            value={reason}
            disabled={saving}
            maxLength={120}
            onChange={event=>setReason(event.target.value)}
          />
          <Typography.Text type="secondary" className="experience-drawer-hint">
            保存后将基于当前快照生成新版本,原有匹配报告会标记为已过期,可重新生成。
          </Typography.Text>
        </div>}
      </div>
    )}
  </Drawer>;
}
