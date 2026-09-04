import {useEffect,useMemo,useState} from 'react';
import {App,Button,Card,Descriptions,Empty,List,Space,Spin,Table,Tag,Timeline,Typography} from 'antd';
import {useNavigate,useParams} from 'react-router-dom';
import {ApartmentOutlined} from '@ant-design/icons';
import {ApiError} from '../../../shared/api';
import {Failure,type LoadState} from '../../../shared/components/States';
import {EvidenceDrawer,EvidenceRow} from '../../evidence/components/EvidenceViewer';
import {useAuth} from '../../auth/AuthContext';
import {getEmergingDisplay,updateEmergingDisplay,type EmergingPosition,type EmergingAsset} from '../api';
import {isTechnicalIdentifier} from '../lib/discoveryDisplay';
import {emergingCacheKeys,invalidateEmergingCache} from '../cache';
import {EmergingDefinitionEditor,type EmergingDefinitionSection,type EmergingDefinitionUpdate} from '../components/EmergingDefinitionEditor';

type Json=Record<string,unknown>;
const record=(value:unknown):Json=>value&&typeof value==='object'&&!Array.isArray(value)?value as Json:{};
const list=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const text=(...values:unknown[])=>values.find(value=>typeof value==='string'&&value.trim()) as string|undefined;
const number=(...values:unknown[])=>values.find(value=>typeof value==='number'&&Number.isFinite(value)) as number|undefined;
const responsibilityLabel=(value:string)=>/^负责第\s*\d+\s*类业务场景的智能体评测、追踪与上线$/.test(value.trim())
  ? '建立智能体效果评测与运行监控机制，推动应用稳定上线并持续优化'
  : value;
const anonymousEnterpriseTypes=['企业知识管理服务商','智能客服解决方案商','流程自动化平台','AI 应用研发企业'];
const enterpriseLabel=(value:string)=>{
  const matched=value.trim().match(/^演示企业\s*[·・._-]?\s*(\d+)$/);
  return matched?anonymousEnterpriseTypes[(Number(matched[1])-1)%anonymousEnterpriseTypes.length]:value;
};
const normalizeEnterpriseCoverage=(value:unknown)=>{
  if(Array.isArray(value))return Object.fromEntries(value.map(name=>[enterpriseLabel(String(name)),null])) as Record<string,number|null>;
  return Object.entries(record(value)).reduce<Record<string,number|null>>((result,[name,count])=>{
    const label=enterpriseLabel(name);
    result[label]=(result[label]||0)+(typeof count==='number'&&Number.isFinite(count)?count:0);
    return result;
  },{});
};
const cleanWindow=(value:string)=>value.split('@')[0].replace('..',' 至 ');
const skillLabel=(value:unknown)=>{
  const item=record(value);
  const label=text(item.content,item.skill_name,item.raw_skill,item.name,item.label,item.skill_id,item.normalized_skill_id,item.id)
    ||(typeof value==='string'?value:undefined);
  return label&&!isTechnicalIdentifier(label)?label:'待命名技能';
};
const sourceEvidenceContexts=(fields:Json)=>{
  const bySource=new Map<string,string[]>();
  const add=(value:unknown)=>{
    const evidence=record(value);
    const source=text(evidence.source_jd_id,evidence.source_id,evidence.document_id);
    const quote=text(evidence.original_text_snippet,evidence.quote);
    if(!source||!quote)return;
    const snippets=bySource.get(source)||[];
    if(!snippets.includes(quote))snippets.push(quote);
    bySource.set(source,snippets);
  };
  Object.values(fields).forEach(value=>{
    const field=record(value);
    list(field.evidence).forEach(add);
    list(field.items).forEach(item=>list(record(item).evidence).forEach(add));
  });
  return new Map([...bySource].map(([source,snippets])=>[source,snippets.join('\n\n')]));
};
const normalizeTimeline=(values:unknown[])=>values.map((value,index)=>{
  if(typeof value==='string'){
    const separator=Math.max(value.lastIndexOf('：'),value.lastIndexOf(':'));
    const window=separator>=0?value.slice(0,separator).trim():'';
    const detail=separator>=0?value.slice(separator+1).trim():value.trim();
    return {window_id:window||`阶段 ${index+1}`,description:/^\d+$/.test(detail)?`观测到 ${detail} 份岗位样本`:detail};
  }
  const row=record(value);
  const count=number(row.member_count,row.sample_count);
  return {...row,description:text(row.description,row.event,row.summary)||(count!=null?`观测到 ${count} 份岗位样本`:'该阶段尚未提供演化说明')};
});

export function EmergingDetail(){
  const {emergingId=''}=useParams();
  const navigate=useNavigate();
  const {message}=App.useApp();
  const {can,user}=useAuth();
  const [state,setState]=useState<LoadState<EmergingPosition>>({kind:'loading'});
  const [editing,setEditing]=useState<Exclude<EmergingDefinitionSection,'all'>|null>(null);
  const [evidenceOpen,setEvidenceOpen]=useState<{skill:string;items:unknown[]}|null>(null);
  useEffect(()=>{
    getEmergingDisplay(emergingId).then(data=>setState({kind:'success',data})).catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}));
  },[emergingId]);
  const view=useMemo(()=>{
    if(state.kind!=='success')return null;
    const item=state.data;
    const fields=record(item.field_evidence);
    const published=record(item.published_snapshot);
    const publishedDefinition=record((item as Partial<EmergingAsset>).asset_definition??published.definition);
    const lifecycle=record(fields.candidate_lifecycle);
    const lifecycleWindows=list(lifecycle.observed_window_ids).map(String);
    const responsibilityValue=record(fields.core_responsibilities).content;
    const skillField=record(fields.required_skills);
    const skillValue=skillField.content;
    const skillItems=list(skillField.items);
    const bonusSkillField=record(fields.bonus_skills);
    const bonusSkillItems=list(bonusSkillField.items);
    const bonusSkillValue=bonusSkillField.content;
    const enterpriseValue=record(fields.representative_enterprises).content;
    const trajectoryValue=record(fields.growth_trajectory).content;
    const publishedFields=record(publishedDefinition.field_evidence);
    const definition=text(record(publishedFields.position_summary).content);
    const responsibilities=(emergingId.startsWith('formal:')?item.core_responsibilities:list(responsibilityValue).length?list(responsibilityValue):item.core_responsibilities)
      .map(value=>responsibilityLabel(String(value)))
      .filter(value=>value.trim()!==definition?.trim());
    const skills=emergingId.startsWith('formal:')||item.required_skills.length?item.required_skills:(skillItems.length?skillItems:list(skillValue));
    const bonusSkills=emergingId.startsWith('formal:')||item.bonus_skills.length?item.bonus_skills:(bonusSkillItems.length?bonusSkillItems:list(bonusSkillValue));
    const enterprises=normalizeEnterpriseCoverage(enterpriseValue);
    const trajectory=list(trajectoryValue).length?list(trajectoryValue):list(published.lineage);
    const timeline=normalizeTimeline(lifecycleWindows.length>trajectory.length?lifecycleWindows.map((windowId,index)=>({
      window_id:windowId,
      description:index===0?'首次识别到候选岗位':index===lifecycleWindows.length-1?'达到稳定新兴岗位标准':'持续观测到同一候选岗位',
    })):trajectory);
    return {
      item,displayPositionName:isTechnicalIdentifier(item.position_name)?'新兴岗位':item.position_name,fields,responsibilities,skills,bonusSkills,enterprises,timeline,
      definition,
    };
  },[state,emergingId]);
  const saveDefinition=async(values:EmergingDefinitionUpdate)=>{
    if(state.kind!=='success')return;
    const previous=state.data;
    const updated=await updateEmergingDisplay(previous.emerging_id,values);
    setState({kind:'success',data:{...previous,...updated,germination_assessment:previous.germination_assessment}});
    setEditing(null);
    invalidateEmergingCache(user?.user_id??'anonymous',[emergingCacheKeys.assets,emergingCacheKeys.governance,emergingCacheKeys.published]);
    message.success('人工优化已保存并创建新版本，岗位已进入重新审核');
  };
  if(state.kind==='loading')return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载…</span></div>;
  if(state.kind==='error')return <Failure {...state}/>;
  if(!view)return null;
  const {item,displayPositionName,fields,responsibilities,skills,bonusSkills,enterprises,timeline,definition}=view;
  const evidenceContexts=sourceEvidenceContexts(fields);
  const optimize=(section:Exclude<EmergingDefinitionSection,'all'>)=>can('emerging.candidate.manage')?<Button size="small" onClick={()=>setEditing(section)}>人工优化</Button>:undefined;
  const editorTitles:Record<Exclude<EmergingDefinitionSection,'all'>,string>={
    definition:'岗位定义',responsibilities:'核心职责',skills:'技能要求',scenarios:'典型行业应用场景',enterprises:'企业覆盖',timeline:'演化时间线',
  };
  const evidenceRows=(evidenceOpen?.items||[]).map((value,index)=>{
    const evidence=record(value);
    const locator=record(evidence.locator);
    const sourceId=text(evidence.source_jd_id,evidence.source_id,evidence.document_id);
    const quote=text(evidence.original_text_snippet,evidence.quote)||'证据原文未提供';
    const context=(sourceId&&evidenceContexts.get(sourceId))||quote;
    const explicitStart=number(evidence.start,locator.start);
    const located=context.indexOf(quote);
    const start=located>=0?located:(explicitStart??null);
    const end=number(evidence.end,locator.end)??(start!=null?start+quote.length:null);
    return {
      key:`${text(evidence.source_jd_id,evidence.source_id)||'evidence'}-${index}`,
      alignment:start!=null&&end!=null?'exact':'unresolved',
      occurrence_index:number(evidence.occurrence_index,locator.occurrence_index)??index,
      start,
      end,
      quote,
      rawText:context,
      documentId:text(evidence.source_jd_id,evidence.document_id),
      meta:<Descriptions size="small" column={1} items={[
        {key:'source',label:'来源',children:text(evidence.data_source,evidence.source_name)||'来源未提供'},
        {key:'window',label:'观测时间',children:text(evidence.window_id)||'时间未提供'},
        {key:'document',label:'招聘样本',children:text(evidence.source_jd_id,evidence.document_id)||'样本标识未提供'},
      ]}/>,
    };
  });
  return <div className="emerging-detail">
    <div className="page-heading page-heading-row">
      <div>
      <Typography.Title level={2}>{displayPositionName}</Typography.Title>
      <Typography.Paragraph type="secondary">公开新兴岗位画像、技能证据与演化信息。</Typography.Paragraph>
      </div>
      <Button type="primary" icon={<ApartmentOutlined aria-hidden/>} onClick={()=>navigate(`/emerging/${encodeURIComponent(emergingId)}/graph`)}>查看图谱</Button>
    </div>
    <Card className="profile emerging-definition-card"><div className="emerging-definition-grid">
      <section><div className="emerging-insight-heading"><Typography.Title level={4}>岗位定义</Typography.Title>{optimize('definition')}</div><Descriptions column={1} items={[
        {key:'position-name',label:'岗位名称',children:<Typography.Text strong>{displayPositionName}</Typography.Text>},
        {key:'position-summary',label:'岗位概述',children:definition||'尚未发布岗位概述'},
      ]}/></section>
      <section><div className="emerging-insight-heading"><Typography.Title level={4}>核心职责</Typography.Title>{optimize('responsibilities')}</div>{responsibilities.length?<List dataSource={responsibilities} renderItem={value=><List.Item>{value}</List.Item>}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无职责数据"/>}</section>
    </div></Card>
    <Card className="profile" title="技能要求" extra={optimize('skills')}>{skills.length||bonusSkills.length?<Table rowKey={(row,index)=>`${row.requirement}-${skillLabel(row.value)}-${index}`} pagination={{pageSize:10,showSizeChanger:false,hideOnSinglePage:true}} dataSource={[
      ...skills.map(value=>({value,requirement:'required' as const})),
      ...bonusSkills.map(value=>({value,requirement:'bonus' as const})),
    ]} columns={[
      {title:'技能',render:(_,row)=>skillLabel(row.value)},
      {title:'岗位要求',render:(_,row)=>row.requirement==='bonus'?<Tag className="skill-requirement-tag-bonus">加分技能</Tag>:<Tag color="success">必备技能</Tag>},
      {title:'证据支持',render:(_,value)=>{const row=record(value.value);const jd=number(row.support_jd_count);const sources=number(row.support_source_count);const companies=number(row.support_enterprise_count);const evidenceItems=list(row.evidence);return <Space wrap size={[4,4]}>{jd!=null&&<Tag>{jd} 份 JD</Tag>}{sources!=null&&<Tag>{sources} 个来源</Tag>}{companies!=null&&<Tag>{companies} 家企业</Tag>}{evidenceItems.length>0&&<Button type="link" size="small" onClick={()=>setEvidenceOpen({skill:skillLabel(value.value),items:evidenceItems})}>查看证据上下文</Button>}</Space>;}},
    ]}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无技能要求"/>}</Card>
    <Card className="profile emerging-insight-card"><div className="emerging-insight-grid">
      <section><div className="emerging-insight-heading"><Typography.Title level={4}>典型行业应用场景</Typography.Title>{optimize('scenarios')}</div>{item.industry_scenarios.length?<Space wrap>{item.industry_scenarios.map(value=><Tag key={value}>{value}</Tag>)}</Space>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无典型行业应用场景"/>}</section>
      <section><div className="emerging-insight-heading"><Typography.Title level={4}>企业覆盖</Typography.Title>{optimize('enterprises')}</div>{Object.keys(enterprises).length?<><Typography.Paragraph type="secondary">共覆盖 {Object.keys(enterprises).length} 家代表企业</Typography.Paragraph><Space wrap>{Object.entries(enterprises).map(([name,count])=><Tag key={name}>{name}{count==null?'':` · ${count} 份 JD`}</Tag>)}</Space></>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无代表企业数据"/>}</section>
      <section><div className="emerging-insight-heading"><Typography.Title level={4}>演化时间线</Typography.Title>{optimize('timeline')}</div>{timeline.length?<Timeline items={timeline.map((value,index)=>{const row=record(value);const rawWindow=text(row.window_id,row.time_window,row.period);return {children:<><Typography.Text strong>{rawWindow?cleanWindow(rawWindow):`阶段 ${index+1}`}</Typography.Text><br/><Typography.Text type="secondary">{text(row.description,row.event,row.summary)||'该阶段尚未提供演化说明'}</Typography.Text></>}})}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无演化时间线"/>}</section>
    </div></Card>
    <EmergingDefinitionEditor open={Boolean(editing)} item={item} section={editing||undefined} title={`人工优化 · ${editing?editorTitles[editing]:''}`} onCancel={()=>setEditing(null)} onSave={saveDefinition}/>
    <EvidenceDrawer open={Boolean(evidenceOpen)} title="技能证据链" subtitle={evidenceOpen?.skill||''} onClose={()=>setEvidenceOpen(null)}>
      {evidenceRows.length?<Space direction="vertical" className="full" size={20}>{evidenceRows.map(row=>emergingId.startsWith('formal:')?<article className="emerging-graph-evidence" key={row.key}><blockquote>{row.quote}</blockquote></article>:<EvidenceRow key={row.key} row={row}/>)}</Space>:<Empty description="暂无可展示的证据上下文"/>}
    </EvidenceDrawer>
  </div>;
}
