"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ImagePlus, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input, Textarea, Select, Label, FieldError } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { POST_STATUS } from "@/lib/status";
import type { PostStatus } from "@prisma/client";
import { cn } from "@/lib/utils";
import {
  apiFetch,
  type BoardOption,
  type CategoryOption,
  type SimilarPost,
  type TagOption,
} from "./types";

const MAX_FILE_BYTES = 500 * 1024;

type Attachment = { filename: string; dataUrl: string; size: number };

export function NewPostButton({
  orgSlug,
  categories: initialCategories,
  tags: initialTags,
  boards,
  defaultType = "FEEDBACK",
}: {
  orgSlug: string;
  categories: CategoryOption[];
  tags: TagOption[];
  boards: BoardOption[];
  defaultType?: "FEEDBACK" | "FEATURE_REQUEST";
}) {
  const router = useRouter();
  const [open, setOpen] = React.useState(false);

  const [title, setTitle] = React.useState("");
  const [body, setBody] = React.useState("");
  const [type, setType] = React.useState<string>(defaultType);
  const [boardId, setBoardId] = React.useState<string>("");
  const [categoryId, setCategoryId] = React.useState<string>("");
  const [categories, setCategories] = React.useState(initialCategories);
  const [tags, setTags] = React.useState(initialTags);
  const [selectedTags, setSelectedTags] = React.useState<Set<string>>(new Set());
  const [newCategory, setNewCategory] = React.useState("");
  const [newTag, setNewTag] = React.useState("");
  const [attachments, setAttachments] = React.useState<Attachment[]>([]);
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [similar, setSimilar] = React.useState<SimilarPost[]>([]);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  // Debounced duplicate detection while the title is typed.
  React.useEffect(() => {
    if (!open || title.trim().length < 4) {
      setSimilar([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const data = await apiFetch<{ matches: SimilarPost[] }>(
          `/api/orgs/${orgSlug}/posts/similar`,
          {
            method: "POST",
            body: JSON.stringify({ title: title.trim(), body }),
          }
        );
        setSimilar(data.matches);
      } catch {
        setSimilar([]);
      }
    }, 400);
    return () => clearTimeout(handle);
  }, [open, title, body, orgSlug]);

  const reset = () => {
    setTitle("");
    setBody("");
    setType(defaultType);
    setBoardId("");
    setCategoryId("");
    setSelectedTags(new Set());
    setNewCategory("");
    setNewTag("");
    setAttachments([]);
    setFileError(null);
    setSimilar([]);
    setError(null);
  };

  const onFiles = (list: FileList | null) => {
    setFileError(null);
    if (!list) return;
    for (const file of Array.from(list).slice(0, 5 - attachments.length)) {
      if (!file.type.startsWith("image/")) {
        setFileError("Only image attachments are supported");
        continue;
      }
      if (file.size > MAX_FILE_BYTES) {
        setFileError(`${file.name} is larger than 500KB`);
        continue;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = String(reader.result || "");
        setAttachments((prev) =>
          prev.length >= 5
            ? prev
            : [...prev, { filename: file.name, dataUrl, size: file.size }]
        );
      };
      reader.readAsDataURL(file);
    }
  };

  const createCategory = async () => {
    const name = newCategory.trim();
    if (!name) return;
    try {
      const { category } = await apiFetch<{ category: CategoryOption }>(
        `/api/orgs/${orgSlug}/categories`,
        { method: "POST", body: JSON.stringify({ name }) }
      );
      setCategories((prev) =>
        prev.some((c) => c.id === category.id) ? prev : [...prev, category]
      );
      setCategoryId(category.id);
      setNewCategory("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create category");
    }
  };

  const createTag = async () => {
    const name = newTag.trim();
    if (!name) return;
    try {
      const { tag } = await apiFetch<{ tag: TagOption }>(
        `/api/orgs/${orgSlug}/tags`,
        { method: "POST", body: JSON.stringify({ name }) }
      );
      setTags((prev) =>
        prev.some((t) => t.id === tag.id) ? prev : [...prev, tag]
      );
      setSelectedTags((prev) => new Set(prev).add(tag.id));
      setNewTag("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create tag");
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!title.trim() || !body.trim()) {
      setError("Title and details are both required");
      return;
    }
    setSubmitting(true);
    try {
      const data = await apiFetch<{ id: string }>(
        `/api/orgs/${orgSlug}/posts`,
        {
          method: "POST",
          body: JSON.stringify({
            title: title.trim(),
            body: body.trim(),
            type,
            boardId: boardId || undefined,
            categoryId: categoryId || undefined,
            tagIds: Array.from(selectedTags),
            attachments: attachments.map((a) => ({
              filename: a.filename,
              dataUrl: a.dataUrl,
            })),
          }),
        }
      );
      setOpen(false);
      reset();
      router.push(`/app/${orgSlug}/feedback/${data.id}`);
      router.refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create post");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <Plus size={14} aria-hidden /> New feedback
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="New feedback"
        description="Capture feedback or a feature request on behalf of your users."
        className="max-w-xl"
      >
        <form onSubmit={submit} className="space-y-4">
          <div>
            <Label htmlFor="np-title">Title</Label>
            <Input
              id="np-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Short, descriptive summary"
              maxLength={200}
              autoFocus
            />
          </div>

          {similar.length > 0 && (
            <div className="rounded-lg border border-warning/30 bg-warning/5 p-3">
              <p className="mb-2 text-xs font-medium text-warning">
                Similar existing posts — consider voting instead of duplicating:
              </p>
              <ul className="space-y-1">
                {similar.map((s) => (
                  <li key={s.id} className="flex items-center gap-2 text-xs">
                    <Link
                      href={`/app/${orgSlug}/feedback/${s.id}`}
                      className="truncate text-ink hover:text-accent-soft hover:underline"
                      onClick={() => setOpen(false)}
                    >
                      {s.title}
                    </Link>
                    <Badge tone={POST_STATUS[s.status as PostStatus]?.tone ?? "neutral"}>
                      {POST_STATUS[s.status as PostStatus]?.label ?? s.status}
                    </Badge>
                    <span className="shrink-0 text-ink-faint">
                      {s.voteCount} votes
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <Label htmlFor="np-body">Details</Label>
            <Textarea
              id="np-body"
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="What did the user ask for? Include context and impact."
              maxLength={10000}
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="np-type">Type</Label>
              <Select
                id="np-type"
                value={type}
                onChange={(e) => setType(e.target.value)}
              >
                <option value="FEEDBACK">Feedback</option>
                <option value="FEATURE_REQUEST">Feature request</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="np-board">Board</Label>
              <Select
                id="np-board"
                value={boardId}
                onChange={(e) => setBoardId(e.target.value)}
              >
                <option value="">General (default)</option>
                {boards.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div>
            <Label htmlFor="np-category">Category</Label>
            <div className="flex gap-2">
              <Select
                id="np-category"
                value={categoryId}
                onChange={(e) => setCategoryId(e.target.value)}
                className="flex-1"
              >
                <option value="">Auto-suggest (AI)</option>
                {categories.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
            <div className="mt-2 flex gap-2">
              <Input
                value={newCategory}
                onChange={(e) => setNewCategory(e.target.value)}
                placeholder="Or create a new category…"
                maxLength={50}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void createCategory();
                  }
                }}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-9 shrink-0"
                disabled={!newCategory.trim()}
                onClick={() => void createCategory()}
              >
                Add
              </Button>
            </div>
          </div>

          <div>
            <Label>Tags</Label>
            <div className="flex flex-wrap gap-1.5">
              {tags.map((t) => {
                const active = selectedTags.has(t.id);
                return (
                  <button
                    key={t.id}
                    type="button"
                    aria-pressed={active}
                    onClick={() =>
                      setSelectedTags((prev) => {
                        const next = new Set(prev);
                        if (next.has(t.id)) next.delete(t.id);
                        else next.add(t.id);
                        return next;
                      })
                    }
                    className={cn(
                      "rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors",
                      active
                        ? "border-accent/50 bg-accent/15 text-accent-soft"
                        : "border-line text-ink-muted hover:border-line-strong hover:text-ink"
                    )}
                  >
                    {t.name}
                  </button>
                );
              })}
              {tags.length === 0 && (
                <span className="text-xs text-ink-faint">No tags yet</span>
              )}
            </div>
            <div className="mt-2 flex gap-2">
              <Input
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                placeholder="New tag…"
                maxLength={40}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    void createTag();
                  }
                }}
              />
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-9 shrink-0"
                disabled={!newTag.trim()}
                onClick={() => void createTag()}
              >
                Add
              </Button>
            </div>
          </div>

          <div>
            <Label htmlFor="np-files">Attachments (images, max 500KB each)</Label>
            <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-dashed border-line px-3 py-2 text-xs text-ink-muted hover:border-line-strong hover:text-ink">
              <ImagePlus size={14} aria-hidden />
              Add images
              <input
                id="np-files"
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp"
                multiple
                className="sr-only"
                onChange={(e) => {
                  onFiles(e.target.files);
                  e.target.value = "";
                }}
              />
            </label>
            <FieldError>{fileError}</FieldError>
            {attachments.length > 0 && (
              <ul className="mt-2 flex flex-wrap gap-2">
                {attachments.map((a, i) => (
                  <li
                    key={`${a.filename}-${i}`}
                    className="relative overflow-hidden rounded-lg border border-line"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={a.dataUrl}
                      alt={a.filename}
                      className="h-16 w-16 object-cover"
                    />
                    <button
                      type="button"
                      aria-label={`Remove ${a.filename}`}
                      onClick={() =>
                        setAttachments((prev) => prev.filter((_, j) => j !== i))
                      }
                      className="absolute right-0.5 top-0.5 rounded-full bg-void/70 p-0.5 text-ink-muted hover:text-ink"
                    >
                      <X size={12} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <FieldError>{error}</FieldError>

          <div className="flex justify-end gap-2 pt-1">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Create feedback
            </Button>
          </div>
        </form>
      </Dialog>
    </>
  );
}
