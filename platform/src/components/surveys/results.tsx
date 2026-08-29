"use client";

import * as React from "react";
import { Download, Inbox, Star } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/ui/misc";
import { timeAgo } from "@/lib/utils";
import {
  QUESTION_KIND_META,
  type QuestionResults,
  type SurveyResultsPayload,
} from "./types";

// ---------------------------------------------------------------------------
// Theme colors for SVG marks: read the token CSS variables at runtime so the
// charts stay in the design system (SVG attributes can't resolve var()).
// ---------------------------------------------------------------------------

type ChartColors = {
  accent: string;
  success: string;
  danger: string;
  neutral: string;
  grid: string;
  ink: string;
  inkMuted: string;
  overlay: string;
  line: string;
};

function readToken(styles: CSSStyleDeclaration, name: string, fallback: string) {
  const raw = styles.getPropertyValue(name).trim();
  return raw ? `rgb(${raw})` : fallback;
}

function useChartColors(): ChartColors | null {
  const [colors, setColors] = React.useState<ChartColors | null>(null);
  React.useEffect(() => {
    const s = getComputedStyle(document.documentElement);
    setColors({
      accent: readToken(s, "--c-accent", "rgb(124 108 255)"),
      success: readToken(s, "--c-success", "rgb(74 222 128)"),
      danger: readToken(s, "--c-danger", "rgb(248 113 113)"),
      neutral: readToken(s, "--c-ink-faint", "rgb(100 108 134)"),
      grid: readToken(s, "--c-line", "rgb(39 44 66)"),
      ink: readToken(s, "--c-ink", "rgb(235 238 248)"),
      inkMuted: readToken(s, "--c-ink-muted", "rgb(154 162 186)"),
      overlay: readToken(s, "--c-surface-overlay", "rgb(26 30 46)"),
      line: readToken(s, "--c-line-strong", "rgb(58 64 92)"),
    });
  }, []);
  return colors;
}

function tooltipStyle(c: ChartColors): React.CSSProperties {
  return {
    background: c.overlay,
    border: `1px solid ${c.line}`,
    borderRadius: 8,
    fontSize: 12,
    color: c.ink,
    padding: "6px 10px",
  };
}

// ---------------------------------------------------------------------------
// Results tab
// ---------------------------------------------------------------------------

export function SurveyResults({
  orgSlug,
  results,
}: {
  orgSlug: string;
  results: SurveyResultsPayload;
}) {
  const colors = useChartColors();
  const [exporting, setExporting] = React.useState(false);
  const [exportError, setExportError] = React.useState<string | null>(null);

  const exportCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const res = await fetch(
        `/api/orgs/${orgSlug}/surveys/${results.survey.id}/results`
      );
      const json = await res.json();
      if (!json.ok) {
        setExportError(json.error?.message ?? "Export failed");
        return;
      }
      const payload: SurveyResultsPayload = json.data.results;
      const esc = (v: string) => `"${v.replace(/"/g, '""')}"`;
      const qs = [...payload.questions].sort((a, b) => a.order - b.order);
      const header = [
        "Response ID",
        "Respondent",
        "Started at",
        "Completed at",
        ...qs.map((q) => q.prompt),
      ];
      const lines = [header.map(esc).join(",")];
      for (const row of payload.rows) {
        lines.push(
          [
            row.responseId,
            row.respondent,
            row.startedAt,
            row.completedAt ?? "",
            ...qs.map((q) => {
              const v = row.answers[q.id];
              if (v === undefined) return "";
              return Array.isArray(v) ? v.join("; ") : String(v);
            }),
          ]
            .map(esc)
            .join(",")
        );
      }
      const blob = new Blob([lines.join("\n")], {
        type: "text/csv;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${payload.survey.name.replace(/[^\w\- ]+/g, "").trim() || "survey"}-results.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      setExportError("Export failed — please try again");
    } finally {
      setExporting(false);
    }
  };

  if (results.totals.responses === 0) {
    return (
      <EmptyState
        icon={<Inbox size={28} aria-hidden />}
        title="No responses yet"
        description="Share the public link — results will appear here as answers come in."
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Headline numbers + export */}
      <div className="flex flex-wrap items-stretch gap-3">
        <StatTile label="Responses" value={String(results.totals.responses)} />
        <StatTile label="Completed" value={String(results.totals.completed)} />
        <StatTile
          label="Completion rate"
          value={`${results.totals.completionRate}%`}
        />
        <div className="ml-auto flex items-end">
          <Button variant="outline" size="sm" onClick={exportCsv} loading={exporting}>
            <Download size={13} aria-hidden />
            Export CSV
          </Button>
        </div>
      </div>
      {exportError && <p className="text-xs text-danger">{exportError}</p>}

      {/* Timeline */}
      <Card>
        <CardHeader>
          <CardTitle>Responses — last 30 days</CardTitle>
        </CardHeader>
        <CardContent>
          {colors && (
            <div className="h-36">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={results.timeline} barCategoryGap={2}>
                  <CartesianGrid
                    vertical={false}
                    stroke={colors.grid}
                    strokeDasharray="2 4"
                  />
                  <XAxis
                    dataKey="label"
                    tick={{ fontSize: 10, fill: colors.inkMuted }}
                    tickLine={false}
                    axisLine={false}
                    interval={6}
                  />
                  <YAxis
                    allowDecimals={false}
                    width={28}
                    tick={{ fontSize: 10, fill: colors.inkMuted }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    cursor={{ fill: colors.grid, opacity: 0.35 }}
                    contentStyle={tooltipStyle(colors)}
                    labelStyle={{ color: colors.inkMuted }}
                    formatter={(value) => [String(value), "responses"]}
                  />
                  <Bar
                    dataKey="count"
                    fill={colors.accent}
                    radius={[3, 3, 0, 0]}
                    maxBarSize={18}
                  />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Per-question breakdowns */}
      {[...results.questions]
        .sort((a, b) => a.order - b.order)
        .map((q, i) => (
          <Card key={q.id}>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle>
                  {i + 1}. {q.prompt}
                </CardTitle>
                <Badge tone="neutral">{QUESTION_KIND_META[q.kind].label}</Badge>
              </div>
              <CardDescription>
                {q.answered} answer{q.answered === 1 ? "" : "s"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <QuestionBreakdown question={q} colors={colors} />
            </CardContent>
          </Card>
        ))}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-[120px] rounded-xl border border-line bg-surface-raised px-4 py-3">
      <p className="text-[11px] font-medium uppercase tracking-wider text-ink-faint">
        {label}
      </p>
      <p className="mt-0.5 font-display text-xl font-semibold text-ink">
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-kind breakdowns
// ---------------------------------------------------------------------------

function QuestionBreakdown({
  question: q,
  colors,
}: {
  question: QuestionResults;
  colors: ChartColors | null;
}) {
  if (q.answered === 0) {
    return <p className="text-xs text-ink-faint">No answers yet.</p>;
  }

  if (q.kind === "NPS" && q.nps) {
    const { score, promoters, passives, detractors, distribution } = q.nps;
    const total = promoters + passives + detractors;
    const pct = (n: number) => (total === 0 ? 0 : Math.round((n / total) * 100));
    const segments = [
      { label: "Detractors", count: detractors, cls: "bg-danger" },
      { label: "Passives", count: passives, cls: "bg-ink-faint" },
      { label: "Promoters", count: promoters, cls: "bg-success" },
    ];
    return (
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-4">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wider text-ink-faint">
              NPS score
            </p>
            <p className="font-display text-2xl font-semibold text-ink">
              {score > 0 ? `+${score}` : score}
            </p>
          </div>
          <div className="flex-1 space-y-1.5">
            <div
              className="flex h-3 w-full gap-0.5 overflow-hidden rounded-full"
              role="img"
              aria-label={`Detractors ${pct(detractors)}%, passives ${pct(passives)}%, promoters ${pct(promoters)}%`}
            >
              {segments.map(
                (s) =>
                  s.count > 0 && (
                    <div
                      key={s.label}
                      className={`${s.cls} rounded-sm`}
                      style={{ width: `${(s.count / total) * 100}%` }}
                    />
                  )
              )}
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-muted">
              {segments.map((s) => (
                <span key={s.label} className="inline-flex items-center gap-1.5">
                  <span
                    aria-hidden
                    className={`h-2 w-2 rounded-sm ${s.cls}`}
                  />
                  {s.label} {s.count} ({pct(s.count)}%)
                </span>
              ))}
            </div>
          </div>
        </div>
        {colors && (
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={distribution.map((count, score10) => ({
                  score: score10,
                  count,
                }))}
                barCategoryGap={2}
              >
                <CartesianGrid
                  vertical={false}
                  stroke={colors.grid}
                  strokeDasharray="2 4"
                />
                <XAxis
                  dataKey="score"
                  tick={{ fontSize: 10, fill: colors.inkMuted }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  width={28}
                  tick={{ fontSize: 10, fill: colors.inkMuted }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={{ fill: colors.grid, opacity: 0.35 }}
                  contentStyle={tooltipStyle(colors)}
                  labelStyle={{ color: colors.inkMuted }}
                  labelFormatter={(l) => `Score ${l}`}
                  formatter={(value) => [String(value), "answers"]}
                />
                <Bar dataKey="count" radius={[3, 3, 0, 0]} maxBarSize={26}>
                  {distribution.map((_, score10) => (
                    <Cell
                      key={score10}
                      fill={
                        score10 <= 6
                          ? colors.danger
                          : score10 <= 8
                            ? colors.neutral
                            : colors.success
                      }
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <p className="text-[11px] text-ink-faint">
          Detractors 0–6 · Passives 7–8 · Promoters 9–10. Score = % promoters −
          % detractors.
        </p>
      </div>
    );
  }

  if (q.kind === "RATING" && q.rating) {
    const { average, histogram } = q.rating;
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-2">
          <p className="font-display text-2xl font-semibold text-ink">
            {average.toFixed(1)}
          </p>
          <div className="flex items-center gap-0.5" aria-hidden>
            {[1, 2, 3, 4, 5].map((n) => (
              <Star
                key={n}
                size={16}
                className={
                  n <= Math.round(average)
                    ? "fill-warning text-warning"
                    : "text-line-strong"
                }
              />
            ))}
          </div>
          <span className="text-xs text-ink-muted">average of {q.answered}</span>
        </div>
        {colors && (
          <div className="h-32">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={histogram.map((count, idx) => ({
                  stars: `${idx + 1}★`,
                  count,
                }))}
                barCategoryGap={6}
              >
                <CartesianGrid
                  vertical={false}
                  stroke={colors.grid}
                  strokeDasharray="2 4"
                />
                <XAxis
                  dataKey="stars"
                  tick={{ fontSize: 10, fill: colors.inkMuted }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  allowDecimals={false}
                  width={28}
                  tick={{ fontSize: 10, fill: colors.inkMuted }}
                  tickLine={false}
                  axisLine={false}
                />
                <Tooltip
                  cursor={{ fill: colors.grid, opacity: 0.35 }}
                  contentStyle={tooltipStyle(colors)}
                  labelStyle={{ color: colors.inkMuted }}
                  formatter={(value) => [String(value), "answers"]}
                />
                <Bar
                  dataKey="count"
                  fill={colors.accent}
                  radius={[3, 3, 0, 0]}
                  maxBarSize={40}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    );
  }

  if ((q.kind === "SINGLE_CHOICE" || q.kind === "MULTIPLE_CHOICE") && q.choices) {
    const max = Math.max(1, ...q.choices.map((c) => c.count));
    return (
      <ul className="space-y-2.5">
        {q.choices.map((c) => (
          <li key={c.label}>
            <div className="mb-1 flex items-baseline justify-between gap-3 text-xs">
              <span className="truncate text-ink">{c.label}</span>
              <span className="shrink-0 tabular-nums text-ink-muted">
                {c.count} · {c.pct}%
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-line/40">
              <div
                className="h-full rounded-full bg-accent"
                style={{ width: `${(c.count / max) * 100}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    );
  }

  if (q.kind === "OPEN_TEXT" && q.text) {
    return (
      <div className="space-y-3">
        {q.text.keywords.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[11px] font-medium uppercase tracking-wider text-ink-faint">
              Top keywords
            </span>
            {q.text.keywords.map((k) => (
              <Badge key={k} tone="accent">
                {k}
              </Badge>
            ))}
          </div>
        )}
        <ul className="max-h-80 space-y-2 overflow-y-auto pr-1">
          {q.text.answers.map((a, i) => (
            <li
              key={`${a.at}-${i}`}
              className="rounded-lg border border-line bg-surface px-3 py-2"
            >
              <p className="whitespace-pre-wrap text-sm text-ink">{a.value}</p>
              <p className="mt-1 text-[11px] text-ink-faint">{timeAgo(a.at)}</p>
            </li>
          ))}
        </ul>
        {q.answered > q.text.answers.length && (
          <p className="text-[11px] text-ink-faint">
            Showing the latest {q.text.answers.length} of {q.answered} answers —
            export the CSV for everything.
          </p>
        )}
      </div>
    );
  }

  return <p className="text-xs text-ink-faint">No answers yet.</p>;
}
