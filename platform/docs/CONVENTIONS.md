# Platform Conventions

Read this before writing any code in `platform/`. It documents the shared
foundation — everything here already exists and typechecks; build on it, don't
reinvent or modify it.

## Ground rules

- **Never modify**: `prisma/schema.prisma`, `package.json`, `tsconfig.json`,
  `tailwind.config.ts`, `src/lib/**` (except adding NEW files under your own
  feature), `src/components/ui/**`, `src/app/layout.tsx`, `src/app/globals.css`,
  `src/middleware.ts`, `src/config/brand.ts`. If something you need is missing,
  work around it in your own files and leave a note.
- **No new npm dependencies.** Installed and available: next 15.5 (App Router),
  react 19, tailwind 3.4, framer-motion, three + @react-three/fiber@9 +
  @react-three/drei@10, gsap (+ ScrollTrigger), lenis, @prisma/client, zod,
  bcryptjs, lucide-react, recharts, @dnd-kit/(core|sortable|utilities),
  date-fns, clsx, tailwind-merge, @anthropic-ai/sdk (server-only).
- **Brand**: never hardcode the product name — import `brand` from
  `@/config/brand`.
- Verify with `npx tsc --noEmit` (run in `platform/`). Ignore errors in files
  owned by other features; yours must be clean.

## Data & server

- DB access: `import { db } from "@/lib/db"` (Prisma singleton). The full data
  model is in `prisma/schema.prisma` — read it.
- Auth (all in `@/lib/auth/…`):
  - Pages: `requireUserPage()`, `requireOrgPage(orgSlug, minRole?)` (redirect).
  - APIs: `requireUser()`, `requireOrg(orgSlug, minRole?)` (throw `AuthError`).
  - `getCurrentUser()` (nullable), `getGuestId()` / `ensureGuestId()` for
    anonymous portal visitors, `getPublicOrg(slug)` for portal routes.
  - Roles: VIEWER < MEMBER < ADMIN < OWNER (`roleAtLeast`).
- Route handlers use the helpers in `@/lib/api`:

```ts
import { api, ok, fail, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { z } from "zod";

const Body = z.object({ title: z.string().min(1).max(200) });

export async function POST(
  req: Request,
  { params }: { params: Promise<{ orgSlug: string }> }
) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "MEMBER");
    const body = await parseBody(req, Body);
    // ... db work scoped by org.id — ALWAYS filter by org.id
    return ok({ id: "..." });
  });
}
```

- **Every query must be tenant-scoped** (`where: { orgId: org.id }` or via a
  relation that is). Never trust an id from the client without checking it
  belongs to the org.
- Server pages: `export const dynamic = "force-dynamic"` on any page that reads
  the DB. Next 15: `params`/`searchParams` are Promises — `await` them.
- Activity + webhooks: `recordActivity(orgId, type, meta, actorId?)` and
  `dispatchWebhooks(orgId, event, payload)` from `@/lib/events` (both
  fire-and-forget safe). Use event names like `post.created`,
  `post.status_changed`, `changelog.published`.
- AI: high-level `analyzePostText(title, body, categories)` from
  `@/lib/ai/analyze`; raw provider via `getProvider()` / `completeJSON()` from
  `@/lib/ai/provider`; pure heuristics (sentiment, clustering, keywords,
  summaries) in `@/lib/ai/heuristic`. Never import a vendor SDK directly.
- Duplicate detection: `findSimilar()` from `@/lib/similarity`.
- Priority: `priorityScore()` from `@/lib/priority` — recompute and store on
  `post.priorityScore` whenever votes/comments/impact/effort change.

## URL map

- Marketing: `/` (3D landing).
- Auth: `/login`, `/register`, `/onboarding`, `/invite/[token]`.
- App (authed): `/app/[orgSlug]/(dashboard|feedback|requests|insights|votes|roadmap|surveys|changelog|users|analytics|integrations|settings)`.
- Public portal: `/p/[orgSlug]`, `/p/[orgSlug]/posts/[postId]`,
  `/p/[orgSlug]/roadmap`, `/p/[orgSlug]/changelog`, `/p/[orgSlug]/s/[surveyId]`.
- Widget: `/w/[widgetKey]` (embeddable iframe page).
- Admin APIs: `/api/orgs/[orgSlug]/…`. Public APIs: `/api/p/[orgSlug]/…`.
  Auth APIs: `/api/auth/…`.

## UI

- Components in `@/components/ui`: `Button`, `Input`, `Textarea`, `Select`,
  `Label`, `FieldError`, `Card(+Header/Title/Description/Content)`, `Badge`,
  `Dialog`, `Tabs(+List/Trigger/Content)`, and from `ui/misc`: `Avatar`,
  `Skeleton`, `Spinner`, `EmptyState`, `PageHeader`. Team-authored rich text
  renders with `Markdown` from `@/lib/markdown` (escaped, XSS-safe) — never
  `dangerouslySetInnerHTML`.
- Status/sentiment display metadata: `@/lib/status` (`POST_STATUS`,
  `ROADMAP_STATUSES`, `SENTIMENT_META`, `SURVEY_STATUS`,
  `CHANGELOG_LABEL_META`). Never invent new status colors/labels.
- Utilities: `cn`, `slugify`, `compactNumber`, `timeAgo`, `initials` from
  `@/lib/utils`.
- Theme is dark-only, token-driven (see `globals.css`). Use semantic Tailwind
  colors (`bg-surface-raised`, `text-ink-muted`, `border-line`, `text-accent`…)
  — never raw hex in components. `.glass` and `.text-gradient` utility classes
  exist.
- App pages render inside the shell (`src/components/app/shell.tsx`) — start
  pages with `<PageHeader title=… />`. Keep the dashboard calm and productive;
  the cinematic 3D belongs to the marketing site only.
- Client components: add `"use client"` only where interactivity requires it.
  Prefer server components + small client islands.
- Mutations from client components: plain `fetch` to the API routes, then
  `router.refresh()`. Responses are `{ ok: true, data }` or
  `{ ok: false, error: { message } }`.

## Quality bar

- No placeholder/fake functionality in the app: every button does what it says
  against the real DB.
- Empty states for every list (use `EmptyState`).
- Loading states via `loading.tsx` or `Skeleton` where it matters.
- Accessible: labels on inputs, aria on icon buttons, focus states (already in
  globals).
