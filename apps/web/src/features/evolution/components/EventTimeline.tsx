import {useMemo,useState} from 'react';
import {Button,Empty,Select,Space,Spin,Tag,Typography} from 'antd';
import {ArrowRightOutlined,ReloadOutlined,ThunderboltOutlined} from '@ant-design/icons';
import type {ApiError} from '../../../shared/api';
import type {EvolutionEvent,EvolutionGraphVersion,EvolutionVersionPair} from '../types';
import {
  dateLabel,eventChangeSummary,eventGroup,eventSubject,eventTypeLabel,
  filterEvents,isStrongEvent,monthLabel,needsCaution,overviewStats,percent,sortEvents,versionNumberById,
} from '../lib/eventFormat';
import {EventDetailDrawer} from './EventDetailDrawer';

type EventTimelineProps={
  positionName:string;
  versions:EvolutionGraphVersion[];
  versionPairs:EvolutionVersionPair[];
  events:EvolutionEvent[];
  loading:boolean;
  error?:ApiError;
  onRetry:()=>void;
};

const groupTags:Array<{value:string;label:string}>=[
  {value:'all',label:'全部'},
  {value:'emergence',label:'新增 / 扩张'},
  {value:'replacement',label:'替代 / 迁移'},
  {value:'decline',label:'衰退 / 收缩'},
  {value:'shift',label:'职责 / 名称变化'},
];

export function EventTimeline({positionName,versions,versionPairs,events,loading,error,onRetry}:EventTimelineProps){
  const [groupFilter,setGroupFilter]=useState<string>('all');
  const [pairFilter,setPairFilter]=useState<string>('all');
  const [selectedEvent,setSelectedEvent]=useState<EvolutionEvent>();

  const sorted=useMemo(()=>sortEvents(events),[events]);
  const stats=useMemo(()=>overviewStats(events,versions,versionPairs),[events,versions,versionPairs]);
  const filtered=useMemo(()=>filterEvents(sorted,groupFilter,pairFilter),[sorted,groupFilter,pairFilter]);
  const pairOptions=useMemo(()=>versionPairs.map(pair=>({
    value:`${pair.from_version_id}:${pair.to_version_id}`,
    label:`${versionNumberById(versions,pair.from_version_id)} → ${versionNumberById(versions,pair.to_version_id)}`,
  })),[versionPairs,versions]);

  const insufficientVersions=versions.length<2;

  return <section className="event-surface">
    <div className="event-surface-head">
      <div>
        <Typography.Title level={4}>{positionName}能力变化记录</Typography.Title>
        <Typography.Text type="secondary">跨图谱版本检测出的岗位能力结构变化 · {events.length} 条记录</Typography.Text>
      </div>
      <Space>
        <Button icon={<ReloadOutlined/>} loading={loading} onClick={onRetry}>刷新</Button>
      </Space>
    </div>


    {error
      ?<div className="state-panel event-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={<span>能力变化记录暂时无法加载。<br/><small>{error.status?`（${error.message}）`:'请稍后重试'}</small></span>}/><Button onClick={onRetry}>重新加载</Button></div>
      :loading&&versions.length===0
        ?<div className="state-panel loading-state"><Spin/><span className="state-panel-hint">正在加载能力变化记录…</span></div>
        :insufficientVersions
          ?<div className="state-panel event-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={`能力变化至少需要 2 个已发布图谱版本；当前只有 ${versions.length} 个。岗位构建次数不等于已发布版本数。`}/></div>
          :loading
            ?<div className="state-panel loading-state"><Spin/><span className="state-panel-hint">正在刷新能力变化记录…</span></div>
            :events.length===0
              ?<div className="state-panel event-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前岗位尚未检测到能力变化记录。"/></div>
              :<>
              <div className="event-overview">
                <div className="event-overview-stat"><strong>{stats.total}</strong><span>事件总数</span></div>
                <div className="event-overview-stat tone-emergence"><strong>{stats.emergence}</strong><span>新增 / 扩张</span></div>
                <div className="event-overview-stat tone-replacement"><strong>{stats.replacement}</strong><span>替代 / 迁移</span></div>
                <div className="event-overview-stat tone-decline"><strong>{stats.decline}</strong><span>衰退 / 收缩</span></div>
                <div className="event-overview-stat tone-shift"><strong>{stats.shift}</strong><span>职责 / 名称</span></div>
                <div className="event-overview-stat"><strong>{stats.versionCount}</strong><span>涉及版本</span></div>
              </div>

              <div className="event-toolbar">
                <Select size="small" value={groupFilter} onChange={setGroupFilter} options={groupTags} style={{width:150}}/>
                <Select size="small" value={pairFilter} onChange={setPairFilter} options={[{value:'all',label:'全部版本对'},...pairOptions]} style={{width:180}}/>
                <span className="event-toolbar-hint">{filtered.length} / {events.length} 个事件</span>
              </div>

              <div className="event-timeline">
                {filtered.length===0
                  ?<div className="state-panel event-state"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件下没有能力变化记录"/></div>
                  :filtered.map((event,index)=>{const group=eventGroup(event.event_type);const strong=isStrongEvent(event);const caution=needsCaution(event);return <article className={`event-timeline-item group-${group}${strong?' is-strong':''}`} key={event.event_id}>
                    <div className="event-timeline-rail"><span className="event-timeline-dot"/>{index<filtered.length-1&&<span className="event-timeline-line"/>}</div>
                    <div className="event-timeline-date"><strong>{monthLabel(event.created_at)}</strong><small>{dateLabel(event.created_at)}</small><Tag>{versionNumberById(versions,event.from_version)} → {versionNumberById(versions,event.to_version)}</Tag></div>
                    <div className="event-timeline-card">
                      <div className="event-timeline-card-head">
                        <Tag className="event-type-tag"><ThunderboltOutlined/>{eventTypeLabel(event.event_type)}</Tag>
                        <div className="event-timeline-flags">
                          {strong&&<Tag className="flag-strong">强信号</Tag>}
                          {caution&&<Tag className="flag-caution">需谨慎解释</Tag>}
                        </div>
                      </div>
                      <div className="event-timeline-subject">{eventSubject(event)}</div>
                      <p className="event-timeline-summary">{eventChangeSummary(event)}</p>
                      <div className="event-timeline-metrics">
                        <span>置信度 <strong>{percent(event.confidence)}</strong></span>
                        <span>变化强度 <strong>{percent(event.magnitude)}</strong></span>
                        <button type="button" className="event-view-evidence" onClick={()=>setSelectedEvent(event)}>查看证据<ArrowRightOutlined/></button>
                      </div>
                    </div>
                  </article>})}
              </div>
            </>}

    <EventDetailDrawer event={selectedEvent} positionName={positionName} versions={versions} onClose={()=>setSelectedEvent(undefined)}/>
  </section>;
}
