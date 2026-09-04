import { useLocation } from "react-router-dom";
import {
  Component,
  type ElementType,
  forwardRef,
  type ReactNode,
  useCallback,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import type { GraphData } from "./types";
import {
  DARK_COLORS,
  FONT_STACK,
  type GraphViewColors,
  LIGHT_COLORS,
  mergeColors,
} from "./colors";
import {
  createGraphIndex,
  deriveGraphViewData,
  type ForceGraphLink,
  type ForceGraphNode,
  normalizeClientRoutePath,
} from "./deriveGraphViewData";

export type { GraphViewColors } from "./colors";

interface GraphViewProps {
  width: number;
  height: number;
  graphData: GraphData;
  currentRoutePath?: string;
  onNodeClick?: (routePath: string) => void;
  onNodeHoverChange?: (label: string | null, x: number, y: number) => void;
  onNodeHoverIdChange?: (
    nodeId:string|null,
    x:number,
    y:number,
    positions:Array<{id:string;x:number;y:number}>,
  )=>void;
  focusNodeId?: string;
  hideLinks: boolean;
  positionNodeRadiusMultiplier: number;
  collapseProgress?: number;
  expandProgress?: number;
  physicsMode?: "default" | "compactHierarchy" | "layerExplore";
  autoFitOnEngineStop?: boolean;
  colors?: GraphViewColors;
}

interface D3ForceHandle {
  strength?: (value: unknown) => unknown;
  distance?: (value: number) => unknown;
  distanceMax?: (value: number) => unknown;
}

interface ForceGraphHandleRef {
  d3ReheatSimulation?: () => void;
  d3StopSimulation?: () => void;
  d3Force?: (forceName: string, forceFn?: D3ForceHandle) => D3ForceHandle | undefined;
  zoom?: {
    (): number;
    (scale: number, durationMs?: number): void;
  };
  zoomToFit?: (durationMs?: number, padding?: number) => void;
  centerAt?: (x?: number, y?: number, durationMs?: number) => void;
}

function isDarkMode(): boolean {
  if (typeof document === "undefined") return false;
  const html = document.documentElement;
  return (
    html.classList.contains("dark") ||
    html.getAttribute("data-theme") === "dark" ||
    html.closest("[data-theme='dark']") !== null
  );
}

function useTheme(): boolean {
  const [dark, setDark] = useState(() => isDarkMode());

  useEffect(() => {
    const observer = new MutationObserver(() => {
      setDark(isDarkMode());
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });
    return () => observer.disconnect();
  }, []);

  return dark;
}

// ─── Error Boundary ────────────────────────────────────────────────

export class GraphErrorBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { hasError: boolean }
> {
  override state = { hasError: false };

  static getDerivedStateFromError(): { hasError: boolean } {
    return { hasError: true };
  }

  override render() {
    if (this.state.hasError) return this.props.fallback;
    return this.props.children;
  }
}

export function GraphFallback({
  width,
  height,
  color,
}: {
  width: number;
  height: number;
  color: string;
}) {
  return (
    <div
      style={{
        width,
        height,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "column",
        gap: 8,
        color,
        fontFamily: FONT_STACK,
        fontSize: 13,
      }}
    >
      <svg
        aria-hidden="true"
        width="24"
        height="24"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      <span>关系图暂不可用</span>
    </div>
  );
}

export interface GraphViewHandle {
  zoomIn: () => void;
  zoomOut: () => void;
  zoomReset: () => void;
  zoomToFit: () => void;
  centerOnCurrent: () => void;
  getStats: () => { nodes: number; links: number };
}

/**
 * Visual node radius in CSS pixels. Must match the radius used by
 * `nodeCanvasObject` — the pointer-area painter relies on it so the
 * hit region exactly matches the rendered dot.
 */
const NODE_HIT_RADIUS = 5;
const GRAPH_FIT_PADDING = 88;

function renderedNodeRadius(
  node: { isCurrent?: boolean;hasChildren?:boolean;hierarchyLevel?:number;nodeKind?:"position"|"classification"|"skill" },
  isLargeGraph: boolean,
  globalScale: number,
  physicsMode:"default"|"compactHierarchy"|"layerExplore",
  positionNodeRadiusMultiplier:number,
) {
  const radiusMultiplier=node.nodeKind==="position"&&node.isCurrent?positionNodeRadiusMultiplier:1;
  if(physicsMode==="compactHierarchy"){
    const hierarchyRadius=node.isCurrent?8:node.hierarchyLevel===1?5.8:node.hasChildren?3.9:2.8;
    return Math.min(hierarchyRadius,(node.isCurrent?20:18)/Math.max(globalScale,0.01))*radiusMultiplier;
  }
  const baseRadius = isLargeGraph ? 4 : NODE_HIT_RADIUS;
  const maximumScreenRadius = node.isCurrent ? 18 : 16;
  return Math.min(baseRadius, maximumScreenRadius / Math.max(globalScale, 0.01))*radiusMultiplier;
}

function colorWithAlpha(color:string,factor:number){
  const rgba=color.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/);
  if(rgba){
    const alpha=(rgba[4]===undefined?1:Number(rgba[4]))*factor;
    return `rgba(${rgba[1]}, ${rgba[2]}, ${rgba[3]}, ${alpha})`;
  }
  const hex=color.match(/^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i);
  if(hex){
    return `rgba(${Number.parseInt(hex[1],16)}, ${Number.parseInt(hex[2],16)}, ${Number.parseInt(hex[3],16)}, ${factor})`;
  }
  return color;
}

function smoothStep(start:number,end:number,value:number){
  const progress=Math.max(0,Math.min(1,(value-start)/(end-start)));
  return progress*progress*(3-2*progress);
}

export default forwardRef<GraphViewHandle, GraphViewProps>(function GraphView(
  {
    width,
    height,
    graphData: sourceGraphData,
    currentRoutePath: currentRouteOverride,
    onNodeClick,
    onNodeHoverChange,
    onNodeHoverIdChange,
    focusNodeId,
    hideLinks,
    positionNodeRadiusMultiplier,
    collapseProgress,
    expandProgress=1,
    physicsMode="default",
    autoFitOnEngineStop=true,
    colors: customColors,
  },
  ref,
) {
  const { pathname } = useLocation();
  const dark = useTheme();
  const baseColors = dark ? DARK_COLORS : LIGHT_COLORS;
  const colors = useMemo(() => mergeColors(baseColors, customColors), [baseColors, customColors]);
  const [ForceGraph, setForceGraph] = useState<ElementType | null>(null);
  const [forceGraphError, setForceGraphError] = useState(false);
  const hoveredNodeRef = useRef<string | null>(null);
  const connectedSetRef = useRef<Set<string>>(new Set());
  const forceRef = useRef<ForceGraphHandleRef | null>(null);
  const statsRef = useRef({ nodes: 0, links: 0 });

  useImperativeHandle(
    ref,
    () => ({
      zoomIn: () => {
        const fg = forceRef.current;
        if (fg?.zoom) {
          const current = fg.zoom();
          fg.zoom(current * 1.3, 300);
        }
      },
      zoomOut: () => {
        const fg = forceRef.current;
        if (fg?.zoom) {
          const current = fg.zoom();
          fg.zoom(current / 1.3, 300);
        }
      },
      zoomReset: () => {
        const fg = forceRef.current;
        if (fg?.zoom) {
          fg.zoom(1, 300);
        }
      },
      zoomToFit: () => {
        const fg = forceRef.current;
        if (fg?.zoomToFit) {
          fg.zoomToFit(360, GRAPH_FIT_PADDING);
        }
      },
      centerOnCurrent: () => {
        const fg = forceRef.current;
        if (fg?.centerAt) {
          fg.centerAt(0, 0, 0);
        }
      },
      getStats: () => ({ ...statsRef.current }),
    }),
    [],
  );

  useEffect(() => {
    let active = true;
    import("react-force-graph-2d")
      .then((mod) => {
        if (active) setForceGraph(() => mod.default);
      })
      .catch(() => {
        if (active) setForceGraphError(true);
      });
    return () => {
      active = false;
    };
  }, []);

  const currentRoutePath = useMemo(
    () => currentRouteOverride ?? normalizeClientRoutePath(pathname),
    [pathname, currentRouteOverride],
  );

  const graphIndex = useMemo(() => createGraphIndex(sourceGraphData), [sourceGraphData]);
  const {
    nodes: fgNodes,
    links: fgLinks,
    isLargeGraph,
    isEmpty,
  } = useMemo(() => {
    const derived = deriveGraphViewData(graphIndex, currentRoutePath);
    return derived;
  }, [graphIndex, currentRoutePath]);
  const forceGraphData = useMemo(
    () => ({ nodes: fgNodes, links: fgLinks }),
    [fgNodes, fgLinks],
  );

  useEffect(() => {
    statsRef.current = { nodes: fgNodes.length, links: fgLinks.length };
  }, [fgNodes, fgLinks]);

  useEffect(()=>{
    if(physicsMode!=="layerExplore"||isEmpty||focusNodeId)return;
    const fitKeepingCurrent=()=>{
      const currentNode=fgNodes.find(node=>node.id===currentRoutePath);
      const positionedNodes=fgNodes.filter(node=>Number.isFinite(node.x)&&Number.isFinite(node.y));
      const fg=forceRef.current;
      if(!currentNode||currentNode.x===undefined||currentNode.y===undefined||positionedNodes.length===0||!fg?.zoom||!fg.centerAt)return;
      const horizontalRadius=Math.max(1,...positionedNodes.map(node=>Math.abs((node.x??0)-currentNode.x!)));
      const verticalRadius=Math.max(1,...positionedNodes.map(node=>Math.abs((node.y??0)-currentNode.y!)));
      const availableWidth=Math.max(120,width-150);
      const availableHeight=Math.max(120,height-150);
      const targetScale=Math.max(.32,Math.min(2.4,availableWidth/(horizontalRadius*2),availableHeight/(verticalRadius*2)));
      fg.centerAt(currentNode.x,currentNode.y,320);
      fg.zoom(targetScale,320);
    };
    const firstTimer=window.setTimeout(fitKeepingCurrent,320);
    const settledTimer=window.setTimeout(fitKeepingCurrent,760);
    return()=>{
      window.clearTimeout(firstTimer);
      window.clearTimeout(settledTimer);
    };
  },[currentRoutePath,fgNodes,focusNodeId,height,isEmpty,physicsMode,width]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: isEmpty is a stable boolean; only route changes should re-center
  useEffect(() => {
    const timer = setTimeout(() => {
      const fg = forceRef.current;
      if (fg?.centerAt && !isEmpty && !(physicsMode==="layerExplore"&&focusNodeId)) {
        fg.centerAt(0, 0, 300);
      }
    }, 120);
    return () => clearTimeout(timer);
  },[currentRoutePath,focusNodeId,isEmpty,physicsMode]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: reheat when the visible graph changes; forceRef is stable
  useEffect(() => {
    // Tweak forces for Obsidian-like physics when data changes
    const fg = forceRef.current;
    if (fg?.d3Force) {
      const endpointId=(value:unknown)=>typeof value==="string"
        ?value
        :String((value as {id?:unknown}|null)?.id??"");
      const isLeafTarget=(item:ForceGraphLink)=>!graphIndex.nodeById.get(endpointId(item.target))?.hasChildren;
      const hasLeafLinks=fgLinks.some(isLeafTarget);
      const center=fg.d3Force("center");
      if(center&&typeof center.strength==="function"){
        center.strength(physicsMode==="layerExplore"&&focusNodeId?0:1);
      }
      const charge = fg.d3Force("charge");
      if (charge && typeof charge.strength === "function") {
        if(physicsMode==="compactHierarchy"){
          charge.strength((node:ForceGraphNode)=>{
            if(node.isCurrent)return -4;
            if(node.hierarchyLevel===1)return node.subtreeDepth===1?-2:-5;
            if(node.hasChildren)return -3;
            if(node.hierarchyLevel===3)return -9;
            return -12;
          });
          charge.distanceMax?.(52);
        }else if(physicsMode==="layerExplore"){
          const activeStrength=focusNodeId?-34:hasLeafLinks?-70:-42;
          charge.strength((node:ForceGraphNode)=>node.layoutInactive?0:activeStrength);
          charge.distanceMax?.(Number.POSITIVE_INFINITY);
        }else{
          charge.strength(-70);
          charge.distanceMax?.(Number.POSITIVE_INFINITY);
        }
      }
      const link = fg.d3Force("link");
      if (link && typeof link.distance === "function") {
        const degreeByNode=new Map<string,number>();
        const childCountByNode=new Map<string,number>();
        for(const item of fgLinks){
          const sourceId=endpointId(item.source);
          const targetId=endpointId(item.target);
          degreeByNode.set(sourceId,(degreeByNode.get(sourceId)??0)+1);
          degreeByNode.set(targetId,(degreeByNode.get(targetId)??0)+1);
          childCountByNode.set(sourceId,(childCountByNode.get(sourceId)??0)+1);
        }
        (link.distance as unknown as (value: (item: ForceGraphLink) => number) => unknown)(
          (item: ForceGraphLink) => {
            if(physicsMode==="layerExplore"){
              if(focusNodeId)return 68;
              return isLeafTarget(item)?54:44;
            }
            if(physicsMode!=="compactHierarchy")return item.level===1?70:45;
            const targetNode=graphIndex.nodeById.get(endpointId(item.target));
            if(isLeafTarget(item))return item.level===3?13:15;
            if(item.level===1){
              if(targetNode?.subtreeDepth===1){
                const childCount=childCountByNode.get(endpointId(item.target))??0;
                const estimatedClusterRadius=15+Math.min(20,childCount*.45);
                return estimatedClusterRadius*1.3;
              }
              return 84;
            }
            if(item.level===2){
              const childCount=childCountByNode.get(endpointId(item.target))??0;
              return (42+Math.min(70,childCount*2.6))*.75;
            }
            return 44;
          },
        );
        if(physicsMode==="compactHierarchy"&&typeof link.strength==="function"){
          (link.strength as unknown as (value:(item:ForceGraphLink)=>number)=>unknown)(
            (item:ForceGraphLink)=>{
              if(item.level===1)return 0.9;
              return isLeafTarget(item)?0.8:1;
            },
          );
        }else if(physicsMode==="layerExplore"&&typeof link.strength==="function"){
          (link.strength as unknown as (value:(item:ForceGraphLink)=>number)=>unknown)(
            (item:ForceGraphLink)=>item.layoutInactive?0:focusNodeId?0.28:1,
          );
        }else if(typeof link.strength==="function"){
          (link.strength as unknown as (value:(item:ForceGraphLink)=>number)=>unknown)(
            (item:ForceGraphLink)=>1/Math.max(1,Math.min(
              degreeByNode.get(endpointId(item.source))??1,
              degreeByNode.get(endpointId(item.target))??1,
            )),
          );
        }
      }
      fg.d3ReheatSimulation?.();
    }
  },[fgNodes,fgLinks,focusNodeId,graphIndex,physicsMode]);

  const nodePointerAreaPaint = useCallback(
    (
      node: ForceGraphNode & { x?: number; y?: number },
      paintColor: string,
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      // The shadow canvas hit-tests by reading the painted pixel color, so
      // paint the same radius as the visible node. Without this, the default
      // hit radius (`sqrt(nodeVal) * nodeRelSize + pad`) collapses to ~1px
      // at `nodeRelSize={1}`, leaving hover/click nearly impossible.
      const radius=renderedNodeRadius(node,isLargeGraph,globalScale,physicsMode,positionNodeRadiusMultiplier);
      ctx.beginPath();
      ctx.arc(node.x || 0, node.y || 0, radius, 0, Math.PI * 2);
      ctx.fillStyle = paintColor;
      ctx.fill();
    },
    [isLargeGraph,physicsMode,positionNodeRadiusMultiplier],
  );

  const handleNodeClick = useCallback(
    (node: { routePath?: string }) => {
      forceRef.current?.d3StopSimulation?.();
      if (node.routePath && onNodeClick) {
        onNodeClick(node.routePath);
      }
    },
    [onNodeClick],
  );

  const skillNodeColor = useCallback(
    (node: {hasChildren?:boolean;importanceLevel?: "core" | "important" | "supplementary" }) => {
      if(node.hasChildren)return colors.node;
      if (node.importanceLevel === "important") return dark ? "#4d4d4d" : "#e0e0e0";
      if (node.importanceLevel === "supplementary") return dark ? "#424242" : "#ececec";
      return dark?"#5b5b5b":"#d0d0d0";
    },
    [colors.node, dark],
  );

  const skillLabelColor = useCallback(
    (node: { importanceLevel?: "core" | "important" | "supplementary" }) => {
      if (node.importanceLevel === "important") return dark ? "#8b8b8b" : "#969696";
      if (node.importanceLevel === "supplementary") return dark ? "#707070" : "#b5b5b5";
      return colors.label;
    },
    [colors.label, dark],
  );

  const linkEndpoints = useMemo(() => {
    const map = new WeakMap<object, [string, string]>();
    for (const link of fgLinks) {
      map.set(link, [link.source, link.target]);
    }
    return map;
  }, [fgLinks]);

  const getLinkEndpoints = useCallback(
    (link: object): [string, string] | null => {
      const endpoints = linkEndpoints.get(link);
      return endpoints ?? null;
    },
    [linkEndpoints],
  );

  const setConnectedNode = useCallback((nodeId:string)=>{
    const adj=graphIndex.adjacentIdsByNode.get(nodeId);
    const set=new Set(adj||[]);
    set.add(nodeId);
    connectedSetRef.current=set;
  },[graphIndex]);

  useEffect(()=>{
    if(focusNodeId&&!hoveredNodeRef.current)setConnectedNode(focusNodeId);
  },[focusNodeId,setConnectedNode]);

  const handleNodeHover = useCallback(
    (node: (ForceGraphNode & { x?: number; y?: number }) | null) => {
      const positions=fgNodes.flatMap(item=>item.x===undefined||item.y===undefined
        ?[]
        :[{id:item.id,x:item.x,y:item.y}]);
      if (node?.id) {
        hoveredNodeRef.current = node.id;
        setConnectedNode(node.id);
        onNodeHoverChange?.(node.label ?? null, node.x ?? 0, node.y ?? 0);
        onNodeHoverIdChange?.(node.id,node.x??0,node.y??0,positions);
      } else {
        hoveredNodeRef.current = null;
        if(focusNodeId)setConnectedNode(focusNodeId);
        else connectedSetRef.current.clear();
        onNodeHoverChange?.(null, 0, 0);
        onNodeHoverIdChange?.(null,0,0,positions);
      }
    },
    [fgNodes,focusNodeId,onNodeHoverChange,onNodeHoverIdChange,setConnectedNode],
  );

  const nodeColor = useCallback(
    (node: ForceGraphNode) => {
      if((hoveredNodeRef.current??focusNodeId)===node.id)return colors.nodeHover;
      if (node.isCurrent) return colors.currentNode;
      if(physicsMode==="compactHierarchy"&&node.hasChildren){
        return node.hierarchyLevel===1?"#b95d3a":"#c58a65";
      }
      if (node.domain) return colors.domain;
      return skillNodeColor(node);
    },
    [colors.currentNode,colors.domain,colors.nodeHover,focusNodeId,physicsMode,skillNodeColor],
  );

  const drawBackground = useCallback(() => {
    // Obsidian-style clean background — no grid, no adornments
  }, []);

  const handleEngineStop = useCallback(() => {
    if(autoFitOnEngineStop)forceRef.current?.zoomToFit?.(360,GRAPH_FIT_PADDING);
  },[autoFitOnEngineStop]);

  const nodeCanvasObject = useCallback(
    (
      node: ForceGraphNode & { x?: number; y?: number },
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const label = node.label || "";
      const fontSize = Math.max(10, 12) / globalScale;
      const radius=renderedNodeRadius(node,isLargeGraph,globalScale,physicsMode,positionNodeRadiusMultiplier);
      const isExiting=collapseProgress!==undefined;
      const exitProgress=collapseProgress??0;
      const activeFocusNodeId=hoveredNodeRef.current??focusNodeId;
      const focusNode=fgNodes.find(item=>item.id===activeFocusNodeId);
      const revealPosition=node.layoutRevealed?smoothStep(0,1,expandProgress):1;
      const contraction=node.layoutRevealed&&isExiting?smoothStep(0,1,exitProgress)*.78:0;
      const positionFactor=revealPosition*(1-contraction);
      const focusX=focusNode?.x??node.x??0;
      const focusY=focusNode?.y??node.y??0;
      const nx=focusX+((node.x||0)-focusX)*positionFactor;
      const ny=focusY+((node.y||0)-focusY)*positionFactor;
      const revealOpacity=node.layoutRevealed?smoothStep(0,.68,expandProgress):1;
      const opacity=node.layoutInactive
        ?.08+.92*smoothStep(0,.9,exitProgress)
        :node.layoutRevealed&&isExiting
          ?revealOpacity*(1-smoothStep(.08,.72,exitProgress))
          :revealOpacity;

      ctx.save();
      ctx.globalAlpha*=opacity;

      const isHovered = activeFocusNodeId === node.id;
      const isDimmed=node.layoutInactive
        ?!isExiting
        :Boolean(activeFocusNodeId)&&!connectedSetRef.current.has(node.id);

      // Flat node dot — Obsidian uses a plain fill, no rings or glow
      ctx.beginPath();
      ctx.arc(nx, ny, radius, 0, Math.PI * 2);
      if (isDimmed) {
        ctx.fillStyle = dark ? "#393939" : "#ededed";
      } else if (isHovered) {
        ctx.fillStyle = colors.nodeHover;
      } else if (node.isCurrent) {
        ctx.fillStyle = colors.currentNode;
      } else if (node.domain) {
        ctx.fillStyle = colors.domain;
      } else if(physicsMode==="compactHierarchy"&&node.hasChildren){
        ctx.fillStyle=node.hierarchyLevel===1?"#b95d3a":"#c58a65";
      } else {
        ctx.fillStyle = skillNodeColor(node);
      }
      ctx.fill();

      // Obsidian's graph shows a clean dot-field — labels only on hover or zoom
      const shouldDrawLabel=physicsMode==="compactHierarchy"
        ?node.isCurrent||isHovered||Boolean(node.hasChildren)
        :node.isCurrent||isHovered||globalScale>=1.4;
      if (shouldDrawLabel && label) {
        const fontW = node.isCurrent || isHovered ? 600 : 400;
        ctx.font = `${fontW} ${fontSize}px ${FONT_STACK}`;
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";

        // Outline for readability like Obsidian
        ctx.lineJoin = "round";
        ctx.lineWidth = 2 / globalScale;
        ctx.strokeStyle = colors.labelShadow;
        const labelY=node.hasChildren&&!node.isCurrent
          ?ny-radius-fontSize-2/globalScale
          :ny+radius+fontSize+2/globalScale;
        ctx.strokeText(label, nx, labelY);

        if (isDimmed) {
          ctx.fillStyle = dark ? "#4a4a4a" : "#d4d4d4";
        } else if (isHovered) {
          ctx.fillStyle = colors.labelHover;
        } else if (node.isCurrent) {
          ctx.fillStyle = colors.currentLabel;
        } else if (node.domain) {
          ctx.fillStyle = colors.domainLabel;
        } else {
          ctx.fillStyle = skillLabelColor(node);
        }
        ctx.fillText(label, nx, labelY);
      }
      ctx.restore();
    },
    [collapseProgress,expandProgress,fgNodes,isLargeGraph,colors,dark,focusNodeId,physicsMode,positionNodeRadiusMultiplier,skillLabelColor,skillNodeColor],
  );

  const drawHierarchyLabel = useCallback(
    (
      node: ForceGraphNode & { x?: number; y?: number },
      ctx: CanvasRenderingContext2D,
      globalScale: number,
    ) => {
      const label=node.label||"";
      if(!label||node.x===undefined||node.y===undefined)return;

      const activeFocusNodeId=hoveredNodeRef.current??focusNodeId;
      const isHovered=activeFocusNodeId===node.id;
      const shouldDrawLabel=node.isCurrent||isHovered||Boolean(node.hasChildren);
      if(!shouldDrawLabel)return;

      const fontSize=Math.max(10,12)/globalScale;
      const radius=renderedNodeRadius(node,isLargeGraph,globalScale,"compactHierarchy",positionNodeRadiusMultiplier);
      const isDimmed=Boolean(activeFocusNodeId)&&!connectedSetRef.current.has(node.id);
      const labelY=node.hasChildren&&!node.isCurrent
        ?node.y-radius-fontSize-2/globalScale
        :node.y+radius+fontSize+2/globalScale;

      ctx.save();
      ctx.font=`${node.isCurrent||isHovered?600:400} ${fontSize}px ${FONT_STACK}`;
      ctx.textAlign="center";
      ctx.textBaseline="middle";
      ctx.lineJoin="round";
      ctx.lineWidth=3/globalScale;
      ctx.strokeStyle=colors.labelShadow;
      ctx.strokeText(label,node.x,labelY);

      if(isDimmed)ctx.fillStyle=dark?"#4a4a4a":"#d4d4d4";
      else if(isHovered)ctx.fillStyle=colors.labelHover;
      else if(node.isCurrent)ctx.fillStyle=colors.currentLabel;
      else if(node.domain)ctx.fillStyle=colors.domainLabel;
      else ctx.fillStyle=skillLabelColor(node);
      ctx.fillText(label,node.x,labelY);
      ctx.restore();
    },
    [colors.currentLabel,colors.domainLabel,colors.labelHover,colors.labelShadow,dark,focusNodeId,isLargeGraph,positionNodeRadiusMultiplier,skillLabelColor],
  );

  const drawForeground = useCallback(
    (ctx: CanvasRenderingContext2D, globalScale: number) => {
      const activeFocusNodeId=hoveredNodeRef.current??focusNodeId;
      const hoveredNode=fgNodes.find(node=>node.id===activeFocusNodeId);
      if(focusNodeId&&!hideLinks){
        const nodeById=new Map(fgNodes.map(node=>[node.id,node]));
        const exitProgress=collapseProgress??0;
        const contraction=collapseProgress===undefined?0:smoothStep(0,1,exitProgress)*.78;
        const revealPosition=smoothStep(0,1,expandProgress);
        const revealOpacity=smoothStep(0,.68,expandProgress);
        const positionFactor=revealPosition*(1-contraction);
        const lineOpacity=revealOpacity*(collapseProgress===undefined?1:1-smoothStep(.08,.72,exitProgress));
        const displayedPosition=(node:ForceGraphNode)=>node.layoutRevealed&&hoveredNode
          ?{
              x:(hoveredNode.x??0)+((node.x??0)-(hoveredNode.x??0))*positionFactor,
              y:(hoveredNode.y??0)+((node.y??0)-(hoveredNode.y??0))*positionFactor,
            }
          :{x:node.x??0,y:node.y??0};
        ctx.save();
        ctx.globalAlpha*=lineOpacity;
        ctx.strokeStyle=colors.linkHighlight;
        ctx.lineWidth=1.3/globalScale;
        for(const link of fgLinks){
          if(link.layoutInactive)continue;
          const endpoints=getLinkEndpoints(link);
          if(!endpoints)continue;
          const source=nodeById.get(endpoints[0]);
          const target=nodeById.get(endpoints[1]);
          if(source?.x===undefined||source.y===undefined||target?.x===undefined||target.y===undefined)continue;
          const displayedSource=displayedPosition(source);
          const displayedTarget=displayedPosition(target);
          ctx.beginPath();
          ctx.moveTo(displayedSource.x,displayedSource.y);
          ctx.lineTo(displayedTarget.x,displayedTarget.y);
          ctx.stroke();
        }
        ctx.restore();
        for(const node of fgNodes){
          if(!node.layoutInactive&&node.id!==activeFocusNodeId)nodeCanvasObject(node,ctx,globalScale);
        }
        if(hoveredNode)nodeCanvasObject(hoveredNode,ctx,globalScale);
        return;
      }
      if(physicsMode==="compactHierarchy"){
        for(const node of fgNodes){
          if(node.hasChildren&&!node.isCurrent&&node.id!==activeFocusNodeId){
            nodeCanvasObject(node,ctx,globalScale);
          }
        }
        const currentNode=fgNodes.find(node=>node.isCurrent);
        if(currentNode)nodeCanvasObject(currentNode,ctx,globalScale);
        if(hoveredNode&&hoveredNode!==currentNode)nodeCanvasObject(hoveredNode,ctx,globalScale);
        for(const node of fgNodes){
          drawHierarchyLabel(node,ctx,globalScale);
        }
        return;
      }
      const currentNode=fgNodes.find(node=>node.isCurrent);
      if(currentNode)nodeCanvasObject(currentNode,ctx,globalScale);
      if(hoveredNode&&hoveredNode!==currentNode)nodeCanvasObject(hoveredNode,ctx,globalScale);
    },
    [collapseProgress,colors.linkHighlight,drawHierarchyLabel,expandProgress,fgLinks,fgNodes,focusNodeId,getLinkEndpoints,hideLinks,nodeCanvasObject,physicsMode],
  );

  const linkColor = useCallback(
    (link: { source?: unknown; target?: unknown }) => {
      if(hideLinks)return "rgba(0, 0, 0, 0)";
      const inactive=(link as {layoutInactive?:boolean}).layoutInactive;
      if(inactive){
        if(collapseProgress!==undefined){
          return colorWithAlpha(colors.link,.08+.92*collapseProgress);
        }
        return colors.fallbackLinkDim;
      }
      const endpoints = getLinkEndpoints(link);
      if (endpoints) {
        const [src, tgt] = endpoints;
        if (hoveredNodeRef.current||focusNodeId) {
          const isConnected = connectedSetRef.current.has(src)&&connectedSetRef.current.has(tgt);
          const color=isConnected?colors.linkHighlight:colors.fallbackLinkDim;
          return focusNodeId?colorWithAlpha(color,0):color;
        }
      }
      return colors.link;
    },
    [collapseProgress,colors.link,colors.linkHighlight,colors.fallbackLinkDim,focusNodeId,getLinkEndpoints,hideLinks],
  );

  const linkWidth = useCallback(
    (link: { source?: unknown; target?: unknown }) => {
      if(hideLinks)return 0;
      if((link as {layoutInactive?:boolean}).layoutInactive)return 0.35;
      if (isLargeGraph && !hoveredNodeRef.current&&!focusNodeId) {
        return 0.6;
      }
      const endpoints = getLinkEndpoints(link);
      if (endpoints) {
        const [src, tgt] = endpoints;
        if (hoveredNodeRef.current||focusNodeId) {
          const isConnected = connectedSetRef.current.has(src)&&connectedSetRef.current.has(tgt);
          return isConnected ? 1.3 : isLargeGraph ? 0.4 : 0.5;
        }
      }
      return isLargeGraph ? 0.6 : 0.8;
    },
    [focusNodeId,getLinkEndpoints,hideLinks,isLargeGraph],
  );

  if (forceGraphError) {
    return <GraphFallback width={width} height={height} color={colors.label} />;
  }

  if (!ForceGraph) {
    return (
      <div
        style={{
          width,
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <div
          style={{
            width: 20,
            height: 20,
            borderRadius: "50%",
            border: `2px solid ${colors.loaderBorder}`,
            borderTopColor: colors.loaderTop,
            animation: "gv-spinner 0.8s linear infinite",
          }}
        />
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div
        style={{
          width,
          height,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          gap: 6,
          color: colors.label,
          fontFamily: FONT_STACK,
          fontSize: 13,
        }}
      >
        <svg
          aria-hidden="true"
          width="22"
          height="22"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="12" cy="12" r="3" />
          <line x1="12" y1="5" x2="12" y2="9" />
          <line x1="12" y1="15" x2="12" y2="19" />
          <line x1="5" y1="12" x2="9" y2="12" />
          <line x1="15" y1="12" x2="19" y2="12" />
        </svg>
        <span>没有关联页面</span>
      </div>
    );
  }

  return (
    <GraphErrorBoundary
      fallback={<GraphFallback width={width} height={height} color={colors.label} />}
    >
      <ForceGraph
        ref={forceRef}
        graphData={forceGraphData}
        width={width}
        height={height}
        nodeRelSize={1}
        nodeColor={nodeColor}
        nodeCanvasObject={nodeCanvasObject}
        nodeCanvasObjectMode={() => "replace" as const}
        nodePointerAreaPaint={
          nodePointerAreaPaint as (
            node: unknown,
            paintColor: string,
            ctx: CanvasRenderingContext2D,
            globalScale: number,
          ) => void
        }
        onNodeHover={handleNodeHover as (node: unknown, prevNode: unknown) => void}
        linkPointerAreaPaint={()=>undefined}
        linkVisibility={!hideLinks}
        linkColor={linkColor as (link: object) => string}
        linkWidth={linkWidth as (link: object) => number}
        onNodeClick={handleNodeClick as (node: unknown, event: MouseEvent) => void}
        onRenderFramePre={drawBackground}
        onRenderFramePost={drawForeground}
        onEngineStop={handleEngineStop}
        backgroundColor="transparent"
        showPointerCursor
        d3AlphaDecay={physicsMode==="compactHierarchy"?0.05:physicsMode==="layerExplore"?0.018:isLargeGraph?0.08:0.04}
        d3VelocityDecay={physicsMode==="compactHierarchy"?0.54:physicsMode==="layerExplore"?0.24:isLargeGraph?0.6:0.4}
      />
    </GraphErrorBoundary>
  );
});
