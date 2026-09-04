import {useCallback,useEffect,useState} from 'react';
import {App,Button,Card,Form,Input,Select,Space,Spin,Table,Tabs,Tag,Typography} from 'antd';
import {useSearchParams} from 'react-router-dom';
import {diffVersions,getVersion,listVersions,rollbackVersion} from '../api';
import type {GraphVersion} from '../types';
import type {GraphDiff} from '../../../shared/api';
import {ApiError} from '../../../shared/api';
import {EmptyState,Failure,ToastAlert as Alert,WorkbenchState,type LoadState} from '../../../shared/components/States';
import {listCatalogAdminPositions,peekCachedCatalogPositions,type CatalogPositionQuery} from '../../build/api';
import type {CatalogPosition,CatalogPositionPage} from '../../build/types';
import {loadPositionListFilter,savePositionListFilter} from '../../../shared/listState';

const sortOptions=[
  {value:'name',label:'按首字母',order:'asc'},
  {value:'domain',label:'按领域',order:'asc'},
  {value:'jd_count',label:'按 JD 数据量',order:'desc'},
] as const;

export function VersionWorkbench(){
  const {message,modal}=App.useApp();
  const [searchParams,setSearchParams]=useSearchParams();
  const positionId=searchParams.get('positionId')?.trim()??'';
  const defaultFilter={search:'',domain:undefined,sort:'name' as const,order:'asc' as const,page:1};
  const [initialFilter]=useState(()=>loadPositionListFilter('versions-positions')??defaultFilter);
  const [keyword,setKeyword]=useState(initialFilter.search);
  const [query,setQuery]=useState(initialFilter.search);
  const [domain,setDomain]=useState<string|undefined>(initialFilter.domain);
  const [sort,setSort]=useState<'name'|'domain'|'jd_count'>(initialFilter.sort);
  const [order,setOrder]=useState<'asc'|'desc'>(initialFilter.order);
  const [page,setPage]=useState(initialFilter.page);
  const cachedInitial=peekCachedCatalogPositions({
    search:initialFilter.search||undefined,
    domain:initialFilter.domain,
    sort:initialFilter.sort,
    order:initialFilter.order,
    page:initialFilter.page,
    page_size:10,
  });
  const [pageState,setPageState]=useState<LoadState<CatalogPositionPage>>(cachedInitial?{kind:'success',data:cachedInitial}:{kind:'loading'});
  const [state,setState]=useState<LoadState<GraphVersion[]>>({kind:'success',data:[]});
  const [fromId,setFromId]=useState<number>();
  const [toId,setToId]=useState<number>();
  const [diff,setDiff]=useState<GraphDiff>();
  const [error,setError]=useState<ApiError>();
  const [busy,setBusy]=useState(false);

  const loadPositions=useCallback(()=>{
    setPageState({kind:'loading'});
    const request:CatalogPositionQuery={
      search:query.trim()||undefined,
      domain,
      sort,
      order,
      page,
      page_size:10,
    };
    listCatalogAdminPositions(request)
      .then(data=>setPageState({kind:'success',data}))
      .catch((reason:ApiError)=>setPageState({kind:'error',message:reason.message,status:reason.status}));
  },[domain,order,page,query,sort]);
  useEffect(()=>{const id=requestAnimationFrame(()=>void loadPositions());return()=>cancelAnimationFrame(id)},[loadPositions]);
  useEffect(()=>{
    savePositionListFilter('versions-positions',{search:query,domain,sort,order,page});
  },[domain,order,page,query,sort]);

  const load=useCallback(()=>{
    if(!positionId){setState({kind:'success',data:[]});return}
    setState({kind:'loading'});
    listVersions(positionId)
      .then(data=>setState({kind:'success',data}))
      .catch((reason:ApiError)=>setState({kind:'error',message:reason.message,status:reason.status}));
  },[positionId]);
  useEffect(()=>{
    if(!positionId)return;
    const id=requestAnimationFrame(()=>void load());
    return ()=>cancelAnimationFrame(id);
  },[load,positionId]);
  useEffect(()=>{
    const id=requestAnimationFrame(()=>{setDiff(undefined);setFromId(undefined);setToId(undefined);setError(undefined)});
    return ()=>cancelAnimationFrame(id);
  },[positionId]);

  const choosePosition=(value:string)=>{
    setSearchParams({positionId:value});
  };
  const chooseSort=(value:'name'|'domain'|'jd_count')=>{
    const option=sortOptions.find(item=>item.value===value);
    setSort(value);
    setOrder(option?.order??'asc');
    setPage(1);
  };
  const view=async(item:GraphVersion)=>{
    try{
      const detail=await getVersion(positionId,item.id);
      modal.info({
        title:`版本 V${item.version_number} 内容`,
        width:1000,
        content:<Tabs items={[
          {key:'nodes',label:'节点与关系',children:<Table pagination={false} rowKey="skill_id" dataSource={detail.snapshot.skill_relations} columns={[{title:'技能节点',dataIndex:'canonical_name'},{title:'关系',dataIndex:'primary_modality'},{title:'权重',dataIndex:'weight'},{title:'置信度',dataIndex:'confidence'},{title:'重要等级',dataIndex:'importance_level'}]}/>},
          {key:'snapshot',label:'完整快照',children:<pre className="versionSnapshot">{JSON.stringify(detail,null,2)}</pre>},
        ]}/>,
      });
    }catch(reason){setError(reason as ApiError)}
  };
  const compare=async()=>{
    if(!fromId||!toId||busy)return;
    setBusy(true);setError(undefined);
    try{setDiff(await diffVersions(positionId,fromId,toId))}
    catch(reason){setError(reason as ApiError)}
    finally{setBusy(false)}
  };
  const rollback=(item:GraphVersion)=>{
    const formId=`rollback-${item.id}`;
    const dialog=modal.confirm({
      title:`回滚到 V${item.version_number}（将创建新版本）`,
      content:<Form id={formId} onFinish={async(values:{reason:string})=>{
        if(busy)return;
        setBusy(true);
        try{
          await rollbackVersion(positionId,item.id,values.reason);
          message.success('回滚版本已创建');
          dialog.destroy();
          await load();
        }catch(reason){message.error((reason as ApiError).message)}
        finally{setBusy(false)}
      }}>
        <Form.Item name="reason" label="回滚理由" rules={[{required:true,message:'请填写回滚理由'}]}><Input.TextArea/></Form.Item>
      </Form>,
      okButtonProps:{htmlType:'submit',form:formId,loading:busy},
    });
  };
  const relationColumns=[
    {title:'技能节点',dataIndex:'canonical_name'},
    {title:'关系',dataIndex:'primary_modality'},
    {title:'权重',dataIndex:'weight'},
    {title:'置信度',dataIndex:'confidence'},
    {title:'重要等级',dataIndex:'importance_level'},
  ];

  const pageData=pageState.kind==='success'?pageState.data:undefined;
  const positions=pageData?.items??[];
  const domains=pageData?.filters?.domains??[];
  const selectedPosition=positions.find(item=>item.position_id===positionId);

  return <>
    <div className="page-heading">
      <Typography.Title level={2}>图谱版本管理</Typography.Title>
      <Typography.Paragraph type="secondary">对比岗位图谱的历史版本，必要时创建回滚版本。</Typography.Paragraph>
    </div>
    <Space wrap style={{marginBottom:16}}>
      <Input.Search
        allowClear
        value={keyword}
        placeholder="搜索岗位名称"
        style={{width:340}}
        onChange={event=>setKeyword(event.target.value)}
        onSearch={value=>{setQuery(value.trim());setPage(1)}}
      />
      <Select
        allowClear
        placeholder="按领域筛选"
        style={{width:220}}
        value={domain}
        onChange={value=>{setDomain(value);setPage(1)}}
        options={domains.map(item=>({value:item.code||item.name,label:item.name}))}
      />
      <Select
        value={sort}
        style={{width:180}}
        onChange={chooseSort}
        options={sortOptions.map(item=>({value:item.value,label:item.label}))}
      />
    </Space>
    {pageState.kind==='loading'&&<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载岗位版本</span></div>}
    {pageState.kind==='error'&&<Failure {...pageState} retry={loadPositions}/>}
    {pageState.kind==='success'&&<Table
      rowKey="position_id"
      dataSource={positions}
      pagination={{
        current:page,
        pageSize:10,
        total:pageData?.pagination.total??0,
        showSizeChanger:false,
        onChange:setPage,
      }}
      columns={[
        {title:'岗位名称',render:(_:unknown,item:CatalogPosition)=><Typography.Text strong>{item.position_name}</Typography.Text>},
        {title:'领域',render:(_:unknown,item:CatalogPosition)=><Typography.Text>{item.taxonomy_family_name||'未分类'}</Typography.Text>},
        {title:'JD 数据',dataIndex:'jd_count',render:(value:number|undefined)=><Typography.Text>{value??0} 条</Typography.Text>},
        {title:'操作',render:(_:unknown,item:CatalogPosition)=><Button onClick={()=>choosePosition(item.position_id)}>版本管理</Button>},
      ]}
    />}
    {positionId&&<div style={{marginTop:20}}>
      <Typography.Title level={4}>{selectedPosition?.position_name??'当前岗位'}版本列表</Typography.Title>
      {error&&<Alert type="error" showIcon title={error.message} action={<Button onClick={()=>setError(undefined)}>关闭</Button>}/>}
      <WorkbenchState title="版本列表" state={state} retry={load} render={items=>items.length===0
        ?<EmptyState text="该岗位暂无版本，请先在图谱构建中发布版本"/>
        :<><Table rowKey="id" dataSource={items} columns={[
          {title:'版本',render:(_:unknown,item:GraphVersion)=><Space direction="vertical" size={0}><Typography.Text strong>V{item.version_number} · {item.version_name}</Typography.Text><Typography.Text type="secondary">构建记录</Typography.Text></Space>},
          {title:'发布批次',render:(_:unknown,item:GraphVersion)=>item.release_id?<Tag color="success">{item.release_id}</Tag>:<Tag color="orange">旧版无发布批次</Tag>},
          {title:'发布时间',dataIndex:'created_at'},
          {title:'回滚来源',render:(_:unknown,item:GraphVersion)=>item.rollback_from_version_id?<Tag>存在回滚来源</Tag>:'-'},
          {title:'操作',render:(_:unknown,item:GraphVersion)=><Space><Button disabled={busy} onClick={()=>void view(item)}>查看版本内容</Button><Button disabled={busy} danger onClick={()=>rollback(item)}>回滚</Button></Space>},
        ]}/>
        {items.length>=2&&<Card title="选择对比版本" style={{marginTop:16}}>
          <Space>
            <Select placeholder="起始版本" value={fromId} onChange={setFromId} options={items.map(item=>({value:item.id,label:`V${item.version_number}`}))}/>
            <Select placeholder="目标版本" value={toId} onChange={setToId} options={items.map(item=>({value:item.id,label:`V${item.version_number}`}))}/>
            <Button type="primary" loading={busy} disabled={!fromId||!toId||fromId===toId} onClick={()=>void compare()}>比较</Button>
          </Space>
        </Card>}
        {diff&&<Card title="版本差异" style={{marginTop:16}}>
          <Tabs items={[
            {key:'added',label:`新增节点/关系 ${diff.added.length}`,children:<Table rowKey="skill_id" pagination={false} dataSource={diff.added} columns={relationColumns}/>},
            {key:'removed',label:`删除节点/关系 ${diff.removed.length}`,children:<Table rowKey="skill_id" pagination={false} dataSource={diff.removed} columns={relationColumns}/>},
            {key:'changed',label:`关系与权重变化 ${diff.changed.length}`,children:<Table rowKey="skill_id" pagination={false} dataSource={diff.changed} columns={[{title:'技能',dataIndex:'skill_id'},{title:'变更字段',render:(_:unknown,item)=>Object.entries(item.changed_fields).map(([field,value])=><div key={field}>{field}: {JSON.stringify(value.before)} → {JSON.stringify(value.after)}</div>)}]}/>},
            {key:'evidence',label:`证据变化 ${diff.evidence_changes.length}`,children:<Table rowKey="skill_id" pagination={false} dataSource={diff.evidence_changes} columns={[{title:'技能',dataIndex:'skill_id'},{title:'变化',render:(_:unknown,item)=><pre>{JSON.stringify({before:item.before,after:item.after},null,2)}</pre>}]}/>},
            {key:'context',label:'画像上下文变化',children:<pre>{JSON.stringify(diff.context_changes,null,2)}</pre>},
          ]}/>
        </Card>}
        </>}
      />
    </div>}
  </>;
}
