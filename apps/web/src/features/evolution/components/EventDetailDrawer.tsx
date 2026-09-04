import {Descriptions,Drawer,Tag} from 'antd';
import {ArrowRightOutlined,DatabaseOutlined,FileTextOutlined,ThunderboltOutlined} from '@ant-design/icons';
import type {EvolutionEvent,EvolutionGraphVersion} from '../types';
import {
  dateLabel,eventChangeSummary,eventSubject,eventTypeLabel,isStrongEvent,needsCaution,
  percent,versionNumberById,
} from '../lib/eventFormat';

const metricLabel:Record<string,string>={
  before_weight:'变化前权重',after_weight:'变化后权重',delta:'权重变化量',breadth_score:'能力广度值',
  removed_count:'移除数量',added_count:'新增数量',similarity:'相似度',
};

export function EventDetailDrawer({event,positionName,versions,onClose}:{event?:EvolutionEvent;positionName:string;versions:EvolutionGraphVersion[];onClose:()=>void}){
  return <Drawer
    title={<span className="event-drawer-title"><ThunderboltOutlined/>{event?`能力变化记录 · ${eventTypeLabel(event.event_type)}`:'能力变化记录'}</span>}
    open={Boolean(event)}
    onClose={onClose}
    size={520}
    destroyOnClose
  >
    {!event?null:<div className="event-drawer">
      <section className="event-drawer-section">
        <div className="event-drawer-section-title"><FileTextOutlined/><span>事件</span></div>
        <Descriptions size="small" column={1} items={[
          {key:'type',label:'事件类型',children:<Tag>{eventTypeLabel(event.event_type)}</Tag>},
          {key:'subject',label:'事件主体',children:<strong>{eventSubject(event)}</strong>},
          {key:'change',label:'变化内容',children:eventChangeSummary(event)},
          {key:'confidence',label:'置信度',children:<span className={needsCaution(event)?'event-caution':''}>{percent(event.confidence)}<small>{needsCaution(event)?' · 需谨慎解释':''}</small></span>},
          {key:'magnitude',label:'变化强度',children:<span className={isStrongEvent(event)?'event-strong':''}>{percent(event.magnitude)}<small>{isStrongEvent(event)?' · 强信号':''}</small></span>},
        ]}/>
      </section>

      <section className="event-drawer-section">
        <div className="event-drawer-section-title"><DatabaseOutlined/><span>版本血缘</span></div>
        <div className="event-version-lineage">
          <div className="event-version-node">
            <strong>对比前版本 {versionNumberById(versions,event.from_version)}</strong>
            {versionMeta(versions,event.from_version,positionName)}
          </div>
          <ArrowRightOutlined/>
          <div className="event-version-node is-after">
            <strong>对比后版本 {versionNumberById(versions,event.to_version)}</strong>
            {versionMeta(versions,event.to_version,positionName)}
          </div>
        </div>
        <Descriptions size="small" column={1} items={[
          {key:'detector',label:'检测规则',children:event.detector_version?'系统已记录':'未返回'},
          {key:'source',label:'证据来源',children:event.evidence?.source==='graph_version_snapshot_diff'?'图谱版本关系快照对比':'图谱版本对比'},
          {key:'created',label:'事件时间',children:dateLabel(event.created_at)},
        ]}/>
      </section>

      <section className="event-drawer-section">
        <div className="event-drawer-section-title"><FileTextOutlined/><span>分析说明</span></div>
        <div className="event-reason"><strong>检测说明</strong><p>{eventChangeSummary(event)}</p></div>
        <Descriptions size="small" column={1} items={[
          {key:'signals',label:'变化信号',children:Array.isArray(event.metadata?.atomic_signals)?(event.metadata.atomic_signals as string[]).map(signal=><Tag key={String(signal)}>{eventTypeLabel(String(signal))}</Tag>):'未返回'},
          ...Object.entries(event.metrics||{}).filter(([key,value])=>Boolean(metricLabel[key])&&(typeof value==='number'||typeof value==='string')).map(([key,value])=>({key:`metric:${key}`,label:metricLabel[key],children:String(value)})),
        ]}/>
      </section>
    </div>}
  </Drawer>;
}

function versionMeta(versions:EvolutionGraphVersion[],versionId:number,positionName:string){
  const version=versions.find(item=>item.id===versionId);
  if(!version)return <small>版本元数据未返回</small>;
  const versionName=localizedVersionName(version.version_name,positionName);
  return <small>
    {versionName?`${versionName} · `:''}{dateLabel(version.created_at)}
    {version.is_current?' · 当前':''}
    {version.rollback_from_version_number!=null?` · 回滚自 V${version.rollback_from_version_number}`:''}
  </small>;
}

function localizedVersionName(versionName:string|null,positionName:string){
  if(!versionName)return '';
  const internalPositionPrefix=/^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*(?=-|$)/.exec(versionName)?.[0];
  return internalPositionPrefix&&positionName?`${positionName}${versionName.slice(internalPositionPrefix.length)}`:versionName;
}
