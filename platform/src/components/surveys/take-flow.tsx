"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowLeft, ArrowRight, CheckCircle2, Star } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  isAnswered,
  isQuestionVisible,
  type AnswerValue,
  type SurveyQuestionDTO,
} from "./types";

type Phase = "intro" | "questions" | "done" | "already";

export function SurveyTakeFlow({
  orgSlug,
  surveyId,
  name,
  description,
  questions,
}: {
  orgSlug: string;
  surveyId: string;
  name: string;
  description: string | null;
  questions: SurveyQuestionDTO[];
}) {
  const [phase, setPhase] = React.useState<Phase>(
    description || questions.length > 1 ? "intro" : "questions"
  );
  const [answers, setAnswers] = React.useState<
    Record<string, AnswerValue | undefined>
  >({});
  const [step, setStep] = React.useState(0);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const ordered = React.useMemo(
    () => [...questions].sort((a, b) => a.order - b.order),
    [questions]
  );
  // Conditions are evaluated against the answers given so far, so the visible
  // path can shrink/grow as the respondent moves through the survey.
  const visible = ordered.filter((q) => isQuestionVisible(q, answers));
  const clampedStep = Math.min(step, Math.max(0, visible.length - 1));
  const current = visible[clampedStep];
  const isLast = clampedStep === visible.length - 1;
  const progress =
    phase === "done"
      ? 100
      : visible.length === 0
        ? 0
        : Math.round((clampedStep / visible.length) * 100);

  const setAnswer = (questionId: string, value: AnswerValue | undefined) => {
    setError(null);
    setAnswers((prev) => ({ ...prev, [questionId]: value }));
  };

  const canAdvance =
    current !== undefined &&
    (!current.required || isAnswered(current.kind, answers[current.id]));

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const payload: Record<string, AnswerValue> = {};
      for (const q of visible) {
        const v = answers[q.id];
        if (v !== undefined && isAnswered(q.kind, v)) payload[q.id] = v;
      }
      const res = await fetch(
        `/api/p/${orgSlug}/surveys/${surveyId}/responses`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ answers: payload }),
        }
      );
      const json = await res.json();
      if (!json.ok) {
        if (res.status === 409) {
          setPhase("already");
          return;
        }
        setError(json.error?.message ?? "Something went wrong — please retry");
        return;
      }
      setPhase("done");
    } catch {
      setError("Network error — please try again");
    } finally {
      setSubmitting(false);
    }
  };

  const next = () => {
    if (!current) return;
    if (!canAdvance) {
      setError("This question is required");
      return;
    }
    if (isLast) {
      void submit();
    } else {
      setError(null);
      setStep(clampedStep + 1);
    }
  };

  const back = () => {
    setError(null);
    setStep(Math.max(0, clampedStep - 1));
  };

  // Enter advances (textarea keeps plain Enter for newlines; Cmd/Ctrl+Enter works there).
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key !== "Enter") return;
    const inTextarea = (e.target as HTMLElement).tagName === "TEXTAREA";
    if (inTextarea && !(e.metaKey || e.ctrlKey)) return;
    e.preventDefault();
    next();
  };

  if (phase === "already") {
    return (
      <GlassShell progress={100}>
        <CenterMessage
          title="You already answered this survey"
          body="Thanks — your earlier response was recorded, and this survey accepts one response per person."
          orgSlug={orgSlug}
        />
      </GlassShell>
    );
  }

  if (phase === "done") {
    return (
      <GlassShell progress={100}>
        <CenterMessage
          title="Thank you!"
          body="Your answers were recorded. We read every response — it genuinely shapes what gets built next."
          orgSlug={orgSlug}
          success
        />
      </GlassShell>
    );
  }

  if (phase === "intro") {
    return (
      <GlassShell progress={0}>
        <div className="flex flex-col items-center gap-4 py-6 text-center">
          <h2 className="font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            {name}
          </h2>
          {description && (
            <p className="max-w-md text-sm leading-relaxed text-ink-muted">
              {description}
            </p>
          )}
          <p className="text-xs text-ink-faint">
            {visible.length} question{visible.length === 1 ? "" : "s"} · takes
            about a minute
          </p>
          <Button size="lg" autoFocus onClick={() => setPhase("questions")}>
            Start
            <ArrowRight size={15} aria-hidden />
          </Button>
        </div>
      </GlassShell>
    );
  }

  if (!current) {
    return (
      <GlassShell progress={0}>
        <CenterMessage
          title="Nothing to answer right now"
          body="This survey has no questions for you at the moment."
          orgSlug={orgSlug}
        />
      </GlassShell>
    );
  }

  return (
    <GlassShell progress={progress}>
      <div onKeyDown={onKeyDown} className="flex min-h-[300px] flex-col">
        <p className="text-[11px] font-medium uppercase tracking-widest text-ink-faint">
          Question {clampedStep + 1} of {visible.length}
          {!current.required && " · optional"}
        </p>
        <h2 className="mt-2 font-display text-lg font-semibold leading-snug tracking-tight text-ink sm:text-xl">
          {current.prompt}
        </h2>

        <div className="mt-6 flex-1">
          <QuestionInput
            key={current.id}
            question={current}
            value={answers[current.id]}
            onChange={(v) => setAnswer(current.id, v)}
          />
        </div>

        {error && (
          <p role="alert" className="mt-4 text-xs text-danger">
            {error}
          </p>
        )}

        <div className="mt-6 flex items-center justify-between gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={back}
            disabled={clampedStep === 0 || submitting}
            className={cn(clampedStep === 0 && "invisible")}
          >
            <ArrowLeft size={14} aria-hidden />
            Back
          </Button>
          <div className="flex items-center gap-3">
            {!current.required &&
              !isAnswered(current.kind, answers[current.id]) && (
                <button
                  type="button"
                  onClick={() => {
                    setAnswer(current.id, undefined);
                    if (isLast) void submit();
                    else setStep(clampedStep + 1);
                  }}
                  className="text-xs text-ink-faint transition-colors hover:text-ink-muted"
                >
                  Skip
                </button>
              )}
            <Button onClick={next} loading={submitting} disabled={!canAdvance}>
              {isLast ? "Submit" : "Next"}
              {!isLast && <ArrowRight size={14} aria-hidden />}
            </Button>
          </div>
        </div>
      </div>
    </GlassShell>
  );
}

// ---------------------------------------------------------------------------
// Shell + terminal states
// ---------------------------------------------------------------------------

function GlassShell({
  progress,
  children,
}: {
  progress: number;
  children: React.ReactNode;
}) {
  return (
    <div className="glass relative overflow-hidden rounded-2xl border border-line p-6 sm:p-8">
      <div
        className="absolute inset-x-0 top-0 h-0.5 bg-line/60"
        role="progressbar"
        aria-valuenow={progress}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Survey progress"
      >
        <div
          className="h-full bg-accent transition-all duration-300"
          style={{ width: `${progress}%` }}
        />
      </div>
      {children}
    </div>
  );
}

function CenterMessage({
  title,
  body,
  orgSlug,
  success,
}: {
  title: string;
  body: string;
  orgSlug: string;
  success?: boolean;
}) {
  return (
    <div className="flex flex-col items-center gap-3 py-8 text-center">
      <CheckCircle2
        size={32}
        aria-hidden
        className={success ? "text-success" : "text-ink-faint"}
      />
      <h2 className="font-display text-xl font-semibold tracking-tight text-ink">
        {title}
      </h2>
      <p className="max-w-md text-sm leading-relaxed text-ink-muted">{body}</p>
      <Link
        href={`/p/${orgSlug}`}
        className="mt-2 inline-flex h-9 items-center gap-1.5 rounded-lg border border-line-strong px-4 text-sm font-medium text-ink transition-colors hover:bg-surface-overlay"
      >
        <ArrowLeft size={14} aria-hidden />
        Back to the feedback portal
      </Link>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Per-kind inputs
// ---------------------------------------------------------------------------

function QuestionInput({
  question,
  value,
  onChange,
}: {
  question: SurveyQuestionDTO;
  value: AnswerValue | undefined;
  onChange: (v: AnswerValue | undefined) => void;
}) {
  switch (question.kind) {
    case "NPS":
      return (
        <div>
          <div
            role="radiogroup"
            aria-label="Score from 0 (not likely) to 10 (very likely)"
            className="grid grid-cols-11 gap-1 sm:gap-1.5"
          >
            {Array.from({ length: 11 }, (_, n) => (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={value === n}
                onClick={() => onChange(n)}
                className={cn(
                  "flex h-10 items-center justify-center rounded-lg border text-sm font-medium transition-colors sm:h-11",
                  value === n
                    ? "border-accent bg-accent text-white shadow-glow"
                    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
                )}
              >
                {n}
              </button>
            ))}
          </div>
          <div className="mt-2 flex justify-between text-[11px] text-ink-faint">
            <span>Not at all likely</span>
            <span>Extremely likely</span>
          </div>
        </div>
      );

    case "RATING":
      return (
        <div
          role="radiogroup"
          aria-label="Rating from 1 to 5 stars"
          className="flex items-center gap-2"
        >
          {[1, 2, 3, 4, 5].map((n) => {
            const active = typeof value === "number" && value >= n;
            return (
              <button
                key={n}
                type="button"
                role="radio"
                aria-checked={value === n}
                aria-label={`${n} star${n === 1 ? "" : "s"}`}
                onClick={() => onChange(n)}
                className="rounded-lg p-1.5 transition-transform hover:scale-110 focus-visible:scale-110"
              >
                <Star
                  size={32}
                  aria-hidden
                  className={cn(
                    "transition-colors",
                    active
                      ? "fill-warning text-warning"
                      : "text-line-strong hover:text-ink-faint"
                  )}
                />
              </button>
            );
          })}
          {typeof value === "number" && (
            <span className="ml-2 text-sm text-ink-muted">{value} / 5</span>
          )}
        </div>
      );

    case "SINGLE_CHOICE":
      return (
        <div role="radiogroup" aria-label={question.prompt} className="space-y-2">
          {question.choices.map((choice) => (
            <button
              key={choice}
              type="button"
              role="radio"
              aria-checked={value === choice}
              onClick={() => onChange(choice)}
              className={cn(
                "block w-full rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                value === choice
                  ? "border-accent bg-accent/15 text-ink"
                  : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
              )}
            >
              {choice}
            </button>
          ))}
        </div>
      );

    case "MULTIPLE_CHOICE": {
      const picked = Array.isArray(value) ? value : [];
      const toggle = (choice: string) => {
        const next = picked.includes(choice)
          ? picked.filter((c) => c !== choice)
          : [...picked, choice];
        onChange(next.length > 0 ? next : undefined);
      };
      return (
        <div role="group" aria-label={question.prompt} className="space-y-2">
          {question.choices.map((choice) => {
            const on = picked.includes(choice);
            return (
              <button
                key={choice}
                type="button"
                role="checkbox"
                aria-checked={on}
                onClick={() => toggle(choice)}
                className={cn(
                  "flex w-full items-center gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                  on
                    ? "border-accent bg-accent/15 text-ink"
                    : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink"
                )}
              >
                <span
                  aria-hidden
                  className={cn(
                    "flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]",
                    on
                      ? "border-accent bg-accent text-white"
                      : "border-line-strong"
                  )}
                >
                  {on && "✓"}
                </span>
                {choice}
              </button>
            );
          })}
          <p className="text-[11px] text-ink-faint">Select all that apply.</p>
        </div>
      );
    }

    case "OPEN_TEXT":
      return (
        <div>
          <Textarea
            autoFocus
            value={typeof value === "string" ? value : ""}
            maxLength={5000}
            rows={5}
            placeholder="Type your answer…"
            aria-label={question.prompt}
            onChange={(e) =>
              onChange(e.target.value.length > 0 ? e.target.value : undefined)
            }
          />
          <p className="mt-1.5 text-[11px] text-ink-faint">
            Press ⌘/Ctrl + Enter to continue.
          </p>
        </div>
      );
  }
}
