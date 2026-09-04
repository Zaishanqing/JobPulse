import {useEffect,useMemo,useRef} from 'react';
import * as echarts from 'echarts';
import type {EChartsOption} from 'echarts';
import type {DimensionScore} from '../types';
import {dimensionLabel} from './dimensionLabels';

type MatchDimensionRadarProps={
  dimensionScores:DimensionScore[];
  compareScores?:DimensionScore[];
  baselineSeriesName?:string;
  compareSeriesName?:string;
};

const radarSplitNumber=4;
const radarVisualMinimum=-100/(radarSplitNumber-1);

const percentageValue=(value:number)=>{
  const normalized=value<=1?value*100:value;
  return Math.round(normalized);
};

const percentageText=(value:number|null|undefined)=>value===null||value===undefined?'暂未评分':`${percentageValue(value)}%`;
const scoreText=(value:number|null)=>value===null?'未评估':String(Math.round(value));

const valueFromTooltip=(params:unknown)=>{
  const item=Array.isArray(params)?params[0]:params;
  if(!item||typeof item!=='object')return undefined;
  const name=(item as {name?:unknown}).name;
  return typeof name==='string'?name:undefined;
};

function tooltipFor(rows:DimensionScore[],params:unknown){
  const name=valueFromTooltip(params);
  const row=rows.find(item=>dimensionLabel(item.dimension)===name||item.dimension===name);
  if(!row)return '';
  return [
    `<strong>${dimensionLabel(row.dimension)}</strong>`,
    `得分：${scoreText(row.score)} / 100`,
    `有效权重：${percentageText(row.effective_weight)}`,
    `置信度：${percentageText(row.confidence)}`,
    `已评分：${row.scored_count} / ${row.applicable_count}`,
    `待确认：${row.uncertain_count}`,
  ].join('<br/>');
}

export function MatchDimensionRadar({dimensionScores,compareScores,baselineSeriesName='当前匹配表现',compareSeriesName='假设分析后'}:MatchDimensionRadarProps){
  const hostRef=useRef<HTMLDivElement>(null);
  const canvasSupported=useMemo(()=>{
    try{
      return Boolean(document.createElement('canvas').getContext?.('2d'));
    }catch{
      return false;
    }
  },[]);
  const formalRows=useMemo(
    ()=>dimensionScores.filter(item=>item.dimension!=='semantic'||item.effective_weight>0),
    [dimensionScores],
  );
  const compareByName=useMemo(
    ()=>new Map((compareScores||[]).map(item=>[item.dimension,item])),
    [compareScores],
  );
  const hasCompare=Boolean(compareScores&&compareScores.length>0);
  const applicableRows=useMemo(
    ()=>formalRows.filter(item=>{
      if(item.score===null||item.applicable_count<=0)return false;
      const compare=compareByName.get(item.dimension);
      if(hasCompare&&(!compare||compare.score===null||compare.applicable_count<=0))return false;
      return true;
    }),
    [compareByName,formalRows,hasCompare],
  );
  const comparison=hasCompare&&applicableRows.length>0;
  const accessibleLabel=applicableRows.length
    ?`多维匹配画像：${applicableRows.map(item=>{
      const compare=compareByName.get(item.dimension);
      const base=`${dimensionLabel(item.dimension)} ${scoreText(item.score)} 分`;
      return compare?`${base} → 假设分析后 ${scoreText(compare.score)}（${signedDeltaText(compare.score,item.score)}）分`:`${base}`;
    }).join('，')}`
    :'当前没有可评分的维度，暂不生成雷达图';

  useEffect(()=>{
    const host=hostRef.current;
    if(!host||applicableRows.length===0||!canvasSupported)return;

    const rootStyle=getComputedStyle(document.documentElement);
    const token=(name:string,fallback:string)=>rootStyle.getPropertyValue(name).trim()||fallback;
    const terracotta=token('--terracotta','#B94D2E');
    const terracottaHover=token('--terracotta-hover','#A94429');
    const border=token('--border','#DED7CC');
    const borderSoft=token('--border-soft','#ECE6DC');
    const textSoft=token('--text-soft','#514F49');
    const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const chart=echarts.init(host);
    const scoreByLabel=new Map(applicableRows.map(item=>{
      const compare=compareByName.get(item.dimension);
      return [
        dimensionLabel(item.dimension),
        compare
          ?`${scoreText(compare.score)}（${signedDeltaText(compare.score,item.score)}）分`
          :`${scoreText(item.score)}分`,
      ];
    }));
    const option:EChartsOption={
      animation:!reduced,
      animationDuration:reduced?0:260,
      tooltip:{
        trigger:'item',
        confine:true,
        formatter:(params:unknown)=>comparison
          ?comparisonTooltipFor(applicableRows,compareByName,params,baselineSeriesName,compareSeriesName)
          :tooltipFor(applicableRows,params),
      },
      legend:comparison?{
        bottom:0,
        left:'center',
        itemWidth:14,
        itemHeight:8,
        textStyle:{color:textSoft,fontSize:12},
        data:[baselineSeriesName,compareSeriesName],
      }:undefined,
      radar:{
        center:['50%',comparison?'44%':'47%'],
        radius:comparison?'48%':'52%',
        shape:'polygon',
        splitNumber:radarSplitNumber,
        // 评分仍使用真实的 0–100；扩展不可见的坐标下界，让 0 分落在最内层多边形顶点而非中心。
        indicator:applicableRows.map(item=>({name:dimensionLabel(item.dimension),min:radarVisualMinimum,max:100})),
        axisNameGap:16,
        axisName:{
          color:textSoft,
          fontSize:13,
          lineHeight:18,
          formatter:(name?:string)=>{
            if(!name)return '';
            return `{dimension|${name}}\n{score|${scoreByLabel.get(name)??'未评估分'}}`;
          },
          rich:{
            dimension:{color:textSoft,fontSize:13,fontWeight:500,lineHeight:18},
            score:{color:terracotta,fontSize:12,fontWeight:700,lineHeight:18},
          },
        },
        axisLine:{lineStyle:{color:border}},
        splitLine:{lineStyle:{color:borderSoft}},
        splitArea:{areaStyle:{color:['transparent','transparent','transparent','transparent']}},
      },
      series:comparison?[
        {
          name:baselineSeriesName,
          type:'radar',
          symbol:'circle',
          symbolSize:5,
          data:[{
            value:applicableRows.map(item=>item.score as number),
            name:baselineSeriesName,
            label:{show:false},
          }],
          // 基线雷达：与匹配报告正式雷达同色（陶土色实线，稍浓）。
          lineStyle:{color:terracottaHover,width:2.5,type:'solid'},
          itemStyle:{color:terracotta},
          areaStyle:{color:terracotta,opacity:.18},
        },
        {
          name:compareSeriesName,
          type:'radar',
          symbol:'circle',
          symbolSize:6,
          data:[{
            value:applicableRows.map(item=>compareByName.get(item.dimension)?.score as number),
            name:compareSeriesName,
            label:{show:false},
          }],
          // 假设分析后雷达：与基线同粗细、颜色略浓，仍略淡于基线（基线为深陶土色）。
          lineStyle:{color:terracotta,width:2.5,type:'dashed'},
          itemStyle:{color:terracotta},
          areaStyle:{color:terracotta,opacity:.1},
        },
      ]:[{
        name:baselineSeriesName,
        type:'radar',
        symbol:'circle',
        symbolSize:6,
        data:[{
          value:applicableRows.map(item=>item.score as number),
          name:baselineSeriesName,
          label:{show:false},
        }],
        lineStyle:{color:terracotta,width:2},
        itemStyle:{color:terracotta},
        areaStyle:{color:terracotta,opacity:.16},
      }],
    };
    chart.setOption(option);
    const resize=()=>{
      chart.resize();
    };
    const observer=new ResizeObserver(resize);
    observer.observe(host);
    window.addEventListener('resize',resize);
    resize();
    return()=>{
      observer.disconnect();
      window.removeEventListener('resize',resize);
      chart.dispose();
    };
  },[applicableRows,canvasSupported,compareByName,comparison,baselineSeriesName,compareSeriesName]);

  if(applicableRows.length===0||!canvasSupported){
    return <div className="match-dimension-radar" role="img" aria-label={accessibleLabel}>
      <div className="match-radar-fallback">
        {applicableRows.length===0&&<div className="match-radar-fallback-message">当前没有可评分的维度，暂不生成雷达图</div>}
        {applicableRows.length?<div className="match-dimension-score-list" aria-label="维度评分列表">
          {applicableRows.map(row=><div key={row.dimension}>
            <span>{dimensionLabel(row.dimension)}</span>
            <b>{scoreText(row.score)}</b>
            <small>权重 {percentageText(row.effective_weight)} · 置信度 {percentageText(row.confidence)} · 已评分 {row.scored_count}/{row.applicable_count} · 待确认 {row.uncertain_count}</small>
          </div>)}
        </div>:<div className="match-radar-no-data">服务未返回可用维度评分</div>}
      </div>
    </div>;
  }

  return <div className="match-dimension-radar" role="img" aria-label={accessibleLabel}>
    <div ref={hostRef} className="match-dimension-radar-canvas" aria-hidden="true"/>
    <div className="match-dimension-radar-accessible">{accessibleLabel}</div>
  </div>;
}

const signedDeltaText=(scenario:number|null|undefined,baseline:number|null|undefined)=>{
  if(scenario===null||scenario===undefined||baseline===null||baseline===undefined)return '待测';
  const delta=Math.round(scenario)-Math.round(baseline);
  return delta>0?`+${delta}`:String(delta);
};

function comparisonTooltipFor(rows:DimensionScore[],compareByName:Map<string,DimensionScore>,params:unknown,baselineName:string,scenarioName:string){
  const name=valueFromTooltip(params);
  const row=rows.find(item=>dimensionLabel(item.dimension)===name||item.dimension===name);
  if(!row)return '';
  const compare=compareByName.get(row.dimension);
  if(!compare)return `<strong>${dimensionLabel(row.dimension)}</strong><br/>得分：${scoreText(row.score)} / 100`;
  return [
    `<strong>${dimensionLabel(row.dimension)}</strong>`,
    `${baselineName}：${scoreText(row.score)} / 100`,
    `${scenarioName}：${scoreText(compare.score)}（${signedDeltaText(compare.score,row.score)}）分`,
    `有效权重：${percentageText(compare.effective_weight)}`,
    `置信度：${percentageText(compare.confidence)}`,
    `已评分：${compare.scored_count} / ${compare.applicable_count}`,
  ].join('<br/>');
}
