import {ArrowLeftOutlined} from '@ant-design/icons';
import {Button} from 'antd';
import {useCallback,useEffect,useMemo,useRef,useState} from 'react';
import ReferenceGraphView,{type GraphViewHandle} from './rspressGraph/GraphView';
import {
  buildDirectSkillGraphData,
  buildExpandedReferenceGraphLayer,
  buildReferenceGraphData,
  buildReferenceGraphLayer,
  type ReferenceGraphData,
  type GraphRelation,
} from './graphTransform';

export type GraphViewMode='skills'|'hierarchy'|'explore';

const SKILL_PANORAMA_POSITION_NODE_RADIUS_MULTIPLIER=1.4;

type GraphViewProps={
  position:string;
  positionName:string;
  relations:GraphRelation[];
  viewMode?:GraphViewMode;
  onSelect?:(skillId:string)=>void;
};

type NodePosition={id:string;x:number;y:number};
type PreviewNode={id:string;x:number;y:number;positions:NodePosition[]};

function stableSeedAngle(nodeId:string){
  let hash=0;
  for(const character of nodeId)hash=(hash*31+character.charCodeAt(0))|0;
  return ((hash>>>0)%360)*Math.PI/180;
}

export function GraphView({position,positionName,relations,viewMode='explore',onSelect}:GraphViewProps){
  const ref=useRef<HTMLDivElement>(null);
  const referenceRef=useRef<GraphViewHandle>(null);
  const transitionTimerRef=useRef<number|null>(null);
  const transitionFrameRef=useRef<number|null>(null);
  const previewTimerRef=useRef<number|null>(null);
  const collapseFrameRef=useRef<number|null>(null);
  const expandFrameRef=useRef<number|null>(null);
  const [size,setSize]=useState({width:0,height:0});
  const [navigation,setNavigation]=useState({position,viewMode,path:[position]});
  const [previewPath,setPreviewPath]=useState<PreviewNode[]>([]);
  const [restoredSnapshot,setRestoredSnapshot]=useState<{parentId:string;positions:NodePosition[]}|null>(null);
  const [collapseProgress,setCollapseProgress]=useState<number|null>(null);
  const [expandProgress,setExpandProgress]=useState(1);
  const lastPointerMoveAtRef=useRef(0);
  const previewChangedAtRef=useRef(0);
  const [transitioning,setTransitioning]=useState(false);

  const referenceData=useMemo<ReferenceGraphData>(
    ()=>buildReferenceGraphData(position,positionName,relations),
    [position,positionName,relations],
  );
  const directSkillData=useMemo(
    ()=>buildDirectSkillGraphData(position,positionName,relations),
    [position,positionName,relations],
  );
  const navigationPath=useMemo(()=>{
    const storedPath=navigation.position===position&&navigation.viewMode===viewMode&&viewMode==='explore'
      ?navigation.path
      :[position];
    const requestedNodeId=storedPath.at(-1)??position;
    return referenceData.nodes.some(node=>node.id===requestedNodeId)?storedPath:[position];
  },[navigation,position,referenceData.nodes,viewMode]);
  const currentNodeId=navigationPath.at(-1)??position;
  const previewNode=previewPath.at(-1);
  const previewNodeId=previewNode?.id;
  const previewBaseParentId=previewPath.length>1?previewPath.at(-2)!.id:currentNodeId;
  const graphRouteNodeId=previewNodeId?previewBaseParentId:currentNodeId;
  const graphData=useMemo<ReferenceGraphData>(()=>{
    if(viewMode==='skills')return directSkillData;
    if(viewMode==='hierarchy'){
      const parentIds=new Set(referenceData.links.map(link=>link.source));
      const levelByNode=new Map(referenceData.links.map(link=>[link.target,link.level??1]));
      const childrenByNode=new Map<string,string[]>();
      for(const link of referenceData.links){
        childrenByNode.set(link.source,[...(childrenByNode.get(link.source)??[]),link.target]);
      }
      const depthByNode=new Map<string,number>();
      const subtreeDepth=(nodeId:string):number=>{
        const cached=depthByNode.get(nodeId);
        if(cached!==undefined)return cached;
        const children=childrenByNode.get(nodeId)??[];
        const depth=children.length?1+Math.max(...children.map(subtreeDepth)):0;
        depthByNode.set(nodeId,depth);
        return depth;
      };
      return {
        ...referenceData,
        nodes:referenceData.nodes.map(node=>({
          ...node,
          hasChildren:parentIds.has(node.id),
          hierarchyLevel:levelByNode.get(node.id)??0,
          subtreeDepth:subtreeDepth(node.id),
          ...(node.id===position?{x:0,y:0}:{}),
        })),
        showAll:true,
      };
    }
    const parentIds=new Set(referenceData.links.map(link=>link.source));
    if(previewNode){
      const base=buildReferenceGraphLayer(referenceData,previewBaseParentId);
      const expanded=buildExpandedReferenceGraphLayer(referenceData,previewBaseParentId,previewNode.id);
      const baseIds=new Set(base.nodes.map(node=>node.id));
      const positions=new Map(previewNode.positions.map(item=>[item.id,item]));
      const revealedNodes=expanded.nodes.filter(node=>!baseIds.has(node.id));
      const revealedIndex=new Map(revealedNodes.map((node,index)=>[node.id,index]));
      const seedRotation=stableSeedAngle(previewNode.id);
      return {
        ...expanded,
        nodes:expanded.nodes.map(node=>{
          const captured=positions.get(node.id);
          if(baseIds.has(node.id)){
            const x=node.id===previewNode.id?previewNode.x:captured?.x;
            const y=node.id===previewNode.id?previewNode.y:captured?.y;
            return {
              ...node,
              hasChildren:parentIds.has(node.id),
              layoutInactive:node.id!==previewNode.id,
              x,
              y,
              fx:x,
              fy:y,
            };
          }
          const index=revealedIndex.get(node.id)??0;
          const angle=seedRotation+Math.PI*2*index/Math.max(1,revealedNodes.length);
          return {
            ...node,
            hasChildren:parentIds.has(node.id),
            layoutRevealed:true,
            x:previewNode.x+Math.cos(angle)*30,
            y:previewNode.y+Math.sin(angle)*30,
          };
        }),
        links:expanded.links.map(link=>({
          ...link,
          layoutInactive:link.source!==previewNode.id,
        })),
        showAll:true,
      };
    }
    const layer=buildReferenceGraphLayer(referenceData,currentNodeId);
    const restored=restoredSnapshot?.parentId===currentNodeId
      ?new Map(restoredSnapshot.positions.map(item=>[item.id,item]))
      :new Map<string,NodePosition>();
    return {
      ...layer,
      nodes:layer.nodes.map(node=>{
        const captured=restored.get(node.id);
        if(captured)return {
          ...node,
          hasChildren:parentIds.has(node.id),
          x:captured.x,
          y:captured.y,
          fx:captured.x,
          fy:captured.y,
        };
        if(node.id===currentNodeId)return {...node,hasChildren:true,x:0,y:0,fx:0,fy:0};
        return {...node,hasChildren:parentIds.has(node.id)};
      }),
    };
  },[currentNodeId,directSkillData,position,previewBaseParentId,previewNode,referenceData,restoredSnapshot,viewMode]);

  const transitionTo=useCallback((nextPath:string[])=>{
    if(transitioning)return;
    setTransitioning(true);
    if(collapseFrameRef.current!==null)window.cancelAnimationFrame(collapseFrameRef.current);
    if(expandFrameRef.current!==null)window.cancelAnimationFrame(expandFrameRef.current);
    setCollapseProgress(null);
    setExpandProgress(1);
    setRestoredSnapshot(null);
    setPreviewPath([]);
    transitionTimerRef.current=window.setTimeout(()=>{
      setNavigation({position,viewMode,path:nextPath});
      transitionTimerRef.current=window.setTimeout(()=>{
        transitionFrameRef.current=window.requestAnimationFrame(()=>{
          transitionFrameRef.current=window.requestAnimationFrame(()=>setTransitioning(false));
        });
      },160);
    },220);
  },[position,transitioning,viewMode]);

  const handleNodeClick=useCallback((routePath:string)=>{
    if(!routePath||routePath===currentNodeId)return;
    const node=referenceData.nodes.find(item=>item.id===routePath);
    if(!node)return;
    if(node.nodeKind==='skill'){
      onSelect?.(node.skillId!);
      return;
    }
    if(viewMode==='explore'&&referenceData.links.some(link=>link.source===node.id)){
      const previewIds=previewPath.map(item=>item.id);
      const nextPath=previewNodeId===node.id
        ?[...navigationPath,...previewIds]
        :[...navigationPath,...previewIds,node.id];
      transitionTo(nextPath);
    }
  },[currentNodeId,navigationPath,onSelect,previewNodeId,previewPath,referenceData,transitionTo,viewMode]);

  const cancelCollapse=useCallback(()=>{
    if(collapseFrameRef.current!==null)window.cancelAnimationFrame(collapseFrameRef.current);
    collapseFrameRef.current=null;
    setCollapseProgress(null);
  },[setCollapseProgress]);

  const startExpand=useCallback(()=>{
    if(expandFrameRef.current!==null)window.cancelAnimationFrame(expandFrameRef.current);
    const startedAt=performance.now();
    const duration=360;
    setExpandProgress(0);
    const animate=(now:number)=>{
      const progress=Math.min(1,(now-startedAt)/duration);
      setExpandProgress(progress);
      if(progress<1){
        expandFrameRef.current=window.requestAnimationFrame(animate);
        return;
      }
      expandFrameRef.current=null;
    };
    expandFrameRef.current=window.requestAnimationFrame(animate);
  },[setExpandProgress]);

  const collapsePreview=useCallback(()=>{
    if(!previewPath.length||collapseProgress!==null)return;
    if(expandFrameRef.current!==null)window.cancelAnimationFrame(expandFrameRef.current);
    const basePositions=previewPath[0]?.positions;
    const startedAt=performance.now();
    const duration=360;
    setCollapseProgress(0);
    const animate=(now:number)=>{
      const progress=Math.min(1,(now-startedAt)/duration);
      setCollapseProgress(progress);
      if(progress<1){
        collapseFrameRef.current=window.requestAnimationFrame(animate);
        return;
      }
      collapseFrameRef.current=null;
      if(basePositions)setRestoredSnapshot({parentId:currentNodeId,positions:basePositions});
      setPreviewPath([]);
      setCollapseProgress(null);
      setExpandProgress(1);
    };
    collapseFrameRef.current=window.requestAnimationFrame(animate);
  },[collapseProgress,currentNodeId,previewPath,setCollapseProgress,setExpandProgress,setPreviewPath,setRestoredSnapshot]);

  const handleNodeHoverIdChange=useCallback((
    nodeId:string|null,
    x:number,
    y:number,
    positions:NodePosition[],
  )=>{
    if(viewMode!=='explore')return;
    if(previewTimerRef.current!==null)window.clearTimeout(previewTimerRef.current);
    if(nodeId){
      if(collapseProgress!==null){
        const isRevealedChild=Boolean(previewNodeId)&&referenceData.links.some(
          link=>link.source===previewNodeId&&link.target===nodeId,
        );
        if(nodeId===previewNodeId||isRevealedChild)cancelCollapse();
        else return;
      }
      if(previewPath.some(node=>node.id===nodeId))return;
      const activeParentId=previewNodeId??currentNodeId;
      const isNestedChild=referenceData.links.some(
        link=>link.source===activeParentId&&link.target===nodeId,
      );
      const isSibling=Boolean(previewNodeId)&&referenceData.links.some(
        link=>link.source===previewBaseParentId&&link.target===nodeId,
      );
      const parentIndex=isNestedChild
        ?previewPath.length
        :isSibling
          ?previewPath.length-1
          :-1;
      const hasChildren=referenceData.links.some(link=>link.source===nodeId);
      if(parentIndex>=0&&hasChildren){
        previewTimerRef.current=window.setTimeout(()=>{
          previewChangedAtRef.current=performance.now();
          startExpand();
          setPreviewPath(current=>[
            ...current.slice(0,parentIndex),
            {id:nodeId,x,y,positions},
          ]);
        },90);
      }
      return;
    }
    if(previewPath.length&&lastPointerMoveAtRef.current>previewChangedAtRef.current){
      previewTimerRef.current=window.setTimeout(collapsePreview,700);
    }
  },[cancelCollapse,collapsePreview,collapseProgress,currentNodeId,previewBaseParentId,previewNodeId,previewPath,referenceData.links,setPreviewPath,startExpand,viewMode]);

  const clearPreview=useCallback(()=>{
    if(previewTimerRef.current!==null)window.clearTimeout(previewTimerRef.current);
    collapsePreview();
  },[collapsePreview]);

  const handleBack=useCallback(()=>{
    if(navigationPath.length>1)transitionTo(navigationPath.slice(0,-1));
  },[navigationPath,transitionTo]);

  useEffect(()=>()=>{
    if(transitionTimerRef.current!==null)window.clearTimeout(transitionTimerRef.current);
    if(transitionFrameRef.current!==null)window.cancelAnimationFrame(transitionFrameRef.current);
    if(previewTimerRef.current!==null)window.clearTimeout(previewTimerRef.current);
    if(collapseFrameRef.current!==null)window.cancelAnimationFrame(collapseFrameRef.current);
    if(expandFrameRef.current!==null)window.cancelAnimationFrame(expandFrameRef.current);
  },[]);

  useEffect(()=>{
    if(viewMode==='explore')return;
    const timer=window.setTimeout(()=>referenceRef.current?.zoomToFit(),800);
    return()=>window.clearTimeout(timer);
  },[currentNodeId,directSkillData,referenceData,viewMode]);

  useEffect(()=>{
    if(size.width<=1||size.height<=1)return;
    if(viewMode==='explore')return;
    const timer=window.setTimeout(()=>referenceRef.current?.zoomToFit(),140);
    return()=>window.clearTimeout(timer);
  },[currentNodeId,size.height,size.width,viewMode]);

  useEffect(()=>{
    const container=ref.current;
    if(!container)return;
    const update=()=>{
      const next={width:container.clientWidth,height:container.clientHeight};
      setSize(current=>current.width===next.width&&current.height===next.height?current:next);
    };
    update();
    const observer=new ResizeObserver(update);
    observer.observe(container);
    return()=>observer.disconnect();
  },[]);

  return <div
    ref={ref}
    aria-label="岗位技能关系图"
    className="graphCanvas"
    onPointerMove={()=>{lastPointerMoveAtRef.current=performance.now()}}
    onMouseLeave={clearPreview}
  >
    {viewMode==='explore'&&navigationPath.length>1&&<Button
      className="graph-level-back"
      icon={<ArrowLeftOutlined/>}
      onClick={handleBack}
    >返回</Button>}
    <div className={`graph-level-stage${transitioning?' is-transitioning':''}`}>
      <ReferenceGraphView
        ref={referenceRef}
        width={size.width||1}
        height={size.height||1}
        graphData={graphData}
        currentRoutePath={graphRouteNodeId}
        onNodeClick={handleNodeClick}
        onNodeHoverIdChange={handleNodeHoverIdChange}
        focusNodeId={previewNodeId}
        hideLinks={viewMode==='skills'}
        positionNodeRadiusMultiplier={viewMode==='skills'?SKILL_PANORAMA_POSITION_NODE_RADIUS_MULTIPLIER:1}
        collapseProgress={collapseProgress??undefined}
        expandProgress={expandProgress}
        physicsMode={viewMode==='hierarchy'?'compactHierarchy':viewMode==='explore'?'layerExplore':'default'}
        autoFitOnEngineStop={viewMode!=='explore'}
      />
    </div>
  </div>;
}
