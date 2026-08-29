/**
 * Static experience — served to prefers-reduced-motion users, browsers
 * without WebGL, and as the SSR/SEO document before the journey hydrates.
 * Same copy, same CTAs, no scroll hijacking: elegant sections with CSS-only
 * glow. No hooks, no browser APIs — safe to prerender.
 */
import Link from "next/link";
import { brand } from "@/config/brand";
import {
  CHAPTERS,
  CHAOS_SNIPPETS,
  MATRIX_FEATURES,
  VOTE_CARDS,
  ROADMAP_STAGES,
  SURVEY_PANELS,
  CHANGELOG_MILESTONES,
  type ChapterCopy,
} from "./copy";
import { Footer } from "./Footer";
import { cn } from "@/lib/utils";

const TONE_CLASS: Record<string, string> = {
  accent: "text-accent-soft",
  aurora: "text-aurora",
  ember: "text-ember",
  success: "text-success",
};

function StaticNav() {
  return (
    <header className="fixed inset-x-0 top-0 z-50">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="glass flex items-center gap-2.5 rounded-full px-4 py-2" aria-label={`${brand.name} home`}>
          <span aria-hidden className="h-2.5 w-2.5 rounded-full bg-accent-soft" />
          <span className="font-display text-sm font-bold tracking-[0.28em] text-ink">
            {brand.wordmark}
          </span>
        </Link>
        <div className="glass flex items-center gap-2 rounded-full p-1.5">
          <Link href="/login" className="rounded-full px-3 py-1.5 text-sm font-medium text-ink-muted hover:text-ink">
            Log in
          </Link>
          <Link href="/register" className="rounded-full bg-accent px-4 py-1.5 text-sm font-semibold text-white shadow-glow hover:bg-accent-strong">
            Get started
          </Link>
        </div>
      </div>
    </header>
  );
}

function Ctas({ chapter, center }: { chapter: ChapterCopy; center?: boolean }) {
  if (!chapter.ctas) return null;
  return (
    <div className={cn("mt-8 flex flex-wrap items-center gap-4", center && "justify-center")}>
      {chapter.ctas.map((cta) =>
        cta.variant === "primary" ? (
          <Link
            key={cta.label}
            href={cta.href}
            className="inline-flex h-12 items-center rounded-full bg-accent px-7 text-sm font-semibold text-white shadow-glow-lg hover:bg-accent-strong"
          >
            {cta.label}
          </Link>
        ) : (
          <Link
            key={cta.label}
            href={cta.href}
            className="inline-flex h-12 items-center rounded-full border border-line-strong px-7 text-sm font-medium text-ink hover:border-accent-soft/60 hover:text-accent-soft"
          >
            {cta.label}
          </Link>
        )
      )}
    </div>
  );
}

function SectionHeader({ chapter, center }: { chapter: ChapterCopy; center?: boolean }) {
  return (
    <div className={cn(center && "text-center")}>
      <p className="font-mono text-xs font-medium uppercase tracking-[0.34em] text-accent-soft">
        {chapter.kicker}
      </p>
      <h2 className="mt-4 font-display text-3xl font-bold tracking-tight text-ink sm:text-5xl">
        {chapter.headline}
      </h2>
      <p className={cn("mt-4 max-w-xl text-base leading-relaxed text-ink-muted sm:text-lg", center && "mx-auto")}>
        {chapter.sub}
      </p>
    </div>
  );
}

function byId(id: ChapterCopy["id"]): ChapterCopy {
  return CHAPTERS.find((c) => c.id === id)!;
}

export function Fallback() {
  const hero = byId("hero");
  const finale = byId("finale");

  return (
    <div className="bg-void">
      <StaticNav />

      {/* Hero — CSS radial glow instead of the 3D core. */}
      <section className="relative flex min-h-[92vh] items-center justify-center overflow-hidden px-6">
        <div
          aria-hidden
          className="absolute inset-0"
          style={{
            background:
              "radial-gradient(ellipse 55% 45% at 50% 42%, rgb(124 108 255 / 0.28) 0%, rgb(66 214 235 / 0.08) 45%, transparent 70%)",
          }}
        />
        <div
          aria-hidden
          className="absolute left-1/2 top-[38%] h-44 w-44 -translate-x-1/2 -translate-y-1/2 rounded-full"
          style={{
            background:
              "radial-gradient(circle, rgb(158 146 255 / 0.85) 0%, rgb(124 108 255 / 0.35) 45%, transparent 70%)",
            filter: "blur(2px)",
          }}
        />
        <div className="relative z-10 max-w-3xl pt-20 text-center">
          <p className="font-mono text-xs font-medium uppercase tracking-[0.34em] text-accent-soft">
            {hero.kicker}
          </p>
          <h1 className="mt-5 font-display text-4xl font-bold leading-[1.02] tracking-tight sm:text-6xl lg:text-7xl">
            <span className="text-gradient">{hero.headline}</span>
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-lg text-ink-muted">{hero.sub}</p>
          <Ctas chapter={hero} center />
        </div>
      </section>

      <div className="mx-auto max-w-6xl space-y-28 px-6 pb-28 pt-8 sm:space-y-36">
        {/* Chaos */}
        <section>
          <SectionHeader chapter={byId("chaos")} />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {CHAOS_SNIPPETS.map((s) => (
              <figure key={s.text} className="glass rounded-2xl p-5">
                <figcaption className="text-[11px] font-semibold uppercase tracking-[0.2em] text-accent-soft">
                  {s.source}
                </figcaption>
                <blockquote className="mt-3 text-sm text-ink">“{s.text}”</blockquote>
              </figure>
            ))}
          </div>
        </section>

        {/* Capture */}
        <section>
          <SectionHeader chapter={byId("capture")} />
          <ul className="mt-8 flex flex-wrap gap-3">
            {byId("capture").bullets?.map((b) => (
              <li key={b} className="glass rounded-full px-4 py-2 text-sm font-medium text-ink">
                <span aria-hidden className="mr-2 inline-block h-1.5 w-1.5 rounded-full bg-aurora align-middle" />
                {b}
              </li>
            ))}
          </ul>
        </section>

        {/* AI analysis */}
        <section>
          <SectionHeader chapter={byId("analysis")} />
          <dl className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {byId("analysis").stats?.map((stat) => (
              <div key={stat.label} className="glass rounded-2xl p-5">
                <dd className={cn("font-display text-2xl font-bold tracking-tight", TONE_CLASS[stat.tone ?? "accent"])}>
                  {stat.value}
                </dd>
                <dt className="mt-1 text-[11px] uppercase tracking-[0.18em] text-ink-faint">
                  {stat.label}
                </dt>
              </div>
            ))}
          </dl>
        </section>

        {/* Prioritization */}
        <section>
          <SectionHeader chapter={byId("priorities")} />
          <div className="mt-10 space-y-3">
            {MATRIX_FEATURES.map((f) => (
              <div key={f.name} className="glass flex items-center gap-4 rounded-2xl p-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-ink">{f.name}</p>
                  <p className="mt-0.5 text-xs text-ink-faint">
                    ▲ {f.votes.toLocaleString("en-US")} votes · revenue {f.revenue} · AI confidence{" "}
                    {Math.round(f.confidence * 100)}%
                  </p>
                </div>
                <div className="h-2 w-32 overflow-hidden rounded-full bg-surface-overlay sm:w-48" aria-hidden>
                  <div
                    className="h-full rounded-full bg-accent-gradient"
                    style={{ width: `${Math.round(f.score * 100)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* Voting */}
        <section>
          <SectionHeader chapter={byId("voting")} />
          <div className="mt-10 grid gap-4 sm:grid-cols-3">
            {VOTE_CARDS.map((v) => (
              <div key={v.name} className="glass rounded-2xl p-6">
                <p className="text-sm font-semibold text-ink">{v.name}</p>
                <p className="mt-3 font-display text-3xl font-bold text-gradient">
                  {v.votes.toLocaleString("en-US")}
                </p>
                <p className="mt-1 text-xs text-ink-faint">votes and climbing ↑</p>
              </div>
            ))}
          </div>
        </section>

        {/* Roadmap */}
        <section>
          <SectionHeader chapter={byId("roadmap")} />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {ROADMAP_STAGES.map((stage) => (
              <div key={stage.name} className="glass rounded-2xl p-5">
                <p className="font-display text-sm font-bold tracking-[0.2em]" style={{ color: stage.hex }}>
                  {stage.name}
                </p>
                <ul className="mt-4 space-y-2">
                  {stage.cards.map((card) => (
                    <li key={card} className="rounded-lg border border-line bg-surface-raised/60 px-3 py-2 text-sm text-ink">
                      {card}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* Surveys */}
        <section>
          <SectionHeader chapter={byId("surveys")} />
          <div className="mt-10 grid gap-4 lg:grid-cols-3">
            {SURVEY_PANELS.map((panel) => (
              <div key={panel.prompt} className="glass rounded-2xl p-6">
                <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-aurora">
                  {panel.kind === "NPS" ? "NPS survey" : panel.kind === "CHOICE" ? "Multiple choice" : "Open question"}
                </p>
                <p className="mt-3 text-sm font-medium text-ink">{panel.prompt}</p>
                {panel.kind === "NPS" && (
                  <div className="mt-4 flex flex-wrap gap-1" aria-hidden>
                    {Array.from({ length: 11 }).map((_, i) => (
                      <span
                        key={i}
                        className={cn(
                          "flex h-7 w-7 items-center justify-center rounded-md border text-[11px]",
                          i >= 9
                            ? "border-accent-soft bg-accent/30 text-ink"
                            : "border-line text-ink-faint"
                        )}
                      >
                        {i}
                      </span>
                    ))}
                  </div>
                )}
                {panel.kind === "CHOICE" && panel.choices && (
                  <ul className="mt-4 space-y-2" aria-hidden>
                    {panel.choices.map((choice, i) => (
                      <li
                        key={choice}
                        className={cn(
                          "rounded-lg border px-3 py-2 text-xs",
                          i === 0 ? "border-aurora/50 bg-aurora/10 text-ink" : "border-line text-ink-muted"
                        )}
                      >
                        {choice}
                      </li>
                    ))}
                  </ul>
                )}
                {panel.kind === "TEXT" && (
                  <div className="mt-4 rounded-lg border border-line px-3 py-3 text-xs text-ink-faint" aria-hidden>
                    Tell us anything…
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Changelog */}
        <section>
          <SectionHeader chapter={byId("changelog")} />
          <ol className="mt-10 space-y-0">
            {CHANGELOG_MILESTONES.map((m, i) => (
              <li key={m.version} className="relative flex gap-5 pb-8 last:pb-0">
                <div className="flex flex-col items-center">
                  <span aria-hidden className="mt-1 h-3 w-3 rounded-full bg-accent shadow-glow" />
                  {i < CHANGELOG_MILESTONES.length - 1 && (
                    <span aria-hidden className="mt-1 w-px flex-1 bg-line" />
                  )}
                </div>
                <div>
                  <p className="font-display text-lg font-bold text-ink">{m.version}</p>
                  <p className="text-sm text-ink-muted">{m.title}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* Finale */}
        <section className="relative overflow-hidden rounded-3xl border border-line px-6 py-20 text-center">
          <div
            aria-hidden
            className="absolute inset-0"
            style={{
              background:
                "radial-gradient(ellipse 60% 70% at 50% 100%, rgb(124 108 255 / 0.22) 0%, transparent 70%)",
            }}
          />
          <div className="relative z-10">
            <p className="font-mono text-xs font-medium uppercase tracking-[0.34em] text-accent-soft">
              {finale.kicker}
            </p>
            <h2 className="mt-4 font-display text-3xl font-bold tracking-tight sm:text-5xl">
              <span className="text-gradient">{finale.headline}</span>
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-base text-ink-muted sm:text-lg">{finale.sub}</p>
            <Ctas chapter={finale} center />
          </div>
        </section>
      </div>

      <Footer />
    </div>
  );
}
