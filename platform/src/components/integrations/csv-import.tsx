"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";

/** Minimal CSV parser handling quoted fields and escaped quotes. */
function parseCSV(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let inQuotes = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]!;
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ",") { row.push(field); field = ""; }
    else if (ch === "\n" || ch === "\r") {
      if (ch === "\r" && text[i + 1] === "\n") i++;
      row.push(field); field = "";
      if (row.some((c) => c.trim() !== "")) rows.push(row);
      row = [];
    } else field += ch;
  }
  row.push(field);
  if (row.some((c) => c.trim() !== "")) rows.push(row);
  return rows;
}

export function CsvImport({ orgSlug }: { orgSlug: string }) {
  const router = useRouter();
  const fileRef = React.useRef<HTMLInputElement>(null);
  const [status, setStatus] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);

  async function onFile(file: File) {
    setBusy(true);
    setStatus(null);
    try {
      const rows = parseCSV(await file.text());
      if (rows.length < 2) throw new Error("CSV needs a header row plus at least one data row");
      const header = rows[0]!.map((h) => h.trim().toLowerCase());
      const titleIdx = header.indexOf("title");
      const bodyIdx = header.indexOf("body");
      if (titleIdx === -1 || bodyIdx === -1) {
        throw new Error('CSV must have "title" and "body" columns');
      }
      const catIdx = header.indexOf("category");
      const nameIdx = header.indexOf("name");
      const items = rows
        .slice(1)
        .map((r) => ({
          title: r[titleIdx]?.trim() ?? "",
          body: r[bodyIdx]?.trim() ?? "",
          ...(catIdx >= 0 && r[catIdx]?.trim() ? { category: r[catIdx]!.trim() } : {}),
          ...(nameIdx >= 0 && r[nameIdx]?.trim() ? { guestName: r[nameIdx]!.trim() } : {}),
        }))
        .filter((r) => r.title && r.body)
        .slice(0, 500);
      if (items.length === 0) throw new Error("No valid rows found");
      const res = await fetch(`/api/orgs/${orgSlug}/posts/import`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rows: items }),
      });
      const json = await res.json();
      if (!json.ok) throw new Error(json.error?.message ?? "Import failed");
      setStatus(`Imported ${json.data.created ?? items.length} posts ✓`);
      router.refresh();
    } catch (err) {
      setStatus(err instanceof Error ? err.message : "Import failed");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="flex items-center gap-3">
      <input
        ref={fileRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
      />
      <Button size="sm" variant="outline" loading={busy} onClick={() => fileRef.current?.click()}>
        <Upload size={13} /> Upload CSV
      </Button>
      {status && <span className="text-xs text-ink-muted">{status}</span>}
    </div>
  );
}
