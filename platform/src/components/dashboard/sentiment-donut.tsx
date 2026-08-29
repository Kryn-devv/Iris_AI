"use client";

import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip } from "recharts";
import { SENTIMENT_COLORS, TOOLTIP_STYLE } from "@/components/analytics/chart-theme";

export function SentimentDonut({
  positive,
  neutral,
  negative,
}: {
  positive: number;
  neutral: number;
  negative: number;
}) {
  const total = positive + neutral + negative;
  const data = [
    { name: "Positive", value: positive, color: SENTIMENT_COLORS.POSITIVE },
    { name: "Neutral", value: neutral, color: SENTIMENT_COLORS.NEUTRAL },
    { name: "Negative", value: negative, color: SENTIMENT_COLORS.NEGATIVE },
  ].filter((d) => d.value > 0);
  const positivePct = total ? Math.round((positive / total) * 100) : 0;

  return (
    <div className="relative h-48">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Tooltip contentStyle={TOOLTIP_STYLE} />
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius={56}
            outerRadius={78}
            paddingAngle={3}
            strokeWidth={0}
          >
            {data.map((d, i) => (
              <Cell key={i} fill={d.color} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-2xl font-semibold text-ink">{positivePct}%</span>
        <span className="text-[11px] text-ink-faint">positive · 30d</span>
      </div>
      <div className="mt-1 flex justify-center gap-4 text-[11px] text-ink-muted">
        {data.map((d) => (
          <span key={d.name} className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: d.color }} />
            {d.name} {total ? Math.round((d.value / total) * 100) : 0}%
          </span>
        ))}
      </div>
    </div>
  );
}
