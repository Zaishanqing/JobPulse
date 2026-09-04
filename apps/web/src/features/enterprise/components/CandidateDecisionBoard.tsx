import {useCallback,useEffect,useMemo,useState} from 'react';
import {
  App,
  Button,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
} from 'antd';
import type {ColumnsType} from 'antd/es/table';
import {localizeSystemMessage} from '../../../shared/api';
import {
  BarChartOutlined,
  CheckOutlined,
  CloseOutlined,
  EyeOutlined,
  LinkOutlined,
} from '@ant-design/icons';
import {Link} from 'react-router-dom';
import {ApiError} from '../../../shared/api';
import {Failure,ToastAlert as Alert} from '../../../shared/components/States';
import {dimensionLabel} from '../../matching/components/dimensionLabels';
import {decideCandidate,getCandidateDecisionBoard} from '../api';
import type {CandidateBoardItem,CandidateDecisionBoard} from '../types';

const evalStatusCopy:Record<string,string>={
  never_matched:'尚未匹配',
  pending:'排队中',
  running:'匹配中',
  succeeded:'已完成',
  failed:'匹配失败',
  stale:'评估已过期',
  needs_rematch:'需要重新匹配',
  revoked:'已撤销',
};

const evalStatusColor=(status:string)=>{
  if(status==='succeeded')return 'success';
  if(status==='running'||status==='pending')return 'processing';
  if(status==='failed'||status==='stale')return 'error';
  if(status==='needs_rematch')return 'warning';
  return 'default';
};

const decisionCopy:Record<string,string>={fit:'已适配',unfit:'已不适配'};
const decisionReasonCopy:Record<string,string>={requirements_met:'核心要求满足',experience_aligned:'经验匹配',critical_gap:'存在关键缺口',insufficient_evidence:'证据不足',other:'其他'};
const recommendationCopy:Record<string,string>={strong_match:'高度匹配',match:'匹配',partial_match:'部分匹配',weak_match:'匹配较弱',not_match:'不匹配'};
const isCurrentSucceeded=(item:CandidateBoardItem)=>item.evaluation_status==='succeeded'&&!item.stale;
const riskKindCopy:Record<string,string>={
  missing_required:'缺少必备技能',
  weak_requirement:'必备技能弱匹配',
  critical_gap:'关键缺口',
  evidence_weakness:'证据薄弱',
  hard_constraint:'硬性条件未满足',
  gap:'匹配缺口',
};

const scoreText=(score:number|null)=>score===null?'—':score.toFixed(1);
const coverageText=(coverage:{matched:number;total:number;coverage:number|null}|null)=>{
  if(!coverage)return '—';
  const percent=coverage.coverage===null?'':` · ${Math.round(coverage.coverage*100)}%`;
  return `${coverage.matched}/${coverage.total}${percent}`;
};

export function CandidateDecisionBoard({jobId}:{jobId:string}){
  const {message}=App.useApp();
  const [board,setBoard]=useState<CandidateDecisionBoard>({enterprise_job_id:'',total:0,ranked_count:0,items:[]});
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState<ApiError>();
  const [selected,setSelected]=useState<CandidateBoardItem>();
  const [compareOpen,setCompareOpen]=useState(false);
  const [compareIds,setCompareIds]=useState<string[]>([]);
  const [working,setWorking]=useState('');
  const [pendingDecision,setPendingDecision]=useState<{item:CandidateBoardItem;decision:'fit'|'unfit'}>();
  const [reasonCode,setReasonCode]=useState('');
  const [reasonText,setReasonText]=useState('');

  const load=useCallback(async()=>{
    setLoading(true);setError(undefined);
    try{setBoard(await getCandidateDecisionBoard(jobId))}
    catch(reason){setError(reason as ApiError)}
    finally{setLoading(false)}
  },[jobId]);

  useEffect(()=>{const timer=window.setTimeout(()=>void load(),0);return()=>window.clearTimeout(timer)},[load]);

  const decide=async(item:CandidateBoardItem,decision:'fit'|'unfit')=>{
    if(!item.evaluation_id)return;
    setWorking(`${decision}:${item.resume_id}`);setError(undefined);
    try{
      await decideCandidate(jobId,item.resume_id,item.evaluation_id,decision,reasonCode||undefined,reasonText||undefined);
      message.success(decision==='fit'?'已标记为适配':'已标记为不适配');
      await load();
    }catch(reason){setError(reason as ApiError)}
    finally{setWorking('');setPendingDecision(undefined);setReasonCode('');setReasonText('')}
  };

  const columns:ColumnsType<CandidateBoardItem>=[
    {title:'排名',dataIndex:'rank',width:70,render:(rank:number|null,item)=><span className="board-rank">{isCurrentSucceeded(item)?rank??'—':'—'}</span>},
    {title:'候选人',dataIndex:'candidate_display_name',width:180,ellipsis:true,render:(name:string,item)=>(
      <Space direction="vertical" size={0}>
        <Typography.Text strong>{name}</Typography.Text>
        {item.decision
          &&isCurrentSucceeded(item)
          &&item.decision.evaluation_id===item.evaluation_id
          &&<Tag color={item.decision.decision==='fit'?'success':'error'}>{decisionCopy[item.decision.decision]}</Tag>}
      </Space>
    )},
    {title:'匹配得分',dataIndex:'overall_score',width:110,render:(score:number|null,item)=>(
      <Space direction="vertical" size={0}>
        <span className={!isCurrentSucceeded(item)||score===null?'board-score board-score-muted':`board-score ${item.recommendation_level==='strong_match'?'board-score-strong':''}`}>{scoreText(isCurrentSucceeded(item)?score:null)}</span>
        {(!isCurrentSucceeded(item)||item.rank===null)&&item.evaluation_status!=='never_matched'&&<Typography.Text type="warning" className="board-meta">不参与排名</Typography.Text>}
      </Space>
    )},
    {title:'必备技能覆盖',dataIndex:'required_coverage',width:120,render:(coverage)=>coverageText(coverage)},
    {title:'关键缺口',dataIndex:'critical_gap_count',width:110,render:(count:number)=>(
      <Space direction="vertical" size={0}>
        <span className={count>0?'board-gap-count':''}>{count>0?`${count} 项`:'无'}</span>
      </Space>
    )},
    {title:'状态',dataIndex:'evaluation_status',width:120,render:(status:string,item)=>(
      <Space direction="vertical" size={0}>
        <Tag color={evalStatusColor(status)}>{evalStatusCopy[status]||'状态未知'}</Tag>
        {item.error_code&&<Typography.Text type="danger" className="board-meta">处理失败</Typography.Text>}
        {item.error_message&&<Typography.Text type="secondary" ellipsis={{tooltip:localizeSystemMessage(item.error_message)}} className="board-meta">{localizeSystemMessage(item.error_message)}</Typography.Text>}
        {item.candidate_status==='revoked'&&<Typography.Text type="secondary" className="board-meta">投递已撤销</Typography.Text>}
      </Space>
    )},
    {title:'操作',key:'actions',width:250,render:(_,item)=>(
      <Space size={4}>
        <Button size="small" icon={<EyeOutlined/>} onClick={()=>setSelected(item)}>查看</Button>
        {item.candidate_status==='revoked'
          ?<Tag>不可决策</Tag>
          :isCurrentSucceeded(item)?(
            <>
              <Button size="small" type="primary" aria-label="适配" icon={<CheckOutlined/>} loading={working===`fit:${item.resume_id}`} onClick={()=>setPendingDecision({item,decision:'fit'})}>适配</Button>
              <Button size="small" danger aria-label="不适配" icon={<CloseOutlined/>} loading={working===`unfit:${item.resume_id}`} onClick={()=>setPendingDecision({item,decision:'unfit'})}>不适配</Button>
            </>
          ):<Tag color="default">需先完成匹配</Tag>}
      </Space>
    )},
  ];

  const compareItems=useMemo(
    ()=>board.items.filter(item=>isCurrentSucceeded(item)&&item.rank!==null&&compareIds.includes(item.resume_id)),
    [board.items,compareIds],
  );

  const hasCandidates=board.total>0;
  const allUnmatched=hasCandidates&&board.items.every(item=>item.evaluation_status==='never_matched');

  if(loading)return <div className="state-panel loading-state"><Spin/><span className="state-panel-hint">正在加载决策板…</span></div>;

  if(error)return <Failure message={error.message} status={error.status} retry={()=>void load()}/>;

  if(!hasCandidates)return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无候选投递"/>;

  return <div className="candidate-decision-board">
    <div className="board-toolbar">
      <div className="board-toolbar-summary">
        <Tag>{board.total} 位候选</Tag>
        <Tag color={board.ranked_count?'success':'default'}>{board.ranked_count} 位可排名</Tag>
        <Typography.Text type="secondary">排名仅用于招聘辅助，适配 / 不适配由企业用户人工决定。</Typography.Text>
      </div>
      <Space>
        <Button icon={<BarChartOutlined/>} disabled={compareItems.length<2||compareItems.length>3} onClick={()=>setCompareOpen(true)}>
          比较候选{compareItems.length?` (${compareItems.length})`:''}
        </Button>
      </Space>
    </div>
    {allUnmatched&&<Alert type="info" title="尚未运行正式匹配" description="候选已投递但还没有正式匹配结果，请先在候选池中运行候选评估。" style={{marginBottom:14}}/>}
    <Table<CandidateBoardItem>
      rowKey="resume_id"
      size="middle"
      columns={columns}
      dataSource={board.items}
      pagination={false}
      tableLayout="fixed"
      rowSelection={{
        selectedRowKeys:compareIds,
        onChange:(keys)=>setCompareIds(keys as string[]),
        getCheckboxProps:(item)=>({disabled:!isCurrentSucceeded(item)||item.rank===null}),
        preserveSelectedRowKeys:true,
      }}
    />

    <Drawer title={selected?`候选详情 · ${selected.candidate_display_name}`:'候选详情'} width={560} open={Boolean(selected)} onClose={()=>setSelected(undefined)} destroyOnHidden>
      {selected&&<CandidateDrawerContent item={selected}/>}
    </Drawer>

    <Drawer title="候选横向比较" width={720} open={compareOpen} onClose={()=>setCompareOpen(false)} destroyOnHidden>
      {compareItems.length?<CompareBoard items={compareItems}/>:<Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="请选择 2～3 位候选进行比较"/>}
    </Drawer>

    <Modal
      title={pendingDecision?.decision==='fit'?'记录适配决策依据':'记录不适配决策依据'}
      open={Boolean(pendingDecision)}
      okText="确认决策"
      cancelText="取消"
      confirmLoading={Boolean(working)}
      onCancel={()=>{setPendingDecision(undefined);setReasonCode('');setReasonText('')}}
      onOk={()=>pendingDecision&&void decide(pendingDecision.item,pendingDecision.decision)}
    >
      <Space direction="vertical" size="middle" style={{width:'100%'}}>
        <Typography.Text type="secondary">依据将与当前评估版本绑定；重新匹配后不会沿用旧依据。</Typography.Text>
        <Select
          aria-label="决策原因"
          placeholder="选择原因（可选）"
          value={reasonCode||undefined}
          onChange={setReasonCode}
          allowClear
          options={[
            {value:'requirements_met',label:'核心要求满足'},
            {value:'experience_aligned',label:'经验匹配'},
            {value:'critical_gap',label:'存在关键缺口'},
            {value:'insufficient_evidence',label:'证据不足'},
            {value:'other',label:'其他'},
          ]}
        />
        <Input.TextArea aria-label="决策说明" value={reasonText} maxLength={2000} showCount rows={4} placeholder="补充人工判断依据（可选）" onChange={event=>setReasonText(event.target.value)}/>
      </Space>
    </Modal>
  </div>;
}

function CandidateDrawerContent({item}:{item:CandidateBoardItem}){
  return <div className="board-drawer">
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="综合得分">{scoreText(isCurrentSucceeded(item)?item.overall_score:null)}</Descriptions.Item>
      <Descriptions.Item label="必备技能覆盖">{coverageText(item.required_coverage)}</Descriptions.Item>
      <Descriptions.Item label="关键缺口">{item.critical_gap_count>0?`${item.critical_gap_count} 项`:'无'}</Descriptions.Item>
      <Descriptions.Item label="证据">{item.evidence?`${item.evidence.count} 条证据`:'—'}</Descriptions.Item>
      <Descriptions.Item label="评估版本">{item.evaluation_status==='succeeded'?'正式匹配已完成':evalStatusCopy[item.evaluation_status]}</Descriptions.Item>
      <Descriptions.Item label="评估状态">
      <Tag color={evalStatusColor(item.evaluation_status)}>{evalStatusCopy[item.evaluation_status]||'状态未知'}</Tag>
      </Descriptions.Item>
      {item.error_code&&<Descriptions.Item label="失败状态">处理失败</Descriptions.Item>}
      <Descriptions.Item label="推荐等级">{item.recommendation_level?recommendationCopy[item.recommendation_level]||'未分类':'—'}</Descriptions.Item>
      {item.decision&&<Descriptions.Item label="人工决策依据">{item.decision.reason_text||(item.decision.reason_code?decisionReasonCopy[item.decision.reason_code]||'其他':'未填写')}</Descriptions.Item>}
    </Descriptions>
    {item.error_message&&<Alert type="error" showIcon title="匹配未完成" description={localizeSystemMessage(item.error_message)} style={{marginTop:12}}/>}
    {isCurrentSucceeded(item)&&item.evaluation_id&&<div className="board-drawer-report-link">
      <Link to={`/enterprise/recruitment/reports/${item.evaluation_id}`}><LinkOutlined/> 查看完整匹配报告</Link>
    </div>}
    {item.evaluation_delta&&<EvaluationDeltaTimeline item={item}/>}
    <Typography.Title level={5}>优势</Typography.Title>
    {item.strengths.length?<List size="small" dataSource={item.strengths} renderItem={strength=>(
      <List.Item className="board-insight-item">
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{strength.message}</Typography.Text>
          <Typography.Text type="secondary" className="board-meta">{dimensionLabel(strength.dimension)} · {strength.evidence_count} 条证据</Typography.Text>
        </Space>
      </List.Item>
    )}/>:<Typography.Text type="secondary">正式报告中无优势结论</Typography.Text>}
    <Typography.Title level={5}>风险</Typography.Title>
    {item.risks.length?<List size="small" dataSource={item.risks} renderItem={risk=>(
      <List.Item className="board-insight-item">
        <Space direction="vertical" size={0}>
          <Typography.Text><Tag color="error">{riskKindCopy[risk.kind]||'其他风险'}</Tag>{risk.message}</Typography.Text>
          {risk.evidence_count>0&&<Typography.Text type="secondary" className="board-meta">{risk.evidence_count} 条证据</Typography.Text>}
        </Space>
      </List.Item>
    )}/>:<Typography.Text type="secondary">正式报告中无风险结论</Typography.Text>}
    <Alert type="warning" showIcon title="适配 / 不适配需人工决定" description="决策板只组织正式匹配结果与关键缺口，不自动录用候选人。" style={{marginTop:16}}/>
    {item.candidate_status==='revoked'&&<Alert type="error" showIcon title="投递已撤销" description="该候选人的投递已被撤销，不能作为正常可决策候选。" style={{marginTop:12}}/>}
  </div>;
}

function signed(value:number|null,suffix=''){
  if(value===null)return '—';
  return `${value>0?'+':''}${value}${suffix}`;
}

function EvaluationDeltaTimeline({item}:{item:CandidateBoardItem}){
  const delta=item.evaluation_delta!;
  return <div className="board-evaluation-delta">
    <Typography.Title level={5}>评估变化</Typography.Title>
    <Descriptions column={1} size="small" bordered>
      <Descriptions.Item label="版本">上一版评估 → 当前评估</Descriptions.Item>
      <Descriptions.Item label="总分">{scoreText(delta.previous.overall_score)} → {scoreText(delta.current.overall_score)}（{signed(delta.overall_score_delta)}）</Descriptions.Item>
      <Descriptions.Item label="必备技能覆盖">{coverageText(delta.previous.required_coverage)} → {coverageText(delta.current.required_coverage)}（{signed(delta.required_coverage_delta===null?null:Math.round(delta.required_coverage_delta*100),'%')}）</Descriptions.Item>
      <Descriptions.Item label="关键缺口">{delta.previous.critical_gap_count} → {delta.current.critical_gap_count}（{signed(delta.critical_gap_count_delta)}）</Descriptions.Item>
      <Descriptions.Item label="评估版本">上一版 → 当前版</Descriptions.Item>
      <Descriptions.Item label="评估时间">{delta.previous.evaluated_at||'—'} → {delta.current.evaluated_at||'—'}</Descriptions.Item>
      <Descriptions.Item label="过期原因变化">{delta.stale_reasons_changed?'有变化':'无变化'}</Descriptions.Item>
    </Descriptions>
  </div>;
}

function CompareBoard({items}:{items:CandidateBoardItem[]}){
  return <div className="compare-board">
    <CompareMatrix items={items}/>
  </div>;
}

function CompareMatrix({items}:{items:CandidateBoardItem[]}){
  const rows=[
    {label:'总分',get:(item:CandidateBoardItem)=>scoreText(item.overall_score)},
    {label:'必备技能覆盖',get:(item:CandidateBoardItem)=>coverageText(item.required_coverage)},
    {label:'关键缺口',get:(item:CandidateBoardItem)=>`${item.critical_gap_count} 项`},
    {label:'证据',get:(item:CandidateBoardItem)=>item.evidence?`${item.evidence.count} 条`:'—'},
  ];
  return <div className="compare-matrix">
    <table>
      <thead><tr><th>指标</th>{items.map(item=><th key={item.resume_id}>{item.candidate_display_name}</th>)}</tr></thead>
      <tbody>
        {rows.map(row=><tr key={row.label}><td>{row.label}</td>{items.map(item=><td key={item.resume_id}>{row.get(item)}</td>)}</tr>)}
        <tr><td>匹配状态</td>{items.map(item=><td key={item.resume_id}><Tag color={evalStatusColor(item.evaluation_status)}>{evalStatusCopy[item.evaluation_status]||'状态未知'}</Tag></td>)}</tr>
      </tbody>
    </table>
    {items.map(item=><div key={item.resume_id} className="compare-detail">
      <Typography.Title level={5}>{item.candidate_display_name}</Typography.Title>
      <Typography.Text strong>优势：</Typography.Text>
      <div>{item.strengths.length?item.strengths.map((strength,index)=><div key={index} className="board-meta">· {strength.message}</div>):'—'}</div>
      <Typography.Text strong>风险：</Typography.Text>
      <div>{item.risks.length?item.risks.map((risk,index)=><div key={index} className="board-meta">· {riskKindCopy[risk.kind]||'其他风险'}：{risk.message}</div>):'—'}</div>
      <Typography.Text strong>缺少必备：</Typography.Text>
      <div>{item.risks.filter(risk=>risk.kind==='missing_required').length
        ?item.risks.filter(risk=>risk.kind==='missing_required').map((risk,index)=><div key={index} className="board-meta">· {risk.message}</div>)
        :'—'}</div>
    </div>)}
  </div>;
}
