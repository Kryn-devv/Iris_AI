"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";

export function RunAnalysisButton({ orgSlug }: { orgSlug: string }) {
  const router = useRouter();
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/insights/run`, { method: "POST" });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error?.message ?? "Analysis failed");
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center gap-2">
      {error && <span className="text-xs text-danger">{error}</span>}
      <Button onClick={run} loading={loading}>
        <Sparkles size={14} /> {loading ? "Analyzing…" : "Run analysis"}
      </Button>
    </div>
  );
}
