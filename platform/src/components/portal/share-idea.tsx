"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Sparkles, ChevronUp, Check } from "lucide-react";
import { Dialog } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Select, Label, FieldError } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { POST_STATUS } from "@/lib/status";
import { cn, compactNumber } from "@/lib/utils";
import type { SimilarPortalPost } from "./types";

type Category = { id: string; name: string };

/**
 * "Share an idea" composer. Guests can post with an optional name/email;
 * while typing a title we surface similar existing posts with a one-click
 * "vote for this instead" escape hatch.
 */
export function ShareIdea({
  orgSlug,
  categories,
  signedIn,
  source = "PORTAL",
  buttonClassName,
  onCreated,
}: {
  orgSlug: string;
  categories: Category[];
  signedIn: boolean;
  source?: "PORTAL" | "WIDGET";
  buttonClassName?: string;
  onCreated?: (id: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  return (
    <>
      <Button
        size="lg"
        className={cn("shadow-glow", buttonClassName)}
        onClick={() => setOpen(true)}
      >
        <Sparkles size={15} aria-hidden />
        Share an idea
      </Button>
      {open && (
        <ShareIdeaDialog
          orgSlug={orgSlug}
          categories={categories}
          signedIn={signedIn}
          source={source}
          onClose={() => setOpen(false)}
          onCreated={onCreated}
        />
      )}
    </>
  );
}

export function ShareIdeaDialog({
  orgSlug,
  categories,
  signedIn,
  source,
  onClose,
  onCreated,
}: {
  orgSlug: string;
  categories: Category[];
  signedIn: boolean;
  source: "PORTAL" | "WIDGET";
  onClose: () => void;
  onCreated?: (id: string) => void;
}) {
  const router = useRouter();
  const [title, setTitle] = React.useState("");
  const [body, setBody] = React.useState("");
  const [type, setType] = React.useState<"FEATURE_REQUEST" | "FEEDBACK">(
    "FEATURE_REQUEST"
  );
  const [categoryId, setCategoryId] = React.useState("");
  const [guestName, setGuestName] = React.useState("");
  const [guestEmail, setGuestEmail] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);
  const [similar, setSimilar] = React.useState<SimilarPortalPost[]>([]);
  const [votedInstead, setVotedInstead] = React.useState<Set<string>>(new Set());
  const debounce = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const requestSeq = React.useRef(0);

  // Live duplicate suggestions while the title is typed.
  React.useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    const draft = title.trim();
    if (draft.length < 4) {
      setSimilar([]);
      return;
    }
    debounce.current = setTimeout(async () => {
      const seq = ++requestSeq.current;
      try {
        const res = await fetch(
          `/api/p/${encodeURIComponent(orgSlug)}/posts?similarTo=${encodeURIComponent(draft)}`
        );
        const json = await res.json();
        if (seq === requestSeq.current && json?.ok) {
          setSimilar(json.data.posts ?? []);
        }
      } catch {
        /* suggestions are best-effort */
      }
    }, 400);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [title, orgSlug]);

  async function voteInstead(post: SimilarPortalPost) {
    try {
      const res = await fetch(
        `/api/p/${encodeURIComponent(orgSlug)}/posts/${encodeURIComponent(post.id)}/vote`,
        { method: "POST" }
      );
      const json = await res.json();
      if (json?.ok && json.data.voted) {
        setVotedInstead((prev) => new Set(prev).add(post.id));
        router.refresh();
      }
    } catch {
      /* ignore */
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const res = await fetch(`/api/p/${encodeURIComponent(orgSlug)}/posts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: title.trim(),
          body: body.trim(),
          type,
          categoryId: categoryId || null,
          guestName: guestName.trim() || undefined,
          guestEmail: guestEmail.trim() || undefined,
          source,
        }),
      });
      const json = await res.json();
      if (!json?.ok) {
        setError(json?.error?.message ?? "Something went wrong");
        return;
      }
      onClose();
      if (onCreated) onCreated(json.data.id as string);
      else router.push(`/p/${orgSlug}/posts/${json.data.id}`);
      router.refresh();
    } catch {
      setError("Network error — please try again");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="Share an idea"
      description="Tell us what you'd love to see — or what's getting in your way."
    >
      <form onSubmit={submit} className="space-y-4">
        <div
          role="group"
          aria-label="What kind of post is this?"
          className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface p-1"
        >
          {(
            [
              ["FEATURE_REQUEST", "Feature request"],
              ["FEEDBACK", "Feedback"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setType(value)}
              aria-pressed={type === value}
              className={cn(
                "rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                type === value
                  ? "bg-accent/15 text-accent-soft"
                  : "text-ink-faint hover:text-ink-muted"
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div>
          <Label htmlFor="idea-title">Title</Label>
          <Input
            id="idea-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="One sentence that captures it"
            maxLength={200}
            required
            minLength={3}
          />
        </div>

        {similar.length > 0 && (
          <div className="rounded-lg border border-accent/25 bg-accent/5 p-3">
            <p className="mb-2 text-xs font-medium text-accent-soft">
              Looks like this might already exist — vote instead?
            </p>
            <ul className="space-y-1.5">
              {similar.map((p) => (
                <li key={p.id} className="flex items-center justify-between gap-3">
                  <span className="min-w-0 flex-1 truncate text-xs text-ink-muted">
                    {p.title}
                    <Badge tone={POST_STATUS[p.status].tone} className="ml-2 align-middle">
                      {POST_STATUS[p.status].label}
                    </Badge>
                  </span>
                  {p.voted || votedInstead.has(p.id) ? (
                    <span className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-success">
                      <Check size={12} aria-hidden /> Voted
                    </span>
                  ) : (
                    <button
                      type="button"
                      onClick={() => voteInstead(p)}
                      className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line bg-surface px-2 py-0.5 text-xs font-medium text-ink-muted transition-colors hover:border-accent/50 hover:text-accent-soft"
                    >
                      <ChevronUp size={12} aria-hidden />
                      {compactNumber(p.voteCount)} · vote for this
                    </button>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <Label htmlFor="idea-body">Details</Label>
          <Textarea
            id="idea-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="What problem would it solve? Any context helps."
            maxLength={5000}
          />
        </div>

        {categories.length > 0 && (
          <div>
            <Label htmlFor="idea-category">Category (optional)</Label>
            <Select
              id="idea-category"
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
            >
              <option value="">Let the team decide</option>
              {categories.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </Select>
          </div>
        )}

        {!signedIn && (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="idea-name">Your name (optional)</Label>
              <Input
                id="idea-name"
                value={guestName}
                onChange={(e) => setGuestName(e.target.value)}
                placeholder="Ada Lovelace"
                maxLength={80}
                autoComplete="name"
              />
            </div>
            <div>
              <Label htmlFor="idea-email">Email (optional)</Label>
              <Input
                id="idea-email"
                type="email"
                value={guestEmail}
                onChange={(e) => setGuestEmail(e.target.value)}
                placeholder="you@example.com"
                maxLength={200}
                autoComplete="email"
              />
            </div>
          </div>
        )}

        <FieldError>{error}</FieldError>

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting} disabled={title.trim().length < 3}>
            Submit idea
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
