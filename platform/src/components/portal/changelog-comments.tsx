"use client";

import * as React from "react";
import { MessageSquare, ChevronDown } from "lucide-react";
import { Avatar } from "@/components/ui/misc";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea, FieldError } from "@/components/ui/input";
import { cn, timeAgo } from "@/lib/utils";
import type { PortalComment } from "./types";

/**
 * Collapsible comment thread under a changelog entry. Guest-friendly:
 * an optional name field, otherwise comments post as "Anonymous".
 */
export function ChangelogComments({
  orgSlug,
  entryId,
  initialComments,
  signedIn,
}: {
  orgSlug: string;
  entryId: string;
  initialComments: PortalComment[];
  signedIn: boolean;
}) {
  const [open, setOpen] = React.useState(false);
  const [comments, setComments] = React.useState(initialComments);
  const [body, setBody] = React.useState("");
  const [guestName, setGuestName] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(
        `/api/p/${encodeURIComponent(orgSlug)}/changelog/${encodeURIComponent(entryId)}/comments`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            body: body.trim(),
            guestName: guestName.trim() || undefined,
          }),
        }
      );
      const json = await res.json();
      if (!json?.ok) {
        setError(json?.error?.message ?? "Something went wrong");
        return;
      }
      setComments((prev) => [...prev, json.data.comment as PortalComment]);
      setBody("");
    } catch {
      setError("Network error — please try again");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="border-t border-line pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted transition-colors hover:text-ink"
      >
        <MessageSquare size={13} aria-hidden />
        {comments.length === 0
          ? "Leave a comment"
          : `${comments.length} comment${comments.length === 1 ? "" : "s"}`}
        <ChevronDown
          size={13}
          aria-hidden
          className={cn("transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div className="mt-3 space-y-4">
          {comments.length > 0 && (
            <ul className="space-y-3">
              {comments.map((c) => (
                <li key={c.id} className="flex gap-2.5">
                  <Avatar name={c.name} size={24} className="mt-0.5 shrink-0" />
                  <div className="min-w-0 flex-1">
                    <p className="flex flex-wrap items-center gap-1.5 text-xs">
                      <span className="font-medium text-ink">{c.name}</span>
                      {c.isTeam && <Badge tone="accent">Team</Badge>}
                      <span className="text-ink-faint">{timeAgo(c.createdAt)}</span>
                    </p>
                    <p className="mt-0.5 whitespace-pre-wrap text-xs leading-relaxed text-ink-muted">
                      {c.body}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={submit} className="space-y-2">
            <Textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What do you think about this update?"
              maxLength={2000}
              required
              aria-label="Comment"
              className="min-h-[64px]"
            />
            <div className="flex flex-wrap items-center justify-between gap-2">
              {!signedIn ? (
                <Input
                  value={guestName}
                  onChange={(e) => setGuestName(e.target.value)}
                  placeholder="Your name (optional)"
                  maxLength={80}
                  autoComplete="name"
                  aria-label="Your name (optional)"
                  className="h-8 max-w-[200px] text-xs"
                />
              ) : (
                <span />
              )}
              <Button
                type="submit"
                size="sm"
                loading={submitting}
                disabled={body.trim().length === 0}
              >
                Comment
              </Button>
            </div>
            <FieldError>{error}</FieldError>
          </form>
        </div>
      )}
    </div>
  );
}
