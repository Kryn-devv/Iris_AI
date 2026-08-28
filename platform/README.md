# Novaris — Feedback Intelligence Platform

**Stop guessing what to build.** Novaris centralizes scattered customer
feedback — emails, tickets, community messages, surveys, widget submissions —
into one intelligent system: capture everything, understand what matters with
AI, prioritize with real demand, and ship a roadmap your users can follow.

The marketing site at `/` is a scroll-driven cinematic **3D journey**: the
visitor travels through a living digital universe where raw feedback (chaos)
is captured, analyzed, prioritized, and shipped — each chapter of the scroll
is the feature it explains. The product app itself (`/app`) is deliberately
calm, fast, and productivity-first.

## Feature map

| Area | What's inside |
| --- | --- |
| Feedback & requests | Boards, guest + authenticated submissions, attachments, categories/tags, duplicate detection & merge, statuses |
| Voting | One-click voting for members, portal visitors, and anonymous guests (cookie identity), leaderboards |
| AI insights | Provider-agnostic engine: sentiment, summaries, clustering, trends, generated insights — with a deterministic offline fallback |
| Prioritization | Transparent priority score (demand + impact/effort + revenue + momentum), editable signals, sortable matrix |
| Roadmap | Drag-and-drop kanban (Under consideration → Planned → In progress → Shipped) + public roadmap |
| Surveys | Builder (NPS, rating, choice, open text), conditional logic, scheduling & audience, one-question-per-step public flow, results analytics |
| Changelog | Markdown editor with live preview, labels, cover images/video, publish + notify, emoji reactions & comments |
| Portal & widget | Public portal per workspace (`/p/[slug]`) and embeddable widget (`/w/[widgetKey]`) |
| Integrations | Signed webhooks, API keys + public REST API (`/api/v1`), CSV import, embed snippets |
| Teams | Workspaces (orgs), roles (Owner/Admin/Member/Viewer), invites, member management |

## Quick start

```bash
cd platform
npm install
docker compose up -d          # local PostgreSQL 16 (or point DATABASE_URL elsewhere)
cp .env.example .env          # adjust if needed
npm run db:push               # create schema
npm run db:seed               # demo workspace: demo@novaris.app / demopass123
npm run dev                   # http://localhost:3000
```

Demo logins after seeding: `demo@novaris.app` / `demopass123` (owner) and
`mia@novaris.app` / `demopass123` (admin) — workspace **Orbit Labs**
(`/app/orbit-labs/dashboard`, public portal at `/p/orbit-labs`).

## Tech stack

- **Next.js 15** (App Router, server components) · React 19 · TypeScript strict
- **Tailwind CSS** with a token-driven dark design system (rebrand = edit
  `src/config/brand.ts` + CSS variables in `src/app/globals.css`)
- **3D journey**: three.js + @react-three/fiber + drei, GSAP ScrollTrigger,
  Lenis smooth scrolling, framer-motion
- **PostgreSQL + Prisma** (`prisma/schema.prisma` is the source of truth)
- **Auth**: DB-backed sessions (httpOnly cookie, SHA-256 token hashes),
  bcrypt passwords, role-guarded multi-tenancy
- **AI abstraction** (`src/lib/ai/`): `AI_PROVIDER=heuristic | openai |
  anthropic`. The heuristic engine (lexicon sentiment, keyword clustering,
  extractive summaries) is deterministic and fully offline — the platform is
  complete with zero API keys. OpenAI-compatible endpoints (OpenRouter, Groq,
  …) work via `OPENAI_BASE_URL`.

## Performance & accessibility notes

The 3D landing targets 60 fps on mid-range hardware: instanced meshes for all
repeated geometry, memoized materials, adaptive DPR/quality scaling, reduced
particle counts on mobile, rendering paused when hidden — and a full static
fallback for `prefers-reduced-motion` or missing WebGL. Sound is synthesized
(WebAudio), strictly opt-in, and never autoplays.

## Layout

```
platform/
  prisma/            schema + seed
  src/
    config/brand.ts  single source of brand truth
    lib/             db, auth, api helpers, AI engine, similarity, priority
    components/      ui kit · app shell · feature components · 3D scenes
    app/
      (marketing)/   the 3D journey landing
      (auth)/ …      login/register/onboarding/invites
      app/[orgSlug]/ the product (dashboard, feedback, roadmap, …)
      p/[orgSlug]/   public portal   ·  w/[widgetKey]/  embeddable widget
      api/           admin APIs · public portal APIs · /api/v1 REST
docs/CONVENTIONS.md  contributor guide (patterns, guards, URL map)
```
