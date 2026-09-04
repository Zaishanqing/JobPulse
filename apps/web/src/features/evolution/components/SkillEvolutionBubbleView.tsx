import {useEffect,useMemo,useState} from 'react';
import {Button,Descriptions,Drawer,Empty,Segmented,Select,Slider,Space,Statistic,Tag,Typography} from 'antd';
import {CaretRightOutlined,PauseOutlined,StepBackwardOutlined,StepForwardOutlined} from '@ant-design/icons';
import type {CapabilityEvolution} from '../types';
import {buildBubbleTimeline,datumAt,type EvolutionBubbleDatum} from '../lib/bubbleTimeline';
import {SkillEvolutionBubbleChart} from './SkillEvolutionBubbleChart';

const levelLabels:Record<string,string>={core:'核心',high:'高频',medium:'常规',low:'低频',bonus:'加分',edge:'边缘'};

export function SkillEvolutionBubbleView({evolution,positionName}:{evolution:CapabilityEvolution;positionName:string}){
  const timeline=useMemo(()=>buildBubbleTimeline(evolution),[evolution]);
  const [frameIndex,setFrameIndex]=useState(Math.max(0,timeline.frames.length-1));
  const [playing,setPlaying]=useState(false);
  const [speed,setSpeed]=useState(1);
  const [direction,setDirection]=useState<'all'|'growth'|'decline'>('all');
  const [stack,setStack]=useState('');
  const [level,setLevel]=useState('');
  const [selected,setSelected]=useState<EvolutionBubbleDatum>();

  useEffect(()=>{
    const timer=window.setTimeout(()=>{setFrameIndex(Math.max(0,timeline.frames.length-1));setPlaying(false)},0);
    return ()=>window.clearTimeout(timer);
  },[timeline.frames.length]);
  useEffect(()=>{
    if(!playing||timeline.frames.length<2)return;
    const timer=window.setInterval(()=>setFrameIndex(current=>{
      if(current>=timeline.frames.length-1){setPlaying(false);return current}
      return current+1;
    }),Math.round(1800/speed));
    return ()=>window.clearInterval(timer);
  },[playing,speed,timeline.frames.length]);

  const frame=timeline.frames[frameIndex];
  const visible=useMemo(()=>frame?.skills.filter(skill=>skill.comparable&&(direction==='all'||skill.direction===direction)&&(!stack||skill.stack===stack)&&(!level||skill.level===level))||[],[direction,frame,level,stack]);
  const bubbleFilter=useMemo(()=>({direction,stack,level}),[direction,level,stack]);
  const fastest=[...visible].sort((a,b)=>b.delta-a.delta)[0];
  const declining=[...visible].sort((a,b)=>a.delta-b.delta)[0];
  const marks=Object.fromEntries(timeline.frames.map((item,index)=>[index,{label:item.label.replace(' 第 ',' Q').replace(' 季度','')}])) as Record<number,{label:string}>;
  const selectedHistory=selected?timeline.frames.map((item,index)=>datumAt(timeline,selected.id,index)).filter(Boolean) as EvolutionBubbleDatum[]:[];

  if(!timeline.frames.length)return <section className="bubble-evolution-empty"><Empty description="暂无已发布图谱版本，发布后即可查看动态泡泡图"/></section>;

  return <section className="bubble-evolution-view">
    <div className="bubble-evolution-head">
      <div><Typography.Title level={4}>{positionName}能力动态演化</Typography.Title><Typography.Text type="secondary">只展示前后版本均已存在、达到动态文档门槛且确实发生变化的能力；首次出现不计入变化，并按支持度最多展示 30 项。</Typography.Text></div>
      <Tag>{frame.label}</Tag>
    </div>
    <div className="bubble-filter-bar">
      <Segmented value={direction} onChange={value=>setDirection(value as typeof direction)} options={[{label:'全部',value:'all'},{label:'增长能力',value:'growth'},{label:'下降能力',value:'decline'}]}/>
      <Space wrap>
        <Select allowClear value={stack||undefined} placeholder="全部技术栈" options={timeline.stacks.map(value=>({value,label:value}))} onChange={value=>setStack(value||'')}/>
        <Select allowClear value={level||undefined} placeholder="全部岗位级别" options={timeline.levels.map(value=>({value,label:levelLabels[value]||value}))} onChange={value=>setLevel(value||'')}/>
      </Space>
    </div>
    <div className="bubble-stat-grid">
      <div><span>增长最快能力</span><Statistic value={fastest?.name||'暂无'} suffix={fastest?`${fastest.delta>=0?'+':''}${fastest.delta.toFixed(1)}%`:''}/></div>
      <div><span>下降最快能力</span><Statistic value={declining&&declining.delta<0?declining.name:'暂无'} suffix={declining&&declining.delta<0?`${declining.delta.toFixed(1)}%`:''}/></div>
      <div><span>最低文档支持</span><Statistic value={frame.supportThreshold} suffix="篇/前后版本"/></div>
      <div><span>当前显示</span><Statistic value={visible.length} suffix="项能力"/></div>
    </div>
    <div className="bubble-legend"><span><i className="is-growth"/>需求增强</span><span><i className="is-decline"/>需求减弱</span><span><i className="is-stable"/>接近稳定</span><em>圆面积直接表示变化幅度</em></div>
    <SkillEvolutionBubbleChart timeline={timeline} frameIndex={frameIndex} filter={bubbleFilter} speed={speed} onSelect={setSelected}/>
    <div className="bubble-playback">
      <div className="bubble-playback-buttons">
        <Button aria-label="上一个时间点" icon={<StepBackwardOutlined/>} disabled={frameIndex===0} onClick={()=>{setPlaying(false);setFrameIndex(value=>Math.max(0,value-1))}}/>
        <Button type="primary" aria-label={playing?'暂停播放':'开始播放'} icon={playing?<PauseOutlined/>:<CaretRightOutlined/>} disabled={timeline.frames.length<2} onClick={()=>setPlaying(value=>!value)}>{playing?'暂停':'播放'}</Button>
        <Button aria-label="下一个时间点" icon={<StepForwardOutlined/>} disabled={frameIndex===timeline.frames.length-1} onClick={()=>{setPlaying(false);setFrameIndex(value=>Math.min(timeline.frames.length-1,value+1))}}/>
        <Select aria-label="播放速度" value={speed} options={[{value:.5,label:'0.5 倍速'},{value:1,label:'1 倍速'},{value:2,label:'2 倍速'}]} onChange={setSpeed}/>
      </div>
      <Slider min={0} max={timeline.frames.length-1} step={1} value={frameIndex} marks={marks} tooltip={{formatter:value=>timeline.frames[value??0]?.label}} onChange={value=>{setPlaying(false);setFrameIndex(value)}}/>
    </div>
    <Drawer title={selected?`${selected.name} · 能力详情`:'能力详情'} open={Boolean(selected)} onClose={()=>setSelected(undefined)} size={460}>
      {selected&&<div className="bubble-detail">
        <Descriptions column={1} size="small" items={[
          {key:'stack',label:'技术栈',children:selected.stack},
          {key:'level',label:'岗位级别',children:levelLabels[selected.level]||selected.level},
          {key:'value',label:'当前重要程度',children:selected.value.toFixed(1)},
          {key:'delta',label:'当前变化',children:`${selected.delta>0?'+':''}${selected.delta.toFixed(1)}%`},
          {key:'evidence',label:'图谱证据',children:`${selected.evidenceCount} 条`},
        ]}/>
        <Typography.Title level={5}>跨时间轨迹</Typography.Title>
        <div className="bubble-history">{selectedHistory.map(item=><div key={item.time}><strong>{item.time}</strong><span>重要程度 {item.value.toFixed(1)}</span><Tag color={item.direction==='growth'?'volcano':item.direction==='decline'?'green':'default'}>{item.delta>0?'+':''}{item.delta.toFixed(1)}%</Tag></div>)}</div>
      </div>}
    </Drawer>
  </section>;
}
