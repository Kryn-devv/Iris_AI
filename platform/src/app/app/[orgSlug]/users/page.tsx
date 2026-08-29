import Link from "next/link";
import { Search, Users as UsersIcon } from "lucide-react";
import { db } from "@/lib/db";
import { requireOrgPage } from "@/lib/auth/guards";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Avatar, EmptyState, PageHeader } from "@/components/ui/misc";
import { compactNumber, timeAgo } from "@/lib/utils";

export const dynamic = "force-dynamic";

type Row = {
  key: string;
  name: string;
  email: string | null;
  avatarUrl: string | null;
  kind: "joined" | "guest";
  posts: number;
  votes: number;
  comments: number;
  lastActive: Date | null;
};

function laterOf(a: Date | null, b: Date | null): Date | null {
  if (!a) return b;
  if (!b) return a;
  return a > b ? a : b;
}

/**
 * Customer-voices directory: everyone who participated in this org's feedback
 * — registered users (posts/votes/comments) and portal guests — aggregated
 * from a handful of groupBy queries and merged in code.
 */
export default async function UsersPage({
  params,
  searchParams,
}: {
  params: Promise<{ orgSlug: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { orgSlug } = await params;
  const sp = await searchParams;
  const search = typeof sp.search === "string" ? sp.search.trim() : "";
  const { org } = await requireOrgPage(orgSlug);
  const orgId = org.id;

  const [
    postsByUser,
    votesByUser,
    commentsByUser,
    guestPosts,
    guestVotes,
    guestComments,
  ] = await Promise.all([
    db.post.groupBy({
      by: ["authorId"],
      where: { orgId, authorId: { not: null } },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
    db.vote.groupBy({
      by: ["userId"],
      where: { post: { orgId }, userId: { not: null } },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
    db.comment.groupBy({
      by: ["authorId"],
      where: { post: { orgId }, authorId: { not: null } },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
    db.post.groupBy({
      by: ["guestEmail", "guestName"],
      where: {
        orgId,
        authorId: null,
        OR: [{ guestEmail: { not: null } }, { guestName: { not: null } }],
      },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
    db.vote.groupBy({
      by: ["guestId"],
      where: { post: { orgId }, guestId: { not: null } },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
    db.comment.groupBy({
      by: ["guestName"],
      where: { post: { orgId }, authorId: null, guestName: { not: null } },
      _count: { _all: true },
      _max: { createdAt: true },
    }),
  ]);

  const rows = new Map<string, Row>();

  function bump(
    key: string,
    seed: Omit<Row, "posts" | "votes" | "comments" | "lastActive">,
    delta: { posts?: number; votes?: number; comments?: number },
    at: Date | null
  ) {
    const existing = rows.get(key);
    if (existing) {
      existing.posts += delta.posts ?? 0;
      existing.votes += delta.votes ?? 0;
      existing.comments += delta.comments ?? 0;
      existing.lastActive = laterOf(existing.lastActive, at);
      if (!existing.email && seed.email) existing.email = seed.email;
    } else {
      rows.set(key, {
        ...seed,
        posts: delta.posts ?? 0,
        votes: delta.votes ?? 0,
        comments: delta.comments ?? 0,
        lastActive: at,
      });
    }
  }

  // --- Registered users --------------------------------------------------
  const userIds = new Set<string>();
  for (const g of postsByUser) if (g.authorId) userIds.add(g.authorId);
  for (const g of votesByUser) if (g.userId) userIds.add(g.userId);
  for (const g of commentsByUser) if (g.authorId) userIds.add(g.authorId);

  const users = userIds.size
    ? await db.user.findMany({
        where: { id: { in: [...userIds] } },
        select: { id: true, name: true, email: true, avatarUrl: true },
      })
    : [];
  const userById = new Map(users.map((u) => [u.id, u]));

  const seedUser = (id: string) => {
    const u = userById.get(id);
    return {
      key: `user:${id}`,
      name: u?.name ?? "Unknown user",
      email: u?.email ?? null,
      avatarUrl: u?.avatarUrl ?? null,
      kind: "joined" as const,
    };
  };

  for (const g of postsByUser) {
    if (!g.authorId) continue;
    bump(
      `user:${g.authorId}`,
      seedUser(g.authorId),
      { posts: g._count._all },
      g._max.createdAt
    );
  }
  for (const g of votesByUser) {
    if (!g.userId) continue;
    bump(
      `user:${g.userId}`,
      seedUser(g.userId),
      { votes: g._count._all },
      g._max.createdAt
    );
  }
  for (const g of commentsByUser) {
    if (!g.authorId) continue;
    bump(
      `user:${g.authorId}`,
      seedUser(g.authorId),
      { comments: g._count._all },
      g._max.createdAt
    );
  }

  // --- Guests -------------------------------------------------------------
  // Post guests keyed by email when known, else display name; remember the
  // name→key mapping so guest comments (name only) merge into the same row.
  const guestKeyByName = new Map<string, string>();

  for (const g of guestPosts) {
    const email = g.guestEmail?.toLowerCase() ?? null;
    const name = g.guestName?.trim() || g.guestEmail || "Guest";
    const key = email ? `guest:email:${email}` : `guest:name:${name.toLowerCase()}`;
    if (g.guestName) guestKeyByName.set(g.guestName.trim().toLowerCase(), key);
    bump(
      key,
      { key, name, email: g.guestEmail ?? null, avatarUrl: null, kind: "guest" },
      { posts: g._count._all },
      g._max.createdAt
    );
  }

  for (const g of guestComments) {
    if (!g.guestName) continue;
    const name = g.guestName.trim();
    const key =
      guestKeyByName.get(name.toLowerCase()) ?? `guest:name:${name.toLowerCase()}`;
    bump(
      key,
      { key, name, email: null, avatarUrl: null, kind: "guest" },
      { comments: g._count._all },
      g._max.createdAt
    );
  }

  // Anonymous voters: one row per distinct guest cookie id.
  for (const g of guestVotes) {
    if (!g.guestId) continue;
    const key = `guest:anon:${g.guestId}`;
    bump(
      key,
      { key, name: "Anonymous", email: null, avatarUrl: null, kind: "guest" },
      { votes: g._count._all },
      g._max.createdAt
    );
  }

  // --- Filter + sort -------------------------------------------------------
  let list = [...rows.values()];
  if (search) {
    const q = search.toLowerCase();
    list = list.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        (r.email ?? "").toLowerCase().includes(q)
    );
  }
  list.sort((a, b) => {
    const ta = a.lastActive?.getTime() ?? 0;
    const tb = b.lastActive?.getTime() ?? 0;
    if (tb !== ta) return tb - ta;
    return a.name.localeCompare(b.name);
  });

  const total = rows.size;

  return (
    <>
      <PageHeader
        title="Users"
        description={`${compactNumber(total)} ${
          total === 1 ? "person has" : "people have"
        } shared feedback, voted, or commented in this workspace.`}
      />

      <form method="get" role="search" className="mb-4 max-w-sm">
        <div className="relative">
          <Search
            size={14}
            aria-hidden
            className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <Input
            type="search"
            name="search"
            defaultValue={search}
            placeholder="Search by name or email…"
            aria-label="Search users by name or email"
            className="pl-8"
          />
        </div>
      </form>

      {list.length === 0 ? (
        <EmptyState
          icon={<UsersIcon size={22} />}
          title={search ? "No matching users" : "No participants yet"}
          description={
            search
              ? "Try a different name or email."
              : "As people post feedback, vote, and comment — in the app or on your public portal — they'll show up here."
          }
        />
      ) : (
        <Card>
          <CardContent className="px-0 pb-0 pt-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-line text-xs text-ink-faint">
                    <th className="px-5 py-3 font-medium">Name</th>
                    <th className="px-3 py-3 font-medium">Email</th>
                    <th className="px-3 py-3 text-right font-medium">Posts</th>
                    <th className="px-3 py-3 text-right font-medium">Votes</th>
                    <th className="px-3 py-3 text-right font-medium">Comments</th>
                    <th className="px-3 py-3 font-medium">Last active</th>
                    <th className="px-5 py-3 font-medium">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {list.map((row) => (
                    <tr
                      key={row.key}
                      className="border-b border-line/60 last:border-0"
                    >
                      <td className="px-5 py-3">
                        <Link
                          href={`/app/${orgSlug}/feedback?search=${encodeURIComponent(
                            row.name
                          )}`}
                          className="flex items-center gap-2.5 hover:text-accent-soft"
                          title={`View feedback from ${row.name}`}
                        >
                          <Avatar name={row.name} src={row.avatarUrl} size={28} />
                          <span className="font-medium text-ink">{row.name}</span>
                        </Link>
                      </td>
                      <td className="px-3 py-3 text-xs text-ink-muted">
                        {row.email ?? "—"}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-ink">
                        {row.posts}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-ink">
                        {row.votes}
                      </td>
                      <td className="px-3 py-3 text-right tabular-nums text-ink">
                        {row.comments}
                      </td>
                      <td className="px-3 py-3 text-xs text-ink-muted">
                        {row.lastActive ? timeAgo(row.lastActive) : "—"}
                      </td>
                      <td className="px-5 py-3">
                        <Badge tone={row.kind === "joined" ? "accent" : "neutral"}>
                          {row.kind === "joined" ? "Joined" : "Guest"}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
