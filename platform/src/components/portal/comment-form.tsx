"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Label, FieldError } from "@/components/ui/input";

/**
 * Comment composer on the public post detail page. Guests can leave a name
 * or stay anonymous; the server re-renders the thread on refresh.
 */
export function CommentForm({
  orgSlug,
  postId,
  signedIn,
}: {
  orgSlug: string;
  postId: string;
  signedIn: boolean;
}) {
  const router = useRouter();
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
        `/api/p/${encodeURIComponent(orgSlug)}/posts/${encodeURIComponent(postId)}/comments`,
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
      setBody("");
      router.refresh();
    } catch {
      setError("Network error — please try again");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <Label htmlFor="comment-body">Add a comment</Label>
        <Textarea
          id="comment-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Share context, use cases, or a workaround…"
          maxLength={2000}
          required
        />
      </div>
      <div className="flex flex-wrap items-end justify-between gap-3">
        {!signedIn ? (
          <div className="min-w-0 flex-1 sm:max-w-[220px]">
            <Label htmlFor="comment-name">Your name (optional)</Label>
            <Input
              id="comment-name"
              value={guestName}
              onChange={(e) => setGuestName(e.target.value)}
              placeholder="Anonymous"
              maxLength={80}
              autoComplete="name"
            />
          </div>
        ) : (
          <span />
        )}
        <Button type="submit" loading={submitting} disabled={body.trim().length === 0}>
          Post comment
        </Button>
      </div>
      <FieldError>{error}</FieldError>
    </form>
  );
}
