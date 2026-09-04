import type { GraphData, GraphLink, GraphNode } from "./types";

export interface ForceGraphNode extends GraphNode {
  isCurrent: boolean;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
}

export interface ForceGraphLink {
  source: string;
  target: string;
  level?: number;
  layoutInactive?: boolean;
}

export interface GraphIndex {
  nodeById: Map<string, GraphNode>;
  adjacentIdsByNode: Map<string, Set<string>>;
  linksByNode: Map<string, GraphLink[]>;
  showAll: boolean;
}

export interface DerivedGraphViewData {
  nodes: ForceGraphNode[];
  links: ForceGraphLink[];
  isLargeGraph: boolean;
  /** True when the current route has no neighbors (isolated page) */
  isEmpty: boolean;
}

export const LARGE_GRAPH_NODE_THRESHOLD = 80;
export const LARGE_GRAPH_LINK_THRESHOLD = 160;

/** Normalize a browser pathname (which may carry a `.html` suffix and trailing slashes) to a graph node id. */
export function normalizeClientRoutePath(pathname: string): string {
  const withoutHtml = pathname.replace(/\.html$/, "");
  const trimmed = withoutHtml.replace(/\/+$/, "");
  const withoutIndex = trimmed.replace(/\/index$/, "");
  return withoutIndex || "/";
}

export function createGraphIndex(graphData: GraphData): GraphIndex {
  const nodeById = new Map<string, GraphNode>();
  const adjacentIdsByNode = new Map<string, Set<string>>();
  const linksByNode = new Map<string, GraphLink[]>();

  for (const node of graphData.nodes) {
    nodeById.set(node.id, node);
  }

  for (const link of graphData.links) {
    ensureNeighborSet(adjacentIdsByNode, link.source).add(link.target);
    ensureNeighborSet(adjacentIdsByNode, link.target).add(link.source);
    ensureLinkBucket(linksByNode, link.source).push(link);

    if (link.target !== link.source) {
      ensureLinkBucket(linksByNode, link.target).push(link);
    }
  }

  return {
    nodeById,
    adjacentIdsByNode,
    linksByNode,
    showAll: graphData.showAll ?? false,
  };
}

export function deriveGraphViewData(
  graphIndex: GraphIndex,
  currentRoutePath: string,
): DerivedGraphViewData {
  const currentNode = graphIndex.nodeById.get(currentRoutePath);

  if (!currentNode) {
    return {
      nodes: [],
      links: [],
      isLargeGraph: false,
      isEmpty: true,
    };
  }

  if (graphIndex.showAll) {
    const nodes=Array.from(graphIndex.nodeById.values(),node=>({...node,isCurrent:node.id===currentRoutePath}));
    const visibleLinks=new Set<GraphLink>();
    for(const bucket of graphIndex.linksByNode.values()){
      bucket.forEach(link=>visibleLinks.add(link));
    }
    const links=Array.from(visibleLinks,link=>({
      source:link.source,
      target:link.target,
      level:link.level,
      layoutInactive:link.layoutInactive,
    }));
    return {
      nodes,
      links,
      isLargeGraph:nodes.length>LARGE_GRAPH_NODE_THRESHOLD||links.length>LARGE_GRAPH_LINK_THRESHOLD,
      isEmpty:links.length===0,
    };
  }

  const connectedIds = new Set(graphIndex.adjacentIdsByNode.get(currentRoutePath) ?? []);
  connectedIds.add(currentRoutePath);

  const nodes: ForceGraphNode[] = [];
  for (const nodeId of connectedIds) {
    const node = graphIndex.nodeById.get(nodeId);
    if (!node) {
      continue;
    }

    nodes.push({
      ...node,
      isCurrent: node.id === currentRoutePath,
    });
  }

  const links = (graphIndex.linksByNode.get(currentRoutePath) ?? []).map((link) => ({
    source: link.source,
    target: link.target,
  }));

  const isEmpty = links.length === 0;
  if (isEmpty) {
    return {
      nodes,
      links,
      isLargeGraph: false,
      isEmpty: true,
    };
  }

  const nodeCount = nodes.length;
  const linkCount = links.length;
  return {
    nodes,
    links,
    isLargeGraph: nodeCount > LARGE_GRAPH_NODE_THRESHOLD || linkCount > LARGE_GRAPH_LINK_THRESHOLD,
    isEmpty: false,
  };
}

function ensureNeighborSet(
  adjacentIdsByNode: GraphIndex["adjacentIdsByNode"],
  nodeId: string,
): Set<string> {
  const existing = adjacentIdsByNode.get(nodeId);
  if (existing) {
    return existing;
  }

  const neighborSet = new Set<string>();
  adjacentIdsByNode.set(nodeId, neighborSet);
  return neighborSet;
}

function ensureLinkBucket(linksByNode: GraphIndex["linksByNode"], nodeId: string): GraphLink[] {
  const existing = linksByNode.get(nodeId);
  if (existing) {
    return existing;
  }

  const linkBucket: GraphLink[] = [];
  linksByNode.set(nodeId, linkBucket);
  return linkBucket;
}
