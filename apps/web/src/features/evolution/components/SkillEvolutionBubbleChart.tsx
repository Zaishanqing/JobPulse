import {useEffect,useLayoutEffect,useRef} from 'react';
import {
  easeCubicInOut,forceCollide,forceSimulation,interpolateNumber,interpolateRgb,scaleLinear,scalePoint,scaleSqrt,select,
  type Force,type Simulation,type SimulationNodeDatum,
} from 'd3';
import type {EvolutionBubbleDatum,EvolutionBubbleTimeline} from '../lib/bubbleTimeline';

type BubbleFilter={direction:'all'|'growth'|'decline';stack:string;level:string};

type BubbleNode=SimulationNodeDatum&{
  id:string;
  datum?:EvolutionBubbleDatum;
  targetX:number;
  targetY:number;
  targetRadius:number;
  targetColor:string;
  currentTargetX:number;
  currentTargetY:number;
  currentRadius:number;
  currentColor:string;
  currentDelta:number;
  currentOpacity:number;
  visible:boolean;
};

type Props={
  timeline:EvolutionBubbleTimeline;
  frameIndex:number;
  filter:BubbleFilter;
  speed:number;
  onSelect:(datum:EvolutionBubbleDatum)=>void;
};

const palette={growth:'#b94d2e',decline:'#1f883d',stable:'#9b9185'};

function matchesFilter(datum:EvolutionBubbleDatum|undefined,filter:BubbleFilter){
  if(!datum||!datum.comparable)return false;
  if(filter.direction!=='all'&&datum.direction!==filter.direction)return false;
  if(filter.stack&&datum.stack!==filter.stack)return false;
  if(filter.level&&datum.level!==filter.level)return false;
  return true;
}

export function SkillEvolutionBubbleChart({timeline,frameIndex,filter,speed,onSelect}:Props){
  const hostRef=useRef<HTMLDivElement>(null);
  const simulationRef=useRef<Simulation<BubbleNode,undefined>|null>(null);
  const nodesRef=useRef<BubbleNode[]>([]);
  const dimensionsRef=useRef({width:1100,height:620});
  const onSelectRef=useRef(onSelect);
  useEffect(()=>{onSelectRef.current=onSelect},[onSelect]);

  useLayoutEffect(()=>{
    const host=hostRef.current;
    if(!host)return;
    const svg=select(host).append('svg').attr('role','img').attr('aria-label','岗位能力动态泡泡图');
    const root=svg.append('g');
    const backdrop=root.append('g').attr('class','bubble-chart-grid');
    const nodesLayer=root.append('g').attr('class','bubble-chart-nodes');
    const tooltip=select(host).append('div').attr('class','bubble-chart-tooltip').style('opacity','0');
    const tooltipTitle=tooltip.append('strong');
    const tooltipMeta=tooltip.append('span');
    const tooltipValue=tooltip.append('span');
    const tooltipDelta=tooltip.append('span');
    const tooltipEvidence=tooltip.append('span');
    nodesRef.current=timeline.skillIds.map((id,index)=>{
      const x=80+(index%10)*72;
      const y=200+Math.floor(index/10)*44;
      return {id,x,y,targetX:x,targetY:y,targetRadius:0,targetColor:palette.stable,currentTargetX:x,currentTargetY:y,currentRadius:0,currentColor:palette.stable,currentDelta:0,currentOpacity:0,visible:false};
    });
    const followX=((alpha:number)=>nodesRef.current.forEach(node=>{
      node.vx=(node.vx??0)+(node.currentTargetX-(node.x??node.currentTargetX))*.14*alpha;
    })) as Force<BubbleNode,undefined>;
    const followY=((alpha:number)=>nodesRef.current.forEach(node=>{
      node.vy=(node.vy??0)+(node.currentTargetY-(node.y??node.currentTargetY))*.2*alpha;
    })) as Force<BubbleNode,undefined>;
    const collide=forceCollide<BubbleNode>(node=>node.currentRadius+3).iterations(3);
    const simulation=forceSimulation(nodesRef.current)
      .velocityDecay(.3)
      .force('x',followX)
      .force('y',followY)
      .force('collide',collide)
      .alphaDecay(.035)
      .on('tick',()=>{
        const {width,height}=dimensionsRef.current;
        nodesLayer.selectAll<SVGGElement,BubbleNode>('g.bubble-node').attr('transform',node=>{
          const margin=node.currentRadius>0?node.currentRadius+4:0;
          node.x=Math.max(margin,Math.min(width-margin,node.x??node.currentTargetX));
          node.y=Math.max(margin,Math.min(height-margin,node.y??node.currentTargetY));
          return `translate(${node.x},${node.y})`;
        });
      });
    simulationRef.current=simulation;

    const drawFrame=()=>{
      const {width,height}=dimensionsRef.current;
      const baseline=Math.round(height*.53);
      svg.attr('viewBox',`0 0 ${width} ${height}`);
      backdrop.selectAll('*').remove();
      backdrop.append('rect').attr('width',width).attr('height',height).attr('rx',18).attr('class','bubble-chart-background');
      backdrop.append('line').attr('x1',24).attr('x2',width-24).attr('y1',baseline).attr('y2',baseline).attr('class','bubble-zero-line');
      backdrop.append('text').attr('x',width-28).attr('y',baseline-10).attr('text-anchor','end').attr('class','bubble-zero-label').text('变化幅度 = 0');
      backdrop.append('text').attr('x',28).attr('y',58).attr('class','bubble-axis-hint is-growth').text('需求增强');
      backdrop.append('text').attr('x',28).attr('y',height-20).attr('class','bubble-axis-hint is-decline').text('需求减弱');
    };

    const resize=()=>{
      const width=Math.max(720,host.clientWidth||1100);
      const height=Math.max(540,Math.min(680,width*.58));
      dimensionsRef.current={width,height};
      drawFrame();
      simulation.alpha(.7).restart();
    };
    const observer=new ResizeObserver(resize);
    observer.observe(host);resize();

    const nodeSelection=nodesLayer.selectAll<SVGGElement,BubbleNode>('g.bubble-node')
      .data(nodesRef.current,node=>node.id)
      .join(enter=>{
        const group=enter.append('g').attr('class','bubble-node').attr('tabindex',0).attr('role','button').style('opacity',0);
        group.append('circle').attr('r',0).attr('fill',palette.stable);
        group.append('text').attr('class','bubble-name').attr('text-anchor','middle').attr('dy','-.12em');
        group.append('text').attr('class','bubble-delta').attr('text-anchor','middle').attr('dy','1.18em');
        return group;
      })
      .on('mouseenter focus',function(event,node){
        if(!node.datum||!node.visible)return;
        select(this).classed('is-hovered',true);
        tooltipTitle.text(node.datum.name);
        tooltipMeta.text(`${node.datum.stack} · ${node.datum.time}`);
        tooltipValue.text(`重要程度 ${node.datum.value.toFixed(1)}`);
        tooltipDelta.text(`变化 ${node.datum.delta>0?'+':''}${node.datum.delta.toFixed(1)}%`);
        tooltipEvidence.text(`${node.datum.evidenceCount} 条图谱证据`);
        tooltip.style('opacity','1');
        const bounds=host.getBoundingClientRect();
        const pointerEvent=event as MouseEvent;
        const left=pointerEvent.clientX?pointerEvent.clientX-bounds.left+14:(node.x??0)+14;
        const top=pointerEvent.clientY?pointerEvent.clientY-bounds.top+14:(node.y??0)+14;
        tooltip.style('left',`${Math.min(left,bounds.width-230)}px`).style('top',`${Math.max(8,top)}px`);
      })
      .on('mouseleave blur',function(){select(this).classed('is-hovered',false);tooltip.style('opacity','0')})
      .on('click keydown',function(event,node){
        if((event as KeyboardEvent).type==='keydown'&&!['Enter',' '].includes((event as KeyboardEvent).key))return;
        if(node.datum&&node.visible){event.preventDefault();onSelectRef.current(node.datum)}
      });
    nodeSelection.attr('aria-label',node=>node.datum?.name||node.id);

    return ()=>{observer.disconnect();simulation.stop();svg.remove();tooltip.remove()};
  },[timeline.skillIds]);

  useLayoutEffect(()=>{
    const host=hostRef.current;
    const simulation=simulationRef.current;
    if(!host||!simulation)return;
    const {width,height}=dimensionsRef.current;
    const frame=timeline.frames[frameIndex];
    if(!frame)return;
    const currentById=new Map(frame.skills.map(skill=>[skill.id,skill]));
    const visibleSkills=frame.skills.filter(skill=>matchesFilter(skill,filter));
    const stacks=[...new Set(visibleSkills.map(skill=>skill.stack))];
    const stackX=scalePoint<string>().domain(stacks).range([80,width-80]).padding(.55);
    select(host).select<SVGSVGElement>('svg').select<SVGGElement>('g.bubble-chart-grid')
      .selectAll<SVGTextElement,string>('text.bubble-stack-label')
      .data(stacks,value=>value)
      .join(
        enter=>enter.append('text').attr('class','bubble-stack-label').attr('y',30).attr('text-anchor','middle').style('opacity',0).text(value=>value)
          .attr('x',value=>stackX(value)??width/2).call(selection=>selection.transition().duration(320).style('opacity',1)),
        update=>update.text(value=>value).call(selection=>selection.interrupt().transition().duration(320).attr('x',value=>stackX(value)??width/2).style('opacity',1)),
        exit=>exit.call(selection=>selection.interrupt().transition().duration(220).style('opacity',0).remove()),
      );
    const comparableDeltas=timeline.frames.flatMap(item=>item.skills.filter(skill=>skill.comparable).map(skill=>Math.abs(skill.delta))).sort((a,b)=>a-b);
    const radiusCeiling=Math.max(1,comparableDeltas[Math.floor((comparableDeltas.length-1)*.95)]||1);
    const radius=scaleSqrt().domain([0,radiusCeiling]).range([9,54]).clamp(true);
    const maxDelta=Math.max(10,...comparableDeltas);
    const baseline=height*.53;
    const y=scaleLinear().domain([-maxDelta,maxDelta]).range([height-70,62]).clamp(true);
    nodesRef.current.forEach((node,index)=>{
      const datum=currentById.get(node.id);
      const visible=matchesFilter(datum,filter);
      node.datum=datum;
      node.visible=visible;
      node.targetX=visible?(stackX(datum!.stack)??width/2):Math.max(40,Math.min(width-40,node.x??(40+index*11)));
      node.targetY=visible?y(datum!.delta):baseline;
      node.targetRadius=visible?radius(Math.abs(datum!.delta)):0;
      node.targetColor=datum?palette[datum.direction]:palette.stable;
    });
    const duration=Math.round(760/speed);
    const selection=select(host).select<SVGSVGElement>('svg').selectAll<SVGGElement,BubbleNode>('g.bubble-node')
      .attr('aria-label',node=>node.datum?`${node.datum.name}，变化 ${node.datum.delta.toFixed(1)}%`:node.id)
      .classed('is-hidden',node=>!node.visible)
      .style('pointer-events',node=>node.visible?'auto':'none');
    const states=nodesRef.current.map(node=>({
      node,
      radius:interpolateNumber(node.currentRadius,node.targetRadius),
      x:interpolateNumber(node.currentTargetX,node.targetX),
      y:interpolateNumber(node.currentTargetY,node.targetY),
      delta:interpolateNumber(node.currentDelta,node.datum?.delta||0),
      opacity:interpolateNumber(node.currentOpacity,node.visible?1:0),
      color:interpolateRgb(node.currentColor,node.targetColor),
    }));
    const svg=select(host).select<SVGSVGElement>('svg');
    svg.interrupt('timeline').transition('timeline').duration(duration).ease(easeCubicInOut).tween('bubble-state',()=>progress=>{
      states.forEach(state=>{
        state.node.currentRadius=state.radius(progress);
        state.node.currentTargetX=state.x(progress);
        state.node.currentTargetY=state.y(progress);
        state.node.currentDelta=state.delta(progress);
        state.node.currentOpacity=state.opacity(progress);
        state.node.currentColor=state.color(progress);
      });
      selection.style('opacity',node=>String(node.currentOpacity));
      selection.select('circle')
        .attr('r',node=>node.currentRadius)
        .attr('fill',node=>node.currentColor)
        .attr('stroke',node=>node.currentOpacity>.01?'rgba(255,255,255,.72)':'transparent');
      selection.select<SVGTextElement>('text.bubble-name')
        .text(node=>node.currentOpacity>.5&&node.currentRadius>=18?(node.datum?.name||''):'')
        .style('font-size',node=>`${Math.max(10,Math.min(14,node.currentRadius/3.1))}px`);
      selection.select<SVGTextElement>('text.bubble-delta')
        .text(node=>node.currentOpacity>.5&&node.currentRadius>=25?`${node.currentDelta>0?'+':''}${node.currentDelta.toFixed(1)}%`:'');
      const collide=simulation.force('collide') as ReturnType<typeof forceCollide<BubbleNode>>;
      collide.radius(node=>node.currentRadius+3);
      simulation.alpha(Math.max(.16,(1-progress)*.8)).restart();
    }).on('end',()=>{
      nodesRef.current.forEach(node=>{node.visible=node.currentOpacity>.01});
      simulation.alpha(.12).restart();
    });
  },[filter,frameIndex,speed,timeline]);

  return <div className="skill-evolution-bubble-chart" ref={hostRef}/>;
}
