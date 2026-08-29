"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  ArrowDown,
  ArrowUp,
  GitBranch,
  Lock,
  Plus,
  Trash2,
} from "lucide-react";
import type { QuestionKind, SurveyStatus } from "@prisma/client";
import { Button } from "@/components/ui/button";
import { Input, Textarea, Select, Label, FieldError } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { SURVEY_STATUS } from "@/lib/status";
import {
  QUESTION_KINDS,
  QUESTION_KIND_META,
  SURVEY_TRANSITIONS,
  isChoiceKind,
  isScaleKind,
  type SurveyDTO,
} from "./types";

// ---------------------------------------------------------------------------
// Builder-local state
// ---------------------------------------------------------------------------

type DraftCondition = { key: string; equals?: string; lte?: number };

type DraftQuestion = {
  key: string;
  kind: QuestionKind;
  prompt: string;
  required: boolean;
  choices: string[];
  condition: DraftCondition | null;
};

let uidCounter = 0;
function uid(): string {
  uidCounter += 1;
  return `new-${Date.now().toString(36)}-${uidCounter}`;
}

function fromDTO(survey: SurveyDTO): DraftQuestion[] {
  return survey.questions.map((q) => ({
    key: q.id,
    kind: q.kind,
    prompt: q.prompt,
    required: q.required,
    choices: [...q.choices],
    condition: q.condition
      ? {
          key: q.condition.questionId,
          ...(q.condition.equals !== undefined
            ? { equals: q.condition.equals }
            : {}),
          ...(q.condition.lte !== undefined ? { lte: q.condition.lte } : {}),
        }
      : null,
  }));
}

/** Drop conditions that no longer point at a valid EARLIER question. */
function sanitizeConditions(questions: DraftQuestion[]): DraftQuestion[] {
  return questions.map((q, i) => {
    if (!q.condition) return q;
    const target = questions
      .slice(0, i)
      .find((p) => p.key === q.condition!.key);
    if (!target) return { ...q, condition: null };
    if (q.condition.equals !== undefined) {
      if (!isChoiceKind(target.kind) || !target.choices.includes(q.condition.equals)) {
        return { ...q, condition: null };
      }
    }
    if (q.condition.lte !== undefined && !isScaleKind(target.kind)) {
      return { ...q, condition: null };
    }
    return q;
  });
}

function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function toIso(local: string): string | null {
  if (!local) return null;
  const d = new Date(local);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function SurveyBuilder({
  orgSlug,
  survey,
}: {
  orgSlug: string;
  /** undefined → create mode */
  survey?: SurveyDTO;
}) {
  const router = useRouter();
  const isEdit = Boolean(survey);
  const locked = (survey?.responseCount ?? 0) > 0;

  const [name, setName] = React.useState(survey?.name ?? "");
  const [description, setDescription] = React.useState(
    survey?.description ?? ""
  );
  const [startsAt, setStartsAt] = React.useState(
    toLocalInput(survey?.startsAt ?? null)
  );
  const [endsAt, setEndsAt] = React.useState(toLocalInput(survey?.endsAt ?? null));
  const [allowMultiple, setAllowMultiple] = React.useState(
    survey?.allowMultipleResponses ?? false
  );
  const [segment, setSegment] = React.useState<"all" | "members">(
    survey?.audience.segment ?? "all"
  );
  const [urlContains, setUrlContains] = React.useState(
    survey?.audience.urlContains ?? ""
  );
  const [questions, setQuestions] = React.useState<DraftQuestion[]>(
    survey ? fromDTO(survey) : []
  );
  const [saving, setSaving] = React.useState(false);
  const [statusBusy, setStatusBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [saved, setSaved] = React.useState(false);
  const status: SurveyStatus = survey?.status ?? "DRAFT";

  const update = (fn: (qs: DraftQuestion[]) => DraftQuestion[]) => {
    setQuestions((qs) => sanitizeConditions(fn(qs)));
    setSaved(false);
  };

  const addQuestion = () =>
    update((qs) => [
      ...qs,
      {
        key: uid(),
        kind: "OPEN_TEXT",
        prompt: "",
        required: true,
        choices: [],
        condition: null,
      },
    ]);

  const move = (i: number, dir: -1 | 1) =>
    update((qs) => {
      const j = i + dir;
      if (j < 0 || j >= qs.length) return qs;
      const next = [...qs];
      const tmp = next[i]!;
      next[i] = next[j]!;
      next[j] = tmp;
      return next;
    });

  const patch = (i: number, part: Partial<DraftQuestion>) =>
    update((qs) => qs.map((q, k) => (k === i ? { ...q, ...part } : q)));

  const validate = (): string | null => {
    if (!name.trim()) return "Give the survey a name";
    for (let i = 0; i < questions.length; i++) {
      const q = questions[i]!;
      if (!q.prompt.trim()) return `Question ${i + 1} needs a prompt`;
      if (isChoiceKind(q.kind)) {
        const filled = q.choices.map((c) => c.trim()).filter(Boolean);
        if (filled.length < 2) return `Question ${i + 1} needs at least two options`;
        if (new Set(filled).size !== filled.length)
          return `Question ${i + 1} has duplicate options`;
      }
    }
    const startIso = toIso(startsAt);
    const endIso = toIso(endsAt);
    if (startIso && endIso && new Date(endIso) <= new Date(startIso)) {
      return "End date must be after the start date";
    }
    return null;
  };

  const buildPayload = () => {
    const base = {
      name: name.trim(),
      description: description.trim() ? description.trim() : null,
      startsAt: toIso(startsAt),
      endsAt: toIso(endsAt),
      allowMultipleResponses: allowMultiple,
      audience: {
        segment,
        ...(urlContains.trim() ? { urlContains: urlContains.trim() } : {}),
      },
    };
    if (locked) {
      // Copy-only edits: prompts by id, plus settings.
      return {
        ...base,
        promptUpdates: questions
          .filter((q) => !q.key.startsWith("new-"))
          .map((q) => ({ id: q.key, prompt: q.prompt.trim() })),
      };
    }
    return {
      ...base,
      questions: questions.map((q) => ({
        key: q.key,
        kind: q.kind,
        prompt: q.prompt.trim(),
        required: q.required,
        ...(isChoiceKind(q.kind)
          ? { choices: q.choices.map((c) => c.trim()).filter(Boolean) }
          : {}),
        condition: q.condition,
      })),
    };
  };

  const save = async () => {
    const invalid = validate();
    if (invalid) {
      setError(invalid);
      return;
    }
    setError(null);
    setSaving(true);
    try {
      const res = await fetch(
        isEdit
          ? `/api/orgs/${orgSlug}/surveys/${survey!.id}`
          : `/api/orgs/${orgSlug}/surveys`,
        {
          method: isEdit ? "PATCH" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(buildPayload()),
        }
      );
      const json = await res.json();
      if (!json.ok) {
        setError(json.error?.message ?? "Could not save the survey");
        return;
      }
      setSaved(true);
      if (!isEdit) {
        router.push(`/app/${orgSlug}/surveys/${json.data.id}`);
        router.refresh();
      } else {
        router.refresh();
      }
    } catch {
      setError("Network error — please try again");
    } finally {
      setSaving(false);
    }
  };

  const transition = async (to: SurveyStatus) => {
    if (!survey) return;
    setError(null);
    setStatusBusy(true);
    try {
      const res = await fetch(`/api/orgs/${orgSlug}/surveys/${survey.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: to }),
      });
      const json = await res.json();
      if (!json.ok) {
        setError(json.error?.message ?? "Could not change the status");
        return;
      }
      router.refresh();
    } catch {
      setError("Network error — please try again");
    } finally {
      setStatusBusy(false);
    }
  };

  const transitions = SURVEY_TRANSITIONS[status];
  const transitionLabel: Record<SurveyStatus, string> = {
    DRAFT: "Back to draft",
    ACTIVE: "Activate",
    PAUSED: "Pause",
    COMPLETED: "Mark completed",
  };

  return (
    <div className="space-y-4">
      {locked && (
        <div className="flex items-start gap-2.5 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-xs leading-relaxed text-warning">
          <Lock size={14} className="mt-0.5 shrink-0" aria-hidden />
          <span>
            This survey already has {survey!.responseCount} response
            {survey!.responseCount === 1 ? "" : "s"}, so its structure is
            locked to keep results consistent. You can still edit copy (name,
            description and question wording), scheduling, audience and
            lifecycle — or duplicate the survey to change its questions.
          </span>
        </div>
      )}

      {/* Basics */}
      <Card>
        <CardHeader>
          <CardTitle>Basics</CardTitle>
          <CardDescription>
            What respondents see at the top of the survey.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="survey-name">Name</Label>
            <Input
              id="survey-name"
              value={name}
              maxLength={200}
              placeholder="e.g. Q3 product satisfaction"
              onChange={(e) => {
                setName(e.target.value);
                setSaved(false);
              }}
            />
          </div>
          <div>
            <Label htmlFor="survey-description">Description (optional)</Label>
            <Textarea
              id="survey-description"
              value={description}
              maxLength={2000}
              placeholder="A sentence or two shown before the first question."
              onChange={(e) => {
                setDescription(e.target.value);
                setSaved(false);
              }}
            />
          </div>
        </CardContent>
      </Card>

      {/* Lifecycle & scheduling */}
      <Card>
        <CardHeader>
          <CardTitle>Lifecycle &amp; scheduling</CardTitle>
          <CardDescription>
            Only active surveys within their window are shown publicly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {isEdit ? (
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={SURVEY_STATUS[status].tone}>
                {SURVEY_STATUS[status].label}
              </Badge>
              {transitions.map((to) => (
                <Button
                  key={to}
                  size="sm"
                  variant={to === "ACTIVE" ? "primary" : "outline"}
                  loading={statusBusy}
                  disabled={to === "ACTIVE" && questions.length === 0}
                  title={
                    to === "ACTIVE" && questions.length === 0
                      ? "Add at least one question first"
                      : undefined
                  }
                  onClick={() => transition(to)}
                >
                  {transitionLabel[to]}
                </Button>
              ))}
              {status === "COMPLETED" && (
                <span className="text-xs text-ink-faint">
                  Completed surveys are final — duplicate to run it again.
                </span>
              )}
            </div>
          ) : (
            <p className="text-xs text-ink-muted">
              The survey is created as a{" "}
              <Badge tone="neutral">Draft</Badge> — activate it from this page
              once you have added questions.
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="survey-starts">Starts at (optional)</Label>
              <Input
                id="survey-starts"
                type="datetime-local"
                value={startsAt}
                onChange={(e) => {
                  setStartsAt(e.target.value);
                  setSaved(false);
                }}
              />
            </div>
            <div>
              <Label htmlFor="survey-ends">Ends at (optional)</Label>
              <Input
                id="survey-ends"
                type="datetime-local"
                value={endsAt}
                onChange={(e) => {
                  setEndsAt(e.target.value);
                  setSaved(false);
                }}
              />
            </div>
          </div>
          <label className="flex cursor-pointer items-center gap-2.5 text-sm text-ink">
            <input
              type="checkbox"
              checked={allowMultiple}
              onChange={(e) => {
                setAllowMultiple(e.target.checked);
                setSaved(false);
              }}
              className="h-4 w-4 rounded border-line bg-surface accent-[rgb(var(--c-accent))]"
            />
            <span>
              Allow multiple responses
              <span className="block text-xs text-ink-muted">
                When off, each signed-in user or anonymous visitor can answer
                only once.
              </span>
            </span>
          </label>
        </CardContent>
      </Card>

      {/* Audience */}
      <Card>
        <CardHeader>
          <CardTitle>Audience</CardTitle>
          <CardDescription>
            Who should be asked. This targeting travels with the survey — the
            public link enforces the segment, and the URL filter is used by
            in-app targeting on your own site.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="survey-segment">Segment</Label>
              <Select
                id="survey-segment"
                value={segment}
                onChange={(e) => {
                  setSegment(e.target.value as "all" | "members");
                  setSaved(false);
                }}
              >
                <option value="all">Everyone (including anonymous)</option>
                <option value="members">Signed-in members only</option>
              </Select>
              <p className="mt-1 text-[11px] text-ink-faint">
                {segment === "members"
                  ? "Visitors must sign in before they can answer."
                  : "Anyone with the link can answer; anonymous visitors are tracked with a guest id."}
              </p>
            </div>
            <div>
              <Label htmlFor="survey-url">Show when URL contains (optional)</Label>
              <Input
                id="survey-url"
                value={urlContains}
                maxLength={300}
                placeholder="/checkout"
                onChange={(e) => {
                  setUrlContains(e.target.value);
                  setSaved(false);
                }}
              />
              <p className="mt-1 text-[11px] text-ink-faint">
                Used when the survey is embedded in your product: it only
                appears on pages whose URL contains this text.
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Questions */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between gap-3">
            <div>
              <CardTitle>Questions ({questions.length})</CardTitle>
              <CardDescription>
                One question is shown per step. Conditions can branch on
                earlier answers.
              </CardDescription>
            </div>
            {!locked && (
              <Button size="sm" variant="secondary" onClick={addQuestion}>
                <Plus size={14} aria-hidden />
                Add question
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {questions.length === 0 && (
            <p className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-xs text-ink-muted">
              No questions yet — add your first question to build the survey.
            </p>
          )}
          {questions.map((q, i) => (
            <QuestionEditor
              key={q.key}
              index={i}
              question={q}
              earlier={questions.slice(0, i)}
              locked={locked}
              isFirst={i === 0}
              isLast={i === questions.length - 1}
              onMove={(dir) => move(i, dir)}
              onRemove={() => update((qs) => qs.filter((_, k) => k !== i))}
              onPatch={(part) => patch(i, part)}
            />
          ))}
        </CardContent>
      </Card>

      {/* Save bar */}
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={save} loading={saving}>
          {isEdit ? "Save changes" : "Create survey"}
        </Button>
        {saved && !error && (
          <span className="text-xs text-success">Saved.</span>
        )}
        <FieldError>{error}</FieldError>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Single question editor
// ---------------------------------------------------------------------------

function QuestionEditor({
  index,
  question: q,
  earlier,
  locked,
  isFirst,
  isLast,
  onMove,
  onRemove,
  onPatch,
}: {
  index: number;
  question: DraftQuestion;
  earlier: DraftQuestion[];
  locked: boolean;
  isFirst: boolean;
  isLast: boolean;
  onMove: (dir: -1 | 1) => void;
  onRemove: () => void;
  onPatch: (part: Partial<DraftQuestion>) => void;
}) {
  const conditionTargets = earlier.filter(
    (p) => isChoiceKind(p.kind) || isScaleKind(p.kind)
  );
  const target = q.condition
    ? earlier.find((p) => p.key === q.condition!.key) ?? null
    : null;

  const setConditionTarget = (key: string) => {
    if (!key) {
      onPatch({ condition: null });
      return;
    }
    const t = earlier.find((p) => p.key === key);
    if (!t) return;
    if (isChoiceKind(t.kind)) {
      const first = t.choices.map((c) => c.trim()).filter(Boolean)[0] ?? "";
      onPatch({ condition: { key, equals: first } });
    } else {
      onPatch({ condition: { key, lte: t.kind === "NPS" ? 6 : 3 } });
    }
  };

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="mt-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-surface-overlay text-[11px] font-semibold text-ink-muted">
          {index + 1}
        </span>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_200px]">
            <div>
              <Label htmlFor={`q-prompt-${q.key}`}>Prompt</Label>
              <Input
                id={`q-prompt-${q.key}`}
                value={q.prompt}
                maxLength={500}
                placeholder={QUESTION_KIND_META[q.kind].hint}
                onChange={(e) => onPatch({ prompt: e.target.value })}
              />
            </div>
            <div>
              <Label htmlFor={`q-kind-${q.key}`}>Type</Label>
              <Select
                id={`q-kind-${q.key}`}
                value={q.kind}
                disabled={locked}
                onChange={(e) => {
                  const kind = e.target.value as QuestionKind;
                  onPatch({
                    kind,
                    choices: isChoiceKind(kind)
                      ? q.choices.length > 0
                        ? q.choices
                        : ["", ""]
                      : [],
                  });
                }}
              >
                {QUESTION_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {QUESTION_KIND_META[k].label}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          {isChoiceKind(q.kind) && (
            <div>
              <Label>Options</Label>
              <div className="space-y-2">
                {q.choices.map((choice, ci) => (
                  <div key={ci} className="flex items-center gap-2">
                    <Input
                      value={choice}
                      maxLength={300}
                      disabled={locked}
                      placeholder={`Option ${ci + 1}`}
                      aria-label={`Question ${index + 1} option ${ci + 1}`}
                      onChange={(e) =>
                        onPatch({
                          choices: q.choices.map((c, k) =>
                            k === ci ? e.target.value : c
                          ),
                        })
                      }
                    />
                    {!locked && (
                      <button
                        type="button"
                        aria-label={`Remove option ${ci + 1}`}
                        className="text-ink-faint transition-colors hover:text-danger disabled:opacity-40"
                        disabled={q.choices.length <= 2}
                        onClick={() =>
                          onPatch({
                            choices: q.choices.filter((_, k) => k !== ci),
                          })
                        }
                      >
                        <Trash2 size={14} aria-hidden />
                      </button>
                    )}
                  </div>
                ))}
                {!locked && (
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={q.choices.length >= 20}
                    onClick={() => onPatch({ choices: [...q.choices, ""] })}
                  >
                    <Plus size={13} aria-hidden />
                    Add option
                  </Button>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <label className="flex cursor-pointer items-center gap-2 text-xs text-ink-muted">
              <input
                type="checkbox"
                checked={q.required}
                disabled={locked}
                onChange={(e) => onPatch({ required: e.target.checked })}
                className="h-3.5 w-3.5 rounded border-line bg-surface accent-[rgb(var(--c-accent))]"
              />
              Required
            </label>

            {/* Conditional visibility */}
            <div className="flex flex-wrap items-center gap-2 text-xs text-ink-muted">
              <GitBranch size={13} aria-hidden className="text-ink-faint" />
              <Select
                aria-label={`Question ${index + 1} visibility condition`}
                className="h-8 w-auto min-w-[160px] text-xs"
                value={q.condition?.key ?? ""}
                disabled={locked || conditionTargets.length === 0}
                onChange={(e) => setConditionTarget(e.target.value)}
              >
                <option value="">Always show</option>
                {conditionTargets.map((p) => (
                  <option key={p.key} value={p.key}>
                    Show if Q{earlier.indexOf(p) + 1}
                    {isChoiceKind(p.kind) ? " equals…" : " is at most…"}
                  </option>
                ))}
              </Select>
              {q.condition && target && isChoiceKind(target.kind) && (
                <Select
                  aria-label="Condition value"
                  className="h-8 w-auto min-w-[120px] text-xs"
                  value={q.condition.equals ?? ""}
                  disabled={locked}
                  onChange={(e) =>
                    onPatch({
                      condition: { key: q.condition!.key, equals: e.target.value },
                    })
                  }
                >
                  {target.choices
                    .map((c) => c.trim())
                    .filter(Boolean)
                    .map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                </Select>
              )}
              {q.condition && target && isScaleKind(target.kind) && (
                <Select
                  aria-label="Condition threshold"
                  className="h-8 w-auto text-xs"
                  value={String(q.condition.lte ?? 0)}
                  disabled={locked}
                  onChange={(e) =>
                    onPatch({
                      condition: {
                        key: q.condition!.key,
                        lte: Number(e.target.value),
                      },
                    })
                  }
                >
                  {Array.from(
                    { length: target.kind === "NPS" ? 11 : 5 },
                    (_, n) => (target.kind === "NPS" ? n : n + 1)
                  ).map((n) => (
                    <option key={n} value={n}>
                      ≤ {n}
                    </option>
                  ))}
                </Select>
              )}
              {conditionTargets.length === 0 && !q.condition && (
                <span className="text-[11px] text-ink-faint">
                  (add an earlier scale or choice question to branch)
                </span>
              )}
            </div>
          </div>
        </div>

        {!locked && (
          <div className="flex shrink-0 flex-col items-center gap-1">
            <button
              type="button"
              aria-label={`Move question ${index + 1} up`}
              disabled={isFirst}
              onClick={() => onMove(-1)}
              className="rounded p-1 text-ink-faint transition-colors hover:bg-surface-overlay hover:text-ink disabled:opacity-30"
            >
              <ArrowUp size={14} aria-hidden />
            </button>
            <button
              type="button"
              aria-label={`Move question ${index + 1} down`}
              disabled={isLast}
              onClick={() => onMove(1)}
              className="rounded p-1 text-ink-faint transition-colors hover:bg-surface-overlay hover:text-ink disabled:opacity-30"
            >
              <ArrowDown size={14} aria-hidden />
            </button>
            <button
              type="button"
              aria-label={`Remove question ${index + 1}`}
              onClick={onRemove}
              className="rounded p-1 text-ink-faint transition-colors hover:bg-surface-overlay hover:text-danger"
            >
              <Trash2 size={14} aria-hidden />
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
