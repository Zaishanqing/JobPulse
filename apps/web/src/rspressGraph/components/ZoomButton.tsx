export interface ZoomButtonProps {
  children: React.ReactNode;
  ariaLabel: string;
  onClick: () => void;
  size?: "sm" | "md";
}

export default function ZoomButton({ children, ariaLabel, onClick, size = "md" }: ZoomButtonProps) {
  const dimension = size === "sm" ? 22 : 26;

  return (
    <button
      type="button"
      aria-label={ariaLabel}
      onClick={onClick}
      style={{
        width: dimension,
        height: dimension,
        borderRadius: size === "sm" ? 4 : 5,
        border: "none",
        background: "transparent",
        color: "var(--rp-c-text-2, #64748b)",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 0,
        transition: "background 0.12s, color 0.12s",
        flexShrink: 0,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background =
          "color-mix(in srgb, var(--rp-c-brand, #6366f1) 12%, transparent)";
        e.currentTarget.style.color = "var(--rp-c-brand, #6366f1)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "transparent";
        e.currentTarget.style.color = "var(--rp-c-text-2, #64748b)";
      }}
    >
      {children}
    </button>
  );
}
