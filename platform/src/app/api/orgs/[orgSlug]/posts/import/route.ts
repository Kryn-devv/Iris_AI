import { z } from "zod";
import { api, ok, parseBody } from "@/lib/api";
import { requireOrg } from "@/lib/auth/guards";
import { db } from "@/lib/db";
import {
  analyzeSentiment,
  summarizeText,
  suggestCategory,
} from "@/lib/ai/heuristic";
import { priorityScore } from "@/lib/priority";
import { recordActivity } from "@/lib/events";
import { POST_TYPES, POST_STATUSES } from "../helpers";

type Ctx = { params: Promise<{ orgSlug: string }> };

const Row = z.object({
  title: z.string().trim().min(1).max(200),
  body: z.string().max(10_000).optional(),
  type: z.enum(POST_TYPES).optional(),
  status: z.enum(POST_STATUSES).optional(),
  category: z.string().trim().max(60).optional(),
  guestName: z.string().trim().max(120).optional(),
  guestEmail: z.string().trim().max(200).optional(),
  createdAt: z.coerce.date().optional(),
});

const Body = z.object({ rows: z.array(Row).min(1).max(500) });

/**
 * POST /api/orgs/[orgSlug]/posts/import — bulk CSV import (ADMIN+).
 * Each row is enriched with heuristic sentiment/summary and mapped to a
 * category (named categories are created on the fly). Source = IMPORT.
 */
export async function POST(req: Request, { params }: Ctx) {
  return api(async () => {
    const { orgSlug } = await params;
    const { org, user } = await requireOrg(orgSlug, "ADMIN");
    const { rows } = await parseBody(req, Body);

    const categories = await db.category.findMany({
      where: { orgId: org.id },
      select: { id: true, name: true },
    });
    const categoryByName = new Map(
      categories.map((c) => [c.name.toLowerCase(), c])
    );

    let created = 0;
    for (const row of rows) {
      const bodyText = row.body?.trim() || row.title;
      const text = `${row.title}\n\n${bodyText}`;

      // Category: explicit name (find-or-create) beats the heuristic guess.
      let categoryId: string | null = null;
      if (row.category) {
        const key = row.category.toLowerCase();
        let cat = categoryByName.get(key);
        if (!cat) {
          cat = await db.category.create({
            data: { orgId: org.id, name: row.category },
            select: { id: true, name: true },
          });
          categoryByName.set(key, cat);
          categories.push(cat);
        }
        categoryId = cat.id;
      } else {
        categoryId = suggestCategory(text, categories);
      }

      const { sentiment, score: sentimentScore } = analyzeSentiment(text);
      const createdAt = row.createdAt ?? new Date();

      await db.post.create({
        data: {
          orgId: org.id,
          type: row.type ?? "FEEDBACK",
          status: row.status ?? "OPEN",
          title: row.title,
          body: bodyText,
          source: "IMPORT",
          guestName: row.guestName || null,
          guestEmail: row.guestEmail || null,
          categoryId,
          sentiment,
          sentimentScore,
          aiSummary: summarizeText(text),
          createdAt,
          shippedAt: (row.status ?? "OPEN") === "SHIPPED" ? createdAt : null,
          priorityScore: priorityScore({
            voteCount: 0,
            commentCount: 0,
            sentimentScore,
            impact: null,
            effort: null,
            revenueImpact: null,
            createdAt,
          }),
        },
      });
      created++;
    }

    await recordActivity(
      org.id,
      "posts.imported",
      { count: created },
      user.id
    );

    return ok({ created }, { status: 201 });
  });
}
