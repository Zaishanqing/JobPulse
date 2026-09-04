import {useCallback,useEffect,useMemo,useState} from 'react';
import {Button,Input,Select,Space,Spin,Table,Typography} from 'antd';
import {ArrowRightOutlined} from '@ant-design/icons';
import {useNavigate} from 'react-router-dom';
import {listPublishedPositions} from '../api';
import type {Position} from '../types';
import {ApiError} from '../../../shared/api';
import {domainText} from '../../../shared/idText';
import {loadPositionListFilter,savePositionListFilter} from '../../../shared/listState';
import {EmptyState,Failure,type LoadState} from '../../../shared/components/States';

const sortOptions=[
  {value:'name',label:'按首字母',order:'asc'},
  {value:'domain',label:'按领域',order:'asc'},
  {value:'jd_count',label:'按 JD 数据量',order:'desc'},
] as const;
type PositionSort=typeof sortOptions[number]['value'];

export function Panorama(){
  const nav=useNavigate();
  const [state,setState]=useState<LoadState<Position[]>>({kind:'loading'});
  const defaultFilter={search:'',domain:undefined,sort:'name' as const,order:'asc' as const,page:1};
  const [initialFilter]=useState(()=>loadPositionListFilter('panorama-positions')??defaultFilter);
  const [keyword,setKeyword]=useState(initialFilter.search);
  const [query,setQuery]=useState(initialFilter.search);
  const [domain,setDomain]=useState<string|undefined>(initialFilter.domain);
  const [sort,setSort]=useState<PositionSort>(initialFilter.sort);
  const [order,setOrder]=useState<'asc'|'desc'>(initialFilter.order);
  const [page,setPage]=useState(initialFilter.page);
  const load=useCallback(()=>{
    listPublishedPositions()
      .then(data=>setState({kind:'success',data}))
      .catch((error:ApiError)=>setState({kind:'error',message:error.message,status:error.status}));
  },[]);
  useEffect(()=>{void load()},[load]);
  useEffect(()=>{
    savePositionListFilter('panorama-positions',{search:query,domain,sort,order,page});
  },[domain,order,page,query,sort]);
  const allPositions=useMemo(()=>state.kind==='success'?state.data:[],[state]);
  const domainOptions=useMemo(()=>{
    const labels=new Map<string,string>();
    allPositions.forEach(item=>labels.set(item.category_code,domainText(item.category_code)));
    return [...labels.entries()]
      .map(([value,label])=>({value,label}))
      .sort((left,right)=>left.label.localeCompare(right.label,'zh-CN'));
  },[allPositions]);
  const filtered=useMemo(()=>{
    const needle=query.trim().toLowerCase();
    const rows=allPositions.filter(item=>!needle||item.name.toLowerCase().includes(needle))
      .filter(item=>!domain||item.category_code===domain);
    const direction=order==='asc'?1:-1;
    return rows.sort((left,right)=>{
      if(sort==='name')return left.name.localeCompare(right.name,'zh-CN')*direction;
      if(sort==='domain')return domainText(left.category_code).localeCompare(domainText(right.category_code),'zh-CN')*direction;
      return (left.sample_count-right.sample_count)*direction;
    });
  },[allPositions,domain,order,query,sort]);
  const totalJds=useMemo(()=>allPositions.reduce((sum,item)=>sum+item.sample_count,0),[allPositions]);
  const chooseSort=(value:PositionSort)=>{
    const option=sortOptions.find(item=>item.value===value);
    setSort(value);
    setOrder(option?.order??'asc');
    setPage(1);
  };

  // 验收指标来自最新冻结评测（固定演示数据，非实时计算）：
  // JD 解析 99.67%（Exact Span F1）
  // CV 解析 99.45%（Field F1）
  // 人岗匹配 98.00%（200 个冻结 CV-JD pairs，最终结论级准确率）
  const acceptanceMetrics=[
    {label:'JD 精确片段 F1',value:'99.67%'},
    {label:'CV 字段 F1',value:'99.45%'},
    {label:'人岗匹配结论准确率',value:'98.00%'},
  ];

  return <>
    <div className="page-heading page-heading-row">
      <div>
        <Typography.Title level={2}>岗位能力全景</Typography.Title>
        <Typography.Paragraph type="secondary">浏览已发布的标准岗位能力基线，顶部统计来自正式评估报告。</Typography.Paragraph>
      </div>
    </div>
    {state.kind==='success'&&<div className="panorama-stats">
      <div><span>岗位数</span><strong>{state.data.length}</strong></div>
      <div><span>JD 条数</span><strong>{totalJds}</strong></div>
      {acceptanceMetrics.map(metric=><div key={metric.label}><span>{metric.label}</span><strong>{metric.value}</strong></div>)}
    </div>}
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
        options={domainOptions}
      />
      <Select
        value={sort}
        style={{width:180}}
        onChange={chooseSort}
        options={sortOptions.map(item=>({value:item.value,label:item.label}))}
      />
    </Space>
    {state.kind==='loading'?<div className="center-loading" aria-live="polite"><Spin size="large" description="正在加载岗位"/></div>
      :state.kind==='error'?<Failure {...state} retry={load}/>
      :filtered.length===0?<EmptyState centered text="没有匹配的已发布岗位"/>
      :<Table
        className="primary-table"
        rowKey="position_id"
        dataSource={filtered}
        pagination={{current:page,pageSize:10,total:filtered.length,showSizeChanger:false,onChange:setPage}}
        onRow={position=>({onDoubleClick:()=>nav(`/positions/${position.position_id}`)})}
        columns={[
          {title:'岗位',render:(_:unknown,position:Position)=><div className="table-primary"><strong>{position.name}</strong></div>},
          {title:'构建内容',render:(_:unknown,position:Position)=><Space direction="vertical" size={0}><span>{position.sample_count} 条 JD · {position.skill_count} 项标准技能</span>{position.quality_state==='thin'?<Typography.Text type="warning">样本或技能过少，结论需谨慎</Typography.Text>:<Typography.Text type="secondary">已形成岗位能力基线</Typography.Text>}</Space>},
          {title:'当前版本',render:(_:unknown,position:Position)=><Space><span className="stable-dot"/><span>{position.current_version_number?`当前发布版本 #${position.current_version_number}`:'当前已发布'}</span></Space>},
          {title:'操作',align:'right',render:(_:unknown,position:Position)=><Button type="text" icon={<ArrowRightOutlined/>} onClick={()=>nav(`/positions/${position.position_id}`)}>查看图谱</Button>},
        ]}
      />}
  </>;
}
