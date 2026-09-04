import {useState} from 'react';
import {App,Button,Descriptions,Empty,Space,Table,Tag,Typography} from 'antd';
import {useAuth} from '../../auth/AuthContext';
import {EmptyState,WorkbenchState} from '../../../shared/components/States';
import {localizeSystemMessage} from '../../../shared/api';
import {statusText} from '../../../shared/idText';
import {generateDefinition,listCandidates,listDefinitionVersions,promoteCandidate,publishCandidate,reviewCandidate,submitCandidateReview,updateCandidate,type DefinitionVersion,type EmergingPosition} from '../api';
import {emergingCacheKeys,invalidateEmergingCache} from '../cache';
import {useEmergingCachedQuery} from '../useEmergingCachedQuery';
import {isTechnicalIdentifier} from '../lib/discoveryDisplay';
import {EmergingDefinitionEditor,type EmergingDefinitionUpdate} from '../components/EmergingDefinitionEditor';

const object=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
const values=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const valueText=(...candidates:unknown[])=>candidates.find(value=>typeof value==='string'&&value.trim()) as string|undefined;
const displayTime=(value:string|null)=>value?new Intl.DateTimeFormat('zh-CN',{dateStyle:'medium',timeStyle:'short'}).format(new Date(value)):'时间未提供';
const positionLabel=(value:string)=>isTechnicalIdentifier(value)?'待命名岗位':value;
function DefinitionVersions({versions}:{versions:DefinitionVersion[]}){
  if(!versions.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无岗位定义版本"/>;
  return <Table
    rowKey="version_id"
    pagination={false}
    dataSource={versions.map((version,index)=>({...version,display_version:index+1}))}
    columns={[
      {title:'版本',dataIndex:'display_version',render:(value:number,row)=><Space direction="vertical" size={0}><Typography.Text>第 {value} 版</Typography.Text>{row.selected&&<Tag color="success">当前版本</Tag>}</Space>},
      {title:'岗位定义',render:(_,row)=>valueText(object(row.snapshot).position_summary,object(object(object(row.snapshot).field_evidence).position_summary).content,object(row.snapshot).description)||'岗位概述未提供'},
      {title:'创建信息',render:(_,row)=><Space direction="vertical" size={0}><span>管理员操作</span><Typography.Text type="secondary">{displayTime(row.created_at)}</Typography.Text></Space>},
      {title:'保存状态',render:()=><Tag>已保存，可恢复</Tag>},
    ]}
    expandable={{expandedRowRender:row=>{
      const snapshot=object(row.snapshot);
      const responsibilities=values(snapshot.core_responsibilities).map(String);
      const skills=values(snapshot.required_skills);
      return <Descriptions size="small" column={1} items={[
        {key:'responsibilities',label:'职责',children:responsibilities.length?responsibilities.join('；'):'未提供'},
        {key:'skills',label:'核心技能',children:skills.length?<Space wrap>{skills.map((skill,index)=>{const item=object(skill);return <Tag key={`${valueText(item.skill_id,item.name)||index}`}>{valueText(item.skill_name,item.name,item.skill_id)||String(skill)}</Tag>})}</Space>:'未提供'},
        {key:'evidence',label:'证据引用',children:`${values(snapshot.evidence_jd_ids).length} 条引用`},
      ]}/>;
    }}}
  />;
}

export function EmergingWorkbench(){
  const {message,modal}=App.useApp();
  const {user}=useAuth();
  const userId=user?.user_id??'anonymous';
  const [busyKey,setBusyKey]=useState<string|null>(null);
  const [rowOverrides,setRowOverrides]=useState<Record<string,EmergingPosition>>({});
  const [definitionEditor,setDefinitionEditor]=useState<EmergingPosition|null>(null);
  const governanceQuery=useEmergingCachedQuery<EmergingPosition[]>(
    emergingCacheKeys.governance,
    listCandidates,
  );
  const state=governanceQuery.state;

  const replaceRow=(item:EmergingPosition)=>setRowOverrides(current=>({...current,[item.emerging_id]:item}));
  const done=async<T,>(key:string,label:string,work:()=>Promise<T>,onSuccess?:(result:T)=>void)=>{
    setBusyKey(key);
    try{
      const result=await work();
      onSuccess?.(result);
      invalidateEmergingCache(userId,[emergingCacheKeys.assets,emergingCacheKeys.governance,emergingCacheKeys.published]);
      message.success(label);
    }catch(error){
      message.error(error instanceof Error?localizeSystemMessage(error.message):'操作失败，请稍后重试。');
    }finally{
      setBusyKey(null);
    }
  };
  const openVersions=async(item:EmergingPosition)=>{
    try{
      const versions=await listDefinitionVersions(item.emerging_id);
      modal.info({title:`岗位定义版本 · ${positionLabel(item.position_name)}`,width:960,content:<DefinitionVersions versions={versions}/>});
    }catch(error){
      message.error(error instanceof Error?localizeSystemMessage(error.message):'岗位定义版本加载失败，请稍后重试。');
    }
  };
  const saveDefinition=async(values:EmergingDefinitionUpdate)=>{
    if(!definitionEditor)return;
    const updated=await updateCandidate(definitionEditor.emerging_id,values);
    replaceRow(updated);
    setDefinitionEditor(null);
    invalidateEmergingCache(userId,[emergingCacheKeys.assets,emergingCacheKeys.governance,emergingCacheKeys.published]);
    message.success('岗位定义优化已保存，并已创建新版本');
  };

  return <div className="emerging-workbench">
    {/* 页面说明与业务列表分层，避免两个同级大标题争夺注意力。 */}
    <div className="page-heading">
      <Typography.Title level={2}>新兴岗位候选</Typography.Title>
      <Typography.Paragraph type="secondary">审核已进入治理的新兴岗位定义；发布后会立即出现在新兴岗位公开页，并可继续转为标准岗位。</Typography.Paragraph>
    </div>

    <section className="emerging-governance-surface" aria-label="新兴岗位治理列表">
      {state.kind==='success'&&state.data.length===0
        ?<EmptyState centered text="暂无进入治理的候选"/>
        :<WorkbenchState
          state={state}
          retry={governanceQuery.reload}
          render={items=><Table
        rowKey="emerging_id"
        dataSource={items.map(item=>rowOverrides[item.emerging_id]??item)}
        columns={[
          {title:'岗位',dataIndex:'position_name',render:(value:string)=>positionLabel(value)},
          {title:'状态',dataIndex:'status',render:(value:string)=>statusText(value)},
          {title:'证据',render:(_:unknown,item:EmergingPosition)=>{const count=object(item.score_dimensions.counts).independent_postings;return `${typeof count==='number'?count:item.evidence_jd_ids.length} 份 JD`}},
          {
            title:'操作',
            render:(_:unknown,item:EmergingPosition)=><Space wrap>
              <Button disabled={busyKey!==null} onClick={()=>setDefinitionEditor(item)}>人工优化</Button>
              <Button loading={busyKey===`${item.emerging_id}:generate`} disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:generate`,'已生成完整岗位定义版本',()=>generateDefinition(item.emerging_id),result=>{replaceRow(result);setDefinitionEditor(result)})}>生成定义</Button>
              <Button disabled={busyKey!==null} onClick={()=>void openVersions(item)}>版本</Button>
              {(item.status==='draft'||item.status==='rejected')&&<Button disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:submit`,'已提交审核',()=>submitCandidateReview(item.emerging_id),replaceRow)}>提交审核</Button>}
              {item.status==='pending_review'&&<>
                <Button disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:approve`,'审核通过',()=>reviewCandidate(item.emerging_id,'approved','人工核验定义和证据完整'),replaceRow)}>审核通过</Button>
                <Button danger disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:reject`,'已驳回',()=>reviewCandidate(item.emerging_id,'rejected','证据或定义需要补充'),replaceRow)}>驳回</Button>
              </>}
              {item.status==='approved'&&<Button type="primary" loading={busyKey===`${item.emerging_id}:publish`} disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:publish`,'已发布，新兴岗位公开页已同步更新',()=>publishCandidate(item.emerging_id),replaceRow)}>发布</Button>}
              {item.status==='published'&&<Button type="primary" loading={busyKey===`${item.emerging_id}:promote`} disabled={busyKey!==null} onClick={()=>void done(`${item.emerging_id}:promote`,'已创建标准岗位，等待图谱映射/构建',()=>promoteCandidate(item.emerging_id))}>转为标准岗位</Button>}
            </Space>,
          },
        ]}
          />}
        />}
    </section>
    <EmergingDefinitionEditor open={Boolean(definitionEditor)} item={definitionEditor} title={definitionEditor?`编辑岗位定义 · ${positionLabel(definitionEditor.position_name)}`:'编辑岗位定义'} onCancel={()=>setDefinitionEditor(null)} onSave={saveDefinition}/>
  </div>;
}
