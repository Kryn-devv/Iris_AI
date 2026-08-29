/**
 * Chart color system (validated with the dataviz palette checker against the
 * dark surface #131622 — lightness band, chroma, CVD separation, contrast).
 *
 * - SERIES: fixed-order categorical hues; never cycle beyond three series.
 * - SENTIMENT: semantic status colors, reserved for sentiment/status only.
 * - Single-series magnitude charts use SERIES[0] alone.
 */
export const SERIES = ["#7c6cff", "#0f9fb5", "#e5502e"] as const;

export const SENTIMENT_COLORS = {
  POSITIVE: "#4ade80",
  NEUTRAL: "#646c86",
  NEGATIVE: "#f87171",
} as const;

export const CHART_INK = "#9aa2ba"; // axis/label ink (ink-muted)
export const CHART_GRID = "#272c42"; // hairline grid (line token)

export const TOOLTIP_STYLE: React.CSSProperties = {
  backgroundColor: "#1a1e2e",
  border: "1px solid #3a405c",
  borderRadius: 8,
  fontSize: 12,
  color: "#ebeef8",
};
