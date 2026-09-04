import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {App,Button,Descriptions,Empty,Form,Input,Modal,Select,Skeleton,Space,Table,Tag,Typography} from 'antd';
import {excludeUnresolved,listCatalogSkills,listUnresolved,resolveUnresolved,suggestNormalizations} from '../api';
import type {NormalizationSuggestion,UnresolvedItem} from '../types';
import {ApiError,type CatalogSkill} from '../../../shared/api';
import {ToastAlert as Alert,WorkbenchState,type LoadState} from '../../../shared/components/States';

export function UnresolvedWorkbench({embedded=false}:{embedded?:boolean}){
  const {message,modal}=App.useApp();
  const [mappingForm]=Form.useForm<{skill_id:string}>();
  const [state,setState]=useState<LoadState<UnresolvedItem[]>>({kind:'loading'});
  const [catalog,setCatalog]=useState<CatalogSkill[]>([]);
  const [current,setCurrent]=useState<UnresolvedItem>();
  const [query,setQuery]=useState('');
  const [selectedSkillId,setSelectedSkillId]=useState<string>();
  const [suggestions,setSuggestions]=useState<NormalizationSuggestion[]>([]);
  const [suggestionState,setSuggestionState]=useState<'idle'|'loading'|'success'|'error'>('idle');
  const [suggestionError,setSuggestionError]=useState('');
  const [submitting,setSubmitting]=useState(false);
  const [actionError,setActionError]=useState<ApiError>();
  const suggestionRequest=useRef(0);
  const load=useCallback(()=>{
    Promise.all([listUnresolved(),listCatalogSkills()])
      .then(([items,skills])=>{setCatalog(skills);setState({kind:'success',data:items})})
      .catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}));
  },[]);
  useEffect(()=>{void load()},[load]);
  const loadSuggestions=useCallback((item:UnresolvedItem)=>{
    const requestId=++suggestionRequest.current;
    setSuggestionState('loading');setSuggestionError('');setSuggestions([]);
    suggestNormalizations(item.source_name,item.raw_text.slice(0,4000),5)
      .then(items=>{if(requestId===suggestionRequest.current){setSuggestions(items);setSuggestionState('success')}})
      .catch((reason:ApiError)=>{if(requestId===suggestionRequest.current){setSuggestionError(reason.message);setSuggestionState('error')}});
  },[]);
  const candidates=useMemo(()=>{
    const key=query.trim().toLocaleLowerCase();
    if(!key)return catalog.slice(0,30);
    return catalog.filter(item=>item.skill_name.toLocaleLowerCase().includes(key)||(item.category||'').toLocaleLowerCase().includes(key)).slice(0,50);
  },[catalog,query]);
  const selectCandidates=useMemo(()=>{
    if(!selectedSkillId||candidates.some(item=>item.skill_id===selectedSkillId))return candidates;
    const selected=catalog.find(item=>item.skill_id===selectedSkillId);
    return selected?[selected,...candidates]:candidates;
  },[candidates,catalog,selectedSkillId]);
  const confirm=async(values:{skill_id:string})=>{
    if(!current||submitting)return;
    setSubmitting(true);setActionError(undefined);
    try{
      await resolveUnresolved(current,values.skill_id);
      message.success(`“${current.source_name}”已映射到标准技能`);
      setCurrent(undefined);setSelectedSkillId(undefined);await load();
    }catch(reason){setActionError(reason as ApiError)}finally{setSubmitting(false)}
  };
  const reject=(item:UnresolvedItem)=>{
    const formId=`exclude-${item.id}`;
    const dialog=modal.confirm({
      title:`确认不让“${item.source_name}”进入下游？`,
      content:<><Typography.Paragraph>该技能仍会保留在 JD 原始解析结果中，但不会进入发布图谱、匹配或统计。</Typography.Paragraph><Form id={formId} layout="vertical" onFinish={async(values:{reason:string})=>{if(submitting)return;setSubmitting(true);try{await excludeUnresolved(item,values.reason);message.success('该技能已标记为不进入下游');setCurrent(undefined);dialog.destroy();await load()}catch(reason){message.error((reason as ApiError).message)}finally{setSubmitting(false)}}}><Form.Item name="reason" label="排除原因" rules={[{required:true,message:'请说明为什么不应进入下游'}]}><Input.TextArea placeholder="例如：不是技能、表述过于宽泛或当前目录中没有对应能力"/></Form.Item></Form></>,
      okText:'确认不进入下游',cancelText:'返回检查',okButtonProps:{htmlType:'submit',form:formId,loading:submitting},
    });
  };
  return <>
    {!embedded&&<div className="page-heading">
      <Typography.Title level={2}>技能归一化审核</Typography.Title>
      <Typography.Paragraph type="secondary">逐条确认抽取出的技能应映射到哪个标准技能；无法确认的可仅保留原文。</Typography.Paragraph>
    </div>}
    <WorkbenchState title="待归一化技能" state={state} retry={load} render={items=>items.length===0?<Typography.Paragraph type="secondary">{embedded?'当前没有待归一化技能。':'当前没有待归一化技能，可以前往审核中心处理 JD。'}</Typography.Paragraph>:<Table rowKey="id" dataSource={items} pagination={{pageSize:12,showSizeChanger:false}} columns={[
      {title:'待判断技能',width:260,render:(_:unknown,item:UnresolvedItem)=><Space direction="vertical" size={2}><Typography.Text strong>{item.source_name}</Typography.Text><Typography.Text type="secondary">{item.reason}</Typography.Text></Space>},
      {title:'来自哪条 JD',render:(_:unknown,item:UnresolvedItem)=><Space direction="vertical" size={2}><Typography.Text>{item.jd_title}</Typography.Text><Typography.Text type="secondary">{item.source_name_label||item.source_type}</Typography.Text></Space>},
      {title:'处理后的影响',width:250,render:()=> <Typography.Text type="secondary">确认映射：该技能进入下游；仅保留原文：该技能不进入下游，不影响同一 JD 的其他内容。</Typography.Text>},
      {title:'操作',width:280,render:(_:unknown,item:UnresolvedItem)=><Space><Button type="primary" onClick={()=>{mappingForm.resetFields();setCurrent(item);setQuery(item.source_name);setSelectedSkillId(undefined);setActionError(undefined);loadSuggestions(item)}}>匹配标准技能</Button><Button onClick={()=>reject(item)}>仅保留原文</Button></Space>},
    ]}/>} />
    <Modal title="确认技能归一化" open={Boolean(current)} width={820} footer={null} onCancel={()=>{if(!submitting){setCurrent(undefined);setSelectedSkillId(undefined)}}}>
      {current?<Space direction="vertical" size={18} style={{width:'100%'}}>
        <Typography.Title level={4}>判断“{current.source_name}”对应的标准技能</Typography.Title>
        <Descriptions bordered size="small" column={1} items={[
          {key:'jd',label:'来源 JD',children:current.jd_title},
          {key:'reason',label:'为什么需要人工判断',children:current.reason},
          {key:'text',label:'JD 原文',children:<Typography.Paragraph ellipsis={{rows:6,expandable:true,symbol:'展开全文'}} style={{marginBottom:0,whiteSpace:'pre-wrap'}}>{current.raw_text}</Typography.Paragraph>},
        ]}/>
        <section className="normalization-suggestions" aria-labelledby="normalization-suggestions-title">
          <div className="normalization-suggestions-head">
            <div><Typography.Title id="normalization-suggestions-title" level={5}>推荐的标准技能</Typography.Title><Typography.Text type="secondary">分数用于辅助判断，选择后仍需人工确认保存。</Typography.Text></div>
            {suggestionState==='success'&&suggestions.length?<Tag color={suggestions[0].semantic_available?'default':'warning'}>{suggestions[0].semantic_available?'Hybrid 排序':'Lexical fallback'}</Tag>:null}
          </div>
          {suggestionState==='loading'?<Skeleton active paragraph={{rows:3}} title={false}/>:null}
          {suggestionState==='error'?<Alert type="warning" showIcon message="候选推荐暂不可用" description={<Space direction="vertical" size={6}><span>{suggestionError}。仍可使用下方手动搜索。</span><Button size="small" onClick={()=>loadSuggestions(current)}>重试推荐</Button></Space>}/>:null}
          {suggestionState==='success'&&suggestions.length===0?<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前没有可推荐的标准技能，请使用手动搜索"/>:null}
          {suggestionState==='success'&&suggestions.length?<div className="normalization-suggestion-list">
            {suggestions.map(item=><button type="button" key={item.skill_id} className={`normalization-suggestion${selectedSkillId===item.skill_id?' is-selected':''}`} aria-pressed={selectedSkillId===item.skill_id} onClick={()=>{setSelectedSkillId(item.skill_id);mappingForm.setFieldValue('skill_id',item.skill_id)}}>
              <span className="normalization-suggestion-rank">{item.rank}</span>
              <span className="normalization-suggestion-main"><span><strong>{item.skill_name}</strong><small>{item.category||'标准技能目录'}</small></span>{item.matched_alias?<span className="normalization-alias">alias：{item.matched_alias}</span>:null}<span className="normalization-reasons">{item.reasons.map(reason=><Tag key={reason}>{reason}</Tag>)}</span></span>
              <span className="normalization-scores"><span><small>综合</small><b>{item.combined_score.toFixed(3)}</b></span><span><small>文本</small><b>{item.lexical_score.toFixed(3)}</b></span><span><small>语义</small><b>{item.semantic_score===null?'不可用':item.semantic_score.toFixed(3)}</b></span></span>
            </button>)}
          </div>:null}
        </section>
        {actionError?<Typography.Text type="danger">映射未保存：{actionError.message}</Typography.Text>:null}
        <Form form={mappingForm} layout="vertical" onFinish={confirm}>
          <Form.Item label="手动搜索标准技能"><Input value={query} onChange={event=>setQuery(event.target.value)} placeholder="输入技能名称或类别" allowClear/></Form.Item>
          <Form.Item name="skill_id" label="最终选择" extra={`手动搜索当前显示 ${candidates.length} 个候选`} rules={[{required:true,message:'请选择一个标准技能'}]}>
            <Select onChange={setSelectedSkillId} showSearch optionFilterProp="label" placeholder="请选择标准技能" options={selectCandidates.map(item=>({value:item.skill_id,label:`${item.skill_name} · ${item.category||'已纳入标准目录'}`}))}/>
          </Form.Item>
          <Space><Button type="primary" htmlType="submit" loading={submitting}>确认映射并保存</Button><Button onClick={()=>current&&reject(current)}>没有合适技能，仅保留原文</Button></Space>
        </Form>
      </Space>:null}
    </Modal>
  </>;
}
