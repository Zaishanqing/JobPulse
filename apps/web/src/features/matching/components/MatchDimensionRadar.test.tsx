import {cleanup,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,test,vi} from 'vitest';
import type {DimensionScore} from '../types';

const {chart,setOption,init}=vi.hoisted(()=>{
  const setOption=vi.fn();
  const chart={setOption,resize:vi.fn(),dispose:vi.fn()};
  return {chart,setOption,init:vi.fn(()=>chart)};
});

vi.mock('echarts',()=>({init}));

import {MatchDimensionRadar} from './MatchDimensionRadar';

const row=(dimension:string,score:number|null,effective_weight=0.2):DimensionScore=>({
  dimension,
  score,
  confidence:.8,
  configured_weight:effective_weight,
  effective_weight,
  applicable_count:4,
  scored_count:score===null?0:4,
  uncertain_count:score===null?1:0,
});

afterEach(()=>{
  cleanup();
  vi.restoreAllMocks();
  setOption.mockClear();
  init.mockClear();
  chart.resize.mockClear();
  chart.dispose.mockClear();
});

describe('MatchDimensionRadar',()=>{
  beforeEach(()=>{
    vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({} as CanvasRenderingContext2D);
  });

  test('keeps formal radar geometry and rounds displayed scores to integers',async()=>{
    render(<MatchDimensionRadar dimensionScores={[
      row('required_skills',82.49),
      row('responsibilities',76.5),
      row('projects',64.51),
      row('capability_level',58.4),
    ]}/>);

    await waitFor(()=>expect(init).toHaveBeenCalledTimes(1));
    const option=setOption.mock.calls[0][0] as {
      radar:{indicator:{name:string;min:number;max:number}[];radius:string;axisName:{formatter:(name:string)=>string}};
      series:{name:string;data:{value:number[];label:{show:boolean}}[]}[];
    };
    expect(option.radar.indicator).toEqual([
      {name:'必备技能',min:-100/3,max:100},
      {name:'岗位职责',min:-100/3,max:100},
      {name:'综合实践',min:-100/3,max:100},
      {name:'能力等级',min:-100/3,max:100},
    ]);
    expect(option.series[0].name).toBe('当前匹配表现');
    expect(option.series[0].data[0].value).toEqual([82.49,76.5,64.51,58.4]);
    expect(option.series[0].data[0].label.show).toBe(false);
    expect(option.radar.radius).toBe('52%');
    expect(option.radar.axisName.formatter('必备技能')).toContain('必备技能');
    expect(option.radar.axisName.formatter('必备技能')).toContain('82分');
    expect(option.radar.axisName.formatter('岗位职责')).toContain('77分');
    expect(option.radar.axisName.formatter('综合实践')).toContain('65分');
    expect(screen.queryByLabelText('维度精确分值')).not.toBeInTheDocument();
    expect(screen.getByRole('img',{name:/必备技能 82 分，岗位职责 77 分，综合实践 65 分/})).toBeInTheDocument();
  });

  test('places a real zero score on the innermost polygon without changing the displayed score',async()=>{
    render(<MatchDimensionRadar dimensionScores={[
      row('required_skills',0),
      row('responsibilities',50),
      row('hard_conditions',100),
    ]}/>);

    await waitFor(()=>expect(init).toHaveBeenCalledTimes(1));
    const option=setOption.mock.calls[0][0] as {
      radar:{splitNumber:number;indicator:Array<{min:number;max:number}>};
      series:Array<{data:Array<{value:number[]}>}>;
    };
    expect(option.radar.splitNumber).toBe(4);
    expect(option.radar.indicator.every(item=>item.min===-100/3&&item.max===100)).toBe(true);
    expect(option.series[0].data[0].value).toEqual([0,50,100]);
    expect(screen.getByRole('img',{name:/必备技能 0 分/})).toBeInTheDocument();
  });

  test('excludes null scores instead of drawing them as zero',async()=>{
    render(<MatchDimensionRadar dimensionScores={[
      row('required_skills',82),
      row('responsibilities',76),
      row('projects',null),
      row('hard_conditions',91),
    ]}/>);

    await waitFor(()=>expect(init).toHaveBeenCalledTimes(1));
    const option=setOption.mock.calls[0][0] as {radar:{indicator:Array<{name:string}>};series:Array<{data:Array<{value:number[]}>}>};
    expect(option.radar.indicator.map(item=>item.name)).toEqual(['必备技能','岗位职责','硬性条件']);
    expect(option.series[0].data[0].value).toEqual([82,76,91]);
    expect(screen.queryByText('综合实践')).not.toBeInTheDocument();
  });

  test('does not add semantic shadow when its effective weight is zero',async()=>{
    render(<MatchDimensionRadar dimensionScores={[
      row('required_skills',82),
      row('responsibilities',76),
      row('projects',64),
      row('semantic',99,0),
    ]}/>);

    await waitFor(()=>expect(init).toHaveBeenCalledTimes(1));
    const option=setOption.mock.calls[0][0] as {radar:{indicator:Array<{name:string}>}};
    expect(option.radar.indicator.map(item=>item.name)).not.toContain('语义补充');
  });

  test('renders with a single applicable dimension and falls back only with none',async()=>{
    render(<MatchDimensionRadar dimensionScores={[row('required_skills',82),row('projects',null),row('semantic',99,0)]}/>);

    await waitFor(()=>expect(init).toHaveBeenCalledTimes(1));
    const option=setOption.mock.calls[0][0] as {radar:{indicator:Array<{name:string}>}};
    expect(option.radar.indicator.map(item=>item.name)).toEqual(['必备技能']);
    expect(screen.queryByText('当前没有可评分的维度，暂不生成雷达图')).not.toBeInTheDocument();
  });
});
