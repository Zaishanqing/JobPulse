import {useMemo,useState} from 'react';
import {Button,Descriptions,Drawer,Empty,Input,Select,Space,Spin,Statistic,Table,Tabs,Tag,Typography} from 'antd';
import {useNavigate} from 'react-router-dom';
import {Failure} from '../../../shared/components/States';
import {
  getFormalDiscoveryExperiment,
  listFormalExperimentClusters,
  emergingAssetId,
  type FormalDiscoveryExperiment,
  type FormalExperimentCluster,
} from '../api';
import {emergingCacheKeys} from '../cache';
import {useEmergingCachedQuery} from '../useEmergingCachedQuery';
import {isTechnicalIdentifier} from '../lib/discoveryDisplay';

const stateLabel=(value:unknown)=>({emerging:'新兴',weak_emerging_signal:'弱信号',not_emerging:'非新兴',insufficient_evidence:'证据不足'}[String(value)]||'未知状态');
const stateColor=(value:unknown)=>String(value)==='emerging'?'success':String(value)==='weak_emerging_signal'?'gold':String(value)==='not_emerging'?'default':'default';
const relationLabel=(value:unknown)=>({same_or_not_novel:'同类岗位 / 无结构新颖性',renaming:'岗位重命名',tool_migration:'工具迁移',specialization:'岗位专业化',hybridization:'岗位混合化',unexplained_structural_novelty:'结构新颖性',insufficient_evidence:'结构证据不足'}[String(value)]||'未分类');
const ablationLabel=(key:string)=>({baseline:'基线判定',no_enterprise_diffusion:'去企业扩散',no_structural_evolution:'去结构演化',no_temporal:'去时序证据'}[key]||'其他消融项');
const layerCountLabel=(value:unknown)=>typeof value==='number'?value:'—';
const UUID_PATTERN=/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i;
const readableEvidenceRef=(ref:string)=>Boolean(
  ref.trim()
  &&!UUID_PATTERN.test(ref)
  &&!/^\d+$/.test(ref)
  &&!/_\d+$/.test(ref)
  &&!/^[A-Za-z]+\d+$/.test(ref)
  &&!/^(?:sha256:|https?:\/\/|frozen-d5:|crawler-jd-v1:|run-|cluster-|candidate-|obs-)/i.test(ref)
  &&!(ref.length>=20&&/^[A-Za-z0-9]+$/.test(ref)),
);

const STATE_FILTERS=[
  {value:'all',label:'全部状态'},
  {value:'emerging',label:'新兴'},
  {value:'weak_emerging_signal',label:'弱信号'},
  {value:'not_emerging',label:'非新兴'},
  {value:'insufficient_evidence',label:'证据不足'},
] as const;

export function DiscoveryWorkbench(){
  const navigate=useNavigate();
  const [activeTab,setActiveTab]=useState('overview');
  const [query,setQuery]=useState('');
  const [stateFilter,setStateFilter]=useState<string>('all');
  const [selected,setSelected]=useState<FormalExperimentCluster|null>(null);
  const [drawerOpen,setDrawerOpen]=useState(false);

  const formalQuery=useEmergingCachedQuery<FormalDiscoveryExperiment>(
    emergingCacheKeys.formalExperiment,
    getFormalDiscoveryExperiment,
  );
  const clustersQuery=useEmergingCachedQuery<FormalExperimentCluster[]>(
    emergingCacheKeys.formalClusters,
    listFormalExperimentClusters,
  );
  const formal=formalQuery.state;
  const clustersState=clustersQuery.state;

  const filteredClusters=useMemo(()=>{
    if(clustersState.kind!=='success')return [];
    const needle=query.trim().toLocaleLowerCase();
    return clustersState.data.filter(item=>{
      if(isTechnicalIdentifier(item.canonical_title))return false;
      if(stateFilter!=='all'&&item.state!==stateFilter)return false;
      if(!needle)return true;
      return item.canonical_title.toLocaleLowerCase().includes(needle);
    });
  },[clustersState,query,stateFilter]);

  return <div className="discovery-workbench">
    <div className="page-heading">
      <Typography.Title level={2}>新兴岗位发现</Typography.Title>
      <Typography.Paragraph type="secondary">识别尚未标准化、但已形成独立职责与技能结构的市场新岗位。</Typography.Paragraph>
    </div>

    <Tabs
      className="discovery-tabs"
      activeKey={activeTab}
      onChange={setActiveTab}
      items={[
        {
          key:'overview',
          label:'正式发现结果',
          children:<section className="discovery-run-surface" aria-label="正式发现结果">
            {formal.kind==='loading'
              ?<div className="center-loading"><Spin size="large"/></div>
              :formal.kind==='error'
                ?<Failure {...formal} retry={formalQuery.reload}/>
                :<>
              {(()=>{const report=formal.data;const distribution=report.stage2_distribution_over_eligible;const emerging=report.emerging_clusters;return <>
              <div className="discovery-experiment-stats">
                <Statistic title="正式岗位簇" value={report.cluster_counts.total_clusters}/>
                <Statistic title="第二阶段可判定" value={report.cluster_counts.clusters_eligible_for_stage2}/>
                <Statistic title="新兴岗位" value={distribution.emerging}/>
                <Statistic title="第一阶段回归" value={`${report.stage1_regression.matched}/${report.stage1_regression.total}`}/>
              </div>
              <Typography.Title level={5}>正式发现判定分布</Typography.Title>
              <Space wrap>{Object.entries(distribution).map(([state,count])=><Tag key={state} color={state==='emerging'?'success':'default'}>{stateLabel(state)}：{count}</Tag>)}</Space>
              <Typography.Title level={5} className="membership-title">正式发现的 10 个新兴岗位簇</Typography.Title>
              <Table size="small" rowKey="cluster_key" pagination={false} dataSource={emerging} columns={[
                {title:'岗位',dataIndex:'canonical_title'},
                {title:'第一阶段关系',render:(_:unknown,item)=>relationLabel(item.stage1_relation)},
                {title:'独立发布',dataIndex:'postings'},
                {title:'企业',dataIndex:'enterprises'},
                {title:'来源',dataIndex:'sources'},
              ]}/>
              </>})()}
            </>}
          </section>,
        },
        {
          key:'clusters',
          label:'岗位簇明细',
          children:<section className="discovery-ranking" aria-label="岗位簇明细">
            <Space className="formal-cluster-filters" wrap>
              <Input
                allowClear
                placeholder="搜索岗位名称"
                value={query}
                onChange={event=>setQuery(event.target.value)}
                style={{width:280}}
              />
              <Select
                value={stateFilter}
                onChange={setStateFilter}
                style={{width:160}}
                options={STATE_FILTERS.map(item=>({value:item.value,label:item.label}))}
              />
              {clustersState.kind==='success'&&<Typography.Text type="secondary">当前显示 {filteredClusters.length} 个已命名岗位簇</Typography.Text>}
            </Space>
            {clustersState.kind==='loading'
              ?<div className="center-loading"><Spin size="large"/></div>
              :clustersState.kind==='error'
                ?<Failure {...clustersState} retry={clustersQuery.reload}/>
                :filteredClusters.length
                  ?<Table
                    rowKey="cluster_key"
                    dataSource={filteredClusters}
                    pagination={{pageSize:20,showSizeChanger:true,showTotal:total=>`共 ${total} 个岗位簇`}}
                    onRow={item=>({onClick:()=>{setSelected(item);setDrawerOpen(true);}})}
                    columns={[
                      {title:'岗位',render:(_:unknown,item:FormalExperimentCluster)=><div className="table-primary"><strong>{item.canonical_title||'待命名岗位簇'}</strong>{item.representative&&<Tag color="blue">代表簇</Tag>}</div>},
                      {title:'结构关系',render:(_:unknown,item:FormalExperimentCluster)=>relationLabel(item.stage1_relation)},
                      {title:'独立发布',width:90,render:(_:unknown,item:FormalExperimentCluster)=>layerCountLabel(item.counts.independent_postings)},
                      {title:'企业',width:80,render:(_:unknown,item:FormalExperimentCluster)=>layerCountLabel(item.counts.enterprises)},
                      {title:'来源',width:80,render:(_:unknown,item:FormalExperimentCluster)=>layerCountLabel(item.counts.sources)},
                      {title:'日期',width:80,render:(_:unknown,item:FormalExperimentCluster)=>layerCountLabel(item.counts.distinct_dates)},
                      {title:'第二阶段',width:100,align:'center',render:(_:unknown,item:FormalExperimentCluster)=><Tag color={item.eligible?'blue':'default'}>{item.eligible?'已进入':'未进入'}</Tag>},
                      {title:'判定状态',width:110,render:(_:unknown,item:FormalExperimentCluster)=><Tag color={stateColor(item.state)}>{stateLabel(item.state)}</Tag>},
                      {title:'操作',width:110,render:(_:unknown,item:FormalExperimentCluster)=>item.state==='emerging'&&<Button onClick={event=>{event.stopPropagation();navigate(`/emerging/${encodeURIComponent(emergingAssetId(item.cluster_key))}/graph`);}}>查看图谱</Button>},
                    ]}
                  />
                  :<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的岗位簇"/>}
          </section>,
        },
      ]}
    />

    <Drawer
      open={drawerOpen}
      onClose={()=>setDrawerOpen(false)}
      size="large"
      extra={selected?.state==='emerging'&&<Button type="primary" onClick={()=>navigate(`/emerging/${encodeURIComponent(emergingAssetId(selected.cluster_key))}/graph`)}>查看图谱</Button>}
      title={selected&&!isTechnicalIdentifier(selected.canonical_title)?selected.canonical_title:'待命名岗位簇'}
    >
      {selected&&<>
        <Space wrap className="formal-cluster-detail-head">
          <Tag color={stateColor(selected.state)}>{stateLabel(selected.state)}</Tag>
          {selected.representative&&<Tag color="blue">代表簇</Tag>}
          {selected.eligible?<Tag color="blue">已进入第二阶段</Tag>:<Tag>未进入第二阶段</Tag>}
        </Space>
        <Descriptions size="small" column={1} items={[
          {key:'relation',label:'结构关系',children:relationLabel(selected.stage1_relation)},
          {key:'observations',label:'观察记录',children:layerCountLabel(selected.counts.observations)},
          {key:'postings',label:'独立招聘发布',children:layerCountLabel(selected.counts.independent_postings)},
          {key:'dates',label:'不同日期',children:layerCountLabel(selected.counts.distinct_dates)},
          {key:'enterprises',label:'独立企业',children:layerCountLabel(selected.counts.enterprises)},
          {key:'sources',label:'独立来源',children:layerCountLabel(selected.counts.sources)},
          {key:'versions',label:'内容版本',children:layerCountLabel(selected.counts.content_hash_count)},
          {key:'structural',label:'结构演化',children:selected.structural_changed?'内容版本发生变化':'内容版本未发生变化'},
          {key:'growth',label:'市场增长',children:selected.growth.available?`可评估 · 变化 ${selected.growth.growth_delta.toFixed(3)}`:'当前不可评估'},
        ]}/>
        {selected.definition&&<>
          <Typography.Title level={5}>岗位定义</Typography.Title>
          <Typography.Paragraph>{selected.definition.position_summary}</Typography.Paragraph>
          {selected.definition.core_responsibilities.length>0&&<>
            <Typography.Title level={5}>核心职责</Typography.Title>
            <ul className="formal-definition-list">{selected.definition.core_responsibilities.map((item,index)=><li key={index}>{item}</li>)}</ul>
          </>}
          {selected.definition.required_skills.length>0&&<>
            <Typography.Title level={5}>核心技能</Typography.Title>
            <Space wrap>{selected.definition.required_skills.map((skill,index)=>{
              const rawName=String(skill.raw_skill||'');
              const name=rawName&&!isTechnicalIdentifier(rawName)?rawName:'待命名技能';
              return <Tag key={`${name}-${index}`}>{name}</Tag>;
            })}</Space>
          </>}
          {selected.definition.distinguishing_features.length>0&&<>
            <Typography.Title level={5}>差异特征</Typography.Title>
            <Space wrap>{selected.definition.distinguishing_features.map((item,index)=><Tag key={`${item}-${index}`} color="gold">{item}</Tag>)}</Space>
          </>}
          {selected.definition.representative_enterprises.length>0&&<>
            <Typography.Title level={5}>代表企业</Typography.Title>
            <Space wrap>{selected.definition.representative_enterprises.map((item,index)=><Tag key={`${item}-${index}`}>{item}</Tag>)}</Space>
          </>}
          {selected.definition.growth_trajectory.length>0&&<>
            <Typography.Title level={5}>演化轨迹</Typography.Title>
            <Space wrap direction="vertical">{selected.definition.growth_trajectory.map((item,index)=><Typography.Text key={index}>{item}</Typography.Text>)}</Space>
          </>}
        </>}
        {selected.growth.per_window.length>0&&<>
          <Typography.Title level={5}>各窗口独立发布数</Typography.Title>
          <Space wrap>{selected.growth.per_window.map(item=><Tag key={item.date}>{item.date} · {item.distinct_postings} 条</Tag>)}</Space>
        </>}
        <Typography.Title level={5}>消融判定</Typography.Title>
        <Space wrap>{Object.entries(selected.ablation_states).map(([mode,state])=><Tag key={mode} color={stateColor(state)}>{ablationLabel(mode)}：{stateLabel(state)}</Tag>)}</Space>
        {(selected.display_refs?.length||selected.evidence_refs.length>0)&&<>
          <Typography.Title level={5}>证据引用</Typography.Title>
          <Space wrap>{(selected.display_refs?.length
            ?selected.display_refs
            :selected.evidence_refs.filter(readableEvidenceRef)
          ).map(ref=><Tag key={ref}>{ref}</Tag>)}</Space>
        </>}
      </>}
    </Drawer>
  </div>;
}
