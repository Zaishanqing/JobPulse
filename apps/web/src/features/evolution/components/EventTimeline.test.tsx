import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {beforeEach,describe,expect,test,vi} from 'vitest';
import {EventTimeline} from './EventTimeline';
import type {EvolutionEvent,EvolutionGraphVersion,EvolutionVersionPair} from '../types';

const relation=(skillId:string,name:string,weight:number)=>({
  skill_id:skillId,canonical_name:name,category_code:'ML',weight,confidence:.85,
  importance_level:'core',primary_modality:'required',
  statistics:{support_document_count:4,source_diversity:2,enterprise_coverage:1},
});

const event=(partial:Partial<EvolutionEvent>):EvolutionEvent=>({
  event_id:'evt-1-2-skill_emergence-001',
  event_type:'skill_emergence',
  position_id:'POS_AI',
  from_version:1,
  to_version:2,
  source_entities:[],
  target_entities:[relation('PY','Python',.55)],
  confidence:.91,
  magnitude:.74,
  evidence:{
    lineage:{position_id:'POS_AI',from_version_id:1,to_version_id:2},
    source_relations:[],
    target_relations:[relation('PY','Python',.55)],
    source:'graph_version_snapshot_diff',
  },
  reason:'skill Python (PY) emerged: weight 0.4 -> 0.55',
  detector_version:'position-evolution-events-v1',
  created_at:'2026-05-12T00:00:00Z',
  metrics:{before_weight:.4,after_weight:.55,delta:.15},
  metadata:{atomic_signals:['skill_emergence']},
  ...partial,
});

const versions:EvolutionGraphVersion[]=[
  {id:1,version_number:1,version_name:'初始图谱',build_run_id:1,release_id:null,rollback_from_version_id:null,created_at:'2026-01-10T00:00:00Z'},
  {id:2,version_number:2,version_name:'图谱 v2',build_run_id:2,release_id:null,rollback_from_version_id:null,is_current:true,created_at:'2026-05-12T00:00:00Z'},
];
const pairs:EvolutionVersionPair[]=[{from_version_id:1,to_version_id:2}];

const renderTimeline=(overrides:Partial<React.ComponentProps<typeof EventTimeline>>={})=>{
  const props:React.ComponentProps<typeof EventTimeline>={
    positionName:'AI 工程师',
    versions,
    versionPairs:pairs,
    events:[event({})],
    loading:false,
    onRetry:vi.fn(),
    ...overrides,
  };
  return render(<EventTimeline {...props}/>);
};

beforeEach(()=>cleanup());

describe('EventTimeline',()=>{
  test('正常时间线：类型中文、主体、置信度、变化强度、版本对与 Overview',()=>{
    renderTimeline();
    expect(screen.getByText('新技能出现')).toBeInTheDocument();
    expect(screen.getByText('Python')).toBeInTheDocument();
    expect(screen.getByText(/置信度/)).toBeInTheDocument();
    expect(screen.getByText('91%')).toBeInTheDocument();
    expect(screen.getByText('74%')).toBeInTheDocument();
    expect(screen.getByText('V1 → V2')).toBeInTheDocument();
    expect(screen.getByText('事件总数')).toBeInTheDocument();
    expect(screen.getByText('2026-05')).toBeInTheDocument();
  });

  test('多事件按时间顺序展示',()=>{
    renderTimeline({events:[
      event({event_id:'later',created_at:'2026-07-01T00:00:00Z',event_type:'skill_decline',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[],metrics:{before_weight:.6,after_weight:.2,delta:-.4}}),
      event({event_id:'early',created_at:'2026-03-01T00:00:00Z',event_type:'skill_replacement',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[relation('PT','PyTorch',.5)]}),
    ]});
    // 第一个事件卡片应为 3 月的 skill_replacement（TensorFlow → PyTorch）
    const cards=document.querySelectorAll('.event-timeline-item');
    expect(cards[0]).toHaveTextContent('TensorFlow → PyTorch');
    expect(cards[1]).toHaveTextContent('TensorFlow');
    expect(cards[0]).toHaveTextContent('2026-03');
    expect(cards[1]).toHaveTextContent('2026-07');
  });

  test('未知 event_type 不崩溃且不暴露内部英文枚举',()=>{
    renderTimeline({events:[event({event_type:'future_unknown_event',event_id:'unknown',reason:'future change',metrics:{}})]});
    expect(screen.getByText('其他能力变化')).toBeInTheDocument();
    expect(screen.getByText('能力结构发生变化')).toBeInTheDocument();
  });

  test('点击事件打开 Drawer：版本血缘与分析说明',async()=>{
    renderTimeline({events:[event({})]});
    fireEvent.click(screen.getByRole('button',{name:/查看证据/}));
    expect(await screen.findByText(/对比前版本 V1/)).toBeInTheDocument();
    expect(screen.getByText(/对比后版本 V2/)).toBeInTheDocument();
    expect(screen.getByText(/初始图谱/)).toBeInTheDocument();
    expect(screen.getByText(/图谱 v2/)).toBeInTheDocument();
    expect(screen.getAllByText('版本血缘').length).toBeGreaterThan(0);
    expect(screen.getByText('系统已记录')).toBeInTheDocument();
    expect(screen.queryByText('证据',{exact:true})).not.toBeInTheDocument();
    expect(screen.queryByText(/引文级证据/)).not.toBeInTheDocument();
    expect(screen.getAllByText('置信度').length).toBeGreaterThan(0);
    expect(screen.getAllByText('91%').length).toBeGreaterThan(0);
  });

  test('版本血缘将内部岗位编码替换为中文岗位名称',async()=>{
    renderTimeline({
      positionName:'大模型算法工程师',
      versions:[
        {...versions[0],version_name:'LLM_ALGORITHM_ENGINEER-2026-bundle-1'},
        {...versions[1],version_name:'LLM_ALGORITHM_ENGINEER-2026-bundle-2'},
      ],
    });
    fireEvent.click(screen.getByRole('button',{name:/查看证据/}));
    expect(await screen.findByText(/大模型算法工程师-2026-bundle-1/)).toBeInTheDocument();
    expect(screen.getByText(/大模型算法工程师-2026-bundle-2/)).toBeInTheDocument();
    expect(screen.queryByText(/LLM_ALGORITHM_ENGINEER/)).not.toBeInTheDocument();
  });

  test('event list 为空时区分“没有事件”而不是 API 错误',()=>{
    renderTimeline({events:[]});
    expect(screen.getByText('当前岗位尚未检测到能力变化记录。')).toBeInTheDocument();
    expect(screen.queryByText('能力变化记录暂时无法加载。')).not.toBeInTheDocument();
  });

  test('只有一个 GraphVersion 时提示版本不足',()=>{
    renderTimeline({versions:[versions[0]],versionPairs:[],events:[]});
    expect(screen.getByText('能力变化至少需要 2 个已发布图谱版本；当前只有 1 个。岗位构建次数不等于已发布版本数。')).toBeInTheDocument();
  });

  test('API error 展示独立错误态并可重试',()=>{
    const onRetry=vi.fn();
    renderTimeline({events:[],error:{status:503,message:'上游服务不可用'} as never,onRetry});
    expect(screen.getByText('能力变化记录暂时无法加载。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'重新加载'}));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  test('筛选控件渲染：事件类型与图谱版本对',()=>{
    renderTimeline({versionPairs:[...pairs,{from_version_id:2,to_version_id:3}]});
    expect(screen.getAllByRole('combobox').length).toBeGreaterThanOrEqual(2);
    // 筛选是纯前端过滤，过滤逻辑在 eventFormat.filterEvents 中单独覆盖
    expect(screen.getByText('1 / 1 个事件')).toBeInTheDocument();
  });

  test('强信号与需谨慎解释标记',()=>{
    renderTimeline({events:[
      event({event_id:'strong',magnitude:.8,confidence:.95}),
      event({event_id:'caution',event_type:'skill_decline',magnitude:.5,confidence:.4,source_entities:[relation('TF','TensorFlow',.2)],target_entities:[],metrics:{before_weight:.6,after_weight:.2,delta:-.4}}),
    ]});
    expect(screen.getByText('强信号')).toBeInTheDocument();
    expect(screen.getByText('需谨慎解释')).toBeInTheDocument();
  });

  test('Overview 统计覆盖新增/衰退/替代/职责变化',()=>{
    renderTimeline({events:[
      event({event_id:'a',event_type:'skill_emergence'}),
      event({event_id:'b',event_type:'role_expansion',source_entities:[],target_entities:[],metrics:{breadth_score:.4}}),
      event({event_id:'c',event_type:'skill_replacement',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[relation('PT','PyTorch',.5)]}),
      event({event_id:'d',event_type:'skill_decline',source_entities:[relation('TF','TensorFlow',.2)],target_entities:[],metrics:{before_weight:.6,after_weight:.2,delta:-.4}}),
      event({event_id:'e',event_type:'responsibility_shift',source_entities:['旧职责'],target_entities:['新职责'],metrics:{removed_count:1,added_count:1,similarity:.4}}),
    ]});
    const overview=screen.getByText('事件总数').parentElement!;
    expect(within(overview).getByText('5')).toBeInTheDocument();
    expect(screen.getByText('新增 / 扩张')).toBeInTheDocument();
    expect(screen.getByText('替代 / 迁移')).toBeInTheDocument();
    expect(screen.getByText('衰退 / 收缩')).toBeInTheDocument();
    expect(screen.getByText('职责 / 名称')).toBeInTheDocument();
    expect(screen.getByText('涉及版本')).toBeInTheDocument();
  });
});
