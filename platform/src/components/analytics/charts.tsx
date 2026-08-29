"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  Cell,
  LabelList,
} from "recharts";
import {
  SERIES,
  SENTIMENT_COLORS,
  CHART_INK,
  CHART_GRID,
  TOOLTIP_STYLE,
} from "./chart-theme";

const axisProps = {
  stroke: CHART_GRID,
  tick: { fill: CHART_INK, fontSize: 11 },
  tickLine: false,
  axisLine: { stroke: CHART_GRID },
} as const;

export type WeekPoint = {
  week: string;
  feedback: number;
  requests: number;
  votes: number;
  positive: number;
  neutral: number;
  negative: number;
};

/** Feedback volume by week, split by post type (2 fixed series). */
export function VolumeChart({ data }: { data: WeekPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="0" vertical={false} />
        <XAxis dataKey="week" {...axisProps} />
        <YAxis {...axisProps} allowDecimals={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ stroke: CHART_INK, strokeWidth: 1 }} />
        <Legend wrapperStyle={{ fontSize: 12, color: CHART_INK }} />
        <Area
          type="monotone"
          dataKey="feedback"
          name="Feedback"
          stroke={SERIES[0]}
          fill={SERIES[0]}
          fillOpacity={0.22}
          strokeWidth={2}
          dot={false}
        />
        <Area
          type="monotone"
          dataKey="requests"
          name="Feature requests"
          stroke={SERIES[1]}
          fill={SERIES[1]}
          fillOpacity={0.22}
          strokeWidth={2}
          dot={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Votes per week (single magnitude series). */
export function VotesChart({ data }: { data: WeekPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }} barCategoryGap="30%">
        <CartesianGrid stroke={CHART_GRID} vertical={false} />
        <XAxis dataKey="week" {...axisProps} />
        <YAxis {...axisProps} allowDecimals={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#272c4240" }} />
        <Bar dataKey="votes" name="Votes" fill={SERIES[0]} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Sentiment mix by week — semantic status colors, stacked. */
export function SentimentTrendChart({ data }: { data: WeekPoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
        <CartesianGrid stroke={CHART_GRID} vertical={false} />
        <XAxis dataKey="week" {...axisProps} />
        <YAxis {...axisProps} allowDecimals={false} />
        <Tooltip contentStyle={TOOLTIP_STYLE} />
        <Legend wrapperStyle={{ fontSize: 12, color: CHART_INK }} />
        <Area type="monotone" stackId="s" dataKey="positive" name="Positive" stroke={SENTIMENT_COLORS.POSITIVE} fill={SENTIMENT_COLORS.POSITIVE} fillOpacity={0.35} strokeWidth={1.5} dot={false} />
        <Area type="monotone" stackId="s" dataKey="neutral" name="Neutral" stroke={SENTIMENT_COLORS.NEUTRAL} fill={SENTIMENT_COLORS.NEUTRAL} fillOpacity={0.35} strokeWidth={1.5} dot={false} />
        <Area type="monotone" stackId="s" dataKey="negative" name="Negative" stroke={SENTIMENT_COLORS.NEGATIVE} fill={SENTIMENT_COLORS.NEGATIVE} fillOpacity={0.35} strokeWidth={1.5} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export type NamedCount = { name: string; count: number; color?: string };

/**
 * Horizontal magnitude bars. Identity comes from the row label (and an
 * optional per-entity color from data, e.g. category colors); single-hue
 * otherwise.
 */
export function BreakdownBars({
  data,
  height,
}: {
  data: NamedCount[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height ?? Math.max(140, data.length * 34)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 36, left: 8, bottom: 0 }} barCategoryGap="28%">
        <XAxis type="number" hide />
        <YAxis type="category" dataKey="name" width={140} {...axisProps} />
        <Tooltip contentStyle={TOOLTIP_STYLE} cursor={{ fill: "#272c4240" }} />
        <Bar dataKey="count" name="Items" radius={[0, 4, 4, 0]}>
          <LabelList dataKey="count" position="right" style={{ fill: CHART_INK, fontSize: 11 }} />
          {data.map((row, i) => (
            <Cell key={i} fill={row.color ?? SERIES[0]} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
