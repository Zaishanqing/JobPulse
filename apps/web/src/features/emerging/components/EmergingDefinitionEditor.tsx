import {useEffect,useState} from 'react';
import {App,Form,Input,Modal,Select} from 'antd';
import type {EmergingPosition} from '../api';

type Json=Record<string,unknown>;
export type EmergingDefinitionUpdate=Partial<{
  position_name:string;
  core_responsibilities:string[];
  required_skills:Array<Record<string,unknown>>;
  bonus_skills:Array<Record<string,unknown>>;
  industry_scenarios:string[];
  field_evidence:Record<string,unknown>;
}>;
export type EmergingDefinitionSection='all'|'definition'|'responsibilities'|'skills'|'scenarios'|'enterprises'|'timeline';

const record=(value:unknown):Json=>value&&typeof value==='object'&&!Array.isArray(value)?value as Json:{};
const list=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const text=(...values:unknown[])=>values.find(value=>typeof value==='string'&&value.trim()) as string|undefined;
const skillName=(value:unknown)=>{
  const item=record(value);
  return text(item.raw_skill,item.skill_name,item.name,item.label,item.normalized_skill_id)||String(value||'');
};
const lines=(value:string)=>value.split(/\r?\n/).map(item=>item.trim()).filter(Boolean);
const enterpriseNames=(value:unknown)=>Array.isArray(value)?value.map(String):Object.keys(record(value));
const timelineLines=(value:unknown)=>list(value).map(item=>{
  if(typeof item==='string')return item;
  const row=record(item);
  const window=text(row.window_id,row.time_window,row.period)||'';
  const description=text(row.description,row.event,row.summary);
  const count=typeof row.member_count==='number'?row.member_count:typeof row.sample_count==='number'?row.sample_count:undefined;
  return [window,description||(count!=null?`${count} 份岗位样本`:'')].filter(Boolean).join('：');
}).filter(Boolean);
const preserveSkills=(names:string[],existing:Array<Record<string,unknown>>)=>{
  const byName=new Map(existing.map(item=>[skillName(item),item]));
  return names.map(name=>byName.get(name)||{raw_skill:name});
};

export function EmergingDefinitionEditor({open,item,title,section='all',onCancel,onSave}:{
  open:boolean;
  item:EmergingPosition|null;
  title:string;
  section?:EmergingDefinitionSection;
  onCancel:()=>void;
  onSave:(values:EmergingDefinitionUpdate)=>Promise<void>;
}){
  const {message}=App.useApp();
  const [form]=Form.useForm();
  const [saving,setSaving]=useState(false);
  useEffect(()=>{
    if(!open||!item)return;
    const fields=record(item.field_evidence);
    form.setFieldsValue({
      position_name:item.position_name,
      position_summary:text(record(fields.position_summary).content)||'',
      core_responsibilities:item.core_responsibilities.join('\n'),
      required_skills:item.required_skills.map(skillName).filter(Boolean),
      bonus_skills:item.bonus_skills.map(skillName).filter(Boolean),
      industry_scenarios:item.industry_scenarios,
      representative_enterprises:enterpriseNames(record(fields.representative_enterprises).content),
      growth_trajectory:timelineLines(record(fields.growth_trajectory).content).join('\n'),
    });
  },[form,item,open]);
  const submit=async()=>{
    if(!item)return;
    const values=await form.validateFields();
    const fields=record(item.field_evidence);
    const summaryField=record(fields.position_summary);
    const enterpriseField=record(fields.representative_enterprises);
    const trajectoryField=record(fields.growth_trajectory);
    setSaving(true);
    try{
      const update:EmergingDefinitionUpdate={};
      if(section==='all'||section==='definition'){
        update.position_name=String(values.position_name).trim();
        update.field_evidence={...fields,position_summary:{...summaryField,content:String(values.position_summary).trim()}};
      }
      if(section==='all'||section==='responsibilities')update.core_responsibilities=lines(values.core_responsibilities);
      if(section==='all'||section==='skills'){
        update.required_skills=preserveSkills(values.required_skills||[],item.required_skills);
        update.bonus_skills=preserveSkills(values.bonus_skills||[],item.bonus_skills);
      }
      if(section==='all'||section==='scenarios')update.industry_scenarios=values.industry_scenarios||[];
      if(section==='all'||section==='enterprises'){
        update.field_evidence={...fields,representative_enterprises:{...enterpriseField,content:values.representative_enterprises||[]}};
      }
      if(section==='all'||section==='timeline'){
        update.field_evidence={...fields,growth_trajectory:{...trajectoryField,content:lines(values.growth_trajectory||'')}};
      }
      if(section==='all'){
        update.field_evidence={
          ...fields,
          position_summary:{...summaryField,content:String(values.position_summary).trim()},
          representative_enterprises:{...enterpriseField,content:values.representative_enterprises||[]},
          growth_trajectory:{...trajectoryField,content:lines(values.growth_trajectory||'')},
        };
      }
      await onSave(update);
    }catch(error){
      message.error(error instanceof Error?error.message:'岗位定义保存失败');
    }finally{
      setSaving(false);
    }
  };
  return <Modal title={title} width={860} open={open} onCancel={onCancel} onOk={()=>void submit()} okText="保存优化" cancelText="取消" confirmLoading={saving} destroyOnHidden>
    <Form form={form} layout="vertical" className="emerging-definition-editor">
      {(section==='all'||section==='definition')&&<>
        <Form.Item name="position_name" label="岗位名称" rules={[{required:true,message:'请输入岗位名称'}]}><Input maxLength={120}/></Form.Item>
        <Form.Item name="position_summary" label="岗位概述" rules={[{required:true,message:'请输入岗位概述'}]}><Input.TextArea autoSize={{minRows:3,maxRows:7}} maxLength={1000} showCount/></Form.Item>
      </>}
      {(section==='all'||section==='responsibilities')&&<Form.Item name="core_responsibilities" label="核心职责（每行一条）" rules={[{required:true,message:'请至少填写一条核心职责'}]}><Input.TextArea autoSize={{minRows:5,maxRows:12}}/></Form.Item>}
      {(section==='all'||section==='skills')&&<>
        <Form.Item name="required_skills" label="必备技能"><Select mode="tags" tokenSeparators={[',','，']} placeholder="输入技能后回车"/></Form.Item>
        <Form.Item name="bonus_skills" label="加分技能"><Select mode="tags" tokenSeparators={[',','，']} placeholder="输入技能后回车"/></Form.Item>
      </>}
      {(section==='all'||section==='scenarios')&&<Form.Item name="industry_scenarios" label="典型行业应用场景"><Select mode="tags" tokenSeparators={[',','，']} placeholder="输入场景后回车"/></Form.Item>}
      {(section==='all'||section==='enterprises')&&<Form.Item name="representative_enterprises" label="代表企业"><Select mode="tags" tokenSeparators={[',','，']} placeholder="输入企业后回车"/></Form.Item>}
      {(section==='all'||section==='timeline')&&<Form.Item name="growth_trajectory" label="演化时间线（每行一个阶段）"><Input.TextArea autoSize={{minRows:3,maxRows:8}} placeholder="例如：2026-07-31：观测到 5 份岗位样本"/></Form.Item>}
    </Form>
  </Modal>;
}
