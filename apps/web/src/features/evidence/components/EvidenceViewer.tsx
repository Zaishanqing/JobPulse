import {useEffect,useState,type ReactNode} from 'react';
import {Descriptions,Drawer,Space,Spin,Typography} from 'antd';
import {relationEvidence} from '../api';
import type {EvidenceSupport} from '../types';
import type {Relation} from '../../graph/types';
import {ApiError} from '../../../shared/api';
import {domainText} from '../../../shared/idText';
import {EmptyState,Failure,ToastAlert as Alert,type LoadState} from '../../../shared/components/States';

const kindLabels:Record<string,string>={skill:'技能',education:'学历',experience:'经验',certificate:'证书',soft_skill:'软技能',company_fact:'公司背景',employment_fact:'用工信息'};
const modalityLabels:Record<string,string>={required:'必须掌握',preferred:'优先考虑',bonus:'加分项',unknown:'原文未说明'};

export type EvidenceDrawerRow={
  key:string|number;
  alignment:string;
  occurrence_index:number|null;
  start:number|null;
  end:number|null;
  quote:string;
  rawText?:string;
  documentId?:string;
  meta?:ReactNode;
};

export function EvidenceDrawer({open,title,subtitle,onClose,children}:{open:boolean;title:string;subtitle:string;onClose:()=>void;children:ReactNode}){
  return <Drawer
    className="evidence-drawer"
    width={440}
    title={<div><span>{title}</span><small>{subtitle}</small></div>}
    open={open}
    onClose={onClose}
  >
    {children}
  </Drawer>;
}

export function EvidenceRow({row}:{row:EvidenceDrawerRow}){
  const exact=row.alignment==='exact'&&row.start!==null&&row.end!==null&&Boolean(row.rawText);
  return <div className="evidence-row">
    <Space wrap size={4} className="evidence-location">
      <Typography.Text type="secondary">出现位置：{String(row.occurrence_index??'-')}</Typography.Text>
      {row.start!==null&&row.end!==null&&<Typography.Text type="secondary">坐标 {row.start}–{row.end}</Typography.Text>}
    </Space>
    {row.meta}
    {!exact&&<Alert type="warning" showIcon title="该证据不能用于正式发布。"/>}
    <div className="evidence-copy">
      <Typography.Paragraph className="rawText">
        {exact&&row.rawText
          ?<>{row.rawText.slice(0,row.start!)}<mark>{row.rawText.slice(row.start!,row.end!)}</mark>{row.rawText.slice(row.end!)}</>
          :row.quote}
      </Typography.Paragraph>
    </div>
  </div>;
}

export function EvidenceViewer({relation,onClose}:{relation:Relation|null;onClose:()=>void}){
  const [state,setState]=useState<LoadState<EvidenceSupport[]>>({kind:'loading'});
  useEffect(()=>{
    if(!relation)return;
    relationEvidence(relation.relation_id)
      .then(supports=>setState({kind:'success',data:supports}))
      .catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}));
  },[relation]);
  const rows=(state.kind==='success'?state.data:[]).map(support=>{
    const original=support.original_requirement;
    const originalText=[original.text,...(original.items||[]).map(item=>item.name)].filter(Boolean).join('、')||'未返回';
    return {
      key:support.support_id,
      alignment:support.evidence.alignment,
      occurrence_index:support.evidence.occurrence_index,
      start:support.evidence.start,
      end:support.evidence.end,
      quote:support.evidence.quote,
      rawText:support.source.raw_text,
      documentId:support.evidence.document_id||support.source.document_id,
      meta:<Descriptions column={1} size="small" items={[
        {key:'source',label:'原始要求',children:originalText},
        {key:'kind',label:'要求类型',children:kindLabels[original?.kind||'']||original?.kind||'-'},
        {key:'modality',label:'要求方式',children:modalityLabels[original?.modality||'']||original?.modality||'-'},
        {key:'domain',label:'岗位领域',children:domainText(relation?.category_name||relation?.category_code)},
        {key:'normalized',label:'归一化技能',children:support.normalized_skill.canonical_name},
      ]}/>,
    };
  });
  return <EvidenceDrawer open={Boolean(relation)} title="证据链" subtitle={relation?.canonical_name||''} onClose={onClose}>
    {state.kind==='loading'?<Spin/>
      :state.kind==='error'?<Failure {...state}/>
      :rows.length===0?<EmptyState text="暂无可用证据"/>
      :<Space direction="vertical" className="full" size={20}>{rows.map(row=><EvidenceRow key={row.key} row={row}/>)}</Space>}
  </EvidenceDrawer>;
}
