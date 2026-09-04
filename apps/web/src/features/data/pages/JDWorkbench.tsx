import {useCallback,useEffect,useState} from 'react';
import {
  App,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Progress,
  Select,
  Segmented,
  Space,
  Spin,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
} from 'antd';
import {FileAddOutlined,FileTextOutlined,InboxOutlined,ReloadOutlined} from '@ant-design/icons';
import type {UploadFile} from 'antd';
import {useNavigate,useSearchParams} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {Failure} from '../../../shared/components/States';
import {StatusTag,type StatusTone} from '../../../shared/components/StatusTag';
import {useAuth} from '../../auth/AuthContext';
import {EvidenceDeepLinkFocus} from '../../rag/EvidenceDeepLink';
import {
  createFileJD,
  createTextJD,
  getJD,
  getJDParseResult,
  getJDPage,
  getJDSummary,
  getExtractionModesReadiness,
  parseJD,
  parseJDBatch,
  runOCR,
} from '../api';
import type {ExtractionMode,ExtractionModesReadiness,JDSummary} from '../api';
import type {JDParseResult,JDRecord} from '../types';
import {jdSourceLabel} from './jdSourceLabel';

type ImportMode='text'|'file'|'ocr';
type ImportValues={title:string;raw_text:string;source_name?:string};
type JDEvidence={field:string;quote:string;sourceId?:string};

const collectEvidence=(value:unknown,path='解析字段',seen=new Set<string>()):JDEvidence[]=>{
  if(Array.isArray(value))return value.flatMap((item,index)=>collectEvidence(item,`${path} ${index+1}`,seen));
  if(!value||typeof value!=='object')return [];
  const record=value as Record<string,unknown>;
  const nested=record.evidence&&typeof record.evidence==='object'?record.evidence as Record<string,unknown>:undefined;
  const quote=[nested?.quote,record.quote,record.original_text_snippet].find(item=>typeof item==='string'&&item.trim()) as string|undefined;
  const source=[nested?.source_id,record.source_id,record.source_jd_id,record.jd_id].find(item=>typeof item==='string'&&item.trim()) as string|undefined;
  const rows:JDEvidence[]=[];
  if(quote&&!seen.has(quote)){seen.add(quote);rows.push({field:path,quote,sourceId:source})}
  Object.entries(record).forEach(([key,item])=>{
    if(!['evidence','quote','original_text_snippet'].includes(key))rows.push(...collectEvidence(item,key,seen));
  });
  return rows;
};

const statusCopy:Record<string,string>={
  pending:'待解析',
  parsing:'解析中',
  parsed:'待确认',
  completed:'已解析',
  draft:'待确认',
  reviewed:'已确认',
  confirmed:'已确认',
  published:'已发布',
  failed:'解析失败',
};

function statusTag(status:string){
  const tone:StatusTone=status==='published'||status==='confirmed'?'stable':status==='failed'?'risk':status==='parsed'?'review':'neutral';
  return <StatusTag tone={tone}>{statusCopy[status]||'状态未知'}</StatusTag>;
}

export function JDWorkbench(){
  const {message}=App.useApp();
  const {can}=useAuth();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const routeJdId=searchParams.get('jdId')||undefined;
  const [items,setItems]=useState<JDRecord[]>([]);
  const [summary,setSummary]=useState<JDSummary>();
  const [page,setPage]=useState(1);
  const [total,setTotal]=useState(0);
  const [selectedId,setSelectedId]=useState<string|undefined>(routeJdId);
  const [selected,setSelected]=useState<JDRecord>();
  const [parsed,setParsed]=useState<JDParseResult>();
  const [loading,setLoading]=useState(true);
  const [detailLoading,setDetailLoading]=useState(false);
  const [actionLoading,setActionLoading]=useState('');
  const [selectedJdIds,setSelectedJdIds]=useState<string[]>([]);
  const [batchParsing,setBatchParsing]=useState(false);
  const [error,setError]=useState<ApiError>();
  const [query,setQuery]=useState('');
  const [sort,setSort]=useState<'created_desc'|'created_asc'|'title_asc'>('created_desc');
  const [importOpen,setImportOpen]=useState(false);
  const [importMode,setImportMode]=useState<ImportMode>('text');
  const [extractionMode,setExtractionMode]=useState<ExtractionMode>('llm');
  const [modeReadiness,setModeReadiness]=useState<ExtractionModesReadiness['jd']>({
    rule:{ready:true,provider:'rule_based_jd_extraction',requires_review:true},
    llm:{ready:false,provider:'http_jd_extraction',error:'正在检查模型服务'},
  });
  const [fileList,setFileList]=useState<UploadFile[]>([]);
  const [importForm]=Form.useForm<ImportValues>();
  const canCreate=can('jd.create');
  const canParse=can('jd.parse');
  const canReview=can('kg.review.manage');
  const published=parsed?.workflow_status==='published';
  const selectedModeReady=Boolean(extractionMode&&modeReadiness[extractionMode].ready);
  const evidenceRows=parsed?collectEvidence([parsed.extraction_result,parsed.normalized_result]):[];

  const loadPage=useCallback(async(nextPage=1,search=query,preferredId?:string)=>{
    setLoading(true);setError(undefined);
    try{
      const data=await getJDPage((nextPage-1)*20,20,search,sort);
      setItems(data.items);setTotal(data.total);setPage(nextPage);
      setSelectedId(current=>preferredId||current||data.items[0]?.jd_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[query,sort]);

  const loadSummary=useCallback(async()=>{
    setLoading(true);setError(undefined);
    try{setSummary(await getJDSummary())}catch(reason){setError(reason as ApiError)}finally{setLoading(false)}
  },[]);

  const loadDetail=useCallback(async(jdId:string)=>{
    try{
      const detail=await getJD(jdId);
      setSelected(detail);
      try{
        const result=await getJDParseResult(jdId);
        setParsed(result);
      }catch(reason){
        const parseError=reason as ApiError;
        if(parseError.status!==404)throw reason;
      }
    }catch(reason){setError(reason as ApiError)}
    finally{setDetailLoading(false)}
  },[]);

  useEffect(()=>{const timer=window.setTimeout(()=>void loadSummary(),0);return()=>window.clearTimeout(timer)},[loadSummary]);
  useEffect(()=>{
    let active=true;
    void getExtractionModesReadiness().then(value=>{
      if(!active||!value?.jd?.rule||!value.jd.llm)return;
      setModeReadiness(value.jd);
    }).catch(()=>{
      if(active){
        setModeReadiness(current=>({...current,llm:{...current.llm,ready:false,error:'模型服务当前不可用'}}));
      }
    });
    return()=>{active=false};
  },[]);
  useEffect(()=>{
    const timer=window.setTimeout(()=>void loadPage(1,'',routeJdId),0);
    return()=>window.clearTimeout(timer);
  },[loadPage,routeJdId]);
  useEffect(()=>{
    if(!selectedId)return;
    const timer=window.setTimeout(()=>void loadDetail(selectedId),0);
    return ()=>window.clearTimeout(timer);
  },[loadDetail,selectedId]);

  const refreshSelected=async()=>{
    await loadSummary();
    await loadPage(page,query,selectedId);
    if(selectedId)await loadDetail(selectedId);
  };

  const runAction=async(key:string,action:()=>Promise<unknown>,success:string)=>{
    setActionLoading(key);setError(undefined);
    try{await action();message.success(success);await refreshSelected()}
    catch(reason){setError(reason as ApiError)}
    finally{setActionLoading('')}
  };

  const runBatchParse=async()=>{
    if(!selectedJdIds.length||!extractionMode||!selectedModeReady||batchParsing)return;
    setBatchParsing(true);setError(undefined);
    try{
      const result=await parseJDBatch(selectedJdIds,extractionMode);
      message.success(`已批量解析 ${result.parsed_count} 条 JD`);
      setSelectedJdIds([]);
      await refreshSelected();
    }catch(reason){setError(reason as ApiError)}
    finally{setBatchParsing(false)}
  };

  const submitImport=async()=>{
    const values=await importForm.validateFields();
    setActionLoading('import');setError(undefined);
    try{
      const upload=fileList[0]?.originFileObj;
      if(importMode!=='text'&&!upload){message.error('请选择需要处理的文件');return}
      const created=importMode==='text'
        ?await createTextJD(values)
        :importMode==='file'
          ?await createFileJD(upload as File,values.title)
          :await runOCR(upload as File).then(result=>{
            if(!result.text.trim())throw new Error(result.error_message||'OCR 未提取到文本');
            return createTextJD({title:values.title,raw_text:result.text,source_name:`OCR · ${result.filename||upload?.name}`});
          });
      message.success('JD 已进入数据中心');
      setImportOpen(false);importForm.resetFields();setFileList([]);
      await loadSummary();
      await loadPage(1,'',created.jd_id);
    }catch(reason){setError(reason as ApiError)}
    finally{setActionLoading('')}
  };

  if(loading&&!items.length&&!selectedId)return <div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载 JD</span></div>;

  return <>
    <EvidenceDeepLinkFocus resourceId={selectedId}/>
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>JD 数据中心</Typography.Title>
        <Typography.Paragraph type="secondary">导入岗位描述并查看解析数据；需要人工判断的内容统一进入审核中心。</Typography.Paragraph>
      </div>
      <Space>
        <Input.Search className="page-search" allowClear value={query} onChange={event=>setQuery(event.target.value)} onSearch={value=>void loadPage(1,value)} placeholder="搜索岗位名称或来源"/>
        {canCreate&&<Button type="primary" icon={<FileAddOutlined/>} onClick={()=>setImportOpen(true)}>导入 JD</Button>}
      </Space>
    </div>

    {error&&<Failure message={error.message} status={error.status} retry={()=>void refreshSelected()}/>}
    <section className="jd-overview" aria-label="JD 数据概览">
      <Space size={40} wrap>
        <Statistic title="JD 总数" value={summary?.total||0}/>
        <Statistic title="待审核" value={summary?.awaiting_review||0}/>
        <Statistic title="已确认" value={summary?.reviewed||0}/>
        <Statistic title="已发布" value={summary?.published||0}/>
        <Statistic title="处理失败" value={summary?.failed||0}/>
      </Space>
    </section>
    <div className="data-workbench">
      <section className="data-list" aria-label="JD 列表">
        <div className="section-toolbar">
          <Space>
            <Typography.Text strong>岗位描述</Typography.Text>
            <Select
              size="small"
              style={{width:130}}
              value={sort}
              onChange={value=>{setSort(value);void loadPage(1,query,selectedId)}}
              options={[
                {value:'created_desc',label:'最新优先'},
                {value:'created_asc',label:'最早优先'},
                {value:'title_asc',label:'标题 A-Z'},
              ]}
            />
            <Button type="text" icon={<ReloadOutlined/>} loading={loading} onClick={()=>void refreshSelected()}>刷新</Button>
          </Space>
        </div>
        {canParse&&<div className="batch-parse-toolbar">
          <ExtractionModePicker compact value={extractionMode} onChange={setExtractionMode} readiness={modeReadiness}/>
          <Button type="primary" loading={batchParsing} disabled={!selectedJdIds.length||!selectedModeReady} onClick={()=>void runBatchParse()}>批量解析（{selectedJdIds.length}）</Button>
        </div>}
        <Table
          className="primary-table"
          rowKey="jd_id"
          loading={loading}
          dataSource={items}
          rowSelection={{
            selectedRowKeys:selectedJdIds,
            onChange:keys=>setSelectedJdIds(keys as string[]),
          }}
          pagination={{current:page,total,pageSize:20,showSizeChanger:false,onChange:next=>void loadPage(next)}}
          locale={{emptyText:<Empty description={canCreate?'暂无 JD，可从文本或文件导入':'暂无可查看的 JD'}/>}}
          onRow={record=>({onClick:()=>{setDetailLoading(true);setError(undefined);setParsed(undefined);setSelectedId(record.jd_id)},className:record.jd_id===selectedId?'is-selected':''})}
          columns={[
            {title:'岗位',render:(_:unknown,item:JDRecord)=><div className="table-primary"><strong>{item.title}</strong><span>{jdSourceLabel(item)}</span></div>},
            {title:'处理状态',dataIndex:'parse_status',width:112,render:(value:string)=>statusTag(value)},
            {title:'更新时间',dataIndex:'updated_at',width:150,render:(value:string|null)=>value?new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-'},
          ]}
        />
      </section>

      <aside className="context-panel" aria-label="JD 处理详情">
        {!selected?<Empty description="选择一条 JD 查看处理详情"/>:<>
          <div className="context-panel-head">
            <div><Typography.Title level={4}>{selected.title}</Typography.Title><Typography.Text type="secondary">{jdSourceLabel(selected)}</Typography.Text></div>
            {statusTag(parsed?.workflow_status||selected.parse_status)}
          </div>
          <Descriptions size="small" column={2} items={[
            {key:'source',label:'数据来源',children:jdSourceLabel(selected)},
            {key:'input',label:'输入处理',children:selected.input_extraction_status||'文本直入'},
          ]}/>
          {selected.input_error_message&&<Typography.Paragraph type="danger">输入处理失败：{selected.input_error_message}</Typography.Paragraph>}

          <div className="workflow-strip" aria-label="JD 处理流程">
            {['导入','解析','审核','发布'].map((label,index)=>{
              const stages:Record<string,number>={pending:0,failed:0,parsing:0,parsed:1,completed:1,draft:1,reviewed:2,confirmed:2,published:3};
              const active=(stages[parsed?.workflow_status||selected.parse_status]??0)>=index;
              return <span className={active?'is-active':''} key={label}><i/>{label}</span>;
            })}
          </div>

          {!parsed&&!detailLoading&&<div className="context-empty">
            <FileTextOutlined/>
            <Typography.Text>{canParse?'尚未生成解析结果':'等待具备解析权限的角色完成解析'}</Typography.Text>
            {canParse&&<>
              <ExtractionModePicker value={extractionMode} onChange={setExtractionMode} readiness={modeReadiness}/>
              <Button type="primary" disabled={!selectedModeReady} loading={actionLoading==='parse'} onClick={()=>extractionMode&&selectedModeReady&&void runAction('parse',()=>parseJD(selected.jd_id,extractionMode),'解析完成')}>开始解析</Button>
            </>}
          </div>}

          {parsed&&<>
            <div className="parse-summary">
              <div><span>解析置信度</span><Progress percent={Math.round(parsed.parse_confidence*100)} size="small"/></div>
              <StatusTag tone={parsed.need_review?'review':'stable'}>{parsed.need_review?'需要人工确认':'规则校验通过'}</StatusTag>
            </div>
            {published&&<Typography.Text type="secondary">该 JD 已发布，当前展示的是锁定版本。</Typography.Text>}
            <Descriptions bordered size="small" column={1} items={[
              {key:'mode',label:'解析模式',children:parsed.execution?.mode==='rule'?'规则解析':parsed.execution?.mode==='llm'?'LLM 解析':'未返回'},
              {key:'provider',label:'执行服务',children:parsed.execution?.provider||'未返回'},
              {key:'version',label:'模型 / 算法版本',children:parsed.execution?.mode==='llm'?(parsed.execution.model||'未返回'):(parsed.execution?.algorithm_version||'未返回')},
              {key:'title',label:'抽取识别岗位',children:parsed.position_title||'未识别'},
              {key:'responsibilities',label:`职责 ${parsed.responsibilities.length} 项`,children:parsed.responsibilities.length?<Space direction="vertical" size={2}>{parsed.responsibilities.slice(0,6).map(item=><Typography.Text key={item}>· {item}</Typography.Text>)}</Space>:'未识别'},
              {key:'required',label:`必备技能 ${parsed.required_skills.length} 项`,children:parsed.required_skills.length?<Space wrap>{parsed.required_skills.map(item=><Tag key={item.raw_skill}>{item.raw_skill}</Tag>)}</Space>:'未识别'},
              {key:'bonus',label:`加分技能 ${parsed.bonus_skills.length} 项`,children:parsed.bonus_skills.length?<Space wrap>{parsed.bonus_skills.map(item=><Tag key={item.raw_skill}>{item.raw_skill}</Tag>)}</Space>:'未识别'},
              {key:'other',label:'其他条件',children:[parsed.education&&`学历：${parsed.education}`,parsed.experience&&`经验：${parsed.experience}`,parsed.industry&&`行业：${parsed.industry}`].filter(Boolean).join('；')||'未识别'},
            ]}/>
            <Card size="small" className="profile" title={`原文证据 · ${evidenceRows.length}`}>
              {evidenceRows.length?<List
                dataSource={evidenceRows.slice(0,12)}
                renderItem={item=><List.Item><div className="evidence-record"><strong>{item.field}</strong><p>“{item.quote}”</p>{item.sourceId&&<small>{item.sourceId}</small>}</div></List.Item>}
              />:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前解析结果未返回可核对的引用片段；页面不会从 JD 原文猜造证据"/>}
            </Card>
            <div className="context-actions">
              {canParse&&!published&&<Space direction="vertical" align="start"><ExtractionModePicker compact value={extractionMode} onChange={setExtractionMode} readiness={modeReadiness}/><Button disabled={!selectedModeReady} loading={actionLoading==='parse'} onClick={()=>extractionMode&&selectedModeReady&&void runAction('parse',()=>parseJD(selected.jd_id,extractionMode),'已重新解析')}>按所选模式重新解析</Button></Space>}
              {!published&&canReview&&<Button type="primary" onClick={()=>navigate('/admin/review')}>前往审核中心处理</Button>}
            </div>
          </>}
        </>}
      </aside>
    </div>

    <Drawer title="导入岗位描述" width={520} open={importOpen} onClose={()=>setImportOpen(false)} extra={<Button type="primary" loading={actionLoading==='import'} onClick={()=>void submitImport()}>提交导入</Button>}>
      <Segmented block value={importMode} onChange={value=>{setImportMode(value as ImportMode);setFileList([])}} options={[{label:'粘贴文本',value:'text'},{label:'上传文件',value:'file'},{label:'OCR 识别',value:'ocr'}]}/>
      <Form className="import-form" form={importForm} layout="vertical">
        <Form.Item label="岗位标题" name="title" rules={[{required:true,message:'请输入岗位标题'}]}><Input placeholder="例如：数据治理工程师"/></Form.Item>
        {importMode==='text'
          ?<><Form.Item label="来源名称" name="source_name"><Input placeholder="企业官网、招聘平台或内部需求"/></Form.Item><Form.Item label="JD 原文" name="raw_text" rules={[{required:true,message:'请粘贴 JD 原文'}]}><Input.TextArea rows={14} placeholder="粘贴完整岗位职责与任职要求"/></Form.Item></>
          :<Form.Item label={importMode==='ocr'?'扫描件或 PDF':'JD 文件'} required><Upload.Dragger accept={importMode==='ocr'?'.pdf,image/*':'.pdf,.doc,.docx,.txt'} beforeUpload={()=>false} maxCount={1} fileList={fileList} onChange={({fileList:next})=>setFileList(next)}><p className="ant-upload-drag-icon"><InboxOutlined/></p><p>{importMode==='ocr'?'选择图片或扫描版 PDF':'选择 PDF、Word 或文本文件'}</p><p className="ant-upload-hint">{importMode==='ocr'?'OCR 结果将作为可审核的 JD 原文进入解析流程。':'文件将由后端适配器抽取，处理状态会保留在 JD 记录中。'}</p></Upload.Dragger></Form.Item>}
      </Form>
    </Drawer>
  </>;
}

function ExtractionModePicker({value,onChange,readiness,compact=false}:{value?:ExtractionMode;onChange:(value:ExtractionMode)=>void;readiness:ExtractionModesReadiness['jd'];compact?:boolean}){
  return <div className={compact?'extraction-mode-picker is-compact':'extraction-mode-picker'}>
    <Typography.Text strong>选择解析模式</Typography.Text>
    <Segmented<ExtractionMode>
      value={value}
      onChange={onChange}
      options={[
        {value:'llm',label:'LLM'},
        {value:'rule',label:'规则',disabled:!readiness.rule.ready},
      ]}
    />
  </div>;
}
