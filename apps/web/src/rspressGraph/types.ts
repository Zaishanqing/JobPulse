export interface GraphNode {
  id: string;
  label: string;
  routePath: string;
  nodeKind?: "position" | "classification" | "skill";
  domain?: boolean;
  importanceLevel?: "core" | "important" | "supplementary";
  hasChildren?: boolean;
  hierarchyLevel?: number;
  subtreeDepth?: number;
  layoutInactive?: boolean;
  layoutRevealed?: boolean;
  x?: number;
  y?: number;
  fx?: number;
  fy?: number;
}

export interface GraphLink {
  source: string;
  target: string;
  level?: number;
  layoutInactive?: boolean;
}

export interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
  showAll?: boolean;
}
