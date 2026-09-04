import {useEffect,useMemo,useState} from 'react';
import {Button,Card,Empty,Segmented,Select,Space,Spin,Table,Tabs,Tag,Typography} from 'antd';
import {useParams} from 'react-router-dom';
import {GraphView,type GraphViewMode} from '../../../GraphView';
import {ApiError} from '../../../shared/api';
import {Failure,type LoadState} from '../../../shared/components/States';
import {EvidenceDrawer} from '../../evidence/components/EvidenceViewer';
import {getEmergingDisplay,type EmergingPosition} from '../api';
import {buildEmergingGraph,type EmergingGraphClaim,type EmergingGraphEvidence,type EmergingGraphSkill} from '../lib/emergingGraph';

const requirementLabels={required:'必备技能',bonus:'加分技能'};

export function EmergingGraph(){
  const {emergingId=''}=useParams();
  return <EmergingGraphPage key={emergingId} emergingId={emergingId}/>;
}

function EmergingGraphPage({emergingId}:{emergingId:string}){
  const [state,setState]=useState<LoadState<EmergingPosition>>({kind:'loading'});
  const [reload,setReload]=useState(0);
  const [viewMode,setViewMode]=useState<GraphViewMode>('skills');
  const [requirement,setRequirement]=useState<'all'|'required'|'bonus'>('all');
  const [selectedId,setSelectedId]=useState<string|null>(null);
  const [openedEvidence,setOpenedEvidence]=useState<{title:string;items:EmergingGraphEvidence[]}|null>(null);
  useEffect(()=>{
    let active=true;
    getEmergingDisplay(emergingId)
      .then(data=>{if(active)setState({kind:'success',data})})
      .catch((error:ApiError)=>{if(active)setState({kind:'error',message:error.message,status:error.status})});
    return()=>{active=false};
  },[emergingId,reload]);
  const graph=useMemo(()=>state.kind==='success'?buildEmergingGraph(state.data):null,[state]);
  const visibleSkills=useMemo(()=>graph?.skills.filter(item=>requirement==='all'||item.requirement===requirement)??[],[graph,requirement]);
  const selected=visibleSkills.find(item=>item.skill_id===selectedId)??visibleSkills[0];
  const retry=()=>{setState({kind:'loading'});setReload(value=>value+1)};
  if(state.kind==='loading')return <div className="center-loading" aria-label="正在加载岗位图谱"><Spin size="large"/></div>;
  if(state.kind==='error')return <Failure {...state} retry={retry}/>;
  if(!graph)return null;

  const evidenceButton=(title:string,items:EmergingGraphEvidence[])=>items.length>0
    ?<Button onClick={()=>setOpenedEvidence({title,items})}>查看证据</Button>:null;
  const claimTable=(items:EmergingGraphClaim[],columnTitle:string)=><Table
    className="profile-table" rowKey="id" dataSource={items}
    pagination={{pageSize:10,showSizeChanger:false,hideOnSinglePage:true}}
    locale={{emptyText:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无内容"/>}}
    columns={[
      {title:columnTitle,dataIndex:'text'},
      {title:'证据',width:130,render:(_:unknown,item:EmergingGraphClaim)=>evidenceButton(item.text,item.evidence)},
    ]}
  />;
  return <div className="emerging-graph-page">
    <div className="page-heading"><Typography.Title level={2}>{graph.name}能力图谱</Typography.Title></div>
    <div className="graph-profile-view-toolbar emerging-graph-toolbar">
      <Typography.Text strong>图谱视图</Typography.Text>
      <Segmented value={viewMode} onChange={value=>setViewMode(value as GraphViewMode)} options={[
        {label:'技能全景',value:'skills'},{label:'逐层探索',value:'explore'},{label:'层级树',value:'hierarchy'},
      ]}/>
    </div>
    <div className="graph-layout emerging-graph-layout">
      <section className="graph-layout-info">
        <Typography.Title level={5}>技能统计</Typography.Title>
        <div className="graph-build-info">
          <div><span>技能总数</span><strong>{graph.skills.length}</strong></div>
          <div><span>必备技能</span><strong>{graph.skills.filter(item=>item.requirement==='required').length}</strong></div>
          <div><span>加分技能</span><strong>{graph.skills.filter(item=>item.requirement==='bonus').length}</strong></div>
          <div><span>核心职责</span><strong>{graph.responsibilities.length}</strong></div>
        </div>
        <div className="emerging-graph-filter">
          <Typography.Text strong>岗位要求</Typography.Text>
          <Select aria-label="筛选岗位要求" value={requirement} onChange={setRequirement} options={[
            {value:'all',label:'全部技能'},{value:'required',label:'必备技能'},{value:'bonus',label:'加分技能'},
          ]}/>
        </div>
      </section>
      <section className="graph-layout-canvas">
        {visibleSkills.length?<div className="graph-filter-stage"><GraphView
          position={graph.positionId} positionName={graph.name} relations={visibleSkills}
          viewMode={viewMode} onSelect={setSelectedId}
        /></div>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无技能"/>}
      </section>
      <section className="graph-layout-tools" aria-label="技能详情">
        {selected&&<>
          <div className="graph-tools-head"><Typography.Title level={5} style={{margin:0}}>{selected.canonical_name}</Typography.Title></div>
          <Tag className="emerging-graph-requirement" color={selected.requirement==='required'?'success':'default'}>{requirementLabels[selected.requirement]}</Tag>
          {selected.supportJdCount!=null&&<div className="graph-tools-info"><div className="graph-tools-field"><span>支持 JD</span><strong>{selected.supportJdCount}</strong></div></div>}
          {selected.evidence.length>0&&<>
            <Typography.Paragraph className="emerging-graph-quote">{selected.evidence[0].quote}</Typography.Paragraph>
            <div className="graph-tools-actions">{evidenceButton(selected.canonical_name,selected.evidence)}</div>
          </>}
        </>}
      </section>
    </div>
    <Card className="profile graph-profile-lists" title="岗位画像明细"><Tabs items={[
      {key:'skills',label:'技能要求',children:<Table<EmergingGraphSkill> className="profile-table" rowKey="skill_id" dataSource={graph.skills} pagination={{pageSize:10,showSizeChanger:false,hideOnSinglePage:true}} columns={[
        {title:'技能',dataIndex:'canonical_name',render:(value:string,item:EmergingGraphSkill)=><Button type="link" onClick={()=>{setRequirement('all');setSelectedId(item.skill_id)}}>{value}</Button>},
        {title:'岗位要求',width:140,render:(_:unknown,item:EmergingGraphSkill)=>requirementLabels[item.requirement]},
        {title:'支持 JD',width:110,render:(_:unknown,item:EmergingGraphSkill)=>item.supportJdCount??'—'},
        {title:'证据',width:130,render:(_:unknown,item:EmergingGraphSkill)=>evidenceButton(item.canonical_name,item.evidence)},
      ]}/>},
      {key:'responsibilities',label:'核心职责',children:claimTable(graph.responsibilities,'职责')},
      {key:'scenarios',label:'应用场景',children:claimTable(graph.scenarios,'应用场景')},
    ]}/></Card>
    <EvidenceDrawer open={Boolean(openedEvidence)} title="证据" subtitle={openedEvidence?.title??''} onClose={()=>setOpenedEvidence(null)}>
      <Space direction="vertical" size={20} className="full">{openedEvidence?.items.map((item,index)=><article className="emerging-graph-evidence" key={index}>
        {(item.source||item.window)&&<Space wrap>{item.source&&<Tag>{item.source}</Tag>}{item.window&&<Tag>{item.window}</Tag>}</Space>}
        <blockquote>{item.quote}</blockquote>
      </article>)}</Space>
    </EvidenceDrawer>
  </div>;
}
