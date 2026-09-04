import {Button,Space,Spin,Table,Tag,Typography} from 'antd';
import {ArrowRightOutlined} from '@ant-design/icons';
import {useNavigate} from 'react-router-dom';
import {EmptyState,Failure} from '../../../shared/components/States';
import {listEmergingAssets,listRecentPositionSignals,type EmergingAsset,type RecentPositionSignal,type RecentPositionSignalFeed} from '../api';
import {emergingCacheKeys} from '../cache';
import {useEmergingCachedQuery} from '../useEmergingCachedQuery';
import {isTechnicalIdentifier} from '../lib/discoveryDisplay';

const month=(value:string|null)=>value?value.slice(0,7):'未提供';
const positionLabel=(value:string)=>isTechnicalIdentifier(value)?'待命名岗位':value;

export function EmergingList(){
  const nav=useNavigate();
  const publishedQuery=useEmergingCachedQuery<EmergingAsset[]>(
    emergingCacheKeys.assets,
    listEmergingAssets,
  );
  const signalQuery=useEmergingCachedQuery<RecentPositionSignalFeed>(
    emergingCacheKeys.recentSignals,
    listRecentPositionSignals,
  );
  const state=publishedQuery.state;
  const signalState=signalQuery.state;

  return <>
    <div className="page-heading">
      <Typography.Title level={2}>新兴岗位</Typography.Title>
    </div>
    {state.kind==='loading'?<div className="center-loading" aria-live="polite"><Spin size="large"/><span className="state-panel-hint">正在加载新兴岗位</span></div>
      :state.kind==='error'?<Failure {...state} retry={publishedQuery.reload}/>
      :<>
      <div className="analysis-surface-head emerging-list-section-head">
        <Typography.Title level={4}>新兴岗位发现结果</Typography.Title>
        <Tag color="green">{state.data.length} 个岗位</Tag>
      </div>
      {state.data.length?<Table
        className="primary-table"
        rowKey="emerging_id"
        dataSource={state.data}
        pagination={{pageSize:8,showSizeChanger:false}}
        columns={[
          {title:'岗位',render:(_:unknown,item:EmergingAsset)=><div className="table-primary"><strong>{positionLabel(item.position_name)}</strong><Tag>新兴岗位</Tag></div>},
          {title:'招聘样本',render:(_:unknown,item:EmergingAsset)=>`${item.support_jd_count} 份 JD`},
          {title:'操作',width:220,render:(_:unknown,item:EmergingAsset)=><Space><Button type="text" icon={<ArrowRightOutlined/>} onClick={()=>nav(`/emerging/${encodeURIComponent(item.emerging_id)}`)}>查看详情</Button><Button onClick={()=>nav(`/emerging/${encodeURIComponent(item.emerging_id)}/graph`)}>查看图谱</Button></Space>},
        ]}
      />:<EmptyState centered text="暂无新兴岗位"/>}
      <div className="analysis-surface-head emerging-list-section-head">
        <Typography.Title level={4}>近期岗位信号</Typography.Title>
        {signalState.kind==='success'&&<Tag color="blue">{signalState.data.signals.length} 个近期方向</Tag>}
      </div>
      {signalState.kind==='loading'?<div className="center-loading" aria-live="polite"><Spin/><span className="state-panel-hint">正在加载近期岗位信号</span></div>
      :signalState.kind==='error'?<Failure {...signalState} retry={signalQuery.reload}/>
      :signalState.data.signals.length?<Table
        className="primary-table"
        rowKey="signal_id"
        dataSource={signalState.data.signals}
        pagination={false}
        columns={[
          {title:'岗位方向',render:(_:unknown,item:RecentPositionSignal)=><div className="table-primary"><strong>{positionLabel(item.position_name)}</strong><Tag color="blue">近期信号</Tag></div>},
          {title:'代表性招聘标题',dataIndex:'representative_title'},
          {title:'能力关键词',render:(_:unknown,item:RecentPositionSignal)=><Space wrap>{item.skills.map(skill=><Tag key={skill}>{skill}</Tag>)}</Space>},
          {title:'最近观测',dataIndex:'observed_at',render:month},
          {title:'状态',width:140,render:()=><Typography.Text type="secondary">持续观察</Typography.Text>},
        ]}
      />:<EmptyState centered text="暂无符合正式投影规则的近期岗位信号"/>}
      </>}
  </>;
}
