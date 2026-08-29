"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { ChangelogLabel } from "@prisma/client";
import { ImagePlus, PackageCheck, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label, Textarea, FieldError } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Markdown } from "@/lib/markdown";
import { CHANGELOG_LABEL_META } from "@/lib/status";
import { cn, slugify } from "@/lib/utils";

const MAX_COVER_BYTES = 800 * 1024;

export type EditorEntry = {
  id: string;
  title: string;
  slug: string;
  version: string | null;
  body: string;
  labels: string[];
  coverImageUrl: string | null;
  videoUrl: string | null;
};

export type ShippedPost = { id: string; title: string };

export function ChangelogEditor({
  orgSlug,
  entry,
  shippedPosts,
}: {
  orgSlug: string;
  entry?: EditorEntry | null;
  shippedPosts: ShippedPost[];
}) {
  const router = useRouter();
  const isNew = !entry;

  const [title, setTitle] = React.useState(entry?.title ?? "");
  const [slug, setSlug] = React.useState(entry?.slug ?? "");
  const [slugTouched, setSlugTouched] = React.useState(Boolean(entry));
  const [version, setVersion] = React.useState(entry?.version ?? "");
  const [labels, setLabels] = React.useState<string[]>(entry?.labels ?? []);
  const [body, setBody] = React.useState(entry?.body ?? "");
  const [coverImageUrl, setCoverImageUrl] = React.useState(
    entry?.coverImageUrl ?? ""
  );
  const [videoUrl, setVideoUrl] = React.useState(entry?.videoUrl ?? "");
  const [selectedShipped, setSelectedShipped] = React.useState<string[]>([]);

  const [saving, setSaving] = React.useState(false);
  const [uploading, setUploading] = React.useState(false);
  const [errors, setErrors] = React.useState<Record<string, string>>({});
  const [savedAt, setSavedAt] = React.useState<number | null>(null);
  const fileRef = React.useRef<HTMLInputElement>(null);

  const onTitleChange = (value: string) => {
    setTitle(value);
    if (!slugTouched) setSlug(slugify(value));
  };

  const toggleLabel = (label: string) => {
    setLabels((prev) =>
      prev.includes(label) ? prev.filter((l) => l !== label) : [...prev, label]
    );
  };

  const toggleShipped = (id: string) => {
    setSelectedShipped((prev) =>
      prev.includes(id) ? prev.filter((p) => p !== id) : [...prev, id]
    );
  };

  const insertShipped = () => {
    const chosen = shippedPosts.filter((p) => selectedShipped.includes(p.id));
    if (chosen.length === 0) return;
    const block = `\n\n## Shipped in this release\n${chosen
      .map((p) => `- ${p.title}`)
      .join("\n")}\n`;
    setBody((prev) => `${prev.trimEnd()}${block}`);
    setSelectedShipped([]);
  };

  const uploadCover = async (file: File) => {
    setErrors((e) => ({ ...e, cover: "" }));
    if (file.size > MAX_COVER_BYTES) {
      setErrors((e) => ({ ...e, cover: "Image must be 800KB or smaller" }));
      return;
    }
    if (!/^image\/(png|jpe?g|webp|gif)$/.test(file.type)) {
      setErrors((e) => ({ ...e, cover: "Use a png, jpeg, webp or gif image" }));
      return;
    }
    setUploading(true);
    try {
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(new Error("Could not read the file"));
        reader.readAsDataURL(file);
      });
      const res = await fetch(`/api/orgs/${orgSlug}/changelog/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataUrl }),
      });
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Upload failed");
      }
      setCoverImageUrl(json.data.url as string);
    } catch (err) {
      setErrors((e) => ({
        ...e,
        cover: err instanceof Error ? err.message : "Upload failed",
      }));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const validate = (): boolean => {
    const next: Record<string, string> = {};
    if (!title.trim()) next.title = "A title is required";
    if (!body.trim()) next.body = "Write something for the release notes";
    if (videoUrl.trim() && !/^https:\/\/.+/.test(videoUrl.trim())) {
      next.videoUrl = "Use an https link (YouTube, Vimeo, …)";
    }
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const save = async () => {
    if (!validate()) return;
    setSaving(true);
    setErrors({});
    try {
      const payload = {
        title: title.trim(),
        slug: slug.trim() || slugify(title),
        version: version.trim() || null,
        labels,
        body,
        coverImageUrl: coverImageUrl || null,
        videoUrl: videoUrl.trim() || null,
      };
      const res = await fetch(
        isNew
          ? `/api/orgs/${orgSlug}/changelog`
          : `/api/orgs/${orgSlug}/changelog/${entry!.id}`,
        {
          method: isNew ? "POST" : "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      const json = await res.json().catch(() => null);
      if (!res.ok || !json?.ok) {
        throw new Error(json?.error?.message ?? "Could not save the entry");
      }
      // Server may have adjusted the slug for uniqueness.
      if (json.data.slug) setSlug(json.data.slug as string);
      setSavedAt(Date.now());
      if (isNew) {
        router.push(`/app/${orgSlug}/changelog/${json.data.id}`);
      } else {
        router.refresh();
      }
    } catch (err) {
      setErrors((e) => ({
        ...e,
        form: err instanceof Error ? err.message : "Could not save the entry",
      }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="space-y-4 pt-4">
          <div className="grid gap-4 sm:grid-cols-[1fr,180px]">
            <div>
              <Label htmlFor="cl-title">Title</Label>
              <Input
                id="cl-title"
                value={title}
                onChange={(e) => onTitleChange(e.target.value)}
                placeholder="e.g. Slack integration is live"
                maxLength={200}
              />
              <FieldError>{errors.title}</FieldError>
            </div>
            <div>
              <Label htmlFor="cl-version">Version</Label>
              <Input
                id="cl-version"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
                placeholder="v2.4.0"
                maxLength={50}
              />
            </div>
          </div>
          <div>
            <Label htmlFor="cl-slug">Slug</Label>
            <Input
              id="cl-slug"
              value={slug}
              onChange={(e) => {
                setSlugTouched(true);
                setSlug(slugify(e.target.value) || e.target.value);
              }}
              placeholder="slack-integration-is-live"
              maxLength={80}
            />
            <p className="mt-1 text-[11px] text-ink-faint">
              Public URL: /p/{orgSlug}/changelog — a “-2” suffix is added
              automatically if the slug is taken.
            </p>
          </div>
          <div>
            <Label>Labels</Label>
            <div className="flex flex-wrap gap-1.5">
              {Object.values(ChangelogLabel).map((label) => {
                const meta = CHANGELOG_LABEL_META[label];
                const activeLabel = labels.includes(label);
                return (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={activeLabel}
                    onClick={() => toggleLabel(label)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                      activeLabel
                        ? "border-accent/60 bg-accent/15 text-accent-soft"
                        : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
                    )}
                  >
                    {meta?.label ?? label}
                  </button>
                );
              })}
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label>Cover image</Label>
              {coverImageUrl ? (
                <div className="relative overflow-hidden rounded-lg border border-line">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={coverImageUrl}
                    alt="Cover"
                    className="max-h-40 w-full object-cover"
                  />
                  <button
                    type="button"
                    aria-label="Remove cover image"
                    onClick={() => setCoverImageUrl("")}
                    className="absolute right-2 top-2 rounded-full bg-void/70 p-1 text-ink hover:bg-void"
                  >
                    <X size={13} aria-hidden />
                  </button>
                </div>
              ) : (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  loading={uploading}
                  onClick={() => fileRef.current?.click()}
                >
                  <ImagePlus size={14} aria-hidden />
                  Upload image (≤ 800KB)
                </Button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif"
                className="hidden"
                aria-hidden
                tabIndex={-1}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void uploadCover(file);
                }}
              />
              <FieldError>{errors.cover}</FieldError>
            </div>
            <div>
              <Label htmlFor="cl-video">Video URL</Label>
              <Input
                id="cl-video"
                value={videoUrl}
                onChange={(e) => setVideoUrl(e.target.value)}
                placeholder="https://www.youtube.com/watch?v=…"
                maxLength={500}
              />
              <FieldError>{errors.videoUrl}</FieldError>
            </div>
          </div>
        </CardContent>
      </Card>

      {shippedPosts.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <PackageCheck size={15} aria-hidden className="text-success" />
              Ship it — reference shipped roadmap posts
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-1.5">
              {shippedPosts.map((post) => {
                const checked = selectedShipped.includes(post.id);
                return (
                  <button
                    key={post.id}
                    type="button"
                    aria-pressed={checked}
                    onClick={() => toggleShipped(post.id)}
                    className={cn(
                      "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                      checked
                        ? "border-success/60 bg-success/10 text-success"
                        : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
                    )}
                  >
                    {post.title}
                  </button>
                );
              })}
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              className="mt-3"
              disabled={selectedShipped.length === 0}
              onClick={insertShipped}
            >
              Insert “Shipped in this release” list
            </Button>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div>
          <Label htmlFor="cl-body">Release notes (markdown)</Label>
          <Textarea
            id="cl-body"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={"## What's new\n\n- Feature one\n- Feature two"}
            className="min-h-[320px] font-mono text-xs leading-relaxed"
          />
          <FieldError>{errors.body}</FieldError>
        </div>
        <div>
          <Label>Preview</Label>
          <div className="min-h-[320px] rounded-lg border border-line bg-surface p-4">
            {body.trim() ? (
              <Markdown source={body} />
            ) : (
              <p className="text-xs text-ink-faint">
                The rendered markdown preview appears here.
              </p>
            )}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Button onClick={save} loading={saving}>
          {isNew ? "Create entry" : "Save changes"}
        </Button>
        {savedAt && !saving && (
          <span className="text-xs text-success">Saved</span>
        )}
        <FieldError>{errors.form}</FieldError>
      </div>
    </div>
  );
}
