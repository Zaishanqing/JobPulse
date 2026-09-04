/* eslint-disable react-refresh/only-export-components */
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import {App,Button,Collapse,Descriptions,Drawer,Empty,Space,Table,Tag,Timeline,Tooltip,Typography} from 'antd';
import {ApiError,localizeSystemMessage} from '../../../shared/api';
import {WorkbenchState,type LoadState} from '../../../shared/components/States';
import {useAuth} from '../../auth/AuthContext';
import {
  enterDiscoveryCandidateGovernance,
  getDiscoveryCandidateTrajectory,
  listClusters,
  listDiscoveryCandidates,
  type DiscoveryCandidate,
  type DiscoveryCandidateList,
  type DiscoveryCandidateObservation,
  type PositionCluster,
} from '../api';
import {emergingCacheKeys,invalidateEmergingCache,loadEmergingCache,readEmergingCache} from '../cache';
import {useEmergingCachedQuery} from '../useEmergingCachedQuery';
import {clusterDisplayName,discoveryWindowLabel,isTechnicalIdentifier,readableObservationCluster} from '../lib/discoveryDisplay';

type Json=Record<string,unknown>;
const object=(value:unknown):Json=>value&&typeof value==='object'&&!Array.isArray(value)?value as Json:{};
const array=(value:unknown):unknown[]=>Array.isArray(value)?value:[];
const labelOf=(value:unknown)=>typeof value==='string'&&value.trim()?value:'—';
const numberText=(value:unknown,digits=3)=>typeof value==='number'&&Number.isFinite(value)?value.toFixed(digits):'—';

const STATUS_LABELS:Record<string,string>={
  weak_signal:'弱信号',
  incubating:'孵化中',
  emerging_candidate:'新兴候选',
  stable_emerging_role:'稳定新兴岗位',
  dead:'已消亡',
  noise:'噪声',
  official_position:'正式岗位',
};
const STATUS_COLORS:Record<string,string>={
  weak_signal:'default',
  incubating:'blue',
  emerging_candidate:'gold',
  stable_emerging_role:'success',
  dead:'red',
  noise:'red',
  official_position:'purple',
};
const statusLabel=(status:string)=>STATUS_LABELS[status]||'状态未知';
const statusColor=(status:string)=>STATUS_COLORS[status]||'default';

/** 优先使用 definition.required_skills 中的可读原始名称；缺省回退到 identity_profile.skills。 */
const skillsOf=(candidate:DiscoveryCandidate):string[]=>{
  const definitionSkills=array(object(candidate.definition).required_skills)
    .map(item=>labelOf(object(item).raw_skill))
    .filter(value=>value!=='—'&&!isTechnicalIdentifier(value));
  if(definitionSkills.length)return definitionSkills;
  return array(object(candidate.identity_profile).skills)
    .map(value=>labelOf(value))
    .filter(value=>value!=='—'&&!isTechnicalIdentifier(value));
};
const responsibilitiesOf=(candidate:DiscoveryCandidate)=>array(object(candidate.identity_profile).responsibilities)
  .map(value=>labelOf(value))
  .filter(value=>value!=='—'&&!isTechnicalIdentifier(value));
const memberJdCount=(candidate:DiscoveryCandidate)=>array(object(candidate.identity_profile).member_jd_ids).length;
const evidenceSourceCount=(point:DiscoveryCandidateObservation)=>{
  const evidence=object(point.evidence);
  const sources=array(evidence.sources);
  return sources.length;
};

const STATUS_PRIORITY:Record<string,number>={
  stable_emerging_role:6,
  emerging_candidate:5,
  incubating:4,
  weak_signal:3,
  official_position:2,
  dead:1,
  noise:0,
};
const identityKey=(candidate:DiscoveryCandidate)=>labelOf(candidate.canonical_title||candidate.display_title)
  .trim().toLocaleLowerCase().replace(/\s+/g,' ');
const recencyKey=(candidate:DiscoveryCandidate)=>[
  candidate.last_seen_window_id.split('@')[0].split('..').at(-1)||'',
  String(STATUS_PRIORITY[candidate.status]??-1).padStart(2,'0'),
  String(candidate.age||0).padStart(6,'0'),
  candidate.updated_at||'',
].join('|');

export const latestCandidatesByIdentity=(candidates:DiscoveryCandidate[])=>{
  const latest=new Map<string,DiscoveryCandidate>();
  candidates.forEach(candidate=>{
    const key=identityKey(candidate);
    const current=latest.get(key);
    if(!current||recencyKey(candidate)>recencyKey(current))latest.set(key,candidate);
  });
  return [...latest.values()].sort((left,right)=>recencyKey(right).localeCompare(recencyKey(left)));
};

export const sortCandidatesForReview=(
  candidates:DiscoveryCandidate[],
  isReviewable:(candidate:DiscoveryCandidate)=>boolean,
)=>[...candidates].sort((left,right)=>{
  const group=(candidate:DiscoveryCandidate)=>isReviewable(candidate)?0:candidate.status==='weak_signal'?2:1;
  const groupDifference=group(left)-group(right);
  return groupDifference||recencyKey(right).localeCompare(recencyKey(left));
});

type GovernanceGate={operable:boolean;reason:string;detail?:string};

function IdentityContinuity({point}:{point:DiscoveryCandidateObservation}){
  const evidence=object(point.match_evidence);
  const components=object(evidence.components);
  // 真实结构：semantic 位于 match_evidence.components.semantic_similarity；
  // 缺省时回退到 observation 顶层真实 semantic_similarity。
  const semantic=components.semantic_similarity??point.semantic_similarity;
  const semanticText=typeof semantic==='number'&&Number.isFinite(semantic)
    ?semantic.toFixed(3)
    :'不可用';
  const similarity=typeof evidence.identity_similarity==='number'
    ?evidence.identity_similarity
    :point.identity_similarity;
  const threshold=typeof evidence.threshold==='number'?evidence.threshold:null;
  const matched=evidence.matched;
  const decisionSummary=matched
    ?'综合相似度达到判定阈值，沿用同一候选岗位。'
    :'综合相似度未达到判定阈值，作为新的候选岗位记录。';
  return <div className="identity-continuity">
    <div className="identity-score-line">
      <Typography.Text strong>身份相似度</Typography.Text>
      <span className="numeric-score">{numberText(similarity)}</span>
      {threshold!==null&&<>
        <Typography.Text type="secondary">{matched?'≥':'<'}</Typography.Text>
        <span className="numeric-score">阈值 {numberText(threshold)}</span>
      </>}
      <Tag color={matched?'success':'default'}>{matched?'匹配':'未匹配'}</Tag>
    </div>
    <Descriptions
      size="small"
      column={1}
      items={[
        {key:'title',label:'标题相似度',children:numberText(components.title_similarity)},
        {key:'skill',label:'技能相似度',children:numberText(components.skill_similarity)},
        {key:'responsibility',label:'职责相似度',children:numberText(components.responsibility_similarity)},
        {key:'membership',label:'簇成员重合',children:numberText(components.membership_overlap)},
        {key:'semantic',label:'语义相似度',children:semanticText},
      ]}
    />
    <Typography.Paragraph type="secondary" className="identity-decision-reason">{decisionSummary}</Typography.Paragraph>
  </div>;
}

function TrajectoryTimeline({points,currentStatus,gate,onGovern}:{points:DiscoveryCandidateObservation[];currentStatus:string;gate:GovernanceGate;onGovern:()=>void}){
  const lastStatus=points.length?points[points.length-1].status:null;
  const statuses=[...new Set(points.map(point=>point.status))];
  const hasTrailingStatus=Boolean(currentStatus&&currentStatus!==lastStatus);
  if(hasTrailingStatus)statuses.push(currentStatus);
  if(!points.length)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该候选没有跨窗口轨迹数据"/>;
  return <div className="candidate-lifecycle-timeline">
    <div className="candidate-lifecycle-status-path" aria-label="生命周期状态链">
      <Typography.Text type="secondary">生命周期状态链：</Typography.Text>
      <Space wrap>
        {statuses.map((status,index)=><Tag key={`${status}-${index}`} color={statusColor(status)}>{index<statuses.length-1||!hasTrailingStatus?statusLabel(status):`当前：${statusLabel(status)}`}</Tag>)}
      </Space>
    </div>
    {hasTrailingStatus&&<Typography.Paragraph type="secondary" className="candidate-status-trailing-hint">
      当前状态 {statusLabel(currentStatus)} 发生于最近一次岗位簇观测之后，当前轨迹未返回对应观测点，因此不虚构窗口 / 运行 / 岗位簇标识。
    </Typography.Paragraph>}
    <Timeline
      items={points.map((point,index)=>({
        color:statusColor(point.status) as never,
        children:<div className="candidate-trajectory-point" key={point.observation_id||index}>
          <div className="candidate-trajectory-point-head">
            <Typography.Text strong>{discoveryWindowLabel(point.window_id)}</Typography.Text>
            <Tag color={statusColor(point.status)}>{statusLabel(point.status)}</Tag>
            <span className="numeric-score">生命周期得分 {numberText(point.emergence_score)}</span>
          </div>
          <Typography.Paragraph className="candidate-trajectory-title">{labelOf(point.title)}</Typography.Paragraph>
          <Descriptions
            size="small"
            column={1}
            items={[
              {key:'cluster',label:'岗位簇',children:readableObservationCluster(point.cluster_name,point.title)},
              {key:'support',label:'窗口支持 JD',children:`${Number(point.support_count||0)} 条`},
              {key:'companies',label:'企业覆盖',children:`${Number(point.company_count||0)} 家`},
              {key:'identity',label:'身份相似度',children:numberText(point.identity_similarity)},
            ]}
          />
          {evidenceSourceCount(point)>0&&<Typography.Text type="secondary" className="candidate-evidence-sources">证据源数：{evidenceSourceCount(point)}</Typography.Text>}
          <Collapse
            ghost
            size="small"
            className="identity-collapse"
            items={[{key:'identity',label:'身份连续性解释',children:<IdentityContinuity point={point}/>}]}
          />
        </div>,
      }))}
    />
    <div className="candidate-governance-entry">
      <Typography.Title level={5}>进入人工审核</Typography.Title>
      <Typography.Paragraph type="secondary">
        候选只是跨窗口演化信号。只有进入「稳定新兴岗位」状态且当前岗位簇已投影到主系统时，才能创建新兴岗位并进入「审核 → 通过 → 发布 → 转标准岗位」流程；系统不会自动发布。
      </Typography.Paragraph>
      {gate.operable
        ?<Button type="primary" onClick={()=>{void onGovern()}}>进入审核（创建新兴岗位）</Button>
        :<Tooltip title={gate.detail||gate.reason}>
          <Button disabled>{gate.reason}</Button>
        </Tooltip>}
    </div>
  </div>;
}

type LifecycleFocusRequest={clusterId:string;requestId:number};

export function CandidateLifecycle({onGovernanceCreated,focusRequest}:{onGovernanceCreated?:()=>void;focusRequest?:LifecycleFocusRequest}={}){
  const {message}=App.useApp();
  const {user}=useAuth();
  const userId=user?.user_id??'anonymous';
  const candidateQuery=useEmergingCachedQuery<DiscoveryCandidateList>(
    emergingCacheKeys.candidates,
    listDiscoveryCandidates,
  );
  const clusterQuery=useEmergingCachedQuery<PositionCluster[]>(
    emergingCacheKeys.clusters,
    listClusters,
  );
  const state:LoadState<DiscoveryCandidate[]>=useMemo(
    ()=>candidateQuery.state.kind==='success'
      ?{kind:'success',data:latestCandidatesByIdentity(candidateQuery.state.data.candidates)}
      :candidateQuery.state,
    [candidateQuery.state],
  );
  const clusterState=clusterQuery.state;
  const [selected,setSelected]=useState<DiscoveryCandidate|null>(null);
  const [trajectory,setTrajectory]=useState<LoadState<DiscoveryCandidateObservation[]>>({kind:'loading'});
  const [drawerOpen,setDrawerOpen]=useState(false);
  const trajectorySeq=useRef(0);
  const handledFocusRequest=useRef<number|null>(null);

  const openTrajectory=useCallback((candidate:DiscoveryCandidate)=>{
    const seq=++trajectorySeq.current;
    const cacheKey=emergingCacheKeys.trajectory(candidate.candidate_id);
    const cached=readEmergingCache<DiscoveryCandidateObservation[]>(userId,cacheKey);
    setSelected(candidate);
    setDrawerOpen(true);
    setTrajectory(cached?{kind:'success',data:cached}:{kind:'loading'});
    loadEmergingCache(
      userId,
      cacheKey,
      ()=>getDiscoveryCandidateTrajectory(candidate.candidate_id).then(data=>data.trajectory),
    )
      .then(data=>{if(trajectorySeq.current===seq)setTrajectory({kind:'success',data})})
      .catch((error:ApiError)=>{if(trajectorySeq.current===seq)setTrajectory({kind:'error',message:error.message,status:error.status})});
  },[userId]);

  useEffect(()=>{
    if(state.kind!=='success'||!focusRequest||handledFocusRequest.current===focusRequest.requestId)return;
    handledFocusRequest.current=focusRequest.requestId;
    const candidate=state.data.find(item=>item.current_cluster_id===focusRequest.clusterId)
      ||state.data.find(item=>item.previous_cluster_ids.includes(focusRequest.clusterId));
    const timer=window.setTimeout(()=>{
      if(candidate)openTrajectory(candidate);
      else message.warning('该岗位簇暂未形成可查看的生命周期候选');
    },0);
    return()=>window.clearTimeout(timer);
  },[focusRequest,message,openTrajectory,state]);

  const clusterIds=new Set(clusterState.kind==='success'?clusterState.data.map(item=>item.cluster_id):[]);

  const governanceGate=(candidate:DiscoveryCandidate):GovernanceGate=>{
    if(clusterState.kind==='error'){
      return {operable:false,reason:'无法确认岗位簇映射状态',detail:'主系统岗位簇查询失败，不能确认该候选的当前岗位簇是否已投影；请重试后再操作。'};
    }
    if(clusterState.kind!=='success'){
      return {operable:false,reason:'正在确认岗位簇映射状态',detail:'岗位簇列表加载中，暂时不能操作。'};
    }
    if(candidate.status!=='stable_emerging_role'){
      return {operable:false,reason:`仅「稳定新兴岗位」状态可进入治理（当前：${statusLabel(candidate.status)}）`,detail:'生命周期门禁：只有稳定新兴岗位候选才能进入人工审核。'};
    }
    if(!candidate.current_cluster_id){
      return {operable:false,reason:'该候选没有当前岗位簇',detail:'候选未关联当前岗位簇，无法创建新兴岗位。'};
    }
    if(!clusterIds.has(candidate.current_cluster_id)){
      return {operable:false,reason:'当前岗位簇尚未投影到主系统',detail:'该候选的当前岗位簇不在主系统岗位簇中，无法创建新兴岗位。'};
    }
    return {operable:true,reason:'',detail:undefined};
  };

  const enterGovernance=async(candidate:DiscoveryCandidate)=>{
    try{
      await enterDiscoveryCandidateGovernance(candidate.candidate_id);
      invalidateEmergingCache(userId,[
        emergingCacheKeys.candidates,
        emergingCacheKeys.governance,
        emergingCacheKeys.published,
      ]);
      await candidateQuery.reload();
      message.success('已创建新兴岗位，请在「新兴岗位候选」页继续审核');
      onGovernanceCreated?.();
    }catch(error){
      message.error(error instanceof Error?localizeSystemMessage(error.message):'创建失败');
    }
  };

  return <div className="candidate-lifecycle">
    <WorkbenchState
      state={state}
      retry={candidateQuery.reload}
      render={items=><Table
        className="candidate-lifecycle-table"
        rowKey="candidate_id"
        dataSource={sortCandidatesForReview(items,candidate=>governanceGate(candidate).operable)}
        pagination={false}
        tableLayout="fixed"
        onRow={item=>({onClick:()=>openTrajectory(item)})}
        columns={[
          {title:'候选名称',width:'19%',render:(_:unknown,item:DiscoveryCandidate)=><Typography.Text className="candidate-name" strong>{labelOf(item.display_title||item.canonical_title)}</Typography.Text>},
          {title:'当前状态',width:'9%',align:'center',render:(_:unknown,item:DiscoveryCandidate)=><Tag color={statusColor(item.status)}>{statusLabel(item.status)}</Tag>},
          {title:'首次出现',width:'13%',render:(_:unknown,item:DiscoveryCandidate)=><span className="candidate-window-label">{discoveryWindowLabel(item.first_seen_window_id)}</span>},
          {title:'最近出现',width:'13%',render:(_:unknown,item:DiscoveryCandidate)=><span className="candidate-window-label">{discoveryWindowLabel(item.last_seen_window_id)}</span>},
          {title:'持续窗口',width:'7%',align:'center',dataIndex:'age'},
          {title:'代表技能',width:'14%',render:(_:unknown,item:DiscoveryCandidate)=>{const skills=skillsOf(item);return skills.length?<Space className="candidate-skills" size={[4,4]} wrap>{skills.slice(0,4).map(skill=><Tag key={skill}>{skill}</Tag>)}</Space>:'待识别';}},
          {title:'累计 JD',width:'7%',align:'center',render:(_:unknown,item:DiscoveryCandidate)=>`${memberJdCount(item)} 份`},
          {title:'生命周期得分',width:'8%',align:'center',render:(_:unknown,item:DiscoveryCandidate)=><span className="numeric-score">{numberText(item.emergence_score)}</span>},
          {title:'操作',width:'10%',align:'center',render:(_:unknown,item:DiscoveryCandidate)=>{
            const gate=governanceGate(item);
            return <Space wrap>
              <Button onClick={event=>{event.stopPropagation();openTrajectory(item)}}>查看演化</Button>
              <Tooltip title={gate.detail||(gate.operable?undefined:gate.reason)}>
                <Button
                  type="primary"
                  disabled={!gate.operable}
                  onClick={event=>{event.stopPropagation();void enterGovernance(item)}}
                >进入审核</Button>
              </Tooltip>
            </Space>;
          }},
        ]}
      />}
    />

    <Drawer
      open={drawerOpen}
      onClose={()=>setDrawerOpen(false)}
      width={760}
      title={selected?`候选生命周期 · ${labelOf(selected.display_title||selected.canonical_title)}`:'候选生命周期'}
    >
      {selected&&<>
        <div className="candidate-lifecycle-summary">
          <Space wrap>
            <Tag color={statusColor(selected.status)}>{statusLabel(selected.status)}</Tag>
          </Space>
          <Descriptions
            size="small"
            column={2}
            items={[
              {key:'first',label:'首次出现',children:discoveryWindowLabel(selected.first_seen_window_id)},
              {key:'last',label:'最近出现',children:discoveryWindowLabel(selected.last_seen_window_id)},
              {key:'age',label:'持续窗口数',children:`${selected.age} 个窗口`},
              {key:'cluster',label:'当前岗位簇',children:clusterState.kind==='success'?clusterDisplayName(clusterState.data.find(item=>item.cluster_id===selected.current_cluster_id)||{}):'正在读取'},
              {key:'skills',label:'核心技能',children:skillsOf(selected).join('、')||'待识别'},
              {key:'responsibilities',label:'职责表达',children:responsibilitiesOf(selected).join('；')||'暂未生成'},
              {key:'jd',label:'累计代表 JD',children:`${memberJdCount(selected)} 份`},
              {key:'score',label:'生命周期得分',children:numberText(selected.emergence_score)},
              {key:'novelty',label:'新颖度',children:numberText(selected.novelty_score)},
              {key:'identity',label:'身份稳定性',children:`${selected.identity_stability||0} 次稳定匹配`},
            ]}
          />
        </div>
        <WorkbenchState
          state={trajectory}
          retry={()=>{if(selected)openTrajectory(selected)}}
          render={points=><TrajectoryTimeline
            points={points}
            currentStatus={selected.status}
            gate={governanceGate(selected)}
            onGovern={()=>{void enterGovernance(selected)}}
          />}
        />
      </>}
    </Drawer>
  </div>;
}
