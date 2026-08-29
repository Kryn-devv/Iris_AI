"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";

export function CopyButton({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = React.useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable */
        }
      }}
      className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-1 text-[11px] text-ink-muted hover:text-ink"
    >
      {copied ? <Check size={11} className="text-success" /> : <Copy size={11} />}
      {copied ? "Copied" : label ?? "Copy"}
    </button>
  );
}
