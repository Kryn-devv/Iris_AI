"use client";

import * as React from "react";
import { CheckCircle2, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Label, FieldError } from "@/components/ui/input";
import { POST_STATUS } from "@/lib/status";
import { brand } from "@/config/brand";
import { cn } from "@/lib/utils";
import { VoteButton } from "./vote-button";
import type { PortalPost } from "./types";

/**
 * The embeddable feedback widget (rendered inside an ~380px iframe).
 * Two tabs: top ideas with voting, and a mini composer. Zero auth required —
 * guests are identified by the guest cookie the APIs set.
 */
export function WidgetApp({
  orgSlug,
  orgName,
  posts: initialPosts,
  signedIn,
}: {
  orgSlug: string;
  orgName: string;
  posts: PortalPost[];
  signedIn: boolean;
}) {
  const [tab, setTab] = React.useState<"ideas" | "submit">("ideas");
  const [posts] = React.useState(initialPosts);
  const [createdId, setCreatedId] = React.useState<string | null>(null);

  return (
    <div className="mx-auto flex min-h-screen w-full max-w-[380px] flex-col px-3 py-3">
      <header className="mb-3 flex items-center justify-between gap-2">
        <p className="truncate text-sm font-semibold text-ink">{orgName} · Feedback</p>
        <div
          role="tablist"
          aria-label="Widget sections"
          className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-line bg-surface-raised p-0.5"
        >
          {(
            [
              ["ideas", "Ideas"],
              ["submit", "Submit"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value}
              onClick={() => setTab(value)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
                tab === value
                  ? "bg-accent/15 text-accent-soft"
                  : "text-ink-faint hover:text-ink-muted"
              )}
            >
              {label}
            </button>
          ))}
        </div>
      </header>

      <main className="flex-1">
        {tab === "ideas" ? (
          posts.length === 0 ? (
            <div className="rounded-lg border border-dashed border-line px-4 py-10 text-center">
              <p className="text-xs text-ink-muted">No ideas yet — share the first one!</p>
              <Button size="sm" className="mt-3" onClick={() => setTab("submit")}>
                Share an idea
              </Button>
            </div>
          ) : (
            <ul className="space-y-1.5">
              {posts.map((post) => (
                <li
                  key={post.id}
                  className="flex items-center gap-2.5 rounded-lg border border-line bg-surface-raised px-2.5 py-2"
                >
                  <VoteButton
                    orgSlug={orgSlug}
                    postId={post.id}
                    voted={post.voted}
                    count={post.voteCount}
                    layout="inline"
                  />
                  <div className="min-w-0 flex-1">
                    <a
                      href={`/p/${orgSlug}/posts/${post.id}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate text-xs font-medium text-ink hover:text-accent-soft"
                    >
                      {post.title}
                    </a>
                    <span className="mt-0.5 inline-block">
                      <Badge tone={POST_STATUS[post.status].tone}>
                        {POST_STATUS[post.status].label}
                      </Badge>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )
        ) : createdId ? (
          <div className="rounded-lg border border-success/30 bg-success/10 px-4 py-8 text-center">
            <CheckCircle2 size={22} aria-hidden className="mx-auto text-success" />
            <p className="mt-2 text-sm font-medium text-ink">Thanks — idea received!</p>
            <p className="mt-1 text-xs text-ink-muted">
              The team will take a look. Others can now find and vote for it.
            </p>
            <div className="mt-3 flex items-center justify-center gap-2">
              <a
                href={`/p/${orgSlug}/posts/${createdId}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs font-medium text-accent-soft hover:text-accent"
              >
                View it on the portal <ExternalLink size={11} aria-hidden />
              </a>
              <button
                type="button"
                onClick={() => setCreatedId(null)}
                className="text-xs font-medium text-ink-muted hover:text-ink"
              >
                Share another
              </button>
            </div>
          </div>
        ) : (
          <WidgetComposer
            orgSlug={orgSlug}
            signedIn={signedIn}
            onCreated={(id) => setCreatedId(id)}
          />
        )}
      </main>

      <footer className="mt-3 border-t border-line pt-2 text-center">
        <a
          href="/"
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-ink-faint transition-colors hover:text-ink-muted"
        >
          Powered by <span className="font-medium text-accent-soft">{brand.name}</span>
        </a>
      </footer>
    </div>
  );
}

function WidgetComposer({
  orgSlug,
  signedIn,
  onCreated,
}: {
  orgSlug: string;
  signedIn: boolean;
  onCreated: (id: string) => void;
}) {
  const [title, setTitle] = React.useState("");
  const [body, setBody] = React.useState("");
  const [isRequest, setIsRequest] = React.useState(true);
  const [guestName, setGuestName] = React.useState("");
  const [guestEmail, setGuestEmail] = React.useState("");
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

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
          type: isRequest ? "FEATURE_REQUEST" : "FEEDBACK",
          guestName: guestName.trim() || undefined,
          guestEmail: guestEmail.trim() || undefined,
          source: "WIDGET",
        }),
      });
      const json = await res.json();
      if (!json?.ok) {
        setError(json?.error?.message ?? "Something went wrong");
        return;
      }
      setTitle("");
      setBody("");
      onCreated(json.data.id as string);
    } catch {
      setError("Network error — please try again");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3">
      <div
        role="group"
        aria-label="What kind of post is this?"
        className="inline-flex items-center gap-1 rounded-lg border border-line bg-surface-raised p-0.5"
      >
        {(
          [
            [true, "Feature request"],
            [false, "Feedback"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={label}
            type="button"
            onClick={() => setIsRequest(value)}
            aria-pressed={isRequest === value}
            className={cn(
              "rounded-md px-2.5 py-1 text-xs font-medium transition-colors",
              isRequest === value
                ? "bg-accent/15 text-accent-soft"
                : "text-ink-faint hover:text-ink-muted"
            )}
          >
            {label}
          </button>
        ))}
      </div>
      <div>
        <Label htmlFor="w-title">Title</Label>
        <Input
          id="w-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="One sentence that captures it"
          maxLength={200}
          minLength={3}
          required
        />
      </div>
      <div>
        <Label htmlFor="w-body">Details (optional)</Label>
        <Textarea
          id="w-body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Any context helps the team"
          maxLength={5000}
          className="min-h-[70px]"
        />
      </div>
      {!signedIn && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label htmlFor="w-name">Name (optional)</Label>
            <Input
              id="w-name"
              value={guestName}
              onChange={(e) => setGuestName(e.target.value)}
              maxLength={80}
              autoComplete="name"
            />
          </div>
          <div>
            <Label htmlFor="w-email">Email (optional)</Label>
            <Input
              id="w-email"
              type="email"
              value={guestEmail}
              onChange={(e) => setGuestEmail(e.target.value)}
              maxLength={200}
              autoComplete="email"
            />
          </div>
        </div>
      )}
      <FieldError>{error}</FieldError>
      <Button
        type="submit"
        className="w-full"
        loading={submitting}
        disabled={title.trim().length < 3}
      >
        Submit idea
      </Button>
    </form>
  );
}
