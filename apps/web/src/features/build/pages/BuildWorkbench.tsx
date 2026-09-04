import {useCallback,useEffect,useState} from 'react';
import {App,Button,Input,Select,Space,Spin,Table,Typography} from 'antd';
import {useNavigate,useSearchParams} from 'react-router-dom';

import {useAuth} from '../../auth/AuthContext';
import {buildPosition,invalidateBuildRuns,listCatalogAdminPositions,peekCachedCatalogPositions,type CatalogPositionQuery} from '../api';
import type {CatalogPosition,CatalogPositionPage} from '../types';
import {ApiError} from '../../../shared/api';
import {Failure,type LoadState} from '../../../shared/components/States';
import {loadPositionListFilter,savePositionListFilter} from '../../../shared/listState';

const sortOptions=[
  {value:'name',label:'按首字母',order:'asc'},
  {value:'domain',label:'按领域',order:'asc'},
  {value:'jd_count',label:'按 JD 数据量',order:'desc'},
] as const;

export function BuildWorkbench(){
  const {message}=App.useApp();
  const navigate=useNavigate();
  const [searchParams]=useSearchParams();
  const positionId=searchParams.get('positionId')?.trim()??'';
  const defaultFilter={search:'',domain:undefined,sort:'name' as const,order:'asc' as const,page:1};
  const [initialFilter]=useState(()=>loadPositionListFilter('build-positions')??defaultFilter);
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
  const [building,setBuilding]=useState(false);
  const {can}=useAuth();

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
    savePositionListFilter('build-positions',{search:query,domain,sort,order,page});
  },[domain,order,page,query,sort]);

  const chooseSort=(value:'name'|'domain'|'jd_count')=>{
    const option=sortOptions.find(item=>item.value===value);
    setSort(value);
    setOrder(option?.order??'asc');
    setPage(1);
  };
  const startBuild=async(target:string)=>{
    if(!can('kg.build.manage'))return;
    setBuilding(true);
    try{
      await buildPosition(target);
      invalidateBuildRuns(target);
      message.success('构建任务已加入队列，请稍后在构建记录中查看进度');
    }catch(reason){
      const apiError=reason as ApiError;
      message.error(apiError.status===409?apiError.message:`构建任务创建失败：${apiError.message}`);
    }finally{
      setBuilding(false);
    }
  };

  const pageData=pageState.kind==='success'?pageState.data:undefined;
  const positions=pageData?.items??[];
  const domains=pageData?.filters?.domains??[];

  return <>
    <div className="page-heading">
      <Typography.Title level={2}>图谱构建</Typography.Title>
      <Typography.Paragraph type="secondary">选择已发布岗位发起能力图谱构建，或查看历史构建记录。</Typography.Paragraph>
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
    {pageState.kind==='loading'&&<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载岗位</span></div>}
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
        {title:'操作',render:(_:unknown,item:CatalogPosition)=><Space>
          <Button type="primary" loading={building} onClick={()=>void startBuild(item.position_id)}>启动构建</Button>
          <Button onClick={()=>navigate(`/admin/build/records?positionId=${encodeURIComponent(item.position_id)}`)}>构建记录</Button>
        </Space>},
      ]}
    />}
    {positionId&&<Typography.Paragraph type="secondary" style={{marginTop:16}}>当前 URL 已指定岗位，可直接通过「构建记录」查看该岗位的历史构建版本。</Typography.Paragraph>}
  </>;
}
