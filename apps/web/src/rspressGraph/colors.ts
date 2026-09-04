export const FONT_STACK = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

export interface CanvasColors {
  currentNode: string;
  currentLabel: string;
  domain: string;
  domainLabel: string;
  node: string;
  nodeHover: string;
  label: string;
  labelHover: string;
  link: string;
  linkHighlight: string;
  labelShadow: string;
  fallbackLinkDim: string;
  loaderBorder: string;
  loaderTop: string;
}

export type GraphViewColors = Partial<CanvasColors>;

export const LIGHT_COLORS: CanvasColors = {
  currentNode: "#5b5b5b",
  currentLabel: "#1a1a1a",
  domain: "#64748b",
  domainLabel: "#334155",
  node: "#9a9a9a",
  nodeHover: "#b94d2e",
  label: "#4a4a4a",
  labelHover: "#1a1a1a",
  link: "rgba(218, 218, 218, 0.85)",
  linkHighlight: "rgba(90, 90, 90, 0.85)",
  labelShadow: "rgba(255, 255, 255, 0.9)",
  fallbackLinkDim: "rgba(218, 218, 218, 0.35)",
  loaderBorder: "rgba(90, 90, 90, 0.2)",
  loaderTop: "#5b5b5b",
};

export const DARK_COLORS: CanvasColors = {
  currentNode: "#d4d4d4",
  currentLabel: "#ffffff",
  domain: "#94a3b8",
  domainLabel: "#e2e8f0",
  node: "#8a8a8a",
  nodeHover: "#b94d2e",
  label: "#9a9a9a",
  labelHover: "#ffffff",
  link: "rgba(63, 63, 63, 0.9)",
  linkHighlight: "rgba(200, 200, 200, 0.85)",
  labelShadow: "rgba(24, 24, 24, 0.85)",
  fallbackLinkDim: "rgba(63, 63, 63, 0.4)",
  loaderBorder: "rgba(160, 160, 160, 0.2)",
  loaderTop: "#d4d4d4",
};

export function mergeColors(base: CanvasColors, overrides?: GraphViewColors): CanvasColors {
  if (!overrides) return base;
  return { ...base, ...overrides };
}
