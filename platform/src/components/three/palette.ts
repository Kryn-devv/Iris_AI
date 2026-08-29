/**
 * Hex mirrors of the CSS brand tokens in globals.css, for use inside WebGL
 * where CSS variables can't reach. If globals.css is re-skinned, update here.
 */
export const PALETTE = {
  void: "#07080e",
  surface: "#0d0f18",
  surfaceRaised: "#131622",
  line: "#272c42",
  ink: "#ebeef8",
  inkMuted: "#9aa2ba",
  accent: "#7c6cff",
  accentSoft: "#9e92ff",
  accentStrong: "#634fff",
  aurora: "#42d6eb",
  ember: "#ff7a59",
  success: "#4ade80",
  warning: "#facc15",
} as const;

export type PaletteKey = keyof typeof PALETTE;
