"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { TrendingUp, AlertTriangle, Lightbulb, FileText, X } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { timeAgo } from "@/lib/utils";

const KIND_META: Record<string, { label: string; tone: BadgeTone; icon: React.ReactNode }> = {
  TREND: { label: "Trend", tone: "aurora", icon: <TrendingUp size={14} /> },
  ALERT: { label: "Alert", tone: "danger", icon: <AlertTriangle size={14} /> },
  OPPORTUNITY: { label: "Opportunity", tone: "success", icon: <Lightbulb size={14} /> },
  SUMMARY: { label: "Summary", tone: "accent", icon: <FileText size={14} /> },
};

export function InsightCard({
  orgSlug,
  insight,
}: {
  orgSlug: string;
  insight: { id: string; kind: string; title: string; body: string; createdAt: string };
}) {
  const router = useRouter();
  const [gone, setGone] = React.useState(false);
  const meta = KIND_META[insight.kind] ?? KIND_META.SUMMARY!;

  async function dismiss() {
    setGone(true);
    await fetch(`/api/orgs/${orgSlug}/insights/${insight.id}`, { method: "PATCH" });
    router.refresh();
  }

  if (gone) return null;
  return (
    <Card>
      <CardContent className="flex items-start gap-3 px-5 py-4">
        <span className="mt-0.5 rounded-lg bg-line/40 p-1.5 text-ink-muted">{meta.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <Badge tone={meta.tone}>{meta.label}</Badge>
            <span className="text-[10px] text-ink-faint">{timeAgo(insight.createdAt)}</span>
          </div>
          <p className="mt-1.5 text-sm font-medium text-ink">{insight.title}</p>
          <p className="mt-0.5 text-sm text-ink-muted">{insight.body}</p>
        </div>
        <button
          onClick={dismiss}
          aria-label="Dismiss insight"
          className="text-ink-faint hover:text-ink"
        >
          <X size={14} />
        </button>
      </CardContent>
    </Card>
  );
}
